#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Installation de MailizClean..."
echo
bash installation/setup-mac.sh

echo
echo "Installation terminee."
echo "Pour lancer MailizClean, ouvrez le dossier lancement puis double-cliquez sur lancer-mailizclean.command."
echo
read -r -p "Appuyez sur Entree pour fermer..."
