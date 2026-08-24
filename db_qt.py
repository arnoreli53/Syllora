import shutil
import sys
from pathlib import Path
from PySide6.QtSql import QSqlDatabase, QSqlQuery

import paths

DEFAULT_CONNECTION_NAME = "qt_sql_default_connection"


def _current_app_bundle() -> Path | None:
    exe = Path(sys.executable).resolve()
    if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        bundle = exe.parent.parent.parent
        if bundle.suffix == ".app":
            return bundle
    return None


def _legacy_bundle_db_paths() -> list[Path]:
    bundle = _current_app_bundle()
    if bundle is None:
        return []
    return [
        bundle / "Contents" / "Frameworks" / "gradetracker.db",
        bundle / "Contents" / "Resources" / "gradetracker.db",
    ]


def _migrate_legacy_bundle_db() -> None:
    if paths.DB_PATH.exists():
        return
    if not paths.ALLOW_LEGACY_DB_MIGRATION:
        return
    if not getattr(sys, "frozen", False):
        return

    paths.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    for legacy_path in _legacy_bundle_db_paths():
        if legacy_path.exists():
            shutil.copy2(legacy_path, paths.DB_PATH)
            return


def close_db() -> None:
    if not QSqlDatabase.contains(DEFAULT_CONNECTION_NAME):
        return
    db = QSqlDatabase.database(DEFAULT_CONNECTION_NAME, False)
    if db.isValid():
        db.close()
    del db
    QSqlDatabase.removeDatabase(DEFAULT_CONNECTION_NAME)


def open_db() -> QSqlDatabase:
    _migrate_legacy_bundle_db()

    close_db()
    paths.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    ## open local SQL database using SQL driver
    db = QSqlDatabase.addDatabase("QSQLITE")
    db.setDatabaseName(str(paths.DB_PATH))

    if not db.open():
        raise RuntimeError("Failed to open SQLite database")
    
    for statement in (
        "PRAGMA foreign_keys = ON;",
        "PRAGMA busy_timeout = 5000;",
        "PRAGMA journal_mode = WAL;",
        "PRAGMA synchronous = NORMAL;",
    ):
        query = QSqlQuery()
        if not query.exec(statement):
            error = query.lastError().text()
            db.close()
            raise RuntimeError(f"Failed to configure SQLite: {error}")

    return db
