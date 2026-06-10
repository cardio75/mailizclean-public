#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

APP_NAME="MailizClean"
VERSION="${MAILIZCLEAN_VERSION:-0.1.0}"
DMG_STAGING_DIR="build/dmg"
DMG_PATH="dist/${APP_NAME}-macOS-${VERSION}.dmg"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Erreur: le build macOS doit etre lance sur un Mac."
  exit 1
fi

if [ ! -d venv ]; then
  python3 -m venv venv
fi

source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-build.txt

# Store Chromium inside the Playwright package so PyInstaller can collect it.
PLAYWRIGHT_BROWSERS_PATH=0 python -m playwright install chromium
BROWSERS_SOURCE="$(find venv/lib -path '*/site-packages/playwright/driver/package/.local-browsers' -type d -print -quit 2>/dev/null || true)"

python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --name "$APP_NAME" \
  --osx-bundle-identifier "fr.mailizclean.app" \
  --additional-hooks-dir packaging/pyinstaller-hooks \
  --add-data "config/cleanup_rules.json:config" \
  --add-data ".env.example:." \
  mailiz_app.py

PLAYWRIGHT_PACKAGE_DIR="$(find "dist/${APP_NAME}.app" "dist/${APP_NAME}" -path '*/playwright/driver/package' -type d -print -quit 2>/dev/null || true)"
if [ -z "$PLAYWRIGHT_PACKAGE_DIR" ]; then
  echo "Erreur: dossier Playwright introuvable dans le build."
  exit 1
fi
if [ -z "$BROWSERS_SOURCE" ]; then
  echo "Erreur: navigateurs Playwright introuvables dans le venv."
  exit 1
fi

rm -rf "${PLAYWRIGHT_PACKAGE_DIR}/.local-browsers"
cp -R "$BROWSERS_SOURCE" "$PLAYWRIGHT_PACKAGE_DIR/.local-browsers"
codesign --force --deep --sign - "dist/${APP_NAME}.app"

rm -rf "$DMG_STAGING_DIR"
mkdir -p "$DMG_STAGING_DIR"
cp -R "dist/${APP_NAME}.app" "$DMG_STAGING_DIR/"
ln -s /Applications "$DMG_STAGING_DIR/Applications"

rm -f "$DMG_PATH"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$DMG_STAGING_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo
echo "Build macOS termine:"
echo "- Application: dist/${APP_NAME}.app"
echo "- DMG: ${DMG_PATH}"
echo "Test local: open dist/${APP_NAME}.app"
