import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import paths
from secret_store import delete_secret
from version import APP_DB_FILENAME

REGISTRY_PATH = paths.APP_SUPPORT_DIR / "users.json"
REGISTRY_BACKUP_PATH = paths.APP_SUPPORT_DIR / "users.json.bak"
USERS_DIR = paths.APP_SUPPORT_DIR / "users"
SETTINGS_FILENAME = "settings.ini"


@dataclass(frozen=True)
class UserProfile:
    id: str
    name: str
    db_path: Path
    settings_path: Path
    legacy_import: bool = False

    @property
    def data_dir(self) -> Path:
        return self.db_path.parent


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "user"


def _read_registry_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_registry() -> dict:
    data = _read_registry_file(REGISTRY_PATH)
    return data or _read_registry_file(REGISTRY_BACKUP_PATH)


def _write_registry(data: dict) -> None:
    paths.APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    if _read_registry_file(REGISTRY_PATH):
        shutil.copy2(REGISTRY_PATH, REGISTRY_BACKUP_PATH)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=paths.APP_SUPPORT_DIR,
            prefix="users-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, REGISTRY_PATH)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _path_from_registry(value: object, fallback: Path) -> Path:
    text = str(value or "").strip()
    if not text:
        return fallback
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = paths.APP_SUPPORT_DIR / path
    return path


def _profile_from_row(row: dict) -> UserProfile | None:
    if not isinstance(row, dict):
        return None
    profile_id = str(row.get("id", "")).strip()
    name = str(row.get("name", "")).strip()
    if not profile_id or not name:
        return None
    fallback_dir = USERS_DIR / profile_id
    return UserProfile(
        id=profile_id,
        name=name,
        db_path=_path_from_registry(row.get("db_path"), fallback_dir / APP_DB_FILENAME),
        settings_path=_path_from_registry(row.get("settings_path"), fallback_dir / SETTINGS_FILENAME),
        legacy_import=bool(row.get("legacy_import", False)),
    )


def _row_from_profile(profile: UserProfile) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "db_path": str(profile.db_path),
        "settings_path": str(profile.settings_path),
        "legacy_import": bool(profile.legacy_import),
    }


def _registry_profiles(data: dict | None = None) -> list[UserProfile]:
    source = _read_registry() if data is None else data
    raw_profiles = source.get("profiles", [])
    if not isinstance(raw_profiles, list):
        return []
    profiles: list[UserProfile] = []
    seen: set[str] = set()
    for row in raw_profiles:
        profile = _profile_from_row(row)
        if profile is None or profile.id in seen:
            continue
        profiles.append(profile)
        seen.add(profile.id)
    return profiles


def _legacy_data_exists() -> bool:
    return paths.DEFAULT_DB_PATH.exists() or paths.DEFAULT_SETTINGS_PATH.exists()


def needs_onboarding() -> bool:
    if not REGISTRY_PATH.exists():
        return not _legacy_data_exists()
    return not _registry_profiles(_read_registry()) and not _legacy_data_exists()


def _adopt_existing_data() -> UserProfile:
    return UserProfile(
        id="default",
        name="Default User",
        db_path=paths.DEFAULT_DB_PATH,
        settings_path=paths.DEFAULT_SETTINGS_PATH,
        legacy_import=True,
    )


def ensure_registry() -> UserProfile | None:
    data = _read_registry()
    profiles = _registry_profiles(data)

    if not profiles and _legacy_data_exists():
        profile = _adopt_existing_data()
        _write_registry(
            {
                "version": 1,
                "current_user_id": profile.id,
                "profiles": [_row_from_profile(profile)],
            }
        )
        return profile

    if not profiles:
        return None

    current_id = str(data.get("current_user_id", "")).strip()
    current = next((p for p in profiles if p.id == current_id), profiles[0])
    if current.id != current_id:
        data["current_user_id"] = current.id
        data["profiles"] = [_row_from_profile(p) for p in profiles]
        data["version"] = int(data.get("version", 1) or 1)
        _write_registry(data)
    return current


def list_profiles() -> list[UserProfile]:
    current = ensure_registry()
    profiles = _registry_profiles()
    if current is not None and not any(p.id == current.id for p in profiles):
        return [current]
    return profiles


def current_profile() -> UserProfile | None:
    data = _read_registry()
    profiles = _registry_profiles(data)
    current_id = str(data.get("current_user_id", "")).strip()
    return next((p for p in profiles if p.id == current_id), None) or (profiles[0] if profiles else None)


def activate_profile(profile: UserProfile) -> None:
    paths.set_active_data_paths(
        profile.db_path,
        profile.settings_path,
        allow_legacy_migration=profile.legacy_import,
        allow_legacy_settings_import=profile.legacy_import,
    )


def activate_current_profile() -> UserProfile | None:
    profile = ensure_registry()
    if profile is not None:
        activate_profile(profile)
    return profile


def set_current_profile(profile_id: str) -> UserProfile:
    data = _read_registry()
    profiles = _registry_profiles(data)
    profile = next((p for p in profiles if p.id == profile_id), None)
    if profile is None:
        raise RuntimeError("User profile not found.")
    data["version"] = int(data.get("version", 1) or 1)
    data["current_user_id"] = profile.id
    data["profiles"] = [_row_from_profile(p) for p in profiles]
    _write_registry(data)
    activate_profile(profile)
    return profile


def _unique_profile_id(name: str, profiles: list[UserProfile]) -> str:
    base = _slugify(name)
    used = {p.id for p in profiles}
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def create_profile(name: str, *, set_current: bool = False) -> UserProfile:
    clean_name = name.strip() or "New User"
    data = _read_registry()
    profiles = _registry_profiles(data)
    profile_id = _unique_profile_id(clean_name, profiles)
    data_dir = USERS_DIR / profile_id
    profile = UserProfile(
        id=profile_id,
        name=clean_name,
        db_path=data_dir / APP_DB_FILENAME,
        settings_path=data_dir / SETTINGS_FILENAME,
        legacy_import=False,
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    profiles.append(profile)
    data["version"] = int(data.get("version", 1) or 1)
    if set_current or not str(data.get("current_user_id", "")).strip():
        data["current_user_id"] = profile.id
    data["profiles"] = [_row_from_profile(p) for p in profiles]
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_registry(data)
    if data.get("current_user_id") == profile.id:
        activate_profile(profile)
    return profile


def delete_profile(profile_id: str) -> None:
    data = _read_registry()
    profiles = _registry_profiles(data)
    current_id = str(data.get("current_user_id", "")).strip()
    if profile_id == current_id:
        raise RuntimeError("Switch to another user before deleting this one.")
    if len(profiles) <= 1:
        raise RuntimeError("At least one user is required.")

    profile = next((p for p in profiles if p.id == profile_id), None)
    if profile is None:
        raise RuntimeError("User profile not found.")

    profiles = [p for p in profiles if p.id != profile_id]
    data["profiles"] = [_row_from_profile(p) for p in profiles]
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_registry(data)
    delete_secret(profile.id)

    profile_dir = profile.data_dir
    try:
        if profile_dir.exists() and profile_dir.is_dir() and profile_dir.parent == USERS_DIR:
            shutil.rmtree(profile_dir)
        else:
            for file_path in (profile.db_path, profile.settings_path):
                if file_path.exists():
                    file_path.unlink()
    except Exception:
        orphan_id = uuid4().hex[:8]
        orphan_dir = paths.APP_SUPPORT_DIR / f"deleted-user-{profile.id}-{orphan_id}"
        if profile_dir.exists() and profile_dir.is_dir():
            profile_dir.rename(orphan_dir)


def profile_summary(profile: UserProfile | None = None) -> dict:
    active = current_profile() if profile is None else profile
    if active is None:
        return {}
    return {
        "id": active.id,
        "name": active.name,
        "db_path": str(active.db_path),
        "settings_path": str(active.settings_path),
    }
