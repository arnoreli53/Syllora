import ctypes
import sys

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer, QSize
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from db_qt import close_db, open_db
from migrate import ensure_schema
from ui_common import apply_app_theme, main_tab_stylesheet, theme_colors
from ui_overview import build_overview_tab
from ui_tasks import build_tasks_tab
from ui_courses import build_courses_tab
from ui_settings import build_settings_tab, get_theme
from user_profiles import current_profile, list_profiles, set_current_profile
from version import APP_DISPLAY_NAME, APP_VERSION


def _apply_macos_window_chrome(window: QMainWindow, *, dark: bool) -> None:
    if sys.platform != "darwin":
        return
    app = QApplication.instance()
    if app is None or app.platformName().lower() != "cocoa":
        return

    try:
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")

        c_void_p = ctypes.c_void_p
        c_char_p = ctypes.c_char_p

        objc.objc_getClass.restype = c_void_p
        objc.objc_getClass.argtypes = [c_char_p]
        objc.sel_registerName.restype = c_void_p
        objc.sel_registerName.argtypes = [c_char_p]

        def _cls(name: str) -> int:
            return int(objc.objc_getClass(name.encode("utf-8")))

        def _sel(name: str) -> int:
            return int(objc.sel_registerName(name.encode("utf-8")))

        def _send(receiver: int, selector: str, *args, restype=c_void_p, argtypes=None):
            msg = objc.objc_msgSend
            msg.restype = restype
            if argtypes is None:
                argtypes = [c_void_p, c_void_p] + [c_void_p for _ in args]
            msg.argtypes = argtypes
            return msg(receiver, _sel(selector), *args)

        nsview = int(window.winId())
        if not nsview:
            return

        nswindow = _send(nsview, "window")
        if not nswindow:
            return

        nsapp_class = _cls("NSApplication")
        if not nsapp_class:
            return

        # Only set appearance, avoid titlebar/visibility modifications that can lock window behavior
        nsappearance_class = _cls("NSAppearance")
        nsstring_class = _cls("NSString")
        if not nsappearance_class or not nsstring_class:
            return

        ns_name = _send(
            _send(nsstring_class, "alloc"),
            "initWithUTF8String:",
            (b"NSAppearanceNameDarkAqua" if dark else b"NSAppearanceNameAqua"),
            restype=c_void_p,
            argtypes=[c_void_p, c_void_p, c_char_p],
        )
        if not ns_name:
            return

        appearance = _send(
            nsappearance_class,
            "appearanceNamed:",
            ns_name,
            restype=c_void_p,
            argtypes=[c_void_p, c_void_p, c_void_p],
        )
        if appearance:
            application = _send(nsapp_class, "sharedApplication")
            if application:
                _send(
                    application,
                    "setAppearance:",
                    appearance,
                    restype=None,
                    argtypes=[c_void_p, c_void_p, c_void_p],
                )
            _send(
                nswindow,
                "setAppearance:",
                appearance,
                restype=None,
                argtypes=[c_void_p, c_void_p, c_void_p],
            )
    except Exception:
        pass


class MainWindow(QMainWindow):
    DESIGN_WINDOW_SIZE = QSize(1280, 800)
    ABSOLUTE_MIN_WINDOW_SIZE = QSize(820, 600)
    SCREEN_EDGE_MARGIN = 40

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle(APP_DISPLAY_NAME)
        self._apply_window_size_constraints()
        self.setMaximumSize(16777215, 16777215)

        central = QWidget()
        central.setObjectName("AppShell")
        central.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        shell_layout = QVBoxLayout()
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self.header = QFrame()
        self.header.setObjectName("AppHeader")
        self.header.setFixedHeight(60)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(22, 0, 22, 0)
        header_layout.setSpacing(28)

        brand = QWidget()
        brand_layout = QHBoxLayout()
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(10)
        brand_mark = QLabel("S")
        brand_mark.setObjectName("BrandMark")
        brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_mark.setFixedSize(34, 34)
        brand_name = QLabel(APP_DISPLAY_NAME)
        brand_name.setObjectName("BrandName")
        brand_layout.addWidget(brand_mark)
        brand_layout.addWidget(brand_name, 0, Qt.AlignmentFlag.AlignVCenter)
        brand.setLayout(brand_layout)
        header_layout.addWidget(brand)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(4)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        # Reserve enough width for each label plus its padding up front so the
        # layout never has to shrink a button below a readable size.
        nav_metrics = QFontMetrics(self.font())
        for index, label in enumerate(("Overview", "Tasks", "Courses", "Settings")):
            button = QPushButton(label)
            button.setObjectName("HeaderNavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumWidth(nav_metrics.horizontalAdvance(label) + 48)
            button.clicked.connect(lambda _checked=False, target=index: self.tab_widget.setCurrentIndex(target))
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            nav_row.addWidget(button)
        header_layout.addLayout(nav_row)

        header_layout.addStretch(1)

        self.header_user = QLabel("Local workspace")
        self.header_user.setObjectName("HeaderUser")
        header_layout.addWidget(self.header_user)

        self.version_label = QLabel(f"Syllora {APP_VERSION}")
        self.version_label.setObjectName("HeaderVersion")
        header_layout.addWidget(self.version_label)

        self.header.setLayout(header_layout)

        self.tab_widget = QTabWidget()
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.tabBar().hide()
        self.tab_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        self.workspace = QFrame()
        self.workspace.setObjectName("AppWorkspace")
        workspace_layout = QVBoxLayout()
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self.tab_widget, 1)
        self.workspace.setLayout(workspace_layout)

        shell_layout.addWidget(self.header)
        shell_layout.addWidget(self.workspace, 1)
        central.setLayout(shell_layout)
        self.setCentralWidget(central)

        self.overview_page = None
        self.tasks_page = None
        self.courses_page = None
        self.settings_page = None
        self._build_pages()

    def _clear_pages(self) -> None:
        self.tab_widget.blockSignals(True)
        try:
            while self.tab_widget.count():
                widget = self.tab_widget.widget(0)
                self.tab_widget.removeTab(0)
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
            self.overview_page = None
            self.tasks_page = None
            self.courses_page = None
            self.settings_page = None
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
        finally:
            self.tab_widget.blockSignals(False)

    def _build_pages(self, current_tab: str = "Overview") -> None:
        self.overview_page = build_overview_tab()
        self.tasks_page = build_tasks_tab()
        self.courses_page = build_courses_tab()
        self.settings_page = build_settings_tab()

        self.tab_widget.blockSignals(True)
        self.tab_widget.addTab(self.overview_page, "Overview")
        self.tab_widget.addTab(self.tasks_page, "Tasks")
        self.tab_widget.addTab(self.courses_page, "Courses")
        self.tab_widget.addTab(self.settings_page, "Settings")
        target_index = max(0, self.tab_widget.indexOf(self.settings_page if current_tab == "Settings" else self.overview_page))
        for index in range(self.tab_widget.count()):
            if self.tab_widget.tabText(index) == current_tab:
                target_index = index
                break
        self.tab_widget.setCurrentIndex(target_index)
        self.tab_widget.blockSignals(False)
        self._sync_sidebar_selection(target_index)

        if hasattr(self.settings_page, "changed"):
            try:
                self.settings_page.changed.connect(self.apply_app_settings)
            except Exception:
                pass
        if hasattr(self.settings_page, "user_switch_requested"):
            try:
                self.settings_page.user_switch_requested.connect(self.switch_user)
            except Exception:
                pass

        if hasattr(self.overview_page, "task_updated"):
            try:
                self.overview_page.task_updated.connect(self.tasks_page.refresh)
                self.overview_page.task_updated.connect(self.courses_page.refresh)
            except Exception:
                pass

        if hasattr(self.tasks_page, "changed"):
            try:
                self.tasks_page.changed.connect(self.overview_page.refresh)
                self.tasks_page.changed.connect(self.courses_page.refresh)
            except Exception:
                pass

        if hasattr(self.courses_page, "changed"):
            try:
                self.courses_page.changed.connect(self.tasks_page.refresh)
                self.courses_page.changed.connect(self.overview_page.refresh)
            except Exception:
                pass

        self.apply_app_settings()

    def _on_tab_changed(self, index: int) -> None:
        self._sync_sidebar_selection(index)
        tab = self.tab_widget.tabText(index)
        if tab == "Overview" and self.overview_page is not None and hasattr(self.overview_page, "refresh"):
            self.overview_page.refresh()
        if tab == "Tasks" and self.tasks_page is not None and hasattr(self.tasks_page, "refresh"):
            self.tasks_page.refresh()
        if tab == "Courses" and self.courses_page is not None and hasattr(self.courses_page, "refresh"):
            self.courses_page.refresh()
        if tab == "Settings" and self.settings_page is not None and hasattr(self.settings_page, "load"):
            self.settings_page.load()

    def _sync_sidebar_selection(self, index: int) -> None:
        for button_index, button in enumerate(self.nav_buttons):
            button.blockSignals(True)
            button.setChecked(button_index == index)
            button.blockSignals(False)

    def _refresh_sidebar_identity(self) -> None:
        profile = current_profile()
        full_text = "Local workspace" if profile is None else f"{profile.name} · Local workspace"
        # Elide rather than letting a long profile name overflow into the nav
        # buttons or version label next to it.
        elided = QFontMetrics(self.header_user.font()).elidedText(full_text, Qt.TextElideMode.ElideRight, 260)
        self.header_user.setText(elided)
        self.header_user.setToolTip(full_text)

    def _apply_window_size_constraints(self) -> None:
        target = QSize(self.DESIGN_WINDOW_SIZE)
        minimum = QSize(self.ABSOLUTE_MIN_WINDOW_SIZE)
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            max_width = max(640, available.width() - self.SCREEN_EDGE_MARGIN)
            max_height = max(480, available.height() - self.SCREEN_EDGE_MARGIN)
            target.setWidth(min(target.width(), max_width))
            target.setHeight(min(target.height(), max_height))
            minimum.setWidth(min(minimum.width(), max_width))
            minimum.setHeight(min(minimum.height(), max_height))

        self.setMinimumSize(minimum)
        if not getattr(self, "_initial_window_size_applied", False):
            self.resize(target)
            self._initial_window_size_applied = True

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # At the narrow end of the supported window range, drop the least
        # essential header content first so the nav buttons never get squeezed.
        if hasattr(self, "version_label"):
            self.version_label.setVisible(self.width() >= 940)
        if hasattr(self, "header_user"):
            self.header_user.setVisible(self.width() >= 860)

    def apply_app_settings(self) -> None:
        theme = get_theme()
        colors = theme_colors(theme)
        apply_app_theme(QApplication.instance(), theme)
        self.tab_widget.setStyleSheet(main_tab_stylesheet(theme))
        self.header.setStyleSheet(
            f"""
            QFrame#AppHeader {{
                background: {colors['panel_bg']};
                border: none;
                border-bottom: 1px solid {colors['border']};
            }}
            QLabel#BrandMark {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {colors['accent_hover']}, stop:1 {colors['accent']});
                color: {colors['accent_text']};
                border: 1px solid rgba(255, 255, 255, 0.22);
                border-radius: 10px;
                font-size: 16px;
                font-weight: 800;
                padding-bottom: 1px;
            }}
            QLabel#BrandName {{
                color: {colors['text_soft']};
                font-size: 16px;
                font-weight: 750;
                letter-spacing: -0.2px;
            }}
            QPushButton#HeaderNavButton {{
                background: transparent;
                color: {colors['muted_text']};
                border: 1px solid transparent;
                border-radius: 9px;
                padding: 8px 16px;
                text-align: center;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#HeaderNavButton:hover {{
                background: {colors['surface_bg']};
                color: {colors['text_soft']};
                border-color: {colors['border_soft']};
            }}
            QPushButton#HeaderNavButton:checked {{
                background: {colors['accent_tint']};
                color: {colors['text_soft']};
                border-color: rgba(69, 178, 255, 0.36);
            }}
            QLabel#HeaderUser {{
                color: {colors['text']};
                font-size: 12px;
                font-weight: 600;
            }}
            QLabel#HeaderVersion {{
                color: {colors['faint_text']};
                font-size: 10px;
            }}
            """
        )
        self.workspace.setStyleSheet(
            f"QFrame#AppWorkspace {{ background: {colors['window_bg']}; border: none; }}"
        )
        self._refresh_sidebar_identity()
        _apply_macos_window_chrome(self, dark=(theme == "dark"))
        for page in (self.overview_page, self.tasks_page, self.courses_page, self.settings_page):
            if page is None:
                continue
            try:
                if hasattr(page, "apply_settings"):
                    page.apply_settings()
            except Exception:
                pass

        for page in (self.overview_page, self.tasks_page, self.courses_page):
            if page is None:
                continue
            try:
                if hasattr(page, "refresh"):
                    page.refresh()
            except Exception:
                pass

    def _flush_pending_changes(self) -> bool:
        try:
            if self.tasks_page is not None:
                if hasattr(self.tasks_page, "_commit_current_editor"):
                    self.tasks_page._commit_current_editor()
                model = getattr(self.tasks_page, "model", None)
                if model is not None and model.isDirty():
                    if not self.tasks_page._submit_changes(show_errors=True, trigger_weight_banner=False):
                        return False

            if self.courses_page is not None:
                model = getattr(self.courses_page, "_model", None)
                if model is not None and model.isDirty():
                    if not self.courses_page._submit_changes():
                        QMessageBox.critical(
                            self,
                            "Save failed",
                            "Every course needs a unique, non-empty name before continuing.",
                        )
                        return False

            if self.settings_page is not None and hasattr(self.settings_page, "save"):
                self.settings_page.save()
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        return True

    def _background_work_active(self) -> bool:
        page = self.settings_page
        return bool(page is not None and hasattr(page, "has_active_background_work") and page.has_active_background_work())

    def switch_user(self, profile_id: str) -> None:
        profile_id = str(profile_id or "").strip()
        active = current_profile()
        if not profile_id or (active is not None and profile_id == active.id):
            return

        target = next((profile for profile in list_profiles() if profile.id == profile_id), None)
        if target is None:
            if self.settings_page is not None and hasattr(self.settings_page, "load"):
                self.settings_page.load()
            QMessageBox.warning(self, "Switch user", "That user profile could not be found.")
            return

        res = QMessageBox.question(
            self,
            "Switch user",
            f"Switch to {target.name}? Current edits will be saved first.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if res != QMessageBox.StandardButton.Yes:
            if self.settings_page is not None and hasattr(self.settings_page, "load"):
                self.settings_page.load()
            return

        previous_id = active.id if active is not None else ""
        try:
            if self._background_work_active():
                QMessageBox.information(self, "Switch user", "Finish or cancel the active import/update before switching users.")
                if self.settings_page is not None and hasattr(self.settings_page, "load"):
                    self.settings_page.load()
                return
            if not self._flush_pending_changes():
                if self.settings_page is not None and hasattr(self.settings_page, "load"):
                    self.settings_page.load()
                return
            self._clear_pages()
            close_db()
            set_current_profile(profile_id)
            open_db()
            ensure_schema()
            self._build_pages(current_tab="Settings")
        except Exception as exc:
            try:
                self._clear_pages()
                close_db()
                if previous_id:
                    set_current_profile(previous_id)
                open_db()
                ensure_schema()
                self._build_pages(current_tab="Settings")
            except Exception:
                pass
            QMessageBox.critical(self, "Switch user failed", str(exc))

    def import_syllabi_from_paths(self, paths: list[str]) -> None:
        if self.settings_page is None:
            return
        flow = getattr(self.settings_page, "_syllabus_import_flow", None)
        if flow is not None and hasattr(flow, "start_paths"):
            flow.start_paths(paths)

    def closeEvent(self, event) -> None:
        if self._background_work_active():
            QMessageBox.information(self, "Work in progress", "Finish or cancel the active import/update before closing Syllora.")
            event.ignore()
            return
        if not self._flush_pending_changes():
            event.ignore()
            return
        self.setProperty("_app_closing", True)
        super().closeEvent(event)
        if not event.isAccepted():
            self.setProperty("_app_closing", False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_window_size_constraints)
