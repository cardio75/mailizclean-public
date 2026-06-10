# Release

## Verification avant publication

```bash
venv/bin/python -m py_compile config/settings.py mailiz_app.py mailiz_cleaner.py mailiz_dashboard.py
```

Verifier ensuite :

- aucun fichier `.env` suivi par Git ;
- aucun fichier `data/reports`, `data/logs` ou `data/temp` suivi par Git ;
- le dashboard demarre avec `python mailiz_cleaner.py app --no-browser` ;
- le build macOS ou Windows demarre depuis un clone propre.

## Build macOS

```bash
./scripts/build_macos.sh
```

Sortie :

```text
dist/MailizClean.app
dist/MailizClean-macOS-0.1.0.dmg
```

## Build Windows

```powershell
.\scripts\build_windows.ps1
```

Sortie :

```text
dist\MailizClean\MailizClean.exe
dist\MailizClean-Windows-0.1.0.zip
```

Distribuer le `.dmg` sur Mac. Sur Windows, distribuer le `.zip` ou le dossier `dist\MailizClean` complet, pas le `.exe` seul.

## A faire avant diffusion large

- signature et notarisation macOS ;
- signature Windows ;
- checksums des archives ;
- test sur machine sans Python ;
- documentation utilisateur finale.
