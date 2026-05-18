# Security Policy

MailizClean manipule des metadonnees de messagerie de sante. Les rapports,
captures debug, journaux et fichiers `.env` ne doivent jamais etre publies.

## Donnees sensibles

Ne pas inclure dans une issue, une pull request ou un commit :

- identifiants Mailiz ou OTP ;
- fichiers `.env` ;
- fichiers `data/reports/` ;
- fichiers `data/logs/` ;
- fichiers `data/temp/debug/` ;
- captures d'ecran contenant des noms de patients, adresses ou sujets de mails.

## Signalement

Pour un probleme de securite, ouvrir d'abord un canal prive avec le mainteneur
du depot. Ne pas publier de reproduction contenant des donnees reelles.

## Principe actuel

L'application fonctionne localement. Elle n'envoie pas les rapports MailizClean
a un serveur tiers. Les seules connexions reseau attendues sont Mailiz/Roundcube,
la boite OTP configuree et, pendant le build developpeur, les telechargements de
dependances.
