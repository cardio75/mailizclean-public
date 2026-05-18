#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x "venv/bin/python" ]; then
    echo "MailizClean n'est pas encore installe."
    echo "Ouvrez d'abord le dossier installation et lancez installer-mailizclean.command."
    echo "Relancez ensuite ce fichier."
    echo
    read -r -p "Appuyez sur Entree pour fermer..."
    exit 1
fi

echo "Lancement de MailizClean..."
echo "Gardez cette fenetre ouverte pendant l'utilisation."
echo
venv/bin/python mailiz_cleaner.py app

echo
echo "MailizClean est arrete."
read -r -p "Appuyez sur Entree pour fermer..."
