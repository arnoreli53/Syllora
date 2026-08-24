# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from version import APP_BUNDLE_IDENTIFIER, APP_DISPLAY_NAME, APP_VERSION

ICON_ICNS = Path.cwd() / "icon.icns"
ICON_WINDOWED_ICNS = Path.cwd() / "icon-windowed.icns"

for icon_path in (ICON_ICNS, ICON_WINDOWED_ICNS):
    if not icon_path.is_file():
        raise SystemExit(f'Missing required app icon: {icon_path}')

try:
    import certifi  # noqa: F401
    import docx  # noqa: F401
    import openai  # noqa: F401
    import openpyxl  # noqa: F401
    import PyPDF2  # noqa: F401
except ImportError as exc:
    raise SystemExit(
        "The build environment is missing a runtime dependency. "
        "Run './.venv/bin/python -m pip install -r requirements-dev.txt' before building."
    ) from exc


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('install_update.py', 'update_helper'),
        ('icon.icns', '.'),
        ('icon-windowed.icns', '.'),
        ('icon.png', '.'),
    ],
    hiddenimports=['certifi', 'docx', 'openai', 'openpyxl', 'PyPDF2'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_DISPLAY_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=os.environ.get('SYLLORA_CODESIGN_IDENTITY') or None,
    entitlements_file=None,
    icon=['icon-windowed.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_DISPLAY_NAME,
)
app = BUNDLE(
    coll,
    name=f'{APP_DISPLAY_NAME}.app',
    icon='icon-windowed.icns',
    bundle_identifier=APP_BUNDLE_IDENTIFIER,
    info_plist={
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
    },
)
