# MailizClean

MailizClean est un utilitaire local pour aider a liberer de l'espace dans une boite Mailiz/Roundcube MSSante.

L'objectif est simple : scanner la boite, choisir un scenario, verifier une proposition, mettre les messages choisis en corbeille, vider la corbeille apres confirmation, puis verifier le resultat.

MailizClean fonctionne localement. Il ne fournit pas de service distant et n'envoie pas les rapports a un serveur MailizClean.

Outil fourni sans garantie. L'utilisateur doit verifier les propositions avant toute mise en corbeille ou suppression definitive.

## Etat actuel

- Connexion Mailiz avec code OTP recu par email.
- Configuration depuis le dashboard.
- Scan mono-passe des messages recus ou envoyes : chaque page Roundcube est parcourue une seule fois.
- Lecture du quota Mailiz.
- Classement par type de document : biologie, imagerie, consultation, lettre, synthese de sejour, autre.
- Propositions de nettoyage a partir de scenarios.
- Deplacement controle des messages coches vers la corbeille Mailiz.
- Vidage de la corbeille avec confirmation.
- Scan de controle apres vidage.
- Packaging macOS et Windows avec PyInstaller.

## Pour les utilisateurs

Le parcours simple est de telecharger une application prete a lancer depuis la page des releases GitHub :

[https://github.com/cardio75/mailizclean-public/releases](https://github.com/cardio75/mailizclean-public/releases)

Pour la version `0.1.0`, telecharger uniquement l'installeur adapte :

- Mac : `MailizClean-macOS-0.1.0.dmg`
- Windows : `MailizClean-Windows-0.1.0.zip`

Ne pas telecharger `Source code.zip` ou `Source code.tar.gz` pour une utilisation normale : ces fichiers sont ajoutes automatiquement par GitHub et servent seulement aux developpeurs.

La notice utilisateur detaillee est ici : `docs/USER_GUIDE.md`.

## Installation Mac

1. Telecharger `MailizClean-macOS-0.1.0.dmg` depuis la release GitHub.
2. Ouvrir le fichier `.dmg`.
3. Glisser `MailizClean.app` dans `Applications`.
4. Lancer `MailizClean.app`.

Si macOS refuse l'ouverture parce que l'application n'est pas signee, faire clic droit sur `MailizClean.app`, puis `Ouvrir`.

## Installation Windows

1. Telecharger `MailizClean-Windows-0.1.0.zip` depuis la release GitHub.
2. Decompresser le fichier `.zip`.
3. Ouvrir le dossier `MailizClean`.
4. Double-cliquer sur `MailizClean.exe`.

Ne pas sortir `MailizClean.exe` de son dossier : il a besoin des fichiers qui sont a cote de lui.

Si Windows affiche un avertissement de securite, c'est attendu pour cette premiere version non signee.

## Connexion Mailiz

MailizClean propose deux facons de se connecter a Mailiz.

### Connexion manuelle

C'est le mode le plus simple pour ne pas confier ses identifiants a l'application.

MailizClean ouvre une fenetre Chromium. L'utilisateur se connecte lui-meme sur le site Mailiz, choisit son mode de reception du code OTP si besoin, saisit son mot de passe et son code OTP, puis attend de voir sa boite de reception Mailiz.

Ensuite, il revient dans MailizClean. Des que MailizClean detecte la boite Mailiz, le scan ou l'action demandee demarre automatiquement.

Dans ce mode, il n'est pas necessaire de saisir le mot de passe Mailiz, le mot de passe de la boite OTP ou les identifiants dans MailizClean.

### Connexion automatique

Ce mode est utile pour un usage repete.

MailizClean utilise les identifiants enregistres localement sur l'ordinateur :

- l'adresse MSSante utilisee sur le site Mailiz ;
- le mot de passe Mailiz ;
- l'adresse email ordinaire renseignee dans Mailiz, celle qui recoit le code OTP ;
- le mot de passe ou mot de passe d'application de cette boite email ;
- le serveur IMAP de cette boite email.

Ces informations restent sur l'ordinateur. Voir aussi `docs/PRIVACY.md`.

Dans les deux modes, une fenetre Chromium peut s'ouvrir. Apres la connexion manuelle eventuelle, elle peut etre minimisee, mais il ne faut pas cliquer dedans pendant que MailizClean travaille.

## Parcours normal

1. Ouvrir MailizClean.
2. Completer la configuration si besoin.
3. Choisir `Messages recus` ou `Messages envoyes`, choisir le mode de connexion Mailiz, puis cliquer sur `Scanner la boite`.
4. Choisir un scenario.
5. Verifier la date limite. C'est le critere principal pour affiner la proposition.
6. Cocher `Inclure les non lus` seulement si besoin.
7. Cliquer sur `Voir / mettre a jour la proposition` pour afficher les messages candidats.
8. Dans la proposition, modifier la date limite si besoin, puis mettre a jour la proposition.
9. Cocher tout ou partie des messages a nettoyer. Seuls les messages coches seront envoyes vers la corbeille.
10. Cliquer sur `Mettre en corbeille`.
11. Verifier le nombre de messages dans la corbeille.
12. Cliquer sur `Vider la corbeille`.
13. Attendre le scan de controle.

## Depuis le code source

Sur Windows, ouvrir le dossier `installation`, puis lancer `setup-windows.bat`. Le script affiche des etapes numerotees. L'installation de Chromium peut prendre plusieurs minutes. Ensuite, ouvrir le dossier `lancement`, puis double-cliquer sur `lancer-mailizclean.bat`.

Sur Mac, ouvrir le dossier `installation`, puis double-cliquer sur `installer-mailizclean.command`. Ensuite, ouvrir le dossier `lancement`, puis double-cliquer sur `lancer-mailizclean.command`. Si macOS refuse l'ouverture, clic droit sur le fichier puis `Ouvrir`.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Lancer l'application locale :

```bash
venv/bin/python mailiz_cleaner.py app
```

Raccourcis de lancement :

```text
Windows : lancement/lancer-mailizclean.bat
Mac     : lancement/lancer-mailizclean.command
Terminal: venv/bin/python mailiz_cleaner.py app
```

Le dashboard s'ouvre sur :

```text
http://127.0.0.1:8765
```

Si le navigateur ne s'ouvre pas automatiquement, garder la fenetre MailizClean ouverte puis copier l'adresse affichee dans Chrome, Safari, Edge ou Firefox. Le port peut etre different si `8765` est deja utilise.

La configuration peut se faire depuis le dashboard. En developpement, elle est stockee dans `data/.env`.

## Commandes utiles

Lancer le dashboard sans ouverture automatique du navigateur :

```bash
venv/bin/python mailiz_cleaner.py app --no-browser
```

Afficher l'aide :

```bash
venv/bin/python mailiz_cleaner.py --help
```

Scanner depuis le terminal :

```bash
venv/bin/python mailiz_cleaner.py scan --max-pages 0
```

Verifier la corbeille :

```bash
venv/bin/python mailiz_cleaner.py trash-status --max-pages 1
```

Vider la corbeille depuis le terminal, avec confirmation explicite :

```bash
venv/bin/python mailiz_cleaner.py empty-trash --i-understand-this-deletes-trash
```

Le parcours normal reste le dashboard.

## Donnees locales

En developpement, MailizClean ecrit dans `data/`.

Dans une application packagee :

- macOS : `~/Library/Application Support/MailizClean`
- Windows : `%APPDATA%\MailizClean`

Les rapports, logs et captures debug peuvent contenir des donnees sensibles. Ils sont ignores par Git.

## Depot open source

Le depot ignore volontairement :

- `.env` ;
- `data/.env` ;
- `data/reports/` ;
- `data/logs/` ;
- `data/temp/` ;
- `venv/` ;
- `build/` et `dist/`.

Avant d'ouvrir une issue ou une pull request, lire :

- `SECURITY.md`
- `docs/PRIVACY.md`

Ne jamais joindre de rapport reel, log reel, capture contenant des noms de patients, ou fichier `.env`.

## Packaging

La documentation de packaging est ici :

```text
packaging/README.md
```

Build macOS :

```bash
./scripts/build_macos.sh
```

Sorties : `dist/MailizClean.app` et `dist/MailizClean-macOS-0.1.0.dmg`.

Build Windows :

```powershell
.\scripts\build_windows.ps1
```

Sorties : `dist\MailizClean\MailizClean.exe` et `dist\MailizClean-Windows-0.1.0.zip`.

Les builds doivent etre produits sur l'OS cible. Sur Windows, distribuer le `.zip` ou le dossier `dist\MailizClean`, pas le `.exe` seul. La signature macOS/Windows n'est pas encore configuree.

## Limites connues

- Les tailles liberees sont estimees a partir des tailles affichees par Roundcube.
- La detection du patient est heuristique.
- Le stockage des identifiants se fait encore dans un fichier `.env` local ; un trousseau systeme sera preferable avant diffusion large.
- Les builds ne sont pas encore signes.

## Licence

MIT. Voir `LICENSE`.
