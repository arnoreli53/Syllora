import shutil
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
