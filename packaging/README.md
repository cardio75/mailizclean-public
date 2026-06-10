# Packaging MailizClean

Objectif : produire une application autonome pour des utilisateurs sans Python.

## macOS

```bash
./scripts/build_macos.sh
```

Sortie attendue :

```text
dist/MailizClean.app
dist/MailizClean-macOS-0.1.0.dmg
```

Le `.dmg` contient `MailizClean.app` et un raccourci vers `Applications`.

## Windows

Dans PowerShell :

```powershell
.\scripts\build_windows.ps1
```

Sortie attendue :

```text
dist\MailizClean\MailizClean.exe
dist\MailizClean-Windows-0.1.0.zip
```

Pour Windows, distribuer le `.zip` ou le dossier `dist\MailizClean` complet. Ne pas envoyer le `.exe` seul : il a besoin des fichiers voisins, dont Chromium/Playwright.

## Choix techniques

- Build `onedir`, plus fiable avec Playwright/Chromium que `onefile`.
- Le `.exe` Windows est dans le dossier `dist\MailizClean`.
- Le `.dmg` macOS est genere avec `hdiutil`.
- Donnees utilisateur hors bundle :
  - macOS : `~/Library/Application Support/MailizClean`
  - Windows : `%APPDATA%\MailizClean`
- La premiere execution cree un `.env` utilisateur a partir de `.env.example`.
- Chromium est installe avec `PLAYWRIGHT_BROWSERS_PATH=0` avant PyInstaller pour etre collectable dans le build.

## Limites avant diffusion large

- Signature/notarisation macOS non encore configuree.
- Signature Windows non encore configuree.
- Stockage trousseau systeme non encore implemente.
- Les builds doivent etre produits sur chaque OS cible.
