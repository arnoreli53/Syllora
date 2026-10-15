"""Sign in with ChatGPT: lets the user run Syllora's AI features on their own ChatGPT plan.

Implements OpenAI's documented flow for open-source / locally hosted apps
(https://developers.openai.com/siwc/token-sharing-open-source): OAuth 2.0 authorization code
with PKCE through the system browser and a 127.0.0.1 loopback callback, dynamic client
registration, and refresh-token rotation. Tokens live in the macOS Keychain.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import secret_store
from paths import APP_SUPPORT_DIR
from version import APP_DISPLAY_NAME, APP_NAME

try:
    import certifi
except ImportError:  # pragma: no cover - certifi ships with the app build
    certifi = None

AUTH_ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{AUTH_ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{AUTH_ISSUER}/api/accounts/oauth/token"
JWKS_URL = f"{AUTH_ISSUER}/.well-known/jwks.json"
API_BASE_URL = "https://api.openai.com/v1"
USAGE_SETTINGS_URL = "https://chatgpt.com/settings/usage"

DYNAMIC_CLIENT_ID = "dynamic_agent_client"
PLAN_USAGE_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = f"openid profile email offline_access resource.invoke {PLAN_USAGE_SCOPE}"
CALLBACK_PATH = "/auth/callback"
PREFERRED_CALLBACK_PORT = 1455
SIGN_IN_TIMEOUT_SECONDS = 300
HTTP_TIMEOUT_SECONDS = 20
REFRESH_MARGIN_SECONDS = 120

HOST_ID_PATH = APP_SUPPORT_DIR / "chatgpt_host_id"

# Refresh failures that mean the stored tokens can never work again (the user disconnected
# the app or the grant expired) as opposed to a temporary outage.
TERMINAL_REFRESH_ERRORS = frozenset(
    {
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    }
)

_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
_refresh_lock = threading.Lock()


class ChatGPTAuthError(RuntimeError):
    """A problem the user can act on; the message is safe to show as-is."""


class ChatGPTSignInCancelled(ChatGPTAuthError):
    pass


class ChatGPTNetworkError(ChatGPTAuthError):
    """OpenAI could not be reached; stored credentials are left untouched."""


class OAuthError(ChatGPTAuthError):
    def __init__(self, code: str, description: str = "", status: int = 0) -> None:
        self.code = code
        self.description = description
        self.status = status
        super().__init__(f"{code}: {description}" if description else code)


@dataclass
class ChatGPTSession:
    client_id: str
    access_token: str
    refresh_token: str
    expires_at: float
    scopes: list[str] = field(default_factory=list)
    subject: str = ""
    email: str = ""
    name: str = ""

    @property
    def plan_usage_enabled(self) -> bool:
        return PLAN_USAGE_SCOPE in self.scopes


@dataclass(frozen=True)
class ChatGPTStatus:
    signed_in: bool = False
    email: str = ""
    name: str = ""
    plan_usage: bool = False


@dataclass
class _StoredState:
    client_id: str = ""
    last_email: str = ""
    session: ChatGPTSession | None = None


# ---------------------------------------------------------------------------
# Storage (macOS Keychain, one item per Syllora profile)
# ---------------------------------------------------------------------------

def _read_blob(profile_id: str) -> str:
    return secret_store.load_secret(profile_id, service=secret_store.CHATGPT_KEYCHAIN_SERVICE)


def _write_blob(profile_id: str, text: str) -> None:
    secret_store.save_secret(profile_id, text, service=secret_store.CHATGPT_KEYCHAIN_SERVICE)


def _delete_blob(profile_id: str) -> None:
    secret_store.delete_secret(profile_id, service=secret_store.CHATGPT_KEYCHAIN_SERVICE)


def _load_state(profile_id: str) -> _StoredState:
    try:
        raw = json.loads(_read_blob(profile_id) or "{}")
        session_raw = raw.get("session")
        session = ChatGPTSession(**session_raw) if isinstance(session_raw, dict) else None
        return _StoredState(
            client_id=str(raw.get("client_id", "") or ""),
            last_email=str(raw.get("last_email", "") or ""),
            session=session,
        )
    except (ValueError, TypeError):
        return _StoredState()


def _save_state(profile_id: str, state: _StoredState) -> None:
    if not state.client_id and state.session is None:
        _delete_blob(profile_id)
        return
    payload = {
        "client_id": state.client_id,
        "last_email": state.last_email,
        "session": None if state.session is None else state.session.__dict__,
    }
    _write_blob(profile_id, json.dumps(payload))


def ensure_host_id() -> str:
    """A random, persistent, non-identifying ID for this Mac, as OpenAI's registration requires."""
    try:
        existing = HOST_ID_PATH.read_text(encoding="utf-8").strip()
        if existing.startswith("urn:uuid:"):
            return existing
    except OSError:
        pass
    host_id = f"urn:uuid:{uuid.uuid4()}"
    HOST_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    HOST_ID_PATH.write_text(host_id, encoding="utf-8")
    return host_id


def status(profile_id: str) -> ChatGPTStatus:
    session = _load_state(profile_id).session
    if session is None:
        return ChatGPTStatus()
    return ChatGPTStatus(True, session.email, session.name, session.plan_usage_enabled)


def is_signed_in(profile_id: str) -> bool:
    return _load_state(profile_id).session is not None


def sign_out(profile_id: str) -> None:
    """Drop the tokens but keep the registered client ID so signing back in reuses it."""
    state = _load_state(profile_id)
    state.session = None
    _save_state(profile_id, state)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def _read_json_error(exc: urllib.error.HTTPError) -> dict:
    try:
        body = json.loads(exc.read().decode("utf-8", "replace"))
        return body if isinstance(body, dict) else {}
    except (ValueError, OSError):
        return {}
    finally:
        exc.close()


def _post_form(url: str, fields: dict[str, str]) -> dict:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": f"{APP_NAME}-ChatGPTSignIn",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS, context=_ssl_context()) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        body = _read_json_error(exc)
        error = body.get("error")
        code = str(error.get("code") or error.get("type") or "") if isinstance(error, dict) else str(error or "")
        description = str(body.get("error_description") or (error.get("message") if isinstance(error, dict) else "") or "")
        raise OAuthError(code or f"http_{exc.code}", description, exc.code) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ChatGPTNetworkError("Could not reach OpenAI to sign in with ChatGPT. Check your connection and try again.") from exc
    if not isinstance(data, dict):
        raise ChatGPTAuthError("OpenAI returned an unexpected sign-in response.")
    return data


def _get_json(url: str, *, bearer: str = "") -> object:
    headers = {"Accept": "application/json", "User-Agent": f"{APP_NAME}-ChatGPTSignIn"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS, context=_ssl_context()) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise ChatGPTAuthError(f"OpenAI returned HTTP {exc.code} for {urllib.parse.urlparse(url).path}.") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ChatGPTNetworkError("Could not reach OpenAI. Check your connection and try again.") from exc


# ---------------------------------------------------------------------------
# PKCE, authorize URL, ID-token validation
# ---------------------------------------------------------------------------

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_pkce() -> tuple[str, str]:
    verifier = _b64url_encode(secrets.token_bytes(48))
    challenge = _b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(
    *,
    client_id: str,
    host_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
    login_hint: str = "",
) -> str:
    registering = client_id == DYNAMIC_CLIENT_ID
    params: dict[str, str] = {
        "client_id": client_id,
        "ext_agent_host_id": host_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "resource": API_BASE_URL,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    if registering:
        params["agent_name_hint"] = APP_DISPLAY_NAME
    elif login_hint:
        params["login_hint"] = login_hint
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"


def _rsa_verify_pkcs1_sha256(modulus: int, exponent: int, message: bytes, signature: bytes) -> bool:
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    recovered = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(size, "big")
    suffix = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if size < len(suffix) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (size - len(suffix) - 3) + b"\x00" + suffix
    return hmac.compare_digest(recovered, expected)


def _verify_signature(signing_input: bytes, signature: bytes, jwk: dict) -> bool:
    if jwk.get("kty") != "RSA":
        return False
    modulus = int.from_bytes(_b64url_decode(str(jwk.get("n", ""))), "big")
    exponent = int.from_bytes(_b64url_decode(str(jwk.get("e", ""))), "big")
    return _rsa_verify_pkcs1_sha256(modulus, exponent, signing_input, signature)


def _fetch_jwks() -> list[dict]:
    data = _get_json(JWKS_URL)
    keys = data.get("keys") if isinstance(data, dict) else None
    return [key for key in keys if isinstance(key, dict)] if isinstance(keys, list) else []


def validate_id_token(id_token: str, *, client_id: str, nonce: str, now: float | None = None) -> dict:
    """Verify an ID token's signature (RS256 against OpenAI's JWKS) and its issuer/audience/expiry/nonce."""
    try:
        header_b64, payload_b64, signature_b64 = id_token.split(".")
        header = json.loads(_b64url_decode(header_b64))
        claims = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(signature_b64)
    except (ValueError, TypeError) as exc:
        raise ChatGPTAuthError("OpenAI returned a malformed ID token.") from exc

    if header.get("alg") != "RS256":
        raise ChatGPTAuthError("OpenAI returned an ID token with an unsupported signature algorithm.")
    key = next((k for k in _fetch_jwks() if k.get("kid") == header.get("kid")), None)
    if key is None or not _verify_signature(f"{header_b64}.{payload_b64}".encode("ascii"), signature, key):
        raise ChatGPTAuthError("The ChatGPT ID token's signature could not be verified.")

    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    current = time.time() if now is None else now
    if claims.get("iss") != AUTH_ISSUER:
        raise ChatGPTAuthError("The ChatGPT ID token came from an unexpected issuer.")
    if client_id not in audiences:
        raise ChatGPTAuthError("The ChatGPT ID token was issued for a different app.")
    if not isinstance(claims.get("exp"), (int, float)) or float(claims["exp"]) < current - 60:
        raise ChatGPTAuthError("The ChatGPT ID token has expired.")
    if not hmac.compare_digest(str(claims.get("nonce", "")).encode("utf-8"), nonce.encode("utf-8")):
        raise ChatGPTAuthError("The ChatGPT sign-in response did not match this sign-in attempt.")
    return claims


# ---------------------------------------------------------------------------
# Loopback callback listener
# ---------------------------------------------------------------------------

class _CallbackServer:
    """Accepts exactly one OAuth redirect on 127.0.0.1, ignoring anything that doesn't carry our state."""

    def __init__(self, expected_state: str, preferred_port: int = PREFERRED_CALLBACK_PORT) -> None:
        self._expected_state = expected_state
        self.result: dict[str, str] | None = None
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def _reply(self, status: int, body: str) -> None:
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:  # noqa: N802 - http.server naming
                parsed = urllib.parse.urlparse(self.path)
                query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items() if v}
                host = (self.headers.get("Host") or "").strip()
                if host != f"127.0.0.1:{owner.port}":
                    self._reply(403, "Forbidden")
                    return
                if parsed.path != CALLBACK_PATH:
                    self._reply(404, "Not found")
                    return
                if not hmac.compare_digest(query.get("state", "").encode("utf-8"), owner._expected_state.encode("utf-8")):
                    self._reply(400, "This sign-in link is not valid for the current attempt.")
                    return
                owner.result = query
                title = "Signed in" if "code" in query else "Sign-in was not completed"
                self._reply(
                    200,
                    f"<!doctype html><meta charset='utf-8'><title>{APP_DISPLAY_NAME}</title>"
                    "<body style='font-family:-apple-system,sans-serif;text-align:center;padding:72px;color:#222'>"
                    f"<h2>{title}</h2><p>You can close this tab and return to the app.</p></body>",
                )

        try:
            self._httpd = http.server.HTTPServer(("127.0.0.1", preferred_port), Handler)
        except OSError:
            self._httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._httpd.server_address[1])
        self.redirect_uri = f"http://127.0.0.1:{self.port}{CALLBACK_PATH}"

    def wait(self, timeout: float, cancel: threading.Event | None) -> dict[str, str]:
        self._httpd.timeout = 0.25
        deadline = time.monotonic() + timeout
        while self.result is None:
            if cancel is not None and cancel.is_set():
                raise ChatGPTSignInCancelled("Sign-in cancelled.")
            if time.monotonic() > deadline:
                raise ChatGPTAuthError("Sign-in timed out. Try again when you're ready to finish it in the browser.")
            self._httpd.handle_request()
        return self.result

    def close(self) -> None:
        self._httpd.server_close()


# ---------------------------------------------------------------------------
# Sign-in, refresh, models
# ---------------------------------------------------------------------------

def _session_from_tokens(tokens: dict, *, client_id: str, claims: dict | None, previous: ChatGPTSession | None) -> ChatGPTSession:
    access_token = str(tokens.get("access_token", "") or "")
    if not access_token:
        raise ChatGPTAuthError("OpenAI did not return an access token.")
    try:
        lifetime = float(tokens.get("expires_in", 3600) or 3600)
    except (TypeError, ValueError):
        lifetime = 3600.0
    scope_text = tokens.get("scope")
    if isinstance(scope_text, str) and scope_text.strip():
        scopes = scope_text.split()
    else:
        scopes = list(previous.scopes) if previous is not None else []
    return ChatGPTSession(
        client_id=client_id,
        access_token=access_token,
        refresh_token=str(tokens.get("refresh_token") or (previous.refresh_token if previous else "") or ""),
        expires_at=time.time() + lifetime,
        scopes=scopes,
        subject=str((claims or {}).get("sub") or (previous.subject if previous else "") or ""),
        email=str((claims or {}).get("email") or (previous.email if previous else "") or ""),
        name=str((claims or {}).get("name") or (previous.name if previous else "") or ""),
    )


def sign_in(
    profile_id: str,
    *,
    open_url: Callable[[str], bool] = webbrowser.open,
    cancel_event: threading.Event | None = None,
    timeout: float = SIGN_IN_TIMEOUT_SECONDS,
) -> ChatGPTSession:
    """Run the browser sign-in and persist the result. Blocks until finished, so call it off the UI thread."""
    host_id = ensure_host_id()
    state = _load_state(profile_id)
    client_id = state.client_id or DYNAMIC_CLIENT_ID
    registering = client_id == DYNAMIC_CLIENT_ID

    oauth_state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = generate_pkce()
    server = _CallbackServer(oauth_state)
    try:
        url = build_authorize_url(
            client_id=client_id,
            host_id=host_id,
            redirect_uri=server.redirect_uri,
            state=oauth_state,
            nonce=nonce,
            code_challenge=challenge,
            login_hint=state.last_email,
        )
        if not open_url(url):
            raise ChatGPTAuthError("Could not open your web browser to continue with ChatGPT.")
        callback = server.wait(timeout, cancel_event)
    finally:
        server.close()

    if callback.get("error"):
        if callback["error"] == "access_denied":
            raise ChatGPTSignInCancelled("Sign-in was cancelled in the browser.")
        raise ChatGPTAuthError(f"ChatGPT sign-in failed: {callback.get('error_description') or callback['error']}")
    code = callback.get("code", "")
    if not code:
        raise ChatGPTAuthError("ChatGPT did not return an authorization code.")

    issued_client_id = callback.get("client_id", "")
    if registering:
        if not issued_client_id or issued_client_id == DYNAMIC_CLIENT_ID:
            raise ChatGPTAuthError("ChatGPT did not complete app registration. Please try again.")
        client_id = issued_client_id
    elif issued_client_id and issued_client_id != client_id:
        raise ChatGPTAuthError("ChatGPT returned a different app registration than expected.")

    tokens = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": server.redirect_uri,
            "resource": API_BASE_URL,
        },
    )
    id_token = str(tokens.get("id_token", "") or "")
    if not id_token:
        raise ChatGPTAuthError("OpenAI did not return an ID token.")
    claims = validate_id_token(id_token, client_id=client_id, nonce=nonce)

    session = _session_from_tokens(tokens, client_id=client_id, claims=claims, previous=None)
    if not session.refresh_token:
        raise ChatGPTAuthError("OpenAI did not return a refresh token, so the sign-in could not be kept.")
    _save_state(profile_id, _StoredState(client_id=client_id, last_email=session.email, session=session))
    return session


def get_valid_session(profile_id: str, *, force_refresh: bool = False) -> ChatGPTSession | None:
    """The stored session with a usable access token, refreshing (and rotating the refresh token) if needed."""
    with _refresh_lock:
        state = _load_state(profile_id)
        session = state.session
        if session is None:
            return None
        if not force_refresh and session.expires_at - time.time() > REFRESH_MARGIN_SECONDS:
            return session

        try:
            tokens = _post_form(
                TOKEN_URL,
                {"grant_type": "refresh_token", "client_id": session.client_id, "refresh_token": session.refresh_token},
            )
        except OAuthError as exc:
            if exc.code in TERMINAL_REFRESH_ERRORS:
                state.session = None
                _save_state(profile_id, state)
                raise ChatGPTAuthError(
                    "Your ChatGPT sign-in expired or was disconnected. Continue with ChatGPT in Settings to sign in again."
                ) from exc
            raise ChatGPTAuthError(f"Could not refresh your ChatGPT sign-in ({exc}).") from exc

        refreshed = _session_from_tokens(tokens, client_id=session.client_id, claims=None, previous=session)
        state.session = refreshed
        _save_state(profile_id, state)
        return refreshed


def list_models(session: ChatGPTSession) -> list[str]:
    """Model slugs this account can use with plan usage, in the order OpenAI lists them."""
    data = _get_json(f"{API_BASE_URL}/models", bearer=session.access_token)
    entries = data.get("data") or data.get("models") if isinstance(data, dict) else data
    slugs: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        visibility = entry.get("visibility")
        slug = str(entry.get("slug") or entry.get("id") or "").strip()
        if slug and visibility in (None, "list"):
            slugs.append(slug)
    return slugs
