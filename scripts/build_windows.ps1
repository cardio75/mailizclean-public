$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path "venv")) {
    py -3 -m venv venv
}

.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt

# Store Chromium inside the Playwright package so PyInstaller can collect it.
$env:PLAYWRIGHT_BROWSERS_PATH = "0"
.\venv\Scripts\python.exe -m playwright install chromium

.\venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name MailizClean `
    --collect-data playwright `
    --add-data "config/cleanup_rules.json;config" `
    --add-data ".env.example;." `
    mailiz_app.py

Write-Host ""
Write-Host "Build Windows termine: dist\MailizClean\MailizClean.exe"
Write-Host "Test local: .\dist\MailizClean\MailizClean.exe"
