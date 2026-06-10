$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

$AppName = "MailizClean"
$Version = if ($env:MAILIZCLEAN_VERSION) { $env:MAILIZCLEAN_VERSION } else { "0.1.0" }
$ZipPath = "dist\$AppName-Windows-$Version.zip"

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
    --name $AppName `
    --additional-hooks-dir packaging\pyinstaller-hooks `
    --add-data "config/cleanup_rules.json;config" `
    --add-data ".env.example;." `
    mailiz_app.py

$PlaywrightPackage = .\venv\Scripts\python.exe -c "from pathlib import Path; import playwright; print(Path(playwright.__file__).parent / 'driver' / 'package')"
$BrowsersSource = Join-Path $PlaywrightPackage ".local-browsers"
$PlaywrightBuildPackage = "dist\$AppName\_internal\playwright\driver\package"
$BrowsersDestination = Join-Path $PlaywrightBuildPackage ".local-browsers"

if (-not (Test-Path $BrowsersSource)) {
    throw "Navigateurs Playwright introuvables dans le venv: $BrowsersSource"
}
if (-not (Test-Path $PlaywrightBuildPackage)) {
    throw "Dossier Playwright introuvable dans le build: $PlaywrightBuildPackage"
}
if (Test-Path $BrowsersDestination) {
    Remove-Item $BrowsersDestination -Recurse -Force
}
Copy-Item $BrowsersSource $BrowsersDestination -Recurse

if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

Compress-Archive `
    -Path "dist\$AppName" `
    -DestinationPath $ZipPath `
    -Force

Write-Host ""
Write-Host "Build Windows termine:"
Write-Host "- Executable: dist\$AppName\$AppName.exe"
Write-Host "- Archive a distribuer: $ZipPath"
Write-Host "Test local: .\dist\$AppName\$AppName.exe"
Write-Host "Important: ne pas distribuer le .exe seul, il a besoin du dossier dist\$AppName complet."
