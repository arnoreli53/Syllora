
from __future__ import annotations

import sys
from datetime import datetime
from PySide6.QtCore import Qt, QDateTime, QTime, QSortFilterProxyModel, QRect, QTimer, QSettings, QEvent, QObject, Signal, QModelIndex
from PySide6.QtGui import QColor, QBrush, QPalette, QPainter, QPen, QKeySequence, QShortcut
from PySide6.QtSql import QSqlRelationalTableModel, QSqlRelation, QSqlQuery

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableView,
    QMessageBox,
    QComboBox,
    QMenu,
    QCheckBox,
    QStyledItemDelegate,
    QDateTimeEdit,
    QLabel,
    QFrame,
    QHeaderView,
    QAbstractItemView,
    QFileDialog,
    QDialog,
    QApplication,
    QLineEdit,
    QAbstractItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QProxyStyle,
)

# --- Settings imports ---

from ui_common import apply_body_font, common_page_stylesheet, scaled_row_height, theme_colors
from app_settings import app_qsettings
from ui_settings import (
    SyllabusImportFlow,
    SwitchCheckBox,
    get_confirm_delete,
    get_compact_rows,
    get_default_status,
    get_font_size_mode,
    get_in_progress_enabled,
    get_priority_enabled,
    parse_task_grade_input,
    get_theme,
)


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

# --- openpyxl import with safe fallback ---
try:
    from openpyxl import load_workbook
except Exception:  # pragma: no cover
    load_workbook = None


# --- Proxy model for view filtering + row coloring ---


def _theme_colors() -> dict[str, str]:
    return theme_colors(get_theme())


def _theme_text_color() -> QColor:
    return QColor(_theme_colors()["text"])


def _theme_muted_color() -> QColor:
    return QColor(_theme_colors()["muted_text"])

class TasksFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.course_id: int | None = None
        self.course_name: str | None = None
        self.hide_ungraded: bool = False
        self.status_filter: str | None = None  # None = all
        self._task_course_cache: dict[int, int] = {}

    def clear_cache(self) -> None:
        self._task_course_cache.clear()

    def refresh_filter(self) -> None:
        """Re-evaluate rows using the current Qt filtering API."""
        if hasattr(self, "beginFilterChange") and hasattr(self, "endFilterChange"):
            self.beginFilterChange()
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:  # Compatibility with older supported PySide6 builds.
            self.invalidateFilter()

    def _course_column(self, model) -> int:
        try:
            col = model.fieldIndex("course_id")
        except Exception:
            col = -1
        if col != -1:
            return col

        try:
            column_count = int(model.columnCount())
        except Exception:
            column_count = 0

        for col in range(column_count):
            try:
                relation = model.relation(col)
            except Exception:
                relation = None
            if relation is None or not relation.isValid():
                continue
            if str(relation.tableName() or "").strip().lower() == "courses":
                return col

        return -1

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        m = self.sourceModel()
        if m is None:
            return True

        # Course filter (reliable): compare against the raw FK from the tasks table.
        if self.course_id is not None or self.course_name:
            ccol = self._course_column(m)
            id_col = m.fieldIndex("id")
            target_id = self.course_id
            target_name = (self.course_name or "").strip().lower()

            matched = False

            # 1) Raw FK match from the source model itself (important for unsaved new rows).
            if target_id is not None and ccol != -1:
                raw_fk_values: list[object] = [m.data(m.index(source_row, ccol), Qt.EditRole)]
                try:
                    raw_fk_values.append(m.record(source_row).value("course_id"))
                except Exception:
                    pass
                for raw_fk in raw_fk_values:
                    try:
                        if int(raw_fk) == int(target_id):
                            matched = True
                            break
                    except Exception:
                        continue

            # 2) Raw FK match via tasks.id -> tasks.course_id (robust for saved rows too)
            if not matched and target_id is not None and id_col != -1:
                try:
                    task_id = int(m.record(source_row).value("id"))
                except Exception:
                    task_id = None

                if task_id is not None:
                    fk = self._task_course_cache.get(task_id)
                    if fk is None:
                        q = QSqlQuery()
                        q.prepare("SELECT course_id FROM tasks WHERE id = ?")
                        q.addBindValue(task_id)
                        if q.exec() and q.next():
                            try:
                                fk = int(q.value(0))
                                self._task_course_cache[task_id] = fk
                            except Exception:
                                fk = None
                    if fk is not None and int(fk) == int(target_id):
                        matched = True

            # 3) Fallback: display-name match
            if not matched and target_name and ccol != -1:
                dn = m.data(m.index(source_row, ccol), Qt.DisplayRole)
                dn = "" if dn is None else str(dn).strip().lower()
                if dn == target_name:
                    matched = True

            if not matched:
                return False

        # Hide ungraded filter (hide tasks explicitly marked as ungraded)
        if self.hide_ungraded:
            task_type_col = m.fieldIndex("task_type")
            ungraded_col = m.fieldIndex("ungraded")
            if task_type_col != -1:
                task_type = normalize_task_type(m.data(m.index(source_row, task_type_col), Qt.EditRole))
                if task_type == "ungraded":
                    return False
            if ungraded_col != -1:
                legacy_ungraded = bool(m.data(m.index(source_row, ungraded_col), Qt.EditRole) or 0)
                if legacy_ungraded:
                    return False

        # Status filter (if set)
        if self.status_filter:
            scol = m.fieldIndex("status")
            if scol != -1:
                s = (m.data(m.index(source_row, scol), Qt.EditRole) or "").strip().lower()
                if s != self.status_filter:
                    return False

        return True

    def lessThan(self, left, right) -> bool:
        m = self.sourceModel()
        if m is None:
            return super().lessThan(left, right)

        # Primary: Due date (ascending) when available
        dcol = m.fieldIndex("due_datetime")
        if dcol != -1:
            dl = m.data(m.index(left.row(), dcol), Qt.EditRole) or m.data(m.index(left.row(), dcol), Qt.DisplayRole) or ""
            dr = m.data(m.index(right.row(), dcol), Qt.EditRole) or m.data(m.index(right.row(), dcol), Qt.DisplayRole) or ""
            dl = "" if dl is None else str(dl).strip()
            dr = "" if dr is None else str(dr).strip()

            # Normalize to YYYY-MM-DD HH:MM (missing -> very large)
            def _norm_due(s: str) -> str:
                if not s:
                    return "9999-12-31 23:59"
                # If date-only
                if len(s) >= 10 and s[4] == "-" and s[7] == "-":
                    if len(s) == 10:
                        return s + " 23:59"
                    return s[:16]
                return s

            ndl = _norm_due(dl)
            ndr = _norm_due(dr)
            if ndl != ndr:
                return ndl < ndr

        # Secondary: Status order (requested)
        scol = m.fieldIndex("status")
        if scol != -1:
            sl = (m.data(m.index(left.row(), scol), Qt.EditRole) or "").strip().lower()
            sr = (m.data(m.index(right.row(), scol), Qt.EditRole) or "").strip().lower()

            rank = {
                "in progress": 0,
                "not started": 1,
                "submitted": 2,
                "graded": 3,
            }
            rl = rank.get(sl, 99)
            rr = rank.get(sr, 99)
            if rl != rr:
                return rl < rr

        # Tertiary: fall back to default
        return super().lessThan(left, right)
    
    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.BackgroundRole and index.isValid():
            m = self.sourceModel()
            if m is not None:
                theme = get_theme()
                scol = m.fieldIndex("status")
                if scol != -1:
                    src_idx = self.mapToSource(index)
                    s = (m.data(m.index(src_idx.row(), scol), Qt.EditRole) or "").strip().lower()
                    if s == "in progress":
                        return QBrush(QColor(243, 247, 255) if theme == "light" else QColor("#16314A"))
                    if s == "submitted":
                        return QBrush(QColor(255, 248, 231) if theme == "light" else QColor("#382F1D"))
                    if s == "graded":
                        return QBrush(QColor(236, 251, 239) if theme == "light" else QColor("#18372A"))

                return QBrush(QColor(theme_colors(theme)["row_default_bg"]))

        return super().data(index, role)

# --- Row card delegate for visual separation ---

def _menu_global_pos(event, option) -> object:
    try:
        return event.globalPosition().toPoint()
    except Exception:
        try:
            return event.globalPos()
        except Exception:
            if option.widget is not None:
                return option.widget.mapToGlobal(option.rect.center())
            return option.rect.center()

def row_card_fill(view: QTableView, index) -> QColor:
    bg = view.model().data(index, Qt.BackgroundRole)
    fill = QColor(_theme_colors()["row_default_bg"])
    if isinstance(bg, QBrush):
        fill = bg.color()
        fill.setAlpha(255)
    return fill


def row_card_rect(view: QTableView, row: int, anchor_col: int) -> QRect:
    left_idx = view.model().index(row, anchor_col)
    left_rect = view.visualRect(left_idx)
    if not left_rect.isValid():
        return QRect()

    margin = 5
    return QRect(
        margin,
        left_rect.top() + 2,
        max(0, view.viewport().width() - (2 * margin)),
        max(0, left_rect.height() - 4),
    )

def paint_row_card_background(painter, row_rect: QRect, fill: QColor, *, selected: bool = False) -> None:
    if not row_rect.isValid():
        return
    card_fill = QColor(_theme_colors()["row_selection_bg"]) if selected else QColor(fill)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(card_fill)
    painter.drawRoundedRect(row_rect, 9, 9)

    painter.restore()


def paint_row_card_selection_outline(painter, row_rect: QRect) -> None:
    if not row_rect.isValid():
        return

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(_theme_colors()["row_selection_border"]), 2))
    painter.drawRoundedRect(row_rect.adjusted(1, 1, -1, -1), 9, 9)
    painter.restore()


def draw_row_card(painter, option, index, view, first_col):
    """Draw row background/selection card once per row based on first visible column."""
    if not view or not view.model() or not index.isValid():
        return

    visible_col, _ = _visible_edge_columns(view, first_col)

    # Only draw once per row (on first visible column)
    if index.column() != visible_col:
        return

    fill = row_card_fill(view, view.model().index(index.row(), visible_col))
    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    paint_row_card_background(
        painter,
        row_card_rect(view, index.row(), visible_col),
        fill,
        selected=selected,
    )


def inline_editor_rect(option, index, view: QTableView, *, base_left: int = 8, base_right: int = 8) -> QRect:
    return content_rect_for_cell(option, index, view, base_left=base_left, base_right=base_right)


def _visible_edge_columns(view: QTableView, fallback: int) -> tuple[int, int]:
    if view is None or view.model() is None:
        return fallback, fallback
    viewport_width = view.viewport().width()
    viewport_visible = []
    non_hidden = []
    for c in range(view.model().columnCount()):
        if view.isColumnHidden(c):
            continue
        non_hidden.append(c)
        x = view.columnViewportPosition(c)
        w = view.columnWidth(c)
        if w > 0 and (x + w) > 0 and x < viewport_width:
            viewport_visible.append(c)

    if viewport_visible:
        return viewport_visible[0], viewport_visible[-1]
    if non_hidden:
        return non_hidden[0], non_hidden[-1]
    return fallback, fallback


def content_rect_for_cell(option, index, view: QTableView, base_left: int = 10, base_right: int = 10) -> QRect:
    left = base_left
    right = base_right
    first_col, last_col = _visible_edge_columns(view, index.column())
    if index.column() == first_col:
        left += 6
    if index.column() == last_col:
        right += 8
    return option.rect.adjusted(left, 2, -right, -2)


def mark_delegate_editor(editor: QWidget, index) -> None:
    editor.setProperty("_tasks_delegate_editor", True)
    editor.setProperty("_tasks_editor_row", int(index.row()))
    editor.setProperty("_tasks_editor_col", int(index.column()))


def is_unsaved_model_row(index) -> bool:
    try:
        model = index.model()
        source_model = model.sourceModel() if hasattr(model, "sourceModel") else model
        source_index = model.mapToSource(index) if hasattr(model, "mapToSource") else index
        if source_model is None or not source_index.isValid():
            return False
        id_col = source_model.fieldIndex("id")
        if id_col == -1:
            return False
        task_id = source_model.data(source_model.index(source_index.row(), id_col), Qt.EditRole)
        return int(task_id or 0) <= 0
    except Exception:
        return False


def visible_delegate_editors(view: QTableView | None) -> list[QWidget]:
    if view is None:
        return []
    try:
        return [
            widget
            for widget in view.findChildren(QWidget)
            if bool(widget.property("_tasks_delegate_editor")) and widget.isVisible()
        ]
    except Exception:
        return []


def is_cell_being_edited(option: QStyleOptionViewItem, index, view: QTableView | None) -> bool:
    if bool(option.state & QStyle.StateFlag.State_Editing):
        return True
    if view is None or not index.isValid():
        return False
    try:
        if view.state() == QAbstractItemView.State.EditingState and view.currentIndex() == index:
            return True
        for editor in visible_delegate_editors(view):
            if (
                int(editor.property("_tasks_editor_row") or -1) == index.row()
                and int(editor.property("_tasks_editor_col") or -1) == index.column()
            ):
                return True
    except Exception:
        pass
    return False


def style_inline_line_editor(editor: QLineEdit) -> None:
    colors = _theme_colors()
    editor.setFrame(False)
    editor.setAutoFillBackground(False)
    editor.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
    editor.setStyleSheet(
        "QLineEdit {"
        " background: transparent;"
        " border: none;"
        " border-radius: 0px;"
        " padding: 0px 2px;"
        " margin: 0px;"
        f" color: {colors['text']};"
        f" selection-background-color: {colors['accent_tint']};"
        "}"
        "QLineEdit:focus {"
        " background: transparent;"
        " border: none;"
        f" color: {colors['text']};"
        "}"
    )


def style_inline_datetime_editor(editor: QDateTimeEdit) -> None:
    colors = _theme_colors()
    editor.setFrame(False)
    editor.setAutoFillBackground(False)
    editor.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
    editor.setStyleSheet(
        "QDateTimeEdit {"
        " background: transparent;"
        " border: none;"
        " border-radius: 0px;"
        " padding: 0px 2px;"
        " margin: 0px;"
        f" color: {colors['text']};"
        "}"
        "QDateTimeEdit:focus {"
        " background: transparent;"
        " border: none;"
        f" color: {colors['text']};"
        "}"
        "QDateTimeEdit::drop-down { border: 0px; width: 22px; background: transparent; }"
    )

# --- Helper: normalize inline text editor font and selection ---
def normalize_inline_text_editor(editor: QLineEdit) -> None:
    """Keep inline text editors visually identical to table text."""
    font = editor.font()
    font.setBold(False)
    font.setItalic(False)
    editor.setFont(font)

    def _clear_selection():
        try:
            editor.deselect()
            editor.setSelection(0, 0)
            editor.setCursorPosition(len(editor.text()))
        except Exception:
            pass

    QTimer.singleShot(0, _clear_selection)


def status_chip_colors(status: str) -> tuple[QColor, QColor]:
    if get_theme() == "dark":
        palette = {
            "not started": (QColor(58, 61, 69), QColor(197, 202, 212)),
            "in progress": (QColor(28, 63, 102), QColor(99, 181, 255)),
            "submitted": (QColor(96, 74, 24), QColor(255, 205, 102)),
            "graded": (QColor(33, 79, 52), QColor(110, 214, 145)),
        }
        return palette.get(status, (QColor(58, 61, 69), QColor(197, 202, 212)))
    palette = {
        "not started": (QColor(238, 241, 248), QColor(96, 102, 122)),
        "in progress": (QColor(223, 243, 255), QColor(23, 146, 224)),
        "submitted": (QColor(255, 245, 213), QColor(228, 156, 7)),
        "graded": (QColor(224, 245, 230), QColor(48, 167, 88)),
    }
    return palette.get(status, (QColor(238, 241, 248), QColor(96, 102, 122)))


def allowed_statuses() -> list[str]:
    statuses = ["not started"]
    if get_in_progress_enabled():
        statuses.append("in progress")
    statuses.extend(["submitted", "graded"])
    return statuses


def normalize_task_type(value) -> str:
    text = "" if value is None else str(value).strip().lower()
    if text in {"ungraded", "bonus"}:
        return text
    return ""

def make_display_option(option: QStyleOptionViewItem, index, view: QTableView, *, base_left: int = 8, base_right: int = 8) -> QStyleOptionViewItem:
    opt = QStyleOptionViewItem(option)
    opt.state = opt.state & ~QStyle.StateFlag.State_Selected
    opt.state = opt.state & ~QStyle.StateFlag.State_MouseOver
    opt.state = opt.state & ~QStyle.StateFlag.State_HasFocus
    opt.showDecorationSelected = False
    opt.backgroundBrush = QBrush(Qt.GlobalColor.transparent)
    pal = QPalette(opt.palette)
    pal.setBrush(QPalette.ColorRole.Base, QBrush(Qt.GlobalColor.transparent))
    pal.setBrush(QPalette.ColorRole.Window, QBrush(Qt.GlobalColor.transparent))
    opt.palette = pal
    opt.rect = content_rect_for_cell(opt, index, view, base_left=base_left, base_right=base_right)

    font = opt.font
    font.setBold(False)
    font.setItalic(False)
    opt.font = font
    return opt


class CardTableStyle(QProxyStyle):
    """Prevent the native item-view style from painting hover/current-cell rectangles."""

    def _stifle_option(self, opt: QStyleOptionViewItem) -> QStyleOptionViewItem:
        opt = QStyleOptionViewItem(opt)
        opt.state = opt.state & ~QStyle.StateFlag.State_Selected
        opt.state = opt.state & ~QStyle.StateFlag.State_MouseOver
        opt.state = opt.state & ~QStyle.StateFlag.State_HasFocus
        opt.showDecorationSelected = False
        opt.backgroundBrush = QBrush(Qt.GlobalColor.transparent)

        pal = QPalette(opt.palette)
        pal.setBrush(QPalette.ColorRole.Base, QBrush(Qt.GlobalColor.transparent))
        pal.setBrush(QPalette.ColorRole.Window, QBrush(Qt.GlobalColor.transparent))
        opt.palette = pal

        return opt

    def drawPrimitive(self, element, option, painter, widget=None):
        if element in (
            QStyle.PrimitiveElement.PE_PanelItemViewItem,
            QStyle.PrimitiveElement.PE_PanelItemViewRow,
            QStyle.PrimitiveElement.PE_FrameFocusRect,
        ):
            return

        return super().drawPrimitive(element, option, painter, widget)

    def drawControl(self, element, option, painter, widget=None):
        if element == QStyle.ControlElement.CE_ItemViewItem:
            return

        return super().drawControl(element, option, painter, widget)


class TasksTableView(QTableView):
    """Custom viewport painting keeps row-card backgrounds stable across partial repaints."""

    def selectionChanged(self, selected, deselected) -> None:
        super().selectionChanged(selected, deselected)
        self.viewport().update()

    def currentChanged(self, current, previous) -> None:
        super().currentChanged(current, previous)
        self.viewport().update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

        model = self.model()
        if model is None or model.rowCount() == 0 or model.columnCount() == 0:
            return

        first_col, _ = _visible_edge_columns(self, 0)
        if first_col < 0 or first_col >= model.columnCount():
            return

        top_row = self.rowAt(max(0, event.rect().top()))
        if top_row < 0:
            top_row = 0

        bottom_row = self.rowAt(max(0, event.rect().bottom()))
        if bottom_row < 0:
            bottom_row = model.rowCount() - 1

        if bottom_row < top_row:
            return

        painter = QPainter(self.viewport())
        painter.setClipRect(event.rect())
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOver)
        selection = self.selectionModel()
        selected_rows = set()
        if selection is not None:
            selected_rows = {idx.row() for idx in selection.selectedIndexes()}
            current_index = selection.currentIndex()
            if current_index.isValid():
                selected_rows.add(current_index.row())

        for row in range(top_row, bottom_row + 1):
            if self.isRowHidden(row):
                continue

            anchor_index = model.index(row, first_col)
            fill = row_card_fill(self, anchor_index)
            is_selected = row in selected_rows
            paint_row_card_background(
                painter,
                row_card_rect(self, row, first_col),
                fill,
                selected=is_selected,
            )

        if selected_rows:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            for row in range(top_row, bottom_row + 1):
                if row in selected_rows and not self.isRowHidden(row):
                    paint_row_card_selection_outline(painter, row_card_rect(self, row, first_col))


class RowCardDelegate(QStyledItemDelegate):
    """Paint each row as a rounded card with padding/spacing, using the model BackgroundRole as the card fill."""

    def __init__(self, view: QTableView, first_col: int, last_col: int):
        super().__init__(view)
        self.view = view
        self.first_col = first_col
        self.last_col = last_col

    def _first_visible_column(self) -> int:
        """Return the first visible column in the view, fallback to self.first_col."""
        for c in range(self.view.model().columnCount()):
            if not self.view.isColumnHidden(c):
                return c
        return self.first_col


    def paint(self, painter, option, index):
        if is_cell_being_edited(option, index, self.view):
            return
        # Draw text for this cell only
        raw = index.data(Qt.DisplayRole) or ""
        rect = content_rect_for_cell(option, index, self.view, base_left=8, base_right=8)
        painter.save()
        font = painter.font()
        font.setBold(False)
        font.setItalic(False)
        painter.setFont(font)
        painter.setPen(_theme_text_color())
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(raw))
        painter.restore()


class InlineTextDelegate(QStyledItemDelegate):
    """Inset text editor that matches the rounded row-card styling."""

    def __init__(self, parent=None, placeholder_text=""):
        super().__init__(parent)
        self.placeholder_text = placeholder_text

    def paint(self, painter, option, index):
        view = self.parent()
        if is_cell_being_edited(option, index, view):
            return
        raw = index.data(Qt.DisplayRole) or ""
        rect = content_rect_for_cell(option, index, view, base_left=8, base_right=8)
        painter.save()
        font = painter.font()
        font.setBold(False)
        font.setItalic(False)
        painter.setFont(font)
        
        # Show placeholder text if cell is empty
        if not raw or str(raw).strip() == "":
            painter.setPen(QColor(_theme_text_color()).lighter(150))
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.placeholder_text)
        else:
            painter.setPen(_theme_text_color())
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(raw))
        painter.restore()

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        style_inline_line_editor(editor)
        normalize_inline_text_editor(editor)
        editor.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        editor.setPlaceholderText(self.placeholder_text)
        mark_delegate_editor(editor, index)
        return editor

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(inline_editor_rect(option, index, self.parent(), base_left=8, base_right=8))


# --- Percent display delegate ---
class PercentDisplayDelegate(QStyledItemDelegate):
    """Show numeric values as trimmed decimal percentages; edit as free text to allow blanks."""

    @staticmethod
    def _format_percent(value: float) -> str:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return f"{text}%"

    def paint(self, painter, option, index):
        view = self.parent()
        if is_cell_being_edited(option, index, view):
            return
        value = index.data(Qt.DisplayRole)
        if value is None:
            value = index.data(Qt.EditRole)
        text = self.displayText(value, None)
        rect = content_rect_for_cell(option, index, view, base_left=8, base_right=8)
        painter.save()
        font = painter.font()
        font.setBold(False)
        font.setItalic(False)
        painter.setFont(font)
        painter.setPen(_theme_text_color())
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(text))
        painter.restore()

    def displayText(self, value, locale):
        if value is None:
            return ""
        s = str(value).strip()
        if s == "":
            return ""
        try:
            x = float(s.replace("%", ""))
        except Exception:
            return s
        return self._format_percent(x)

    def createEditor(self, parent, option, index):
        e = QLineEdit(parent)
        e.setPlaceholderText("")
        style_inline_line_editor(e)
        normalize_inline_text_editor(e)
        e.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        mark_delegate_editor(e, index)
        return e

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(inline_editor_rect(option, index, self.parent(), base_left=8, base_right=8))

    def setEditorData(self, editor, index):
        raw = index.data(Qt.EditRole)
        if raw is None:
            editor.setText("")
            return
        text = str(raw).strip().replace("%", "")
        if is_unsaved_model_row(index) and text in {"0", "0.0", "0.00"}:
            text = ""
        editor.setText(text)
        if text:
            QTimer.singleShot(0, editor.selectAll)

    def setModelData(self, editor, model, index):
        s = editor.text().strip().replace("%", "")
        if s == "":
            model.setData(index, None, Qt.EditRole)
            return
        try:
            x = float(s)
        except Exception:
            # leave unchanged on parse failure
            return
        model.setData(index, x, Qt.EditRole)


class TaskGradeDelegate(QStyledItemDelegate):
    """Allow either numeric percentages or letter grades from the configured scale."""

    @staticmethod
    def _format_percent(value: float) -> str:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return f"{text}%"

    def paint(self, painter, option, index):
        view = self.parent()
        if is_cell_being_edited(option, index, view):
            return
        value = index.data(Qt.DisplayRole)
        if value is None:
            value = index.data(Qt.EditRole)
        text = self.displayText(value, None)
        rect = content_rect_for_cell(option, index, view, base_left=8, base_right=8)
        painter.save()
        font = painter.font()
        font.setBold(False)
        font.setItalic(False)
        painter.setFont(font)
        painter.setPen(_theme_text_color())
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(text))
        painter.restore()

    def displayText(self, value, locale):
        if value is None:
            return ""
        text = str(value).strip()
        if text == "":
            return ""
        try:
            numeric = float(text.replace("%", "").replace(",", ""))
        except Exception:
            return text
        return self._format_percent(numeric)

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setPlaceholderText("e.g. 92 or A-")
        style_inline_line_editor(editor)
        normalize_inline_text_editor(editor)
        editor.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        mark_delegate_editor(editor, index)
        return editor

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(inline_editor_rect(option, index, self.parent(), base_left=8, base_right=8))

    def setEditorData(self, editor, index):
        raw = index.data(Qt.EditRole)
        if raw is None:
            editor.setText("")
            return
        text = str(raw).strip().replace("%", "")
        if is_unsaved_model_row(index) and text in {"0", "0.0", "0.00"}:
            text = ""
        editor.setText(text)
        if text:
            QTimer.singleShot(0, editor.selectAll)

    def setModelData(self, editor, model, index):
        text = editor.text().strip()
        model.setData(index, text or None, Qt.EditRole)


class DueDateTimeDelegate(QStyledItemDelegate):
    """Date-only picker that ALWAYS stores due time as 23:59 with card styling."""

    DISPLAY_FMT = "yyyy-MM-dd"            # what the user sees/edits
    STORE_FMT = "yyyy-MM-dd HH:mm"        # what we store in SQLite
    DEFAULT_DUE_TIME = QTime(23, 59)
    MONTH_SECTION_INDEX = 1                # index of the month segment in DISPLAY_FMT

    def __init__(self, parent=None, tab_target_column: int = -1):
        super().__init__(parent)
        # Keyboard flow: Tab out of the due-date editor jumps straight to this
        # column (Weight), skipping the click-only Status cell in between.
        self._tab_target_column = tab_target_column

    def paint(self, painter, option, index):
        view = self.parent()
        if is_cell_being_edited(option, index, view):
            return
        text = self.displayText(index.data(Qt.DisplayRole) or index.data(Qt.EditRole) or "", None)
        rect = content_rect_for_cell(option, index, view, base_left=8, base_right=8)
        painter.save()
        font = painter.font()
        font.setBold(False)
        font.setItalic(False)
        painter.setFont(font)
        painter.setPen(_theme_text_color())
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(text))
        painter.restore()

    def displayText(self, value, locale):
        s = "" if value is None else str(value)
        s = s.strip()
        if not s:
            return ""
        if " " in s:
            return s.split(" ", 1)[0]
        if "T" in s:
            return s.split("T", 1)[0]
        return s

    def createEditor(self, parent, option, index):
        w = QDateTimeEdit(parent)
        w.setCalendarPopup(True)
        w.setDisplayFormat(self.DISPLAY_FMT)
        style_inline_datetime_editor(w)
        mark_delegate_editor(w, index)
        # Land on the month segment: the year rarely needs changing task-to-task,
        # so skip straight past it instead of making users tab/arrow over it.
        QTimer.singleShot(0, lambda w=w: self._focus_month_section(w))
        return w

    @staticmethod
    def _focus_month_section(editor: QDateTimeEdit) -> None:
        try:
            editor.setCurrentSectionIndex(DueDateTimeDelegate.MONTH_SECTION_INDEX)
        except RuntimeError:
            pass

    def eventFilter(self, editor, event):
        if (
            self._tab_target_column != -1
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key_Tab
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            view = self.parent()
            row = editor.property("_tasks_editor_row")
            if view is not None and row is not None:
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
                target = view.model().index(int(row), self._tab_target_column)
                if target.isValid():
                    view.setCurrentIndex(target)
                    view.edit(target)
                return True
        return super().eventFilter(editor, event)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(inline_editor_rect(option, index, self.parent(), base_left=8, base_right=8))

    def setEditorData(self, editor, index):
        raw = (index.data() or "").strip()

        dt = QDateTime()
        if raw:
            dt = QDateTime.fromString(raw, Qt.ISODate)
        if not dt.isValid() and raw:
            dt = QDateTime.fromString(raw, self.STORE_FMT)
        if not dt.isValid() and raw:
            dt = QDateTime.fromString(raw, self.DISPLAY_FMT)

        if not dt.isValid():
            dt = QDateTime.currentDateTime()
            dt = QDateTime(dt.date(), self.DEFAULT_DUE_TIME)
        else:
            dt = QDateTime(dt.date(), self.DEFAULT_DUE_TIME)

        editor.setDateTime(dt)

    def setModelData(self, editor, model, index):
        dt = editor.dateTime()
        dt = QDateTime(dt.date(), self.DEFAULT_DUE_TIME)
        model.setData(index, dt.toString(self.STORE_FMT))


# --- Status combobox delegate ---
class StatusDelegate(QStyledItemDelegate):
    """Menu-based status picker to avoid accidental in-cell overlay editors."""
    CHIP_SCALE = 1.25

    def paint(self, painter, option, index):
        raw = index.data(Qt.EditRole) or index.data(Qt.DisplayRole) or "not started"
        status = str(raw).strip().lower()
        bg, fg = status_chip_colors(status)
        label = status.title()

        text_rect = content_rect_for_cell(option, index, self.parent(), base_left=8, base_right=8)
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)

        font = painter.font()
        base_point_size = max(9, font.pointSize() - 1)
        font.setPointSizeF(base_point_size * self.CHIP_SCALE)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()

        chip_h = 28
        chip_radius = 14
        dot_d = 9
        chip_left_pad = 15
        text_left_pad = 33
        chip_right_pad = 18
        chip_w = min(text_rect.width(), fm.horizontalAdvance(label) + text_left_pad + chip_right_pad)
        chip_rect = QRect(text_rect.left(), text_rect.center().y() - (chip_h // 2), chip_w, chip_h)
        dot_x = chip_rect.left() + chip_left_pad
        dot_y = chip_rect.center().y() - (dot_d // 2)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(chip_rect, chip_radius, chip_radius)
        painter.setBrush(fg)
        painter.drawEllipse(QRect(dot_x, dot_y, dot_d, dot_d))

        painter.setPen(fg)
        painter.drawText(
            chip_rect.adjusted(text_left_pad, 0, -chip_right_pad, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            label,
        )
        painter.restore()

    def createEditor(self, parent, option, index):
        return None

    def editorEvent(self, event, model, option, index):
        if not index.isValid():
            return False
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            raw = index.data(Qt.EditRole) or index.data(Qt.DisplayRole) or "not started"
            current = str(raw).strip().lower()
            statuses = allowed_statuses()
            if current not in statuses:
                current = "not started"

            menu = QMenu(option.widget)
            actions = {}
            for status in statuses:
                action = menu.addAction(status)
                action.setCheckable(True)
                action.setChecked(status == current)
                actions[action] = status

            chosen = menu.exec(_menu_global_pos(event, option))
            if chosen in actions:
                model.setData(index, actions[chosen], Qt.EditRole)
            return True
        return super().editorEvent(event, model, option, index)


class TaskTypeDelegate(QStyledItemDelegate):
    OPTIONS = [
        ("", ""),
        ("ungraded", "ungraded"),
        ("bonus", "bonus"),
    ]

    def paint(self, painter, option, index):
        raw = normalize_task_type(index.data(Qt.EditRole) or index.data(Qt.DisplayRole) or "")
        labels = {value: label for value, label in self.OPTIONS}
        text = labels.get(raw, "—")
        rect = content_rect_for_cell(option, index, self.parent(), base_left=8, base_right=8)
        painter.save()
        font = painter.font()
        font.setBold(False)
        font.setItalic(False)
        painter.setFont(font)
        painter.setPen(_theme_text_color())
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    def createEditor(self, parent, option, index):
        return None

    def editorEvent(self, event, model, option, index):
        if not index.isValid():
            return False
        if (
            event.type() != QEvent.Type.MouseButtonRelease
            or event.button() != Qt.MouseButton.LeftButton
        ):
            return super().editorEvent(event, model, option, index)

        current = normalize_task_type(index.data(Qt.EditRole) or index.data(Qt.DisplayRole) or "")
        menu = QMenu(option.widget)
        actions = {}
        for value, label in self.OPTIONS:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(value == current)
            actions[action] = value

        chosen = menu.exec(_menu_global_pos(event, option))
        if chosen in actions:
            model.setData(index, actions[chosen], Qt.EditRole)
            return True
        return False


# --- Priority delegate ---
class PriorityDelegate(QStyledItemDelegate):
    """Menu-based priority picker to avoid accidental in-cell overlay editors."""

    PRIORITIES = ["low", "normal", "high"]

    def paint(self, painter, option, index):
        raw = index.data(Qt.DisplayRole) or index.data(Qt.EditRole) or "normal"
        rect = content_rect_for_cell(option, index, self.parent(), base_left=8, base_right=8)
        painter.save()
        font = painter.font()
        font.setBold(False)
        font.setItalic(False)
        painter.setFont(font)
        painter.setPen(_theme_text_color())
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(raw))
        painter.restore()

    def createEditor(self, parent, option, index):
        return None

    def editorEvent(self, event, model, option, index):
        if not index.isValid():
            return False
        if (
            event.type() != QEvent.Type.MouseButtonRelease
            or event.button() != Qt.MouseButton.LeftButton
        ):
            return super().editorEvent(event, model, option, index)

        raw = index.data(Qt.EditRole) or index.data(Qt.DisplayRole) or "normal"
        current = str(raw).strip().lower()
        if current not in self.PRIORITIES:
            current = "normal"

        menu = QMenu(option.widget)
        actions = {}
        for priority in self.PRIORITIES:
            action = menu.addAction(priority)
            action.setCheckable(True)
            action.setChecked(priority == current)
            actions[action] = priority

        chosen = menu.exec(_menu_global_pos(event, option))
        if chosen in actions:
            model.setData(index, actions[chosen], Qt.EditRole)
            return True
        return False


# --- Proxy relational delegate for course_id through proxy ---
class ProxyRelationalDelegate(QStyledItemDelegate):
    """
    Menu-based course picker that works with a proxy model without embedding a combobox editor.
    """
    PLACEHOLDER_TEXT = "Select course"

    def _current_course_id(self, source_model, source_index) -> int | None:
        if source_model is None or not source_index.isValid():
            return None

        raw_value = source_model.data(source_index, Qt.EditRole)
        try:
            cid = int(raw_value)
            if cid > 0:
                return cid
        except Exception:
            pass

        display_value = source_model.data(source_index, Qt.DisplayRole)
        course_name = "" if display_value is None else str(display_value).strip()
        if course_name:
            relation_model = source_model.relationModel(source_index.column())
            if relation_model is not None:
                relation_model.select()
                id_col = relation_model.fieldIndex("id")
                name_col = relation_model.fieldIndex("name")
                if id_col == -1:
                    id_col = 0
                if name_col == -1:
                    name_col = 1
                for row in range(relation_model.rowCount()):
                    name_raw = relation_model.data(relation_model.index(row, name_col))
                    if str(name_raw or "").strip() != course_name:
                        continue
                    try:
                        cid = int(relation_model.data(relation_model.index(row, id_col)))
                        if cid > 0:
                            return cid
                    except Exception:
                        continue

        try:
            task_id_col = source_model.fieldIndex("id")
        except Exception:
            task_id_col = -1
        if task_id_col != -1:
            task_id_value = source_model.data(source_model.index(source_index.row(), task_id_col), Qt.EditRole)
            try:
                task_id = int(task_id_value)
            except Exception:
                task_id = None
            if task_id is not None:
                q = QSqlQuery()
                q.prepare("SELECT course_id FROM tasks WHERE id = ?")
                q.addBindValue(task_id)
                if q.exec() and q.next():
                    try:
                        cid = int(q.value(0))
                        if cid > 0:
                            return cid
                    except Exception:
                        pass

        return None

    def paint(self, painter, option, index):
        raw = index.data(Qt.DisplayRole) or index.data(Qt.EditRole) or ""
        source_model = self.proxy_model.sourceModel() if self.proxy_model is not None else None
        source_index = self.proxy_model.mapToSource(index) if self.proxy_model is not None else QModelIndex()
        current_id = self._current_course_id(source_model, source_index)
        missing_course = current_id is None and str(raw).strip() == ""

        rect = content_rect_for_cell(option, index, self.parent(), base_left=8, base_right=8)
        painter.save()
        font = painter.font()
        font.setBold(False)
        font.setItalic(False)
        painter.setFont(font)
        if missing_course:
            painter.setPen(QColor(_theme_colors().get("error_text", "#B42318")))
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.PLACEHOLDER_TEXT)
        else:
            painter.setPen(_theme_text_color())
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(raw))
        painter.restore()

    def __init__(self, proxy_model, parent=None):
        super().__init__(parent)
        self.proxy_model = proxy_model

    def createEditor(self, parent, option, index):
        return None

    def editorEvent(self, event, model, option, index):
        if not index.isValid() or self.proxy_model is None:
            return False
        if (
            event.type() != QEvent.Type.MouseButtonRelease
            or event.button() != Qt.MouseButton.LeftButton
        ):
            return super().editorEvent(event, model, option, index)

        source_model = self.proxy_model.sourceModel()
        if source_model is None:
            return False

        source_index = self.proxy_model.mapToSource(index)
        if not source_index.isValid():
            return False

        rm = source_model.relationModel(source_index.column())
        if rm is None:
            return False
        rm.select()

        id_col = rm.fieldIndex("id")
        name_col = rm.fieldIndex("name")
        if id_col == -1:
            id_col = 0
        if name_col == -1:
            name_col = 1

        current_id = self._current_course_id(source_model, source_index)

        menu = QMenu(option.widget)
        actions = {}
        for row in range(rm.rowCount()):
            cid_raw = rm.data(rm.index(row, id_col))
            try:
                cid = int(cid_raw)
            except Exception:
                continue

            name_raw = rm.data(rm.index(row, name_col))
            text = str(name_raw).strip() if name_raw is not None else ""
            if not text:
                text = f"Course {cid}"

            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(current_id == cid)
            actions[action] = cid

        if not actions:
            return False

        chosen = menu.exec(_menu_global_pos(event, option))
        if chosen in actions:
            model.setData(index, actions[chosen], Qt.EditRole)
        return True





# --- Import overlay dialog ---

class DropOverlay(QDialog):
    """Modal overlay that accepts an Excel file via drag-and-drop."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.file_path: str | None = None
        colors = _theme_colors()
        dark = get_theme() == "dark"

        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)

        # Darkened backdrop
        backdrop = QFrame()
        backdrop.setStyleSheet("background: rgba(0,0,0,0.45);")
        backdrop_layout = QVBoxLayout()
        backdrop_layout.setContentsMargins(0, 0, 0, 0)
        backdrop_layout.addStretch(1)

        # Center drop zone
        zone = QFrame()
        zone.setObjectName("DropZone")
        zone.setFixedSize(520, 260)
        zone.setAcceptDrops(True)
        zone.setStyleSheet(
            "QFrame#DropZone {"
            f"  background: {'rgba(37,37,38,0.98)' if dark else 'rgba(255,255,255,0.96)'};"
            f"  border: 2px dashed {colors['input_border']};"
            "  border-radius: 14px;"
            "}"
        )

        z = QVBoxLayout()
        z.setContentsMargins(22, 22, 22, 22)
        z.setSpacing(10)
        title = QLabel("Import tasks")
        title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {colors['text_soft']};")
        hint = QLabel("Drag & drop an .xlsx file here\n(or click to choose a file)")
        hint.setStyleSheet(f"color: {colors['muted_text']};")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.msg = QLabel("")
        self.msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.msg.setStyleSheet(f"color: {colors['muted_text']};")

        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)

        z.addWidget(title, 0, Qt.AlignmentFlag.AlignCenter)
        z.addWidget(hint)
        z.addWidget(self.msg)
        z.addStretch(1)
        z.addLayout(btns)
        zone.setLayout(z)

        # Click-to-browse
        zone.mousePressEvent = self._browse  # type: ignore

        backdrop_layout.addWidget(zone, 0, Qt.AlignmentFlag.AlignCenter)
        backdrop_layout.addStretch(1)
        backdrop.setLayout(backdrop_layout)

        root.addWidget(backdrop)
        self.setLayout(root)

        self.setAcceptDrops(True)

    def _browse(self, event):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Excel file", "", "Excel Files (*.xlsx)")
        if path:
            self.file_path = path
            self.accept()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].toLocalFile().lower().endswith(".xlsx"):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path.lower().endswith(".xlsx"):
            self.file_path = path
            self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        # Ensure overlay covers parent
        if self.parent() is not None and isinstance(self.parent(), QWidget):
            self.setGeometry(self.parent().rect())


# --- Excel parsing + DB insert helpers ---

def _normalize_header(s: str) -> str:
    return "".join(ch.lower() for ch in s.strip() if ch.isalnum())


def _parse_due(value) -> str:
    """Return STORE_FMT yyyy-MM-dd HH:mm at 23:59."""
    if value is None:
        return ""

    # openpyxl can return datetime/date
    try:
        import datetime as _dt

        if isinstance(value, _dt.datetime):
            d = value.date()
            return f"{d.isoformat()} 23:59"
        if isinstance(value, _dt.date):
            return f"{value.isoformat()} 23:59"
    except Exception:
        pass

    s = str(value).strip()
    if not s:
        return ""

    # Try common formats
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y", "%b %d %Y", "%B %d %Y"):
        try:
            import datetime as _dt

            d = _dt.datetime.strptime(s, fmt).date()
            return f"{d.isoformat()} 23:59"
        except Exception:
            continue

    # If it's already like yyyy-mm-dd ...
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"{s[:10]} 23:59"

    return ""


def import_tasks_from_excel(path: str) -> tuple[int, int]:
    """Returns (courses_added, tasks_added). Raises RuntimeError on failure."""
    if load_workbook is None:
        raise RuntimeError("openpyxl is not available. Install it with: pip install openpyxl")

    wb = load_workbook(path)

    # Prefer a sheet named Tasks, else first sheet
    ws = wb["Tasks"] if "Tasks" in wb.sheetnames else wb.worksheets[0]

    # Find the header row (some templates have a title row before headers)
    header_row = None
    header_map: dict[str, int] = {}

    def _build_map(row_vals) -> dict[str, int]:
        m: dict[str, int] = {}
        for i, h in enumerate(row_vals):
            if h is None:
                continue
            key = _normalize_header(str(h))
            if key:
                m[key] = i
        return m

    def _col(hmap: dict[str, int], *names: str) -> int:
        for n in names:
            k = _normalize_header(n)
            if k in hmap:
                return hmap[k]
        return -1

    # Scan first 10 rows to locate a row that looks like headers
    for r in range(1, 11):
        row_vals = [c.value for c in next(ws.iter_rows(min_row=r, max_row=r))]
        m = _build_map(row_vals)
        cc = _col(m, "course", "coursename", "course name")
        ci = _col(m, "item", "assessment", "task", "task name", "assessment name")
        if cc != -1 and ci != -1:
            header_row = r
            header_map = m
            break

    if header_row is None:
        raise RuntimeError("Could not find a header row. Ensure the sheet has columns like Course and Item.")

    # Flexible header names (using detected header_map)
    c_course = _col(header_map, "course", "coursename", "course name")
    c_item = _col(header_map, "item", "assessment", "task", "task name", "assessment name")
    c_due = _col(header_map, "due", "duedate", "due date", "duedatetime", "due datetime")
    c_status = _col(header_map, "status")
    c_weight = _col(header_map, "weight", "weight%", "weightpercent", "weight percent")
    c_grade = _col(header_map, "grade", "grade%", "gradepercent", "grade percent")
    c_notes = _col(header_map, "notes", "note")

    if c_course == -1 or c_item == -1:
        raise RuntimeError("Excel must include at least a Course and Item column.")

    # DB helpers
    def ensure_course(name: str) -> tuple[int, bool]:
        name = name.strip()
        if not name:
            return -1, False
        q = QSqlQuery()
        q.prepare("SELECT id FROM courses WHERE name = ? LIMIT 1")
        q.addBindValue(name)
        if q.exec() and q.next():
            return int(q.value(0)), False
        q2 = QSqlQuery()
        q2.prepare("INSERT INTO courses(name, term, academic_year_start) VALUES(?, ?, ?)")
        q2.addBindValue(name)
        q2.addBindValue(_current_course_term())
        q2.addBindValue(_current_academic_year_start())
        if not q2.exec():
            raise RuntimeError(q2.lastError().text())
        q3 = QSqlQuery("SELECT last_insert_rowid()")
        if q3.next():
            return int(q3.value(0)), True
        # fallback
        q4 = QSqlQuery()
        q4.prepare("SELECT id FROM courses WHERE name = ? LIMIT 1")
        q4.addBindValue(name)
        if q4.exec() and q4.next():
            return int(q4.value(0)), True
        raise RuntimeError("Could not create course")

    courses_added = 0
    tasks_added = 0

    # Transaction for speed
    QSqlQuery("BEGIN")
    try:
        for row in ws.iter_rows(min_row=header_row + 1):
            vals = [c.value for c in row]

            course_name = "" if c_course == -1 else str(vals[c_course] or "").strip()
            item = "" if c_item == -1 else str(vals[c_item] or "").strip()

            if not course_name and not item:
                continue
            if not course_name or not item:
                # skip incomplete lines
                continue

            course_id, created = ensure_course(course_name)
            if created:
                courses_added += 1

            due_raw = "" if c_due == -1 else vals[c_due]
            due = _parse_due(due_raw)
            due = due or None

            status = "not started" if c_status == -1 else str(vals[c_status] or "not started").strip().lower()
            if status not in ("not started", "in progress", "submitted", "graded"):
                status = "not started"

            def _num(v):
                """Parse numeric fields.

                Excel often stores percent-formatted cells as decimals (0.25 == 25%).
                This converts values in [0, 1] to percent by multiplying by 100.
                """
                if v is None:
                    return None
                try:
                    if isinstance(v, str):
                        s = v.strip()
                        if s == "":
                            return None
                        is_pct = "%" in s
                        s = s.replace("%", "").strip()
                        x = float(s)
                        if not is_pct and 0.0 <= x <= 1.0:
                            return x * 100.0
                        return x

                    x = float(v)
                    if 0.0 <= x <= 1.0:
                        return x * 100.0
                    return x
                except Exception:
                    return None

            weight = None if c_weight == -1 else _num(vals[c_weight])
            grade = None
            if c_grade != -1:
                raw_grade = vals[c_grade]
                grade_text = "" if raw_grade is None else str(raw_grade).strip()
                if grade_text:
                    grade = _num(raw_grade)
                    if grade is None:
                        parsed_grade = parse_task_grade_input(raw_grade)
                        if parsed_grade is None:
                            raise RuntimeError(f"Invalid grade '{grade_text}' for task '{item}'.")
                        grade = parsed_grade[0]
            notes = "" if c_notes == -1 else str(vals[c_notes] or "")

            q = QSqlQuery()
            q.prepare(
                """
                INSERT INTO tasks(course_id, item, due_datetime, due_status, status, weight, grade, notes)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """
            )
            q.addBindValue(course_id)
            q.addBindValue(item)
            q.addBindValue(due)
            q.addBindValue("exact" if due else "unknown")
            q.addBindValue(status)
            q.addBindValue(weight)
            q.addBindValue(grade)
            q.addBindValue(notes)
            if not q.exec():
                raise RuntimeError(q.lastError().text())
            tasks_added += 1

        QSqlQuery("COMMIT")
    except Exception as e:
        QSqlQuery("ROLLBACK")
        raise

    return courses_added, tasks_added


class TasksPage(QWidget):
    """Tasks tab widget with an explicit refresh() to reload relations and rows."""
    changed = Signal()

    def _app_is_closing(self) -> bool:
        win = self.window()
        return bool(win is not None and win.property("_app_closing"))

    def _delegate_editor_for_widget(self, widget: QWidget | None) -> QWidget | None:
        table = getattr(self, "table", None)
        if table is None:
            return None
        current = widget
        while current is not None and current is not table:
            if bool(current.property("_tasks_delegate_editor")) and table.isAncestorOf(current):
                return current
            current = current.parentWidget()
        return None

    def _visible_delegate_editors(self) -> list[QWidget]:
        return visible_delegate_editors(getattr(self, "table", None))

    def _active_delegate_editor(self) -> QWidget | None:
        editor = self._delegate_editor_for_widget(QApplication.focusWidget())
        if editor is not None:
            return editor
        editors = self._visible_delegate_editors()
        return editors[0] if editors else None

    def _commit_current_editor(self) -> None:
        """Let the active inline editor finish naturally without force-closing it."""
        try:
            editor = self._delegate_editor_for_widget(QApplication.focusWidget())
            if editor is None:
                QApplication.processEvents()
                return
            self.table.setFocus(Qt.FocusReason.OtherFocusReason)
            QApplication.processEvents()
            if editor.isVisible() and editor.hasFocus():
                editor.clearFocus()
                QApplication.processEvents()
        except Exception:
            pass

    def _mark_refresh_after_submit(self) -> None:
        self._refresh_after_next_submit = True

    def _on_rows_inserted(self, *_args) -> None:
        self._mark_refresh_after_submit()
        self._schedule_autosave()

    def _on_rows_removed(self, *_args) -> None:
        self._mark_refresh_after_submit()
        self._schedule_autosave()

    def _post_submit_without_refresh(self, *, trigger_weight_banner: bool) -> None:
        try:
            self.proxy.clear_cache()
        except Exception:
            pass
        try:
            self.proxy.refresh_filter()
        except Exception:
            pass
        try:
            self.proxy.sort(0, Qt.SortOrder.AscendingOrder)
        except Exception:
            pass
        self.update_empty_state()
        try:
            self.table.viewport().update()
        except Exception:
            pass

    def _hide_status_move_banner(self) -> None:
        if hasattr(self, "status_banner"):
            self.status_banner.setVisible(False)

    def _status_move_banner_message(self, updates: list[dict]) -> str:
        if not updates:
            return ""
        first = updates[0]
        item_name = str(first.get("item_name", "") or "").strip() or "Task"
        status = str(first.get("status", "") or "").strip().lower() or "submitted"
        message = f"{item_name} moved to {status}."
        if len(updates) > 1:
            message += f" +{len(updates) - 1} more."
        return message

    def _show_status_move_banner(self, updates: list[dict]) -> None:
        message = self._status_move_banner_message(updates)
        if not message or not hasattr(self, "status_banner_label") or not self.isVisible():
            return
        self.status_banner_label.setText(message)
        self.status_banner.setVisible(True)
        if hasattr(self, "_status_banner_timer"):
            self._status_banner_timer.start()

    def _pending_status_banner_updates(self) -> list[dict]:
        id_col = self.model.fieldIndex("id")
        item_col = self.model.fieldIndex("item")
        status_col = self.model.fieldIndex("status")
        if id_col == -1 or item_col == -1 or status_col == -1:
            return []

        existing_statuses: dict[int, str] = {}
        q = QSqlQuery()
        if q.exec("SELECT id, lower(coalesce(status, '')) FROM tasks"):
            while q.next():
                try:
                    existing_statuses[int(q.value(0))] = str(q.value(1) or "").strip().lower()
                except Exception:
                    continue

        current_task_id = None
        current_index = self.table.currentIndex() if hasattr(self, "table") else QModelIndex()
        if current_index.isValid():
            source_index = self.proxy.mapToSource(current_index)
            if source_index.isValid():
                try:
                    current_task_id = int(self.model.data(self.model.index(source_index.row(), id_col), Qt.EditRole))
                except Exception:
                    current_task_id = None

        updates: list[dict] = []
        seen_task_ids: set[int] = set()
        for row in range(self.model.rowCount()):
            try:
                task_id = int(self.model.data(self.model.index(row, id_col), Qt.EditRole))
            except Exception:
                continue
            if task_id in seen_task_ids:
                continue

            previous_status = existing_statuses.get(task_id)
            if previous_status is None:
                continue

            current_status = self._normalize_status_value(self.model.data(self.model.index(row, status_col), Qt.EditRole))
            if current_status not in {"submitted", "graded"} or current_status == previous_status:
                continue

            item_name = str(self.model.data(self.model.index(row, item_col), Qt.EditRole) or "").strip()
            updates.append(
                {
                    "task_id": task_id,
                    "item_name": item_name or "Task",
                    "status": current_status,
                }
            )
            seen_task_ids.add(task_id)

        if current_task_id is not None:
            updates.sort(key=lambda row: 0 if int(row.get("task_id") or 0) == current_task_id else 1)
        return updates

    def _schedule_autosave(self) -> None:
        if getattr(self, "_suspend_autosave", False):
            return
        if hasattr(self, "_autosave_timer"):
            self._autosave_timer.start()

    def _normalize_status_value(self, value) -> str:
        text = ("" if value is None else str(value)).strip().lower()
        statuses = allowed_statuses()
        if text == "in progress" and not get_in_progress_enabled():
            return "not started"
        if text not in statuses:
            return "not started"
        return text

    def _to_float(self, value):
        if value is None:
            return None
        text = str(value).strip()
        if text == "":
            return None
        if text.endswith("%"):
            text = text[:-1].strip()
        text = text.replace(",", "")
        try:
            return float(text)
        except Exception:
            return None

    def _task_type_counts_toward_weights(self, row: int) -> bool:
        task_type_col = self.model.fieldIndex("task_type")
        if task_type_col != -1:
            task_type = normalize_task_type(self.model.data(self.model.index(row, task_type_col), Qt.EditRole))
            if task_type in {"ungraded", "bonus"}:
                return False

        legacy_ungraded_col = self.model.fieldIndex("ungraded")
        if legacy_ungraded_col != -1:
            legacy_ungraded = bool(self.model.data(self.model.index(row, legacy_ungraded_col), Qt.EditRole) or 0)
            if legacy_ungraded:
                return False

        return True

    def _course_name(self, cid: int) -> str:
        qn = QSqlQuery()
        qn.prepare("SELECT name FROM courses WHERE id = ?")
        qn.addBindValue(cid)
        if qn.exec() and qn.next():
            return str(qn.value(0) or f"Course {cid}")
        return f"Course {cid}"

    def _active_course_filter_id(self) -> int | None:
        if not hasattr(self, "course_filter"):
            return None
        try:
            current = self.course_filter.currentData()
        except Exception:
            return None
        if current is None:
            return None
        try:
            course_id = int(current)
        except Exception:
            return None
        return course_id if course_id > 0 else None

    def _course_id_for_row(self, row: int) -> int | None:
        course_col = self.course_id_col if getattr(self, "course_id_col", -1) != -1 else self.model.fieldIndex("course_id")
        if course_col != -1:
            raw_value = self.model.data(self.model.index(row, course_col), Qt.EditRole)
            try:
                cid = int(raw_value)
                if cid > 0:
                    return cid
            except Exception:
                pass

        task_id_col = self.model.fieldIndex("id")
        if task_id_col != -1:
            task_id_value = self.model.data(self.model.index(row, task_id_col), Qt.EditRole)
            try:
                task_id = int(task_id_value)
            except Exception:
                task_id = None

            if task_id is not None:
                q = QSqlQuery()
                q.prepare("SELECT course_id FROM tasks WHERE id = ?")
                q.addBindValue(task_id)
                if q.exec() and q.next():
                    try:
                        return int(q.value(0))
                    except Exception:
                        pass

        if course_col != -1:
            display_value = self.model.data(self.model.index(row, course_col), Qt.DisplayRole)
            course_name = "" if display_value is None else str(display_value).strip()
            if course_name:
                q = QSqlQuery()
                q.prepare("SELECT id FROM courses WHERE name = ? LIMIT 1")
                q.addBindValue(course_name)
                if q.exec() and q.next():
                    try:
                        return int(q.value(0))
                    except Exception:
                        pass

        return None

    def _focus_source_cell(self, row: int, col: int, *, start_edit: bool = True) -> None:
        src_idx = self.model.index(row, col)
        px_idx = self.proxy.mapFromSource(src_idx)
        target = px_idx if px_idx.isValid() else src_idx
        self.table.setCurrentIndex(target)
        self.table.scrollTo(target)
        if start_edit:
            self.table.edit(target)

    def _validate_before_submit(self, *, show_errors: bool) -> bool:
        item_col = self.model.fieldIndex("item")
        course_col = self.course_id_col if getattr(self, "course_id_col", -1) != -1 else self.model.fieldIndex("course_id")
        due_col = self.model.fieldIndex("due_datetime")
        due_status_col = self.model.fieldIndex("due_status")
        status_col = self.model.fieldIndex("status")
        weight_col = self.model.fieldIndex("weight")
        grade_col = self.model.fieldIndex("grade")
        task_type_col = self.model.fieldIndex("task_type")
        legacy_ungraded_col = self.model.fieldIndex("ungraded")

        for r in range(self.model.rowCount()):
            if course_col != -1:
                has_course = self._course_id_for_row(r) is not None
                if not has_course:
                    if show_errors:
                        QMessageBox.critical(self.table, "Save failed", "Every task must have a Course selected.")
                        self._focus_source_cell(r, course_col, start_edit=False)
                    return False

            if item_col != -1:
                v = self.model.data(self.model.index(r, item_col), Qt.EditRole)
                if v is None or str(v).strip() == "":
                    # Don't block the user - just mark row as invalid visually
                    # Only show error if explicitly saving (when called with show_errors=True)
                    if show_errors:
                        QMessageBox.critical(self.table, "Save failed", "Every task must have an Item (name).")
                        self._focus_source_cell(r, item_col)
                    # Always return False to prevent save, but don't block editing
                    return False

            if status_col != -1:
                normalized_status = self._normalize_status_value(self.model.data(self.model.index(r, status_col), Qt.EditRole))
                self.model.setData(self.model.index(r, status_col), normalized_status, Qt.EditRole)

            task_type = ""
            if task_type_col != -1:
                task_type = normalize_task_type(self.model.data(self.model.index(r, task_type_col), Qt.EditRole))
                self.model.setData(self.model.index(r, task_type_col), task_type, Qt.EditRole)

            legacy_ungraded = False
            if legacy_ungraded_col != -1:
                legacy_ungraded = bool(self.model.data(self.model.index(r, legacy_ungraded_col), Qt.EditRole) or 0)

            is_ungraded = task_type == "ungraded" or legacy_ungraded

            if legacy_ungraded_col != -1:
                self.model.setData(self.model.index(r, legacy_ungraded_col), 1 if is_ungraded else 0, Qt.EditRole)

            if is_ungraded and weight_col != -1:
                self.model.setData(self.model.index(r, weight_col), None, Qt.EditRole)

            if due_col != -1:
                due_value = self.model.data(self.model.index(r, due_col), Qt.EditRole)
                has_due = due_value is not None and bool(str(due_value).strip())
                if due_status_col != -1:
                    raw_due_status = str(
                        self.model.data(self.model.index(r, due_status_col), Qt.EditRole) or ""
                    ).strip().lower()
                    due_status = "exact" if has_due and raw_due_status not in {"approximate", "inferred"} else raw_due_status
                    if not has_due:
                        due_status = "unknown"
                    self.model.setData(
                        self.model.index(r, due_status_col),
                        due_status or ("exact" if has_due else "unknown"),
                        Qt.EditRole,
                    )

            has_grade = False
            if grade_col != -1:
                raw_grade = self.model.data(self.model.index(r, grade_col), Qt.EditRole)
                raw_grade_text = "" if raw_grade is None else str(raw_grade).strip()
                if raw_grade_text == "":
                    self.model.setData(self.model.index(r, grade_col), None, Qt.EditRole)
                else:
                    parsed_grade = parse_task_grade_input(raw_grade)
                    if parsed_grade is None:
                        if show_errors:
                            QMessageBox.critical(
                                self.table,
                                "Save failed",
                                "Grade must be a percent or a grade label from your scale.",
                            )
                            self._focus_source_cell(r, grade_col)
                        return False
                    normalized_grade, _grade_kind = parsed_grade
                    self.model.setData(self.model.index(r, grade_col), normalized_grade, Qt.EditRole)
                    has_grade = True

            if grade_col != -1 and status_col != -1:
                s = self._normalize_status_value(self.model.data(self.model.index(r, status_col), Qt.EditRole))
                if has_grade:
                    if s != "graded":
                        self.model.setData(self.model.index(r, status_col), "graded", Qt.EditRole)
                elif s == "graded":
                    self.model.setData(self.model.index(r, status_col), "submitted", Qt.EditRole)

        return True

    def _submit_changes(
        self,
        *,
        show_errors: bool,
        trigger_weight_banner: bool = False,
        refresh_after_save: bool | None = None,
    ) -> bool:
        self._commit_current_editor()
        if not self._validate_before_submit(show_errors=show_errors):
            return False
        status_banner_updates = self._pending_status_banner_updates()
        if self.model.submitAll():
            should_refresh = (
                getattr(self, "_refresh_after_next_submit", False)
                if refresh_after_save is None
                else bool(refresh_after_save)
            )
            self._refresh_after_next_submit = False
            if should_refresh:
                self.refresh()
            else:
                self._post_submit_without_refresh(trigger_weight_banner=trigger_weight_banner)
            self.changed.emit()
            if status_banner_updates:
                self._show_status_move_banner(status_banner_updates)
            return True
        if show_errors:
            QMessageBox.critical(self.table, "Save failed", self.model.lastError().text())
        return False

    def _autosave(self) -> None:
        if self._app_is_closing():
            return
        if self.table.state() == QAbstractItemView.State.EditingState:
            self._autosave_timer.start()
            return
        if self._visible_delegate_editors():
            self._autosave_timer.start()
            return
        if not self.model.isDirty():
            return
        self._submit_changes(show_errors=False, trigger_weight_banner=False)

    def _apply_status_filter_options(self) -> None:
        current = self.status_filter.currentData()
        self.status_filter.blockSignals(True)
        self.status_filter.clear()
        self.status_filter.addItem("all statuses", None)
        for status in allowed_statuses():
            self.status_filter.addItem(status, status)
        idx = self.status_filter.findData(current)
        self.status_filter.setCurrentIndex(idx if idx != -1 else 0)
        self.status_filter.blockSignals(False)

    def apply_settings(self) -> None:
        """Apply settings that affect the Tasks UI."""
        if hasattr(self, "table"):
            mode = get_font_size_mode()
            theme = get_theme()
            colors = theme_colors(theme)
            self._apply_status_filter_options()
            apply_body_font(self, mode)
            self.setStyleSheet(
                common_page_stylesheet(mode, theme=theme, title_px=20)
                +
                f"""
                QFrame#Card {{
                    background: {colors['card_bg']};
                    border: 1px solid {colors['border_soft']};
                    border-radius: 18px;
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
                QTableView::item,
                QTableView::item:selected,
                QTableView::item:focus,
                QTableView::item:hover,
                QTableView::item:selected:hover,
                QAbstractItemView::item,
                QAbstractItemView::item:hover,
                QAbstractItemView::item:selected,
                QAbstractItemView::item:selected:hover {{
                    padding: 0px;
                    background: transparent;
                    border: none;
                    outline: none;
                    color: {colors['text']};
                }}
                QHeaderView::section {{
                    background: transparent;
                    padding: 10px 12px;
                    border: none;
                    border-bottom: 1px solid {colors['border_soft']};
                    font-weight: 600;
                    font-size: 11px;
                    color: {colors['header_text']};
                }}
                QLabel#ErrorBanner {{
                    background: {colors['error_bg']};
                    color: {colors['error_text']};
                    border: 1px solid {colors['error_border']};
                    border-radius: 10px;
                    padding: 8px 12px;
                    font-weight: 700;
                }}
                QPushButton#BannerDismissButton {{
                    background: {colors['secondary_bg']};
                    color: {colors['error_text']};
                    border: 1px solid {colors['error_border']};
                    border-radius: 10px;
                    padding: 7px 12px;
                    font-weight: 700;
                    min-width: 0px;
                }}
                QPushButton#BannerDismissButton:hover {{
                    background: {colors['danger_bg_hover']};
                    border: 1px solid {colors['error_border']};
                }}
                QPushButton#BannerDismissButton:pressed {{
                    background: {colors['danger_bg_pressed']};
                }}
                QFrame#StatusMoveBanner {{
                    background: {"rgba(224, 245, 230, 0.96)" if theme == "light" else "rgba(33, 79, 52, 0.92)"};
                    border: 1px solid {"rgba(48, 167, 88, 0.35)" if theme == "light" else "rgba(110, 214, 145, 0.35)"};
                    border-radius: 999px;
                }}
                QLabel#StatusMoveBannerText {{
                    color: {"#1F6F3D" if theme == "light" else "#D6F5E0"};
                    font-weight: 700;
                    padding: 7px 14px;
                }}
                """
            )
            self.table.verticalHeader().setDefaultSectionSize(
                scaled_row_height(mode, compact=get_compact_rows(), compact_px=38, regular_px=42)
            )
            QTimer.singleShot(0, self.apply_column_layout)

    def update_empty_state(self) -> None:
        has_tasks = self.model.rowCount() > 0
        self.table.setVisible(has_tasks)
        self.empty_label.setVisible(not has_tasks)

    def __init__(self) -> None:
        super().__init__()
        self._prefs = app_qsettings()
        self._syllabus_import_flow = SyllabusImportFlow(self)
        self._syllabus_import_flow.imported.connect(self.refresh)
        self._syllabus_import_flow.imported.connect(self.changed.emit)

        title = QLabel("Tasks")
        title.setObjectName("Title")
        subtitle = QLabel("Add assignments and track status, due dates, grades, and more.")
        subtitle.setObjectName("Subtitle")
        self._status_banner_timer = QTimer(self)
        self._status_banner_timer.setSingleShot(True)
        self._status_banner_timer.setInterval(2400)
        self._status_banner_timer.timeout.connect(self._hide_status_move_banner)

        self.status_banner = QFrame()
        self.status_banner.setObjectName("StatusMoveBanner")
        self.status_banner.setVisible(False)
        self.status_banner_label = QLabel("")
        self.status_banner_label.setObjectName("StatusMoveBannerText")
        status_banner_layout = QHBoxLayout()
        status_banner_layout.setContentsMargins(0, 0, 0, 0)
        status_banner_layout.setSpacing(0)
        status_banner_layout.addWidget(self.status_banner_label)
        self.status_banner.setLayout(status_banner_layout)

        # Filters
        self.course_filter = QComboBox()
        self.course_filter.setMinimumWidth(220)
        self.course_filter.addItem("all courses", None)

        self.status_filter = QComboBox()
        self.status_filter.setMinimumWidth(180)
        self._apply_status_filter_options()
        
        # Restore status filter preference if available
        saved_status = self._prefs.value("tasks/status", "", type=str)
        if saved_status:
            for i in range(self.status_filter.count()):
                if self.status_filter.itemData(i) == saved_status:
                    self.status_filter.setCurrentIndex(i)
                    break

        self.hide_ungraded = SwitchCheckBox("Hide ungraded")
        self.hide_ungraded.setChecked(self._prefs.value("tasks/hide_ungraded", False, type=bool))
        self.hide_ungraded.setFixedWidth(self.hide_ungraded.sizeHint().width())

        self.course_filter.currentIndexChanged.connect(lambda _=None: self.apply_filters())
        self.status_filter.currentIndexChanged.connect(lambda _=None: self.apply_filters())
        self.hide_ungraded.toggled.connect(lambda _=None: self.apply_filters())

        # 1) Relational model so course_id/project_id can be dropdowns.
        self.model = QSqlRelationalTableModel(self)
        self.model.setTable("tasks")

        # 2) Relations
        self.course_id_col = self.model.fieldIndex("course_id")
        if self.course_id_col != -1:
            self.model.setRelation(self.course_id_col, QSqlRelation("courses", "id", "name"))


        # 3) Manual submit
        self.model.setEditStrategy(QSqlRelationalTableModel.EditStrategy.OnManualSubmit)
        self.model.select()
        self._suspend_autosave = False
        self._refresh_after_next_submit = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(220)
        self._autosave_timer.timeout.connect(self._autosave)
        self.model.dataChanged.connect(lambda *_args: self._schedule_autosave())
        self.model.rowsInserted.connect(self._on_rows_inserted)
        self.model.rowsRemoved.connect(self._on_rows_removed)

        # 4) Table view
        self.table = TasksTableView()
        self.proxy = TasksFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table.setModel(self.proxy)
        transparent_brush = QBrush(Qt.GlobalColor.transparent)
        table_palette = QPalette(self.table.palette())
        for role in (
            QPalette.ColorRole.Base,
            QPalette.ColorRole.Window,
            QPalette.ColorRole.AlternateBase,
            QPalette.ColorRole.Highlight,
        ):
            table_palette.setBrush(role, transparent_brush)
        table_palette.setColor(QPalette.ColorRole.HighlightedText, QColor(_theme_colors()["text"]))
        self.table.setPalette(table_palette)
        self.table.viewport().setPalette(table_palette)
        # Use the same hover/focus suppression style on the view and viewport.
        self._table_card_style = CardTableStyle()
        self._table_card_style.setParent(self.table)
        self.table.setStyle(self._table_card_style)
        self._viewport_card_style = CardTableStyle()
        self._viewport_card_style.setParent(self.table.viewport())
        self.table.viewport().setStyle(self._viewport_card_style)
        self.table.setSortingEnabled(True)
        # Row card and custom delegate painting handle all visual rendering.
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setTabKeyNavigation(True)
        self.table.setCornerButtonEnabled(False)
        # Single-click selects rows; editing requires an intentional double-click or keyboard edit.
        self.table.setEditTriggers(
            QTableView.EditTrigger.DoubleClicked
            | QTableView.EditTrigger.EditKeyPressed
        )
        # Determine which columns are visible (not hidden)
        item_col = self.model.fieldIndex("item")
        due_col = self.model.fieldIndex("due_datetime")
        status_col = self.model.fieldIndex("status")
        priority_col = self.model.fieldIndex("priority")
        weight_col = self.model.fieldIndex("weight")
        grade_col = self.model.fieldIndex("grade")
        task_type_col = self.model.fieldIndex("task_type")
        notes_col = self.model.fieldIndex("notes")
        
# Apply card delegate to the table as default (row separation styling)
        # This draws rounded cards with padding and handles row selected outline.
        first_visible = self.course_id_col if self.course_id_col != -1 else item_col
        last_visible = task_type_col if task_type_col != -1 else item_col

        card_delegate = RowCardDelegate(self.table, first_visible, last_visible)
        self.table.setItemDelegate(card_delegate)

        # Optional: per-column delegate overrides for editor specialization.
        # Row background remains from the shared row card logic via RowCardDelegate.
        
        # Status should be a dropdown (not a text editor)
        if status_col != -1:
            self.table.setItemDelegateForColumn(status_col, StatusDelegate(self.table))

        # Priority dropdown (if priority column exists)
        if priority_col != -1:
            self.table.setItemDelegateForColumn(priority_col, PriorityDelegate(self.table))
        self.priority_col = priority_col

        if task_type_col != -1:
            self.table.setItemDelegateForColumn(task_type_col, TaskTypeDelegate(self.table))

        # Due date editor. Tab out of it jumps straight to Weight, skipping the
        # click-only Status cell (it has no keyboard editor of its own).
        if due_col != -1:
            tab_target = weight_col if weight_col != -1 else -1
            self.table.setItemDelegateForColumn(due_col, DueDateTimeDelegate(self.table, tab_target_column=tab_target))

        # Text cells use inset rounded editors so editing doesn't look like a floating white block.
        if item_col != -1:
            self.table.setItemDelegateForColumn(item_col, InlineTextDelegate(self.table, placeholder_text="Task name"))

        # Weight stays percent-only; grade accepts either percentages or letter grades.
        if weight_col != -1:
            self.table.setItemDelegateForColumn(weight_col, PercentDisplayDelegate(self.table))

        if grade_col != -1:
            self.table.setItemDelegateForColumn(grade_col, TaskGradeDelegate(self.table))
        
        # Course column: use relational delegate
        if self.course_id_col != -1:
            self.table.setItemDelegateForColumn(self.course_id_col, ProxyRelationalDelegate(self.proxy, self.table))

        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Completely disable hover handling on the viewport to prevent grey hover rectangles.
        self.table.setMouseTracking(False)
        self.table.viewport().setMouseTracking(False)
        self.table.setAttribute(Qt.WidgetAttribute.WA_Hover, False)
        self.table.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, False)
        self.table.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.table.viewport().setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.table.viewport().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.viewport().installEventFilter(self)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QTableView.EditTrigger.DoubleClicked
            | QTableView.EditTrigger.EditKeyPressed
        )

        # Aesthetics
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38 if get_compact_rows() else 42)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)


        #hh = self.table.horizontalHeader()
        #hh.setStretchLastSection(True)
        #hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        # Do NOT allow user resizing by default; per-column modes are applied in apply_headers().
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        hh.setSectionsClickable(False)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setSectionsMovable(False)

        # Column sizing is handled centrally in apply_headers() after filters/refresh.
        # No per-column delegates: unified delegate handles all editors/painting

        # # Ensure relational dropdown columns use the wide delegate
        # if self.course_id_col != -1:
        #     self.table.setItemDelegateForColumn(self.course_id_col, WideRelationalDelegate(self.table))

        # Hide non-user columns
        for field in ["id", "created_at", "updated_at", "ungraded", "component", "due_status", "weight_raw_value", "weight_raw_unit", "weight_source", "syllabus_metadata", "notes"]:
            col = self.model.fieldIndex(field)
            if col != -1:
                self.table.setColumnHidden(col, True)

        # Hide priority column if disabled in settings
        if priority_col != -1 and not get_priority_enabled():
            self.table.setColumnHidden(priority_col, True)

        # Friendly headers
        if self.course_id_col != -1:
            self.model.setHeaderData(self.course_id_col, Qt.Horizontal, "Course")

        def _hdr(field: str, title_txt: str) -> None:
            col = self.model.fieldIndex(field)
            if col != -1:
                self.model.setHeaderData(col, Qt.Horizontal, title_txt)

        _hdr("item", "Item")
        _hdr("due_datetime", "Due")
        _hdr("status", "Status")
        _hdr("priority", "Priority")
        _hdr("weight", "Weight (%)")
        _hdr("grade", "Grade")
        _hdr("task_type", "Type")

        # Buttons
        btn_add = QPushButton("Add")
        btn_add.setObjectName("PrimaryButton")
        btn_duplicate = QPushButton("Duplicate")
        btn_duplicate.setObjectName("SecondaryButton")
        btn_import_syllabus = QPushButton("Import from syllabus")
        btn_import_syllabus.setObjectName("SecondaryButton")
        btn_delete = QPushButton("Delete")
        btn_delete.setObjectName("DangerButton")
        btn_duplicate.setEnabled(False)
        btn_delete.setEnabled(False)

        def update_row_action_state() -> None:
            selection = self.table.selectionModel()
            has_selection = bool(selection is not None and selection.selectedRows())
            btn_duplicate.setEnabled(has_selection)
            btn_delete.setEnabled(has_selection)

        selection_model = self.table.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(lambda *_args: update_row_action_state())

        def add_row() -> None:
            self._suspend_autosave = True
            if hasattr(self, "_autosave_timer"):
                self._autosave_timer.stop()
            try:
                # Insert row
                self.model.insertRow(self.model.rowCount())
                row = self.model.rowCount() - 1
                self.update_empty_state()

                # If the user is scoped to a course, inherit that course on the new task.
                active_course_id = self._active_course_filter_id()
                if self.course_id_col != -1:
                    self.model.setData(
                        self.model.index(row, self.course_id_col),
                        active_course_id,
                        Qt.EditRole,
                    )

                # Keep the task-name placeholder visible until the user types a real name.
                item_col = self.model.fieldIndex("item")
                if item_col != -1:
                    self.model.setData(self.model.index(row, item_col), "", Qt.EditRole)

                # Qt initializes REAL columns to 0.0 on inserted rows; weight should start empty too.
                weight_col2 = self.model.fieldIndex("weight")
                if weight_col2 != -1:
                    self.model.setData(self.model.index(row, weight_col2), None, Qt.EditRole)

                # Qt initializes REAL columns to 0.0 on inserted rows; grade should start empty.
                grade_col2 = self.model.fieldIndex("grade")
                if grade_col2 != -1:
                    self.model.setData(self.model.index(row, grade_col2), None, Qt.EditRole)

                # Default status for new rows
                status_col2 = self.model.fieldIndex("status")
                if status_col2 != -1:
                    self.model.setData(self.model.index(row, status_col2), get_default_status(), Qt.EditRole)

                # New tasks remain intentionally undated until the user chooses a due date.
                due_col2 = self.model.fieldIndex("due_datetime")
                if due_col2 != -1:
                    self.model.setData(self.model.index(row, due_col2), None, Qt.EditRole)
                due_status_col2 = self.model.fieldIndex("due_status")
                if due_status_col2 != -1:
                    self.model.setData(self.model.index(row, due_status_col2), "unknown", Qt.EditRole)

                # Refresh proxy so new row appears, then edit
                if item_col != -1:
                    self.proxy.refresh_filter()
                    def _do_edit():
                        src_idx = self.model.index(row, item_col)
                        px_idx = self.proxy.mapFromSource(src_idx)
                        if px_idx.isValid():
                            self.table.setCurrentIndex(px_idx)
                            self.table.edit(px_idx)
                        else:
                            # Fallback: just select the source row and try to edit
                            self.table.setCurrentIndex(px_idx)
                    QTimer.singleShot(0, _do_edit)
            finally:
                self._suspend_autosave = False

        # import_excel removed

        def duplicate_row() -> None:
            self._suspend_autosave = True
            if hasattr(self, "_autosave_timer"):
                self._autosave_timer.stop()
            try:
                # Duplicate the currently selected row (source model), including course_id.
                px = self.table.currentIndex()
                if not px.isValid():
                    return

                src_idx0 = self.proxy.mapToSource(px)
                if not src_idx0.isValid():
                    return

                src_row = src_idx0.row()

                # Resolve the raw course FK robustly.
                def _course_fk_for_row(r: int) -> int | None:
                    if self.course_id_col == -1:
                        return None

                    # First try EditRole (should be FK).
                    fk = self.model.data(self.model.index(r, self.course_id_col), Qt.EditRole)
                    try:
                        return int(fk)
                    except Exception:
                        pass

                    # Fall back to DisplayRole (course name) -> lookup id.
                    name = self.model.data(self.model.index(r, self.course_id_col), Qt.DisplayRole)
                    name = "" if name is None else str(name).strip()
                    if not name:
                        return None

                    q = QSqlQuery()
                    q.prepare("SELECT id FROM courses WHERE name = ? LIMIT 1")
                    q.addBindValue(name)
                    if q.exec() and q.next():
                        try:
                            return int(q.value(0))
                        except Exception:
                            return None
                    return None

                src_course_fk = _course_fk_for_row(src_row)

                # Insert new row at end
                self.model.insertRow(self.model.rowCount())
                dst_row = self.model.rowCount() - 1

                # Copy fields we care about
                fields_to_copy = [
                    "course_id",
                    "item",
                    "component",
                    "due_datetime",
                    "due_status",
                    "status",
                    "weight",
                    "priority",
                    "task_type",
                    "notes",
                    "weight_raw_value",
                    "weight_raw_unit",
                    "weight_source",
                    "syllabus_metadata",
                ]

                for f in fields_to_copy:
                    col = self.model.fieldIndex(f)
                    if col == -1:
                        continue

                    if f == "course_id":
                        val = src_course_fk
                    else:
                        # Use EditRole so we copy raw values where possible
                        val = self.model.data(self.model.index(src_row, col), Qt.EditRole)

                    if f == "item" and (val is None or str(val).strip() == ""):
                        val = ""

                    self.model.setData(self.model.index(dst_row, col), val, Qt.EditRole)

                # Force course_id once more (helps relational display update)
                if self.course_id_col != -1 and src_course_fk is not None:
                    self.model.setData(self.model.index(dst_row, self.course_id_col), src_course_fk, Qt.EditRole)

                # Ensure duplicate stays visible under Active-only filter
                status_col = self.model.fieldIndex("status")
                if status_col != -1:
                    self.model.setData(self.model.index(dst_row, status_col), "not started", Qt.EditRole)

                grade_col2 = self.model.fieldIndex("grade")
                if grade_col2 != -1:
                    self.model.setData(self.model.index(dst_row, grade_col2), None, Qt.EditRole)

                # Preserve an intentionally missing due date on duplicated tasks.
                due_col = self.model.fieldIndex("due_datetime")
                if due_col != -1:
                    due_val = self.model.data(self.model.index(dst_row, due_col), Qt.EditRole)
                    if due_val is None or str(due_val).strip() == "":
                        self.model.setData(self.model.index(dst_row, due_col), None, Qt.EditRole)
                        due_status_col = self.model.fieldIndex("due_status")
                        if due_status_col != -1:
                            self.model.setData(self.model.index(dst_row, due_status_col), "unknown", Qt.EditRole)
            finally:
                self._suspend_autosave = False

            # Update view filter without reloading the SQL model
            self.proxy.refresh_filter()

            # Focus the new row's Item cell
            item_col2 = self.model.fieldIndex("item")
            if item_col2 != -1:
                def _do_edit_dupe():
                    src_item = self.model.index(dst_row, item_col2)
                    px_item = self.proxy.mapFromSource(src_item)
                    if px_item.isValid():
                        self.table.setCurrentIndex(px_item)
                        self.table.edit(px_item)
                        self.table.scrollTo(px_item)
                QTimer.singleShot(0, _do_edit_dupe)

        def delete_row() -> None:
            sel = self.table.selectionModel()
            if sel is None:
                return

            rows = sel.selectedRows()
            if not rows:
                # Fall back to current row
                px = self.table.currentIndex()
                if px.isValid():
                    rows = [px]

            # Map proxy rows -> source rows
            src_rows = set()
            for idx in rows:
                if not idx.isValid():
                    continue
                src = self.proxy.mapToSource(idx)
                if src.isValid():
                    src_rows.add(src.row())

            if not src_rows:
                return

            count = len(src_rows)
            msg = "Delete the selected task?" if count == 1 else f"Delete {count} selected tasks?"
            if get_confirm_delete():
                res = QMessageBox.question(
                    self.table,
                    "Delete row" if count == 1 else "Delete rows",
                    msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if res != QMessageBox.StandardButton.Yes:
                    return

            # Remove from bottom up so indices don't shift
            for r in sorted(src_rows, reverse=True):
                self.model.removeRow(r)

            # Commit deletion immediately so it doesn't wait for Save
            if self.model.submitAll():
                self.refresh()
                self.changed.emit()
                return

            err = self.model.lastError().text()
            self.model.revertAll()
            self.refresh()
            QMessageBox.critical(self.table, "Delete failed", err)

        btn_add.clicked.connect(add_row)
        btn_duplicate.clicked.connect(duplicate_row)
        btn_import_syllabus.clicked.connect(self._syllabus_import_flow.start)
        btn_delete.clicked.connect(delete_row)
        
        self.shortcut_add_task = QShortcut(QKeySequence("Shift+N"), self, activated=add_row)
        self.shortcut_duplicate_task = QShortcut(QKeySequence("Shift+D"), self, activated=duplicate_row)
        self.shortcut_delete_task = QShortcut(QKeySequence("Delete"), self, activated=delete_row)

        # Keep the title and actions on separate rows so the toolbar remains legible
        # in compact windows and beside the app sidebar.
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        row1.addWidget(title)
        row1.addStretch(1)

        row2 = QHBoxLayout()
        row2.addWidget(subtitle)
        row2.addStretch(1)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(btn_add)
        action_row.addWidget(btn_duplicate)
        action_row.addWidget(btn_import_syllabus)
        action_row.addWidget(btn_delete)
        action_row.addStretch(1)

        row3 = QHBoxLayout()
        row3.setSpacing(10)
        row3.addWidget(QLabel("Course:"))
        row3.addWidget(self.course_filter)
        row3.addSpacing(10)
        row3.addWidget(QLabel("Status:"))
        row3.addWidget(self.status_filter)
        row3.addSpacing(10)
        row3.addWidget(self.hide_ungraded)
        row3.addStretch(1)

        header = QVBoxLayout()
        header.setSpacing(8)
        header.addLayout(row1)
        header.addLayout(row2)
        header.addLayout(action_row)
        header.addLayout(row3)
        banner_row = QHBoxLayout()
        banner_row.setContentsMargins(0, 0, 0, 0)
        banner_row.addStretch(1)
        banner_row.addWidget(self.status_banner)
        header.addLayout(banner_row)

        # Card
        self.empty_label = QLabel("No tasks saved")
        self.empty_label.setObjectName("EmptyState")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setMinimumHeight(240)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(10, 10, 10, 12)
        card_layout.addWidget(self.table)
        card_layout.addWidget(self.empty_label)
        card.setLayout(card_layout)

        root = QVBoxLayout()
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(14)
        root.addLayout(header)
        root.addWidget(card, 1)
        self.setLayout(root)
        self.apply_settings()
        self.refresh()
    
    def eventFilter(self, obj, event):
        if hasattr(self, "table") and obj is self.table.viewport():
            if event.type() in (
                QEvent.Type.HoverEnter,
                QEvent.Type.HoverMove,
                QEvent.Type.HoverLeave,
            ):
                return True

            if event.type() == QEvent.Type.MouseButtonPress:
                try:
                    pos = event.position().toPoint()
                except Exception:
                    pos = event.pos()
                idx = self.table.indexAt(pos)
                if not idx.isValid():
                    self._commit_current_editor()
                    sel = self.table.selectionModel()
                    if sel is not None:
                        sel.clearSelection()
                    self.table.setCurrentIndex(QModelIndex())
                    self.table.viewport().update()
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Apply layout after the widget is actually on-screen
        QTimer.singleShot(0, self.apply_column_layout)
    def hideEvent(self, event) -> None:
        try:
            app_closing = self._app_is_closing()
            self._hide_status_move_banner()
            self._commit_current_editor()
            if self.model.isDirty():
                if not self._submit_changes(show_errors=not app_closing, trigger_weight_banner=False) and not app_closing:
                    QMessageBox.warning(self, "Auto-save failed", "Some task changes still need attention before they can be saved.")
        except Exception:
            pass
        super().hideEvent(event)

    def populate_course_filter(self) -> None:
        """Populate the course filter dropdown from the courses relation model."""
        current_id = self.course_filter.currentData()
        
        # If no current selection and loading for first time, try to restore from preferences
        if current_id is None:
            saved_id = self._prefs.value("tasks/course_id", "", type=str)
            if saved_id and saved_id != "":
                try:
                    current_id = int(saved_id)
                except Exception:
                    current_id = None

        self.course_filter.blockSignals(True)
        self.course_filter.clear()
        self.course_filter.addItem("all courses", None)

        if self.course_id_col != -1:
            rm = self.model.relationModel(self.course_id_col)
            if rm is not None:
                rm.select()
                # relation model columns are (id, name, ...)
                id_col = rm.fieldIndex("id")
                name_col = rm.fieldIndex("name")
                if id_col == -1:
                    id_col = 0
                if name_col == -1:
                    name_col = 1

                for r in range(rm.rowCount()):
                    cid = rm.data(rm.index(r, id_col))
                    cname = rm.data(rm.index(r, name_col))
                    self.course_filter.addItem(str(cname), int(cid))

        # Restore selection if possible
        if current_id is not None:
            try:
                current_id_int = int(current_id)
            except Exception:
                current_id_int = None
            if current_id_int is not None:
                for i in range(self.course_filter.count()):
                    if self.course_filter.itemData(i) == current_id_int:
                        self.course_filter.setCurrentIndex(i)
                        break

        self.course_filter.blockSignals(False)

    def apply_filters(self) -> None:
        self._commit_current_editor()
        cid = self.course_filter.currentData()
        self.proxy.course_id = None if cid is None else int(cid)
        # Also store the displayed course name for robust matching
        name = self.course_filter.currentText()
        self.proxy.course_name = None if cid is None else str(name)

        s = self.status_filter.currentData()
        self.proxy.status_filter = None if s is None else str(s)

        # Clear cached FK lookups (safe)
        self.proxy.clear_cache()

        self.proxy.hide_ungraded = self.hide_ungraded.isChecked()

        # Save preferences to QSettings
        self._prefs.setValue("tasks/course_id", cid if cid is not None else "")
        self._prefs.setValue("tasks/status", s if s is not None else "")
        self._prefs.setValue("tasks/hide_ungraded", self.hide_ungraded.isChecked())

        self.proxy.refresh_filter()

        # Re-sort so submitted stays at the bottom
        self.proxy.sort(0, Qt.SortOrder.AscendingOrder)

        self.apply_headers()

    def refresh(self) -> None:
        self._suspend_autosave = True
        self._refresh_after_next_submit = False
        try:
            current_task_id = None
            id_col = self.model.fieldIndex("id")
            current_index = self.table.currentIndex()
            if id_col != -1 and current_index.isValid():
                src_current = self.proxy.mapToSource(current_index)
                if src_current.isValid():
                    current_task_id = self.model.data(self.model.index(src_current.row(), id_col), Qt.EditRole)

            # Reload the source model so external inserts/updates appear without needing a manual Save click.
            self.model.select()

            # Refresh course relation source (safe) and repopulate the filter dropdown.
            if self.course_id_col != -1:
                rm = self.model.relationModel(self.course_id_col)
                if rm is not None:
                    rm.select()

            # Make sure priority column visibility follows the current setting
            if self.priority_col != -1:
                self.table.setColumnHidden(self.priority_col, not get_priority_enabled())

            self._apply_status_filter_options()
            self.populate_course_filter()
            self.apply_filters()
            self.update_empty_state()

            if current_task_id is not None and id_col != -1:
                for row in range(self.model.rowCount()):
                    src_idx = self.model.index(row, id_col)
                    if self.model.data(src_idx, Qt.EditRole) == current_task_id:
                        proxy_idx = self.proxy.mapFromSource(self.model.index(row, 0))
                        if proxy_idx.isValid():
                            self.table.setCurrentIndex(proxy_idx)
                            self.table.scrollTo(proxy_idx)
                        break
        finally:
            self._suspend_autosave = False


    def apply_headers(self) -> None:
        # Friendly headers
        if self.course_id_col != -1:
            self.model.setHeaderData(self.course_id_col, Qt.Horizontal, "Course")

        def _hdr(field: str, title_txt: str) -> None:
            col = self.model.fieldIndex(field)
            if col != -1:
                self.model.setHeaderData(col, Qt.Horizontal, title_txt)

        _hdr("item", "Item")
        _hdr("due_datetime", "Due")
        _hdr("status", "Status")
        _hdr("weight", "Weight (%)")
        _hdr("grade", "Grade")
        _hdr("task_type", "Type")
        _hdr("priority", "Priority")

        # Show/hide priority column based on the global setting
        priority_col = self.model.fieldIndex("priority")
        if priority_col != -1:
            self.table.setColumnHidden(priority_col, not get_priority_enabled())

        # Hide legacy columns if they exist
        for field in ["id", "created_at", "updated_at", "ungraded", "project_id", "component", "due_status", "weight_raw_value", "weight_raw_unit", "weight_source", "syllabus_metadata", "notes"]:
            col = self.model.fieldIndex(field)
            if col != -1:
                self.table.setColumnHidden(col, True)

        # Header text alignment
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setSectionsMovable(False)
        hh.setSectionsClickable(False)

        # Apply sizing after the event loop so proxy/view indices are valid
        QTimer.singleShot(0, self.apply_column_layout)

    def apply_column_layout(self) -> None:
        try:
            hh = self.table.horizontalHeader()
        except RuntimeError:
            return
        hh.setStretchLastSection(False)
        # self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)  # Removed: wrong enum usage

        def view_col(field: str) -> int:
            scol = self.model.fieldIndex(field)
            if scol == -1:
                return -1
            if self.model.rowCount() > 0:
                px = self.proxy.mapFromSource(self.model.index(0, scol))
                if px.isValid():
                    return px.column()
            return scol

        c_course = view_col("course_id")
        c_item   = view_col("item")
        c_due    = view_col("due_datetime")
        c_status = view_col("status")
        c_priority = view_col("priority")
        c_weight = view_col("weight")
        c_grade  = view_col("grade")
        c_task_type = view_col("task_type")

        fixed = {
            c_due: 130,
            c_status: 190,
            c_priority: 90,
            c_weight: 84,
            c_grade: 84,
            c_task_type: 92,
        }

        # Course width from DB course names
        if c_course != -1:
            from PySide6.QtGui import QFontMetrics
            fm = QFontMetrics(self.table.font())
            max_w = fm.horizontalAdvance("Course")
            q = QSqlQuery()
            q.exec("SELECT name FROM courses")
            while q.next():
                name = str(q.value(0) or "")
                if name:
                    max_w = max(max_w, fm.horizontalAdvance(name))
            fixed[c_course] = max(150, min(max_w + 56, 300))

        for col, w in fixed.items():
            if col == -1:
                continue
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            hh.resizeSection(col, w)

        if c_item != -1:
            hh.setSectionResizeMode(c_item, QHeaderView.ResizeMode.Stretch)
            hh.setMinimumSectionSize(110)



def build_tasks_tab() -> QWidget:
    return TasksPage()
