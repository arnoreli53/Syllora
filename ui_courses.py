from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt, QRect, QTimer, Signal, QEvent, QModelIndex, QObject, QThread, QPropertyAnimation, QParallelAnimationGroup, QEasingCurve
from PySide6.QtGui import QColor, QBrush, QPalette, QLinearGradient
from PySide6.QtSql import QSqlDatabase, QSqlQuery, QSqlTableModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from transcript_import import (
    TranscriptCourseRow,
    TranscriptExtractionResult,
    TranscriptImportCoordinator,
    transcript_openai_available,
)
from ui_common import apply_body_font, common_page_stylesheet, font_metrics, make_version_label, scaled_row_height, theme_colors
from ui_settings import (
    get_compact_rows,
    get_confirm_delete,
    get_effective_openai_api_key,
    get_font_size_mode,
    get_gpa_last_x_credit_hours,
    get_gpa_view_mode,
    get_grade_calc_mode,
    get_syllabus_ai_model,
    get_theme,
    grade_scale_row_for_gpa43,
    grade_scale_row_for_percent,
    grade_scale_row_for_label,
    gpa4_for_grade_label,
    gpa4_for_percent,
    gpa43_for_grade_label,
    gpa43_for_percent,
    parse_task_grade_input,
)


PREVIOUS_COURSE_TERMS: list[tuple[str, str]] = [
    ("fall", "Fall"),
    ("winter", "Winter"),
    ("summer", "Summer"),
]
PREVIOUS_COURSE_TERM_RANK = {"fall": 0, "winter": 1, "summer": 2}
PREVIOUS_COURSE_YEAR_COUNT = 30


def normalize_previous_course_term(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in PREVIOUS_COURSE_TERM_RANK else "fall"


def previous_course_term_label(value: object) -> str:
    normalized = normalize_previous_course_term(value)
    for term_value, label in PREVIOUS_COURSE_TERMS:
        if term_value == normalized:
            return label
    return "Fall"


def previous_course_term_rank(value: object) -> int:
    return PREVIOUS_COURSE_TERM_RANK.get(normalize_previous_course_term(value), -1)


def current_academic_year_start(now: datetime | None = None) -> int:
    current = now or datetime.now()
    return current.year if current.month >= 8 else current.year - 1


def current_previous_course_term(now: datetime | None = None) -> str:
    current = now or datetime.now()
    if current.month >= 8:
        return "fall"
    if current.month <= 4:
        return "winter"
    return "summer"


def previous_course_academic_year_label(start_year: int) -> str:
    return f"{start_year}/{start_year + 1}"


def previous_course_academic_year_options(
    count: int = PREVIOUS_COURSE_YEAR_COUNT,
    now: datetime | None = None,
) -> list[tuple[str, int]]:
    current = now or datetime.now()
    start_year = max(current_academic_year_start(current), current.year)
    return [
        (previous_course_academic_year_label(year), year)
        for year in range(start_year, start_year - count, -1)
    ]


def current_course_can_complete(summary: dict | None) -> bool:
    return bool(
        summary
        and summary.get("is_complete")
        and (
            summary.get("final_percent") is not None
            or summary.get("final_gpa43") is not None
            or str(summary.get("final_letter", "") or "").strip()
        )
    )


def course_success_button_styles(mode: str, theme: str) -> str:
    metrics = font_metrics(mode)
    _colors = theme_colors(theme)
    return f"""
        QPushButton#CourseSuccessButton {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #4CC983, stop:1 #2D9B5C);
            color: #FFFFFF;
            border: none;
            border-radius: 8px;
            padding: 8px 18px 9px 18px;
            font-weight: 600;
            font-size: {metrics['button']}px;
            min-width: 80px;
            min-height: 20px;
        }}
        QPushButton#CourseSuccessButton:hover {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #61D591, stop:1 #35AA67);
        }}
        QPushButton#CourseSuccessButton:pressed {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #2E955B, stop:1 #237347);
        }}
        QPushButton#CourseSuccessButton:disabled {{
            background: {_colors['secondary_bg']};
            color: {_colors['muted_text']};
            border: 1px solid {_colors['input_border']};
        }}
    """


def format_credit_hours(value: float | int | None) -> str:
    if value is None:
        return "0"
    try:
        number = float(value)
    except Exception:
        return "0"
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def format_previous_course_percent(value: float | int | None) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except Exception:
        return ""
    if number < 0:
        return ""
    return f"{number:.2f}".rstrip("0").rstrip(".")


def format_previous_course_grade(row: dict | None = None, *, percent: float | int | None = None, letter: object = "") -> str:
    if row is not None:
        percent = row.get("final_percent")
        letter = row.get("final_letter", "")
    letter_text = str(letter or "").strip()
    if letter_text:
        return letter_text
    return format_previous_course_percent(percent)


def parse_previous_course_grade_input(value: object) -> tuple[float, str] | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    numeric_text = text.replace("%", "").replace("％", "").strip()
    try:
        numeric = float(numeric_text)
    except Exception:
        numeric = None
    if numeric is not None:
        if 0.0 <= numeric <= 100.0:
            return numeric, ""
        return None
    grade_row = grade_scale_row_for_label(text)
    if grade_row is None:
        return None
    label = str(grade_row.get("label", "") or "").strip() or text
    return -1.0, label


def list_previous_courses() -> list[dict]:
    rows: list[dict] = []
    q = QSqlQuery()
    if not q.exec(
        """
        SELECT
            id,
            course_name,
            final_percent,
            final_letter,
            credit_hours,
            lower(term),
            academic_year_start,
            created_at
        FROM previous_courses
        ORDER BY
            academic_year_start DESC,
            CASE lower(term)
                WHEN 'summer' THEN 2
                WHEN 'winter' THEN 1
                ELSE 0
            END DESC,
            created_at DESC,
            id DESC
        """
    ):
        return rows

    while q.next():
        rows.append(
                {
                    "id": int(q.value(0)),
                    "course_name": str(q.value(1) or "").strip(),
                    "final_percent": float(q.value(2) or 0.0),
                    "final_letter": str(q.value(3) or "").strip(),
                    "credit_hours": float(q.value(4) or 0.0),
                    "term": normalize_previous_course_term(q.value(5)),
                    "academic_year_start": int(q.value(6) or current_academic_year_start()),
                    "created_at": str(q.value(7) or ""),
                }
            )
    return rows


def save_previous_courses(rows: list[dict]) -> None:
    db = QSqlDatabase.database()
    if not db.isValid():
        raise RuntimeError("Database is not available.")
    if not db.transaction():
        raise RuntimeError(db.lastError().text())

    try:
        existing_ids: set[int] = set()
        q_existing = QSqlQuery()
        if not q_existing.exec("SELECT id FROM previous_courses"):
            raise RuntimeError(q_existing.lastError().text())
        while q_existing.next():
            existing_ids.add(int(q_existing.value(0)))

        kept_ids: set[int] = set()
        for row in rows:
            course_name = str(row.get("course_name") or "").strip()
            term = normalize_previous_course_term(row.get("term"))
            try:
                credit_hours = float(row.get("credit_hours"))
                academic_year_start = int(row.get("academic_year_start"))
            except Exception as exc:
                raise RuntimeError("Historical course values are invalid.") from exc
            final_letter = str(row.get("final_letter", "") or "").strip()
            try:
                final_percent = float(row.get("final_percent"))
            except Exception:
                final_percent = -1.0

            if not course_name:
                raise RuntimeError("Historical courses must include a course name.")
            if final_percent < 0 and not final_letter:
                raise RuntimeError("Historical courses must include a final grade.")
            if credit_hours <= 0:
                raise RuntimeError("Historical courses must include positive credit hours.")

            row_id = row.get("id")
            try:
                row_id_int = int(row_id) if row_id is not None else None
            except Exception:
                row_id_int = None

            if row_id_int is not None and row_id_int in existing_ids:
                q_update = QSqlQuery()
                q_update.prepare(
                    """
                    UPDATE previous_courses
                    SET course_name=?, final_percent=?, final_letter=?, credit_hours=?, term=?, academic_year_start=?
                    WHERE id=?
                    """
                )
                q_update.addBindValue(course_name)
                q_update.addBindValue(final_percent)
                q_update.addBindValue(final_letter)
                q_update.addBindValue(credit_hours)
                q_update.addBindValue(term)
                q_update.addBindValue(academic_year_start)
                q_update.addBindValue(row_id_int)
                if not q_update.exec():
                    raise RuntimeError(q_update.lastError().text())
                kept_ids.add(row_id_int)
                continue

            q_insert = QSqlQuery()
            q_insert.prepare(
                """
                INSERT INTO previous_courses(course_name, final_percent, final_letter, credit_hours, term, academic_year_start)
                VALUES(?, ?, ?, ?, ?, ?)
                """
            )
            q_insert.addBindValue(course_name)
            q_insert.addBindValue(final_percent)
            q_insert.addBindValue(final_letter)
            q_insert.addBindValue(credit_hours)
            q_insert.addBindValue(term)
            q_insert.addBindValue(academic_year_start)
            if not q_insert.exec():
                raise RuntimeError(q_insert.lastError().text())

        for deleted_id in sorted(existing_ids - kept_ids, reverse=True):
            q_delete = QSqlQuery()
            q_delete.prepare("DELETE FROM previous_courses WHERE id=?")
            q_delete.addBindValue(deleted_id)
            if not q_delete.exec():
                raise RuntimeError(q_delete.lastError().text())

        if not db.commit():
            raise RuntimeError(db.lastError().text())
    except Exception:
        db.rollback()
        raise


def calculate_previous_course_gpa_metrics(rows: list[dict], last_x_credit_hours: int) -> dict[str, dict]:
    normalized_rows: list[dict] = []
    for row in rows:
        try:
            credit_hours = float(row.get("credit_hours"))
            academic_year_start = int(row.get("academic_year_start"))
        except Exception:
            continue
        if credit_hours <= 0:
            continue
        try:
            final_percent = float(row.get("final_percent"))
        except Exception:
            final_percent = -1.0
        final_letter = str(row.get("final_letter", "") or "").strip()

        gpa_points = gpa4_for_percent(final_percent) if final_percent >= 0 else gpa4_for_grade_label(final_letter)
        if gpa_points is None:
            continue

        normalized_rows.append(
            {
                **row,
                "credit_hours": credit_hours,
                "final_percent": final_percent,
                "final_letter": final_letter,
                "academic_year_start": academic_year_start,
                "term": normalize_previous_course_term(row.get("term")),
                "gpa_points": float(gpa_points),
            }
        )

    normalized_rows.sort(
        key=lambda row: (
            int(row.get("academic_year_start") or 0),
            previous_course_term_rank(row.get("term")),
            str(row.get("created_at") or ""),
            int(row.get("id") or 0),
        ),
        reverse=True,
    )

    metrics: dict[str, dict] = {}
    if not normalized_rows:
        return metrics

    cumulative_credits = sum(float(row["credit_hours"]) for row in normalized_rows)
    cumulative_quality_points = sum(float(row["credit_hours"]) * float(row["gpa_points"]) for row in normalized_rows)
    if cumulative_credits > 0:
        metrics["cumulative"] = {
            "title": "Cumulative",
            "gpa": cumulative_quality_points / cumulative_credits,
            "credits": cumulative_credits,
            "course_count": len(normalized_rows),
        }

    selected_rows: list[dict] = []
    selected_credits = 0.0
    for row in normalized_rows:
        selected_rows.append(row)
        selected_credits += float(row["credit_hours"])
        if selected_credits >= float(last_x_credit_hours):
            break

    if selected_rows and selected_credits > 0:
        selected_quality_points = sum(
            float(row["credit_hours"]) * float(row["gpa_points"])
            for row in selected_rows
        )
        metrics["last_x"] = {
            "title": f"Last {last_x_credit_hours} Credit Hours",
            "gpa": selected_quality_points / selected_credits,
            "credits": selected_credits,
            "course_count": len(selected_rows),
            "target_credits": int(last_x_credit_hours),
            "met_target": selected_credits >= float(last_x_credit_hours),
        }

    return metrics


CURRENT_COURSE_REGULAR_WEIGHT_SUM_EXPR = """
    SUM(CASE
        WHEN t.weight IS NOT NULL AND trim(t.weight) <> ''
         AND t.grade  IS NOT NULL AND trim(t.grade)  <> ''
         AND lower(coalesce(t.task_type, '')) <> 'ungraded'
         AND lower(coalesce(t.task_type, '')) <> 'bonus'
         AND coalesce(t.ungraded, 0) = 0
         AND CAST(t.weight AS REAL) > 0
        THEN CAST(t.weight AS REAL)
        ELSE 0
    END)
"""

CURRENT_COURSE_REGULAR_POINTS_EXPR = """
    SUM(CASE
        WHEN t.weight IS NOT NULL AND trim(t.weight) <> ''
         AND t.grade  IS NOT NULL AND trim(t.grade)  <> ''
         AND lower(coalesce(t.task_type, '')) <> 'ungraded'
         AND lower(coalesce(t.task_type, '')) <> 'bonus'
         AND coalesce(t.ungraded, 0) = 0
         AND CAST(t.weight AS REAL) > 0
        THEN (CAST(t.weight AS REAL) * CAST(t.grade AS REAL) / 100.0)
        ELSE 0
    END)
"""

CURRENT_COURSE_BONUS_POINTS_EXPR = """
    SUM(CASE
        WHEN t.weight IS NOT NULL AND trim(t.weight) <> ''
         AND t.grade  IS NOT NULL AND trim(t.grade)  <> ''
         AND lower(coalesce(t.task_type, '')) = 'bonus'
         AND coalesce(t.ungraded, 0) = 0
         AND CAST(t.weight AS REAL) > 0
        THEN (CAST(t.weight AS REAL) * CAST(t.grade AS REAL) / 100.0)
        ELSE 0
    END)
"""

CURRENT_COURSE_FINAL_PERCENT_EXPR = f"""
    CASE
        WHEN ({CURRENT_COURSE_REGULAR_WEIGHT_SUM_EXPR}) = 0
        THEN NULL
        ELSE (
            (
                ({CURRENT_COURSE_REGULAR_POINTS_EXPR})
                + ({CURRENT_COURSE_BONUS_POINTS_EXPR})
            )
            /
            ({CURRENT_COURSE_REGULAR_WEIGHT_SUM_EXPR})
            * 100.0
        )
    END
"""


def format_grade_percent(value: float | int | None) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except Exception:
        return "—"
    return f"{number:.1f}%"


def format_current_course_grade(summary: dict | None) -> str:
    if not summary:
        return "—"
    final_letter = str(summary.get("final_letter", "") or "").strip()
    final_gpa43 = summary.get("final_gpa43")
    if final_letter:
        try:
            gpa_text = f"{float(final_gpa43):.2f}".rstrip("0").rstrip(".")
        except Exception:
            gpa_text = ""
        return f"{final_letter} ({gpa_text})" if gpa_text else final_letter
    if final_gpa43 is not None:
        try:
            return f"{float(final_gpa43):.2f}".rstrip("0").rstrip(".")
        except Exception:
            return "—"
    return format_grade_percent(summary.get("final_percent"))


def _format_grade_band_gpa(value: float | int | None) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except Exception:
        return "—"
    if abs(number - round(number)) <= 1e-9:
        return f"{int(round(number))}.0"
    return f"{number:.1f}"


def format_current_course_grade_for_mode(summary: dict | None, grade_mode: str) -> str:
    if not summary:
        return "—"

    final_letter = str(summary.get("final_letter", "") or "").strip()
    final_percent = summary.get("final_percent")
    final_gpa4 = summary.get("final_gpa4")
    final_gpa43 = summary.get("final_gpa43")

    if final_letter:
        if grade_mode == "gpa4.0":
            return _format_grade_band_gpa(final_gpa4)
        if grade_mode == "gpa4.3":
            return _format_grade_band_gpa(final_gpa43)
        return final_letter

    if final_percent is None:
        return "—"

    if grade_mode == "letter":
        row = grade_scale_row_for_percent(final_percent)
        if not row:
            return "—"
        label = str(row.get("label", "") or "").strip()
        return label or "—"
    if grade_mode == "gpa4.0":
        return _format_grade_band_gpa(gpa4_for_percent(final_percent))
    if grade_mode == "gpa4.3":
        return _format_grade_band_gpa(gpa43_for_percent(final_percent))
    return format_grade_percent(final_percent)


def query_rows_as_dicts(sql: str, bind_values: list[object] | tuple[object, ...] | None = None) -> list[dict]:
    query = QSqlQuery()
    if bind_values is None:
        if not query.exec(sql):
            raise RuntimeError(query.lastError().text())
    else:
        query.prepare(sql)
        for value in bind_values:
            query.addBindValue(value)
        if not query.exec():
            raise RuntimeError(query.lastError().text())

    record = query.record()
    field_names = [record.fieldName(i) for i in range(record.count())]
    rows: list[dict] = []
    while query.next():
        rows.append({field_name: query.value(i) for i, field_name in enumerate(field_names)})
    return rows


def insert_row_dict(table_name: str, row: dict) -> None:
    columns = list(row.keys())
    placeholders = ", ".join(["?"] * len(columns))
    query = QSqlQuery()
    query.prepare(f"INSERT INTO {table_name}({', '.join(columns)}) VALUES({placeholders})")
    for column in columns:
        query.addBindValue(row.get(column))
    if not query.exec():
        raise RuntimeError(query.lastError().text())


def update_row_dict(table_name: str, row: dict, where_key: str) -> None:
    columns = [column for column in row.keys() if column != where_key]
    query = QSqlQuery()
    query.prepare(
        f"UPDATE {table_name} SET {', '.join(f'{column}=?' for column in columns)} WHERE {where_key}=?"
    )
    for column in columns:
        query.addBindValue(row.get(column))
    query.addBindValue(row.get(where_key))
    if not query.exec():
        raise RuntimeError(query.lastError().text())


def ensure_current_courses_have_metadata() -> None:
    db = QSqlDatabase.database()
    if not db.isValid() or not db.isOpen():
        return
    current_term = current_previous_course_term()
    current_year_start = current_academic_year_start()
    query = QSqlQuery()
    if not query.exec(
        f"""
        UPDATE courses
        SET
            term = CASE
                WHEN coalesce(trim(term), '') = '' THEN '{current_term}'
                ELSE lower(trim(term))
            END,
            academic_year_start = CASE
                WHEN coalesce(academic_year_start, 0) <= 0 THEN {current_year_start}
                ELSE academic_year_start
            END
        """
    ):
        raise RuntimeError(query.lastError().text())


def list_current_course_completion_state() -> dict[int, dict]:
    db = QSqlDatabase.database()
    if not db.isValid() or not db.isOpen():
        return {}
    course_rows = query_rows_as_dicts(
        """
        SELECT
            id AS course_id,
            name AS course_name,
            lower(coalesce(term, '')) AS term,
            coalesce(academic_year_start, 0) AS academic_year_start
        FROM courses
        ORDER BY id ASC
        """
    )
    task_rows = query_rows_as_dicts(
        """
        SELECT
            course_id,
            lower(coalesce(status, '')) AS status,
            weight,
            grade,
            lower(coalesce(task_type, '')) AS task_type,
            coalesce(ungraded, 0) AS ungraded
        FROM tasks
        ORDER BY course_id ASC, id ASC
        """
    )

    tasks_by_course: dict[int, list[dict]] = {}
    for row in task_rows:
        try:
            course_id = int(row.get("course_id") or 0)
        except Exception:
            continue
        tasks_by_course.setdefault(course_id, []).append(row)

    grade_calc_mode = get_grade_calc_mode()
    state: dict[int, dict] = {}
    for course in course_rows:
        try:
            course_id = int(course.get("course_id") or 0)
        except Exception:
            continue

        course_task_rows = tasks_by_course.get(course_id, [])
        tracked_task_count = 0
        graded_tracked_task_count = 0
        uses_letter_grades = False

        for row in course_task_rows:
            task_type = str(row.get("task_type", "") or "").strip().lower()
            is_ungraded = bool(row.get("ungraded") or 0) or task_type == "ungraded"
            if is_ungraded:
                continue
            parsed_grade = parse_task_grade_input(row.get("grade"))
            if parsed_grade is not None and parsed_grade[1] == "letter":
                uses_letter_grades = True
            if task_type != "bonus":
                tracked_task_count += 1
                if str(row.get("status", "") or "").strip().lower() == "graded":
                    graded_tracked_task_count += 1

        regular_weight_sum = 0.0
        regular_points_percent = 0.0
        regular_points_gpa4 = 0.0
        regular_points_gpa43 = 0.0
        bonus_points_percent = 0.0
        bonus_points_gpa4 = 0.0
        bonus_points_gpa43 = 0.0

        for row in course_task_rows:
            task_type = str(row.get("task_type", "") or "").strip().lower()
            is_ungraded = bool(row.get("ungraded") or 0) or task_type == "ungraded"
            if is_ungraded:
                continue

            parsed_grade = parse_task_grade_input(row.get("grade"))
            status = str(row.get("status", "") or "").strip().lower()
            count_as_zero = grade_calc_mode == "submitted_as_zero" and status == "submitted"
            if parsed_grade is None and not count_as_zero:
                continue
            normalized_grade, grade_kind = parsed_grade if parsed_grade is not None else (0.0, "percent")

            try:
                weight = float(row.get("weight"))
            except Exception:
                weight = 0.0
            if weight <= 0:
                continue

            if uses_letter_grades:
                grade_value_gpa43 = (
                    gpa43_for_grade_label(normalized_grade)
                    if grade_kind == "letter"
                    else gpa43_for_percent(normalized_grade)
                )
                grade_value_gpa4 = (
                    gpa4_for_grade_label(normalized_grade)
                    if grade_kind == "letter"
                    else gpa4_for_percent(normalized_grade)
                )
                if grade_value_gpa43 is None or grade_value_gpa4 is None:
                    continue
                weighted_points_gpa43 = weight * float(grade_value_gpa43)
                weighted_points_gpa4 = weight * float(grade_value_gpa4)
            else:
                try:
                    grade_value = float(normalized_grade)
                except Exception:
                    continue
                weighted_points_percent = weight * grade_value / 100.0

            if task_type == "bonus":
                if uses_letter_grades:
                    bonus_points_gpa4 += weighted_points_gpa4
                    bonus_points_gpa43 += weighted_points_gpa43
                else:
                    bonus_points_percent += weighted_points_percent
            else:
                regular_weight_sum += weight
                if uses_letter_grades:
                    regular_points_gpa4 += weighted_points_gpa4
                    regular_points_gpa43 += weighted_points_gpa43
                else:
                    regular_points_percent += weighted_points_percent

        final_percent = None
        final_gpa4 = None
        final_gpa43 = None
        final_letter = ""
        if regular_weight_sum > 0:
            if uses_letter_grades:
                final_gpa4 = (regular_points_gpa4 + bonus_points_gpa4) / regular_weight_sum
                final_gpa43 = (regular_points_gpa43 + bonus_points_gpa43) / regular_weight_sum
                letter_row = grade_scale_row_for_gpa43(final_gpa43)
                if letter_row:
                    final_letter = str(letter_row.get("label", "") or "").strip()
            else:
                final_percent = ((regular_points_percent + bonus_points_percent) / regular_weight_sum) * 100.0

        state[course_id] = {
            "course_id": course_id,
            "course_name": str(course.get("course_name") or "").strip(),
            "term": normalize_previous_course_term(course.get("term")),
            "academic_year_start": int(course.get("academic_year_start") or current_academic_year_start()),
            "is_complete": tracked_task_count > 0 and graded_tracked_task_count == tracked_task_count,
            "final_percent": final_percent,
            "final_gpa4": final_gpa4,
            "final_gpa43": final_gpa43,
            "final_letter": final_letter,
            "uses_letter_grades": uses_letter_grades,
            "tracked_task_count": tracked_task_count,
        }
    return state


class FileDropLabel(QLabel):
    def __init__(self, path_edit: QLineEdit, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.path_edit = path_edit
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.path_edit.setText(urls[0].toLocalFile())


class TranscriptDropDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Import transcript")
        self.setModal(True)
        self.setMinimumWidth(540)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Choose an academic record or transcript")

        btn_browse = QPushButton("Browse…")
        btn_browse.setObjectName("SecondaryButton")
        btn_browse.clicked.connect(self._browse)

        self.drop = FileDropLabel(self.path_edit, "Drag & drop a transcript or academic record here")
        self.drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop.setMinimumHeight(168)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Next")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self.path_edit, 1)
        top.addWidget(btn_browse)

        privacy_note = QLabel("Privacy: continuing sends this file to OpenAI for extraction. You can review every course before it is saved.")
        privacy_note.setObjectName("FooterNote")
        privacy_note.setWordWrap(True)

        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addLayout(top)
        root.addWidget(self.drop)
        root.addWidget(privacy_note)
        root.addWidget(buttons)
        self.setLayout(root)
        self.apply_settings()

    def apply_settings(self) -> None:
        mode = get_font_size_mode()
        theme = get_theme()
        colors = theme_colors(theme)
        apply_body_font(self, mode)
        drop_bg = "rgba(0,0,0,0.02)" if theme == "light" else "rgba(255,255,255,0.03)"
        self.setStyleSheet(
            common_page_stylesheet(mode, theme=theme, title_px=18)
            +
            f"""
            QLabel {{
                color: {colors['muted_text']};
            }}
            QLabel#DropZone {{
                border: 2px dashed {colors['input_border']};
                border-radius: 10px;
                background: {drop_bg};
                color: {colors['muted_text']};
            }}
            """
        )
        self.drop.setObjectName("DropZone")

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose transcript",
            "",
            "Academic Record (*.pdf *.docx *.txt *.png *.jpg *.jpeg *.webp);;All files (*)",
        )
        if path:
            self.path_edit.setText(path)

    def path(self) -> str:
        return self.path_edit.text().strip()


class TranscriptExtractionWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, coordinator: TranscriptImportCoordinator, path: str):
        super().__init__()
        self._coordinator = coordinator
        self._path = path
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        self._coordinator.cancel()

    def run(self) -> None:
        try:
            result = self._coordinator.extract(self._path)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        if self._cancelled:
            self.failed.emit("Transcript import cancelled.")
        else:
            self.finished.emit(result)


class TranscriptProgressDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Import transcript")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

        label = QLabel("Analyzing your transcript with AI…")
        label.setWordWrap(True)

        sublabel = QLabel("This can take a few seconds. You’ll be able to review and edit courses before adding them.")
        sublabel.setObjectName("Subtitle")
        sublabel.setWordWrap(True)

        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addWidget(label)
        root.addWidget(sublabel)
        root.addWidget(bar)
        root.addWidget(buttons)
        self.setLayout(root)
        self.apply_settings()

    def apply_settings(self) -> None:
        mode = get_font_size_mode()
        theme = get_theme()
        apply_body_font(self, mode)
        self.setStyleSheet(common_page_stylesheet(mode, theme=theme, title_px=18))


class TranscriptReviewDialog(QDialog):
    def __init__(self, result: TranscriptExtractionResult, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Review transcript courses")
        self.setModal(True)
        self.setMinimumWidth(960)
        self.setMinimumHeight(540)
        self._result = result

        title = QLabel("Review Transcript Courses")
        title.setObjectName("Title")
        subtitle = QLabel("Edit extracted rows before adding them to your previous courses list.")
        subtitle.setObjectName("Subtitle")

        self.banner = QLabel()
        self.banner.setObjectName("Subtitle")
        self.banner.setWordWrap(True)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Course", "Final Grade", "Credit Hours", "Term", "Academic Year", "✕"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setAlternatingRowColors(False)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 130)
        self.table.setColumnWidth(5, 44)

        for row in result.rows:
            self._append_row(row)
        self._set_banner_text()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add Courses")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(self.banner)
        root.addWidget(self.table, 1)
        root.addWidget(buttons)
        self.setLayout(root)
        self.apply_settings()

    def apply_settings(self) -> None:
        mode = get_font_size_mode()
        theme = get_theme()
        colors = theme_colors(theme)
        apply_body_font(self, mode)
        self.setStyleSheet(
            common_page_stylesheet(mode, theme=theme, title_px=18)
            +
            f"""
            QTableWidget {{
                background: {colors['card_bg']};
                border: 1px solid {colors['border']};
                border-radius: 10px;
                gridline-color: transparent;
                selection-background-color: {colors['accent_tint']};
                selection-color: {colors['text']};
            }}
            QHeaderView::section {{
                background: {colors['window_alt_bg']};
                padding: 9px 12px;
                border: none;
                border-bottom: 1px solid {colors['border']};
                font-weight: 700;
                font-size: 11px;
                color: {colors['header_text']};
            }}
            """
        )

    def _set_banner_text(self) -> None:
        parts: list[str] = [f"AI extracted {self.table.rowCount()} previous course(s)."]
        if self._result.institution:
            parts.append(f"Institution: {self._result.institution}.")
        if self._result.student_name:
            parts.append(f"Student: {self._result.student_name}.")
        parts.extend(warning for warning in self._result.warnings if warning)
        self.banner.setText(" ".join(parts))

    def _create_grade_edit(self, value: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText("85 or A-")
        edit.setText(str(value or "").strip())
        return edit

    def _create_credit_hours_spinbox(self, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setRange(0.5, 99.0)
        spin.setSingleStep(0.5)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setValue(float(value))
        return spin

    def _create_term_combo(self, value: str) -> QComboBox:
        combo = QComboBox()
        for term_value, label in PREVIOUS_COURSE_TERMS:
            combo.addItem(label, term_value)
        idx = combo.findData(normalize_previous_course_term(value))
        combo.setCurrentIndex(idx if idx != -1 else 0)
        return combo

    def _create_year_combo(self, value: int) -> QComboBox:
        combo = QComboBox()
        for label, start_year in previous_course_academic_year_options():
            combo.addItem(label, start_year)
        if combo.findData(int(value)) == -1:
            combo.addItem(previous_course_academic_year_label(int(value)), int(value))
        combo.setCurrentIndex(max(0, combo.findData(int(value))))
        return combo

    def _append_row(self, row_data: TranscriptCourseRow) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        course_item = QTableWidgetItem(row_data.course_name)
        tooltip_parts: list[str] = []
        if row_data.confidence < 0.999:
            tooltip_parts.append(f"Confidence: {int(round(row_data.confidence * 100))}%")
        if row_data.source_excerpt:
            tooltip_parts.append(f"Source: {row_data.source_excerpt}")
        if tooltip_parts:
            course_item.setToolTip("\n".join(tooltip_parts))
        self.table.setItem(row, 0, course_item)

        self.table.setCellWidget(row, 1, self._create_grade_edit(row_data.final_grade))
        self.table.setCellWidget(row, 2, self._create_credit_hours_spinbox(row_data.credit_hours))
        self.table.setCellWidget(row, 3, self._create_term_combo(row_data.term))
        self.table.setCellWidget(row, 4, self._create_year_combo(row_data.academic_year_start))

        remove_button = QPushButton("✕")
        remove_button.setObjectName("SecondaryButton")
        remove_button.setFixedWidth(34)
        remove_button.clicked.connect(lambda _checked=False, button=remove_button: self._remove_button_row(button))
        self.table.setCellWidget(row, 5, remove_button)

    def _remove_button_row(self, button: QPushButton) -> None:
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, 5) is button:
                self.table.removeRow(row)
                self._set_banner_text()
                break

    def get_rows(self) -> list[dict]:
        rows: list[dict] = []
        for row in range(self.table.rowCount()):
            course_item = self.table.item(row, 0)
            course_name = str(course_item.text() if course_item is not None else "").strip()
            if not course_name:
                continue

            grade_widget = self.table.cellWidget(row, 1)
            credit_widget = self.table.cellWidget(row, 2)
            term_widget = self.table.cellWidget(row, 3)
            year_widget = self.table.cellWidget(row, 4)

            grade_text = grade_widget.text().strip() if isinstance(grade_widget, QLineEdit) else ""
            grade_data = parse_previous_course_grade_input(grade_text)
            if grade_data is None:
                continue
            final_percent, final_letter = grade_data
            credit_hours = credit_widget.value() if isinstance(credit_widget, QDoubleSpinBox) else 0.0
            term = term_widget.currentData() if isinstance(term_widget, QComboBox) else "fall"
            academic_year_start = year_widget.currentData() if isinstance(year_widget, QComboBox) else 0

            rows.append(
                {
                    "course_name": course_name,
                    "final_percent": float(final_percent),
                    "final_letter": final_letter,
                    "credit_hours": float(credit_hours),
                    "term": normalize_previous_course_term(term),
                    "academic_year_start": int(academic_year_start),
                }
            )
        return rows

    def accept(self) -> None:
        for row in range(self.table.rowCount()):
            course_item = self.table.item(row, 0)
            course_name = str(course_item.text() if course_item is not None else "").strip()
            if not course_name:
                QMessageBox.critical(self, "Missing data", "Each transcript course needs a course name.")
                self.table.setCurrentCell(row, 0)
                return
            grade_widget = self.table.cellWidget(row, 1)
            grade_text = grade_widget.text().strip() if isinstance(grade_widget, QLineEdit) else ""
            if parse_previous_course_grade_input(grade_text) is None:
                QMessageBox.critical(
                    self,
                    "Missing data",
                    "Each transcript course needs a valid final grade. Use a percent or a grade label from your scale.",
                )
                self.table.setCurrentCell(row, 1)
                if isinstance(grade_widget, QLineEdit):
                    grade_widget.setFocus()
                return
        super().accept()


class PreviousCoursesDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._transcript_thread: QThread | None = None
        self._transcript_worker: TranscriptExtractionWorker | None = None
        self._transcript_progress: TranscriptProgressDialog | None = None
        self._transcript_import_cancelled = False
        self.setWindowTitle("Previous Courses")
        self.setModal(True)
        self.setMinimumWidth(900)
        self.setMinimumHeight(520)

        title = QLabel("Previous Courses")
        title.setObjectName("Title")
        subtitle = QLabel("Add completed courses manually or import them from a transcript before saving.")
        subtitle.setObjectName("Subtitle")

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Course", "Final Grade", "Credit Hours", "Term", "Academic Year"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.setAlternatingRowColors(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setSortingEnabled(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 130)

        self.btn_add = QPushButton("Add")
        self.btn_add.setObjectName("SecondaryButton")
        self.btn_import_transcript = QPushButton("Import Transcript")
        self.btn_import_transcript.setObjectName("SecondaryButton")
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setObjectName("SecondaryButton")
        self.btn_add.clicked.connect(self._add_blank_row)
        self.btn_import_transcript.clicked.connect(self.start_transcript_import)
        self.btn_delete.clicked.connect(self._delete_selected_rows)

        top_actions = QHBoxLayout()
        top_actions.setSpacing(8)
        top_actions.addWidget(self.btn_add)
        top_actions.addWidget(self.btn_import_transcript)
        top_actions.addWidget(self.btn_delete)
        top_actions.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addLayout(top_actions)
        root.addWidget(self.table, 1)
        root.addWidget(buttons)
        self.setLayout(root)

        self.apply_settings()
        self.load_rows()

    def apply_settings(self) -> None:
        mode = get_font_size_mode()
        theme = get_theme()
        colors = theme_colors(theme)
        apply_body_font(self, mode)
        self.setStyleSheet(
            common_page_stylesheet(mode, theme=theme, title_px=18)
            +
            f"""
            QTableWidget {{
                background: {colors['card_bg']};
                border: 1px solid {colors['border']};
                border-radius: 10px;
                gridline-color: transparent;
                selection-background-color: {colors['accent_tint']};
                selection-color: {colors['text']};
            }}
            QHeaderView::section {{
                background: {colors['window_alt_bg']};
                padding: 9px 12px;
                border: none;
                border-bottom: 1px solid {colors['border']};
                font-weight: 700;
                font-size: 11px;
                color: {colors['header_text']};
            }}
            """
        )

    def load_rows(self) -> None:
        self.table.setRowCount(0)
        for row in list_previous_courses():
            self._add_row(row, insert_at_top=False)

    def _create_grade_edit(self, grade_text: str = "") -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText("85 or A-")
        edit.setText(str(grade_text or "").strip())
        return edit

    def _create_credit_hours_spinbox(self, value: float | None = None) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setRange(0.0, 99.0)
        spin.setSingleStep(0.5)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setSpecialValueText("")
        spin.setValue(0.0 if value is None else float(value))
        return spin

    def _create_term_combo(self, value: str | None = None) -> QComboBox:
        combo = QComboBox()
        for term_value, label in PREVIOUS_COURSE_TERMS:
            combo.addItem(label, term_value)
        term = normalize_previous_course_term(value or current_previous_course_term())
        idx = combo.findData(term)
        combo.setCurrentIndex(idx if idx != -1 else 0)
        return combo

    def _create_year_combo(self, value: int | None = None) -> QComboBox:
        combo = QComboBox()
        options = previous_course_academic_year_options()
        target_year = current_academic_year_start() if value is None else int(value)
        found = False
        for label, start_year in options:
            combo.addItem(label, start_year)
            if start_year == target_year:
                found = True
        if not found:
            combo.addItem(previous_course_academic_year_label(target_year), target_year)
        idx = combo.findData(target_year)
        combo.setCurrentIndex(idx if idx != -1 else 0)
        return combo

    def _add_row(self, row_data: dict | None = None, *, insert_at_top: bool = True) -> None:
        target_row = 0 if insert_at_top else self.table.rowCount()
        self.table.insertRow(target_row)

        course_item = QTableWidgetItem(str((row_data or {}).get("course_name", "")).strip())
        course_item.setData(Qt.ItemDataRole.UserRole, (row_data or {}).get("id"))
        self.table.setItem(target_row, 0, course_item)

        grade_widget = self._create_grade_edit(format_previous_course_grade(row_data or {}))
        credit_widget = self._create_credit_hours_spinbox((row_data or {}).get("credit_hours"))
        term_widget = self._create_term_combo((row_data or {}).get("term"))
        year_widget = self._create_year_combo((row_data or {}).get("academic_year_start"))

        self.table.setCellWidget(target_row, 1, grade_widget)
        self.table.setCellWidget(target_row, 2, credit_widget)
        self.table.setCellWidget(target_row, 3, term_widget)
        self.table.setCellWidget(target_row, 4, year_widget)

    def _add_blank_row(self) -> None:
        self._add_row(
            {
                "course_name": "",
                "final_percent": -1.0,
                "final_letter": "",
                "credit_hours": None,
                "term": current_previous_course_term(),
                "academic_year_start": current_academic_year_start(),
            }
        )
        row = 0
        item = self.table.item(row, 0)
        if item is not None:
            self.table.setCurrentCell(row, 0)
            QTimer.singleShot(0, lambda item=item: self.table.editItem(item))

    def _selected_rows(self) -> list[int]:
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows and self.table.currentRow() >= 0:
            rows.add(self.table.currentRow())
        return sorted(rows)

    def _delete_selected_rows(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        for row in reversed(rows):
            self.table.removeRow(row)

    def _set_row_data(self, target_row: int, row_data: dict) -> None:
        course_item = self.table.item(target_row, 0)
        if course_item is None:
            course_item = QTableWidgetItem()
            self.table.setItem(target_row, 0, course_item)
        course_item.setText(str(row_data.get("course_name", "") or "").strip())
        course_item.setData(Qt.ItemDataRole.UserRole, row_data.get("id"))

        grade_widget = self.table.cellWidget(target_row, 1)
        if isinstance(grade_widget, QLineEdit):
            grade_widget.setText(format_previous_course_grade(row_data))

        credit_widget = self.table.cellWidget(target_row, 2)
        if isinstance(credit_widget, QDoubleSpinBox):
            credit_widget.setValue(float(row_data.get("credit_hours", 0.0) or 0.0))

        term_widget = self.table.cellWidget(target_row, 3)
        if isinstance(term_widget, QComboBox):
            idx = term_widget.findData(normalize_previous_course_term(row_data.get("term")))
            term_widget.setCurrentIndex(idx if idx != -1 else 0)

        year_widget = self.table.cellWidget(target_row, 4)
        if isinstance(year_widget, QComboBox):
            target_year = int(row_data.get("academic_year_start") or current_academic_year_start())
            if year_widget.findData(target_year) == -1:
                year_widget.addItem(previous_course_academic_year_label(target_year), target_year)
            year_widget.setCurrentIndex(max(0, year_widget.findData(target_year)))

    def _find_matching_row(self, course_name: str, term: str, academic_year_start: int) -> int:
        target_name = course_name.strip().lower()
        target_term = normalize_previous_course_term(term)
        target_year = int(academic_year_start)
        for row in range(self.table.rowCount()):
            course_item = self.table.item(row, 0)
            existing_name = str(course_item.text() if course_item is not None else "").strip().lower()
            term_widget = self.table.cellWidget(row, 3)
            year_widget = self.table.cellWidget(row, 4)
            existing_term = (
                normalize_previous_course_term(term_widget.currentData())
                if isinstance(term_widget, QComboBox)
                else "fall"
            )
            existing_year = int(year_widget.currentData()) if isinstance(year_widget, QComboBox) else 0
            if existing_name == target_name and existing_term == target_term and existing_year == target_year:
                return row
        return -1

    def _merge_transcript_rows(self, rows: list[dict]) -> tuple[int, int]:
        added = 0
        updated = 0
        for row_data in rows:
            course_name = str(row_data.get("course_name", "") or "").strip()
            if not course_name:
                continue
            match_row = self._find_matching_row(
                course_name,
                str(row_data.get("term", "") or ""),
                int(row_data.get("academic_year_start") or 0),
            )
            if match_row >= 0:
                self._set_row_data(match_row, row_data)
                updated += 1
            else:
                self._add_row(row_data, insert_at_top=False)
                added += 1
        return added, updated

    def start_transcript_import(self) -> None:
        if self._transcript_thread is not None:
            QMessageBox.information(self, "Import in progress", "A transcript import is already running.")
            return

        if not transcript_openai_available():
            QMessageBox.critical(self, "Import failed", "The OpenAI Python package is not installed in this build.")
            return

        pick = TranscriptDropDialog(self)
        if pick.exec() != QDialog.DialogCode.Accepted:
            return

        path = pick.path()
        if not path:
            return

        coordinator = TranscriptImportCoordinator(
            api_key=get_effective_openai_api_key(),
            model=get_syllabus_ai_model(),
        )

        self._transcript_import_cancelled = False
        self._transcript_thread = QThread(self)
        self._transcript_worker = TranscriptExtractionWorker(coordinator, path)
        self._transcript_worker.moveToThread(self._transcript_thread)

        self._transcript_progress = TranscriptProgressDialog(self)
        self._transcript_progress.rejected.connect(self.cancel_transcript_import)

        self._transcript_thread.started.connect(self._transcript_worker.run)
        self._transcript_worker.finished.connect(self._on_transcript_extraction_finished)
        self._transcript_worker.failed.connect(self._on_transcript_extraction_failed)
        self._transcript_worker.finished.connect(self._transcript_thread.quit)
        self._transcript_worker.failed.connect(self._transcript_thread.quit)
        self._transcript_thread.finished.connect(self._on_transcript_thread_finished)
        self._transcript_thread.finished.connect(self._transcript_thread.deleteLater)
        self._transcript_thread.start()
        self._transcript_progress.open()

    def cancel_transcript_import(self) -> None:
        self._transcript_import_cancelled = True
        if self._transcript_worker is not None:
            self._transcript_worker.cancel()
        if self._transcript_progress is not None:
            self._transcript_progress.hide()

    def _close_transcript_progress(self) -> None:
        if self._transcript_progress is None:
            return
        self._transcript_progress.blockSignals(True)
        self._transcript_progress.close()
        self._transcript_progress.deleteLater()
        self._transcript_progress = None

    def _on_transcript_extraction_finished(self, result: object) -> None:
        self._close_transcript_progress()
        if self._transcript_import_cancelled:
            return
        if not isinstance(result, TranscriptExtractionResult):
            QMessageBox.critical(self, "Import failed", "Transcript extraction did not return a usable result.")
            return

        dialog = TranscriptReviewDialog(result, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        imported_rows = dialog.get_rows()
        if not imported_rows:
            QMessageBox.critical(self, "Import failed", "No previous courses remain to add.")
            return

        added, updated = self._merge_transcript_rows(imported_rows)
        QMessageBox.information(
            self,
            "Transcript import",
            f"Added {added} course(s) and updated {updated} existing row(s). Save when you're ready.",
        )

    def _on_transcript_extraction_failed(self, message: str) -> None:
        self._close_transcript_progress()
        if self._transcript_import_cancelled:
            return
        QMessageBox.critical(self, "Import failed", message)

    def _on_transcript_thread_finished(self) -> None:
        if self._transcript_worker is not None:
            self._transcript_worker.deleteLater()
        self._transcript_worker = None
        self._transcript_thread = None
        self._transcript_import_cancelled = False

    def _collect_rows_for_save(self) -> list[dict] | None:
        rows: list[dict] = []
        for row in range(self.table.rowCount()):
            course_item = self.table.item(row, 0)
            course_name = "" if course_item is None else str(course_item.text() or "").strip()
            row_id = None if course_item is None else course_item.data(Qt.ItemDataRole.UserRole)

            grade_widget = self.table.cellWidget(row, 1)
            credit_widget = self.table.cellWidget(row, 2)
            term_widget = self.table.cellWidget(row, 3)
            year_widget = self.table.cellWidget(row, 4)

            grade_text = grade_widget.text().strip() if isinstance(grade_widget, QLineEdit) else ""
            grade_data = parse_previous_course_grade_input(grade_text)
            credit_hours = credit_widget.value() if isinstance(credit_widget, QDoubleSpinBox) else 0.0
            term = term_widget.currentData() if isinstance(term_widget, QComboBox) else current_previous_course_term()
            academic_year_start = (
                year_widget.currentData() if isinstance(year_widget, QComboBox) else current_academic_year_start()
            )

            if not course_name and not grade_text and credit_hours <= 0:
                continue

            if not course_name:
                QMessageBox.critical(self, "Missing data", "Each previous course needs a course name.")
                self.table.setCurrentCell(row, 0)
                if course_item is not None:
                    self.table.editItem(course_item)
                return None

            if grade_data is None:
                QMessageBox.critical(
                    self,
                    "Missing data",
                    "Each previous course needs a valid final grade. Use a percent or a grade label from your scale.",
                )
                self.table.setCurrentCell(row, 1)
                if isinstance(grade_widget, QLineEdit):
                    grade_widget.setFocus()
                return None
            final_percent, final_letter = grade_data

            if credit_hours <= 0:
                QMessageBox.critical(self, "Missing data", "Each previous course needs positive credit hours.")
                self.table.setCurrentCell(row, 2)
                if isinstance(credit_widget, QDoubleSpinBox):
                    credit_widget.setFocus()
                return None

            rows.append(
                {
                    "id": row_id,
                    "course_name": course_name,
                    "final_percent": float(final_percent),
                    "final_letter": final_letter,
                    "credit_hours": float(credit_hours),
                    "term": normalize_previous_course_term(term),
                    "academic_year_start": int(academic_year_start),
                }
            )

        return rows

    def _save_and_accept(self) -> None:
        rows = self._collect_rows_for_save()
        if rows is None:
            return
        try:
            save_previous_courses(rows)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.accept()


class CourseEditDialog(QDialog):
    def __init__(self, course: dict | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._course = course or {}
        is_editing = bool(self._course.get("id"))
        self.setWindowTitle("Edit Course" if is_editing else "Add Course")
        self.setModal(True)
        self.setMinimumWidth(420)

        title = QLabel("Edit Course" if is_editing else "Add Course")
        title.setObjectName("Title")
        subtitle = QLabel("Update the course name and the semester it belongs to.")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)

        self.name_input = QLineEdit(str(self._course.get("name", "") or "").strip())
        self.name_input.setPlaceholderText("Course name")

        self.term_input = QComboBox()
        for term_value, label in PREVIOUS_COURSE_TERMS:
            self.term_input.addItem(label, term_value)
        target_term = normalize_previous_course_term(self._course.get("term") or current_previous_course_term())
        self.term_input.setCurrentIndex(max(0, self.term_input.findData(target_term)))

        self.year_input = QComboBox()
        target_year = int(self._course.get("academic_year_start") or current_academic_year_start())
        for label, start_year in previous_course_academic_year_options():
            self.year_input.addItem(label, start_year)
        if self.year_input.findData(target_year) == -1:
            self.year_input.addItem(previous_course_academic_year_label(target_year), target_year)
        self.year_input.setCurrentIndex(max(0, self.year_input.findData(target_year)))

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addRow("Course:", self.name_input)
        form.addRow("Semester:", self.term_input)
        form.addRow("Academic year:", self.year_input)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Done")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addLayout(form)
        root.addWidget(buttons)
        self.setLayout(root)
        self.apply_settings()

    def apply_settings(self) -> None:
        mode = get_font_size_mode()
        theme = get_theme()
        apply_body_font(self, mode)
        self.setStyleSheet(common_page_stylesheet(mode, theme=theme, title_px=18))

    def course_data(self) -> dict:
        return {
            "name": str(self.name_input.text() or "").strip(),
            "term": normalize_previous_course_term(self.term_input.currentData()),
            "academic_year_start": int(self.year_input.currentData() or current_academic_year_start()),
        }

    def accept(self) -> None:
        if not str(self.name_input.text() or "").strip():
            QMessageBox.critical(self, "Missing data", "Please enter a course name.")
            self.name_input.setFocus()
            return
        super().accept()


class CompleteCourseDialog(QDialog):
    def __init__(self, summary: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Complete Course")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._summary = summary

        title = QLabel("Complete Course")
        title.setObjectName("Title")
        subtitle = QLabel(
            "This will move the course into Previous Courses and remove its tasks from your current course list."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)

        self.credit_hours = QDoubleSpinBox()
        self.credit_hours.setDecimals(2)
        self.credit_hours.setRange(0.0, 99.0)
        self.credit_hours.setSingleStep(0.5)
        self.credit_hours.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.credit_hours.setSpecialValueText("")
        self.credit_hours.setValue(0.0)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addRow("Course:", QLabel(str(summary.get("course_name", "") or "").strip()))
        form.addRow("Final grade:", QLabel(format_current_course_grade(summary)))
        form.addRow("Semester:", QLabel(previous_course_term_label(summary.get("term"))))
        form.addRow(
            "Academic year:",
            QLabel(previous_course_academic_year_label(int(summary.get("academic_year_start") or current_academic_year_start()))),
        )
        form.addRow("Credit hours:", self.credit_hours)

        self.complete_button = QPushButton("Complete Course")
        self.complete_button.setObjectName("CourseSuccessButton")
        self.complete_button.clicked.connect(self.accept)

        cancel_button = QPushButton("Cancel")
        cancel_button.setObjectName("SecondaryButton")
        cancel_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)
        buttons.addWidget(self.complete_button)
        buttons.addWidget(cancel_button)

        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addLayout(form)
        root.addLayout(buttons)
        self.setLayout(root)
        self.apply_settings()

    def apply_settings(self) -> None:
        mode = get_font_size_mode()
        theme = get_theme()
        apply_body_font(self, mode)
        self.setStyleSheet(
            common_page_stylesheet(mode, theme=theme, title_px=18)
            + course_success_button_styles(mode, theme)
        )
        self.complete_button.ensurePolished()
        self.complete_button.setMinimumSize(self.complete_button.sizeHint())

    def accept(self) -> None:
        if self.credit_hours.value() <= 0:
            QMessageBox.critical(self, "Missing data", "Please enter the course credit hours before completing it.")
            self.credit_hours.setFocus()
            return
        super().accept()


class CoursesRowOverlay(QLabel):
    def __init__(self, parent: QWidget, pixmap, rect: QRect):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setScaledContents(True)
        self.setPixmap(pixmap)
        self.setGeometry(rect)
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)
        self.show()

    @property
    def opacity_effect(self) -> QGraphicsOpacityEffect:
        return self._opacity_effect


class CoursesPage(QWidget):
    changed = Signal()

    def __init__(
        self,
        model: QSqlTableModel,
        table: QTableView,
        empty_label: QLabel,
        gpa_content_layout: QVBoxLayout,
        toolbar_bar_widget: QWidget,
        toolbar_buttons: list[QPushButton],
        edit_button: QPushButton,
        complete_button: QPushButton,
        undo_button: QPushButton,
        delete_button: QPushButton,
    ):
        super().__init__()
        self._model = model
        self._model.setParent(self)
        self._table = table
        self._empty_label = empty_label
        self._gpa_content_layout = gpa_content_layout
        self._toolbar_bar_widget = toolbar_bar_widget
        self._toolbar_buttons = toolbar_buttons
        self._edit_button = edit_button
        self._complete_button = complete_button
        self._undo_button = undo_button
        self._delete_button = delete_button
        self._id_col = self._model.fieldIndex("id")
        self._name_col = self._model.fieldIndex("name")
        self._term_col = self._model.fieldIndex("term")
        self._year_col = self._model.fieldIndex("academic_year_start")
        self._completion_state: dict[int, dict] = {}
        self._pending_completion_undo: dict | None = None
        self._row_animation_group: QParallelAnimationGroup | None = None
        self._row_animation_overlays: list[CoursesRowOverlay] = []
        self._row_animating = False
        self._suspend_autosave = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(220)
        self._autosave_timer.timeout.connect(self._autosave)
        self._model.dataChanged.connect(lambda *_args: self._schedule_autosave())
        self._model.rowsInserted.connect(lambda *_args: self._schedule_autosave())
        self._model.rowsRemoved.connect(lambda *_args: self._schedule_autosave())
        self._table.viewport().installEventFilter(self)
        selection_model = self._table.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(lambda *_args: self._update_selection_actions())
            selection_model.currentRowChanged.connect(lambda *_args: self._update_selection_actions())
        self._table.doubleClicked.connect(lambda _index: self.open_selected_course_dialog())
        self._edit_button.clicked.connect(self.open_selected_course_dialog)
        self._complete_button.clicked.connect(self.start_selected_course_completion)
        self._undo_button.clicked.connect(self.undo_completed_course)
        self._edit_button.setVisible(False)
        self._complete_button.setVisible(False)
        self._undo_button.setVisible(False)
        self._delete_button.setVisible(False)
        self._delete_button.setEnabled(False)

    def _app_is_closing(self) -> bool:
        win = self.window()
        return bool(win is not None and win.property("_app_closing"))

    def hideEvent(self, event):
        try:
            app_closing = self._app_is_closing()
            if self._model.isDirty():
                if not self._submit_changes():
                    if not app_closing:
                        QMessageBox.critical(
                            self._table,
                            "Save failed",
                            "Some course changes still need attention before they can be saved.",
                        )
        except Exception:
            pass
        self._clear_completion_undo()
        self._clear_course_row_animation()
        super().hideEvent(event)

    def _schedule_autosave(self) -> None:
        if self._suspend_autosave:
            return
        self._autosave_timer.start()

    def _validate_before_submit(self) -> bool:
        seen: set[str] = set()
        for row in range(self._model.rowCount()):
            if self._name_col == -1:
                continue
            raw = self._model.data(self._model.index(row, self._name_col), Qt.EditRole)
            name = str(raw or "").strip()
            if not name:
                return False
            key = name.lower()
            if key in seen:
                return False
            seen.add(key)
        return True

    def _submit_changes(self) -> bool:
        if not self._validate_before_submit():
            return False
        if self._model.submitAll():
            self.refresh()
            self.changed.emit()
            return True
        return False

    def _autosave(self) -> None:
        if self._model.isDirty():
            self._submit_changes()

    def _course_id_for_row(self, row: int) -> int | None:
        if self._id_col == -1 or row < 0 or row >= self._model.rowCount():
            return None
        raw = self._model.data(self._model.index(row, self._id_col), Qt.EditRole)
        try:
            return int(raw)
        except Exception:
            return None

    def _clear_completion_undo(self) -> None:
        self._pending_completion_undo = None
        self._undo_button.setVisible(False)
        self._update_selection_actions()

    def _clear_course_row_animation(self) -> None:
        if self._row_animation_group is not None:
            try:
                self._row_animation_group.stop()
            except Exception:
                pass
            self._row_animation_group.deleteLater()
            self._row_animation_group = None

        for overlay in self._row_animation_overlays:
            overlay.deleteLater()
        self._row_animation_overlays = []
        self._row_animating = False

    def _course_row_rect(self, row: int) -> QRect:
        anchor_col = self._name_col if self._name_col != -1 else 0
        anchor_index = self._model.index(row, anchor_col)
        anchor_rect = self._table.visualRect(anchor_index)
        if not anchor_rect.isValid():
            return QRect()

        margin = 10
        rect = QRect(
            margin,
            anchor_rect.top() + 4,
            max(0, self._table.viewport().width() - (2 * margin)),
            max(0, anchor_rect.height() - 8),
        )
        return rect.intersected(self._table.viewport().rect())

    def _capture_course_row_state(self) -> dict[int, dict]:
        viewport = self._table.viewport()
        state: dict[int, dict] = {}
        for row in range(self._model.rowCount()):
            if self._table.isRowHidden(row):
                continue

            rect = self._course_row_rect(row)
            if not rect.isValid() or rect.height() <= 0 or rect.width() <= 0:
                continue
            if rect.bottom() < 0 or rect.top() > viewport.height():
                continue

            course_id = self._course_id_for_row(row)
            if course_id is None:
                continue

            pixmap = viewport.grab(rect)
            if pixmap.isNull():
                continue

            state[course_id] = {
                "rect": QRect(rect),
                "pixmap": pixmap,
            }
        return state

    def _current_course_row_rects(self) -> dict[int, QRect]:
        rects: dict[int, QRect] = {}
        for row in range(self._model.rowCount()):
            if self._table.isRowHidden(row):
                continue

            rect = self._course_row_rect(row)
            if not rect.isValid() or rect.height() <= 0 or rect.width() <= 0:
                continue

            course_id = self._course_id_for_row(row)
            if course_id is None:
                continue
            rects[course_id] = QRect(rect)
        return rects

    def _play_course_transition(self, before_state: dict[int, dict], changed_course_id: int) -> None:
        if not before_state:
            return

        self._clear_course_row_animation()
        after_rects = self._current_course_row_rects()
        group = QParallelAnimationGroup(self)
        overlays: list[CoursesRowOverlay] = []
        moved_any = False

        for course_id, snapshot in before_state.items():
            start_rect = snapshot.get("rect")
            pixmap = snapshot.get("pixmap")
            if not isinstance(start_rect, QRect) or pixmap is None or pixmap.isNull():
                continue

            end_rect = after_rects.get(course_id)
            overlay = CoursesRowOverlay(self._table.viewport(), pixmap, start_rect)
            overlay.raise_()

            if course_id == changed_course_id and end_rect is None:
                moved_any = True
                overlays.append(overlay)

                geom_anim = QPropertyAnimation(overlay, b"geometry", self)
                geom_anim.setDuration(220)
                geom_anim.setStartValue(QRect(start_rect))
                geom_anim.setEndValue(QRect(start_rect.translated(0, -18)))
                geom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

                opacity_anim = QPropertyAnimation(overlay.opacity_effect, b"opacity", self)
                opacity_anim.setDuration(220)
                opacity_anim.setStartValue(1.0)
                opacity_anim.setEndValue(0.0)
                opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

                group.addAnimation(geom_anim)
                group.addAnimation(opacity_anim)
                continue

            if end_rect is None or start_rect == end_rect:
                overlay.deleteLater()
                continue

            moved_any = True
            overlays.append(overlay)

            geom_anim = QPropertyAnimation(overlay, b"geometry", self)
            geom_anim.setDuration(260 if course_id == changed_course_id else 220)
            geom_anim.setStartValue(QRect(start_rect))
            geom_anim.setEndValue(QRect(end_rect))
            geom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(geom_anim)

        if not moved_any:
            for overlay in overlays:
                overlay.deleteLater()
            return

        self._row_animation_group = group
        self._row_animation_overlays = overlays
        self._row_animating = True

        def _finish() -> None:
            if self._row_animation_group is group:
                self._row_animation_group = None
            for overlay in overlays:
                overlay.deleteLater()
            self._row_animation_overlays = []
            self._row_animating = False

        group.finished.connect(_finish)
        group.start()

    def _set_completion_undo(self, payload: dict | None) -> None:
        self._pending_completion_undo = payload
        self._undo_button.setVisible(payload is not None)
        self._update_selection_actions()

    def _refresh_completion_state(self) -> None:
        try:
            self._completion_state = list_current_course_completion_state()
        except Exception:
            self._completion_state = {}

    def _selected_row(self) -> int:
        index = self._table.currentIndex()
        return index.row() if index.isValid() else -1

    def _selected_course_id(self) -> int | None:
        return self._course_id_for_row(self._selected_row())

    def _selected_course_record(self) -> dict | None:
        row = self._selected_row()
        if row < 0:
            return None
        course_id = self._course_id_for_row(row)
        if course_id is None:
            return None
        return {
            "id": course_id,
            "name": str(self._model.data(self._model.index(row, self._name_col), Qt.EditRole) or "").strip(),
            "term": normalize_previous_course_term(
                self._model.data(self._model.index(row, self._term_col), Qt.EditRole) if self._term_col != -1 else ""
            ),
            "academic_year_start": int(
                self._model.data(self._model.index(row, self._year_col), Qt.EditRole) or current_academic_year_start()
            ) if self._year_col != -1 else current_academic_year_start(),
        }

    def _completion_summary_for_course(self, course_id: int | None) -> dict | None:
        if course_id is None:
            return None
        return self._completion_state.get(course_id)

    def _course_can_complete(self, course_id: int | None) -> bool:
        return current_course_can_complete(self._completion_summary_for_course(course_id))

    def _row_can_complete(self, row: int) -> bool:
        return self._course_can_complete(self._course_id_for_row(row))

    def _update_selection_actions(self) -> None:
        course_id = self._selected_course_id()
        has_selection = course_id is not None
        self._edit_button.setVisible(has_selection)
        self._delete_button.setVisible(has_selection)
        self._delete_button.setEnabled(has_selection)

        self._complete_button.setVisible(self._course_can_complete(course_id))

    def _select_course_id(self, course_id: int | None) -> None:
        if course_id is None:
            return
        for row in range(self._model.rowCount()):
            if self._course_id_for_row(row) == course_id:
                index = self._model.index(row, self._name_col if self._name_col != -1 else 0)
                self._table.setCurrentIndex(index)
                self._table.scrollTo(index, QTableView.ScrollHint.PositionAtCenter)
                break

    def _save_course_data(self, course_data: dict, *, course_id: int | None = None) -> int | None:
        query = QSqlQuery()
        if course_id is None:
            query.prepare("INSERT INTO courses(name, term, academic_year_start) VALUES(?, ?, ?)")
            query.addBindValue(course_data["name"])
            query.addBindValue(course_data["term"])
            query.addBindValue(course_data["academic_year_start"])
            if not query.exec():
                QMessageBox.critical(self, "Save failed", query.lastError().text())
                return None
            id_query = QSqlQuery()
            if not id_query.exec("SELECT last_insert_rowid()") or not id_query.next():
                QMessageBox.critical(self, "Save failed", "The new course could not be created.")
                return None
            return int(id_query.value(0))

        query.prepare("UPDATE courses SET name=?, term=?, academic_year_start=? WHERE id=?")
        query.addBindValue(course_data["name"])
        query.addBindValue(course_data["term"])
        query.addBindValue(course_data["academic_year_start"])
        query.addBindValue(course_id)
        if not query.exec():
            QMessageBox.critical(self, "Save failed", query.lastError().text())
            return None
        return int(course_id)

    def add_course(self) -> None:
        dialog = CourseEditDialog(
            {
                "term": current_previous_course_term(),
                "academic_year_start": current_academic_year_start(),
            },
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        course_id = self._save_course_data(dialog.course_data())
        if course_id is None:
            return

        self.refresh()
        self._select_course_id(course_id)
        self.changed.emit()

    def open_selected_course_dialog(self) -> None:
        course = self._selected_course_record()
        if course is None:
            return

        dialog = CourseEditDialog(course, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        course_id = self._save_course_data(dialog.course_data(), course_id=int(course["id"]))
        if course_id is None:
            return

        self.refresh()
        self._select_course_id(course_id)
        self.changed.emit()

    def delete_selected_course(self) -> None:
        course_id = self._selected_course_id()
        if course_id is None:
            return
        course = self._selected_course_record()
        course_name = str((course or {}).get("name", "") or "").strip() or "this course"

        task_count = 0
        count_query = QSqlQuery()
        count_query.prepare("SELECT COUNT(*) FROM tasks WHERE course_id=?")
        count_query.addBindValue(course_id)
        if count_query.exec() and count_query.next():
            try:
                task_count = int(count_query.value(0) or 0)
            except Exception:
                task_count = 0

        if get_confirm_delete():
            detail = (
                f"Delete {course_name}? This will also delete {task_count} task(s) for this course."
                if task_count > 0
                else f"Delete {course_name}?"
            )
            answer = QMessageBox.question(
                self,
                "Delete course",
                detail,
                QMessageBox.StandardButton.Delete | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Delete:
                return

        query = QSqlQuery()
        query.prepare("DELETE FROM courses WHERE id=?")
        query.addBindValue(course_id)
        if not query.exec():
            QMessageBox.critical(self, "Delete failed", query.lastError().text())
            return
        self.refresh()
        self.changed.emit()

    def start_selected_course_completion(self) -> None:
        course_id = self._selected_course_id()
        if course_id is not None:
            self.start_complete_course(course_id)

    def _previous_course_row_for_completion(self, summary: dict, credit_hours: float) -> dict:
        final_letter = str(summary.get("final_letter", "") or "").strip()
        if not final_letter and summary.get("final_gpa43") is not None:
            letter_row = grade_scale_row_for_gpa43(summary.get("final_gpa43"))
            if letter_row:
                final_letter = str(letter_row.get("label", "") or "").strip()
        return {
            "course_name": str(summary.get("course_name", "") or "").strip(),
            "final_percent": -1.0 if final_letter else float(summary.get("final_percent") or 0.0),
            "final_letter": final_letter,
            "credit_hours": float(credit_hours),
            "term": normalize_previous_course_term(summary.get("term")),
            "academic_year_start": int(summary.get("academic_year_start") or current_academic_year_start()),
        }

    def start_complete_course(self, course_id: int) -> None:
        if self._model.isDirty() and not self._submit_changes():
            QMessageBox.critical(
                self._table,
                "Save failed",
                "Save your course changes before completing a course.",
            )
            return

        self._refresh_completion_state()
        summary = self._completion_state.get(int(course_id))
        if not current_course_can_complete(summary):
            return

        dialog = CompleteCourseDialog(summary, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        before_state = self._capture_course_row_state()
        try:
            undo_payload = self._complete_course_transaction(summary, float(dialog.credit_hours.value()))
        except Exception as exc:
            QMessageBox.critical(self, "Complete course failed", str(exc))
            return

        self._set_completion_undo(undo_payload)
        self.refresh()
        QTimer.singleShot(
            0,
            lambda state=before_state, changed_course_id=int(course_id): self._play_course_transition(state, changed_course_id),
        )
        self.changed.emit()

    def _complete_course_transaction(self, summary: dict, credit_hours: float) -> dict:
        course_id = int(summary.get("course_id") or 0)
        if course_id <= 0:
            raise RuntimeError("The selected course could not be found.")

        course_rows = query_rows_as_dicts("SELECT * FROM courses WHERE id=?", [course_id])
        if not course_rows:
            raise RuntimeError("The selected course could not be found.")
        task_rows = query_rows_as_dicts("SELECT * FROM tasks WHERE course_id=? ORDER BY id ASC", [course_id])

        previous_row = self._previous_course_row_for_completion(summary, credit_hours)
        existing_previous_rows = query_rows_as_dicts(
            """
            SELECT *
            FROM previous_courses
            WHERE lower(course_name)=lower(?)
              AND lower(term)=lower(?)
              AND academic_year_start=?
            LIMIT 1
            """,
            [
                previous_row["course_name"],
                previous_row["term"],
                previous_row["academic_year_start"],
            ],
        )

        db = QSqlDatabase.database()
        if not db.isValid():
            raise RuntimeError("Database is not available.")
        if not db.transaction():
            raise RuntimeError(db.lastError().text())

        undo_payload: dict = {
            "course": course_rows[0],
            "tasks": task_rows,
        }

        try:
            if existing_previous_rows:
                previous_before = existing_previous_rows[0]
                previous_after = {
                    **previous_before,
                    **previous_row,
                }
                update_row_dict("previous_courses", previous_after, "id")
                undo_payload["previous_course_action"] = {
                    "mode": "updated",
                    "before": previous_before,
                }
            else:
                query = QSqlQuery()
                query.prepare(
                    """
                    INSERT INTO previous_courses(course_name, final_percent, final_letter, credit_hours, term, academic_year_start)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """
                )
                query.addBindValue(previous_row["course_name"])
                query.addBindValue(previous_row["final_percent"])
                query.addBindValue(previous_row["final_letter"])
                query.addBindValue(previous_row["credit_hours"])
                query.addBindValue(previous_row["term"])
                query.addBindValue(previous_row["academic_year_start"])
                if not query.exec():
                    raise RuntimeError(query.lastError().text())

                id_query = QSqlQuery()
                if not id_query.exec("SELECT last_insert_rowid()") or not id_query.next():
                    raise RuntimeError("The completed course could not be stored.")
                undo_payload["previous_course_action"] = {
                    "mode": "inserted",
                    "id": int(id_query.value(0)),
                }

            delete_tasks = QSqlQuery()
            delete_tasks.prepare("DELETE FROM tasks WHERE course_id=?")
            delete_tasks.addBindValue(course_id)
            if not delete_tasks.exec():
                raise RuntimeError(delete_tasks.lastError().text())

            delete_course = QSqlQuery()
            delete_course.prepare("DELETE FROM courses WHERE id=?")
            delete_course.addBindValue(course_id)
            if not delete_course.exec():
                raise RuntimeError(delete_course.lastError().text())

            if not db.commit():
                raise RuntimeError(db.lastError().text())
        except Exception:
            db.rollback()
            raise

        return undo_payload

    def undo_completed_course(self) -> None:
        payload = self._pending_completion_undo
        if payload is None:
            return

        if self._model.isDirty() and not self._submit_changes():
            QMessageBox.critical(
                self._table,
                "Save failed",
                "Save your course changes before undoing the completed course.",
            )
            return

        db = QSqlDatabase.database()
        if not db.isValid():
            QMessageBox.critical(self, "Undo failed", "Database is not available.")
            return
        if not db.transaction():
            QMessageBox.critical(self, "Undo failed", db.lastError().text())
            return

        try:
            previous_action = payload.get("previous_course_action", {})
            mode = str(previous_action.get("mode", "") or "")
            if mode == "inserted":
                delete_previous = QSqlQuery()
                delete_previous.prepare("DELETE FROM previous_courses WHERE id=?")
                delete_previous.addBindValue(int(previous_action.get("id") or 0))
                if not delete_previous.exec():
                    raise RuntimeError(delete_previous.lastError().text())
            elif mode == "updated":
                before_row = previous_action.get("before")
                if isinstance(before_row, dict):
                    update_row_dict("previous_courses", before_row, "id")

            course_row = payload.get("course")
            if not isinstance(course_row, dict):
                raise RuntimeError("The course snapshot is no longer available.")
            insert_row_dict("courses", course_row)

            task_rows = payload.get("tasks", [])
            if isinstance(task_rows, list):
                for task_row in task_rows:
                    if isinstance(task_row, dict):
                        insert_row_dict("tasks", task_row)

            if not db.commit():
                raise RuntimeError(db.lastError().text())
        except Exception as exc:
            db.rollback()
            QMessageBox.critical(self, "Undo failed", str(exc))
            return

        self._clear_completion_undo()
        self.refresh()
        self.changed.emit()

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            child = layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
                continue
            nested = child.layout()
            if nested is not None:
                self._clear_layout(nested)  # type: ignore[arg-type]

    def _build_gpa_card(self, title: str, gpa_value: float, lines: list[str]) -> QFrame:
        card = QFrame()
        card.setObjectName("CoursesGpaStatCard")

        title_label = QLabel(title)
        title_label.setObjectName("CoursesGpaStatTitle")

        value_label = QLabel(f"{gpa_value:.2f}")
        value_label.setObjectName("CoursesGpaStatValue")

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        for line in lines:
            detail = QLabel(line)
            detail.setObjectName("CoursesGpaMeta")
            detail.setWordWrap(True)
            layout.addWidget(detail)
        card.setLayout(layout)
        return card

    def _update_gpa_panel(self) -> None:
        self._clear_layout(self._gpa_content_layout)

        rows = list_previous_courses()
        if not rows:
            empty = QLabel("Add previous courses or import transcript to calculate GPA.")
            empty.setObjectName("CoursesGpaEmpty")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._gpa_content_layout.addWidget(empty)
            self._gpa_content_layout.addStretch(1)
            return

        last_x_credit_hours = get_gpa_last_x_credit_hours()
        metrics = calculate_previous_course_gpa_metrics(rows, last_x_credit_hours)
        if not metrics:
            empty = QLabel("Add previous courses or import transcript to calculate GPA.")
            empty.setObjectName("CoursesGpaEmpty")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._gpa_content_layout.addWidget(empty)
            self._gpa_content_layout.addStretch(1)
            return

        view_mode = get_gpa_view_mode()
        if view_mode in {"cumulative", "both"} and "cumulative" in metrics:
            cumulative = metrics["cumulative"]
            self._gpa_content_layout.addWidget(
                self._build_gpa_card(
                    "Cumulative",
                    float(cumulative["gpa"]),
                    [
                        f"Using {format_credit_hours(cumulative['credits'])} credit hours",
                        f"{int(cumulative['course_count'])} previous course(s)",
                    ],
                )
            )

        if view_mode in {"last_x", "both"} and "last_x" in metrics:
            last_x = metrics["last_x"]
            lines = [
                f"Using {format_credit_hours(last_x['credits'])} credit hours",
                f"{int(last_x['course_count'])} previous course(s)",
            ]
            if not bool(last_x.get("met_target")):
                lines.append(f"Target: {int(last_x['target_credits'])} credit hours")
            self._gpa_content_layout.addWidget(
                self._build_gpa_card(
                    str(last_x["title"]),
                    float(last_x["gpa"]),
                    lines,
                )
            )

        self._gpa_content_layout.addStretch(1)

    def open_previous_courses_dialog(self) -> None:
        dialog = PreviousCoursesDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def apply_settings(self) -> None:
        mode = get_font_size_mode()
        theme = get_theme()
        colors = theme_colors(theme)
        apply_body_font(self, mode)
        self.setStyleSheet(
            common_page_stylesheet(mode, theme=theme, title_px=18)
            + course_success_button_styles(mode, theme)
            +
            f"""
            QFrame#Card {{
                background: {colors['card_bg']};
                border: 1px solid {colors['border']};
                border-radius: 16px;
            }}
            QHeaderView::section {{
                background: {colors['window_alt_bg']};
                padding: 9px 12px;
                border: none;
                border-bottom: 1px solid {colors['border']};
                font-weight: 700;
                font-size: 11px;
                color: {colors['header_text']};
            }}
            QTableView {{
                background: transparent;
                border: none;
                gridline-color: transparent;
                selection-background-color: transparent;
                selection-color: {colors['text']};
                alternate-background-color: transparent;
                outline: 0px;
            }}
            QTableView::item {{
                padding: 0px;
                background: transparent;
                border: none;
            }}
            QTableView::item:selected {{
                background: transparent;
            }}
            QTableView::item:focus {{
                outline: none;
                background: transparent;
            }}
            QLabel#CoursesSideHeading {{
                color: {colors['text_soft']};
                font-weight: 700;
            }}
            QFrame#CoursesGpaStatCard {{
                background: {colors['window_alt_bg']};
                border: 1px solid {colors['border']};
                border-radius: 12px;
            }}
            QLabel#CoursesGpaStatTitle {{
                color: {colors['text_soft']};
                font-weight: 700;
            }}
            QLabel#CoursesGpaStatValue {{
                color: {colors['text']};
                font-size: 28px;
                font-weight: 800;
            }}
            QLabel#CoursesGpaMeta {{
                color: {colors['muted_text']};
            }}
            QLabel#CoursesGpaEmpty {{
                color: {colors['muted_text']};
                background: {colors['window_alt_bg']};
                border: 1px dashed {colors['border']};
                border-radius: 12px;
                padding: 16px;
            }}
            """
        )
        for button in self._toolbar_buttons:
            button.ensurePolished()
            button.setMinimumSize(button.sizeHint())
        toolbar_height = max((button.sizeHint().height() for button in self._toolbar_buttons), default=0)
        if toolbar_height > 0:
            self._toolbar_bar_widget.setFixedHeight(toolbar_height)
        self._table.verticalHeader().setDefaultSectionSize(
            scaled_row_height(mode, compact=get_compact_rows(), compact_px=34, regular_px=38)
        )
        self._update_gpa_panel()
        self._update_selection_actions()

    def update_empty_state(self) -> None:
        has_courses = self._model.rowCount() > 0
        self._table.setVisible(has_courses)
        self._empty_label.setVisible(not has_courses)

    def refresh(self) -> None:
        self._suspend_autosave = True
        self._clear_course_row_animation()
        ensure_current_courses_have_metadata()
        self._model.select()
        self._refresh_completion_state()
        self.update_empty_state()
        self._update_gpa_panel()
        self._update_selection_actions()
        self._table.viewport().update()
        self._suspend_autosave = False

    def eventFilter(self, obj, event):
        if obj is self._table.viewport() and event.type() == QEvent.Type.MouseButtonPress:
            try:
                pos = event.position().toPoint()
            except Exception:
                pos = event.pos()
            idx = self._table.indexAt(pos)
            if not idx.isValid():
                sel = self._table.selectionModel()
                if sel is not None:
                    sel.clearSelection()
                self._table.setCurrentIndex(QModelIndex())
                self._table.viewport().update()
                self._update_selection_actions()
        return super().eventFilter(obj, event)


def course_content_rect(option) -> QRect:
    return option.rect.adjusted(16, 2, -12, -2)


def style_course_inline_editor(editor: QLineEdit) -> None:
    colors = theme_colors(get_theme())
    editor.setFrame(False)
    editor.setAutoFillBackground(False)
    editor.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
    editor.setStyleSheet(
        "QLineEdit {"
        " background: transparent;"
        " border: none;"
        " padding: 0px;"
        " margin: 0px;"
        f" color: {colors['text']};"
        f" selection-background-color: {colors['accent_tint']};"
        "}"
    )

    def _clear_selection() -> None:
        try:
            editor.deselect()
            editor.setCursorPosition(len(editor.text()))
        except Exception:
            pass

    QTimer.singleShot(0, _clear_selection)


def draw_course_row_card(
    painter,
    option,
    index,
    view: QTableView,
    first_col: int,
    *,
    completion_ready: bool = False,
) -> None:
    if not view or not view.model() or not index.isValid():
        return
    if index.column() != first_col:
        return

    left_idx = view.model().index(index.row(), first_col)
    left_rect = view.visualRect(left_idx)
    if not left_rect.isValid():
        return

    margin = 10
    row_rect = QRect(
        margin,
        left_rect.top() + 4,
        max(0, view.viewport().width() - (2 * margin)),
        max(0, left_rect.height() - 8),
    )

    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    dark = get_theme() == "dark"
    gradient = QLinearGradient(row_rect.topLeft(), row_rect.bottomLeft())
    if completion_ready and selected and dark:
        gradient.setColorAt(0.0, QColor(35, 78, 53))
        gradient.setColorAt(1.0, QColor(28, 62, 43))
    elif completion_ready and selected:
        gradient.setColorAt(0.0, QColor(235, 252, 241))
        gradient.setColorAt(1.0, QColor(220, 247, 229))
    elif completion_ready and dark:
        gradient.setColorAt(0.0, QColor(28, 56, 39))
        gradient.setColorAt(1.0, QColor(22, 44, 31))
    elif completion_ready:
        gradient.setColorAt(0.0, QColor(244, 253, 247))
        gradient.setColorAt(1.0, QColor(232, 249, 238))
    elif selected and dark:
        gradient.setColorAt(0.0, QColor(48, 57, 74))
        gradient.setColorAt(1.0, QColor(39, 47, 63))
    elif selected:
        gradient.setColorAt(0.0, QColor(253, 254, 255))
        gradient.setColorAt(1.0, QColor(241, 245, 255))
    elif dark:
        gradient.setColorAt(0.0, QColor(48, 48, 51))
        gradient.setColorAt(1.0, QColor(41, 41, 44))
    else:
        gradient.setColorAt(0.0, QColor(255, 255, 255))
        gradient.setColorAt(1.0, QColor(249, 251, 255))

    painter.save()
    painter.setRenderHint(painter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 36) if dark else QColor(26, 36, 78, 8))
    painter.drawRoundedRect(row_rect.adjusted(0, 1, 0, 1), 12, 12)
    painter.setBrush(gradient)
    painter.drawRoundedRect(row_rect, 12, 12)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    if completion_ready and dark:
        painter.setPen(QColor(74, 151, 103))
    elif completion_ready:
        painter.setPen(QColor(139, 208, 164))
    else:
        painter.setPen(QColor(70, 70, 70) if dark else QColor(225, 228, 241))
    painter.drawRoundedRect(row_rect, 12, 12)

    if selected:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if completion_ready and dark:
            painter.setPen(QColor(119, 223, 163))
        elif completion_ready:
            painter.setPen(QColor(61, 177, 104))
        else:
            painter.setPen(QColor(55, 148, 255) if dark else QColor(170, 181, 255))
        painter.drawRoundedRect(row_rect, 12, 12)

    painter.restore()


class CoursesBaseDelegate(QStyledItemDelegate):
    def __init__(self, view: QTableView, first_col: int, completion_ready_for_row: Callable[[int], bool] | None = None):
        super().__init__(view)
        self.view = view
        self.first_col = first_col
        self._completion_ready_for_row = completion_ready_for_row

    def paint(self, painter, option, index):
        draw_course_row_card(
            painter,
            option,
            index,
            self.view,
            self.first_col,
            completion_ready=bool(self._completion_ready_for_row(index.row())) if self._completion_ready_for_row else False,
        )

        if (
            self.view.state() == QAbstractItemView.State.EditingState
            and self.view.currentIndex() == index
        ):
            return

        opt = QStyleOptionViewItem(option)
        opt.state = opt.state & ~QStyle.StateFlag.State_Selected
        opt.state = opt.state & ~QStyle.StateFlag.State_MouseOver
        opt.state = opt.state & ~QStyle.StateFlag.State_HasFocus
        opt.showDecorationSelected = False
        self.initStyleOption(opt, index)
        opt.state = opt.state & ~QStyle.StateFlag.State_Selected
        opt.state = opt.state & ~QStyle.StateFlag.State_MouseOver
        opt.state = opt.state & ~QStyle.StateFlag.State_HasFocus
        opt.backgroundBrush = QBrush(Qt.GlobalColor.transparent)
        pal = QPalette(opt.palette)
        pal.setBrush(QPalette.ColorRole.Base, QBrush(Qt.GlobalColor.transparent))
        pal.setBrush(QPalette.ColorRole.Window, QBrush(Qt.GlobalColor.transparent))
        opt.palette = pal
        opt.rect = course_content_rect(opt)
        self._prepare_option(opt, index)
        style = option.widget.style() if option.widget is not None else self.view.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, option.widget)

    def _prepare_option(self, option: QStyleOptionViewItem, index) -> None:
        pass


class CoursesRowCardDelegate(CoursesBaseDelegate):
    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        style_course_inline_editor(editor)
        return editor

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(course_content_rect(option))


class CoursesTermDelegate(CoursesBaseDelegate):
    def _prepare_option(self, option: QStyleOptionViewItem, index) -> None:
        option.text = previous_course_term_label(index.data(Qt.DisplayRole))

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.setMinimumContentsLength(6)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for term_value, label in PREVIOUS_COURSE_TERMS:
            combo.addItem(label, term_value)
        return combo

    def setEditorData(self, editor, index):
        if not isinstance(editor, QComboBox):
            return
        current_value = normalize_previous_course_term(index.data(Qt.EditRole))
        editor.setCurrentIndex(max(0, editor.findData(current_value)))

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, str(editor.currentData() or "fall"), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(course_content_rect(option))


class CoursesAcademicYearDelegate(CoursesBaseDelegate):
    def _prepare_option(self, option: QStyleOptionViewItem, index) -> None:
        raw_value = index.data(Qt.DisplayRole)
        try:
            year_start = int(raw_value)
        except Exception:
            year_start = current_academic_year_start()
        option.text = previous_course_academic_year_label(year_start)

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.setMinimumContentsLength(9)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for label, start_year in previous_course_academic_year_options():
            combo.addItem(label, start_year)
        return combo

    def setEditorData(self, editor, index):
        if not isinstance(editor, QComboBox):
            return
        try:
            current_year = int(index.data(Qt.EditRole))
        except Exception:
            current_year = current_academic_year_start()
        if editor.findData(current_year) == -1:
            editor.addItem(previous_course_academic_year_label(current_year), current_year)
        editor.setCurrentIndex(max(0, editor.findData(current_year)))

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, int(editor.currentData() or current_academic_year_start()), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(course_content_rect(option))


class CoursesActionDelegate(CoursesBaseDelegate):
    def _prepare_option(self, option: QStyleOptionViewItem, index) -> None:
        option.text = ""


def build_courses_tab() -> QWidget:
    model = QSqlTableModel()
    model.setTable("courses")
    model.setEditStrategy(QSqlTableModel.EditStrategy.OnManualSubmit)
    ensure_current_courses_have_metadata()
    model.select()

    table = QTableView()
    table.setModel(model)
    table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
    table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)

    table.setShowGrid(False)
    table.setAlternatingRowColors(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(38)
    table.setWordWrap(False)

    hh = table.horizontalHeader()
    hh.setVisible(False)
    hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    hh.setStretchLastSection(False)
    hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

    id_col = model.fieldIndex("id")
    created_col = model.fieldIndex("created_at")
    year_col = model.fieldIndex("academic_year_start")
    if id_col != -1:
        table.setColumnHidden(id_col, True)

    name_col = model.fieldIndex("name")
    term_col = model.fieldIndex("term")

    if name_col != -1:
        model.setHeaderData(name_col, Qt.Horizontal, "Course")

    if name_col != -1:
        hh.setSectionResizeMode(name_col, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(name_col, 360)
    if term_col != -1:
        table.setColumnHidden(term_col, True)
    if year_col != -1:
        table.setColumnHidden(year_col, True)
    if created_col != -1:
        table.setColumnHidden(created_col, True)

    card_first_col = name_col if name_col != -1 else 0

    btn_add = QPushButton("Add")
    btn_add.setObjectName("PrimaryButton")
    btn_edit = QPushButton("Edit")
    btn_edit.setObjectName("SecondaryButton")
    btn_complete = QPushButton("Complete course")
    btn_complete.setObjectName("CourseSuccessButton")
    btn_previous = QPushButton("View Previous Courses")
    btn_previous.setObjectName("SecondaryButton")
    btn_undo_complete = QPushButton("Undo complete")
    btn_undo_complete.setObjectName("SecondaryButton")
    btn_delete = QPushButton("Delete")
    btn_delete.setObjectName("DangerButton")

    empty_label = QLabel("No courses saved")
    empty_label.setObjectName("EmptyState")
    empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    empty_label.setMinimumHeight(220)

    gpa_heading = QLabel("Cumulative GPA")
    gpa_heading.setObjectName("CoursesSideHeading")
    gpa_content_widget = QWidget()
    gpa_content_layout = QVBoxLayout()
    gpa_content_layout.setContentsMargins(0, 0, 0, 0)
    gpa_content_layout.setSpacing(8)
    gpa_content_widget.setLayout(gpa_content_layout)

    gpa_panel = QFrame()
    gpa_panel.setObjectName("Card")
    gpa_panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
    gpa_panel.setMinimumWidth(320)
    gpa_layout = QVBoxLayout()
    gpa_layout.setContentsMargins(12, 12, 12, 12)
    gpa_layout.setSpacing(10)
    gpa_layout.addWidget(gpa_heading)
    gpa_layout.addWidget(gpa_content_widget, 1)
    gpa_panel.setLayout(gpa_layout)

    btn_bar = QHBoxLayout()
    btn_bar.setContentsMargins(0, 0, 0, 0)
    btn_bar.setSpacing(10)
    btn_bar.addStretch(1)
    btn_bar.addWidget(btn_complete)
    btn_bar.addWidget(btn_add)
    btn_bar.addWidget(btn_edit)
    btn_bar.addWidget(btn_previous)
    btn_bar.addWidget(btn_undo_complete)
    btn_bar.addWidget(btn_delete)

    btn_bar_widget = QWidget()
    btn_bar_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    btn_bar_widget.setLayout(btn_bar)

    page = CoursesPage(
        model,
        table,
        empty_label,
        gpa_content_layout,
        btn_bar_widget,
        [
            btn_complete,
            btn_add,
            btn_edit,
            btn_previous,
            btn_undo_complete,
            btn_delete,
        ],
        btn_edit,
        btn_complete,
        btn_undo_complete,
        btn_delete,
    )
    if name_col != -1:
        table.setItemDelegateForColumn(
            name_col,
            CoursesRowCardDelegate(table, first_col=card_first_col, completion_ready_for_row=page._row_can_complete),
        )

    def add_row() -> None:
        page.add_course()

    btn_add.clicked.connect(add_row)
    btn_previous.clicked.connect(page.open_previous_courses_dialog)
    btn_delete.clicked.connect(page.delete_selected_course)

    title = QLabel("Courses")
    title.setObjectName("Title")
    subtitle = QLabel("Add and manage your courses. Import completed courses via AI.")
    subtitle.setObjectName("Subtitle")
    version_label = make_version_label()

    left = QVBoxLayout()
    left.setSpacing(2)
    left.addWidget(title)
    left.addWidget(subtitle)

    header_top = QHBoxLayout()
    header_top.setContentsMargins(0, 0, 0, 0)
    header_top.setSpacing(10)
    header_top.addLayout(left)
    header_top.addStretch(1)
    header_top.addWidget(version_label)

    header = QVBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(10)
    header.addLayout(header_top)
    header.addWidget(btn_bar_widget)

    card = QFrame()
    card.setObjectName("Card")
    card_layout = QVBoxLayout()
    card_layout.setContentsMargins(14, 14, 14, 14)
    card_layout.addWidget(table)
    card_layout.addWidget(empty_label)
    card.setLayout(card_layout)

    content = QHBoxLayout()
    content.setSpacing(12)
    content.addWidget(card, 1)
    content.addWidget(gpa_panel)

    root = QVBoxLayout()
    root.setContentsMargins(14, 14, 14, 14)
    root.setSpacing(10)
    root.addLayout(header)
    root.addLayout(content)
    page.setLayout(root)
    page.apply_settings()
    page.refresh()
    return page
