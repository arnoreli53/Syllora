from PySide6.QtCore import Qt, Signal, QMimeData, QObject, QThread, QRectF, QSize, QEasingCurve, QVariantAnimation, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtSql import QSqlQuery, QSqlDatabase
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QGridLayout,
    QBoxLayout,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QFormLayout,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QCheckBox,
    QDialog,
    QTableWidget,
    QTableWidgetItem,
    QDialogButtonBox,
    QFileDialog,
    QLineEdit,
    QTableView,
    QMessageBox,
    QProgressBar,
    QAbstractSpinBox,
    QSizePolicy,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QTabWidget,
)
import os
import json
import csv
import re
import subprocess
import webbrowser
from datetime import datetime
from functools import wraps
from pathlib import Path

from ui_common import (
    apply_body_font,
    common_page_stylesheet,
    font_metrics,
    make_version_label,
    normalize_font_mode,
    theme_colors,
)
from app_settings import app_qsettings, clear_all_app_qsettings
from paths import APP_SUPPORT_DIR
from secret_store import delete_secret, is_compromised_secret, load_secret, save_secret
from syllabus_import import (
    DEFAULT_SYLLABUS_AI_MODEL,
    SyllabusExtractionResult,
    SyllabusImportCoordinator,
    SyllabusRow,
    build_syllabus_validation_warnings,
    category_weight_total,
    coerce_bool,
    coerce_weight_percent,
    combine_due_parts,
    format_raw_weight,
    graded_weight_total,
    normalize_due_date,
    normalize_due_status,
    normalize_due_time,
    normalize_percent_text,
    normalize_weight_source,
    normalize_status,
    parse_raw_weight_display,
    openai_sdk_available,
)
from updater import (
    check_for_updates,
    current_app_bundle_path,
    download_update,
    is_app_bundle_in_applications,
    open_latest_releases_page,
    open_release_page,
    start_update_installer,
    verify_update,
)
from user_profiles import create_profile, current_profile, delete_profile, list_profiles, profile_summary

# -----------------------------
# Settings storage (SQLite)
# -----------------------------

SET_DEFAULT_STATUS = "default_status"
SET_UPCOMING_DAYS = "overview_upcoming_days"
SET_DUE_SOON_DAYS = "overview_due_soon_days"
SET_HIGH_PRIORITY_DUE_SOON_DAYS = "overview_high_priority_due_soon_days"
SET_HIGH_WEIGHT_OVERVIEW_ENABLED = "overview_high_weight_enabled"
SET_HIGH_WEIGHT_OVERVIEW_THRESHOLD = "overview_high_weight_threshold"
SET_HIDE_COMPLETED = "overview_hide_completed"
SET_PIN_OVERDUE = "overview_pin_overdue"
SET_OVERVIEW_REMINDERS_ENABLED = "overview_reminders_enabled"
SET_PIN_HIGH_PRIORITY_DAYS = "overview_pin_high_priority_days"
SET_PIN_HIGH_PRIORITY_ENABLED = "overview_pin_high_priority_enabled"
SET_CONFIRM_DELETE = "ui_confirm_delete"
SET_THEME = "ui_theme"
SET_FONT_SIZE = "ui_font_size"
SET_GRADE_DISPLAY = "overview_grade_display"
SET_GRADE_SCALE_JSON = "overview_grade_scale_json"
SET_GPA_VIEW_MODE = "courses_gpa_view_mode"
SET_GPA_LAST_X_CREDIT_HOURS = "courses_gpa_last_x_credit_hours"

# --- New setting keys ---
SET_IMPORT_DUP_MODE = "import_duplicate_mode"  # allow | skip | overwrite
SET_GRADE_CALC_MODE = "overview_grade_calc_mode"  # graded_only | submitted_as_zero
SET_COMPACT_ROWS = "ui_compact_rows"  # 0/1
SET_BACKUP_ENABLED = "backup_enabled"  # 0/1
SET_BACKUP_FREQ = "backup_frequency"  # daily | weekly
SET_BACKUP_FOLDER = "backup_folder"  # path
SET_CONFIRM_SUBMIT_OVERVIEW = "overview_confirm_submit"  # 0/1
SET_LAST_BACKUP_TS = "backup_last_ts"  # epoch seconds as text
SET_PRIORITY_ENABLED = "tasks_priority_enabled"  # 0/1
SET_IN_PROGRESS_ENABLED = "tasks_in_progress_enabled"  # 0/1
SET_OPENAI_API_KEY = "openai_api_key"  # masked text
SET_SYLLABUS_AI_MODEL = "syllabus_ai_model"  # model name
SENSITIVE_SETTING_KEYS = frozenset({SET_OPENAI_API_KEY})

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


def ensure_settings_table() -> None:
    q = QSqlQuery()
    if not q.exec(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    ):
        raise RuntimeError(q.lastError().text())


def database_transactional(function):
    """Run a database mutation as an all-or-nothing operation."""

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


def _get(key: str, default: str) -> str:
    ensure_settings_table()
    q = QSqlQuery()
    q.prepare("SELECT value FROM settings WHERE key = ?")
    q.addBindValue(key)
    if not q.exec():
        return default
    if q.next():
        v = q.value(0)
        return default if v is None else str(v)
    return default


def _set(key: str, value: str) -> None:
    ensure_settings_table()
    q = QSqlQuery()
    q.prepare(
        """
        INSERT INTO settings(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value;
        """
    )
    q.addBindValue(key)
    q.addBindValue(value)
    if not q.exec():
        raise RuntimeError(q.lastError().text())


def _delete_setting(key: str) -> None:
    ensure_settings_table()
    q = QSqlQuery()
    q.prepare("DELETE FROM settings WHERE key = ?")
    q.addBindValue(key)
    if not q.exec():
        raise RuntimeError(q.lastError().text())


def get_bool(key: str, default: bool) -> bool:
    s = _get(key, "1" if default else "0").strip().lower()
    return s in ("1", "true", "yes", "y", "on")


def set_bool(key: str, value: bool) -> None:
    _set(key, "1" if value else "0")


def get_int(key: str, default: int) -> int:
    try:
        return int(_get(key, str(default)).strip())
    except Exception:
        return default


def set_int(key: str, value: int) -> None:
    _set(key, str(int(value)))


def get_str(key: str, default: str) -> str:
    return _get(key, default)


def set_str(key: str, value: str) -> None:
    _set(key, value)


def get_overview_upcoming_days() -> int:
    return max(1, min(60, get_int(SET_UPCOMING_DAYS, 14)))


def get_overview_due_soon_days() -> int:
    return max(1, min(30, get_int(SET_DUE_SOON_DAYS, 3)))


def get_overview_high_priority_due_soon_days() -> int:
    raw = get_str(SET_HIGH_PRIORITY_DUE_SOON_DAYS, "__unset__").strip().lower()
    if raw == "__unset__":
        return get_overview_due_soon_days()
    try:
        value = int(raw)
    except Exception:
        value = get_overview_due_soon_days()
    return max(0, min(30, value))


def get_overview_high_weight_enabled() -> bool:
    return get_bool(SET_HIGH_WEIGHT_OVERVIEW_ENABLED, False)


def get_overview_high_weight_threshold() -> float:
    raw = get_str(SET_HIGH_WEIGHT_OVERVIEW_THRESHOLD, "15").strip()
    try:
        value = float(raw)
    except Exception:
        value = 15.0
    return max(0.0, min(100.0, value))


def get_overview_hide_completed() -> bool:
    return get_bool(SET_HIDE_COMPLETED, True)


def get_overview_pin_overdue() -> bool:
    return get_bool(SET_PIN_OVERDUE, True)


def get_overview_reminders_enabled() -> bool:
    return get_bool(SET_OVERVIEW_REMINDERS_ENABLED, True)


def get_overview_pin_high_priority_days() -> int:
    return max(0, min(60, get_int(SET_PIN_HIGH_PRIORITY_DAYS, 7)))


def get_overview_pin_high_priority_enabled() -> bool:
    raw = get_str(SET_PIN_HIGH_PRIORITY_ENABLED, "__unset__").strip().lower()
    if raw == "__unset__":
        return get_overview_pin_high_priority_days() > 0
    return raw in ("1", "true", "yes", "y", "on")


def get_default_status() -> str:
    v = get_str(SET_DEFAULT_STATUS, "not started").strip().lower()
    allowed = {"not started", "in progress", "submitted", "graded"}
    return v if v in allowed else "not started"


def get_confirm_delete() -> bool:
    return get_bool(SET_CONFIRM_DELETE, True)

def get_theme() -> str:
    if get_str(SET_THEME, "dark") != "dark":
        set_str(SET_THEME, "dark")
    return "dark"


# --- New settings getters ---
def get_font_size_mode() -> str:
    return normalize_font_mode(get_str(SET_FONT_SIZE, "normal"))


def get_import_duplicate_mode() -> str:
    v = get_str(SET_IMPORT_DUP_MODE, "skip").strip().lower()
    return v if v in {"allow", "skip", "overwrite"} else "skip"


def get_grade_calc_mode() -> str:
    v = get_str(SET_GRADE_CALC_MODE, "graded_only").strip().lower()
    return v if v in {"graded_only", "submitted_as_zero"} else "graded_only"


def get_compact_rows() -> bool:
    return get_bool(SET_COMPACT_ROWS, False)


def get_backup_enabled() -> bool:
    return get_bool(SET_BACKUP_ENABLED, False)


def get_backup_frequency() -> str:
    v = get_str(SET_BACKUP_FREQ, "weekly").strip().lower()
    return v if v in {"daily", "weekly"} else "weekly"


def get_backup_folder() -> str:
    return get_str(SET_BACKUP_FOLDER, "").strip()


def _current_user_data_folder() -> Path:
    profile = current_profile()
    return profile.data_dir if profile is not None else APP_SUPPORT_DIR


def get_confirm_submit_overview() -> bool:
    return get_bool(SET_CONFIRM_SUBMIT_OVERVIEW, True)

def get_priority_enabled() -> bool:
    return get_bool(SET_PRIORITY_ENABLED, False)


def get_in_progress_enabled() -> bool:
    return get_bool(SET_IN_PROGRESS_ENABLED, True)


def get_syllabus_ai_model() -> str:
    value = get_str(SET_SYLLABUS_AI_MODEL, DEFAULT_SYLLABUS_AI_MODEL).strip()
    return value or DEFAULT_SYLLABUS_AI_MODEL


def get_saved_openai_api_key() -> str:
    profile = current_profile()
    return load_secret(profile.id if profile is not None else "default")


def get_effective_openai_api_key() -> str:
    environment_key = os.environ.get("OPENAI_API_KEY", "").strip()
    return environment_key or get_saved_openai_api_key()


def ensure_syllabus_ai_defaults() -> None:
    legacy_key = get_str(SET_OPENAI_API_KEY, "").strip()
    if legacy_key:
        profile = current_profile()
        profile_id = profile.id if profile is not None else "default"
        if is_compromised_secret(legacy_key):
            _delete_setting(SET_OPENAI_API_KEY)
        else:
            try:
                save_secret(profile_id, legacy_key)
            except Exception:
                # Keep a user-provided legacy key until Keychain migration succeeds.
                pass
            else:
                _delete_setting(SET_OPENAI_API_KEY)
    if not get_str(SET_SYLLABUS_AI_MODEL, "").strip():
        set_str(SET_SYLLABUS_AI_MODEL, DEFAULT_SYLLABUS_AI_MODEL)


def get_grade_display_mode() -> str:
    v = get_str(SET_GRADE_DISPLAY, "percent").strip().lower()
    allowed = {"percent", "letter", "gpa4.0", "gpa4.3"}
    return v if v in allowed else "percent"


def get_gpa_view_mode() -> str:
    v = get_str(SET_GPA_VIEW_MODE, "cumulative").strip().lower()
    return v if v in {"cumulative", "last_x", "both"} else "cumulative"


def get_gpa_last_x_credit_hours() -> int:
    return max(1, min(300, get_int(SET_GPA_LAST_X_CREDIT_HOURS, 60)))


def _default_grade_scale() -> list[dict]:
    # Default matches your earlier letter thresholds.
    # min_pct is the inclusive lower bound.
    return [
        {"label": "A+", "min_pct": 90, "gpa4": 4.0, "gpa43": 4.3},
        {"label": "A",  "min_pct": 85, "gpa4": 4.0, "gpa43": 4.0},
        {"label": "A-", "min_pct": 80, "gpa4": 3.7, "gpa43": 3.7},
        {"label": "B+", "min_pct": 77, "gpa4": 3.3, "gpa43": 3.3},
        {"label": "B",  "min_pct": 73, "gpa4": 3.0, "gpa43": 3.0},
        {"label": "B-", "min_pct": 70, "gpa4": 2.7, "gpa43": 2.7},
        {"label": "C+", "min_pct": 65, "gpa4": 2.3, "gpa43": 2.3},
        {"label": "C",  "min_pct": 60, "gpa4": 2.0, "gpa43": 2.0},
        {"label": "C-", "min_pct": 55, "gpa4": 1.7, "gpa43": 1.7},
        {"label": "D",  "min_pct": 50, "gpa4": 1.0, "gpa43": 1.0},
        {"label": "F",  "min_pct": 0,  "gpa4": 0.0, "gpa43": 0.0},
    ]


def get_grade_scale() -> list[dict]:
    raw = get_str(SET_GRADE_SCALE_JSON, "").strip()
    if not raw:
        return _default_grade_scale()
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return _default_grade_scale()
        # Basic validation/coercion
        out: list[dict] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label", "")).strip()
            if not label:
                continue
            try:
                min_pct = int(row.get("min_pct", 0))
            except Exception:
                min_pct = 0
            try:
                gpa4 = float(row.get("gpa4", 0.0))
            except Exception:
                gpa4 = 0.0
            try:
                gpa43 = float(row.get("gpa43", 0.0))
            except Exception:
                gpa43 = 0.0
            out.append({"label": label, "min_pct": min_pct, "gpa4": gpa4, "gpa43": gpa43})
        if not out:
            return _default_grade_scale()
        # Sort by min_pct desc
        out.sort(key=lambda r: int(r.get("min_pct", 0)), reverse=True)
        return out
    except Exception:
        return _default_grade_scale()


def set_grade_scale(scale: list[dict]) -> None:
    # Sort by min_pct desc and persist
    scale2 = list(scale)
    scale2.sort(key=lambda r: int(r.get("min_pct", 0)), reverse=True)
    set_str(SET_GRADE_SCALE_JSON, json.dumps(scale2))


def grade_scale_row_for_percent(percent: float | int | None, *, scale: list[dict] | None = None) -> dict | None:
    if percent is None:
        return None
    try:
        percent_value = float(percent)
    except Exception:
        return None

    scale_rows = get_grade_scale() if scale is None else list(scale)
    for row in scale_rows:
        try:
            min_pct = float(row.get("min_pct", 0))
        except Exception:
            min_pct = 0.0
        if percent_value >= min_pct:
            return row
    return scale_rows[-1] if scale_rows else None


_GRADE_LABEL_TRANSLATION = str.maketrans(
    {
        "−": "-",
        "–": "-",
        "—": "-",
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "﹣": "-",
        "－": "-",
        "＋": "+",
        "﹢": "+",
    }
)


def _normalize_grade_scale_label(label: object) -> str:
    text = str(label or "").strip().translate(_GRADE_LABEL_TRANSLATION).lower()
    return "".join(text.split())


def grade_scale_row_for_label(label: object, *, scale: list[dict] | None = None) -> dict | None:
    target = _normalize_grade_scale_label(label)
    if not target:
        return None
    scale_rows = get_grade_scale() if scale is None else list(scale)
    for row in scale_rows:
        if _normalize_grade_scale_label(row.get("label", "")) == target:
            return row
    return None


def gpa4_for_percent(percent: float | int | None, *, scale: list[dict] | None = None) -> float | None:
    row = grade_scale_row_for_percent(percent, scale=scale)
    if not row:
        return None
    try:
        return float(row.get("gpa4", 0.0))
    except Exception:
        return None


def gpa4_for_grade_label(label: object, *, scale: list[dict] | None = None) -> float | None:
    row = grade_scale_row_for_label(label, scale=scale)
    if not row:
        return None
    try:
        return float(row.get("gpa4", 0.0))
    except Exception:
        return None


def gpa43_for_percent(percent: float | int | None, *, scale: list[dict] | None = None) -> float | None:
    row = grade_scale_row_for_percent(percent, scale=scale)
    if not row:
        return None
    try:
        return float(row.get("gpa43", 0.0))
    except Exception:
        return None


def gpa43_for_grade_label(label: object, *, scale: list[dict] | None = None) -> float | None:
    row = grade_scale_row_for_label(label, scale=scale)
    if not row:
        return None
    try:
        return float(row.get("gpa43", 0.0))
    except Exception:
        return None


def grade_scale_row_for_gpa43(value: float | int | None, *, scale: list[dict] | None = None) -> dict | None:
    if value is None:
        return None
    try:
        value_num = float(value)
    except Exception:
        return None

    scale_rows = get_grade_scale() if scale is None else list(scale)
    ranked_rows: list[dict] = []
    for row in scale_rows:
        try:
            gpa43 = float(row.get("gpa43", 0.0))
        except Exception:
            gpa43 = 0.0
        ranked_rows.append({**row, "_gpa43": gpa43})

    ranked_rows.sort(
        key=lambda row: (float(row.get("_gpa43", 0.0)), float(row.get("min_pct", 0))),
        reverse=True,
    )
    for row in ranked_rows:
        if value_num >= float(row.get("_gpa43", 0.0)):
            return row
    return ranked_rows[-1] if ranked_rows else None


def parse_task_grade_input(value: object, *, scale: list[dict] | None = None) -> tuple[float | str, str] | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None

    numeric_text = text.translate(_GRADE_LABEL_TRANSLATION).replace("%", "").replace("％", "").replace(",", "").strip()
    try:
        numeric_value = float(numeric_text)
    except Exception:
        numeric_value = None

    if numeric_value is not None:
        if numeric_value < 0:
            return None
        return numeric_value, "percent"

    row = grade_scale_row_for_label(text, scale=scale)
    if not row:
        stuck_placeholder = re.match(r"^\s*0+(?:\.0+)?\s*(.+?)\s*$", text.translate(_GRADE_LABEL_TRANSLATION))
        if stuck_placeholder:
            row = grade_scale_row_for_label(stuck_placeholder.group(1), scale=scale)
    if not row:
        return None
    label = str(row.get("label", "")).strip() or text
    return label, "letter"


class GradeScaleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit grade scale")
        self.setModal(True)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Label", "Min %", "GPA 4.0", "GPA 4.3"])
        self.table.horizontalHeader().setStretchLastSection(True)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout()
        root.addWidget(QLabel("Set the minimum percent for each grade label. Rows are interpreted as: min%–next higher min%.") )
        root.addWidget(self.table)
        root.addWidget(btns)
        self.setLayout(root)

        self.load_from_settings()

    def load_from_settings(self) -> None:
        data = get_grade_scale()
        self.table.setRowCount(len(data))
        for r, row in enumerate(data):
            self.table.setItem(r, 0, QTableWidgetItem(str(row.get("label", ""))))
            self.table.setItem(r, 1, QTableWidgetItem(str(row.get("min_pct", 0))))
            self.table.setItem(r, 2, QTableWidgetItem(str(row.get("gpa4", 0.0))))
            self.table.setItem(r, 3, QTableWidgetItem(str(row.get("gpa43", 0.0))))

    def get_scale(self) -> list[dict]:
        out: list[dict] = []
        for r in range(self.table.rowCount()):
            label_item = self.table.item(r, 0)
            min_item = self.table.item(r, 1)
            g4_item = self.table.item(r, 2)
            g43_item = self.table.item(r, 3)

            label = "" if label_item is None else label_item.text().strip()
            if not label:
                continue

            try:
                min_pct = int(("" if min_item is None else min_item.text()).strip())
            except Exception:
                min_pct = 0

            try:
                gpa4 = float(("" if g4_item is None else g4_item.text()).strip())
            except Exception:
                gpa4 = 0.0

            try:
                gpa43 = float(("" if g43_item is None else g43_item.text()).strip())
            except Exception:
                gpa43 = 0.0

            out.append({"label": label, "min_pct": min_pct, "gpa4": gpa4, "gpa43": gpa43})

        if not out:
            return _default_grade_scale()
        out.sort(key=lambda rr: int(rr.get("min_pct", 0)), reverse=True)
        return out


class ApiKeyDialog(QDialog):
    def __init__(self, initial_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OpenAI API key")
        self.setModal(True)
        self.setMinimumWidth(440)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-...")
        self.key_edit.setText(initial_key)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout()
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        root.addWidget(QLabel("Enter OpenAI API key"))
        root.addWidget(self.key_edit)
        root.addWidget(btns)
        self.setLayout(root)

    def api_key(self) -> str:
        return self.key_edit.text().strip()

class DropLabel(QLabel):
    def __init__(self, path_edit: QLineEdit, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.path_edit = path_edit
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            self.path_edit.setText(urls[0].toLocalFile())


class SwitchCheckBox(QCheckBox):
    TRACK_W = 38
    TRACK_H = 22
    KNOB_D = 16

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._thumb_progress = 1.0 if self.isChecked() else 0.0
        self._toggle_animation = QVariantAnimation(self)
        self._toggle_animation.setDuration(170)
        self._toggle_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._toggle_animation.valueChanged.connect(self._on_animation_value_changed)
        self.toggled.connect(self._start_toggle_animation)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        width = self.TRACK_W + 10 + fm.horizontalAdvance(self.text()) + 8
        height = max(self.TRACK_H, fm.height()) + 6
        return QSize(width, height)

    def _on_animation_value_changed(self, value) -> None:
        try:
            self._thumb_progress = float(value)
        except Exception:
            self._thumb_progress = 1.0 if self.isChecked() else 0.0
        self.update()

    def _start_toggle_animation(self, checked: bool) -> None:
        end_value = 1.0 if checked else 0.0
        if not self.isVisible():
            self._toggle_animation.stop()
            self._thumb_progress = end_value
            self.update()
            return
        self._toggle_animation.stop()
        self._toggle_animation.setStartValue(self._thumb_progress)
        self._toggle_animation.setEndValue(end_value)
        self._toggle_animation.start()

    @staticmethod
    def _blend_color(start: QColor, end: QColor, progress: float) -> QColor:
        p = max(0.0, min(1.0, progress))
        return QColor(
            round(start.red() + (end.red() - start.red()) * p),
            round(start.green() + (end.green() - start.green()) * p),
            round(start.blue() + (end.blue() - start.blue()) * p),
            round(start.alpha() + (end.alpha() - start.alpha()) * p),
        )

    def paintEvent(self, event) -> None:
        del event
        colors = theme_colors(get_theme())
        dark = get_theme() == "dark"
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect()
        track_rect = QRectF(0, (rect.height() - self.TRACK_H) / 2, self.TRACK_W, self.TRACK_H)
        inset = (self.TRACK_H - self.KNOB_D) / 2
        progress = max(0.0, min(1.0, self._thumb_progress))
        off_x = track_rect.left() + inset
        on_x = track_rect.right() - self.KNOB_D - inset
        knob_x = off_x + ((on_x - off_x) * progress)
        knob_rect = QRectF(knob_x, track_rect.top() + inset, self.KNOB_D, self.KNOB_D)

        off_track_color = QColor("#E5E7EB") if not dark else QColor("#303B47")
        on_track_color = QColor(colors["accent"])
        off_knob_color = QColor("#6B7280") if not dark else QColor("#B8C2CC")
        on_knob_color = QColor("#FFFFFF")

        track_color = self._blend_color(off_track_color, on_track_color, progress)
        knob_color = self._blend_color(off_knob_color, on_knob_color, progress)

        text_color = QColor(colors["text"] if self.isEnabled() else colors["faint_text"])
        off_border_color = QColor(colors["input_border"])
        on_border_color = QColor(colors["input_border_hover"])
        border_color = self._blend_color(off_border_color, on_border_color, progress)

        if not self.isEnabled():
            track_color.setAlpha(140)
            knob_color.setAlpha(180)
            border_color.setAlpha(120)

        painter.setPen(QPen(border_color, 1))
        painter.setBrush(track_color)
        painter.drawRoundedRect(track_rect, self.TRACK_H / 2, self.TRACK_H / 2)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_color)
        painter.drawEllipse(knob_rect)

        text_rect = QRectF(track_rect.right() + 10, 0, max(0.0, rect.width() - track_rect.width() - 10), rect.height())
        painter.setPen(text_color)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())

        if self.hasFocus():
            focus_pen = QPen(QColor(colors["accent"]), 1.5)
            painter.setPen(focus_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track_rect.adjusted(-2, -2, 2, 2), (self.TRACK_H / 2) + 2, (self.TRACK_H / 2) + 2)

class SyllabusDropDialog(QDialog):
    """Pick a syllabus file via browse or drag/drop."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import from syllabus")
        self.setModal(True)
        self.setMinimumWidth(520)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Choose a syllabus file (PDF/DOCX/TXT)")

        btn_browse = QPushButton("Browse…")
        btn_browse.setObjectName("SecondaryButton")
        btn_browse.clicked.connect(self._browse)

        self.drop = DropLabel(self.path_edit, "Drag & drop your syllabus here")
        self.drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop.setMinimumHeight(160)
        colors = theme_colors(get_theme())
        drop_bg = "rgba(0,0,0,0.02)" if get_theme() == "light" else "rgba(255,255,255,0.03)"
        self.drop.setStyleSheet(
            f"border: 2px dashed {colors['input_border']}; border-radius: 10px; background: {drop_bg}; color: {colors['muted_text']};"
        )
        self.drop.setAcceptDrops(True)

        # Monkey-patch drag/drop handlers on the label
        def _dragEnter(e):
            if e.mimeData().hasUrls():
                e.acceptProposedAction()
        def _dropEvent(e):
            urls = e.mimeData().urls()
            if urls:
                self.path_edit.setText(urls[0].toLocalFile())
        self.drop.dragEnterEvent = _dragEnter  # type: ignore
        self.drop.dropEvent = _dropEvent  # type: ignore

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Next")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(self.path_edit, 1)
        top.addWidget(btn_browse)

        privacy_note = QLabel("Privacy: continuing sends this file to OpenAI for extraction. You can review every item before it is saved.")
        privacy_note.setObjectName("FooterNote")
        privacy_note.setWordWrap(True)

        root = QVBoxLayout()
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        root.addLayout(top)
        root.addWidget(self.drop)
        root.addWidget(privacy_note)
        root.addWidget(btns)
        self.setLayout(root)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose syllabus", "", "Syllabus (*.pdf *.docx *.txt);;All files (*)")
        if path:
            self.path_edit.setText(path)

    def path(self) -> str:
        return self.path_edit.text().strip()


class SyllabusReviewDialog(QDialog):
    """Review extracted tasks; user can edit cells and delete rows."""

    def __init__(self, courses: list[str], result: SyllabusExtractionResult, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review syllabus items")
        self.setModal(True)
        self.setMinimumWidth(960)
        self.setMinimumHeight(520)
        self._result = result

        self.course = QComboBox()
        self.course.setEditable(True)
        self.course.addItems(courses if courses else [""])
        suggested_course = (result.course_suggestion or "").strip()
        if suggested_course:
            if self.course.findText(suggested_course) < 0:
                self.course.insertItem(0, suggested_course)
            self.course.setCurrentText(suggested_course)

        self.chk_skip_ungraded = QCheckBox("Don't import Ungraded Tasks")
        self.chk_skip_ungraded.toggled.connect(lambda _checked: self._set_banner_text())
        self.chk_skip_notes = QCheckBox("Don't import Task Notes")
        self.chk_skip_notes.toggled.connect(lambda _checked: self._set_banner_text())

        self.table = QTableWidget()
        self.table.setColumnCount(11)
        self.table.setHorizontalHeaderLabels(
            ["Item", "Component", "Due", "Due Status", "Weight (%)", "Raw Weight", "Weight Source", "Ungraded", "Status", "Notes", "✕"]
        )
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, self.table.horizontalHeader().ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, self.table.horizontalHeader().ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, self.table.horizontalHeader().ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, self.table.horizontalHeader().ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, self.table.horizontalHeader().ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, self.table.horizontalHeader().ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, self.table.horizontalHeader().ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, self.table.horizontalHeader().ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(8, self.table.horizontalHeader().ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(9, self.table.horizontalHeader().ResizeMode.Stretch)
        self.table.setColumnWidth(10, 44)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setObjectName("Subtitle")

        self._load_rows(result.rows)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self._set_banner_text()

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        head = QHBoxLayout()
        head.addWidget(QLabel("Course:"))
        head.addWidget(self.course)
        head.addSpacing(12)
        head.addWidget(self.chk_skip_ungraded)
        head.addWidget(self.chk_skip_notes)
        head.addStretch(1)

        root = QVBoxLayout()
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        root.addLayout(head)
        root.addWidget(self.banner)
        root.addWidget(self.table, 1)
        root.addWidget(btns)
        self.setLayout(root)

    def _set_banner_text(self) -> None:
        extracted_rows = self.get_rows(include_ungraded=True)
        import_rows = self.get_rows()
        staged_rows = self._stage_rows(import_rows)
        category_total = category_weight_total(self._result.categories)
        parts: list[str] = [
            f"AI extracted {len(extracted_rows)} item(s); {len(import_rows)} item(s) will be imported."
        ]
        seen_parts = {parts[0]}

        def add_part(text: str) -> None:
            message = (text or "").strip()
            if not message or message in seen_parts:
                return
            seen_parts.add(message)
            parts.append(message)

        for warning in self._result.warnings:
            add_part(warning)
        if self.chk_skip_ungraded.isChecked():
            add_part("Ungraded rows will be skipped on import.")
        if self.chk_skip_notes.isChecked():
            add_part("Task notes will be excluded from import.")
        if import_rows:
            inferred_weight_rows = [row for row in staged_rows if not row.ungraded and row.weight_source == "equal_split_inferred"]
            conditional_weight_rows = [row for row in staged_rows if not row.ungraded and row.weight_source == "conditional"]
            missing_weight_rows = [row for row in staged_rows if not row.ungraded and row.weight_source == "missing"]
            if inferred_weight_rows:
                add_part(f"{len(inferred_weight_rows)} item(s) use inferred equal-split weights.")
            if conditional_weight_rows:
                add_part(f"{len(conditional_weight_rows)} item(s) use conditional weighting.")
            if missing_weight_rows:
                add_part(f"{len(missing_weight_rows)} item(s) still have unresolved weights.")
            if self._result.categories:
                add_part(f"Top-level category total: {category_total:.2f}%.")
            else:
                add_part(f"Graded weight total: {graded_weight_total(staged_rows):.2f}%.")
            for warning in build_syllabus_validation_warnings(
                staged_rows,
                categories=self._result.categories,
                grading_context=self._result.grading_context,
            ):
                add_part(warning)
        self.banner.setText(" ".join(parts))

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if item is None:
            return
        if item.column() == 4:
            normalized = normalize_percent_text(item.text())
            if item.text().strip() != normalized:
                self.table.blockSignals(True)
                item.setText(normalized)
                self.table.blockSignals(False)
            if normalized:
                source_item = self.table.item(item.row(), 6)
                if source_item is not None and source_item.text().strip().lower() != "explicit":
                    self.table.blockSignals(True)
                    source_item.setText("explicit")
                    self.table.blockSignals(False)
        elif item.column() == 5:
            raw_value, raw_unit = parse_raw_weight_display(item.text())
            formatted = format_raw_weight(raw_value, raw_unit)
            if item.text().strip() != formatted:
                self.table.blockSignals(True)
                item.setText(formatted)
                self.table.blockSignals(False)
            if formatted:
                source_item = self.table.item(item.row(), 6)
                if source_item is not None and source_item.text().strip().lower() != "explicit":
                    self.table.blockSignals(True)
                    source_item.setText("explicit")
                    self.table.blockSignals(False)
        self._set_banner_text()

    def _load_rows(self, rows: list[SyllabusRow]) -> None:
        self.table.setRowCount(0)
        for r in rows:
            self._append_row(r)

    def _append_row(self, r: SyllabusRow) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        due_text = combine_due_parts(r.due_date, r.due_time)
        item_item = QTableWidgetItem(r.item)
        item_item.setData(
            Qt.ItemDataRole.UserRole,
            {
                "parent_category": r.parent_category,
                "parent_total_weight_value": r.parent_total_weight_value,
                "parent_total_weight_unit": r.parent_total_weight_unit,
                "child_index": r.child_index,
                "child_count": r.child_count,
                "policy_notes": r.policy_notes,
                "confidence": r.confidence,
                "source_excerpt": r.source_excerpt,
            },
        )
        component_item = QTableWidgetItem(r.component)
        due_item = QTableWidgetItem(due_text)
        due_status_item = QTableWidgetItem(r.due_status or ("exact" if due_text else "unknown"))
        weight_item = QTableWidgetItem(r.weight_percent)
        raw_weight_item = QTableWidgetItem(format_raw_weight(r.raw_weight_value, r.raw_weight_unit))
        weight_source_item = QTableWidgetItem(normalize_weight_source(r.weight_source))
        weight_source_item.setFlags(weight_source_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        ungraded_item = QTableWidgetItem("")
        ungraded_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        ungraded_item.setCheckState(
            Qt.CheckState.Checked if r.ungraded else Qt.CheckState.Unchecked
        )
        status_item = QTableWidgetItem(normalize_status(r.status or get_default_status()))
        status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        notes_item = QTableWidgetItem(r.notes)

        tooltip_parts: list[str] = []
        if r.confidence < 0.999:
            tooltip_parts.append(f"Confidence: {int(round(r.confidence * 100))}%")
        if r.source_excerpt:
            tooltip_parts.append(f"Source: {r.source_excerpt}")
        tooltip = "\n".join(tooltip_parts)
        for item in (
            item_item,
            component_item,
            due_item,
            due_status_item,
            weight_item,
            raw_weight_item,
            weight_source_item,
            ungraded_item,
            status_item,
            notes_item,
        ):
            item.setToolTip(tooltip)

        if r.confidence < 0.55:
            warning_bg = QColor(255, 244, 214)
            for item in (
                item_item,
                component_item,
                due_item,
                due_status_item,
                weight_item,
                raw_weight_item,
                weight_source_item,
                ungraded_item,
                status_item,
                notes_item,
            ):
                item.setBackground(warning_bg)

        self.table.setItem(row, 0, item_item)
        self.table.setItem(row, 1, component_item)
        self.table.setItem(row, 2, due_item)
        self.table.setItem(row, 3, due_status_item)
        self.table.setItem(row, 4, weight_item)
        self.table.setItem(row, 5, raw_weight_item)
        self.table.setItem(row, 6, weight_source_item)
        self.table.setItem(row, 7, ungraded_item)
        self.table.setItem(row, 8, status_item)
        self.table.setItem(row, 9, notes_item)

        btn = QPushButton("✕")
        btn.setFixedWidth(32)
        colors = theme_colors(get_theme())
        btn.setStyleSheet(
            "QPushButton { border: 0px; background: transparent; font-weight: 700; "
            f"color: {colors['danger_text']}; }}"
        )
        btn.clicked.connect(lambda _=None, b=btn: self._remove_button_row(b))
        self.table.setCellWidget(row, 10, btn)

    def _remove_button_row(self, button: QPushButton) -> None:
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, 10) is button:
                self.table.removeRow(row)
                self._set_banner_text()
                break

    def _stage_rows(self, rows: list[dict]) -> list[SyllabusRow]:
        staged_rows: list[SyllabusRow] = []
        for row in rows:
            normalized_due = _normalize_due_for_task_import(str(row.get("due", "")).strip()) or ""
            due_parts = normalized_due.split(" ", 1)
            due_date = due_parts[0] if due_parts else ""
            due_time = due_parts[1] if len(due_parts) > 1 else ""
            raw_weight_value, raw_weight_unit = parse_raw_weight_display(str(row.get("raw_weight", "")).strip())
            staged_rows.append(
                SyllabusRow(
                    item=str(row.get("item", "")).strip(),
                    component=str(row.get("component", "")).strip(),
                    due_date=due_date,
                    due_time=due_time,
                    due_status=normalize_due_status(row.get("due_status", ""), has_due=bool(due_date)),
                    weight_percent=normalize_percent_text(str(row.get("weight", "")).strip()),
                    raw_weight_value=raw_weight_value,
                    raw_weight_unit=raw_weight_unit,
                    weight_source=normalize_weight_source(row.get("weight_source", "")),
                    parent_category=str(row.get("parent_category", "")).strip(),
                    parent_total_weight_value=str(row.get("parent_total_weight_value", "")).strip(),
                    parent_total_weight_unit=str(row.get("parent_total_weight_unit", "")).strip(),
                    child_index=int(row.get("child_index", 0) or 0),
                    child_count=int(row.get("child_count", 0) or 0),
                    policy_notes=str(row.get("policy_notes", "")).strip(),
                    ungraded=bool(row.get("ungraded", False)),
                    status=str(row.get("status", "not started")).strip(),
                    notes=str(row.get("notes", "")).strip(),
                )
            )
        return staged_rows

    def get_rows(self, *, include_ungraded: bool | None = None) -> list[dict]:
        if include_ungraded is None:
            include_ungraded = not self.chk_skip_ungraded.isChecked()
        out: list[dict] = []
        for r in range(self.table.rowCount()):
            item_widget = self.table.item(r, 0)
            item = (item_widget.text() if item_widget else "").strip()
            component = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
            due = (self.table.item(r, 2).text() if self.table.item(r, 2) else "").strip()
            due_status = normalize_due_status(
                self.table.item(r, 3).text() if self.table.item(r, 3) else "",
                has_due=bool(due),
            )
            weight = normalize_percent_text(self.table.item(r, 4).text() if self.table.item(r, 4) else "")
            raw_weight = (self.table.item(r, 5).text() if self.table.item(r, 5) else "").strip()
            weight_source = normalize_weight_source(self.table.item(r, 6).text() if self.table.item(r, 6) else "")
            ungraded_item = self.table.item(r, 7)
            ungraded = bool(
                ungraded_item is not None
                and ungraded_item.checkState() == Qt.CheckState.Checked
            )
            status = (self.table.item(r, 8).text() if self.table.item(r, 8) else "not started").strip().lower()
            notes = (self.table.item(r, 9).text() if self.table.item(r, 9) else "").strip()
            meta = item_widget.data(Qt.ItemDataRole.UserRole) if item_widget is not None else {}
            if not isinstance(meta, dict):
                meta = {}
            if not item:
                continue
            if not include_ungraded and ungraded:
                continue
            out.append(
                {
                    "item": item,
                    "component": component,
                    "due": due,
                    "due_status": due_status,
                    "weight": weight,
                    "raw_weight": raw_weight,
                    "weight_source": weight_source,
                    "ungraded": ungraded,
                    "status": status,
                    "notes": notes,
                    "parent_category": str(meta.get("parent_category", "")).strip(),
                    "parent_total_weight_value": str(meta.get("parent_total_weight_value", "")).strip(),
                    "parent_total_weight_unit": str(meta.get("parent_total_weight_unit", "")).strip(),
                    "child_index": meta.get("child_index", 0) or 0,
                    "child_count": meta.get("child_count", 0) or 0,
                    "policy_notes": str(meta.get("policy_notes", "")).strip(),
                }
            )
        return out

    def selected_course(self) -> str:
        return self.course.currentText().strip()

    def import_notes_enabled(self) -> bool:
        return not self.chk_skip_notes.isChecked()


def _list_courses() -> list[str]:
    q = QSqlQuery()
    out: list[str] = []
    if q.exec("SELECT name FROM courses ORDER BY name"):
        while q.next():
            n = str(q.value(0)).strip()
            if n:
                out.append(n)
    return out


def _ensure_course(name: str) -> int:
    name = name.strip()
    if not name:
        raise RuntimeError("Course is required")
    q = QSqlQuery()
    q.prepare("SELECT id FROM courses WHERE lower(name)=lower(?) LIMIT 1")
    q.addBindValue(name)
    if q.exec() and q.next():
        return int(q.value(0))
    qi = QSqlQuery()
    qi.prepare("INSERT INTO courses(name, term, academic_year_start) VALUES(?, ?, ?)")
    qi.addBindValue(name)
    qi.addBindValue(_current_course_term())
    qi.addBindValue(_current_academic_year_start())
    if not qi.exec():
        raise RuntimeError(qi.lastError().text())
    # Fetch id
    q2 = QSqlQuery()
    q2.prepare("SELECT id FROM courses WHERE rowid=last_insert_rowid()")
    q2.exec()
    q2.next()
    return int(q2.value(0))


class SyllabusExtractionWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, coordinator: SyllabusImportCoordinator, path: str):
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
            self.failed.emit("Syllabus import cancelled.")
        else:
            self.finished.emit(result)


class SyllabusProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import from syllabus")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

        label = QLabel("Analyzing your syllabus with AI…")
        label.setWordWrap(True)

        sublabel = QLabel("This can take a few seconds. You’ll review everything before importing.")
        sublabel.setObjectName("Subtitle")
        sublabel.setWordWrap(True)

        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addWidget(label)
        root.addWidget(sublabel)
        root.addWidget(bar)
        root.addWidget(btns)
        self.setLayout(root)


class SyllabusImportFlow(QObject):
    imported = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: SyllabusExtractionWorker | None = None
        self._progress: SyllabusProgressDialog | None = None
        self._cancelled = False
        self._pending_paths: list[str] = []

    def _dialog_parent(self) -> QWidget | None:
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None

    def is_active(self) -> bool:
        return self._thread is not None

    def start(self) -> None:
        parent = self._dialog_parent()
        if self._thread is not None:
            QMessageBox.information(parent, "Import in progress", "A syllabus import is already running.")
            return

        if not openai_sdk_available():
            QMessageBox.critical(parent, "Import failed", "OpenAI SDK is not installed in this build.")
            return

        pick = SyllabusDropDialog(parent)
        if pick.exec() != QDialog.DialogCode.Accepted:
            return

        path = pick.path()
        if not path:
            return

        self._start_path(path)

    def start_path(self, path: str) -> None:
        parent = self._dialog_parent()
        if self._thread is not None:
            QMessageBox.information(parent, "Import in progress", "A syllabus import is already running.")
            return
        self._pending_paths.clear()
        self._start_path(path)

    def start_paths(self, paths: list[str]) -> None:
        clean_paths = [str(path).strip() for path in paths if str(path).strip()]
        if not clean_paths:
            return
        self._pending_paths.extend(clean_paths)
        if self._thread is None:
            self._start_next_pending_path()

    def _start_next_pending_path(self) -> None:
        if self._thread is not None or not self._pending_paths:
            return
        self._start_path(self._pending_paths.pop(0))

    def _start_path(self, path: str) -> None:
        parent = self._dialog_parent()
        if not path:
            return

        if self._thread is not None:
            self._pending_paths.insert(0, path)
            return

        if not openai_sdk_available():
            QMessageBox.critical(parent, "Import failed", "OpenAI SDK is not installed in this build.")
            self._pending_paths.clear()
            return

        coordinator = SyllabusImportCoordinator(
            api_key=get_effective_openai_api_key(),
            model=get_syllabus_ai_model(),
            default_status=get_default_status(),
        )

        self._cancelled = False
        self._thread = QThread(self)
        self._worker = SyllabusExtractionWorker(coordinator, path)
        self._worker.moveToThread(self._thread)

        self._progress = SyllabusProgressDialog(parent)
        self._progress.rejected.connect(self.cancel)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        self._progress.open()

    def cancel(self) -> None:
        self._cancelled = True
        self._pending_paths.clear()
        if self._worker is not None:
            self._worker.cancel()
        if self._progress is not None:
            self._progress.hide()

    def _close_progress(self) -> None:
        if self._progress is None:
            return
        self._progress.blockSignals(True)
        self._progress.close()
        self._progress.deleteLater()
        self._progress = None

    def _on_finished(self, result: object) -> None:
        parent = self._dialog_parent()
        self._close_progress()

        if self._cancelled:
            return
        if not isinstance(result, SyllabusExtractionResult):
            QMessageBox.critical(parent, "Import failed", "Syllabus extraction did not return a usable result.")
            return

        courses = _list_courses()
        dlg = SyllabusReviewDialog(courses, result, parent)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        course_name = dlg.selected_course()
        if not course_name:
            QMessageBox.critical(parent, "Import failed", "Select a course.")
            return

        try:
            rows_to_import = dlg.get_rows()
            if not rows_to_import:
                QMessageBox.critical(parent, "Import failed", "No tasks remain to import.")
                return
            added, updated = _import_syllabus_rows(
                course_name,
                rows_to_import,
                import_notes=dlg.import_notes_enabled(),
            )
        except Exception as exc:
            QMessageBox.critical(parent, "Import failed", str(exc))
            return

        QMessageBox.information(parent, "Import", f"Imported from syllabus. Added: {added}, Updated: {updated}.")
        self.imported.emit()

    def _on_failed(self, message: str) -> None:
        parent = self._dialog_parent()
        self._close_progress()
        if self._cancelled:
            return
        QMessageBox.critical(parent, "Import failed", message)

    def _on_thread_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None
        self._thread = None
        self._cancelled = False
        if self._pending_paths:
            QTimer.singleShot(0, self._start_next_pending_path)


class UpdateDownloadWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, update_info: dict):
        super().__init__()
        self._update_info = dict(update_info)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            version = str(self._update_info.get("latest_version", "")).strip() or "latest"
            zip_url = str(self._update_info.get("zip_url", "")).strip()
            expected_sha = str(self._update_info.get("sha256", "")).strip()
            if not zip_url:
                raise RuntimeError("The update manifest did not include a downloadable zip.")

            zip_path = download_update(
                zip_url,
                version,
                progress_callback=self.progress.emit,
                cancel_callback=lambda: self._cancelled,
            )
            verification = verify_update(zip_path, expected_sha)
            if not verification.ok:
                try:
                    zip_path.unlink()
                except Exception:
                    pass
                raise RuntimeError(
                    "The downloaded update failed verification and was not installed.\n\n"
                    f"Target version: {version}\n"
                    f"Expected SHA-256: {verification.expected_sha256}\n"
                    f"Actual SHA-256: {verification.actual_sha256}\n\n"
                    "The published release metadata does not match the uploaded update asset."
                )

            self.finished.emit({"zip_path": str(zip_path), "info": self._update_info})
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download update")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

        label = QLabel("Downloading the latest version…")
        label.setWordWrap(True)

        sublabel = QLabel("The app will ask before installing the update.")
        sublabel.setObjectName("Subtitle")
        sublabel.setWordWrap(True)

        self._progress_label = QLabel("Preparing download…")
        self._progress_label.setObjectName("Subtitle")
        self._progress_label.setWordWrap(True)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        self._bar.setTextVisible(False)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addWidget(label)
        root.addWidget(sublabel)
        root.addWidget(self._progress_label)
        root.addWidget(self._bar)
        root.addWidget(btns)
        self.setLayout(root)

    def set_progress(self, received_bytes: int, total_bytes: int) -> None:
        received = max(0, int(received_bytes))
        total = int(total_bytes)
        if total > 0:
            fraction = min(1.0, received / total)
            self._bar.setRange(0, 1000)
            self._bar.setValue(int(fraction * 1000))
            self._progress_label.setText(
                f"Downloaded {self._format_bytes(received)} of {self._format_bytes(total)} ({fraction * 100:.0f}%)."
            )
            return

        self._bar.setRange(0, 0)
        if received > 0:
            self._progress_label.setText(f"Downloaded {self._format_bytes(received)}.")
        else:
            self._progress_label.setText("Preparing download…")

    @staticmethod
    def _format_bytes(byte_count: int) -> str:
        units = ["bytes", "KB", "MB", "GB", "TB"]
        value = float(max(0, byte_count))
        unit = units[0]
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                break
            value /= 1024.0
        if unit == "bytes":
            return f"{int(value)} {unit}"
        return f"{value:.1f} {unit}"


@database_transactional
def _import_tasks_from_reader(reader: csv.DictReader) -> tuple[int, int]:
    dup_mode = get_import_duplicate_mode()
    added = 0
    updated = 0

    if not reader.fieldnames:
        raise RuntimeError("CSV must have headers")

    def g(row, *keys):
        for k in keys:
            if k in row and row[k] is not None:
                return str(row[k]).strip()
        return ""

    for row in reader:
        course = g(row, "course", "Course")
        item = g(row, "item", "Item", "assessment", "Assessment")
        due = g(row, "due", "Due", "due_date", "Due date")
        weight = g(row, "weight", "Weight", "weight_percent", "Weight (%)")
        grade = g(row, "grade", "Grade", "grade_percent", "Grade (%)")
        status = g(row, "status", "Status") or get_default_status()
        notes = g(row, "notes", "Notes")
        task_type = str(g(row, "type", "Type", "task_type", "Task Type")).strip().lower()
        if task_type not in {"", "ungraded", "bonus"}:
            task_type = ""
        priority = (g(row, "priority", "Priority") or "normal").strip().lower()
        if priority not in {"normal", "high"}:
            priority = "normal"

        if not course or not item:
            continue

        course_id = _ensure_course(course)

        def _pct(x: str) -> float | None:
            if not x:
                return None
            try:
                v = float(x.replace("%", "").strip())
                return v * 100.0 if 0 < v <= 1 else v
            except Exception:
                return None

        wv = _pct(weight)
        if grade:
            gv = _pct(grade)
            if gv is None:
                parsed_grade = parse_task_grade_input(grade)
                if parsed_grade is None:
                    raise RuntimeError(f"Invalid grade '{grade}' for task '{item}'.")
                gv = parsed_grade[0]
        else:
            gv = None

        if due and re.match(r"^\d{4}-\d{2}-\d{2}$", due):
            due_dt = due + " 23:59"
        else:
            due_dt = due or None

        qd = QSqlQuery()
        if due_dt is None:
            qd.prepare("SELECT id FROM tasks WHERE course_id=? AND item=? AND due_datetime IS NULL LIMIT 1")
            qd.addBindValue(course_id)
            qd.addBindValue(item)
        else:
            qd.prepare("SELECT id FROM tasks WHERE course_id=? AND item=? AND due_datetime=? LIMIT 1")
            qd.addBindValue(course_id)
            qd.addBindValue(item)
            qd.addBindValue(due_dt)
        existing_id = None
        if qd.exec() and qd.next():
            existing_id = int(qd.value(0))

        if existing_id is not None and dup_mode == "skip":
            continue

        if task_type == "ungraded":
            wv = None

        if existing_id is not None and dup_mode == "overwrite":
            qu = QSqlQuery()
            qu.prepare("UPDATE tasks SET status=?, weight=?, grade=?, notes=?, priority=?, task_type=?, ungraded=? WHERE id=?")
            qu.addBindValue(status)
            qu.addBindValue(wv)
            qu.addBindValue(gv)
            qu.addBindValue(notes)
            qu.addBindValue(priority)
            qu.addBindValue(task_type)
            qu.addBindValue(1 if task_type == "ungraded" else 0)
            qu.addBindValue(existing_id)
            if not qu.exec():
                raise RuntimeError(qu.lastError().text())
            updated += 1
            continue

        qi = QSqlQuery()
        qi.prepare(
            "INSERT INTO tasks(course_id,item,due_datetime,due_status,status,weight,grade,notes,priority,task_type,ungraded) VALUES(?,?,?,?,?,?,?,?,?,?,?)"
        )
        qi.addBindValue(course_id)
        qi.addBindValue(item)
        qi.addBindValue(due_dt)
        qi.addBindValue("exact" if due_dt else "unknown")
        qi.addBindValue(status)
        qi.addBindValue(wv)
        qi.addBindValue(gv)
        qi.addBindValue(notes)
        qi.addBindValue(priority)
        qi.addBindValue(task_type)
        qi.addBindValue(1 if task_type == "ungraded" else 0)
        if not qi.exec():
            raise RuntimeError(qi.lastError().text())
        added += 1

    return added, updated


@database_transactional
def _import_courses_from_reader(reader: csv.DictReader) -> tuple[int, int]:
    if not reader.fieldnames:
        raise RuntimeError("CSV must have headers")

    added = 0
    updated = 0

    def g(row, *keys):
        for k in keys:
            if k in row and row[k] is not None:
                return str(row[k]).strip()
        return ""

    for row in reader:
        name = g(row, "course", "Course", "name", "Name")
        term = g(row, "term", "Term").strip().lower() or _current_course_term()
        if term not in {"fall", "winter", "summer"}:
            term = _current_course_term()
        academic_year_raw = g(row, "academic_year_start", "Academic Year Start", "academic_year", "Academic Year")
        try:
            academic_year_start = int(str(academic_year_raw).split("/", 1)[0]) if academic_year_raw else _current_academic_year_start()
        except Exception:
            academic_year_start = _current_academic_year_start()
        if not name:
            continue

        q = QSqlQuery()
        q.prepare(
            """
            SELECT id, coalesce(term, ''), coalesce(academic_year_start, 0)
            FROM courses
            WHERE lower(name)=lower(?)
            LIMIT 1
            """
        )
        q.addBindValue(name)
        existing_id = None
        existing_term = ""
        existing_year = 0
        if q.exec() and q.next():
            existing_id = int(q.value(0))
            existing_term = str(q.value(1) or "")
            existing_year = int(q.value(2) or 0)

        if existing_id is None:
            qi = QSqlQuery()
            qi.prepare("INSERT INTO courses(name, term, academic_year_start) VALUES(?, ?, ?)")
            qi.addBindValue(name)
            qi.addBindValue(term)
            qi.addBindValue(academic_year_start)
            if not qi.exec():
                raise RuntimeError(qi.lastError().text())
            added += 1
            continue

        if term != existing_term or academic_year_start != existing_year:
            qu = QSqlQuery()
            qu.prepare("UPDATE courses SET term=?, academic_year_start=? WHERE id=?")
            qu.addBindValue(term)
            qu.addBindValue(academic_year_start)
            qu.addBindValue(existing_id)
            if not qu.exec():
                raise RuntimeError(qu.lastError().text())
            updated += 1

    return added, updated


# --- Import single-file backup JSON containing both courses and tasks ---
@database_transactional
def _import_backup_database_rows(data: dict) -> tuple[int, int, int, int]:
    if not isinstance(data, dict):
        raise RuntimeError("Backup file is invalid")

    courses = data.get("courses", [])
    tasks = data.get("tasks", [])
    previous_courses = data.get("previous_courses", [])
    if not isinstance(courses, list) or not isinstance(tasks, list):
        raise RuntimeError("Backup file is missing courses/tasks lists")
    if previous_courses is not None and not isinstance(previous_courses, list):
        raise RuntimeError("Backup file has an invalid previous_courses list")

    settings_rows = data.get("settings", {})
    if isinstance(settings_rows, dict):
        for key, value in settings_rows.items():
            clean_key = str(key).strip()
            if clean_key and clean_key not in SENSITIVE_SETTING_KEYS:
                _set(clean_key, "" if value is None else str(value))

    courses_added = 0
    courses_skipped = 0
    tasks_added = 0
    tasks_skipped = 0

    # Import courses first. Existing course names are skipped.
    for row in courses:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        term = str(row.get("term", "")).strip().lower() or _current_course_term()
        if term not in {"fall", "winter", "summer"}:
            term = _current_course_term()
        try:
            academic_year_start = int(row.get("academic_year_start"))
        except Exception:
            academic_year_start = _current_academic_year_start()
        if not name:
            continue

        q = QSqlQuery()
        q.prepare("SELECT id FROM courses WHERE lower(name)=lower(?) LIMIT 1")
        q.addBindValue(name)
        exists = False
        if q.exec() and q.next():
            exists = True

        if exists:
            courses_skipped += 1
            continue

        qi = QSqlQuery()
        qi.prepare("INSERT INTO courses(name, term, academic_year_start) VALUES(?, ?, ?)")
        qi.addBindValue(name)
        qi.addBindValue(term)
        qi.addBindValue(academic_year_start)
        if not qi.exec():
            raise RuntimeError(qi.lastError().text())
        courses_added += 1

    # Import tasks. Existing (course,item,due) rows are skipped.
    for row in tasks:
        if not isinstance(row, dict):
            continue

        course = str(row.get("course", "")).strip()
        item = str(row.get("item", "")).strip()
        component = str(row.get("component", "")).strip()
        due_text = str(row.get("due_datetime", "")).strip()
        due_dt = due_text or None
        status = normalize_status(row.get("status", get_default_status()) or get_default_status())
        notes = str(row.get("notes", "")).strip()
        task_type = str(row.get("task_type", "") or row.get("type", "")).strip().lower()
        if task_type not in {"", "ungraded", "bonus"}:
            task_type = ""
        priority = str(row.get("priority", "normal")).strip().lower() or "normal"
        if priority not in {"normal", "high"}:
            priority = "normal"

        weight = row.get("weight", None)
        grade = row.get("grade", None)
        ungraded = 1 if coerce_bool(row.get("ungraded", False)) else 0
        if ungraded and not task_type:
            task_type = "ungraded"
        if task_type == "ungraded":
            ungraded = 1
        due_status = normalize_due_status(row.get("due_status", ""), has_due=due_dt is not None)
        weight_raw_value = row.get("weight_raw_value", None)
        weight_raw_unit = str(row.get("weight_raw_unit", "")).strip()
        weight_source = normalize_weight_source(row.get("weight_source", ""))
        syllabus_metadata = str(row.get("syllabus_metadata", "") or "").strip()

        if not course or not item:
            continue

        course_id = _ensure_course(course)

        qd = QSqlQuery()
        if due_dt is None:
            qd.prepare(
                "SELECT id FROM tasks WHERE course_id=? AND item=? AND ifnull(component,'')=? AND due_datetime IS NULL LIMIT 1"
            )
            qd.addBindValue(course_id)
            qd.addBindValue(item)
            qd.addBindValue(component)
        else:
            qd.prepare(
                "SELECT id FROM tasks WHERE course_id=? AND item=? AND ifnull(component,'')=? AND due_datetime=? LIMIT 1"
            )
            qd.addBindValue(course_id)
            qd.addBindValue(item)
            qd.addBindValue(component)
            qd.addBindValue(due_dt)
        exists = False
        if qd.exec() and qd.next():
            exists = True

        if exists:
            tasks_skipped += 1
            continue

        qi = QSqlQuery()
        qi.prepare(
            "INSERT INTO tasks(course_id,item,component,due_datetime,status,weight,grade,ungraded,notes,priority,due_status,weight_raw_value,weight_raw_unit,weight_source,syllabus_metadata,task_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        qi.addBindValue(course_id)
        qi.addBindValue(item)
        qi.addBindValue(component)
        qi.addBindValue(due_dt)
        qi.addBindValue(status)
        qi.addBindValue(weight)
        qi.addBindValue(grade)
        qi.addBindValue(ungraded)
        qi.addBindValue(notes)
        qi.addBindValue(priority)
        qi.addBindValue(due_status)
        qi.addBindValue(weight_raw_value)
        qi.addBindValue(weight_raw_unit)
        qi.addBindValue(weight_source)
        qi.addBindValue(syllabus_metadata)
        qi.addBindValue(task_type)
        if not qi.exec():
            raise RuntimeError(qi.lastError().text())
        tasks_added += 1

    # Import previous courses. Existing (course, term, academic_year_start) rows are skipped.
    for row in previous_courses or []:
        if not isinstance(row, dict):
            continue

        course_name = str(row.get("course_name", "") or row.get("name", "")).strip()
        term = str(row.get("term", "")).strip().lower()
        if term not in {"fall", "winter", "summer"}:
            continue
        final_letter = str(row.get("final_letter", "") or "").strip()
        try:
            academic_year_start = int(row.get("academic_year_start"))
            credit_hours = float(row.get("credit_hours"))
        except Exception:
            continue
        try:
            final_percent = float(row.get("final_percent"))
        except Exception:
            final_percent = -1.0

        if not course_name or credit_hours <= 0:
            continue
        if final_percent < 0 and not final_letter:
            continue

        q_prev = QSqlQuery()
        q_prev.prepare(
            """
            SELECT id
            FROM previous_courses
            WHERE lower(course_name)=lower(?)
              AND lower(term)=lower(?)
              AND academic_year_start=?
            LIMIT 1
            """
        )
        q_prev.addBindValue(course_name)
        q_prev.addBindValue(term)
        q_prev.addBindValue(academic_year_start)
        exists = False
        if q_prev.exec() and q_prev.next():
            exists = True

        if exists:
            continue

        qi_prev = QSqlQuery()
        qi_prev.prepare(
            """
            INSERT INTO previous_courses(course_name, final_percent, final_letter, credit_hours, term, academic_year_start)
            VALUES(?, ?, ?, ?, ?, ?)
            """
        )
        qi_prev.addBindValue(course_name)
        qi_prev.addBindValue(final_percent)
        qi_prev.addBindValue(final_letter)
        qi_prev.addBindValue(credit_hours)
        qi_prev.addBindValue(term)
        qi_prev.addBindValue(academic_year_start)
        if not qi_prev.exec():
            raise RuntimeError(qi_prev.lastError().text())

    return courses_added, courses_skipped, tasks_added, tasks_skipped


def _import_backup_json(path: str) -> tuple[int, int, int, int]:
    """Restore database rows atomically, then apply non-database preferences."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise RuntimeError("Backup file is invalid")

    result = _import_backup_database_rows(data)

    qsettings_rows = data.get("app_qsettings", {})
    if isinstance(qsettings_rows, dict):
        settings = app_qsettings()
        for key, value in qsettings_rows.items():
            clean_key = str(key).strip()
            if clean_key:
                settings.setValue(clean_key, value)
        settings.sync()
    return result


def import_from_csv(parent: QWidget) -> tuple[int, int, int, int] | None:
    path, _ = QFileDialog.getOpenFileName(parent, "Restore from backup", "", "Backup/CSV (*.json *.csv)")
    if not path:
        return None

    if path.lower().endswith(".json"):
        return _import_backup_json(path)

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise RuntimeError("CSV must have headers")
        headers = {str(h).strip().lower() for h in reader.fieldnames if h}

        if {"item", "assessment"} & headers:
            tasks_added, tasks_updated = _import_tasks_from_reader(reader)
            return 0, 0, tasks_added, tasks_updated

        if {"course", "name"} & headers:
            courses_added, courses_updated = _import_courses_from_reader(reader)
            return courses_added, courses_updated, 0, 0

    raise RuntimeError("CSV must contain recognizable course or task headers.")


def import_tasks_from_csv(parent: QWidget) -> tuple[int, int]:
    result = import_from_csv(parent)
    if result is None:
        return 0, 0
    _, _, added, updated = result
    return added, updated


def _normalize_due_for_task_import(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    raw = raw.replace("T", " ")
    date_part = normalize_due_date(raw.split(" ", 1)[0])
    if " " not in raw:
        return f"{date_part} 23:59" if date_part else raw
    time_part = normalize_due_time(raw.split(" ", 1)[1])
    if date_part and time_part:
        return f"{date_part} {time_part}"
    if date_part:
        return f"{date_part} 23:59"
    return raw


def _to_weight_value(value: str) -> float | None:
    weight_text = normalize_percent_text(value)
    if not weight_text:
        return None
    try:
        return float(weight_text)
    except Exception:
        return None


def _build_syllabus_metadata(row: dict) -> str:
    payload = {
        "parent_category": str(row.get("parent_category", "")).strip(),
        "parent_total_weight_value": str(row.get("parent_total_weight_value", "")).strip(),
        "parent_total_weight_unit": str(row.get("parent_total_weight_unit", "")).strip(),
        "child_index": int(row.get("child_index", 0) or 0),
        "child_count": int(row.get("child_count", 0) or 0),
        "policy_notes": str(row.get("policy_notes", "")).strip(),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@database_transactional
def _import_syllabus_rows(course_name: str, rows: list[dict], *, import_notes: bool = True) -> tuple[int, int]:
    course_id = _ensure_course(course_name)
    dup_mode = get_import_duplicate_mode()

    added = 0
    updated = 0
    for r in rows:
        item = r.get("item", "").strip()
        component = str(r.get("component", "")).strip()
        due = r.get("due", "").strip()
        weight = normalize_percent_text(str(r.get("weight", "")).strip())
        due_status = normalize_due_status(r.get("due_status", ""), has_due=bool(due))
        status = normalize_status(r.get("status", "") or get_default_status())
        raw_weight_value, raw_weight_unit = parse_raw_weight_display(str(r.get("raw_weight", "")).strip())
        weight_source = normalize_weight_source(r.get("weight_source", ""))
        ungraded = coerce_bool(r.get("ungraded", False))
        notes = str(r.get("notes", "")).strip() if import_notes else ""
        syllabus_metadata = _build_syllabus_metadata(r)
        task_type = "ungraded" if ungraded else ""

        if not item:
            continue

        due_dt = _normalize_due_for_task_import(due)
        wv = None if ungraded else _to_weight_value(weight)

        qd = QSqlQuery()
        if due_dt is None:
            qd.prepare("SELECT id FROM tasks WHERE course_id=? AND item=? AND ifnull(component,'')=? AND due_datetime IS NULL LIMIT 1")
            qd.addBindValue(course_id)
            qd.addBindValue(item)
            qd.addBindValue(component)
        else:
            qd.prepare("SELECT id FROM tasks WHERE course_id=? AND item=? AND ifnull(component,'')=? AND due_datetime=? LIMIT 1")
            qd.addBindValue(course_id)
            qd.addBindValue(item)
            qd.addBindValue(component)
            qd.addBindValue(due_dt)
        existing_id = None
        if qd.exec() and qd.next():
            existing_id = int(qd.value(0))

        if existing_id is not None and dup_mode == "skip":
            continue

        if existing_id is not None and dup_mode == "overwrite":
            qu = QSqlQuery()
            if import_notes:
                qu.prepare(
                    "UPDATE tasks SET component=?, weight=?, ungraded=?, notes=?, due_status=?, weight_raw_value=?, weight_raw_unit=?, weight_source=?, syllabus_metadata=?, task_type=? WHERE id=?"
                )
            else:
                qu.prepare(
                    "UPDATE tasks SET component=?, weight=?, ungraded=?, due_status=?, weight_raw_value=?, weight_raw_unit=?, weight_source=?, syllabus_metadata=?, task_type=? WHERE id=?"
                )
            qu.addBindValue(component)
            qu.addBindValue(wv)
            qu.addBindValue(1 if ungraded else 0)
            if import_notes:
                qu.addBindValue(notes)
            qu.addBindValue(due_status)
            qu.addBindValue(_to_weight_value(raw_weight_value))
            qu.addBindValue(raw_weight_unit if raw_weight_value else "")
            qu.addBindValue(weight_source)
            qu.addBindValue(syllabus_metadata)
            qu.addBindValue(task_type)
            qu.addBindValue(existing_id)
            if not qu.exec():
                raise RuntimeError(qu.lastError().text())
            updated += 1
            continue

        qi = QSqlQuery()
        qi.prepare(
            "INSERT INTO tasks(course_id,item,component,due_datetime,status,weight,ungraded,notes,priority,due_status,weight_raw_value,weight_raw_unit,weight_source,syllabus_metadata,task_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        qi.addBindValue(course_id)
        qi.addBindValue(item)
        qi.addBindValue(component)
        qi.addBindValue(due_dt)
        qi.addBindValue(status)
        qi.addBindValue(wv)
        qi.addBindValue(1 if ungraded else 0)
        qi.addBindValue(notes)
        qi.addBindValue("normal")
        qi.addBindValue(due_status)
        qi.addBindValue(_to_weight_value(raw_weight_value))
        qi.addBindValue(raw_weight_unit if raw_weight_value else "")
        qi.addBindValue(weight_source)
        qi.addBindValue(syllabus_metadata)
        qi.addBindValue(task_type)
        if not qi.exec():
            raise RuntimeError(qi.lastError().text())
        added += 1

    return added, updated


# -----------------------------
# Settings UI
# -----------------------------


class UserManagerDialog(QDialog):
    users_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Modify users")
        self.setModal(True)
        self.setMinimumWidth(420)

        self.user_list = QListWidget()
        self.user_list.itemSelectionChanged.connect(self._update_delete_state)

        self.btn_add = QPushButton("Add user")
        self.btn_add.setObjectName("SecondaryButton")
        self.btn_delete = QPushButton("Delete user")
        self.btn_delete.setObjectName("DangerButton")
        self.btn_close = QPushButton("Close")
        self.btn_close.setObjectName("SecondaryButton")

        self.btn_add.clicked.connect(self._add_user)
        self.btn_delete.clicked.connect(self._delete_user)
        self.btn_close.clicked.connect(self.accept)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(self.btn_add)
        actions.addWidget(self.btn_delete)
        actions.addStretch(1)
        actions.addWidget(self.btn_close)

        note = QLabel("Add separate users or delete users other than the one currently open.")
        note.setObjectName("Subtitle")
        note.setWordWrap(True)

        root = QVBoxLayout()
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        root.addWidget(note)
        root.addWidget(self.user_list)
        root.addLayout(actions)
        self.setLayout(root)

        self._refresh()

    def _refresh(self) -> None:
        active = current_profile()
        active_id = active.id if active is not None else ""
        self.user_list.clear()
        for profile in list_profiles():
            label = profile.name
            if profile.id == active_id:
                label = f"{label} (current)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.user_list.addItem(item)
        if self.user_list.count() > 0:
            self.user_list.setCurrentRow(0)
        self._update_delete_state()

    def _selected_profile_id(self) -> str:
        item = self.user_list.currentItem()
        return "" if item is None else str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _update_delete_state(self) -> None:
        active = current_profile()
        selected_id = self._selected_profile_id()
        self.btn_delete.setEnabled(
            bool(selected_id)
            and active is not None
            and selected_id != active.id
            and len(list_profiles()) > 1
        )

    def _add_user(self) -> None:
        name, ok = QInputDialog.getText(self, "Add user", "Name")
        if not ok:
            return
        clean_name = name.strip()
        if not clean_name:
            QMessageBox.warning(self, "Add user", "Enter a name for the new user.")
            return
        create_profile(clean_name, set_current=False)
        self._refresh()
        self.users_changed.emit()

    def _delete_user(self) -> None:
        profile_id = self._selected_profile_id()
        profile = next((p for p in list_profiles() if p.id == profile_id), None)
        if profile is None:
            return

        res = QMessageBox.question(
            self,
            "Delete user",
            f"Delete {profile.name}? Their courses, tasks, and settings will be removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if res != QMessageBox.StandardButton.Yes:
            return

        try:
            delete_profile(profile.id)
        except Exception as exc:
            QMessageBox.critical(self, "Delete user failed", str(exc))
            return

        self._refresh()
        self.users_changed.emit()


class SettingsPage(QWidget):
    def on_check_for_updates(self) -> None:
        try:
            info = check_for_updates()
        except Exception as exc:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Check for updates")
            box.setText(str(exc))
            open_btn = box.addButton("Open releases page", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is open_btn:
                try:
                    open_latest_releases_page()
                except Exception as open_exc:
                    QMessageBox.warning(self, "Open releases page", str(open_exc))
            return

        current_version = str(info.get("current_version", "")).strip() or "Unknown"
        latest_version = str(info.get("latest_version", "")).strip() or "Unknown"
        notes = info.get("release_notes", []) or []
        if not isinstance(notes, list):
            notes = [str(notes)]

        notes_text = "\n".join(f"• {str(n)}" for n in notes) if notes else "No release notes provided."

        if not bool(info.get("update_available", False)):
            QMessageBox.information(
                self,
                "Check for updates",
                f"You’re up to date.\n\nCurrent version: {current_version}\nLatest version: {latest_version}",
            )
            return

        installed_app = current_app_bundle_path()
        if installed_app is None:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("Automatic updates unavailable")
            box.setText("Automatic updates are only available from the installed app bundle.")
            box.setInformativeText("This copy is not running from a packaged macOS app, so the release page will open instead.")
            open_btn = box.addButton("Open release page", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is open_btn:
                zip_url = str(info.get("zip_url", "")).strip()
                if zip_url:
                    open_release_page(zip_url)
            return

        if not is_app_bundle_in_applications(installed_app):
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("Move app to Applications")
            box.setText("Automatic updates require the app to be installed in /Applications.")
            box.setInformativeText(
                f"Current app location:\n{installed_app}\n\nMove the app to /Applications and open it from there to use automatic updates."
            )
            open_btn = box.addButton("Open release page", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is open_btn:
                zip_url = str(info.get("zip_url", "")).strip()
                if zip_url:
                    open_release_page(zip_url)
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Update available")
        box.setText(f"Version {latest_version} is available.")
        box.setInformativeText(
            f"Current version: {current_version}\nLatest version: {latest_version}\n\nRelease notes:\n{notes_text}"
        )
        download_btn = box.addButton("Download update", QMessageBox.ButtonRole.AcceptRole)
        open_btn = box.addButton("Open release page", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()

        if box.clickedButton() is download_btn:
            self.start_update_download(info, installed_app)
        elif box.clickedButton() is open_btn:
            zip_url = str(info.get("zip_url", "")).strip()
            if zip_url:
                open_release_page(zip_url)
    changed = Signal()
    user_switch_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._syllabus_import_flow = SyllabusImportFlow(self)
        self._syllabus_import_flow.imported.connect(self.changed.emit)
        self._update_thread: QThread | None = None
        self._update_worker: UpdateDownloadWorker | None = None
        self._update_progress: UpdateProgressDialog | None = None
        self._update_cancelled = False
        self._pending_install_info: dict | None = None
        self._loading_settings = False
        self._settings_autosave_timer = QTimer(self)
        self._settings_autosave_timer.setSingleShot(True)
        self._settings_autosave_timer.setInterval(180)
        self._settings_autosave_timer.timeout.connect(self.save)

        title = QLabel("Settings")
        title.setObjectName("Title")
        subtitle = QLabel("User defaults and overview behaviour. Changes save automatically.")
        subtitle.setObjectName("Subtitle")
        version_label = make_version_label()

        self.cmb_current_user = QComboBox()
        self.cmb_current_user.setMinimumWidth(150)
        self.cmb_current_user.currentIndexChanged.connect(self._on_current_user_changed)
        self.btn_modify_users = QPushButton("Modify users...")
        self.btn_modify_users.setObjectName("SecondaryButton")
        self.btn_modify_users.clicked.connect(self.manage_users)

        user_row = QHBoxLayout()
        user_row.setContentsMargins(0, 0, 0, 0)
        user_row.setSpacing(8)
        user_row.addWidget(self.cmb_current_user, 1)
        user_row.addWidget(self.btn_modify_users)
        user_row_w = QWidget()
        user_row_w.setLayout(user_row)

        self.cmb_default_status = QComboBox()
        self.cmb_default_status.addItems(["not started", "in progress", "submitted", "graded"])

        self.spin_upcoming_days = QSpinBox()
        self.spin_upcoming_days.setRange(1, 60)
        self.spin_upcoming_days.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        self.spin_due_soon_days = QSpinBox()
        self.spin_due_soon_days.setRange(1, 30)
        self.spin_due_soon_days.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        self.spin_high_priority_due_soon_days = QSpinBox()
        self.spin_high_priority_due_soon_days.setRange(0, 30)
        self.spin_high_priority_due_soon_days.setSpecialValueText("Off")
        self.spin_high_priority_due_soon_days.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        self.chk_high_weight_overview = SwitchCheckBox("Flag high-weight items in Overview")
        self.spin_high_weight_threshold = QDoubleSpinBox()
        self.spin_high_weight_threshold.setRange(0.0, 100.0)
        self.spin_high_weight_threshold.setDecimals(1)
        self.spin_high_weight_threshold.setSingleStep(0.5)
        self.spin_high_weight_threshold.setSuffix("%")
        self.spin_high_weight_threshold.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        self.spin_pin_high_priority_days = QSpinBox()
        self.spin_pin_high_priority_days.setRange(0, 60)
        self.spin_pin_high_priority_days.setSpecialValueText("Off")
        self.spin_pin_high_priority_days.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.chk_pin_high_priority = SwitchCheckBox("Pin high-priority items in Overview")

        self.chk_hide_completed = SwitchCheckBox("Hide submitted/graded items in Overview")
        self.chk_pin_overdue = SwitchCheckBox("Pin overdue items to top in Overview")
        self.chk_overview_reminders = SwitchCheckBox("Show overview reminders as desktop notifications")
        self.chk_confirm_delete = SwitchCheckBox("Confirm before deleting rows")

        # --- New controls ---
        self.chk_confirm_submit_overview = SwitchCheckBox("Confirm before marking submitted in Overview")

        self.cmb_import_dup = QComboBox()
        self.cmb_import_dup.addItem("skip duplicates", "skip")
        self.cmb_import_dup.addItem("allow duplicates", "allow")
        self.cmb_import_dup.addItem("overwrite duplicates", "overwrite")

        self.lbl_api_key_status = QLabel()
        self.lbl_api_key_status.setObjectName("Subtitle")
        self.btn_set_api_key = QPushButton("Set API key")
        self.btn_set_api_key.setObjectName("SecondaryButton")
        self.btn_set_api_key.clicked.connect(self.edit_api_key)
        api_key_row = QHBoxLayout()
        api_key_row.setContentsMargins(0, 0, 0, 0)
        api_key_row.setSpacing(8)
        api_key_row.addWidget(self.lbl_api_key_status)
        api_key_row.addStretch(1)
        api_key_row.addWidget(self.btn_set_api_key)
        api_key_row_w = QWidget()
        api_key_row_w.setLayout(api_key_row)

        self.cmb_grade_calc = QComboBox()
        self.cmb_grade_calc.addItem("graded only", "graded_only")
        self.cmb_grade_calc.addItem("include submitted as 0%", "submitted_as_zero")

        self.chk_compact_rows = SwitchCheckBox("Compact row height")

        self.ed_backup_folder = QLineEdit()
        self.ed_backup_folder.setReadOnly(True)
        self.ed_backup_folder.setText("Not set")
        self.btn_pick_backup_folder = QPushButton("Choose folder…")
        self.btn_pick_backup_folder.setObjectName("SecondaryButton")
        def _pick_folder():
            path = QFileDialog.getExistingDirectory(self, "Choose backup folder")
            if path:
                self.ed_backup_folder.setText(path)
        self.btn_pick_backup_folder.clicked.connect(_pick_folder)

        backup_row = QHBoxLayout()
        backup_row.setContentsMargins(0, 0, 0, 0)
        backup_row.setSpacing(8)
        backup_row.addWidget(self.ed_backup_folder, 1)
        backup_row.addWidget(self.btn_pick_backup_folder)
        backup_row_w = QWidget()
        backup_row_w.setLayout(backup_row)

        self.lbl_data_folder = QLineEdit()
        self.lbl_data_folder.setReadOnly(True)
        self.lbl_data_folder.setText(str(_current_user_data_folder()))
        self.btn_reveal_data_folder = QPushButton("Reveal in Finder")
        self.btn_reveal_data_folder.setObjectName("SecondaryButton")

        def _reveal_data_folder():
            data_folder = _current_user_data_folder()
            data_folder.mkdir(parents=True, exist_ok=True)
            subprocess.run(["open", str(data_folder)], check=False)

        self.btn_reveal_data_folder.clicked.connect(_reveal_data_folder)

        data_folder_row = QHBoxLayout()
        data_folder_row.setContentsMargins(0, 0, 0, 0)
        data_folder_row.setSpacing(8)
        data_folder_row.addWidget(self.lbl_data_folder, 1)
        data_folder_row.addWidget(self.btn_reveal_data_folder)
        data_folder_row_w = QWidget()
        data_folder_row_w.setLayout(data_folder_row)

        self.cmb_font_size = QComboBox()
        self.cmb_font_size.addItems(["small", "normal", "large"])

        self.cmb_grade_display = QComboBox()
        self.cmb_grade_display.addItems(["percent", "letter", "gpa4.0", "gpa4.3"])

        self.btn_edit_grade_scale = QPushButton("Edit scale…")
        self.btn_edit_grade_scale.setObjectName("SecondaryButton")
        self.btn_edit_grade_scale.clicked.connect(self.edit_grade_scale)

        self.cmb_gpa_view = QComboBox()
        self.cmb_gpa_view.addItem("Cumulative", "cumulative")
        self.cmb_gpa_view.addItem("Last X Credit Hours", "last_x")
        self.cmb_gpa_view.addItem("Both", "both")

        self.spin_gpa_last_x_credit_hours = QSpinBox()
        self.spin_gpa_last_x_credit_hours.setRange(1, 300)
        self.spin_gpa_last_x_credit_hours.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.lbl_gpa_last_x_hours = QLabel("hours")

        grade_row = QHBoxLayout()
        grade_row.setContentsMargins(0, 0, 0, 0)
        grade_row.setSpacing(8)
        grade_row.addWidget(self.cmb_grade_display, 1)
        grade_row.addWidget(self.btn_edit_grade_scale)
        grade_row_widget = QWidget()
        grade_row_widget.setLayout(grade_row)

        gpa_last_x_row = QHBoxLayout()
        gpa_last_x_row.setContentsMargins(0, 0, 0, 0)
        gpa_last_x_row.setSpacing(8)
        gpa_last_x_row.addWidget(self.spin_gpa_last_x_credit_hours)
        gpa_last_x_row.addWidget(self.lbl_gpa_last_x_hours)
        gpa_last_x_row.addStretch(1)
        gpa_last_x_row_widget = QWidget()
        gpa_last_x_row_widget.setLayout(gpa_last_x_row)

        def _make_setting_row(
            label_text: str,
            control: QWidget,
            *,
            label_width: int,
            control_width: int | None = None,
            expand: bool = False,
        ) -> QWidget:
            label = QLabel(label_text)
            label.setObjectName("SettingLabel")
            label.setWordWrap(True)
            label.setFixedWidth(label_width)

            control_box = QWidget()
            control_layout = QHBoxLayout()
            control_layout.setContentsMargins(0, 0, 0, 0)
            control_layout.setSpacing(0)
            control_layout.addWidget(control)
            if not expand:
                control_layout.addStretch(1)
            control_box.setLayout(control_layout)
            if control_width is not None:
                control_box.setMaximumWidth(control_width)

            row = QWidget()
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            layout.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
            if expand:
                layout.addWidget(control_box, 1)
            else:
                layout.addWidget(control_box, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                layout.addStretch(1)
            row.setLayout(layout)
            return row

        def _make_stacked_setting_row(label_text: str, control: QWidget) -> QWidget:
            label = QLabel(label_text)
            label.setObjectName("SettingLabel")
            label.setWordWrap(True)

            control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            row = QWidget()
            layout = QVBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            layout.addWidget(label)
            layout.addWidget(control)
            row.setLayout(layout)
            return row

        def _make_switch_row(control: QWidget) -> QWidget:
            row = QWidget()
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            layout.addWidget(control, 1)
            row.setLayout(layout)
            return row

        self._settings_switch_grids: list[QGridLayout] = []

        def _make_switch_grid_row(*controls: QWidget) -> QWidget:
            row = QWidget()
            layout = QGridLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setHorizontalSpacing(12)
            layout.setVerticalSpacing(6)
            for idx, control in enumerate(controls):
                layout.addWidget(control, idx // 2, idx % 2)
            layout.setColumnStretch(0, 1)
            layout.setColumnStretch(1, 1)
            self._settings_switch_grids.append(layout)
            row.setLayout(layout)
            return row

        def _make_switch_with_field_row(
            control: QWidget,
            field_label_text: str,
            field_control: QWidget,
            *,
            trailing_text: str = "",
            field_width: int = 215,
        ) -> QWidget:
            field_label = QLabel(field_label_text)
            field_label.setObjectName("InlineLabel")
            trailing_label = QLabel(trailing_text)
            trailing_label.setObjectName("InlineLabel")
            trailing_label.setVisible(bool(trailing_text))

            field_box = QWidget()
            field_layout = QHBoxLayout()
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(8)
            field_layout.addWidget(field_label)
            field_layout.addWidget(field_control, 1)
            if trailing_text:
                field_layout.addWidget(trailing_label)
            field_box.setLayout(field_layout)
            field_box.setFixedWidth(field_width)

            row = QWidget()
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            layout.addWidget(control, 1)
            layout.addWidget(field_box, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.setLayout(layout)
            return row

        def _build_section(title_text: str, rows: list[QWidget]) -> QWidget:
            section = QFrame()
            section.setObjectName("SettingsSection")
            layout = QVBoxLayout()
            layout.setContentsMargins(14, 13, 14, 14)
            layout.setSpacing(9)
            heading = QLabel(title_text)
            heading.setObjectName("Section")
            layout.addWidget(heading)
            for row in rows:
                layout.addWidget(row)
            section.setLayout(layout)
            return section

        import_row = QHBoxLayout()
        import_row.setContentsMargins(0, 0, 0, 0)
        import_row.setSpacing(8)
        btn_csv = QPushButton("Import from backup")
        btn_csv.setObjectName("SecondaryButton")
        import_row.addWidget(btn_csv)
        import_row_w = QWidget()
        import_row_w.setLayout(import_row)

        def _do_csv():
            try:
                result = import_from_csv(self)
                if result is None:
                    return
                courses_added, courses_updated, tasks_added, tasks_updated = result
                parts: list[str] = []
                if courses_added or courses_updated:
                    parts.append(f"Courses added: {courses_added}, skipped: {courses_updated}.")
                if tasks_added or tasks_updated:
                    parts.append(f"Tasks added: {tasks_added}, skipped: {tasks_updated}.")
                if not parts:
                    parts.append("No new rows were imported.")
                QMessageBox.information(self, "Import", " ".join(parts))
                self.load()
                self.changed.emit()
            except Exception as e:
                QMessageBox.critical(self, "Import failed", str(e))

        btn_csv.clicked.connect(_do_csv)

        self.chk_priority = SwitchCheckBox("Enable priority ranking")
        self.chk_in_progress = SwitchCheckBox("Enable in progress status")
        self.chk_priority.toggled.connect(self._update_overview_priority_controls)
        self.chk_pin_high_priority.toggled.connect(self._update_overview_priority_controls)
        self.chk_high_weight_overview.toggled.connect(self._update_overview_priority_controls)

        self.btn_backup_now = QPushButton("Backup now")
        self.btn_backup_now.setObjectName("SecondaryButton")

        def _backup_now():
            ok, msg = run_backup_now()
            QMessageBox.information(self, "Backup" if ok else "Backup not run", msg)

        self.btn_backup_now.clicked.connect(_backup_now)
        self.btn_reset_all_data = QPushButton("Reset all data")
        self.btn_reset_all_data.setObjectName("DangerButton")
        self.btn_reset_all_data.clicked.connect(self.reset_all_data)

        backup_actions = QHBoxLayout()
        backup_actions.setContentsMargins(0, 0, 0, 0)
        backup_actions.setSpacing(8)
        backup_actions.addWidget(self.btn_backup_now)
        backup_actions.addWidget(self.btn_reset_all_data)
        backup_actions_w = QWidget()
        backup_actions_w.setLayout(backup_actions)

        app_actions = QHBoxLayout()
        app_actions.setContentsMargins(0, 0, 0, 0)
        app_actions.setSpacing(8)
        self.btn_check_updates = QPushButton("Check for updates")
        self.btn_check_updates.setObjectName("SecondaryButton")
        self.btn_feedback = QPushButton("Report a bug/Provide feedback")
        self.btn_feedback.setObjectName("SecondaryButton")
        app_actions.addWidget(self.btn_check_updates)
        app_actions.addWidget(self.btn_feedback)
        app_actions_w = QWidget()
        app_actions_w.setLayout(app_actions)
        self.btn_check_updates.clicked.connect(self.on_check_for_updates)
        self.btn_feedback.clicked.connect(
            lambda: webbrowser.open(
                "https://docs.google.com/forms/d/e/1FAIpQLSfLNvVotsWdGXiWJQYDe1YoeRpQahFO5FCWEsjEW5SajTb22Q/viewform?usp=header"
            )
        )

        self.footer_note = QLabel("Syllora · Built by Arnor Erlendsson")
        self.footer_note.setObjectName("FooterNote")
        self.footer_note.setWordWrap(True)

        left_label_width = 155
        right_label_width = 135
        left_input_width = 220
        left_compound_width = 300
        right_input_width = 210
        right_compound_width = 300
        compact_input_width = 200

        user_section = _build_section(
            "USER",
            [
                _make_setting_row(
                    "Current user",
                    user_row_w,
                    label_width=left_label_width,
                    control_width=left_compound_width,
                ),
            ],
        )
        defaults_section = _build_section(
            "DEFAULTS",
            [
                _make_setting_row(
                    "Default status for new tasks",
                    self.cmb_default_status,
                    label_width=left_label_width,
                    control_width=left_input_width,
                ),
                _make_setting_row(
                    "Import duplicates",
                    self.cmb_import_dup,
                    label_width=left_label_width,
                    control_width=left_input_width,
                ),
                _make_setting_row(
                    "OpenAI API key",
                    api_key_row_w,
                    label_width=left_label_width,
                    control_width=left_compound_width,
                ),
                _make_stacked_setting_row(
                    "Tools",
                    import_row_w,
                ),
            ],
        )
        overview_section = _build_section(
            "OVERVIEW",
            [
                _make_setting_row(
                    "Overview window (days)",
                    self.spin_upcoming_days,
                    label_width=left_label_width,
                    control_width=left_input_width,
                ),
                _make_setting_row(
                    "Due soon threshold (days)",
                    self.spin_due_soon_days,
                    label_width=left_label_width,
                    control_width=left_input_width,
                ),
                _make_setting_row(
                    "High-priority due-soon threshold (days)",
                    self.spin_high_priority_due_soon_days,
                    label_width=left_label_width,
                    control_width=left_input_width,
                ),
                _make_switch_grid_row(self.chk_hide_completed, self.chk_pin_overdue),
                _make_switch_grid_row(self.chk_overview_reminders, self.chk_confirm_submit_overview),
                _make_setting_row(
                    "Grade calculation",
                    self.cmb_grade_calc,
                    label_width=left_label_width,
                    control_width=left_input_width,
                ),
            ],
        )
        grades_section = _build_section(
            "GRADES",
            [
                _make_setting_row(
                    "Overview grade display",
                    grade_row_widget,
                    label_width=left_label_width,
                    control_width=left_compound_width,
                ),
                _make_setting_row(
                    "Courses GPA view",
                    self.cmb_gpa_view,
                    label_width=left_label_width,
                    control_width=left_input_width,
                ),
                _make_setting_row(
                    "Last-X credit hours",
                    gpa_last_x_row_widget,
                    label_width=left_label_width,
                    control_width=compact_input_width,
                ),
            ],
        )

        display_section = _build_section(
            "DISPLAY",
            [
                _make_setting_row(
                    "Font size",
                    self.cmb_font_size,
                    label_width=right_label_width,
                    control_width=right_input_width,
                ),
                _make_switch_grid_row(
                    self.chk_confirm_delete,
                    self.chk_compact_rows,
                    self.chk_priority,
                    self.chk_in_progress,
                ),
            ],
        )
        courses_section = _build_section(
            "COURSES",
            [
                _make_switch_with_field_row(
                    self.chk_high_weight_overview,
                    "Threshold",
                    self.spin_high_weight_threshold,
                    field_width=215,
                ),
                _make_switch_with_field_row(
                    self.chk_pin_high_priority,
                    "Pin window",
                    self.spin_pin_high_priority_days,
                    field_width=215,
                ),
            ],
        )
        backup_section = _build_section(
            "BACKUP",
            [
                _make_setting_row(
                    "App data folder",
                    data_folder_row_w,
                    label_width=right_label_width,
                    control_width=right_compound_width,
                ),
                _make_setting_row(
                    "Backup folder",
                    backup_row_w,
                    label_width=right_label_width,
                    control_width=right_compound_width,
                ),
                _make_stacked_setting_row(
                    "Actions",
                    backup_actions_w,
                ),
            ],
        )
        app_section = _build_section(
            "APP",
            [
                _make_stacked_setting_row(
                    "Actions",
                    app_actions_w,
                ),
            ],
        )

        def _category_page(*sections: QWidget) -> QWidget:
            page = QWidget()
            page.setObjectName("SettingsCategoryPage")
            layout = QVBoxLayout()
            layout.setContentsMargins(0, 14, 0, 0)
            layout.setSpacing(12)
            for section in sections:
                layout.addWidget(section)
            layout.addStretch(1)
            page.setLayout(layout)
            return page

        category_tabs = QTabWidget()
        category_tabs.setObjectName("SettingsCategories")
        category_tabs.setDocumentMode(True)
        category_tabs.tabBar().setObjectName("SettingsCategoryBar")
        category_tabs.tabBar().setExpanding(True)
        category_tabs.tabBar().setDrawBase(False)
        category_tabs.addTab(_category_page(user_section, defaults_section, display_section), "General")
        category_tabs.addTab(_category_page(overview_section, courses_section), "Overview")
        category_tabs.addTab(_category_page(grades_section), "Grades")
        category_tabs.addTab(_category_page(backup_section, app_section), "Data + App")
        category_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._settings_category_tabs = category_tabs

        header_left = QVBoxLayout()
        header_left.setSpacing(2)
        header_left.addWidget(title)
        header_left.addWidget(subtitle)

        header = QHBoxLayout()
        header.addLayout(header_left)
        header.addStretch(1)
        header.addWidget(version_label)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        card_layout.addWidget(category_tabs)
        card.setLayout(card_layout)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._settings_card = card

        settings_scroll = QScrollArea()
        settings_scroll.setObjectName("SettingsScroll")
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        settings_content = QWidget()
        settings_content_layout = QHBoxLayout()
        settings_content_layout.setContentsMargins(0, 0, 0, 0)
        settings_content_layout.setSpacing(0)
        settings_content_layout.addStretch(1)
        settings_content_layout.addWidget(card, 0, Qt.AlignmentFlag.AlignTop)
        settings_content_layout.addStretch(1)
        settings_content.setLayout(settings_content_layout)
        settings_scroll.setWidget(settings_content)
        self._settings_scroll = settings_scroll
        self._settings_content = settings_content

        root = QVBoxLayout()
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addLayout(header)
        root.addWidget(settings_scroll, 1)
        self.setLayout(root)

        self.cmb_grade_display.currentTextChanged.connect(self._update_grade_scale_button_visibility)
        self.cmb_gpa_view.currentIndexChanged.connect(self._update_gpa_view_controls)
        self._connect_autosave_signals()

        self.load()
        QTimer.singleShot(0, self._apply_responsive_layout)

    def _apply_responsive_layout(self) -> None:
        card = getattr(self, "_settings_card", None)
        if card is None:
            return

        viewport_width = self._settings_scroll.viewport().width()
        available_width = viewport_width if viewport_width > 100 else max(520, self.width() - 36)
        card_width = max(440, min(780, available_width))
        card.setFixedWidth(card_width)

        grid_columns = 2 if card_width >= 680 else 1
        if getattr(self, "_settings_grid_columns", None) != grid_columns:
            self._settings_grid_columns = grid_columns
            for grid in self._settings_switch_grids:
                widgets = [grid.itemAt(index).widget() for index in range(grid.count())]
                widgets = [widget for widget in widgets if widget is not None]
                for widget in widgets:
                    grid.removeWidget(widget)
                for index, widget in enumerate(widgets):
                    grid.addWidget(widget, index // grid_columns, index % grid_columns)
                grid.setColumnStretch(0, 1)
                grid.setColumnStretch(1, 1 if grid_columns == 2 else 0)
        card.setMinimumHeight(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._apply_responsive_layout)

    def apply_settings(self) -> None:
        mode = get_font_size_mode()
        theme = get_theme()
        colors = theme_colors(theme)
        metrics = font_metrics(mode)
        apply_body_font(self, mode)
        self.setStyleSheet(
            common_page_stylesheet(
                mode,
                theme=theme,
                title_px=metrics["settings_title"],
                title_weight=750,
                include_section_label=True,
            )
            +
            f"""
            QFrame#Card {{
                background: transparent;
                border: none;
            }}
            QFrame#Panel {{
                background: transparent;
                border: none;
            }}
            QFrame#SettingsSection {{
                background: {colors['card_bg']};
                border: 1px solid {colors['border_soft']};
                border-radius: 16px;
            }}
            QTabWidget#SettingsCategories::pane {{
                background: transparent;
                border: none;
            }}
            QTabBar#SettingsCategoryBar {{
                background: {colors['surface_bg']};
                border: 1px solid {colors['border_soft']};
                border-radius: 13px;
            }}
            QTabBar#SettingsCategoryBar::tab {{
                background: transparent;
                color: {colors['muted_text']};
                border: none;
                border-radius: 10px;
                padding: 9px 14px;
                margin: 3px;
                font-weight: 650;
            }}
            QTabBar#SettingsCategoryBar::tab:hover {{
                background: {colors['secondary_bg_hover']};
                color: {colors['text_soft']};
            }}
            QTabBar#SettingsCategoryBar::tab:selected {{
                background: {colors['accent_tint']};
                color: {colors['text_soft']};
            }}
            QLabel#Section {{
                color: {colors['section_text']};
                font-weight: 750;
            }}
            QLabel#SettingLabel {{
                color: {colors['text_soft']};
                font-weight: 600;
            }}
            QLabel#InlineLabel {{
                color: {colors['muted_text']};
            }}
            """
        )

    def _refresh_user_controls(self) -> None:
        active = current_profile()
        active_id = active.id if active is not None else ""
        self.cmb_current_user.blockSignals(True)
        try:
            self.cmb_current_user.clear()
            for profile in list_profiles():
                self.cmb_current_user.addItem(profile.name, profile.id)
                if profile.id == active_id:
                    self.cmb_current_user.setCurrentIndex(self.cmb_current_user.count() - 1)
        finally:
            self.cmb_current_user.blockSignals(False)

    def _on_current_user_changed(self, _index: int) -> None:
        if self._loading_settings:
            return
        profile_id = str(self.cmb_current_user.currentData() or "")
        active = current_profile()
        if profile_id and (active is None or profile_id != active.id):
            self.user_switch_requested.emit(profile_id)

    def manage_users(self) -> None:
        dlg = UserManagerDialog(self)
        dlg.users_changed.connect(self._refresh_user_controls)
        dlg.exec()
        self._refresh_user_controls()

    def edit_api_key(self) -> None:
        dlg = ApiKeyDialog(get_effective_openai_api_key(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        profile = current_profile()
        try:
            save_secret(profile.id if profile is not None else "default", dlg.api_key())
            _delete_setting(SET_OPENAI_API_KEY)
        except Exception as exc:
            QMessageBox.critical(self, "API key", str(exc))
            return
        self._update_api_key_status()
        self.changed.emit()

    def _schedule_save(self, *_args) -> None:
        if self._loading_settings:
            return
        self._settings_autosave_timer.start()

    def _connect_autosave_signals(self) -> None:
        combo_boxes = [
            self.cmb_default_status,
            self.cmb_import_dup,
            self.cmb_grade_calc,
            self.cmb_font_size,
            self.cmb_grade_display,
            self.cmb_gpa_view,
        ]
        spin_boxes = [
            self.spin_upcoming_days,
            self.spin_due_soon_days,
            self.spin_high_priority_due_soon_days,
            self.spin_high_weight_threshold,
            self.spin_pin_high_priority_days,
            self.spin_gpa_last_x_credit_hours,
        ]
        checkboxes = [
            self.chk_high_weight_overview,
            self.chk_pin_high_priority,
            self.chk_hide_completed,
            self.chk_pin_overdue,
            self.chk_overview_reminders,
            self.chk_confirm_delete,
            self.chk_confirm_submit_overview,
            self.chk_compact_rows,
            self.chk_priority,
            self.chk_in_progress,
        ]

        for combo in combo_boxes:
            combo.currentIndexChanged.connect(self._schedule_save)
        for spin in spin_boxes:
            spin.valueChanged.connect(self._schedule_save)
        for checkbox in checkboxes:
            checkbox.toggled.connect(self._schedule_save)

        self.ed_backup_folder.textChanged.connect(self._schedule_save)

    def _update_api_key_status(self) -> None:
        key = get_effective_openai_api_key()
        self.lbl_api_key_status.setText("AI Enabled" if key else "API Not set")

    def hideEvent(self, event) -> None:
        try:
            if self._settings_autosave_timer.isActive():
                self._settings_autosave_timer.stop()
                self.save()
        except Exception:
            pass
        super().hideEvent(event)

    def _update_grade_scale_button_visibility(self) -> None:
        mode = (self.cmb_grade_display.currentText() or "percent").strip().lower()
        self.btn_edit_grade_scale.setVisible(mode in ("letter", "gpa4.0", "gpa4.3"))

    def _update_gpa_view_controls(self) -> None:
        mode = str(self.cmb_gpa_view.currentData() or "cumulative").strip().lower()
        enabled = mode in {"last_x", "both"}
        self.spin_gpa_last_x_credit_hours.setEnabled(enabled)

    def _update_overview_priority_controls(self) -> None:
        priority_enabled = self.chk_priority.isChecked()
        self.chk_pin_high_priority.setEnabled(priority_enabled)
        self.spin_high_priority_due_soon_days.setEnabled(priority_enabled)
        self.spin_pin_high_priority_days.setEnabled(priority_enabled and self.chk_pin_high_priority.isChecked())
        self.spin_high_weight_threshold.setEnabled(self.chk_high_weight_overview.isChecked())

    def edit_grade_scale(self) -> None:
        dlg = GradeScaleDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            set_grade_scale(dlg.get_scale())
            self.changed.emit()

    def load(self) -> None:
        ensure_settings_table()
        ensure_syllabus_ai_defaults()
        self._settings_autosave_timer.stop()
        self._loading_settings = True
        try:
            self._refresh_user_controls()
            self.lbl_data_folder.setText(str(_current_user_data_folder()))
            self.cmb_default_status.setCurrentText(get_default_status())
            self.spin_upcoming_days.setValue(get_overview_upcoming_days())
            self.spin_due_soon_days.setValue(get_overview_due_soon_days())
            self.spin_high_priority_due_soon_days.setValue(get_overview_high_priority_due_soon_days())
            self.chk_high_weight_overview.setChecked(get_overview_high_weight_enabled())
            self.spin_high_weight_threshold.setValue(get_overview_high_weight_threshold())
            self.spin_pin_high_priority_days.setValue(get_overview_pin_high_priority_days())
            self.chk_pin_high_priority.setChecked(get_overview_pin_high_priority_enabled())
            self.chk_hide_completed.setChecked(get_overview_hide_completed())
            self.chk_pin_overdue.setChecked(get_overview_pin_overdue())
            self.chk_overview_reminders.setChecked(get_overview_reminders_enabled())
            self.chk_confirm_delete.setChecked(get_confirm_delete())
            self.cmb_font_size.setCurrentText(get_font_size_mode())
            self.cmb_grade_display.setCurrentText(get_grade_display_mode())
            self.cmb_gpa_view.setCurrentIndex(max(0, self.cmb_gpa_view.findData(get_gpa_view_mode())))
            self.spin_gpa_last_x_credit_hours.setValue(get_gpa_last_x_credit_hours())
            self.chk_confirm_submit_overview.setChecked(get_confirm_submit_overview())
            self.cmb_import_dup.setCurrentIndex(max(0, self.cmb_import_dup.findData(get_import_duplicate_mode())))
            self.cmb_grade_calc.setCurrentIndex(max(0, self.cmb_grade_calc.findData(get_grade_calc_mode())))
            self.chk_compact_rows.setChecked(get_compact_rows())
            folder = get_backup_folder()
            self.ed_backup_folder.setText(folder if folder else "Not set")
            self.chk_priority.setChecked(get_priority_enabled())
            self.chk_in_progress.setChecked(get_in_progress_enabled())
            self._update_grade_scale_button_visibility()
            self._update_gpa_view_controls()
            self._update_overview_priority_controls()
            self._update_api_key_status()
            self.apply_settings()
        finally:
            self._loading_settings = False

    def save(self) -> None:
        if self._loading_settings:
            return
        set_str(SET_DEFAULT_STATUS, self.cmb_default_status.currentText())
        set_int(SET_UPCOMING_DAYS, self.spin_upcoming_days.value())
        set_int(SET_DUE_SOON_DAYS, self.spin_due_soon_days.value())
        set_int(SET_HIGH_PRIORITY_DUE_SOON_DAYS, self.spin_high_priority_due_soon_days.value())
        set_bool(SET_HIGH_WEIGHT_OVERVIEW_ENABLED, self.chk_high_weight_overview.isChecked())
        set_str(SET_HIGH_WEIGHT_OVERVIEW_THRESHOLD, f"{self.spin_high_weight_threshold.value():.1f}")
        set_int(SET_PIN_HIGH_PRIORITY_DAYS, self.spin_pin_high_priority_days.value())
        set_bool(SET_PIN_HIGH_PRIORITY_ENABLED, self.chk_pin_high_priority.isChecked())
        set_bool(SET_HIDE_COMPLETED, self.chk_hide_completed.isChecked())
        set_bool(SET_PIN_OVERDUE, self.chk_pin_overdue.isChecked())
        set_bool(SET_OVERVIEW_REMINDERS_ENABLED, self.chk_overview_reminders.isChecked())
        set_bool(SET_CONFIRM_DELETE, self.chk_confirm_delete.isChecked())
        set_str(SET_THEME, "dark")
        set_str(SET_FONT_SIZE, self.cmb_font_size.currentText())
        set_str(SET_GRADE_DISPLAY, self.cmb_grade_display.currentText())
        set_str(SET_GPA_VIEW_MODE, str(self.cmb_gpa_view.currentData() or "cumulative"))
        set_int(SET_GPA_LAST_X_CREDIT_HOURS, self.spin_gpa_last_x_credit_hours.value())
        # --- New settings save ---
        set_bool(SET_CONFIRM_SUBMIT_OVERVIEW, self.chk_confirm_submit_overview.isChecked())
        set_str(SET_IMPORT_DUP_MODE, str(self.cmb_import_dup.currentData() or "skip"))
        set_str(SET_GRADE_CALC_MODE, str(self.cmb_grade_calc.currentData() or "graded_only"))
        set_bool(SET_COMPACT_ROWS, self.chk_compact_rows.isChecked())
        folder_txt = self.ed_backup_folder.text().strip()
        set_str(SET_BACKUP_FOLDER, "" if folder_txt == "Not set" else folder_txt)
        set_bool(SET_PRIORITY_ENABLED, self.chk_priority.isChecked())
        set_bool(SET_IN_PROGRESS_ENABLED, self.chk_in_progress.isChecked())
        set_str(SET_SYLLABUS_AI_MODEL, DEFAULT_SYLLABUS_AI_MODEL)
        self.apply_settings()
        self._update_grade_scale_button_visibility()
        self._update_gpa_view_controls()
        self._update_api_key_status()
        self.changed.emit()

    def start_syllabus_import(self) -> None:
        self._syllabus_import_flow.start()

    def has_active_background_work(self) -> bool:
        return self._syllabus_import_flow.is_active() or self._update_thread is not None

    def cancel_syllabus_import(self) -> None:
        self._syllabus_import_flow.cancel()

    def start_update_download(self, info: dict, installed_app: object) -> None:
        if self._update_thread is not None:
            QMessageBox.information(self, "Update in progress", "An update download is already running.")
            return

        if not isinstance(installed_app, (os.PathLike, str)):
            QMessageBox.warning(self, "Update unavailable", "The installed app path could not be determined.")
            return

        self._pending_install_info = {
            "installed_app": str(installed_app),
            "update_info": dict(info),
        }
        self._update_cancelled = False
        self._update_thread = QThread(self)
        self._update_worker = UpdateDownloadWorker(info)
        self._update_worker.moveToThread(self._update_thread)

        self._update_progress = UpdateProgressDialog(self)
        self._update_progress.rejected.connect(self.cancel_update_download)

        self._update_thread.started.connect(self._update_worker.run)
        self._update_worker.progress.connect(self._update_progress.set_progress)
        self._update_worker.finished.connect(self._on_update_download_finished)
        self._update_worker.failed.connect(self._on_update_download_failed)
        self._update_worker.finished.connect(self._update_thread.quit)
        self._update_worker.failed.connect(self._update_thread.quit)
        self._update_thread.finished.connect(self._on_update_thread_finished)
        self._update_thread.finished.connect(self._update_thread.deleteLater)
        self._update_thread.start()
        self._update_progress.open()

    def cancel_update_download(self) -> None:
        self._update_cancelled = True
        if self._update_worker is not None:
            self._update_worker.cancel()
        if self._update_progress is not None:
            self._update_progress.hide()

    def _close_update_progress(self) -> None:
        if self._update_progress is None:
            return
        self._update_progress.blockSignals(True)
        self._update_progress.close()
        self._update_progress.deleteLater()
        self._update_progress = None

    def _on_update_download_finished(self, payload: object) -> None:
        self._close_update_progress()
        if self._update_cancelled:
            return
        if not isinstance(payload, dict):
            QMessageBox.critical(self, "Update failed", "The downloaded update could not be prepared.")
            return

        zip_path = str(payload.get("zip_path", "")).strip()
        if not zip_path:
            QMessageBox.critical(self, "Update failed", "The downloaded update could not be prepared.")
            return

        pending = self._pending_install_info or {}
        installed_app = str(pending.get("installed_app", "")).strip()
        if not installed_app:
            QMessageBox.critical(self, "Update failed", "The installed app path is no longer available.")
            return

        answer = QMessageBox.question(
            self,
            "Install update",
            "Update ready. Install now?\n\nThe app will quit, replace itself, and relaunch.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            start_update_installer(Path(zip_path), Path(installed_app))
        except Exception as exc:
            QMessageBox.critical(self, "Update failed", str(exc))
            return

        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _on_update_download_failed(self, message: str) -> None:
        self._close_update_progress()
        if self._update_cancelled:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Update failed")
        box.setText(message or "The update could not be downloaded.")
        open_btn = box.addButton("Open release page", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()

        if box.clickedButton() is open_btn and self._pending_install_info is not None:
            update_info = self._pending_install_info.get("update_info", {})
            zip_url = str(update_info.get("zip_url", "")).strip()
            if zip_url:
                open_release_page(zip_url)

    def _on_update_thread_finished(self) -> None:
        if self._update_worker is not None:
            self._update_worker.deleteLater()
        self._update_worker = None
        self._update_thread = None
        self._update_cancelled = False
        self._pending_install_info = None

    def reset_all_data(self) -> None:
        answer = QMessageBox.warning(
            self,
            "Reset all data",
            "Are you sure? This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            clear_all_app_data()
        except Exception as exc:
            QMessageBox.critical(self, "Reset failed", str(exc))
            return

        self.load()
        self.changed.emit()
        QMessageBox.information(self, "Reset complete", "All saved data has been cleared.")


def build_settings_tab() -> QWidget:
    return SettingsPage()


# Alias for older code paths
build_settings = build_settings_tab
# --- Backup / reset utilities ---

def _db_path() -> str | None:
    try:
        db = QSqlDatabase.database()
        p = db.databaseName()
        return p if p else None
    except Exception:
        return None

def run_backup_if_due(force: bool = False) -> tuple[bool, str]:
    # Auto-backup has been removed; keep the helper name as a compatibility shim.
    return run_backup_now()


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _settings_table_snapshot() -> dict:
    ensure_settings_table()
    rows: dict[str, str] = {}
    q = QSqlQuery()
    if not q.exec("SELECT key, value FROM settings ORDER BY key ASC"):
        raise RuntimeError(q.lastError().text())
    while q.next():
        key = str(q.value(0) or "")
        if key not in SENSITIVE_SETTING_KEYS:
            rows[key] = str(q.value(1) or "")
    return rows


def _qsettings_snapshot() -> dict:
    settings = app_qsettings()
    settings.sync()
    return {str(key): _json_safe(settings.value(key)) for key in settings.allKeys()}


def _unique_backup_path(folder: str) -> str:
    base_name = datetime.now().strftime("%d-%b-%Y")
    candidate = os.path.join(folder, f"{base_name}.json")
    if not os.path.exists(candidate):
        return candidate
    suffix = 2
    while True:
        candidate = os.path.join(folder, f"{base_name}-{suffix}.json")
        if not os.path.exists(candidate):
            return candidate
        suffix += 1


def run_backup_now() -> tuple[bool, str]:
    folder = get_backup_folder()
    if not folder:
        return False, "Backup folder not set"

    os.makedirs(folder, exist_ok=True)
    backup_path = _unique_backup_path(folder)

    try:
        courses: list[dict] = []
        q = QSqlQuery()
        if not q.exec(
            """
            SELECT
                name,
                coalesce(term, ''),
                coalesce(academic_year_start, 0)
            FROM courses
            ORDER BY name ASC
            """
        ):
            raise RuntimeError(q.lastError().text())
        while q.next():
            courses.append({
                "name": str(q.value(0) or ""),
                "term": str(q.value(1) or ""),
                "academic_year_start": q.value(2),
            })

        tasks: list[dict] = []
        q = QSqlQuery()
        if not q.exec(
            """
            SELECT
                c.name,
                t.item,
                coalesce(t.component, ''),
                coalesce(t.due_datetime, ''),
                coalesce(t.status, ''),
                t.weight,
                t.grade,
                coalesce(t.ungraded, 0),
                coalesce(t.notes, ''),
                coalesce(t.priority, 'normal'),
                coalesce(t.task_type, ''),
                coalesce(t.due_status, ''),
                t.weight_raw_value,
                coalesce(t.weight_raw_unit, ''),
                coalesce(t.weight_source, 'missing'),
                coalesce(t.syllabus_metadata, '')
            FROM tasks t
            JOIN courses c ON c.id = t.course_id
            ORDER BY c.name ASC, t.due_datetime ASC, t.item ASC
            """
        ):
            raise RuntimeError(q.lastError().text())
        while q.next():
            tasks.append({
                "course": str(q.value(0) or ""),
                "item": str(q.value(1) or ""),
                "component": str(q.value(2) or ""),
                "due_datetime": str(q.value(3) or ""),
                "status": str(q.value(4) or ""),
                "weight": q.value(5),
                "grade": q.value(6),
                "ungraded": bool(q.value(7) or 0),
                "notes": str(q.value(8) or ""),
                "priority": str(q.value(9) or "normal"),
                "task_type": str(q.value(10) or ""),
                "due_status": str(q.value(11) or ""),
                "weight_raw_value": q.value(12),
                "weight_raw_unit": str(q.value(13) or ""),
                "weight_source": str(q.value(14) or "missing"),
                "syllabus_metadata": str(q.value(15) or ""),
            })

        previous_courses: list[dict] = []
        q = QSqlQuery()
        if not q.exec(
            """
            SELECT
                course_name,
                final_percent,
                final_letter,
                credit_hours,
                lower(term),
                academic_year_start
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
            raise RuntimeError(q.lastError().text())
        while q.next():
            previous_courses.append({
                "course_name": str(q.value(0) or ""),
                "final_percent": q.value(1),
                "final_letter": str(q.value(2) or ""),
                "credit_hours": q.value(3),
                "term": str(q.value(4) or ""),
                "academic_year_start": q.value(5),
            })

        payload = {
            "backup_version": 2,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "user": profile_summary(),
            "database_path": _db_path(),
            "settings": _settings_table_snapshot(),
            "app_qsettings": _qsettings_snapshot(),
            "courses": courses,
            "tasks": tasks,
            "previous_courses": previous_courses,
        }

        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return True, f"Backed up to {backup_path}"
    except Exception as e:
        return False, str(e)


def clear_all_app_data() -> None:
    db = QSqlDatabase.database()
    if not db.isValid():
        raise RuntimeError("Database is not available.")

    if not db.transaction():
        raise RuntimeError(db.lastError().text())

    try:
        for sql in [
            "DELETE FROM tasks",
            "DELETE FROM previous_courses",
            "DELETE FROM courses",
            "DELETE FROM settings",
        ]:
            q = QSqlQuery()
            if not q.exec(sql):
                raise RuntimeError(q.lastError().text())
        if not db.commit():
            raise RuntimeError(db.lastError().text())
    except Exception:
        db.rollback()
        raise

    clear_all_app_qsettings()
    profile = current_profile()
    delete_secret(profile.id if profile is not None else "default")
