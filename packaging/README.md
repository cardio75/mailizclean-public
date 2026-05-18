# Packaging MailizClean

Objectif : produire une application autonome pour des utilisateurs sans Python.

## macOS

```bash
./scripts/build_macos.sh
```

Sortie attendue :

```text
dist/MailizClean.app
```

## Windows

Dans PowerShell :

```powershell
.\scripts\build_windows.ps1
```

Sortie attendue :

```text
dist\MailizClean\MailizClean.exe
```

## Choix techniques

- Build `onedir`, plus fiable avec Playwright/Chromium que `onefile`.
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
