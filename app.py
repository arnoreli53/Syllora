import ctypes
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog
from db_qt import close_db, open_db
from migrate import ensure_schema
from onboarding import OnboardingDialog
from ui_common import apply_app_theme
from ui_main import MainWindow
from ui_settings import get_theme
from user_profiles import activate_current_profile, create_profile, needs_onboarding
from version import APP_DISPLAY_NAME, APP_NAME


def _current_app_bundle() -> Path | None:
    exe = Path(sys.executable).resolve()
    if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        bundle = exe.parent.parent.parent
        if bundle.suffix == ".app":
            return bundle
    return None


def _app_icon_path() -> Path | None:
    icns_candidates: list[Path] = []
    png_candidates: list[Path] = []
    seen: set[Path] = set()

    def add_candidate(path: Path) -> None:
        if path in seen:
            return
        seen.add(path)
        if path.suffix.lower() == ".icns":
            icns_candidates.append(path)
        else:
            png_candidates.append(path)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass).resolve()
        add_candidate(base / "icon.icns")
        add_candidate(base / "icon-windowed.icns")
        add_candidate(base / "icon.png")

    executable_parent = Path(sys.executable).resolve().parent
    add_candidate(executable_parent / "icon.icns")
    add_candidate(executable_parent / "icon-windowed.icns")
    add_candidate(executable_parent / "icon.png")
    add_candidate(executable_parent / "_internal" / "icon.icns")
    add_candidate(executable_parent / "_internal" / "icon-windowed.icns")
    add_candidate(executable_parent / "_internal" / "icon.png")

    bundle = _current_app_bundle()
    if bundle is not None:
        resources = bundle / "Contents" / "Resources"
        add_candidate(resources / "icon.icns")
        add_candidate(resources / "icon-windowed.icns")
        add_candidate(resources / "icon.png")

    here = Path(__file__).resolve().parent
    add_candidate(here / "icon.icns")
    add_candidate(here / "icon-windowed.icns")
    add_candidate(here / "icon.png")

    for path in [*icns_candidates, *png_candidates]:
        if path.exists():
            return path
    return None


def _set_macos_application_icon(icon_path: Path) -> None:
    if sys.platform != "darwin" or not icon_path.exists():
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

        ns_string_class = _cls("NSString")
        ns_image_class = _cls("NSImage")
        ns_app_class = _cls("NSApplication")
        if not ns_string_class or not ns_image_class or not ns_app_class:
            return

        ns_path = _send(
            _send(ns_string_class, "alloc"),
            "initWithUTF8String:",
            icon_path.as_posix().encode("utf-8"),
            restype=c_void_p,
            argtypes=[c_void_p, c_void_p, c_char_p],
        )
        if not ns_path:
            return

        image = _send(
            _send(ns_image_class, "alloc"),
            "initWithContentsOfFile:",
            ns_path,
            restype=c_void_p,
            argtypes=[c_void_p, c_void_p, c_void_p],
        )
        if not image:
            return

        application = _send(ns_app_class, "sharedApplication")
        if not application:
            return

        _send(
            application,
            "setApplicationIconImage:",
            image,
            restype=None,
            argtypes=[c_void_p, c_void_p, c_void_p],
        )
    except Exception:
        pass


def _apply_runtime_icon(app: QApplication, window: MainWindow, icon: QIcon | None, icon_path: Path | None) -> None:
    if icon is not None and not icon.isNull():
        app.setWindowIcon(icon)
        window.setWindowIcon(icon)
    if icon_path is not None:
        _set_macos_application_icon(icon_path)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setOrganizationName(APP_NAME)
    bundle = _current_app_bundle()
    use_runtime_icon = not (sys.platform == "darwin" and bundle is not None)
    icon_path = _app_icon_path()
    icon = QIcon(str(icon_path)) if icon_path is not None else None

    onboarding_paths: list[str] = []
    if needs_onboarding():
        apply_app_theme(app, "dark")
        onboarding = OnboardingDialog(icon_path)
        if onboarding.exec() != QDialog.DialogCode.Accepted:
            return 0
        create_profile(onboarding.user_name(), set_current=True)
        onboarding_paths = onboarding.syllabus_paths()
    else:
        activate_current_profile()

    open_db()
    ensure_schema()
    apply_app_theme(app, get_theme())
    window = MainWindow()
    window.show()
    if onboarding_paths and hasattr(window, "import_syllabi_from_paths"):
        QTimer.singleShot(350, lambda: window.import_syllabi_from_paths(onboarding_paths))
    if use_runtime_icon:
        _apply_runtime_icon(app, window, icon, icon_path)
        QTimer.singleShot(0, lambda: _apply_runtime_icon(app, window, icon, icon_path))
        QTimer.singleShot(250, lambda: _apply_runtime_icon(app, window, icon, icon_path))
    exit_code = app.exec()

    try:
        if window is not None:
            window.setCentralWidget(None)
            window.deleteLater()
        app.processEvents()
    except Exception:
        pass
    finally:
        close_db()

    return exit_code
if __name__ == "__main__":
    raise SystemExit(main())
