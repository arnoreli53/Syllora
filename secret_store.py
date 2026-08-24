from __future__ import annotations

import hashlib
import os
import subprocess
import sys

from version import APP_NAME


KEYCHAIN_SERVICE = f"{APP_NAME} OpenAI API"

# Fingerprints let older installations discard credentials that were previously
# shipped in the application without retaining those credentials in source.
COMPROMISED_SECRET_SHA256 = frozenset(
    {
        "eab67ee191e588e871aff6d27f061d59446f25809999ba5c0aaa9aae8a79c9b1",
    }
)


def is_compromised_secret(value: str) -> bool:
    secret = (value or "").strip()
    if not secret:
        return False
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return digest in COMPROMISED_SECRET_SHA256


def _account_name(profile_id: str) -> str:
    return (profile_id or "default").strip() or "default"


def _security_command(*args: str, secret: str | None = None) -> subprocess.CompletedProcess[str]:
    if sys.platform != "darwin":
        raise RuntimeError("Secure API-key storage is only available in the macOS app.")

    environment = os.environ.copy()
    return subprocess.run(
        ["/usr/bin/security", *args],
        input=secret,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )


def load_secret(profile_id: str) -> str:
    if sys.platform != "darwin":
        return ""

    result = _security_command(
        "find-generic-password",
        "-a",
        _account_name(profile_id),
        "-s",
        KEYCHAIN_SERVICE,
        "-w",
    )
    if result.returncode != 0:
        return ""
    secret = result.stdout.strip()
    if is_compromised_secret(secret):
        delete_secret(profile_id)
        return ""
    return secret


def save_secret(profile_id: str, value: str) -> None:
    secret = (value or "").strip()
    if not secret:
        delete_secret(profile_id)
        return
    if is_compromised_secret(secret):
        raise RuntimeError("That API key was included in an older build and has been disabled. Create a new key instead.")

    # `security` does not accept the password over stdin for add-generic-password,
    # so pass it as a discrete argument and never log the command or its output.
    result = _security_command(
        "add-generic-password",
        "-U",
        "-a",
        _account_name(profile_id),
        "-s",
        KEYCHAIN_SERVICE,
        "-w",
        secret,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "macOS Keychain rejected the API key."
        raise RuntimeError(detail)


def delete_secret(profile_id: str) -> None:
    if sys.platform != "darwin":
        return
    _security_command(
        "delete-generic-password",
        "-a",
        _account_name(profile_id),
        "-s",
        KEYCHAIN_SERVICE,
    )
