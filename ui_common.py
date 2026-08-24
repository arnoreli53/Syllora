from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from version import APP_VERSION


def normalize_font_mode(mode: str) -> str:
    mode2 = (mode or "normal").strip().lower()
    return mode2 if mode2 in {"small", "normal", "large"} else "normal"


def normalize_theme(theme: str) -> str:
    return "dark"


def font_metrics(mode: str) -> dict[str, int]:
    mode2 = normalize_font_mode(mode)
    metrics = {
        "small": {
            "body": 12,
            "button": 12,
            "small": 10,
            "section": 11,
            "title": 17,
            "settings_title": 19,
        },
        "normal": {
            "body": 13,
            "button": 13,
            "small": 11,
            "section": 12,
            "title": 18,
            "settings_title": 20,
        },
        "large": {
            "body": 14,
            "button": 14,
            "small": 12,
            "section": 13,
            "title": 20,
            "settings_title": 22,
        },
    }
    return metrics[mode2]


def theme_colors(theme: str) -> dict[str, str]:
    theme2 = normalize_theme(theme)
    light = {
        "window_bg": "#F7F8FC",
        "window_alt_bg": "#EFF2FB",
        "surface_bg": "#FFFFFF",
        "surface_alt_bg": "#F5F7FF",
        "card_bg": "rgba(245, 247, 255, 0.98)",
        "panel_bg": "rgba(255, 255, 255, 0.55)",
        "header_bg": "rgba(0,0,0,0.06)",
        "input_bg": "#FFFFFF",
        "input_readonly_bg": "#F6F7FB",
        "input_border": "#D0D5E4",
        "input_border_hover": "#AEB7CF",
        "border": "#DFE2F1",
        "border_soft": "rgba(0,0,0,0.06)",
        "divider": "rgba(215, 221, 240, 0.95)",
        "text": "#111111",
        "text_soft": "#20283A",
        "muted_text": "rgba(0,0,0,0.65)",
        "faint_text": "rgba(0,0,0,0.42)",
        "section_text": "rgba(0,0,0,0.55)",
        "header_text": "#77809A",
        "accent": "#4756FF",
        "accent_hover": "#6077FF",
        "accent_pressed": "#3643D6",
        "accent_text": "#FFFFFF",
        "accent_tint": "rgba(71, 86, 255, 0.18)",
        "secondary_bg": "#FFFFFF",
        "secondary_bg_hover": "#F5F5F5",
        "secondary_bg_pressed": "#EBEBEB",
        "secondary_text": "#323232",
        "danger_text": "#B42318",
        "danger_border": "rgba(180, 35, 24, 0.30)",
        "danger_bg_hover": "rgba(180, 35, 24, 0.06)",
        "danger_bg_pressed": "rgba(180, 35, 24, 0.10)",
        "empty_bg": "rgba(255,255,255,0.72)",
        "empty_border": "rgba(175, 184, 214, 0.90)",
        "empty_title": "rgba(31, 41, 55, 0.82)",
        "empty_hint": "rgba(75, 85, 99, 0.62)",
        "error_bg": "rgba(255, 236, 236, 0.96)",
        "error_text": "#B42318",
        "error_border": "rgba(240, 173, 173, 0.95)",
        "selection_text": "#111111",
        "menu_bg": "#FFFFFF",
        "menu_border": "#D9DEEC",
        "menu_selected_bg": "rgba(71, 86, 255, 0.12)",
        "menu_selected_text": "#20283A",
        "shadow": "rgba(26, 36, 78, 0.08)",
        "scrollbar_bg": "rgba(0,0,0,0.06)",
        "scrollbar_handle": "rgba(0,0,0,0.18)",
        "row_default_bg": "#FFFFFF",
        "row_selection_bg": "#F2F6FF",
        "row_selection_border": "#5697FF",
    }
    dark = {
        "window_bg": "#0A0F16",
        "window_alt_bg": "#0D141E",
        "surface_bg": "#111A25",
        "surface_alt_bg": "#162230",
        "card_bg": "#101923",
        "panel_bg": "#131E2A",
        "header_bg": "#152130",
        "input_bg": "#0D1722",
        "input_readonly_bg": "#0C131C",
        "input_border": "#29394B",
        "input_border_hover": "#405A73",
        "border": "#223142",
        "border_soft": "rgba(143, 174, 205, 0.12)",
        "divider": "#233244",
        "text": "#E6EDF5",
        "text_soft": "#F5F8FC",
        "muted_text": "#9CABB9",
        "faint_text": "#6F8193",
        "section_text": "#8FA1B4",
        "header_text": "#8093A8",
        "accent": "#4CA7FF",
        "accent_hover": "#6AB6FF",
        "accent_pressed": "#2E83D4",
        "accent_text": "#07121D",
        "accent_tint": "rgba(76, 167, 255, 0.22)",
        "secondary_bg": "#182432",
        "secondary_bg_hover": "#203043",
        "secondary_bg_pressed": "#111B27",
        "secondary_text": "#E6EDF5",
        "danger_text": "#FF8E89",
        "danger_border": "rgba(255, 126, 120, 0.34)",
        "danger_bg_hover": "rgba(255, 126, 120, 0.11)",
        "danger_bg_pressed": "rgba(255, 126, 120, 0.17)",
        "empty_bg": "rgba(143, 174, 205, 0.035)",
        "empty_border": "rgba(143, 174, 205, 0.20)",
        "empty_title": "#ECF3FA",
        "empty_hint": "#8FA0B1",
        "error_bg": "rgba(87, 30, 35, 0.74)",
        "error_text": "#FFAAA5",
        "error_border": "rgba(255, 126, 120, 0.34)",
        "selection_text": "#FFFFFF",
        "menu_bg": "#121C27",
        "menu_border": "#2B3C4E",
        "menu_selected_bg": "rgba(76, 167, 255, 0.18)",
        "menu_selected_text": "#FFFFFF",
        "shadow": "rgba(0,0,0,0.35)",
        "scrollbar_bg": "rgba(143, 174, 205, 0.06)",
        "scrollbar_handle": "rgba(143, 174, 205, 0.24)",
        "row_default_bg": "#162230",
        "row_selection_bg": "#19314A",
        "row_selection_border": "#4CA7FF",
    }
    return light if theme2 == "light" else dark


def apply_body_font(widget: QWidget, mode: str) -> None:
    font = QFont(widget.font())
    font.setPointSize(font_metrics(mode)["body"])
    widget.setFont(font)


def scaled_row_height(mode: str, *, compact: bool, compact_px: int, regular_px: int) -> int:
    metrics = font_metrics(mode)
    base = compact_px if compact else regular_px
    return base + max(0, metrics["body"] - 13) * 2


def common_page_stylesheet(
    mode: str,
    *,
    theme: str = "light",
    title_px: int | None = None,
    title_weight: int = 700,
    include_section_label: bool = False,
) -> str:
    metrics = font_metrics(mode)
    colors = theme_colors(theme)
    title_size = title_px or metrics["title"]
    section_rule = ""
    if include_section_label:
        section_rule = (
            f"QLabel#Section {{ font-size: {metrics['section']}px; font-weight: 700; "
            f"color: {colors['section_text']}; letter-spacing: 0.2px; }}"
        )

    return f"""
        QWidget {{ font-size: {metrics['body']}px; color: {colors['text']}; }}
        QLabel#Title {{ font-size: {title_size}px; font-weight: {title_weight}; color: {colors['text_soft']}; }}
        QLabel#Subtitle {{ color: {colors['muted_text']}; }}
        QLabel#VersionLabel {{ color: {colors['faint_text']}; font-size: {metrics['small']}px; font-weight: 500; }}
        QLabel#EmptyState {{
            color: {colors['muted_text']};
            font-size: {metrics['body']}px;
            font-weight: 600;
            padding: 28px 16px;
        }}
        QLabel#FooterNote {{
            color: {colors['faint_text']};
            font-size: {metrics['small']}px;
        }}
        {section_rule}
        QPushButton#PrimaryButton {{
            background: {colors['accent']};
            color: {colors['accent_text']};
            border: none;
            border-radius: 9px;
            padding: 8px 17px;
            font-weight: 700;
            font-size: {metrics['button']}px;
            min-width: 80px;
        }}
        QPushButton#PrimaryButton:hover {{
            background: {colors['accent_hover']};
        }}
        QPushButton#PrimaryButton:pressed {{
            background: {colors['accent_pressed']};
        }}
        QPushButton#PrimaryButton:disabled {{
            background: {colors['input_readonly_bg']};
            color: {colors['faint_text']};
            border: 1px solid {colors['border']};
        }}
        QPushButton#SecondaryButton {{
            background: {colors['secondary_bg']};
            color: {colors['secondary_text']};
            border: 1px solid {colors['input_border']};
            border-radius: 9px;
            padding: 8px 17px;
            font-weight: 600;
            font-size: {metrics['button']}px;
            min-width: 80px;
        }}
        QPushButton#SecondaryButton:hover {{
            background: {colors['secondary_bg_hover']};
            border: 1px solid {colors['input_border_hover']};
        }}
        QPushButton#SecondaryButton:pressed {{
            background: {colors['secondary_bg_pressed']};
            border: 1px solid {colors['input_border_hover']};
        }}
        QPushButton#SecondaryButton:disabled {{
            background: {colors['input_readonly_bg']};
            color: {colors['faint_text']};
            border: 1px solid {colors['border']};
        }}
        QPushButton#DangerButton {{
            background: {colors['secondary_bg']};
            color: {colors['danger_text']};
            border: 1px solid {colors['danger_border']};
            border-radius: 9px;
            padding: 8px 17px;
            font-weight: 600;
            font-size: {metrics['button']}px;
            min-width: 80px;
        }}
        QPushButton#DangerButton:hover {{
            background: {colors['danger_bg_hover']};
            border: 1px solid {colors['danger_border']};
        }}
        QPushButton#DangerButton:pressed {{
            background: {colors['danger_bg_pressed']};
        }}
        QPushButton#DangerButton:disabled {{
            background: {colors['input_readonly_bg']};
            color: {colors['faint_text']};
            border: 1px solid {colors['border']};
        }}
    """


def application_theme_stylesheet(theme: str) -> str:
    colors = theme_colors(theme)
    return f"""
        QToolTip {{
            background: {colors['menu_bg']};
            color: {colors['text']};
            border: 1px solid {colors['menu_border']};
            padding: 6px 8px;
            border-radius: 7px;
        }}
        QMainWindow {{
            background-color: {colors['window_bg']};
            color: {colors['text']};
        }}
        QDialog, QMessageBox {{
            background: {colors['window_bg']};
            color: {colors['text']};
        }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QAbstractSpinBox {{
            background: {colors['input_bg']};
            color: {colors['text']};
            border: 1px solid {colors['input_border']};
            border-radius: 9px;
            padding: 7px 11px;
            selection-background-color: {colors['accent']};
            selection-color: {colors['selection_text']};
        }}
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QDateTimeEdit:hover, QAbstractSpinBox:hover {{
            border: 1px solid {colors['input_border_hover']};
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateTimeEdit:focus, QAbstractSpinBox:focus {{
            border: 1px solid {colors['accent']};
        }}
        QLineEdit[readOnly="true"], QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
            background: {colors['input_readonly_bg']};
            color: {colors['muted_text']};
        }}
        QComboBox::drop-down, QDateTimeEdit::drop-down {{
            border: none;
            width: 22px;
            background: transparent;
        }}
        QAbstractItemView {{
            background: {colors['surface_alt_bg']};
            color: {colors['text']};
            selection-background-color: {colors['accent']};
            selection-color: {colors['selection_text']};
            border: 1px solid {colors['menu_border']};
            outline: none;
        }}
        QTableView, QTableWidget {{
            border-radius: 10px;
            gridline-color: {colors['border_soft']};
        }}
        QHeaderView::section {{
            background: {colors['window_alt_bg']};
            color: {colors['header_text']};
            border: none;
            border-bottom: 1px solid {colors['border']};
            padding: 8px 10px;
            font-weight: 600;
        }}
        QPushButton:disabled {{
            color: {colors['faint_text']};
            background: {colors['input_readonly_bg']};
            border-color: {colors['border']};
        }}
        QCheckBox, QRadioButton {{
            spacing: 8px;
        }}
        QCheckBox::indicator, QRadioButton::indicator {{
            width: 16px;
            height: 16px;
        }}
        QProgressBar {{
            background: {colors['input_readonly_bg']};
            border: 1px solid {colors['border']};
            border-radius: 7px;
            color: {colors['text']};
            text-align: center;
            min-height: 12px;
        }}
        QProgressBar::chunk {{
            background: {colors['accent']};
            border-radius: 6px;
        }}
        QMenu {{
            background: {colors['menu_bg']};
            color: {colors['text']};
            border: 1px solid {colors['menu_border']};
            padding: 6px;
        }}
        QMenu::item {{
            padding: 6px 18px;
            border-radius: 6px;
            background: transparent;
        }}
        QMenu::item:selected {{
            background: {colors['menu_selected_bg']};
            color: {colors['menu_selected_text']};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 12px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {colors['scrollbar_handle']};
            border-radius: 6px;
            min-height: 24px;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 12px;
            margin: 2px;
        }}
        QScrollBar::handle:horizontal {{
            background: {colors['scrollbar_handle']};
            border-radius: 6px;
            min-width: 24px;
        }}
        QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
            background: transparent;
            border: none;
        }}
    """


def apply_app_theme(app: QApplication | None, theme: str) -> None:
    if app is None:
        return

    colors = theme_colors(theme)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors["window_bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors["surface_alt_bg"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["surface_bg"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors["surface_bg"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["menu_bg"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["selection_text"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["faint_text"]))
    palette.setColor(QPalette.ColorRole.Mid, QColor(colors["border"]))
    palette.setColor(QPalette.ColorRole.Midlight, QColor(colors["border"]))
    app.setPalette(palette)
    app.setStyleSheet(application_theme_stylesheet(theme))


def main_tab_stylesheet(theme: str) -> str:
    colors = theme_colors(theme)
    return f"""
        QTabWidget::pane {{
            border: none;
            background: transparent;
            top: 8px;
        }}
        QTabBar {{
            alignment: center;
            margin-top: 7px;
            background: transparent;
        }}
        QTabBar::tab {{
            background: transparent;
            color: {colors['muted_text']};
            border: 1px solid transparent;
            border-radius: 10px;
            padding: 9px 20px;
            margin: 0 3px;
            min-width: 86px;
            font-size: 13px;
            font-weight: 600;
        }}
        QTabBar::tab:hover {{
            background: {colors['surface_bg']};
            color: {colors['text']};
        }}
        QTabBar::tab:selected {{
            background: {colors['surface_alt_bg']};
            color: {colors['text_soft']};
            border: 1px solid {colors['input_border']};
        }}
    """


def make_version_label(parent: QWidget | None = None) -> QLabel:
    label = QLabel(APP_VERSION, parent)
    label.setObjectName("VersionLabel")
    return label
