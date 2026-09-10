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
        "window_bg": "#070B10",
        "window_alt_bg": "#0A1017",
        "surface_bg": "#121C27",
        "surface_alt_bg": "#182432",
        "card_bg": "#101923",
        "panel_bg": "#0C131C",
        "header_bg": "#1B2A39",
        "input_bg": "#172432",
        "input_readonly_bg": "#0D151E",
        "input_border": "#344A60",
        "input_border_hover": "#567491",
        "border": "#2A3C4E",
        "border_soft": "rgba(157, 190, 222, 0.24)",
        "divider": "#314457",
        "text": "#F0F5FA",
        "text_soft": "#FFFFFF",
        "muted_text": "#B6C4D2",
        "faint_text": "#8195A9",
        "section_text": "#9FB3C7",
        "header_text": "#A8BED2",
        "accent": "#45B2FF",
        "accent_hover": "#74C6FF",
        "accent_pressed": "#2298E8",
        "accent_text": "#04111B",
        "accent_tint": "rgba(69, 178, 255, 0.24)",
        "secondary_bg": "#1A2836",
        "secondary_bg_hover": "#24384B",
        "secondary_bg_pressed": "#13202C",
        "secondary_text": "#F4F8FC",
        "danger_text": "#FF8E89",
        "danger_border": "rgba(255, 126, 120, 0.34)",
        "danger_bg_hover": "rgba(255, 126, 120, 0.11)",
        "danger_bg_pressed": "rgba(255, 126, 120, 0.17)",
        "empty_bg": "rgba(143, 184, 222, 0.07)",
        "empty_border": "rgba(157, 190, 222, 0.30)",
        "empty_title": "#F5F9FD",
        "empty_hint": "#A7B8C8",
        "error_bg": "rgba(87, 30, 35, 0.74)",
        "error_text": "#FFAAA5",
        "error_border": "rgba(255, 126, 120, 0.34)",
        "selection_text": "#FFFFFF",
        "menu_bg": "#141F2B",
        "menu_border": "#344A60",
        "menu_selected_bg": "rgba(69, 178, 255, 0.24)",
        "menu_selected_text": "#FFFFFF",
        "shadow": "rgba(0,0,0,0.35)",
        "scrollbar_bg": "rgba(143, 174, 205, 0.06)",
        "scrollbar_handle": "rgba(170, 200, 228, 0.34)",
        "row_default_bg": "#14202B",
        "row_selection_bg": "#1C3A53",
        "row_selection_border": "#45B2FF",
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
        QLabel#Title {{ font-size: {title_size}px; font-weight: {title_weight}; color: {colors['text_soft']}; letter-spacing: -0.2px; }}
        QLabel#Subtitle {{ color: {colors['muted_text']}; }}
        QLabel#VersionLabel {{ color: {colors['faint_text']}; font-size: {metrics['small']}px; font-weight: 500; }}
        QLabel#EmptyState {{
            color: {colors['muted_text']};
            font-size: {metrics['body']}px;
            font-weight: 500;
            padding: 30px 18px;
        }}
        QLabel#FooterNote {{
            color: {colors['faint_text']};
            font-size: {metrics['small']}px;
        }}
        {section_rule}
        QPushButton#PrimaryButton {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {colors['accent_hover']}, stop:1 {colors['accent']});
            color: {colors['accent_text']};
            border: none;
            border-radius: 11px;
            padding: 8px 16px;
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
            border: 1px solid {colors['border_soft']};
            border-radius: 11px;
            padding: 8px 16px;
            font-weight: 600;
            font-size: {metrics['button']}px;
            min-width: 80px;
        }}
        QPushButton#SecondaryButton:hover {{
            background: {colors['secondary_bg_hover']};
            border: 1px solid {colors['border_soft']};
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
            background: transparent;
            color: {colors['danger_text']};
            border: 1px solid transparent;
            border-radius: 11px;
            padding: 8px 16px;
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
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {colors['window_alt_bg']}, stop:1 {colors['window_bg']});
            color: {colors['text']};
        }}
        QDialog, QMessageBox {{
            background: {colors['window_bg']};
            color: {colors['text']};
        }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QAbstractSpinBox {{
            background: {colors['input_bg']};
            color: {colors['text']};
            border: 1px solid {colors['border_soft']};
            border-radius: 10px;
            padding: 8px 12px;
            selection-background-color: {colors['accent']};
            selection-color: {colors['selection_text']};
        }}
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QDateTimeEdit:hover, QAbstractSpinBox:hover {{
            border: 1px solid {colors['input_border']};
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
            border-radius: 14px;
            gridline-color: {colors['border_soft']};
        }}
        QHeaderView::section {{
            background: transparent;
            color: {colors['header_text']};
            border: none;
            border-bottom: 1px solid {colors['border_soft']};
            padding: 10px 12px;
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
            padding: 4px;
        }}
        QMenu::item {{
            padding: 4px 12px;
            border-radius: 5px;
            background: transparent;
        }}
        QMenu::item:selected {{
            background: {colors['menu_selected_bg']};
            color: {colors['menu_selected_text']};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {colors['scrollbar_handle']};
            border-radius: 4px;
            min-height: 24px;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 8px;
            margin: 2px;
        }}
        QScrollBar::handle:horizontal {{
            background: {colors['scrollbar_handle']};
            border-radius: 4px;
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
            top: 0px;
        }}
        QTabBar {{
            alignment: center;
            margin-top: 4px;
            background: transparent;
        }}
        QTabBar::tab {{
            background: transparent;
            color: {colors['muted_text']};
            border: none;
            border-bottom: 2px solid transparent;
            padding: 10px 18px 8px 18px;
            margin: 0 6px;
            min-width: 78px;
            font-size: 13px;
            font-weight: 600;
        }}
        QTabBar::tab:hover {{
            background: {colors['surface_bg']};
            color: {colors['text']};
            border-radius: 8px;
        }}
        QTabBar::tab:selected {{
            background: transparent;
            color: {colors['text_soft']};
            border-bottom: 2px solid {colors['accent']};
            border-radius: 0px;
        }}
    """


def make_version_label(parent: QWidget | None = None) -> QLabel:
    label = QLabel(APP_VERSION, parent)
    label.setObjectName("VersionLabel")
    return label
