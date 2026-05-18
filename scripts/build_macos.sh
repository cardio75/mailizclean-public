#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d venv ]; then
  python3 -m venv venv
fi

source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-build.txt

# Store Chromium inside the Playwright package so PyInstaller can collect it.
PLAYWRIGHT_BROWSERS_PATH=0 python -m playwright install chromium

python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --name MailizClean \
  --collect-data playwright \
  --add-data "config/cleanup_rules.json:config" \
  --add-data ".env.example:." \
  mailiz_app.py

echo
echo "Build macOS termine: dist/MailizClean.app"
echo "Test local: open dist/MailizClean.app"
