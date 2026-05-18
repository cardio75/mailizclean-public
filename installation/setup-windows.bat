@echo off
setlocal EnableExtensions
title Installation MailizClean

cd /d "%~dp0.."

echo.
echo ========================================
echo Installation de MailizClean
echo ========================================
echo Cette operation peut prendre plusieurs minutes.
echo Le telechargement de Chromium est souvent l'etape la plus longue.
echo.

echo [1/6] Verification de Python...
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo ERREUR: Python est introuvable.
        echo Installez Python 3 depuis https://www.python.org/downloads/windows/
        echo Cochez "Add python.exe to PATH" pendant l'installation.
        goto error
    )
    set "PYTHON_CMD=python"
)
%PYTHON_CMD% --version
if errorlevel 1 goto error
echo.

echo [2/6] Creation de l'environnement Python local...
%PYTHON_CMD% -m venv venv
if errorlevel 1 goto error
echo Environnement cree dans le dossier venv.
echo.

echo [3/6] Activation de l'environnement et mise a jour de pip...
call venv\Scripts\activate.bat
if errorlevel 1 goto error
python -m pip install --upgrade pip
if errorlevel 1 goto error
echo.

echo [4/6] Installation des dependances Python...
echo Cette etape peut afficher beaucoup de lignes, c'est normal.
python -m pip install -r requirements.txt
if errorlevel 1 goto error
echo.

echo [5/6] Installation de Chromium pour Playwright...
echo Cette etape peut etre longue selon la connexion internet.
python -m playwright install chromium
if errorlevel 1 goto error
echo.

echo [6/6] Preparation des dossiers locaux...
if not exist data\logs mkdir data\logs
if not exist data\temp mkdir data\temp
if not exist data\reports mkdir data\reports

if not exist .env (
    copy .env.example .env >nul
    echo Fichier .env cree. La configuration peut aussi se faire depuis le dashboard.
) else (
    echo Fichier .env deja present.
)
echo.

echo ========================================
echo Installation terminee.
echo ========================================
echo Pour lancer MailizClean :
echo   ouvrez le dossier lancement
echo   puis double-cliquez sur lancer-mailizclean.bat
echo.
pause
exit /b 0

:error
echo.
echo ========================================
echo Installation interrompue.
echo ========================================
echo Copiez les lignes d'erreur ci-dessus pour diagnostiquer le probleme.
echo.
pause
exit /b 1
