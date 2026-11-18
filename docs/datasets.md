# Datasets CSV — phase 5

L’API publie des versions append-only par projet et nom logique. Aucun endpoint
ne remplace ou ne supprime une version. Les futurs jobs pourront référencer son
`id` ; leur intégration relève de la phase 6.

## Activation

Installer les dépendances de `web/api` (dont `python-multipart`), sauvegarder la
base applicative, puis appliquer `alembic upgrade head` (migration additive
`0003_dataset_versions`). Configurer `SIGNALFLOW_DATASET_ROOT` avec un répertoire
privé, persistant, accessible uniquement au compte de l’API. Sans ce paramètre,
les opérations de stockage renvoient 503. Sauvegarder ensemble base et fichiers.
Ne pas utiliser un volume MLflow ou un répertoire servi par un serveur statique.

`SIGNALFLOW_MAX_DATASET_BYTES` fixe la limite fichier, au maximum 32 Mio (défaut),
comme le moteur CSV. L’enveloppe multipart dispose de 16 Kio supplémentaires.
La lecture HTTP compte réellement les octets, même sans Content-Length ; le délai
opérateur existant s’applique (5 s par défaut). Les fichiers multipart sont spoolés
sur disque par Starlette ; un seul fichier et deux champs sont acceptés.

## Contrat HTTP

Toutes les routes utilisent Bearer, les permissions `dataset:read/write` et
l’appartenance au projet. Un lecteur peut consulter ; un éditeur peut publier.
Un projet étranger renvoie 404 ; l’administrateur global garde sa visibilité.

- `POST /api/v1/projects/{key}/datasets` : multipart `file`, `name`, `target` → 201.
- `GET /api/v1/projects/{key}/datasets?offset=0&limit=100` : historique paginé.
- `GET /api/v1/projects/{key}/datasets/{id}` : métadonnées, auteur, SHA-256 des octets.
- `GET .../{id}/preview?limit=10` : colonnes et au plus 20 lignes.
- `GET .../{id}/download` : pièce jointe CSV, octets originaux.

Exemple (variables définies par l’opérateur) :

```sh
curl -H "Authorization: Bearer $TOKEN" \
  -F name=entrainement -F target=label -F 'file=@donnees.csv;type=text/csv' \
  http://127.0.0.1:8000/api/v1/projects/demo/datasets
```

Les noms logiques comportent 1–64 caractères alphanumériques, `_` ou `-` et
commencent par un caractère alphanumérique. Le nom du fichier client n’est jamais
utilisé comme chemin. Les fichiers internes portent un UUID aléatoire ; création
exclusive, lecture sans suivre les symlinks, aucun chemin retourné dans l’API.

## Validation et bornes

CSV UTF-8 (BOM accepté), en-tête unique non vide, cible existante non vide,
au moins trois lignes, une caractéristique et deux classes. Les caractéristiques
sont numériques ; les valeurs manquantes/NaN sont admises, sauf colonne entièrement
manquante. Infini refusé. Ces règles reprennent les contraintes de chargement du
moteur sans importer sklearn dans le processus web. La faisabilité du découpage
train/test reste à valider au lancement d’un job.

Maximum 256 colonnes, 128 caractères par nom de colonne, 4 096 par cellule,
1 000 000 cellules. Les contrôles s’effectuent ligne par ligne. Archives, pickle,
binaire et faux CSV sont rejetés par validation du contenu, indépendamment du MIME.
Les types acceptés sont text/csv, application/csv, text/plain et
application/octet-stream avec extension .csv ; aucune décompression automatique.
L’aperçu restitue des chaînes : les clients doivent les afficher comme texte.

## Publication et reprise après panne

1. Contrôler identité et rôle avant de lire le formulaire.
2. Copier vers un fichier privé exclusif, contrôler sa taille et calculer SHA-256.
3. Synchroniser fichier et répertoire, valider le CSV.
4. Verrouiller le projet, allouer le numéro suivant et committer la référence SQL.

Le verrou PostgreSQL sérialise les publications ; une contrainte unique arbitre
également les collisions. Un échec avant commit supprime le fichier. Un échec
pendant le commit peut avoir un résultat incertain : le fichier est conservé pour
ne jamais effacer les données d’une transaction effectivement publiée. Un arrêt
brutal peut également laisser un orphelin. Il n’existe pas de transaction atomique
commune au système de fichiers et à PostgreSQL.

Collecte : arrêter **toutes** les instances API/producteurs, puis exécuter avec les
mêmes variables de base/stockage :

```sh
python -m signalflow_api.datasets.cleanup
python -m signalflow_api.datasets.cleanup --apply --writers-stopped
```

Le premier passage est une simulation. Seuls les UUID non référencés, sans lien
symbolique et âgés d’au moins 24 h sont supprimables. Ne pas exécuter une collecte
avec producteurs actifs. Il n’y a pas encore de quota cumulé par projet ni de
collecteur automatique ; surveiller l’espace disque et limiter la concurrence au
niveau du déploiement. Le stockage privé suppose un opérateur de confiance.

## Validation locale (18 novembre 2026)

Les 19 tests de `tests/datasets` passent : historique et relecture après recréation
API, isolation de projet et rôles, UTF-8/CSV malformé, MIME trompeur, dépassement
sans Content-Length, limites fichier, symlinks/traversée, collision de stockage,
échec disque/base et collecte conservant les fichiers référencés. La migration
SQLite vers head et `alembic check` passent également. Complément pendant la phase 6 : migrations `0003`/`0004` et `alembic check`
réussis sur PostgreSQL jetable, ainsi que deux publications concurrentes du même
dataset (versions 1 et 2). Les mesures de charge restent à effectuer avant déploiement.

Suite complète : **394 réussis** en 231,95 s. Les trois derniers cas ajoutés
(échec base/limite fichier, racine symlink/valeurs manquantes, collision UUID)
ont ensuite été inclus dans les **19 tests datasets réussis** ; pas de seconde
exécution complète inutile. `git diff --check` et compilation Python réussis.
