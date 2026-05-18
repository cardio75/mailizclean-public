# MailizClean - parcours de nettoyage

Ce guide decrit le parcours normal. Les messages selectionnes sont d'abord deplaces vers la corbeille Mailiz. Le vidage de la corbeille reste une action separee.

## 1. Ouvrir MailizClean

Parcours simple :

- Windows : double-cliquer sur `lancer-mailizclean.bat`.
- Mac : double-cliquer sur `lancer-mailizclean.command`.

Ces fichiers sont dans le dossier `lancement`.

Avant le premier lancement, ouvrir le dossier `installation` :

- Windows : double-cliquer sur `setup-windows.bat`.
- Mac : double-cliquer sur `installer-mailizclean.command`.

Si macOS refuse l'ouverture, faire clic droit sur le fichier puis `Ouvrir`.

La fenetre qui s'ouvre doit rester ouverte pendant l'utilisation. Elle affiche l'adresse du dashboard, par exemple `http://127.0.0.1:8765`.

Si le navigateur ne s'ouvre pas automatiquement, copier cette adresse dans Chrome, Edge, Safari ou Firefox.

Option terminal :

```bash
venv/bin/python mailiz_cleaner.py app
```

## 2. Scanner la boite

Choisir `Messages recus` ou `Messages envoyes`, puis cliquer `Scanner la boite`.

Le scan ouvre Mailiz, lit le quota et analyse les messages. Cela peut prendre plusieurs minutes.

## 3. Voir une proposition

Dans `Boite scannee` :

1. Choisir le dernier scan.
2. Choisir un scenario.
3. Verifier la date limite.
4. Cocher `Inclure les non lus` seulement si besoin.
5. Cliquer `Voir la proposition`.

## 4. Choisir les messages

Dans `Propositions` :

1. Filtrer si besoin.
2. Cocher les messages a nettoyer.
3. Cliquer `Mettre en corbeille`.

Le dashboard deplace les messages coches vers la corbeille Mailiz, puis affiche l'etat final de la corbeille. Les messages ne sont pas supprimes definitivement a cette etape.

Si le resultat est correct, le bouton `Vider la corbeille (n)` devient disponible. Cliquer dessus vide definitivement la corbeille apres confirmation navigateur.

## 5. Option terminal si besoin

Le dashboard garde une trace locale de chaque selection nettoyee sous la forme :

```text
data/reports/move-selection-YYYYMMDD-HHMMSS.json
```

Tester sans connexion Mailiz :

```bash
python mailiz_cleaner.py move-to-trash \
  --selection data/reports/move-selection-YYYYMMDD-HHMMSS.json \
  --dry-run
```

Deplacer depuis le terminal :

```bash
python mailiz_cleaner.py move-to-trash \
  --selection data/reports/move-selection-YYYYMMDD-HHMMSS.json \
  --debug \
  --final-trash-status \
  --keep-open \
  --i-understand-this-moves-mail
```

Quand le terminal le demande, taper exactement :

```text
DEPLACER VERS CORBEILLE
```

Le navigateur reste ouvert pour inspection. Appuyer sur `Entree` dans le terminal pour terminer.

## 6. Vider la corbeille depuis le terminal, si besoin

Le bouton du dashboard est le parcours normal. La commande terminal reste disponible en secours.

Verifier d'abord la corbeille :

```bash
python mailiz_cleaner.py trash-status --debug --max-pages 1
```

Puis vider :

```bash
python mailiz_cleaner.py empty-trash \
  --debug \
  --keep-open \
  --i-understand-this-deletes-trash
```

Quand le terminal le demande, taper exactement :

```text
VIDER LA CORBEILLE
```

La commande affiche le quota avant et apres vidage quand Roundcube le fournit.

## 7. Refaire un scan de reference

Apres vidage, cliquer a nouveau `Scanner la boite` dans le dashboard pour obtenir l'etat actualise de la boite et du quota.

La commande terminal equivalente reste disponible :

```bash
python mailiz_cleaner.py scan --max-pages 0 --prefix mailiz-scan-apres-nettoyage
```

Ce scan ouvre une nouvelle session Mailiz et peut prendre du temps. Il sert de reference pour les prochaines propositions.

## Traces locales

- Actions de deplacement : `data/logs/move-to-trash-actions.jsonl`
- Logs generaux : `data/logs/mailiz_cleaner.log`
- Captures debug en cas d'erreur : `data/temp/debug/`
