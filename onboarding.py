from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui_common import apply_body_font, common_page_stylesheet, theme_colors


class OnboardingDropArea(QLabel):
    filesDropped = Signal(list)

    def __init__(self) -> None:
        super().__init__("Drag and drop syllabi here")
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(150)
        self.setObjectName("OnboardingDropArea")

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        files = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile() and url.toLocalFile()
        ]
        if files:
            self.filesDropped.emit(files)
            event.acceptProposedAction()


class OnboardingDialog(QDialog):
    def __init__(self, icon_path: Path | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Syllora")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._syllabus_paths: list[str] = []
        self._import_after_start = False

        colors = theme_colors("dark")
        apply_body_font(self, "normal")

        hero = QFrame()
        hero.setObjectName("OnboardingHero")
        hero_layout = QVBoxLayout()
        hero_layout.setContentsMargins(22, 22, 22, 22)
        hero_layout.setSpacing(10)

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if icon_path is not None and icon_path.exists():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                logo.setPixmap(pixmap.scaled(150, 150, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                opacity = QGraphicsOpacityEffect(logo)
                opacity.setOpacity(0.18)
                logo.setGraphicsEffect(opacity)

        title = QLabel("Welcome to Syllora")
        title.setObjectName("OnboardingTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle = QLabel("Enter name")
        subtitle.setObjectName("OnboardingSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hero_layout.addWidget(logo)
        hero_layout.addWidget(title)
        hero_layout.addWidget(subtitle)
        hero.setLayout(hero_layout)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Your name")
        self.name_edit.setObjectName("OnboardingName")

        self.drop_area = OnboardingDropArea()
        self.drop_area.filesDropped.connect(self._add_syllabus_paths)

        self.files_label = QLabel("No syllabi selected")
        self.files_label.setObjectName("OnboardingFiles")
        self.files_label.setWordWrap(True)

        browse_btn = QPushButton("Choose files")
        browse_btn.setObjectName("SecondaryButton")
        browse_btn.clicked.connect(self._browse_files)

        self.import_btn = QPushButton("Import syllabi")
        self.import_btn.setObjectName("PrimaryButton")
        self.import_btn.clicked.connect(lambda: self._finish(import_after_start=True))

        continue_btn = QPushButton("Continue without uploading")
        continue_btn.setObjectName("SecondaryButton")
        continue_btn.clicked.connect(lambda: self._finish(import_after_start=False))

        file_row = QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.setSpacing(8)
        file_row.addWidget(self.files_label, 1)
        file_row.addWidget(browse_btn)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(10)
        action_row.addStretch(1)
        action_row.addWidget(continue_btn)
        action_row.addWidget(self.import_btn)

        root = QVBoxLayout()
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        root.addWidget(hero)
        root.addWidget(self.name_edit)
        root.addWidget(self.drop_area)
        root.addLayout(file_row)
        root.addLayout(action_row)
        self.setLayout(root)

        self.setStyleSheet(
            common_page_stylesheet("normal", theme="dark")
            + f"""
            QDialog {{
                background: {colors['window_bg']};
            }}
            QFrame#OnboardingHero {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(48, 167, 88, 0.16), stop:1 rgba(255, 255, 255, 0.04));
                border: 1px solid {colors['border']};
                border-radius: 18px;
            }}
            QLabel#OnboardingTitle {{
                color: {colors['text']};
                font-size: 28px;
                font-weight: 800;
            }}
            QLabel#OnboardingSubtitle, QLabel#OnboardingFiles {{
                color: {colors['muted_text']};
                font-weight: 650;
            }}
            QLabel#OnboardingDropArea {{
                border: 2px dashed {colors['input_border']};
                border-radius: 14px;
                background: rgba(255, 255, 255, 0.035);
                color: {colors['muted_text']};
                font-weight: 700;
            }}
            QLineEdit#OnboardingName {{
                min-height: 38px;
                font-size: 16px;
            }}
            """
        )
        self._update_file_state()

    def _browse_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Choose syllabi",
            "",
            "Syllabus files (*.pdf *.docx *.txt);;All files (*)",
        )
        self._add_syllabus_paths(files)

    def _add_syllabus_paths(self, paths: list[str]) -> None:
        seen = set(self._syllabus_paths)
        for path in paths:
            clean_path = str(path).strip()
            if clean_path and clean_path not in seen:
                self._syllabus_paths.append(clean_path)
                seen.add(clean_path)
        self._update_file_state()

    def _update_file_state(self) -> None:
        count = len(self._syllabus_paths)
        if count == 0:
            self.files_label.setText("No syllabi selected")
            self.import_btn.setEnabled(False)
        elif count == 1:
            self.files_label.setText(Path(self._syllabus_paths[0]).name)
            self.import_btn.setEnabled(True)
        else:
            self.files_label.setText(f"{count} syllabi selected")
            self.import_btn.setEnabled(True)

    def _finish(self, *, import_after_start: bool) -> None:
        if not self.user_name():
            QMessageBox.warning(self, "Name required", "Enter a name to create your user.")
            self.name_edit.setFocus()
            return
        if import_after_start and not self._syllabus_paths:
            QMessageBox.warning(self, "No syllabi selected", "Drop or choose at least one syllabus first.")
            return
        self._import_after_start = bool(import_after_start)
        self.accept()

    def user_name(self) -> str:
        return self.name_edit.text().strip()

    def syllabus_paths(self) -> list[str]:
        return list(self._syllabus_paths) if self._import_after_start else []
