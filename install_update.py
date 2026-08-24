import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from version import APP_BUNDLE_NAME, APP_DISPLAY_NAME, LEGACY_APP_BUNDLE_NAME


def wait_for_pid(pid: int, timeout: float = 30.0) -> None:
    start = time.time()
    while time.time() - start < timeout:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.25)


def display_error(message: str) -> None:
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display dialog "{escaped}" with title "{APP_DISPLAY_NAME} Update Failed" buttons {{"OK"}} default button "OK"'
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script], check=False)
    except Exception:
        print(message, file=sys.stderr)


def locate_app_bundle(root: Path) -> Path:
    apps = sorted(p for p in root.rglob("*.app") if p.is_dir())
    if not apps:
        raise RuntimeError("Extracted update does not contain an app bundle.")
    if len(apps) > 1:
        names = ", ".join(p.name for p in apps[:5])
        raise RuntimeError(f"Extracted update contains multiple app bundles: {names}")
    return apps[0]


def legacy_app_path(installed_app: Path) -> Path | None:
    if installed_app.name != APP_BUNDLE_NAME:
        return None
    legacy = installed_app.with_name(LEGACY_APP_BUNDLE_NAME)
    if legacy == installed_app:
        return None
    return legacy


def replace_app_bundle(new_app: Path, installed_app: Path, backup_app: Path, legacy_app: Path | None) -> None:
    existing_app = installed_app if installed_app.exists() else legacy_app if legacy_app and legacy_app.exists() else None
    restore_app = existing_app or installed_app

    try:
        if backup_app.exists():
            shutil.rmtree(backup_app)
        if existing_app is not None:
            shutil.move(str(existing_app), str(backup_app))
        try:
            shutil.move(str(new_app), str(installed_app))
            if legacy_app is not None and legacy_app != installed_app and legacy_app.exists():
                shutil.rmtree(legacy_app)
        except Exception:
            if backup_app.exists() and not restore_app.exists():
                shutil.move(str(backup_app), str(restore_app))
            raise
        return
    except PermissionError:
        pass
    except OSError:
        pass

    shell_script = "\n".join(
        [
            "#!/bin/zsh",
            "set -euo pipefail",
            f"BACKUP_APP={shlex.quote(str(backup_app))}",
            f"EXISTING_APP={shlex.quote(str(existing_app))}" if existing_app is not None else "EXISTING_APP=''",
            f"RESTORE_APP={shlex.quote(str(restore_app))}",
            f"INSTALLED_APP={shlex.quote(str(installed_app))}",
            f"LEGACY_APP={shlex.quote(str(legacy_app))}" if legacy_app is not None else "LEGACY_APP=''",
            f"NEW_APP={shlex.quote(str(new_app))}",
            'rm -rf "$BACKUP_APP"',
            'if [ -n "$EXISTING_APP" ] && [ -e "$EXISTING_APP" ]; then',
            '  mv "$EXISTING_APP" "$BACKUP_APP"',
            "fi",
            'if ! mv "$NEW_APP" "$INSTALLED_APP"; then',
            '  if [ -e "$BACKUP_APP" ] && [ ! -e "$RESTORE_APP" ]; then',
            '    mv "$BACKUP_APP" "$RESTORE_APP" || true',
            "  fi",
            "  exit 1",
            "fi",
            'if [ -n "$LEGACY_APP" ] && [ "$LEGACY_APP" != "$INSTALLED_APP" ] && [ -e "$LEGACY_APP" ]; then',
            '  rm -rf "$LEGACY_APP"',
            "fi",
        ]
    )
    script_path = new_app.parent / "install_update.sh"
    script_path.write_text(shell_script, encoding="utf-8")
    os.chmod(script_path, 0o700)

    osa = f'do shell script {json_string(str(script_path))} with administrator privileges'
    subprocess.run(["/usr/bin/osascript", "-e", osa], check=True)


def json_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def target_installed_app_path(installed_app: Path) -> Path:
    if installed_app.name == APP_BUNDLE_NAME:
        return installed_app
    return installed_app.with_name(APP_BUNDLE_NAME)


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: install_update.py <pid> <zip_path> <installed_app_path>")

    pid = int(sys.argv[1])
    zip_path = Path(sys.argv[2])
    installed_app = target_installed_app_path(Path(sys.argv[3]))
    legacy_installed_app = legacy_app_path(installed_app)

    try:
        wait_for_pid(pid)

        staging_dir = Path(tempfile.mkdtemp(prefix="install_", dir=str(zip_path.parent)))
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(staging_dir)

        new_app = locate_app_bundle(staging_dir)
        backup_app = installed_app.with_name(installed_app.stem + ".old.app")
        replace_app_bundle(new_app, installed_app, backup_app, legacy_installed_app)
        subprocess.Popen(["open", str(installed_app)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        display_error(str(exc))

if __name__ == "__main__":
    main()
