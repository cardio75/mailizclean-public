#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo
echo "========================================"
echo "Installation de MailizClean"
echo "========================================"
echo "Cette operation peut prendre plusieurs minutes."
echo "Le telechargement de Chromium est souvent l'etape la plus longue."
echo

echo "[1/6] Verification de Python..."
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERREUR: python3 est introuvable."
    echo "Installez Python 3, puis relancez installation/setup-mac.sh."
    exit 1
fi
python3 --version
echo

echo "[2/6] Creation de l'environnement Python local..."
python3 -m venv venv
echo "Environnement cree dans le dossier venv."
echo

echo "[3/6] Activation de l'environnement et mise a jour de pip..."
source venv/bin/activate
python -m pip install --upgrade pip
echo

echo "[4/6] Installation des dependances Python..."
echo "Cette etape peut afficher beaucoup de lignes, c'est normal."
python -m pip install -r requirements.txt
echo

echo "[5/6] Installation de Chromium pour Playwright..."
echo "Cette etape peut etre longue selon la connexion internet."
python -m playwright install chromium
echo

echo "[6/6] Preparation des dossiers locaux..."
mkdir -p data/logs data/temp data/reports
chmod +x lancement/lancer-mailizclean.sh lancement/lancer-mailizclean.command installation/installer-mailizclean.command 2>/dev/null || true

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Fichier .env cree. La configuration peut aussi se faire depuis le dashboard."
else
    echo "Fichier .env deja present."
fi
echo

echo "========================================"
echo "Installation terminee."
echo "========================================"
echo "Pour lancer MailizClean :"
echo "  ouvrez le dossier lancement"
echo "  puis double-cliquez sur lancer-mailizclean.command"
echo
