#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
VENV_PYINSTALLER="$ROOT_DIR/.venv/bin/pyinstaller"
SPEC_PATH="$ROOT_DIR/Syllora.spec"
SPEC_STEM="${SPEC_PATH:t:r}"
ICON_SOURCE="$ROOT_DIR/icon.png"
ICONSET_DIR="$ROOT_DIR/icon.iconset"
ICON_ICNS="$ROOT_DIR/icon.icns"
ICON_WINDOWED_ICNS="$ROOT_DIR/icon-windowed.icns"
INSTALL_HELPER_SOURCE="$ROOT_DIR/install_update.py"

if [[ $# -ne 1 ]]; then
  cat >&2 <<'EOF'
Usage: ./build_release.sh <release_version>

Example:
  ./build_release.sh 1.0.2

Local updater testing without rebuilding an older artifact:
  launchctl setenv SYLLORA_UPDATE_TEST_CURRENT_VERSION 1.0.0
  open -na "/Applications/Syllora.app"
  launchctl unsetenv SYLLORA_UPDATE_TEST_CURRENT_VERSION
EOF
  exit 1
fi

RELEASE_VERSION="$1"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Missing virtualenv Python at $VENV_PYTHON" >&2
  exit 1
fi

if [[ ! -x "$VENV_PYINSTALLER" ]]; then
  echo "Missing PyInstaller at $VENV_PYINSTALLER" >&2
  exit 1
fi

if [[ ! -f "$SPEC_PATH" ]]; then
  echo "Missing spec file at $SPEC_PATH" >&2
  exit 1
fi

if [[ ! -f "$ICON_SOURCE" ]]; then
  echo "Missing app icon PNG at $ICON_SOURCE" >&2
  exit 1
fi

if [[ ! -f "$INSTALL_HELPER_SOURCE" ]]; then
  echo "Missing install helper at $INSTALL_HELPER_SOURCE" >&2
  exit 1
fi

for tool in sips iconutil; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing required macOS icon build tool: $tool" >&2
    exit 1
  fi
done

cd "$ROOT_DIR"

"$VENV_PYTHON" - <<'PY'
import importlib.util
import sys

missing = [name for name in ("openai",) if importlib.util.find_spec(name) is None]
if missing:
    names = ", ".join(missing)
    raise SystemExit(
        f"Missing build dependency: {names}. "
        "Run './.venv/bin/python -m pip install openai' before building."
    )
PY

APP_VERSION="$("$VENV_PYTHON" - <<'PY'
from version import APP_VERSION
print(APP_VERSION)
PY
)"

APP_META=("${(@f)$("$VENV_PYTHON" - <<'PY'
from version import APP_DISPLAY_NAME, APP_RELEASE_ARCHIVE_PREFIX, GITHUB_REPO_SLUG, UPDATE_TEST_CURRENT_VERSION_ENV
print(APP_DISPLAY_NAME)
print(APP_RELEASE_ARCHIVE_PREFIX)
print(GITHUB_REPO_SLUG)
print(UPDATE_TEST_CURRENT_VERSION_ENV)
PY
)}")
APP_DISPLAY_NAME="${APP_META[1]}"
APP_RELEASE_ARCHIVE_PREFIX="${APP_META[2]}"
GITHUB_REPO_SLUG="${APP_META[3]}"
UPDATE_TEST_ENV_NAME="${APP_META[4]}"

if [[ "$APP_VERSION" != "$RELEASE_VERSION" ]]; then
  echo "Requested release version '$RELEASE_VERSION' does not match APP_VERSION '$APP_VERSION' in version.py." >&2
  exit 1
fi

APP_BUNDLE="dist/${APP_DISPLAY_NAME}.app"
ONEDIR_DIR="dist/${APP_DISPLAY_NAME}"
ZIP_NAME="${APP_RELEASE_ARCHIVE_PREFIX}-${RELEASE_VERSION}-macos.zip"
ZIP_PATH="dist/${ZIP_NAME}"
PYINSTALLER_LOG_DIR="build/${SPEC_STEM}"
PYINSTALLER_LOG_PATH="${PYINSTALLER_LOG_DIR}/pyinstaller-build.log"
BUNDLE_RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"
BUNDLED_ICON="$BUNDLE_RESOURCES_DIR/icon.icns"
BUNDLED_ICON_PNG="$BUNDLE_RESOURCES_DIR/icon.png"
BUNDLED_WINDOWED_ICON="$BUNDLE_RESOURCES_DIR/icon-windowed.icns"
ONEDIR_INTERNAL_DIR="$ONEDIR_DIR/_internal"
ONEDIR_ICON="$ONEDIR_INTERNAL_DIR/icon.icns"
ONEDIR_ICON_PNG="$ONEDIR_INTERNAL_DIR/icon.png"
ONEDIR_WINDOWED_ICON="$ONEDIR_INTERNAL_DIR/icon-windowed.icns"
ONEDIR_HELPER_DIR="$ONEDIR_DIR/_internal/update_helper"
APP_HELPER_DIR="$BUNDLE_RESOURCES_DIR/update_helper"

rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"

for size in 16 32 128 256 512; do
  retina_size=$((size * 2))
  sips -z "$size" "$size" "$ICON_SOURCE" --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null
  sips -z "$retina_size" "$retina_size" "$ICON_SOURCE" --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" >/dev/null
done

iconutil -c icns "$ICONSET_DIR" -o "$ICON_ICNS"
/bin/cp -f "$ICON_ICNS" "$ICON_WINDOWED_ICNS"

mkdir -p "$PYINSTALLER_LOG_DIR"
if ! "$VENV_PYINSTALLER" --clean --noconfirm "$SPEC_PATH" >"$PYINSTALLER_LOG_PATH" 2>&1; then
  echo "PyInstaller build failed. Full log:" >&2
  cat "$PYINSTALLER_LOG_PATH" >&2
  exit 1
fi

if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "Expected app bundle was not produced: $APP_BUNDLE" >&2
  exit 1
fi

if [[ ! -d "$ONEDIR_DIR" ]]; then
  echo "Expected onedir output was not produced: $ONEDIR_DIR" >&2
  exit 1
fi

/bin/mkdir -p "$BUNDLE_RESOURCES_DIR"
/bin/mkdir -p "$ONEDIR_INTERNAL_DIR"
/bin/mkdir -p "$ONEDIR_HELPER_DIR"
/bin/mkdir -p "$APP_HELPER_DIR"
/bin/cp -f "$ICON_ICNS" "$BUNDLED_ICON"
/bin/cp -f "$ICON_SOURCE" "$BUNDLED_ICON_PNG"
/bin/cp -f "$ICON_WINDOWED_ICNS" "$BUNDLED_WINDOWED_ICON"
/bin/cp -f "$ICON_ICNS" "$ONEDIR_ICON"
/bin/cp -f "$ICON_SOURCE" "$ONEDIR_ICON_PNG"
/bin/cp -f "$ICON_WINDOWED_ICNS" "$ONEDIR_WINDOWED_ICON"
/bin/cp -f "$INSTALL_HELPER_SOURCE" "$ONEDIR_HELPER_DIR/install_update.py"
/bin/cp -f "$INSTALL_HELPER_SOURCE" "$APP_HELPER_DIR/install_update.py"
/usr/libexec/PlistBuddy -c "Set :CFBundleIconFile icon-windowed.icns" "$APP_BUNDLE/Contents/Info.plist" >/dev/null 2>&1 || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string icon-windowed.icns" "$APP_BUNDLE/Contents/Info.plist" >/dev/null

if [[ ! -f "$BUNDLED_ICON" ]]; then
  echo "Bundled app icon is missing after post-build fixup: $BUNDLED_ICON" >&2
  exit 1
fi

if [[ ! -f "$BUNDLED_WINDOWED_ICON" ]]; then
  echo "Bundled windowed icon is missing after post-build fixup: $BUNDLED_WINDOWED_ICON" >&2
  exit 1
fi

BUNDLE_ICON_NAME="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$APP_BUNDLE/Contents/Info.plist" 2>/dev/null || true)"
if [[ "$BUNDLE_ICON_NAME" != "icon-windowed.icns" ]]; then
  echo "Packaged app is using an unexpected bundle icon: '${BUNDLE_ICON_NAME:-<missing>}'" >&2
  exit 1
fi

WARN_FILE="build/${SPEC_STEM}/warn-${SPEC_STEM}.txt"
if [[ -f "$WARN_FILE" ]] && grep -F "missing module named openai - imported by syllabus_import" "$WARN_FILE" >/dev/null 2>&1; then
  echo "OpenAI SDK was not bundled into this build." >&2
  exit 1
fi

ONEDIR_HELPER="$ONEDIR_HELPER_DIR/install_update.py"
APP_HELPER_RESOURCE="$APP_HELPER_DIR/install_update.py"
APP_HELPER_FRAMEWORKS="$APP_BUNDLE/Contents/Frameworks/update_helper/install_update.py"

if [[ ! -f "$ONEDIR_HELPER" ]]; then
  echo "install_update.py was not bundled into the onedir runtime root: $ONEDIR_HELPER" >&2
  exit 1
fi

EXPECTED_APP_HELPER=""
for candidate in "$APP_HELPER_RESOURCE" "$APP_HELPER_FRAMEWORKS"; do
  if [[ -f "$candidate" ]]; then
    EXPECTED_APP_HELPER="$candidate"
    break
  fi
done

if [[ -z "$EXPECTED_APP_HELPER" ]]; then
  echo "install_update.py was not bundled into the packaged app runtime paths." >&2
  echo "Checked: $APP_HELPER_RESOURCE" >&2
  echo "Checked: $APP_HELPER_FRAMEWORKS" >&2
  exit 1
fi

rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_BUNDLE" "$ZIP_PATH"

SHA256="$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')"

cat <<EOF
Built app bundle: $APP_BUNDLE
Built onedir output: $ONEDIR_DIR
Built release zip: $ZIP_PATH
SHA256: $SHA256
PyInstaller log: $PYINSTALLER_LOG_PATH

Verified helper paths:
  Onedir runtime: $ONEDIR_HELPER
  Packaged app:   $EXPECTED_APP_HELPER

Local updater test command:
  launchctl setenv $UPDATE_TEST_ENV_NAME 1.0.0
  open -na "/Applications/$APP_DISPLAY_NAME.app"
  launchctl unsetenv $UPDATE_TEST_ENV_NAME

Update manifest snippet:
{
  "latest_version": "$RELEASE_VERSION",
  "macos_zip_url": "https://github.com/$GITHUB_REPO_SLUG/releases/download/v$RELEASE_VERSION/$ZIP_NAME",
  "sha256": "$SHA256",
  "release_notes": [
    "Add release notes here"
  ]
}
EOF
