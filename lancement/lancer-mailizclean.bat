@echo off
setlocal EnableExtensions
title MailizClean

cd /d "%~dp0.."

if not exist venv\Scripts\python.exe (
    echo MailizClean n'est pas encore installe.
    echo Ouvrez d'abord le dossier installation et lancez setup-windows.bat.
    echo Relancez ensuite ce fichier.
    echo.
    pause
    exit /b 1
)

echo Lancement de MailizClean...
echo Gardez cette fenetre ouverte pendant l'utilisation.
echo.
venv\Scripts\python.exe mailiz_cleaner.py app

echo.
echo MailizClean est arrete.
pause
