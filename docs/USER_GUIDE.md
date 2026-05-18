# Guide utilisateur MailizClean

MailizClean aide a liberer de l'espace dans une boite Mailiz/MSSante. L'outil fonctionne localement sur l'ordinateur.

Outil fourni sans garantie. Verifier les propositions avant toute mise en corbeille ou suppression definitive.

## Ce que fait MailizClean

- Il ouvre Mailiz dans une fenetre Chromium locale.
- Il scanne les messages recus ou envoyes et lit le quota.
- Il classe les messages par type de document.
- Il propose des messages anciens a mettre en corbeille.
- Il peut vider la corbeille apres confirmation.
- Il refait un scan de controle apres vidage.

## Ce que MailizClean ne fait pas

- Il n'envoie pas les rapports a un serveur MailizClean.
- Il ne vide pas la corbeille sans confirmation.
- Il ne choisit pas seul les messages a nettoyer.
- Il ne remplace pas une verification humaine.

## Premier lancement

Sur Windows, `installation/setup-windows.bat` installe Python localement pour MailizClean puis telecharge Chromium. Cette installation peut prendre plusieurs minutes, surtout a l'etape `Installation de Chromium pour Playwright`.

Sur Mac, utiliser `installation/installer-mailizclean.command`.

Au lancement, MailizClean ouvre normalement une page dans votre navigateur.

Si rien ne s'ouvre, gardez la fenetre MailizClean ouverte, puis copiez dans votre navigateur l'adresse affichee dans cette fenetre, par exemple :

```text
http://127.0.0.1:8765/
```

Au premier lancement, ouvrir la section `Configuration Mailiz`, puis renseigner :

- l'adresse MSSante utilisee pour se connecter au site Mailiz ;
- le mot de passe Mailiz ;
- l'adresse email ordinaire renseignee dans Mailiz, celle qui recoit le code OTP ;
- le mot de passe ou mot de passe d'application de cette boite email ;
- le serveur IMAP de cette boite email.

Cliquer ensuite sur `Enregistrer la configuration`.

Les mots de passe sont conserves localement. Lors d'une modification ulterieure, laisser un champ mot de passe vide conserve le mot de passe deja enregistre.

## Connexion a Mailiz

Deux modes sont possibles.

- `Automatique avec identifiants enregistres` : MailizClean se connecte seul et recupere le code OTP par email. Une fenetre Chromium s'ouvre. Vous pouvez la minimiser, mais ne cliquez pas dedans pendant que MailizClean travaille.
- `Manuelle dans une fenetre Mailiz` : une fenetre Chromium s'ouvre. Connectez-vous vous-meme comme d'habitude, choisissez SMS ou email pour recevoir le code, puis revenez dans MailizClean quand vous voyez votre boite de reception. Des que MailizClean detecte la boite, le scan ou l'action demandee demarre automatiquement. Vous pouvez alors minimiser la fenetre. A partir de ce moment, ne cliquez plus dedans pendant le scan ou le nettoyage.

## Parcours recommande

1. Choisir `Messages recus` ou `Messages envoyes`, choisir le mode de connexion Mailiz, puis cliquer sur `Scanner la boite`.
2. Attendre la fin du scan. La progression s'affiche pendant l'analyse.
3. Dans `Boite scannee`, choisir le dernier scan.
4. Choisir un scenario de nettoyage.
5. Verifier la `Date limite`. C'est le critere principal pour affiner la proposition.
6. Cocher `Inclure les non lus` seulement si ces messages doivent aussi etre proposes.
7. Cliquer sur `Voir / mettre a jour la proposition` pour afficher les messages candidats.
8. Dans la proposition, modifier la date limite si besoin, puis cliquer sur `Mettre a jour la proposition`.
9. Verifier les messages affiches.
10. Cocher tout ou partie des messages a nettoyer. Seuls les messages coches seront envoyes vers la corbeille.
11. Cliquer sur `Mettre en corbeille`.
12. Verifier le nombre de messages dans la corbeille.
13. Cliquer sur `Vider la corbeille` si le resultat est correct.
14. Attendre le scan de controle.

## Choisir une date limite

La date limite est essentielle. Un message plus recent que cette date ne doit pas etre propose par le scenario.

Exemple : une date limite au `2024-01-01` signifie que la proposition vise les messages anterieurs au 1er janvier 2024.

## Comprendre les boutons

- `Scanner la boite` : analyse les messages recus ou envoyes selon le choix affiche a cote du bouton.
- `Voir / mettre a jour la proposition` : prepare ou actualise la liste de messages candidats apres un scan.
- `Date limite` : critere principal de selection. La modifier permet de rendre la proposition plus prudente ou plus large.
- `Mettre en corbeille` : deplace les messages coches vers la corbeille Mailiz.
- `Vider la corbeille` : supprime definitivement le contenu de la corbeille Mailiz.
- `Retirer de l'historique` : retire une proposition locale, sans modifier Mailiz.

## Securite

Avant de vider la corbeille, verifier :

- le nombre de messages selectionnes ;
- les types de documents ;
- la date limite ;
- le nombre de messages annonce dans la corbeille.

Par defaut, ne pas inclure les messages non lus dans un nettoyage.

## Donnees locales

MailizClean conserve localement :

- les scans ;
- les propositions ;
- les journaux d'action ;
- la configuration de connexion.

Ces fichiers peuvent contenir des informations sensibles. Ne pas les envoyer par email et ne pas les joindre a une demande d'aide sans anonymisation.

## En cas de probleme

Fermer MailizClean, puis le relancer.

Si le probleme persiste, noter :

- le moment ou l'erreur apparait ;
- l'action en cours ;
- le message affiche dans le dashboard ;
- le systeme utilise : macOS ou Windows.

Ne pas transmettre de capture contenant des noms de patients, des adresses email ou des sujets de messages.
