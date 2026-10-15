"""One way for Syllora's import features to call OpenAI, whether through the user's ChatGPT plan or an API key."""

from __future__ import annotations

from dataclasses import dataclass

import chatgpt_auth

try:
    from openai import OpenAI as OpenAIClient  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    OpenAIClient = None


class AIUnavailableError(RuntimeError):
    """AI can't be used right now; the message tells the user what to do about it."""


@dataclass(frozen=True)
class AIAuth:
    """Who pays for an AI request. A ChatGPT sign-in wins; the API key only covers installs that already had one."""

    profile_id: str = "default"
    api_key: str = ""


def choose_plan_model(available: list[str], preferred: str) -> str:
    if not available:
        raise AIUnavailableError("No AI models are available for your ChatGPT plan right now.")
    if preferred in available:
        return preferred
    return next((slug for slug in available if "mini" in slug.lower()), available[0])


def _error_fields(source: object) -> tuple[str, str, str]:
    """(code, message, param) from an error object, an OpenAI error body, or a bare {"detail": ...} body."""
    if source is None:
        return "", "", ""
    if isinstance(source, dict):
        error = source.get("error")
        if isinstance(error, dict):
            source = error
        else:
            return "", str(source.get("detail") or error or ""), ""
        return (
            str(source.get("code") or ""),
            str(source.get("message") or ""),
            str(source.get("param") or ""),
        )
    return (
        str(getattr(source, "code", "") or ""),
        str(getattr(source, "message", "") or ""),
        str(getattr(source, "param", "") or ""),
    )


def describe_plan_failure(code: str, message: str = "", *, status: int | None = None, param: str = "") -> str:
    """Turn a ChatGPT-plan error into something a student can act on."""
    usage_hint = f"Open ChatGPT settings → Usage ({chatgpt_auth.USAGE_SETTINGS_URL}) to review your plan or this app's limit."
    if code == "subscription_sharing_usage_limit_exceeded" or status == 429:
        return f"Usage limit reached. {usage_hint}"
    if code in {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"} or status == 503:
        return "ChatGPT plan usage is temporarily unavailable. Please try again in a few minutes."
    if code == "subscription_sharing_user_not_eligible":
        return "Your ChatGPT account or workspace can't use plan usage in apps (it requires ChatGPT Plus or Pro)."
    if code == "subscription_sharing_unsupported_capability":
        what = f" ('{param}')" if param else ""
        return f"ChatGPT plan usage doesn't support part of this request{what}: {message}".rstrip(": ")
    if code in {"subscription_sharing_invalid_user", "chatpass_v2_scope_not_authorized", "chatpass_v2_invalid_authorization_context"} or status in (401, 403):
        return (
            "ChatGPT didn't accept this request. Sign out and continue with ChatGPT again in Settings, "
            "and approve plan usage when asked."
        )
    return message or "The ChatGPT plan request failed."


class AIClient:
    def __init__(self, sdk_client, *, plan_usage: bool, model: str) -> None:
        self._sdk = sdk_client
        self.plan_usage = plan_usage
        self.model = model

    def close(self) -> None:
        self._sdk.close()

    def generate_text(self, *, system: str, user_content: list, text_format: dict, timeout: float) -> str:
        if not self.plan_usage:
            response = self._sdk.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                text=text_format,
                timeout=timeout,
            )
            return response.output_text
        return self._generate_with_plan(system=system, user_content=user_content, text_format=text_format, timeout=timeout)

    def _generate_with_plan(self, *, system: str, user_content: list, text_format: dict, timeout: float) -> str:
        # Plan usage only accepts streamed, unstored requests, and rejects `system` input items.
        try:
            stream = self._sdk.responses.create(
                model=self.model,
                instructions=system,
                input=[{"role": "user", "content": user_content}],
                text=text_format,
                store=False,
                stream=True,
                timeout=timeout,
            )
            chunks: list[str] = []
            final_text = ""
            completed = False
            for event in stream:
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    chunks.append(str(getattr(event, "delta", "") or ""))
                elif kind == "response.completed":
                    completed = True
                    final_text = str(getattr(getattr(event, "response", None), "output_text", "") or "")
                elif kind == "response.incomplete":
                    raise AIUnavailableError("The AI response was cut off before it finished. Please try again.")
                elif kind in {"response.failed", "error"}:
                    failure = getattr(getattr(event, "response", None), "error", None) if kind == "response.failed" else event
                    code, message, param = _error_fields(failure)
                    raise AIUnavailableError(describe_plan_failure(code, message, param=param))
        except AIUnavailableError:
            raise
        except Exception as exc:
            code, message, param = _error_fields(getattr(exc, "body", None))
            status = getattr(exc, "status_code", None)
            if code or status:
                raise AIUnavailableError(describe_plan_failure(code, message, status=status, param=param)) from exc
            raise

        if not completed:
            raise AIUnavailableError("The connection to ChatGPT was interrupted before the AI finished. Please try again.")
        return "".join(chunks) or final_text


def open_ai_client(auth: AIAuth, *, model: str, timeout: float) -> AIClient:
    if OpenAIClient is None:
        raise AIUnavailableError("The OpenAI Python package is not installed.")

    try:
        session = chatgpt_auth.get_valid_session(auth.profile_id)
        if session is not None:
            if not session.plan_usage_enabled:
                raise AIUnavailableError(
                    "Syllora isn't allowed to use your ChatGPT plan. Sign out and continue with ChatGPT again, "
                    "and approve plan usage when asked."
                )
            chosen = choose_plan_model(chatgpt_auth.list_models(session), model)
            sdk = OpenAIClient(
                api_key=session.access_token,
                base_url=chatgpt_auth.API_BASE_URL,
                timeout=timeout,
                max_retries=0,
            )
            return AIClient(sdk, plan_usage=True, model=chosen)
    except chatgpt_auth.ChatGPTAuthError as exc:
        raise AIUnavailableError(str(exc)) from exc

    if auth.api_key.strip():
        sdk = OpenAIClient(api_key=auth.api_key.strip(), timeout=timeout, max_retries=0)
        return AIClient(sdk, plan_usage=False, model=model)
    raise AIUnavailableError("Continue with ChatGPT in Settings to use AI import.")
