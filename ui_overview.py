from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QDate, QDateTime, Signal, QEvent, QRect, QPropertyAnimation, QParallelAnimationGroup, QEasingCurve, QTimer
from PySide6.QtGui import QColor, QBrush, QPalette, QIcon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableView,
    QFrame,
    QHeaderView,
    QScrollArea,
    QSizePolicy,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QProxyStyle,
    QLabel,
    QGraphicsOpacityEffect,
    QDialog,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QDialogButtonBox,
    QDateEdit,
    QMessageBox,
    QSystemTrayIcon,
)
from PySide6.QtSql import QSqlQueryModel, QSqlQuery
from ui_common import apply_body_font, common_page_stylesheet, make_version_label, scaled_row_height, theme_colors
from ui_courses import format_current_course_grade_for_mode, list_current_course_completion_state


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


class OverviewTableStyle(QProxyStyle):
    """Suppress native hover/current-cell chrome while keeping delegate text rendering intact."""

    def drawPrimitive(self, element, option, painter, widget=None):
        if element in (
            QStyle.PrimitiveElement.PE_PanelItemViewItem,
            QStyle.PrimitiveElement.PE_PanelItemViewRow,
            QStyle.PrimitiveElement.PE_FrameFocusRect,
        ):
            return
        return super().drawPrimitive(element, option, painter, widget)


class RowCardDelegate(QStyledItemDelegate):
    """Paint each row as a rounded card with padding/spacing, using the model BackgroundRole as the card fill."""

    def __init__(self, view: QTableView, first_col: int, last_col: int):
        super().__init__(view)
        self.view = view
        self.first_col = first_col
        self.last_col = last_col

    def paint(self, painter, option, index):
        model = index.model()
        row = index.row()

        # Draw the rounded row background ONCE (on the first visible column)
        if index.column() == self.first_col:
            left_idx = model.index(row, self.first_col)
            left_rect = self.view.visualRect(left_idx)

            # Build a row-rect that spans the full viewport width (prevents squared right edge)
            m = 6
            vp_w = self.view.viewport().width()
            row_rect = QRect(m, left_rect.top() + 6, max(0, vp_w - (2 * m)), max(0, left_rect.height() - 12))

            bg = model.data(left_idx, Qt.BackgroundRole)
            # Default subtle card background when there's no due/overdue highlight
            if not bg:
                bg = QBrush(QColor(theme_colors(get_theme())["row_default_bg"]))

            painter.save()
            painter.setRenderHint(painter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(row_rect, 10, 10)

            # Subtle border
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(theme_colors(get_theme())["border"]))
            painter.drawRoundedRect(row_rect, 10, 10)

            # If selected, add a slightly stronger outline
            if option.state & QStyle.StateFlag.State_Selected:
                painter.setPen(QColor(theme_colors(get_theme())["row_selection_border"]))
                painter.drawRoundedRect(row_rect.adjusted(1, 1, -1, -1), 10, 10)

            painter.restore()

        # Paint text/content with padding; do not let native selection fill override card
        opt = QStyleOptionViewItem(option)
        opt.state = opt.state & ~QStyle.StateFlag.State_Selected
        opt.state = opt.state & ~QStyle.StateFlag.State_MouseOver
        opt.state = opt.state & ~QStyle.StateFlag.State_HasFocus

        # Force transparent background so the model's BackgroundRole doesn't render per-cell blocks.
        opt.backgroundBrush = QBrush(Qt.GlobalColor.transparent)
        pal = QPalette(opt.palette)
        pal.setBrush(QPalette.ColorRole.Base, QBrush(Qt.GlobalColor.transparent))
        pal.setBrush(QPalette.ColorRole.Window, QBrush(Qt.GlobalColor.transparent))
        opt.palette = pal

        opt.rect = opt.rect.adjusted(10, 2, -10, -2)
        super().paint(painter, opt, index)


class OverviewGradesDelegate(QStyledItemDelegate):
    """Keep course names left-aligned, checkmarks centered, and grade values right-aligned."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if index.column() == 2:
            option.displayAlignment = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        elif index.column() == 1:
            option.displayAlignment = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        else:
            option.displayAlignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

    def paint(self, painter, option, index) -> None:
        if index.column() == 1:
            model = index.model()
            if model is not None:
                is_complete = model.data(index, Qt.DisplayRole)
                if is_complete == 1:
                    painter.save()
                    painter.setFont(option.font)
                    painter.setPen(option.palette.color(QPalette.Text))
                    painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, "✅")
                    painter.restore()
                    return
                # Hide the raw 0 value for incomplete courses
                painter.save()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.restore()
                return
        super().paint(painter, option, index)


class QuickAddTaskDialog(QDialog):
    COURSE_PLACEHOLDER = "Select course"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quick add task")
        self.setModal(True)
        self.setMinimumWidth(420)

        self.course_input = QComboBox()
        self.course_input.setEditable(False)
        self.course_input.setMinimumWidth(240)
        self._load_courses()
        self.course_input.currentIndexChanged.connect(self._update_course_placeholder_style)

        self.item_input = QLineEdit()
        self.due_input = QDateEdit(QDate.currentDate())
        self.due_input.setDisplayFormat("yyyy-MM-dd")
        self.due_input.setCalendarPopup(True)

        self.status_input = QComboBox()
        self.status_input.addItems(["not started", "in progress", "submitted", "graded"])

        self.priority_input = QComboBox()
        self.priority_input.addItems(["normal", "high"])

        self.weight_input = QLineEdit()
        self.grade_input = QLineEdit()
        self.grade_input.setPlaceholderText("e.g. 92 or A-")

        self.type_input = QComboBox()
        self.type_input.addItems(["", "ungraded", "bonus"])

        form = QFormLayout()
        form.addRow("Course:", self.course_input)
        form.addRow("Assessment:", self.item_input)
        form.addRow("Due:", self.due_input)
        form.addRow("Status:", self.status_input)
        form.addRow("Priority:", self.priority_input)
        form.addRow("Weight (%):", self.weight_input)
        form.addRow("Grade:", self.grade_input)
        form.addRow("Type:", self.type_input)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def _load_courses(self) -> None:
        self.course_input.clear()
        self.course_input.addItem(self.COURSE_PLACEHOLDER, "")
        self.course_input.setItemData(0, QColor("#B42318"), Qt.ForegroundRole)
        query = QSqlQuery("SELECT name FROM courses ORDER BY name ASC")
        while query.next():
            name = str(query.value(0) or "").strip()
            if name:
                self.course_input.addItem(name, name)
        self.course_input.setCurrentIndex(0)
        self._update_course_placeholder_style()

    def _update_course_placeholder_style(self) -> None:
        current_value = str(self.course_input.currentData() or "").strip()
        if not current_value:
            self.course_input.setStyleSheet("QComboBox { color: #B42318; }")
        else:
            self.course_input.setStyleSheet("")

    def _get_course_id(self, course_name: str) -> int | None:
        q = QSqlQuery()
        q.prepare("SELECT id FROM courses WHERE name = ? LIMIT 1")
        q.addBindValue(course_name)
        if q.exec() and q.next():
            try:
                return int(q.value(0))
            except Exception:
                pass

        q.prepare("INSERT INTO courses(name, term, academic_year_start) VALUES(?, ?, ?)")
        q.addBindValue(course_name)
        q.addBindValue(_current_course_term())
        q.addBindValue(_current_academic_year_start())
        if not q.exec():
            QMessageBox.critical(self, "Save failed", q.lastError().text())
            return None

        q.exec("SELECT last_insert_rowid()")
        if q.next():
            try:
                return int(q.value(0))
            except Exception:
                pass
        return None

    def _on_accept(self) -> None:
        course_name = str(self.course_input.currentData() or "").strip()
        item_text = str(self.item_input.text() or "").strip()
        if not course_name or not item_text:
            QMessageBox.critical(self, "Missing data", "Please enter both Course and Assessment.")
            return

        course_id = self._get_course_id(course_name)
        if course_id is None:
            return

        due_value = self.due_input.date().toString("yyyy-MM-dd")
        status = self.status_input.currentText()
        priority = self.priority_input.currentText()
        weight = self.weight_input.text().strip() or None
        grade = self.grade_input.text().strip() or None
        if grade:
            parsed_grade = parse_task_grade_input(grade)
            if parsed_grade is None:
                QMessageBox.critical(self, "Missing data", "Grade must be a percent or a grade label from your scale.")
                return
            grade = parsed_grade[0]
        task_type = self.type_input.currentText()
        q = QSqlQuery()
        q.prepare(
            """
            INSERT INTO tasks(course_id, item, due_datetime, status, priority, weight, grade, task_type)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """
        )
        q.addBindValue(course_id)
        q.addBindValue(item_text)
        q.addBindValue(due_value)
        q.addBindValue(status)
        q.addBindValue(priority)
        q.addBindValue(weight)
        q.addBindValue(grade)
        q.addBindValue(task_type)

        if not q.exec():
            QMessageBox.critical(self, "Save failed", q.lastError().text())
            return

        self.accept()
from ui_settings import (
    get_compact_rows,
    get_font_size_mode,
    get_theme,
    get_in_progress_enabled,
    get_overview_upcoming_days,
    get_overview_due_soon_days,
    get_overview_high_weight_enabled,
    get_overview_high_weight_threshold,
    get_overview_high_priority_due_soon_days,
    get_overview_hide_completed,
    get_overview_pin_overdue,
    get_overview_pin_high_priority_enabled,
    get_overview_pin_high_priority_days,
    get_overview_reminders_enabled,
    get_priority_enabled,
    get_grade_display_mode,
    parse_task_grade_input,
)


class OverviewModel(QSqlQueryModel):
    """Read-only model for upcoming tasks with simple row highlighting."""

    def __init__(self) -> None:
        super().__init__()
        self._due_col = 6  # hidden raw due_datetime column (after adding spacer + task_id)
        self._priority_col = 7  # hidden raw priority column
        self._status_col = 8
        self._weight_col = 9
        self._grade_col = 10
        self._task_type_col = 11
        self.due_soon_days = 3
        self.high_priority_due_soon_days = 3
        self.priority_enabled = False
        self.in_progress_enabled = True
        self.high_weight_enabled = False
        self.high_weight_threshold = 15.0
        self.overdue_brush = QBrush(QColor(255, 225, 230))
        self.due_soon_brush = QBrush(QColor(255, 249, 196))
        self.high_priority_brush = QBrush(QColor(228, 238, 255))
        self.high_priority_due_soon_brush = QBrush(QColor(255, 218, 181))
        self.in_progress_brush = QBrush(QColor(223, 243, 255))

    def data(self, index, role=Qt.DisplayRole):
        # Custom Due date display formatting: "Friday, March 6th"
        if role == Qt.DisplayRole and index.column() == 2:
            due_idx = self.index(index.row(), self._due_col)
            raw = super().data(due_idx, Qt.DisplayRole)
            if raw:
                dt = QDateTime.fromString(str(raw), "yyyy-MM-dd HH:mm")
                if not dt.isValid():
                    dt = QDateTime.fromString(str(raw), "yyyy-MM-dd")
                if dt.isValid():
                    d = dt.date()
                    day = int(d.day())

                    # Ordinal suffix
                    if 11 <= (day % 100) <= 13:
                        suf = "th"
                    else:
                        suf = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

                    weekday = d.toString("dddd")
                    month = d.toString("MMMM")
                    return f"{weekday}, {month} {day}{suf}"

        if role == Qt.DisplayRole and index.column() == 3:
            due_idx = self.index(index.row(), self._due_col)
            status_idx = self.index(index.row(), self._status_col)
            weight_idx = self.index(index.row(), self._weight_col)
            grade_idx = self.index(index.row(), self._grade_col)
            task_type_idx = self.index(index.row(), self._task_type_col)
            raw_due = super().data(due_idx, Qt.DisplayRole)
            raw_status = super().data(status_idx, Qt.DisplayRole)
            raw_weight = super().data(weight_idx, Qt.DisplayRole)
            raw_grade = super().data(grade_idx, Qt.DisplayRole)
            raw_task_type = super().data(task_type_idx, Qt.DisplayRole)

            symbols: list[str] = []
            task_type = "" if raw_task_type is None else str(raw_task_type).strip().lower()
            status = "" if raw_status is None else str(raw_status).strip().lower()
            try:
                weight_value = None if raw_weight is None or str(raw_weight).strip() == "" else float(str(raw_weight).strip())
            except Exception:
                weight_value = None

            if (
                self.high_weight_enabled
                and task_type != "ungraded"
                and weight_value is not None
                and weight_value >= float(self.high_weight_threshold)
            ):
                symbols.append("‼️")

            if status == "in progress":
                symbols.append("🔄")

            missing_weight = task_type != "ungraded" and (
                raw_weight is None or str(raw_weight).strip() == ""
            )
            missing_grade = (
                task_type != "ungraded"
                and status in {"submitted", "graded"}
                and (raw_grade is None or str(raw_grade).strip() == "")
            )
            if missing_weight or missing_grade:
                symbols.append("⚠️")

            if raw_due:
                dt = QDateTime.fromString(str(raw_due), "yyyy-MM-dd HH:mm")
                if not dt.isValid():
                    dt = QDateTime.fromString(str(raw_due), "yyyy-MM-dd")
                now = QDateTime.currentDateTime()
                if dt.isValid() and now <= dt <= now.addDays(int(self.due_soon_days)):
                    symbols.append("⏳")

            return " ".join(symbols)

        if role in (Qt.DisplayRole, Qt.EditRole):
            return super().data(index, role)

        if role in (Qt.BackgroundRole, Qt.ForegroundRole):
            # Submit column should not paint its own square background; the row card paints the background.
            if role == Qt.BackgroundRole and index.column() == 4:
                return QBrush(QColor(0, 0, 0, 0))
            priority_raw = super().data(self.index(index.row(), self._priority_col), Qt.DisplayRole)
            priority = "" if priority_raw is None else str(priority_raw).strip().lower()
            status_raw = super().data(self.index(index.row(), self._status_col), Qt.DisplayRole)
            status = "" if status_raw is None else str(status_raw).strip().lower()
            due_idx = self.index(index.row(), self._due_col)
            raw = super().data(due_idx, Qt.DisplayRole)
            if not raw:
                return super().data(index, role)

            dt = QDateTime.fromString(str(raw), "yyyy-MM-dd HH:mm")
            if not dt.isValid():
                dt = QDateTime.fromString(str(raw), "yyyy-MM-dd")
            if not dt.isValid():
                return super().data(index, role)

            now = QDateTime.currentDateTime()

            if status == "in progress":
                if role == Qt.BackgroundRole:
                    return self.in_progress_brush

            if dt < now:
                if role == Qt.BackgroundRole:
                    # Full-row overdue tint (match due-soon behavior)
                    return self.overdue_brush

            if (
                self.priority_enabled
                and priority == "high"
                and self.high_priority_due_soon_days > 0
                and now <= dt <= now.addDays(int(self.high_priority_due_soon_days))
            ):
                if role == Qt.BackgroundRole:
                    return self.high_priority_due_soon_brush

            if now <= dt <= now.addDays(int(self.due_soon_days)):
                if role == Qt.BackgroundRole:
                    return self.due_soon_brush

            if self.priority_enabled and priority == "high":
                if role == Qt.BackgroundRole:
                    return self.high_priority_brush

        return super().data(index, role)


class SubmitButtonDelegate(QStyledItemDelegate):
    def __init__(self, page: "OverviewPage"):
        super().__init__(page.table)
        self.page = page

    def paint(self, painter, option, index):
        # Do not paint a cell background here; the row card delegate paints the row background.

        row = index.row()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = (row == getattr(self.page, "hover_row", -1))
        label = self.page._overview_action_label(row)

        # Only show button when hovered or selected
        if not label or not (selected or hovered):
            return

        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)

        # Small button centered in the Submit cell
        r = option.rect.adjusted(8, 7, -8, -7)
        # constrain height to look like a normal button
        if r.height() > 26:
            dy = (r.height() - 26) // 2
            r = r.adjusted(0, dy, 0, -dy)

        # Faint on hover, stronger on selection.
        bg_alpha = 40 if hovered and not selected else 80
        border_alpha = 90 if hovered and not selected else 140
        text_alpha = 140 if hovered and not selected else 220
        dark = get_theme() == "dark"
        border = QColor(0, 90, 160, border_alpha) if not dark else QColor(55, 148, 255, border_alpha)
        bg = QColor(0, 120, 215, bg_alpha) if not dark else QColor(55, 148, 255, min(120, bg_alpha + 16))
        text = QColor(0, 90, 160, text_alpha) if not dark else QColor(227, 240, 255, text_alpha)

        painter.setPen(border)
        painter.setBrush(bg)
        painter.drawRoundedRect(r, 8, 8)

        painter.setPen(text)
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, label)

        painter.restore()

    def editorEvent(self, event, model, option, index):
        return False


class IndicatorsDelegate(QStyledItemDelegate):
    def __init__(self, view: QTableView):
        super().__init__(view)
        self.view = view

    def paint(self, painter, option, index):
        raw = index.data(Qt.DisplayRole) or ""
        rect = option.rect.adjusted(4, 2, -4, -2)
        painter.save()
        font = painter.font()
        painter.setFont(font)
        painter.setPen(QColor("#4B556F") if get_theme() == "light" else QColor(theme_colors(get_theme())["muted_text"]))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(raw))
        painter.restore()


class OverviewEmptyState(QFrame):
    def __init__(self, title: str, detail: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("EmptyStateCard")

        self.title_label = QLabel(title)
        self.title_label.setObjectName("OverviewEmptyTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.detail_label = QLabel(detail)
        self.detail_label.setObjectName("OverviewEmptyHint")
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label.setWordWrap(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(24, 30, 24, 30)
        layout.setSpacing(6)
        layout.addStretch(1)
        layout.addWidget(self.title_label)
        layout.addWidget(self.detail_label)
        layout.addStretch(1)
        self.setLayout(layout)


class OverviewRowOverlay(QLabel):
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


class OverviewPage(QWidget):
    """Overview tab: upcoming (next 14 days) tasks, overdue pinned to top."""
    task_updated = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._last_overview_action: dict | None = None
        self._overview_animation_group: QParallelAnimationGroup | None = None
        self._overview_animation_overlays: list[OverviewRowOverlay] = []
        self._overview_animating = False
        self._tray_icon: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
            self._tray_icon = QSystemTrayIcon(icon, self)
            self._tray_icon.setToolTip("Syllora overview reminders")
            self._tray_icon.setVisible(True)
        self._last_reminder_notification: str | None = None

        title = QLabel("Upcoming Assessments")
        title.setObjectName("Title")
        version_label = make_version_label()

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setObjectName("PrimaryButton")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_refresh.setVisible(False)

        self.btn_quick_add = QPushButton("Quick add")
        self.btn_quick_add.setObjectName("SecondaryButton")
        self.btn_quick_add.clicked.connect(self._open_quick_add_dialog)

        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(title)

        top = QHBoxLayout()
        top.addLayout(left)
        top.addStretch(1)
        top.addWidget(self.btn_quick_add)
        top.addWidget(version_label)

        self.model = OverviewModel()
        self.model.setParent(self)
        self.grades_model = QStandardItemModel(0, 3, self)

        grades_title = QLabel("Current Grades")
        grades_title.setObjectName("OverviewSideHeading")

        self.grades_table = QTableView()
        self.grades_table.setModel(self.grades_model)
        self.grades_table.setSelectionMode(QTableView.SelectionMode.NoSelection)
        self.grades_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.grades_table.setShowGrid(False)
        self.grades_table.setAlternatingRowColors(True)
        self.grades_table.verticalHeader().setVisible(False)
        self.grades_table.verticalHeader().setDefaultSectionSize(30)
        self.grades_table.setWordWrap(False)
        self.grades_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.grades_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grades_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.grades_table.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.grades_table.horizontalHeader().setVisible(False)
        self.grades_table.setItemDelegate(OverviewGradesDelegate(self.grades_table))

        gh = self.grades_table.horizontalHeader()
        gh.setStretchLastSection(True)
        gh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        gh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        gh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.grades_table.setColumnWidth(0, 120)
        self.grades_table.setColumnWidth(1, 24)

        self.grades_panel_divider = QFrame()
        self.grades_panel_divider.setObjectName("OverviewDivider")
        self.grades_panel_divider.setFixedHeight(1)

        self.grades_panel_heading = QLabel("")
        self.grades_panel_heading.setObjectName("OverviewSideHeading")
        self.grades_panel_heading.setVisible(False)

        self.attention_scroll = QScrollArea()
        self.attention_scroll.setWidgetResizable(True)
        self.attention_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.attention_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.attention_container = QWidget()
        self.attention_layout = QVBoxLayout()
        self.attention_layout.setContentsMargins(0, 0, 0, 0)
        self.attention_layout.setSpacing(8)
        self.attention_container.setLayout(self.attention_layout)
        self.attention_scroll.setWidget(self.attention_container)

        grades_card = QFrame()
        grades_card.setObjectName("Card")
        grades_card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        grades_card.setMinimumWidth(260)
        grades_card.setMaximumWidth(320)
        self.grades_card = grades_card
        self.grades_empty = OverviewEmptyState(
            "No grades available...for now.",
            "Course averages will appear here.",
        )
        grades_layout = QVBoxLayout()
        grades_layout.setContentsMargins(10, 10, 10, 10)
        grades_layout.setSpacing(8)
        grades_layout.addWidget(grades_title)
        grades_layout.addWidget(self.grades_table)
        grades_layout.addWidget(self.grades_empty)
        grades_layout.addWidget(self.grades_panel_divider)
        grades_layout.addWidget(self.grades_panel_heading)
        grades_layout.addWidget(self.attention_scroll, 1)
        grades_card.setLayout(grades_layout)

        self.table = QTableView()
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)

        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setWordWrap(False)
        transparent_brush = QBrush(Qt.GlobalColor.transparent)
        table_palette = QPalette(self.table.palette())
        for role in (
            QPalette.ColorRole.Base,
            QPalette.ColorRole.Window,
            QPalette.ColorRole.AlternateBase,
            QPalette.ColorRole.Highlight,
        ):
            table_palette.setBrush(role, transparent_brush)
        self.table.setPalette(table_palette)
        self.table.viewport().setPalette(table_palette)

        self._overview_table_style = OverviewTableStyle()
        self._overview_table_style.setParent(self.table)
        self.table.setStyle(self._overview_table_style)
        self._overview_viewport_style = OverviewTableStyle()
        self._overview_viewport_style.setParent(self.table.viewport())
        self.table.viewport().setStyle(self._overview_viewport_style)

        self.hover_row = -1
        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)
        self.table.entered.connect(self._on_table_entered)

        # Row-as-card styling for columns 0..3; submit column has its own overlay delegate.
        card_delegate = RowCardDelegate(self.table, first_col=0, last_col=4)
        self.table.setItemDelegateForColumn(0, card_delegate)
        self.table.setItemDelegateForColumn(1, card_delegate)
        self.table.setItemDelegateForColumn(2, card_delegate)
        self.table.setItemDelegateForColumn(3, IndicatorsDelegate(self.table))
        self.table.setItemDelegateForColumn(4, SubmitButtonDelegate(self))

        # Clicking empty space should clear selection
        self.table.viewport().installEventFilter(self)

        # Indicators + action columns
        self.badges_col = 3
        self.submit_col = 4
        self.task_id_col = 5
        self.raw_due_col = 6
        self.priority_col = 7
        self.status_col = 8
        self.weight_col = 9
        self.task_type_col = 10
        self.table.clicked.connect(self._on_table_clicked)

        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setStretchLastSection(False)

        # Keep indicators and action button close to the Due column.
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)   # Course
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)   # Assessment
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)   # Due
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)   # Indicators
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)   # Action

        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 320)
        self.table.setColumnWidth(2, 210)
        self.table.setColumnWidth(3, 84)
        self.table.setColumnWidth(4, 132)

        # Hide native header (we draw our own rounded header bar aligned to column widths)
        self.table.horizontalHeader().setVisible(False)

        header_bar = QFrame()
        header_bar.setObjectName("HeaderBar")
        hb = QHBoxLayout()
        hb.setContentsMargins(16, 10, 16, 10)
        hb.setSpacing(0)

        # Column-aligned blocks: [Course(blank)] [Assessment] [Due date] [Indicators(blank)] [Action(blank)]
        w_course, w_assess, w_due, w_badges, w_submit = 150, 320, 210, 84, 132

        blank0 = QLabel("")
        blank0.setFixedWidth(w_course)

        lab_assess = QLabel("Assessment")
        lab_assess.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lab_assess.setFixedWidth(w_assess)

        lab_due = QLabel("Due date")
        lab_due.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lab_due.setFixedWidth(w_due)

        self.btn_undo = QPushButton("Undo")
        self.btn_undo.setObjectName("OverviewUndoButton")
        self.btn_undo.setMinimumWidth(0)
        self.btn_undo.setFixedSize(72, 28)
        self.btn_undo.setEnabled(False)
        self.btn_undo.setVisible(False)
        self.btn_undo.setToolTip("Undo the most recent Overview action.")
        self.btn_undo.clicked.connect(self._undo_last_overview_action)

        header_actions = QWidget()
        header_actions.setFixedWidth(w_badges + w_submit)
        header_actions_layout = QHBoxLayout()
        header_actions_layout.setContentsMargins(0, 0, 0, 0)
        header_actions_layout.setSpacing(0)
        header_actions_layout.addStretch(1)
        header_actions_layout.addWidget(self.btn_undo)
        header_actions.setLayout(header_actions_layout)
        self._header_course = blank0
        self._header_assessment = lab_assess
        self._header_due = lab_due
        self._header_actions = header_actions

        for w in (blank0, lab_assess, lab_due):
            w.setObjectName("OverviewHeaderLabel")
        header_actions.setObjectName("OverviewHeaderLabel")

        hb.addWidget(blank0)
        hb.addWidget(lab_assess)
        hb.addWidget(lab_due)
        hb.addWidget(header_actions)
        header_bar.setLayout(hb)

        upcoming_card = QFrame()
        upcoming_card.setObjectName("Card")
        self.upcoming_empty = OverviewEmptyState(
            "No upcoming assignments",
            "Tasks with upcoming due dates will appear here automatically.",
        )
        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.setSpacing(10)
        card_layout.addWidget(header_bar)
        card_layout.addWidget(self.table)
        card_layout.addWidget(self.upcoming_empty)
        upcoming_card.setLayout(card_layout)

        main = QHBoxLayout()
        main.setSpacing(10)
        main.addWidget(upcoming_card, 1)
        main.addWidget(grades_card)

        root = QVBoxLayout()
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        root.addLayout(top)
        root.addLayout(main)
        self.setLayout(root)

        self.apply_settings()
        self.refresh()

    def _build_section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("OverviewGlanceSectionLabel")
        return label

    def _build_glance_card(self, title: str, lines: list[str], *, hero: bool = False) -> QFrame:
        card = QFrame()
        card.setObjectName("OverviewGlanceHeroCard" if hero else "OverviewGlanceCard")

        title_label = QLabel(title)
        title_label.setObjectName("OverviewGlanceHeroTitle" if hero else "OverviewGlanceTitle")
        title_label.setWordWrap(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4 if hero else 3)
        layout.addWidget(title_label)
        for line in lines:
            if not line:
                continue
            detail_label = QLabel(line)
            detail_label.setObjectName("OverviewGlanceMeta")
            detail_label.setWordWrap(True)
            layout.addWidget(detail_label)
        card.setLayout(layout)
        return card

    def _show_desktop_notification(self, title: str, message: str) -> None:
        if self._tray_icon is None or not self._tray_icon.supportsMessages():
            return
        self._tray_icon.showMessage(
            title,
            message,
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )

    def _maybe_notify_reminders(self, rows: list[dict], now: QDateTime, glance_days: int) -> None:
        if not get_overview_reminders_enabled():
            return

        lines = self._build_reminder_summary(rows, now, glance_days)
        if not lines or len(lines) <= 1:
            return

        body = "\n".join(lines[1:])
        if body == self._last_reminder_notification:
            return

        self._last_reminder_notification = body
        self._show_desktop_notification("Course reminders", body)

    def _parse_due_datetime(self, raw: str) -> QDateTime:
        text = (raw or "").strip()
        for fmt in ("yyyy-MM-dd HH:mm", "yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd"):
            dt = QDateTime.fromString(text, fmt)
            if dt.isValid():
                return dt
        return QDateTime()

    def _format_compact_due(self, dt: QDateTime) -> str:
        if not dt.isValid():
            return "Unknown"
        return dt.toString("MMM d")

    def _format_glance_due(self, dt: QDateTime, now: QDateTime) -> str:
        if not dt.isValid():
            return "Unknown"
        date = dt.date()
        today = now.date()
        if date == today:
            return "Today"
        if date == today.addDays(1):
            return "Tomorrow"
        return dt.toString("MMM d")

    def _collect_overview_rows(self) -> list[dict]:
        rows: list[dict] = []
        for row in range(self.model.rowCount()):
            course = str(self.model.data(self.model.index(row, 0), Qt.DisplayRole) or "").strip()
            item = str(self.model.data(self.model.index(row, 1), Qt.DisplayRole) or "").strip()
            raw_due = str(self.model.data(self.model.index(row, self.raw_due_col), Qt.DisplayRole) or "").strip()
            raw_status = str(self.model.data(self.model.index(row, self.status_col), Qt.DisplayRole) or "").strip().lower()
            raw_weight = str(self.model.data(self.model.index(row, self.weight_col), Qt.DisplayRole) or "").strip()
            raw_grade = str(self.model.data(self.model.index(row, self.model._grade_col), Qt.DisplayRole) or "").strip()
            raw_task_type = str(self.model.data(self.model.index(row, self.task_type_col), Qt.DisplayRole) or "").strip().lower()
            priority = str(self.model.data(self.model.index(row, self.priority_col), Qt.DisplayRole) or "").strip().lower()
            dt = self._parse_due_datetime(raw_due)
            if not dt.isValid():
                continue
            rows.append(
                {
                    "course": course,
                    "item": item,
                    "raw_due": raw_due,
                    "due": dt,
                    "priority": priority,
                    "status": raw_status,
                    "weight": raw_weight,
                    "grade": raw_grade,
                    "task_type": raw_task_type,
                    "missing_weight": raw_task_type != "ungraded" and raw_weight == "",
                    "missing_grade": raw_task_type != "ungraded" and raw_status in {"submitted", "graded"} and raw_grade == "",
                }
            )
        return rows

    def _build_bucket_summary(self, rows: list[dict], now: QDateTime) -> list[str]:
        if not rows:
            return ["Nothing due in this bucket."]

        course_names = sorted({str(row["course"]).strip() for row in rows if str(row["course"]).strip()})
        course_summary = ", ".join(course_names[:2])
        if len(course_names) > 2:
            course_summary += f" +{len(course_names) - 2} more"

        first_row = min(rows, key=lambda row: int(row["due"].toSecsSinceEpoch()))
        lines = [f"{len(rows)} item(s) across {len(course_names)} course(s)"]
        if course_summary:
            lines.append(f"Courses: {course_summary}")
        lines.append(f"First due: {first_row['item']} • {self._format_glance_due(first_row['due'], now)}")
        return lines

    def _build_reminder_summary(self, rows: list[dict], now: QDateTime, glance_days: int) -> list[str]:
        overdue_rows = [row for row in rows if row["due"] < now]
        due_soon_rows = [
            row
            for row in rows
            if now <= row["due"] <= now.addDays(int(self.model.due_soon_days))
        ]
        missing_weight_rows = [row for row in rows if row.get("missing_weight")]
        missing_grade_rows = [row for row in rows if row.get("missing_grade")]

        lines: list[str] = []
        if overdue_rows:
            lines.append(f"{len(overdue_rows)} overdue item(s)")
        if due_soon_rows:
            lines.append(f"{len(due_soon_rows)} due soon")
        if missing_weight_rows:
            lines.append(f"{len(missing_weight_rows)} item(s) missing weight")
        if missing_grade_rows:
            lines.append(f"{len(missing_grade_rows)} item(s) missing grade")

        if not lines:
            return []

        return [
            "Review these reminders before the next deadline.",
            *lines,
        ]

    def _build_week_snapshot(self, rows: list[dict], now: QDateTime, glance_days: int) -> tuple[str, list[str]]:
        overdue_rows = [row for row in rows if row["due"] < now]
        future_rows = [row for row in rows if row["due"] >= now]
        today = now.date()
        today_rows = [row for row in future_rows if row["due"].date() == today]
        tomorrow_rows = [row for row in future_rows if row["due"].date() == today.addDays(1)]
        within_glance_rows = [row for row in future_rows if row["due"].date() <= today.addDays(glance_days - 1)]

        title = "Nothing pressing right now"
        if overdue_rows:
            title = "Overdue items need clearing"
        elif today_rows:
            title = "Today needs attention"
        elif tomorrow_rows:
            title = "Tomorrow is your next pinch point"
        elif future_rows:
            title = "Your next stretch is manageable"

        total_courses = len({str(row["course"]).strip() for row in rows if str(row["course"]).strip()})
        lines = [f"{len(rows)} item(s) across {total_courses} course(s) in the current overview window."]
        count_parts: list[str] = []
        if overdue_rows:
            count_parts.append(f"Overdue: {len(overdue_rows)}")
        count_parts.append(f"Today: {len(today_rows)}")
        count_parts.append(f"Tomorrow: {len(tomorrow_rows)}")
        later_this_week = max(0, len(within_glance_rows) - len(today_rows) - len(tomorrow_rows))
        count_parts.append(f"Next {max(1, glance_days - 2)} day(s): {later_this_week}")
        lines.append(" • ".join(count_parts))

        next_due_row = min(future_rows, key=lambda row: int(row["due"].toSecsSinceEpoch()), default=None)
        if next_due_row is not None:
            lines.append(
                f"Next due: {next_due_row['item']} • {self._format_glance_due(next_due_row['due'], now)} • {next_due_row['course']}"
            )
        elif overdue_rows:
            oldest_overdue = min(overdue_rows, key=lambda row: int(row["due"].toSecsSinceEpoch()))
            lines.append(
                f"Oldest overdue: {oldest_overdue['item']} • {self._format_glance_due(oldest_overdue['due'], now)} • {oldest_overdue['course']}"
            )

        return title, lines

    def _build_day_buckets(self, rows: list[dict], now: QDateTime, glance_days: int) -> list[tuple[str, list[str]]]:
        buckets: list[tuple[str, list[str]]] = []
        today = now.date()

        overdue_rows = sorted((row for row in rows if row["due"] < now), key=lambda row: int(row["due"].toSecsSinceEpoch()))
        if overdue_rows:
            buckets.append(("Overdue", self._build_bucket_summary(overdue_rows, now)))

        future_rows = [row for row in rows if row["due"] >= now]
        for offset in range(glance_days):
            bucket_date = today.addDays(offset)
            bucket_rows = sorted(
                (row for row in future_rows if row["due"].date() == bucket_date),
                key=lambda row: int(row["due"].toSecsSinceEpoch()),
            )
            if not bucket_rows:
                continue
            if offset == 0:
                title = "Today"
            elif offset == 1:
                title = "Tomorrow"
            else:
                title = bucket_date.toString("ddd, MMM d")
            buckets.append((title, self._build_bucket_summary(bucket_rows, now)))

        later_rows = sorted(
            (row for row in future_rows if row["due"].date() > today.addDays(glance_days - 1)),
            key=lambda row: int(row["due"].toSecsSinceEpoch()),
        )
        if later_rows:
            next_later = later_rows[0]
            buckets.append(
                (
                    "Later in the overview window",
                    [
                        f"{len(later_rows)} item(s) sit beyond the next {glance_days} day(s).",
                        f"Next after that: {next_later['item']} • {self._format_glance_due(next_later['due'], now)} • {next_later['course']}",
                    ],
                )
            )

        return buckets

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            child = layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
                continue
            nested = child.layout()
            if nested is not None:
                self._clear_layout(nested)  # type: ignore[arg-type]

    def _update_grades_table_height(self) -> None:
        row_count = self.grades_model.rowCount()
        header = self.grades_table.horizontalHeader()
        header_height = header.height() if header.isVisible() else 0
        frame = self.grades_table.frameWidth() * 2
        row_height = max(24, self.grades_table.verticalHeader().defaultSectionSize())
        visible_rows = min(max(row_count, 1), 6)
        target_height = header_height + frame + (visible_rows * row_height) + 4
        self.grades_table.setMinimumHeight(target_height)
        self.grades_table.setMaximumHeight(target_height)

    def _update_right_panel(
        self,
        rows: list[dict],
        upcoming_days: int,
    ) -> None:
        now = QDateTime.currentDateTime()
        self._clear_layout(self.attention_layout)
        glance_days = max(1, min(7, int(upcoming_days)))

        if not rows:
            self.attention_layout.addWidget(
                self._build_glance_card(
                    "Nothing pressing right now",
                    ["Take a breath"],
                    hero=True,
                )
            )
            self.attention_layout.addStretch(1)
            return

        hero_title, hero_lines = self._build_week_snapshot(rows, now, glance_days)
        self.attention_layout.addWidget(
            self._build_glance_card(hero_title, hero_lines, hero=True)
        )

        reminder_lines = self._build_reminder_summary(rows, now, glance_days)
        if reminder_lines:
            self.attention_layout.addWidget(self._build_section_label("Reminders"))
            self.attention_layout.addWidget(self._build_glance_card("Action required", reminder_lines))

        buckets = self._build_day_buckets(rows, now, glance_days)
        if buckets:
            self.attention_layout.addWidget(self._build_section_label("Day by Day"))
            for title, lines in buckets[:8]:
                self.attention_layout.addWidget(self._build_glance_card(title, lines))

        self.attention_layout.addStretch(1)

    def _open_quick_add_dialog(self) -> None:
        dialog = QuickAddTaskDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.task_updated.emit()
            self.refresh()

    def apply_settings(self) -> None:
        mode = get_font_size_mode()
        theme = get_theme()
        colors = theme_colors(theme)
        apply_body_font(self, mode)
        self.model.overdue_brush = QBrush(QColor(255, 225, 230) if theme == "light" else QColor("#3B202A"))
        self.model.due_soon_brush = QBrush(QColor(255, 249, 196) if theme == "light" else QColor("#382F1D"))
        self.model.high_priority_brush = QBrush(QColor(228, 238, 255) if theme == "light" else QColor("#19324E"))
        self.model.high_priority_due_soon_brush = QBrush(QColor(255, 218, 181) if theme == "light" else QColor("#49301D"))
        self.model.in_progress_brush = QBrush(QColor(223, 243, 255) if theme == "light" else QColor("#16314A"))
        self.setStyleSheet(
            common_page_stylesheet(mode, theme=theme, title_px=18)
            +
            f"""
            QFrame#Card {{
                background: {colors['card_bg']};
                border: 1px solid {colors['border']};
                border-radius: 16px;
            }}
            QFrame#HeaderBar {{
                background: {colors['header_bg']};
                border: 0px;
                border-radius: 10px;
            }}
            QFrame#EmptyStateCard {{
                background: {colors['empty_bg']};
                border: 1px dashed {colors['empty_border']};
                border-radius: 14px;
            }}
            QLabel#OverviewEmptyTitle {{
                color: {colors['empty_title']};
                font-weight: 700;
            }}
            QLabel#OverviewEmptyHint {{
                color: {colors['empty_hint']};
            }}
            QFrame#OverviewDivider {{
                background: {colors['divider']};
                border: 0px;
            }}
            QLabel#OverviewSideHeading {{
                color: {colors['text_soft']};
                font-weight: 700;
            }}
            QLabel#OverviewHeaderLabel {{
                color: {colors['text_soft']};
                font-weight: 600;
            }}
            QFrame#OverviewGlanceCard, QFrame#OverviewGlanceHeroCard {{
                background: {colors['surface_alt_bg']};
                border: 1px solid {colors['border']};
                border-radius: 12px;
            }}
            QFrame#OverviewGlanceHeroCard {{
                background: {colors['surface_bg']};
                border: 1px solid {colors['accent_tint']};
            }}
            QLabel#OverviewGlanceSectionLabel {{
                color: {colors['muted_text']};
                font-size: 11px;
                font-weight: 700;
            }}
            QLabel#OverviewGlanceHeroTitle, QLabel#OverviewGlanceTitle {{
                color: {colors['text_soft']};
                font-weight: 700;
            }}
            QLabel#OverviewGlanceHeroTitle {{
                font-size: 15px;
            }}
            QLabel#OverviewGlanceMeta {{
                color: {colors['muted_text']};
            }}
            QPushButton#OverviewUndoButton {{
                background: {colors['secondary_bg']};
                color: {colors['secondary_text']};
                border: 1px solid {colors['input_border']};
                border-radius: 8px;
                padding: 4px 10px;
                font-weight: 500;
                min-width: 0px;
            }}
            QPushButton#OverviewUndoButton:hover {{
                background: {colors['secondary_bg_hover']};
                border: 1px solid {colors['input_border_hover']};
            }}
            QPushButton#OverviewUndoButton:pressed {{
                background: {colors['secondary_bg_pressed']};
                border: 1px solid {colors['input_border_hover']};
            }}
            QPushButton#OverviewUndoButton:disabled {{
                color: {colors['faint_text']};
                border: 1px solid {colors['border']};
            }}
            QTableView {{
                background: transparent;
                border: none;
                selection-background-color: transparent;
                selection-color: {colors['text']};
                outline: 0px;
            }}
            QTableView::item {{
                padding: 0px;
                background: transparent;
                border: 0px;
            }}
            QTableView::item:selected {{
                background: transparent;
            }}
            """
        )
        self.grades_table.setStyleSheet(
            f"QTableView {{ background: {colors['surface_bg']}; color: {colors['text']}; border: 1px solid {colors['border']}; "
            f"alternate-background-color: {colors['surface_alt_bg']}; }}"
            f"QTableView::item {{ padding: 8px 10px; border: none; }}"
            f"QTableView::item:alternate {{ background: {colors['surface_alt_bg']}; }}"
            f"QHeaderView::section {{ border: 0px; border-right: 0px; border-left: 0px; padding-left: 12px; padding-right: 8px; "
            f"font-weight: 600; background: {colors['window_alt_bg']}; color: {colors['header_text']}; border-bottom: 1px solid {colors['border']}; }}"
        )
        grades_palette = QPalette(self.grades_table.palette())
        grades_palette.setColor(QPalette.ColorRole.Base, QColor(colors["surface_bg"]))
        grades_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["surface_alt_bg"]))
        grades_palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
        self.grades_table.setPalette(grades_palette)
        table_palette = QPalette(self.table.palette())
        transparent_brush = QBrush(Qt.GlobalColor.transparent)
        for role in (
            QPalette.ColorRole.Base,
            QPalette.ColorRole.Window,
            QPalette.ColorRole.AlternateBase,
            QPalette.ColorRole.Highlight,
        ):
            table_palette.setBrush(role, transparent_brush)
        table_palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["text"]))
        self.table.setPalette(table_palette)
        self.table.viewport().setPalette(table_palette)
        self.table.verticalHeader().setDefaultSectionSize(
            scaled_row_height(mode, compact=get_compact_rows(), compact_px=40, regular_px=48)
        )
        self.grades_table.verticalHeader().setDefaultSectionSize(
            scaled_row_height(mode, compact=get_compact_rows(), compact_px=24, regular_px=30)
        )
        self.apply_column_layout()
        self._update_grades_table_height()

    def update_empty_states(self) -> None:
        has_upcoming = self.model.rowCount() > 0
        self.table.setVisible(has_upcoming)
        self.upcoming_empty.setVisible(not has_upcoming)

        has_grades = self.grades_model.rowCount() > 0
        self.grades_table.setVisible(has_grades)
        self.grades_empty.setVisible(not has_grades)

    def _refresh_current_grades(self, grade_mode: str) -> None:
        self.grades_model.clear()
        self.grades_model.setColumnCount(3)

        summaries = list_current_course_completion_state()
        visible_summaries = [
            summary
            for summary in summaries.values()
            if summary.get("final_percent") is not None
            or summary.get("final_gpa43") is not None
            or str(summary.get("final_letter", "") or "").strip()
        ]
        visible_summaries.sort(key=lambda summary: str(summary.get("course_name", "") or "").strip().lower())

        for summary in visible_summaries:
            course_text = str(summary.get("course_name", "") or "").strip()
            current_text = format_current_course_grade_for_mode(summary, grade_mode)

            course_item = QStandardItem(course_text)
            complete_item = QStandardItem()
            complete_item.setData(1 if summary.get("is_complete") else 0, Qt.DisplayRole)
            current_item = QStandardItem(current_text)

            for item in (course_item, complete_item, current_item):
                item.setEditable(False)

            self.grades_model.appendRow([course_item, complete_item, current_item])

    def refresh(self) -> None:
        upcoming_days = get_overview_upcoming_days()
        due_soon_days = get_overview_due_soon_days()
        hide_completed = get_overview_hide_completed()
        pin_overdue = get_overview_pin_overdue()
        priority_enabled = get_priority_enabled()
        in_progress_enabled = get_in_progress_enabled()
        high_priority_due_soon_days = (
            get_overview_high_priority_due_soon_days() if priority_enabled else 0
        )
        high_weight_enabled = get_overview_high_weight_enabled()
        high_weight_threshold = get_overview_high_weight_threshold()
        pin_priority_enabled = get_overview_pin_high_priority_enabled()
        pin_priority_days = (
            get_overview_pin_high_priority_days()
            if priority_enabled and pin_priority_enabled
            else 0
        )
        grade_mode = get_grade_display_mode()

        # Update model behavior
        self.model.due_soon_days = due_soon_days
        self.model.high_priority_due_soon_days = high_priority_due_soon_days
        self.model.priority_enabled = priority_enabled
        self.model.in_progress_enabled = in_progress_enabled
        self.model.high_weight_enabled = high_weight_enabled
        self.model.high_weight_threshold = high_weight_threshold

        where_status = (
            "AND lower(coalesce(t.status, '')) NOT IN ('submitted', 'graded')" if hide_completed else ""
        )
        order_overdue_pin = (
            "CASE WHEN t.due_datetime < datetime('now', 'localtime') THEN 0 ELSE 1 END,\n            "
            if pin_overdue
            else ""
        )
        order_in_progress_pin = (
            "CASE WHEN lower(coalesce(t.status, '')) = 'in progress' THEN 0 ELSE 1 END,\n            "
            if in_progress_enabled
            else ""
        )
        order_priority_pin = (
            "CASE "
            "WHEN lower(coalesce(t.priority, 'normal')) = 'high' "
            "AND t.due_datetime >= datetime('now', 'localtime') "
            f"AND t.due_datetime <= datetime('now', 'localtime', '+{pin_priority_days} days') "
            "THEN 0 ELSE 1 END,\n            "
            if pin_priority_days > 0
            else ""
        )

        sql = f"""
        SELECT
            c.name AS Course,
            t.item AS Item,
            substr(t.due_datetime, 1, 10) AS Due,
            '' AS _spacer,
            '' AS Submit,
            t.id AS _task_id,
            t.due_datetime AS _due_raw,
            t.priority AS _priority,
            t.status AS _status,
            t.weight AS _weight,
            t.grade AS _grade,
            coalesce(t.task_type, '') AS _task_type
        FROM tasks t
        JOIN courses c ON c.id = t.course_id
        WHERE
            t.due_datetime IS NOT NULL
            AND trim(coalesce(t.due_datetime, '')) <> ''
            AND t.due_datetime <= datetime('now', 'localtime', '+{upcoming_days} days')
            {where_status}
        ORDER BY
            {order_overdue_pin}{order_in_progress_pin}{order_priority_pin}t.due_datetime ASC,
            c.name ASC,
            t.item ASC;
        """
        self.model.setQuery(sql)
        # Ensure headers match content
        self.model.setHeaderData(0, Qt.Horizontal, "")
        self.model.setHeaderData(1, Qt.Horizontal, "Assessment")
        self.model.setHeaderData(2, Qt.Horizontal, "Due date")
        self.model.setHeaderData(3, Qt.Horizontal, "")
        self.model.setHeaderData(4, Qt.Horizontal, "")
        # Keep spacer/submit headers visually minimal
        self.table.horizontalHeader().setMinimumSectionSize(1)

        # Re-apply header sizing after model reset (fixed, predictable widths)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)

        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)   # Course
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)   # Assessment
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)   # Due
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)   # Spacer
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)   # Submit

        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 320)
        self.table.setColumnWidth(2, 210)
        self.table.setColumnWidth(3, 84)
        self.table.setColumnWidth(4, 132)

        # Hide internal columns
        self.table.setColumnHidden(self.task_id_col, True)
        self.table.setColumnHidden(self.raw_due_col, True)
        self.table.setColumnHidden(self.priority_col, True)
        self.table.setColumnHidden(self.status_col, True)
        self.table.setColumnHidden(self.weight_col, True)
        self.table.setColumnHidden(self.model._grade_col, True)
        self.table.setColumnHidden(self.task_type_col, True)
        self.table.setColumnHidden(self.badges_col, False)
        self._refresh_current_grades(grade_mode)

        self.grades_table.setColumnWidth(0, 120)
        self.grades_table.setColumnWidth(1, 24)
        self._update_grades_table_height()
        rows = self._collect_overview_rows()
        self._update_right_panel(
            rows,
            upcoming_days=upcoming_days,
        )
        self._maybe_notify_reminders(rows, QDateTime.currentDateTime(), upcoming_days)

        self.apply_column_layout()
        self.update_empty_states()

    def apply_column_layout(self) -> None:
        """Fit the upcoming table to the available space without crushing content."""
        if not hasattr(self, "table"):
            return

        try:
            viewport_width = self.table.viewport().width()
        except RuntimeError:
            return
        available = max(640, viewport_width if viewport_width > 100 else self.width() - 48)
        course_width = 130 if available >= 760 else 110
        due_width = 170 if available >= 760 else 150
        badge_width = 74 if available >= 760 else 62
        submit_width = 120 if available >= 760 else 106
        assessment_width = max(190, available - course_width - due_width - badge_width - submit_width)
        widths = [course_width, assessment_width, due_width, badge_width, submit_width]

        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        for col, width in enumerate(widths):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, width)

        header_widgets = (
            getattr(self, "_header_course", None),
            getattr(self, "_header_assessment", None),
            getattr(self, "_header_due", None),
            getattr(self, "_header_actions", None),
        )
        for widget, width in zip(header_widgets[:3], widths[:3]):
            if widget is not None:
                widget.setFixedWidth(width)
        if header_widgets[3] is not None:
            header_widgets[3].setFixedWidth(badge_width + submit_width)

        # Ensure the internal columns are hidden
        if hasattr(self, "task_id_col"):
            self.table.setColumnHidden(self.task_id_col, True)
        if hasattr(self, "raw_due_col"):
            self.table.setColumnHidden(self.raw_due_col, True)
        if hasattr(self, "priority_col"):
            self.table.setColumnHidden(self.priority_col, True)
        if hasattr(self, "status_col"):
            self.table.setColumnHidden(self.status_col, True)
        if hasattr(self, "weight_col"):
            self.table.setColumnHidden(self.weight_col, True)
        if hasattr(self, "task_type_col"):
            self.table.setColumnHidden(self.task_type_col, True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "grades_card"):
            self.grades_card.setVisible(self.width() >= 980)
        QTimer.singleShot(0, self.apply_column_layout)

    def eventFilter(self, obj, event):
        if obj is self.table.viewport() and event.type() == QEvent.Type.MouseButtonPress:
            idx = self.table.indexAt(event.position().toPoint())
            if not idx.isValid():
                self.table.clearSelection()
                self.hover_row = -1
                self.table.viewport().update()
        return super().eventFilter(obj, event)

    def _overview_action_label(self, row: int) -> str:
        if row < 0 or row >= self.model.rowCount():
            return ""
        raw_status = self.model.data(self.model.index(row, self.status_col), Qt.DisplayRole)
        status = "" if raw_status is None else str(raw_status).strip().lower()
        if status in {"submitted", "graded"}:
            return ""
        if status == "in progress":
            return "Submit"
        if get_in_progress_enabled():
            return "In progress"
        return "Submit"

    def _clear_overview_animation(self) -> None:
        if self._overview_animation_group is not None:
            try:
                self._overview_animation_group.stop()
            except Exception:
                pass
            self._overview_animation_group.deleteLater()
            self._overview_animation_group = None

        for overlay in self._overview_animation_overlays:
            overlay.deleteLater()
        self._overview_animation_overlays = []
        self._overview_animating = False

    def _overview_row_rect(self, row: int) -> QRect:
        anchor_index = self.model.index(row, 0)
        anchor_rect = self.table.visualRect(anchor_index)
        if not anchor_rect.isValid():
            return QRect()

        margin = 6
        rect = QRect(
            margin,
            anchor_rect.top() + 6,
            max(0, self.table.viewport().width() - (2 * margin)),
            max(0, anchor_rect.height() - 12),
        )
        viewport_rect = self.table.viewport().rect()
        return rect.intersected(viewport_rect)

    def _capture_overview_row_state(self) -> dict[int, dict]:
        viewport = self.table.viewport()
        state: dict[int, dict] = {}
        for row in range(self.model.rowCount()):
            if self.table.isRowHidden(row):
                continue

            rect = self._overview_row_rect(row)
            if not rect.isValid() or rect.height() <= 0 or rect.width() <= 0:
                continue
            if rect.bottom() < 0 or rect.top() > viewport.height():
                continue

            raw_task_id = self.model.data(self.model.index(row, self.task_id_col), Qt.DisplayRole)
            try:
                task_id = int(raw_task_id)
            except Exception:
                continue

            pixmap = viewport.grab(rect)
            if pixmap.isNull():
                continue

            state[task_id] = {
                "rect": QRect(rect),
                "pixmap": pixmap,
            }
        return state

    def _current_overview_row_rects(self) -> dict[int, QRect]:
        rects: dict[int, QRect] = {}
        for row in range(self.model.rowCount()):
            if self.table.isRowHidden(row):
                continue

            rect = self._overview_row_rect(row)
            if not rect.isValid() or rect.height() <= 0 or rect.width() <= 0:
                continue

            raw_task_id = self.model.data(self.model.index(row, self.task_id_col), Qt.DisplayRole)
            try:
                task_id = int(raw_task_id)
            except Exception:
                continue
            rects[task_id] = QRect(rect)
        return rects

    def _play_overview_transition(self, before_state: dict[int, dict], changed_task_id: int) -> None:
        if not before_state:
            return

        self._clear_overview_animation()
        after_rects = self._current_overview_row_rects()
        group = QParallelAnimationGroup(self)
        overlays: list[OverviewRowOverlay] = []
        moved_any = False

        for task_id, snapshot in before_state.items():
            start_rect = snapshot.get("rect")
            pixmap = snapshot.get("pixmap")
            if not isinstance(start_rect, QRect) or pixmap is None or pixmap.isNull():
                continue

            end_rect = after_rects.get(task_id)
            overlay = OverviewRowOverlay(self.table.viewport(), pixmap, start_rect)
            overlay.raise_()

            if task_id == changed_task_id and end_rect is None:
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
            geom_anim.setDuration(260 if task_id == changed_task_id else 220)
            geom_anim.setStartValue(QRect(start_rect))
            geom_anim.setEndValue(QRect(end_rect))
            geom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(geom_anim)

        if not moved_any:
            for overlay in overlays:
                overlay.deleteLater()
            return

        self._overview_animation_group = group
        self._overview_animation_overlays = overlays
        self._overview_animating = True

        def _finish() -> None:
            if self._overview_animation_group is group:
                self._overview_animation_group = None
            for overlay in overlays:
                overlay.deleteLater()
            self._overview_animation_overlays = []
            self._overview_animating = False

        group.finished.connect(_finish)
        group.start()

    def _status_after_overview_submit(self, task_id: int) -> str:
        q = QSqlQuery()
        q.prepare(
            """
            SELECT
                coalesce(ungraded, 0),
                lower(coalesce(task_type, ''))
            FROM tasks
            WHERE id = ?
            """
        )
        q.addBindValue(task_id)
        if not q.exec() or not q.next():
            return "submitted"

        is_ungraded = bool(q.value(0) or 0)
        task_type = str(q.value(1) or "").strip().lower()
        if is_ungraded or task_type == "ungraded":
            return "graded"
        return "submitted"

    def _on_table_clicked(self, index):
        # update the DB and refresh
        if not index.isValid():
            return
        if index.column() != self.submit_col:
            return
        if self._overview_animating:
            return

        row = index.row()
        task_id = self.model.data(self.model.index(row, self.task_id_col), Qt.DisplayRole)
        try:
            task_id = int(task_id)
        except Exception:
            return

        action_label = self._overview_action_label(row)
        if not action_label:
            return

        before_state = self._capture_overview_row_state()
        previous_status = self.model.data(self.model.index(row, self.status_col), Qt.DisplayRole)
        item_name = str(self.model.data(self.model.index(row, 1), Qt.DisplayRole) or "").strip()
        next_status = "in progress" if action_label == "In progress" else self._status_after_overview_submit(task_id)
        q = QSqlQuery()
        q.prepare("UPDATE tasks SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?")
        q.addBindValue(next_status)
        q.addBindValue(task_id)
        if not q.exec():
            QMessageBox.critical(self, "Update failed", q.lastError().text())
            return

        previous_status_text = "" if previous_status is None else str(previous_status).strip()
        if previous_status_text.lower() != next_status.lower():
            self._last_overview_action = {
                "task_id": task_id,
                "previous_status": previous_status_text or "not started",
                "current_status": next_status,
                "item_name": item_name,
            }
            self._update_undo_button()

        # Refresh overview + notify main window to refresh Tasks
        self.refresh()
        QTimer.singleShot(0, lambda state=before_state, changed_id=task_id: self._play_overview_transition(state, changed_id))
        self.task_updated.emit()

    def _on_table_entered(self, index):
        if not index.isValid():
            if self.hover_row != -1:
                self.hover_row = -1
                self.table.viewport().update()
            return

        r = index.row()
        if r != self.hover_row:
            self.hover_row = r
            self.table.viewport().update()

    def _update_undo_button(self) -> None:
        has_action = bool(self._last_overview_action)
        self.btn_undo.setEnabled(has_action)
        self.btn_undo.setVisible(has_action)
        if not has_action:
            self.btn_undo.setToolTip("Undo the most recent Overview action.")
            return

        item_name = str(self._last_overview_action.get("item_name") or "").strip()
        if item_name:
            self.btn_undo.setToolTip(f"Undo the last Overview action for {item_name}.")
        else:
            self.btn_undo.setToolTip("Undo the most recent Overview action.")

    def _undo_last_overview_action(self) -> None:
        if not self._last_overview_action:
            return

        task_id = self._last_overview_action.get("task_id")
        previous_status = str(self._last_overview_action.get("previous_status") or "not started").strip() or "not started"
        if task_id in (None, ""):
            self._last_overview_action = None
            self._update_undo_button()
            return

        q = QSqlQuery()
        q.prepare("UPDATE tasks SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?")
        q.addBindValue(previous_status)
        q.addBindValue(int(task_id))
        if not q.exec():
            QMessageBox.critical(self, "Undo failed", q.lastError().text())
            return

        self._last_overview_action = None
        self._update_undo_button()
        self.refresh()
        self.task_updated.emit()


def build_overview_tab() -> QWidget:
    return OverviewPage()
