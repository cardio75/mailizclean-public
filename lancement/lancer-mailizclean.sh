#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x "venv/bin/python" ]; then
    echo "MailizClean n'est pas encore installe."
    echo "Lancez d'abord installation/setup-mac.sh, puis relancez ce fichier."
    exit 1
fi

echo "Lancement de MailizClean..."
echo "Gardez cette fenetre ouverte pendant l'utilisation."
echo
venv/bin/python mailiz_cleaner.py app
