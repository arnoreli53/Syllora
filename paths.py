import shutil
import sys
from pathlib import Path

from version import APP_DB_FILENAME, APP_NAME, LEGACY_APP_NAME, LEGACY_DB_FILENAME

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
LEGACY_APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / LEGACY_APP_NAME

DEFAULT_DB_PATH = APP_SUPPORT_DIR / APP_DB_FILENAME
DEFAULT_SETTINGS_PATH = APP_SUPPORT_DIR / "settings.ini"

DB_PATH = DEFAULT_DB_PATH
SETTINGS_PATH = DEFAULT_SETTINGS_PATH
ALLOW_LEGACY_DB_MIGRATION = True
ALLOW_LEGACY_SETTINGS_IMPORT = True
UPDATES_DIR = APP_SUPPORT_DIR / "updates"


def _migrate_legacy_app_support() -> None:
    if DEFAULT_DB_PATH.exists():
        return

    legacy_candidates = [
        APP_SUPPORT_DIR / LEGACY_DB_FILENAME,
        LEGACY_APP_SUPPORT_DIR / APP_DB_FILENAME,
        LEGACY_APP_SUPPORT_DIR / LEGACY_DB_FILENAME,
    ]
    for candidate in legacy_candidates:
        if candidate.exists():
            shutil.copy2(candidate, DEFAULT_DB_PATH)
            return


def set_active_data_paths(
    db_path: str | Path,
    settings_path: str | Path,
    *,
    allow_legacy_migration: bool = False,
    allow_legacy_settings_import: bool = False,
) -> None:
    global DB_PATH, SETTINGS_PATH, ALLOW_LEGACY_DB_MIGRATION, ALLOW_LEGACY_SETTINGS_IMPORT

    DB_PATH = Path(db_path)
    SETTINGS_PATH = Path(settings_path)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALLOW_LEGACY_DB_MIGRATION = bool(allow_legacy_migration)
    ALLOW_LEGACY_SETTINGS_IMPORT = bool(allow_legacy_settings_import)


_migrate_legacy_app_support()
UPDATES_DIR.mkdir(parents=True, exist_ok=True)


def current_app_bundle() -> Path | None:
    """The .app bundle root when running as a packaged macOS build, else None."""
    exe = Path(sys.executable).resolve()
    if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        bundle = exe.parent.parent.parent
        if bundle.suffix == ".app":
            return bundle
    return None


def resolve_app_icon_path() -> Path | None:
    """Locate the app's icon file across dev, PyInstaller, and bundled builds."""
    icns_candidates: list[Path] = []
    png_candidates: list[Path] = []
    seen: set[Path] = set()

    def add_candidate(path: Path) -> None:
        if path in seen:
            return
        seen.add(path)
        if path.suffix.lower() == ".icns":
            icns_candidates.append(path)
        else:
            png_candidates.append(path)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass).resolve()
        add_candidate(base / "icon.icns")
        add_candidate(base / "icon-windowed.icns")
        add_candidate(base / "icon.png")

    executable_parent = Path(sys.executable).resolve().parent
    add_candidate(executable_parent / "icon.icns")
    add_candidate(executable_parent / "icon-windowed.icns")
    add_candidate(executable_parent / "icon.png")
    add_candidate(executable_parent / "_internal" / "icon.icns")
    add_candidate(executable_parent / "_internal" / "icon-windowed.icns")
    add_candidate(executable_parent / "_internal" / "icon.png")

    bundle = current_app_bundle()
    if bundle is not None:
        resources = bundle / "Contents" / "Resources"
        add_candidate(resources / "icon.icns")
        add_candidate(resources / "icon-windowed.icns")
        add_candidate(resources / "icon.png")

    here = Path(__file__).resolve().parent
    add_candidate(here / "icon.icns")
    add_candidate(here / "icon-windowed.icns")
    add_candidate(here / "icon.png")

    for path in [*icns_candidates, *png_candidates]:
        if path.exists():
            return path
    return None
