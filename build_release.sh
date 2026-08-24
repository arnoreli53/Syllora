#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
SPEC_PATH="$ROOT_DIR/Syllora.spec"
SPEC_STEM="${SPEC_PATH:t:r}"
ICON_SOURCE="$ROOT_DIR/icon.png"
ICON_ICNS="$ROOT_DIR/icon.icns"
ICON_WINDOWED_ICNS="$ROOT_DIR/icon-windowed.icns"
INSTALL_HELPER_SOURCE="$ROOT_DIR/install_update.py"
RELEASE_METADATA_TOOL="$ROOT_DIR/release_metadata.py"
GENERATED_BUILD_INFO="$ROOT_DIR/generated_build_info.py"

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

if ! "$VENV_PYTHON" -c 'import PyInstaller' >/dev/null 2>&1; then
  echo "PyInstaller is not installed in $ROOT_DIR/.venv." >&2
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

if [[ ! -f "$ICON_ICNS" || ! -f "$ICON_WINDOWED_ICNS" ]]; then
  echo "Missing a compiled macOS app icon (icon.icns or icon-windowed.icns)." >&2
  exit 1
fi

if [[ ! -f "$INSTALL_HELPER_SOURCE" ]]; then
  echo "Missing install helper at $INSTALL_HELPER_SOURCE" >&2
  exit 1
fi

if [[ ! -f "$RELEASE_METADATA_TOOL" ]]; then
  echo "Missing release metadata helper at $RELEASE_METADATA_TOOL" >&2
  exit 1
fi

cd "$ROOT_DIR"

"$VENV_PYTHON" - <<'PY'
import importlib.util
import sys

required = ("PyPDF2", "PySide6", "certifi", "docx", "openai", "openpyxl")
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    names = ", ".join(missing)
    raise SystemExit(
        f"Missing build dependency: {names}. "
        "Run './.venv/bin/python -m pip install -r requirements-dev.txt' before building."
    )
PY

BASE_APP_VERSION="$("$VENV_PYTHON" - <<'PY'
from version import BASE_APP_VERSION
print(BASE_APP_VERSION)
PY
)"

case "$RELEASE_VERSION" in
  "$BASE_APP_VERSION"|"$BASE_APP_VERSION".*) ;;
  *)
    echo "Requested release version '$RELEASE_VERSION' must be '$BASE_APP_VERSION' or an automated build version starting with '$BASE_APP_VERSION.'." >&2
    exit 1
    ;;
esac

RELEASE_TAG="${SYLLORA_RELEASE_TAG:-v$RELEASE_VERSION}"
BUILD_COMMIT="${GITHUB_SHA:-}"
if [[ -z "$BUILD_COMMIT" ]] && command -v git >/dev/null 2>&1; then
  BUILD_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
fi

cleanup_generated_build_info() {
  /bin/rm -f "$GENERATED_BUILD_INFO"
}
trap cleanup_generated_build_info EXIT

"$VENV_PYTHON" "$RELEASE_METADATA_TOOL" build-info \
  --output "$GENERATED_BUILD_INFO" \
  --version "$RELEASE_VERSION" \
  --commit "$BUILD_COMMIT"

APP_VERSION="$("$VENV_PYTHON" - <<'PY'
from version import APP_VERSION
print(APP_VERSION)
PY
)"

APP_META=("${(@f)$("$VENV_PYTHON" - <<'PY'
from version import APP_BUNDLE_IDENTIFIER, APP_DISPLAY_NAME, APP_RELEASE_ARCHIVE_PREFIX, GITHUB_REPO_SLUG, UPDATE_TEST_CURRENT_VERSION_ENV
print(APP_BUNDLE_IDENTIFIER)
print(APP_DISPLAY_NAME)
print(APP_RELEASE_ARCHIVE_PREFIX)
print(GITHUB_REPO_SLUG)
print(UPDATE_TEST_CURRENT_VERSION_ENV)
PY
)}")
APP_BUNDLE_IDENTIFIER="${APP_META[1]}"
APP_DISPLAY_NAME="${APP_META[2]}"
APP_RELEASE_ARCHIVE_PREFIX="${APP_META[3]}"
GITHUB_REPO_SLUG="${APP_META[4]}"
UPDATE_TEST_ENV_NAME="${APP_META[5]}"

if [[ "$APP_VERSION" != "$RELEASE_VERSION" ]]; then
  echo "Requested release version '$RELEASE_VERSION' does not match APP_VERSION '$APP_VERSION' in version.py." >&2
  exit 1
fi

APP_BUNDLE="dist/${APP_DISPLAY_NAME}.app"
ONEDIR_DIR="dist/${APP_DISPLAY_NAME}"
ZIP_NAME="${APP_RELEASE_ARCHIVE_PREFIX}-${RELEASE_VERSION}-macos.zip"
ZIP_PATH="dist/${ZIP_NAME}"
PYINSTALLER_LOG_DIR="build"
PYINSTALLER_LOG_PATH="${PYINSTALLER_LOG_DIR}/pyinstaller-${SPEC_STEM}.log"
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

mkdir -p "$PYINSTALLER_LOG_DIR"
if ! "$VENV_PYTHON" -m PyInstaller --clean --noconfirm "$SPEC_PATH" >"$PYINSTALLER_LOG_PATH" 2>&1; then
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

BUNDLE_IDENTIFIER="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP_BUNDLE/Contents/Info.plist" 2>/dev/null || true)"
BUNDLE_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP_BUNDLE/Contents/Info.plist" 2>/dev/null || true)"
if [[ "$BUNDLE_IDENTIFIER" != "$APP_BUNDLE_IDENTIFIER" ]]; then
  echo "Packaged app has an unexpected bundle identifier: '${BUNDLE_IDENTIFIER:-<missing>}'" >&2
  exit 1
fi
if [[ "$BUNDLE_VERSION" != "$RELEASE_VERSION" ]]; then
  echo "Packaged app has an unexpected version: '${BUNDLE_VERSION:-<missing>}'" >&2
  exit 1
fi

CODESIGN_IDENTITY="${SYLLORA_CODESIGN_IDENTITY:--}"
/usr/bin/codesign --force --deep --sign "$CODESIGN_IDENTITY" "$APP_BUNDLE"
/usr/bin/codesign --verify --deep --strict "$APP_BUNDLE"

rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_BUNDLE" "$ZIP_PATH"

SHA256="$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')"

"$VENV_PYTHON" "$RELEASE_METADATA_TOOL" manifest \
  --output "dist/latest.json" \
  --version "$RELEASE_VERSION" \
  --repository "$GITHUB_REPO_SLUG" \
  --release-tag "$RELEASE_TAG" \
  --asset-name "$ZIP_NAME" \
  --sha256 "$SHA256" \
  --note "${SYLLORA_RELEASE_NOTE:-Automatic update built from the latest code on the main branch.}"

cat <<EOF
Built app bundle: $APP_BUNDLE
Built onedir output: $ONEDIR_DIR
Built release zip: $ZIP_PATH
Built update manifest: dist/latest.json
SHA256: $SHA256
PyInstaller log: $PYINSTALLER_LOG_PATH

Verified helper paths:
  Onedir runtime: $ONEDIR_HELPER
  Packaged app:   $EXPECTED_APP_HELPER

Local updater test command:
  launchctl setenv $UPDATE_TEST_ENV_NAME 1.0.0
  open -na "/Applications/$APP_DISPLAY_NAME.app"
  launchctl unsetenv $UPDATE_TEST_ENV_NAME

Update manifest:
EOF

cat dist/latest.json
