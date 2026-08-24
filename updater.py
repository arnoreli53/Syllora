import base64
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import shlex
import tempfile
import urllib.error
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
    QSslCertificate,
    QSslConfiguration,
)

try:
    import certifi
except ImportError:
    certifi = None

from version import (
    APP_BUNDLE_NAME,
    APP_BUNDLE_IDENTIFIER,
    APP_DISPLAY_NAME,
    APP_NAME,
    APP_RELEASE_ARCHIVE_PREFIX,
    APP_VERSION,
    GITHUB_REPO_SLUG,
    LEGACY_APP_BUNDLE_NAME,
    LEGACY_UPDATE_TEST_CURRENT_VERSION_ENV,
    UPDATE_TEST_CURRENT_VERSION_ENV,
)
from paths import UPDATES_DIR

LATEST_RELEASE_MANIFEST_URL = (
    f"https://github.com/{GITHUB_REPO_SLUG}/releases/latest/download/latest.json"
)
LATEST_JSON_URLS = [
    LATEST_RELEASE_MANIFEST_URL,
    f"https://raw.githubusercontent.com/{GITHUB_REPO_SLUG}/main/updates/latest.json",
    f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/contents/updates/latest.json?ref=main",
    f"https://github.com/{GITHUB_REPO_SLUG}/raw/main/updates/latest.json",
]
LATEST_RELEASES_URL = f"https://github.com/{GITHUB_REPO_SLUG}/releases/latest"
INITIAL_RESPONSE_TIMEOUT_MS = 15_000
INACTIVITY_TIMEOUT_MS = 90_000
MAX_REDIRECTS = 10
UPDATE_HELPER_DIR = "update_helper"
UPDATE_HELPER_ARCHIVE_SUFFIXES = (
    f"/Contents/Resources/{UPDATE_HELPER_DIR}/install_update.py",
    "/Contents/Resources/_internal/install_update.py",
    "/Contents/Resources/install_update.py",
    f"/Contents/Frameworks/{UPDATE_HELPER_DIR}/install_update.py",
    "/Contents/Frameworks/_internal/install_update.py",
    "/Contents/Frameworks/install_update.py",
)


@dataclass(frozen=True)
class UpdateVerificationResult:
    ok: bool
    expected_sha256: str
    actual_sha256: str


def build_ssl_context() -> ssl.SSLContext | None:
    """Return an SSL context that uses certifi when available."""
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def build_qt_ssl_configuration() -> QSslConfiguration:
    configuration = QSslConfiguration.defaultConfiguration()
    if certifi is not None:
        certificates = QSslCertificate.fromPath(certifi.where())
        if certificates:
            configuration.setCaCertificates(certificates)
    return configuration


def parse_version(v: str) -> tuple[int, ...]:
    digits: list[int] = []
    for part in v.strip().split("."):
        number = "".join(ch for ch in part if ch.isdigit())
        if number:
            digits.append(int(number))
    return tuple(digits or [0])


def _normalize_sha256(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized.split(":", 1)[1].strip()
    return normalized


def _validated_sha256(value: str) -> str:
    normalized = _normalize_sha256(value)
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise RuntimeError("Update manifest contains an invalid SHA-256 checksum.")
    return normalized


def current_update_version() -> str:
    override = os.environ.get(UPDATE_TEST_CURRENT_VERSION_ENV, "").strip()
    if override:
        return override
    legacy_override = os.environ.get(LEGACY_UPDATE_TEST_CURRENT_VERSION_ENV, "").strip()
    return legacy_override or APP_VERSION


def _load_json_from_url(url: str, *, timeout: int = 10) -> dict:
    context = build_ssl_context()
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": f"{APP_NAME}-Updater",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout, context=context) as resp:
        data = json.load(resp)

    if isinstance(data, dict) and "content" in data and str(data.get("encoding", "")).lower() == "base64":
        raw = base64.b64decode(str(data["content"]).encode("ascii"))
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise RuntimeError("Decoded update manifest was not a JSON object.")
        return parsed

    if not isinstance(data, dict):
        raise RuntimeError("Update manifest was not a JSON object.")
    return data


def _validated_update_manifest(data: dict) -> dict:
    required = ("latest_version", "macos_zip_url", "sha256")
    missing = [key for key in required if key not in data]
    if missing:
        raise RuntimeError(f"Update manifest missing required keys: {', '.join(missing)}")

    latest = str(data["latest_version"]).strip()
    zip_url = str(data["macos_zip_url"]).strip()
    expected_prefix = f"https://github.com/{GITHUB_REPO_SLUG}/releases/download/"
    if not zip_url.startswith(expected_prefix):
        raise RuntimeError("Update manifest points outside the official Syllora GitHub releases.")

    notes = data.get("release_notes", [])
    if not isinstance(notes, list):
        notes = [str(notes)]

    return {
        "latest_version": latest,
        "zip_url": zip_url,
        "sha256": _validated_sha256(str(data["sha256"])),
        "release_notes": [str(note) for note in notes],
    }


def check_for_updates() -> dict:
    errors: list[tuple[str, Exception]] = []
    manifests: list[dict] = []
    for url in LATEST_JSON_URLS:
        try:
            manifests.append(_validated_update_manifest(_load_json_from_url(url, timeout=10)))
        except Exception as exc:
            errors.append((url, exc))

    if not manifests:
        not_found = bool(errors) and all(
            isinstance(exc, urllib.error.HTTPError) and exc.code == 404
            for _, exc in errors
        )
        if not_found:
            raise RuntimeError(
                "Update information was not found on GitHub.\n\n"
                "The update release may not be published yet, or the Syllora repository "
                "may be private. Private GitHub releases require authentication."
            )

        if errors:
            last_url, last_error = errors[-1]
            detail = f"{last_url} -> {last_error}"
        else:
            detail = "unknown error"
        raise RuntimeError(
            "Failed to fetch update manifest. GitHub could not be reached from the app.\n\n"
            f"Last error: {detail}"
        )

    newest = max(manifests, key=lambda manifest: parse_version(manifest["latest_version"]))
    current_version = current_update_version()

    return {
        "update_available": parse_version(newest["latest_version"]) > parse_version(current_version),
        "current_version": current_version,
        **newest,
    }


def _describe_download_error(
    reply: QNetworkReply,
    *,
    timed_out: bool,
    timed_out_after_response: bool,
    cancelled: bool,
    ssl_error_detail: str,
) -> str:
    if cancelled:
        return "Update download cancelled."

    if timed_out:
        if timed_out_after_response:
            return "The update download timed out because GitHub stopped sending data."
        return "The update download timed out before GitHub responded."

    status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    try:
        http_status = int(status) if status is not None else None
    except (TypeError, ValueError):
        http_status = None

    reason = reply.attribute(QNetworkRequest.Attribute.HttpReasonPhraseAttribute)
    reason_text = str(reason).strip() if reason is not None else ""
    status_detail = f" (HTTP {http_status}{f' {reason_text}' if reason_text else ''})" if http_status else ""

    error = reply.error()
    if error == QNetworkReply.NetworkError.NoError and http_status and http_status < 400:
        return ""

    if error == QNetworkReply.NetworkError.SslHandshakeFailedError or ssl_error_detail:
        detail = f"\n\nDetails: {ssl_error_detail}" if ssl_error_detail else ""
        return f"The update download failed TLS verification.{detail}"
    if error == QNetworkReply.NetworkError.TooManyRedirectsError:
        return "GitHub redirected the update download too many times."
    if error == QNetworkReply.NetworkError.InsecureRedirectError:
        return "GitHub redirected the update download to an insecure address."
    if error in {
        QNetworkReply.NetworkError.HostNotFoundError,
        QNetworkReply.NetworkError.ConnectionRefusedError,
        QNetworkReply.NetworkError.RemoteHostClosedError,
        QNetworkReply.NetworkError.NetworkSessionFailedError,
        QNetworkReply.NetworkError.TemporaryNetworkFailureError,
        QNetworkReply.NetworkError.UnknownNetworkError,
    }:
        return "The update download could not connect to GitHub."
    if error == QNetworkReply.NetworkError.ContentNotFoundError or http_status == 404:
        return f"The update file could not be found on GitHub.{status_detail}"
    if error == QNetworkReply.NetworkError.ContentAccessDenied or http_status == 403:
        return f"GitHub denied access to the update file.{status_detail}"
    if error == QNetworkReply.NetworkError.AuthenticationRequiredError or http_status == 401:
        return f"GitHub requested authentication for the update download.{status_detail}"
    if error == QNetworkReply.NetworkError.ServiceUnavailableError or (http_status is not None and http_status >= 500):
        return f"GitHub is temporarily unavailable.{status_detail}"
    if http_status is not None and http_status >= 400:
        return f"GitHub returned an error while downloading the update.{status_detail}"

    detail = reply.errorString().strip()
    if detail:
        return f"The update could not be downloaded.\n\nDetails: {detail}"
    return "The update could not be downloaded."


def _remove_file_if_exists(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def download_update(zip_url: str, version: str, *, progress_callback=None, cancel_callback=None) -> Path:
    safe_version = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in version.strip()) or "latest"
    out_path = UPDATES_DIR / f"{APP_RELEASE_ARCHIVE_PREFIX}-{safe_version}.zip"
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    _remove_file_if_exists(part_path)

    temporary_app = None
    if QCoreApplication.instance() is None:
        temporary_app = QCoreApplication([])

    request = QNetworkRequest(QUrl(zip_url))
    request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, f"{APP_NAME}-Updater")
    request.setRawHeader(b"Accept", b"application/octet-stream, application/zip, */*")
    request.setSslConfiguration(build_qt_ssl_configuration())
    request.setAttribute(
        QNetworkRequest.Attribute.RedirectPolicyAttribute,
        QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
    )
    if hasattr(QNetworkRequest.Attribute, "MaximumRedirectsAllowedAttribute"):
        request.setAttribute(QNetworkRequest.Attribute.MaximumRedirectsAllowedAttribute, MAX_REDIRECTS)

    manager = QNetworkAccessManager()
    reply = manager.get(request)
    loop = QEventLoop()

    timed_out = False
    timed_out_after_response = False
    cancelled = False
    saw_response = False
    ssl_error_detail = ""
    failure_message = ""
    last_bytes_received = 0

    def emit_progress(received: int, total: int) -> None:
        if progress_callback is None:
            return
        progress_callback(max(0, int(received)), int(total) if total > 0 else -1)

    emit_progress(0, -1)

    initial_timer = QTimer()
    initial_timer.setSingleShot(True)

    activity_timer = QTimer()
    activity_timer.setSingleShot(True)

    cancel_timer = QTimer()
    cancel_timer.setInterval(100)

    def mark_response_started() -> None:
        nonlocal saw_response
        if saw_response:
            return
        saw_response = True
        initial_timer.stop()
        activity_timer.start(INACTIVITY_TIMEOUT_MS)

    def abort_reply() -> None:
        if reply.isRunning():
            reply.abort()

    def on_initial_timeout() -> None:
        nonlocal timed_out, timed_out_after_response
        timed_out = True
        timed_out_after_response = False
        abort_reply()

    def on_activity_timeout() -> None:
        nonlocal timed_out, timed_out_after_response
        timed_out = True
        timed_out_after_response = True
        abort_reply()

    def on_cancel_timer() -> None:
        nonlocal cancelled
        if cancel_callback is not None and cancel_callback():
            cancelled = True
            abort_reply()

    def on_meta_data_changed() -> None:
        mark_response_started()

    def on_download_progress(received: int, total: int) -> None:
        nonlocal last_bytes_received
        if received > 0:
            mark_response_started()
        if received != last_bytes_received:
            activity_timer.start(INACTIVITY_TIMEOUT_MS)
            last_bytes_received = received
        emit_progress(received, total)

    def on_ssl_errors(errors) -> None:
        nonlocal ssl_error_detail
        details = [err.errorString().strip() for err in errors if err.errorString().strip()]
        ssl_error_detail = "; ".join(details)

    initial_timer.timeout.connect(on_initial_timeout)
    activity_timer.timeout.connect(on_activity_timeout)
    cancel_timer.timeout.connect(on_cancel_timer)
    reply.metaDataChanged.connect(on_meta_data_changed)
    reply.downloadProgress.connect(on_download_progress)
    reply.sslErrors.connect(on_ssl_errors)
    reply.finished.connect(loop.quit)

    initial_timer.start(INITIAL_RESPONSE_TIMEOUT_MS)
    cancel_timer.start()

    try:
        with part_path.open("wb") as out:
            def drain_reply() -> None:
                nonlocal failure_message
                while reply.bytesAvailable() > 0:
                    chunk = bytes(reply.read(1024 * 1024))
                    if not chunk:
                        break
                    try:
                        out.write(chunk)
                    except Exception as exc:
                        failure_message = f"The downloaded update could not be written to disk.\n\nDetails: {exc}"
                        abort_reply()
                        break

            reply.readyRead.connect(drain_reply)
            loop.exec()
            drain_reply()
    except Exception:
        _remove_file_if_exists(part_path)
        raise
    finally:
        initial_timer.stop()
        activity_timer.stop()
        cancel_timer.stop()
        reply.deleteLater()
        manager.deleteLater()
        del temporary_app

    if failure_message:
        _remove_file_if_exists(part_path)
        raise RuntimeError(failure_message)

    message = _describe_download_error(
        reply,
        timed_out=timed_out,
        timed_out_after_response=timed_out_after_response,
        cancelled=cancelled,
        ssl_error_detail=ssl_error_detail,
    )
    if message:
        _remove_file_if_exists(part_path)
        raise RuntimeError(message)

    if not part_path.exists() or part_path.stat().st_size <= 0:
        _remove_file_if_exists(part_path)
        raise RuntimeError("The downloaded update file was empty.")

    if out_path.exists():
        out_path.unlink()
    part_path.replace(out_path)
    return out_path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_update(path: Path, expected_sha256: str) -> UpdateVerificationResult:
    expected = _validated_sha256(expected_sha256)
    actual = sha256_file(path).lower()
    return UpdateVerificationResult(
        ok=bool(expected) and actual == expected,
        expected_sha256=expected,
        actual_sha256=actual,
    )


def release_page_url(zip_url: str) -> str:
    marker = "/releases/download/"
    if marker not in zip_url:
        return zip_url
    prefix, rest = zip_url.split(marker, 1)
    tag = rest.split("/", 1)[0]
    return f"{prefix}/releases/tag/{tag}"


def open_release_page(zip_url: str) -> None:
    target = release_page_url(zip_url)
    try:
        if webbrowser.open(target):
            return
    except Exception:
        pass

    try:
        subprocess.Popen(["open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        raise RuntimeError(f"Could not open the release page: {target}")


def open_latest_releases_page() -> None:
    try:
        if webbrowser.open(LATEST_RELEASES_URL):
            return
    except Exception:
        pass

    try:
        subprocess.Popen(["open", LATEST_RELEASES_URL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        raise RuntimeError(f"Could not open the release page: {LATEST_RELEASES_URL}")


def current_app_bundle_path() -> Path | None:
    exe = Path(sys.executable).resolve()
    if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        bundle = exe.parent.parent.parent
        if bundle.suffix == ".app":
            return bundle
    return None


def target_installed_app_path(installed_app_path: Path) -> Path:
    if installed_app_path.name == APP_BUNDLE_NAME:
        return installed_app_path
    return installed_app_path.with_name(APP_BUNDLE_NAME)


def legacy_app_path(installed_app_path: Path) -> Path | None:
    if installed_app_path.name != APP_BUNDLE_NAME:
        return None
    legacy = installed_app_path.with_name(LEGACY_APP_BUNDLE_NAME)
    if legacy == installed_app_path:
        return None
    return legacy


def is_app_bundle_in_applications(app_path: Path | None) -> bool:
    if app_path is None:
        return False
    try:
        resolved = app_path.resolve()
    except Exception:
        resolved = app_path
    try:
        return resolved.is_relative_to(Path("/Applications"))
    except AttributeError:
        return str(resolved).startswith("/Applications/")


def _resource_helper_path(bundle_path: Path) -> Path:
    return bundle_path / "Contents" / "Resources" / "install_update.py"


def _frameworks_helper_path(bundle_path: Path) -> Path:
    return bundle_path / "Contents" / "Frameworks" / "install_update.py"


def _subdir_helper_path(base_path: Path) -> Path:
    return base_path / UPDATE_HELPER_DIR / "install_update.py"


def _legacy_nested_helper_path(base_path: Path) -> Path:
    return base_path / "_internal" / "install_update.py"


def _install_helper_candidates() -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()

    def add_candidate(path: Path | None) -> None:
        if path is None:
            return
        if path in seen:
            return
        seen.add(path)
        candidates.append(path)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_path = Path(meipass).resolve()
        add_candidate(_subdir_helper_path(meipass_path))
        add_candidate(_legacy_nested_helper_path(meipass_path))
        add_candidate(meipass_path / "install_update.py")

    executable_parent = Path(sys.executable).resolve().parent
    add_candidate(executable_parent / "_internal" / UPDATE_HELPER_DIR / "install_update.py")
    add_candidate(executable_parent / UPDATE_HELPER_DIR / "install_update.py")
    add_candidate(executable_parent / "_internal" / "install_update.py")
    add_candidate(executable_parent / "install_update.py")

    bundle = current_app_bundle_path()
    if bundle is not None:
        add_candidate(bundle / "Contents" / "Resources" / UPDATE_HELPER_DIR / "install_update.py")
        add_candidate(bundle / "Contents" / "Frameworks" / UPDATE_HELPER_DIR / "install_update.py")
        add_candidate(bundle / "Contents" / "Resources" / "_internal" / "install_update.py")
        add_candidate(bundle / "Contents" / "Frameworks" / "_internal" / "install_update.py")
        add_candidate(_resource_helper_path(bundle))
        add_candidate(_frameworks_helper_path(bundle))

    add_candidate(Path(__file__).resolve().with_name("install_update.py"))
    return candidates


def install_helper_path() -> Path:
    candidates = _install_helper_candidates()
    for helper in candidates:
        if helper.exists():
            return helper
    searched = "\n".join(f"- {path}" for path in candidates)
    raise RuntimeError(f"Update helper is missing from this build.\n\nSearched:\n{searched}")


def extract_update_helper(zip_path: Path) -> Path:
    if not zip_path.exists():
        raise RuntimeError(f"The downloaded update zip could not be found: {zip_path}")

    with zipfile.ZipFile(zip_path) as archive:
        member_name = next(
            (
                name
                for name in archive.namelist()
                if any(name.endswith(suffix) for suffix in UPDATE_HELPER_ARCHIVE_SUFFIXES)
            ),
            None,
        )
        if member_name is None:
            searched = "\n".join(f"- *{suffix}" for suffix in UPDATE_HELPER_ARCHIVE_SUFFIXES)
            raise RuntimeError(
                "The downloaded update does not contain an installer helper.\n\n"
                f"Searched archive paths:\n{searched}"
            )

        helper_dir = Path(tempfile.mkdtemp(prefix="update_helper_", dir=str(zip_path.parent)))
        helper_path = helper_dir / "install_update.py"
        with archive.open(member_name) as src, helper_path.open("wb") as dst:
            dst.write(src.read())
        helper_path.chmod(0o700)
        return helper_path


def installer_helper_path(zip_path: Path) -> Path:
    try:
        return install_helper_path()
    except RuntimeError as install_error:
        try:
            return extract_update_helper(zip_path)
        except Exception as extract_error:
            raise RuntimeError(f"{install_error}\n\nCould not recover a helper from the downloaded update.\n\n{extract_error}") from extract_error


def bundled_python_path() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()

    bundle = current_app_bundle_path()
    if bundle is None:
        raise RuntimeError("Could not determine the installed app bundle path.")

    major = sys.version_info.major
    minor = sys.version_info.minor
    candidates = [
        bundle / "Contents" / "Frameworks" / "Python.framework" / "Versions" / "Current" / "bin" / f"python{major}.{minor}",
        bundle / "Contents" / "Frameworks" / "Python.framework" / "Versions" / "Current" / "bin" / "python3",
        bundle / "Contents" / "Frameworks" / "Python.framework" / "Versions" / f"{major}.{minor}" / "bin" / f"python{major}.{minor}",
        bundle / "Contents" / "Frameworks" / "Python.framework" / "Versions" / f"{major}.{minor}" / "bin" / "python3",
        bundle / "Contents" / "Resources" / "Python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("The bundled Python runtime could not be found.")


def write_update_installer_script(zip_path: Path, installed_app_path: Path) -> Path:
    target_app_path = target_installed_app_path(installed_app_path)
    legacy_path = legacy_app_path(target_app_path)
    existing_app_path = target_app_path if target_app_path.exists() else legacy_path if legacy_path and legacy_path.exists() else None
    restore_app_path = existing_app_path or target_app_path
    runtime_dir = Path(tempfile.mkdtemp(prefix="update_install_", dir=str(zip_path.parent)))
    script_path = runtime_dir / "install_update_runtime.sh"
    backup_app = restore_app_path.with_name(restore_app_path.stem + ".old.app")

    def shell_quote(value: str | Path) -> str:
        return shlex.quote(str(value))

    script = f"""#!/bin/zsh
set -euo pipefail

APP_PID={os.getpid()}
ZIP_PATH={shell_quote(zip_path)}
INSTALLED_APP={shell_quote(installed_app_path)}
TARGET_APP={shell_quote(target_app_path)}
BACKUP_APP={shell_quote(backup_app)}
EXISTING_APP={shell_quote(existing_app_path) if existing_app_path is not None else "''"}
RESTORE_APP={shell_quote(restore_app_path)}
LEGACY_APP={shell_quote(legacy_path) if legacy_path is not None else "''"}
RUNTIME_DIR={shell_quote(runtime_dir)}
STAGING_DIR="$RUNTIME_DIR/staging"

display_error() {{
  local message="$1"
  /usr/bin/osascript - "$message" <<'OSA' || true
on run argv
  display dialog (item 1 of argv) with title "{APP_DISPLAY_NAME} Update Failed" buttons {{"OK"}} default button "OK"
end run
OSA
}}

wait_for_pid() {{
  local pid="$1"
  local start="$SECONDS"
  while (( SECONDS - start < 30 )); do
    if ! /bin/kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    /bin/sleep 0.25
  done
  return 1
}}

replace_bundle() {{
  /bin/rm -rf "$BACKUP_APP"
  if [[ -n "$EXISTING_APP" && -e "$EXISTING_APP" ]]; then
    /bin/mv "$EXISTING_APP" "$BACKUP_APP"
  fi
  if ! /bin/mv "$NEW_APP" "$TARGET_APP"; then
    if [[ -e "$BACKUP_APP" && ! -e "$RESTORE_APP" ]]; then
      /bin/mv "$BACKUP_APP" "$RESTORE_APP" || true
    fi
    return 1
  fi
  if [[ -n "$LEGACY_APP" && "$LEGACY_APP" != "$TARGET_APP" && -e "$LEGACY_APP" ]]; then
    /bin/rm -rf "$LEGACY_APP"
  fi
  return 0
}}

cleanup_staging() {{
  /bin/rm -rf "$STAGING_DIR"
}}

trap cleanup_staging EXIT

if ! wait_for_pid "$APP_PID"; then
  display_error "Syllora did not close in time, so the update was not installed."
  exit 1
fi

if [[ ! -f "$ZIP_PATH" ]]; then
  display_error "The downloaded update zip could not be found: $ZIP_PATH"
  exit 1
fi

/bin/rm -rf "$STAGING_DIR"
/bin/mkdir -p "$STAGING_DIR"

if ! /usr/bin/ditto -x -k "$ZIP_PATH" "$STAGING_DIR"; then
  display_error "The downloaded update could not be extracted."
  exit 1
fi

APP_COUNT=$(/usr/bin/find "$STAGING_DIR" -type d -name {shell_quote(APP_BUNDLE_NAME)} -prune | /usr/bin/wc -l | /usr/bin/tr -d ' ')
if [[ "$APP_COUNT" == "0" ]]; then
  display_error "Extracted update does not contain {APP_BUNDLE_NAME}."
  exit 1
fi
if (( APP_COUNT > 1 )); then
  names=""
  while IFS= read -r app_path; do
    app_name=$(/usr/bin/basename "$app_path")
    if [[ -n "$names" ]]; then
      names="$names, $app_name"
    else
      names="$app_name"
    fi
  done < <(/usr/bin/find "$STAGING_DIR" -type d -name {shell_quote(APP_BUNDLE_NAME)} -prune | /usr/bin/head -n 5)
  display_error "Extracted update contains multiple {APP_BUNDLE_NAME} bundles: $names"
  exit 1
fi
NEW_APP="$("/usr/bin/find" "$STAGING_DIR" -type d -name {shell_quote(APP_BUNDLE_NAME)} -prune | /usr/bin/head -n 1)"

ACTUAL_BUNDLE_ID=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$NEW_APP/Contents/Info.plist" 2>/dev/null || true)
if [[ "$ACTUAL_BUNDLE_ID" != {shell_quote(APP_BUNDLE_IDENTIFIER)} ]]; then
  display_error "The update has an unexpected application identity and was not installed."
  exit 1
fi
if ! /usr/bin/codesign --verify --deep --strict "$NEW_APP" >/dev/null 2>&1; then
  display_error "The update's code signature is invalid."
  exit 1
fi

if ! replace_bundle; then
  PRIVILEGED_SCRIPT="$RUNTIME_DIR/privileged_install.sh"
  cat > "$PRIVILEGED_SCRIPT" <<'EOS'
#!/bin/zsh
set -euo pipefail
BACKUP_APP={shell_quote(backup_app)}
EXISTING_APP={shell_quote(existing_app_path) if existing_app_path is not None else "''"}
RESTORE_APP={shell_quote(restore_app_path)}
INSTALLED_APP={shell_quote(installed_app_path)}
TARGET_APP={shell_quote(target_app_path)}
LEGACY_APP={shell_quote(legacy_path) if legacy_path is not None else "''"}
NEW_APP="$1"
/bin/rm -rf "$BACKUP_APP"
if [[ -n "$EXISTING_APP" && -e "$EXISTING_APP" ]]; then
  /bin/mv "$EXISTING_APP" "$BACKUP_APP"
fi
if ! /bin/mv "$NEW_APP" "$TARGET_APP"; then
  if [[ -e "$BACKUP_APP" && ! -e "$RESTORE_APP" ]]; then
    /bin/mv "$BACKUP_APP" "$RESTORE_APP" || true
  fi
  exit 1
fi
if [[ -n "$LEGACY_APP" && "$LEGACY_APP" != "$TARGET_APP" && -e "$LEGACY_APP" ]]; then
  /bin/rm -rf "$LEGACY_APP"
fi
EOS
  /bin/chmod 700 "$PRIVILEGED_SCRIPT"
  if ! /usr/bin/osascript - "$PRIVILEGED_SCRIPT" "$NEW_APP" <<'OSA'
on run argv
  do shell script (quoted form of (item 1 of argv) & " " & quoted form of (item 2 of argv)) with administrator privileges
end run
OSA
  then
    display_error "The update could not be installed. Administrator approval was cancelled or failed."
    exit 1
  fi
fi

/usr/bin/open "$TARGET_APP" >/dev/null 2>&1 || true
(
  /bin/sleep 10
  /bin/rm -rf "$RUNTIME_DIR"
) >/dev/null 2>&1 &
exit 0
"""
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o700)
    return script_path


def start_update_installer(zip_path: Path, installed_app_path: Path) -> subprocess.Popen:
    script_path = write_update_installer_script(zip_path, installed_app_path)
    cmd = ["/bin/zsh", str(script_path)]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def expected_bundle_name() -> str:
    return APP_BUNDLE_NAME
