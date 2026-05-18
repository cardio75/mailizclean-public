# Confidentialite

MailizClean est concu comme un utilitaire local.

Outil fourni sans garantie. L'utilisateur reste responsable de la verification des messages selectionnes et des suppressions effectuees dans Mailiz.

- Les scans et propositions sont ecrits localement.
- Les rapports contiennent potentiellement des donnees nominatives.
- Les actions Mailiz passent par une session Chromium locale pilotee par Playwright.
- Aucun serveur MailizClean distant n'est utilise par le dashboard.
- Les identifiants sont enregistres dans le fichier `.env` local de l'utilisateur.
  Ce stockage sera remplace plus tard par le trousseau systeme si le projet est diffuse largement.

## Emplacement des donnees

En developpement, les donnees sont dans `data/`.

Dans une application packagee :

- macOS : `~/Library/Application Support/MailizClean`
- Windows : `%APPDATA%\MailizClean`

## Avant partage d'un bug

Utiliser uniquement des captures ou extraits anonymises. Ne jamais joindre un
rapport JSON/CSV reel sans l'avoir purge des informations patient.
