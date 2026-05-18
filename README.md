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
- Packaging macOS et Windows en preparation avec PyInstaller.

## Pour les utilisateurs

La notice utilisateur est ici :

```text
docs/USER_GUIDE.md
```

Au premier lancement, MailizClean demande :

- l'adresse MSSante utilisee sur le site Mailiz ;
- le mot de passe Mailiz ;
- l'adresse email ordinaire renseignee dans Mailiz, celle qui recoit le code OTP ;
- le mot de passe ou mot de passe d'application de cette boite email ;
- le serveur IMAP de cette boite email.

Ces informations sont enregistrees localement sur l'ordinateur. Voir aussi `docs/PRIVACY.md`.

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

Le mode manuel ouvre une fenetre Chromium. L'utilisateur s'y connecte jusqu'a voir la boite de reception Mailiz, puis revient dans MailizClean. Des que MailizClean detecte la boite, le scan ou l'action demandee demarre automatiquement. La fenetre peut ensuite etre minimisee, mais il ne faut plus cliquer dedans pendant que MailizClean travaille.

Le mode automatique utilise les identifiants enregistres et ouvre une fenetre Chromium. La fenetre peut etre minimisee, mais il ne faut pas cliquer dedans pendant que MailizClean travaille.

## Installation developpeur

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

Build Windows :

```powershell
.\scripts\build_windows.ps1
```

Les builds doivent etre produits sur l'OS cible. La signature macOS/Windows n'est pas encore configuree.

## Limites connues

- Les tailles liberees sont estimees a partir des tailles affichees par Roundcube.
- La detection du patient est heuristique.
- Le stockage des identifiants se fait encore dans un fichier `.env` local ; un trousseau systeme sera preferable avant diffusion large.
- Les builds ne sont pas encore signes.

## Licence

MIT. Voir `LICENSE`.
