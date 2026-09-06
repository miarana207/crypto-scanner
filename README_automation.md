# Automatisation du scan — GitHub Actions + Slack + Email

Ce dossier contient tout ce qu'il faut pour que le scanner tourne **automatiquement
chaque matin dans le cloud**, sans laisser ton ordinateur allumé, et t'envoie
les résultats sur Slack et par email.

## 1. Créer un dépôt GitHub

1. Va sur https://github.com/new, crée un dépôt (peut être privé — recommandé
   puisque ça touche à du trading).
2. Uploade tout le contenu de ce zip dans le dépôt (glisser-déposer sur la page
   du dépôt fonctionne pour un premier envoi, ou `git push` si tu es à l'aise en ligne de commande).

Le dépôt doit avoir cette structure :
```
.github/workflows/daily_scan.yml
app/backtest.py
app/scan.py
app/notify.py
app/run_and_notify.py
app/requirements.txt
```

## 2. Configurer le webhook Slack

1. Va sur https://api.slack.com/apps → **Create New App** → **From scratch**.
2. Choisis ton espace de travail, donne un nom à l'app (ex: "Scanner Trading").
3. Dans le menu de gauche, clique **Incoming Webhooks** → active-le (toggle en haut).
4. Clique **Add New Webhook to Workspace**, choisis le canal où tu veux recevoir
   les scans (ex: #trading-alerts), autorise.
5. Copie l'URL du webhook généré (commence par `https://hooks.slack.com/services/...`).

## 3. Configurer l'email (exemple avec Gmail)

Gmail exige un "mot de passe d'application" plutôt que ton mot de passe normal :
1. Active la validation en 2 étapes sur ton compte Google si ce n'est pas déjà fait.
2. Va sur https://myaccount.google.com/apppasswords, génère un mot de passe
   d'application (choisis "Autre" comme application, nomme-le "Scanner").
3. Copie le mot de passe généré (16 caractères) — c'est celui-ci qu'on utilisera,
   pas ton mot de passe Gmail habituel.

Si tu utilises un autre fournisseur email, il te faudra son adresse SMTP et son
port (souvent 465 en SSL) — dis-moi lequel si ce n'est pas Gmail, je peux ajuster.

## 4. Ajouter les secrets dans GitHub

Dans ton dépôt GitHub : **Settings** → **Secrets and variables** → **Actions**
→ **New repository secret**. Ajoute ces 4 secrets un par un :

| Nom du secret | Valeur |
|---|---|
| `SLACK_WEBHOOK_URL` | L'URL copiée à l'étape 2 |
| `EMAIL_SENDER` | Ton adresse Gmail complète |
| `EMAIL_PASSWORD` | Le mot de passe d'application (16 caractères) de l'étape 3 |
| `EMAIL_RECIPIENT` | L'adresse où tu veux recevoir les résultats (peut être la même) |

Ces secrets sont chiffrés par GitHub et ne sont jamais visibles dans les logs.

## 5. Ajuster l'horaire

Ouvre `.github/workflows/daily_scan.yml` et modifie la ligne :
```yaml
- cron: "0 7 * * 1-5"
```
Le format est `minute heure jour mois jour-semaine`, **toujours en UTC**.
Par exemple, pour 8h heure de Paris en hiver (UTC+1) : `"0 7 * * 1-5"`.
Pour 8h en été (UTC+2) : `"0 6 * * 1-5"`.
`1-5` = du lundi au vendredi seulement (marchés crypto tradent 24/7, donc à
adapter si tu veux aussi le week-end — mets `*` à la place).

## 6. Tester manuellement

Une fois tout configuré : va dans l'onglet **Actions** de ton dépôt GitHub,
sélectionne le workflow "Scan quotidien", clique **Run workflow** pour le
déclencher immédiatement sans attendre le lendemain matin. Regarde les logs
pour vérifier que tout fonctionne, et check ton Slack/email.

## Limites à connaître

- Le tier gratuit de GitHub Actions inclut 2000 minutes/mois pour les dépôts
  privés (largement suffisant pour un scan quotidien de quelques minutes) —
  illimité pour les dépôts publics.
- L'horaire cron de GitHub Actions n'est pas garanti à la minute près
  (retard possible de quelques minutes en cas de forte charge sur
  l'infrastructure GitHub).
- Si Binance bloque la requête (erreur 451, comme on l'a vu sur Colab),
  les serveurs GitHub Actions sont généralement hébergés aux US — même
  souci possible. Dans ce cas, il faudra définir la variable d'environnement
  `BINANCE_DATA_URL=https://api.binance.us` dans le workflow, comme pour Colab.
