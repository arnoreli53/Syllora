from datetime import datetime
from functools import wraps

from PySide6.QtSql import QSqlDatabase, QSqlQuery


def _exec(sql: str) -> None:
    """Execute a SQL statement and raise a readable error if it fails."""
    q = QSqlQuery()
    ok = q.exec(sql)
    if not ok:
        raise RuntimeError(q.lastError().text())


def _atomic_migration(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        db = QSqlDatabase.database()
        if not db.isValid() or not db.isOpen():
            raise RuntimeError("Database is not available.")
        if not db.transaction():
            raise RuntimeError(db.lastError().text())
        try:
            result = function(*args, **kwargs)
            if not db.commit():
                raise RuntimeError(db.lastError().text())
            return result
        except Exception:
            db.rollback()
            raise

    return wrapper


def _current_academic_year_start() -> int:
    current = datetime.now()
    return current.year if current.month >= 8 else current.year - 1


def _current_course_term() -> str:
    current = datetime.now()
    if current.month >= 8:
        return "fall"
    if current.month <= 4:
        return "winter"
    return "summer"


@_atomic_migration
def ensure_schema() -> None:
    """Create required tables if they do not exist yet."""

    # Use CURRENT_TIMESTAMP (no parentheses) to avoid SQLite syntax issues.

    _exec(
        """
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            term TEXT DEFAULT '',
            academic_year_start INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    _exec(
        """
        CREATE TABLE IF NOT EXISTS previous_courses (
            id INTEGER PRIMARY KEY,
            course_name TEXT NOT NULL,
            final_percent REAL NOT NULL,
            final_letter TEXT NOT NULL DEFAULT '',
            credit_hours REAL NOT NULL,
            term TEXT NOT NULL,
            academic_year_start INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    _exec(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY,
            course_id INTEGER NOT NULL,
            item TEXT NOT NULL,
            component TEXT DEFAULT '',
            due_datetime TEXT,
            status TEXT NOT NULL DEFAULT 'not started',
            weight REAL,
            grade REAL,
            ungraded INTEGER NOT NULL DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
        );
        """
    )

    q = QSqlQuery()
    q.exec("PRAGMA table_info(courses);")
    course_cols = []
    while q.next():
        course_cols.append(str(q.value(1)))

    if "term" not in course_cols:
        _exec("ALTER TABLE courses ADD COLUMN term TEXT DEFAULT '';")

    if "academic_year_start" not in course_cols:
        _exec("ALTER TABLE courses ADD COLUMN academic_year_start INTEGER DEFAULT 0;")

    current_term = _current_course_term()
    current_year_start = _current_academic_year_start()
    _exec(
        f"""
        UPDATE courses
        SET term = '{current_term}'
        WHERE coalesce(trim(term), '') = '';
        """
    )
    _exec(
        f"""
        UPDATE courses
        SET academic_year_start = {current_year_start}
        WHERE coalesce(academic_year_start, 0) <= 0;
        """
    )

    q = QSqlQuery()
    q.exec("PRAGMA table_info(previous_courses);")
    previous_course_cols = []
    while q.next():
        previous_course_cols.append(str(q.value(1)))

    if "final_letter" not in previous_course_cols:
        _exec("ALTER TABLE previous_courses ADD COLUMN final_letter TEXT NOT NULL DEFAULT '';")

    q = QSqlQuery()
    q.exec("PRAGMA table_info(tasks);")
    cols = []
    while q.next():
        cols.append(str(q.value(1)))  # column name

    if "project_id" in cols:
        # Rebuild tasks without project_id.
        _exec("DROP TABLE IF EXISTS tasks_new;")

        _exec(
            """
            CREATE TABLE IF NOT EXISTS tasks_new (
                id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                item TEXT NOT NULL,
                component TEXT DEFAULT '',
                due_datetime TEXT,
                status TEXT NOT NULL DEFAULT 'not started',
                weight REAL,
                grade REAL,
                ungraded INTEGER NOT NULL DEFAULT 0,
                notes TEXT DEFAULT '',
                priority TEXT DEFAULT 'normal',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
            );
            """
        )

        # Copy existing data (ignore project_id).
        _exec(
            """
            INSERT INTO tasks_new (
                id, course_id, item, component, due_datetime, status,
                weight, grade, ungraded, notes, priority, created_at, updated_at
            )
            SELECT
                id, course_id, item, component, due_datetime, status,
                weight, grade, ungraded, notes, 'normal', created_at, updated_at
            FROM tasks;
            """
        )

        _exec("DROP TABLE tasks;")
        _exec("ALTER TABLE tasks_new RENAME TO tasks;")


    # --- Migration: add priority column if missing ---
    q.exec("PRAGMA table_info(tasks);")
    cols = []
    while q.next():
        cols.append(str(q.value(1)))  # column name

    if "priority" not in cols:
        # Rebuild tasks to add priority column.
        _exec("DROP TABLE IF EXISTS tasks_new;")

        _exec(
            """
            CREATE TABLE IF NOT EXISTS tasks_new (
                id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                item TEXT NOT NULL,
                component TEXT DEFAULT '',
                due_datetime TEXT,
                status TEXT NOT NULL DEFAULT 'not started',
                weight REAL,
                grade REAL,
                ungraded INTEGER NOT NULL DEFAULT 0,
                notes TEXT DEFAULT '',
                priority TEXT DEFAULT 'normal',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
            );
            """
        )

        # Copy existing data, setting priority to 'normal'.
        _exec(
            """
            INSERT INTO tasks_new (
                id, course_id, item, component, due_datetime, status,
                weight, grade, ungraded, notes, priority, created_at, updated_at
            )
            SELECT
                id, course_id, item, component, due_datetime, status,
                weight, grade, ungraded, notes, 'normal', created_at, updated_at
            FROM tasks;
            """
        )

        _exec("DROP TABLE tasks;")
        _exec("ALTER TABLE tasks_new RENAME TO tasks;")

    q.exec("PRAGMA table_info(tasks);")
    cols = []
    while q.next():
        cols.append(str(q.value(1)))

    extra_columns = {
        "due_status": "TEXT DEFAULT 'unknown'",
        "weight_raw_value": "REAL",
        "weight_raw_unit": "TEXT DEFAULT ''",
        "weight_source": "TEXT DEFAULT 'missing'",
        "syllabus_metadata": "TEXT DEFAULT ''",
        "task_type": "TEXT DEFAULT ''",
    }
    for name, ddl in extra_columns.items():
        if name not in cols:
            q_add = QSqlQuery()
            if not q_add.exec(f"ALTER TABLE tasks ADD COLUMN {name} {ddl};"):
                err = q_add.lastError().text().lower()
                if "duplicate column name" not in err:
                    raise RuntimeError(q_add.lastError().text())

    _exec(
        """
        UPDATE tasks
        SET due_status = CASE
            WHEN coalesce(trim(due_datetime), '') <> '' THEN 'exact'
            ELSE 'unknown'
        END
        WHERE coalesce(trim(due_status), '') = ''
           OR (
                coalesce(trim(due_status), '') = 'unknown'
                AND coalesce(trim(syllabus_metadata), '') = ''
           );
        """
    )

    _exec(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
