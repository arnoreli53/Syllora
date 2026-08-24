from pathlib import Path

from PySide6.QtCore import QSettings

import paths
from version import (
    APP_SETTINGS_APP,
    APP_SETTINGS_ORG,
    LEGACY_APP_SETTINGS_APP,
    LEGACY_APP_SETTINGS_ORG,
)


def _file_settings() -> QSettings:
    paths.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    return QSettings(str(paths.SETTINGS_PATH), QSettings.Format.IniFormat)


def _legacy_settings_sources() -> tuple[QSettings, ...]:
    return (
        QSettings(APP_SETTINGS_ORG, APP_SETTINGS_APP),
        QSettings(LEGACY_APP_SETTINGS_ORG, LEGACY_APP_SETTINGS_APP),
    )


def app_qsettings() -> QSettings:
    settings = _file_settings()
    if not paths.ALLOW_LEGACY_SETTINGS_IMPORT:
        return settings
    if settings.value("_legacy_qsettings_migrated", False, type=bool):
        return settings

    new_keys = set(settings.allKeys())
    for legacy in _legacy_settings_sources():
        for key in legacy.allKeys():
            if key in new_keys:
                continue
            settings.setValue(key, legacy.value(key))
            new_keys.add(key)

    settings.setValue("_legacy_qsettings_migrated", True)
    settings.sync()
    return settings


def clear_all_app_qsettings() -> None:
    settings = _file_settings()
    settings.clear()
    settings.sync()

    settings_path = Path(settings.fileName())
    if settings_path.exists():
        settings_path.unlink()

    for settings in _legacy_settings_sources():
        settings.clear()
        settings.sync()
