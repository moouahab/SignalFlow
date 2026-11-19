# Phase 6 — jobs durables et worker local

L’API enregistre les jobs dans PostgreSQL et renvoie 202 sans lancer de fit.
Un worker Linux distinct exécute le moteur Python de la phase 2 dans un
sous-processus. Le périmètre initial est **un hôte et un stockage partagé local**,
avec une exécution à la fois par racine de jobs. PostgreSQL reste la source de
vérité des états ; MLflow local est isolé dans chaque tentative.

## API

Routes authentifiées sous `/api/v1/projects/{key}` :

- `POST /runs` : en-tête `Idempotency-Key` (1–128 caractères ASCII alphanumériques,
  `_ . : -`) et JSON `{"configuration_version":1,"dataset_id":"…"}` → 202.
- `GET /runs?offset=0&limit=100` : liste paginée.
- `GET /runs/{id}` : état, tentative, résultat expurgé, erreur codifiée et
  `tracking_sync` (`none`, `pending`, `synced`). Aucun PID, chemin ni DSN.
- `POST /runs/{id}/cancel` avec `{}` JSON : annulation idempotente.
- `GET /runs/{id}/events` : SSE avec `Last-Event-ID` numérique, événements ordonnés
  conservés en base ; heartbeat et connexion bornée à 25 s. Reconnecter à fermeture.

L’auteur doit avoir `training:create` et un rôle projet éditeur/admin pour
soumettre/annuler. Les lecteurs autorisés consultent états et événements ; un
projet étranger reste invisible (404). Le SSE revérifie jeton et appartenance.

Une clé d’idempotence est propre au projet : même clé et mêmes références → même
job ; changement de références → 409. Le job conserve une copie profonde de la
configuration et les clés étrangères vers les versions de configuration/dataset.
`dataset_id` optionnel remplace la source de la configuration par le CSV de cette
version, avec sa cible ; sans lui, la source builtin/synthetic est conservée.
Le worker copie le CSV dans sa tentative et vérifie SHA-256 avant tout fit.
Le lancement peut échouer si le dataset ne permet pas le découpage demandé.

L’ancienne route `/trainings` conserve son contrat historique 501 (configuration
inline) : les jobs exigent les versions persistées via `/projects/{key}/runs`.
Le champ historique `training_execution: false` du health décrit l’absence
d’exécution dans l’API ; il ne vérifie pas la disponibilité d’un worker.

## États et garanties

`queued → running → succeeded | failed`, ou `queued → cancelled`.
Une annulation en cours passe par `cancel_requested → cancelled`.
Un bail expiré passe par `recovering`, puis un état terminal. Les états terminaux
ne sont pas réouverts. Une annulation acceptée avant la clôture SQL l’emporte sur
un succès concurrent ; après clôture, elle retourne simplement l’état final.

Prise de job PostgreSQL : verrou `FOR UPDATE SKIP LOCKED`, création de tentative
et événement dans la même transaction. Bail de 30 s, renouvelé pendant l’enfant.
Chaque propriétaire dispose d’un jeton aléatoire ; un ancien propriétaire ne peut
plus renouveler ni clôturer après expiration/reprise.

Le verrou fichier `.worker.lock` interdit deux superviseurs actifs sur le même
stockage. Il reste détenu par l’enfant si le superviseur meurt. L’enfant Linux
reçoit SIGKILL à la mort du parent (`PR_SET_PDEATHSIG`, avec contrôle de la course
au démarrage). Les arrêts ordinaires/annulations/limites tuent le groupe de
processus et attendent sa terminaison ; aucun PID enregistré n’est réutilisé.
Le moteur actuel utilise des threads, pas des petits-enfants persistants.

La reprise ne réentraîne **jamais automatiquement** une tentative incertaine :

- avant lancement ou en l’absence d’un résultat confirmé : `failed/WORKER_LOST` ;
- après publication : relire le rapport et vérifier le run MLflow `FINISHED`
  et la présence du modèle avant de conclure `succeeded` ;
- demande d’annulation persistée : `cancelled`.

Une nouvelle tentative volontaire nécessite une nouvelle clé de soumission.
Cette politique évite un double fit et rend les échecs avant lancement explicites.
Elle ne promet pas un succès automatique après toutes les pannes.

## MLflow, événements et outbox

Les transitions et leurs événements sont commités ensemble. Une clôture ajoute
également une ligne d’outbox. Le worker rejoue cette ligne jusqu’à synchroniser
le tracking : succès confirmé `FINISHED`, échec `FAILED`, annulation `KILLED`.
Une panne entre MLflow et le commit d’acquittement rejoue une opération idempotente.
Une erreur de télémétrie/réconciliation ne fait pas disparaître le job :
`tracking_sync=pending` rend le retard observable. Aucun bus externe n’est requis.
Les logs du sous-processus sont ignorés ; les événements publics ne contiennent
ni cellules du dataset ni messages de dépendances.

## Ressources et exploitation

Valeurs par défaut : 300 s murales, 120 s CPU, 2 Gio d’espace d’adressage,
256 Mio par fichier. Le worker fixe RLIMIT_CPU/AS/FSIZE/CORE avant d’importer le
moteur ; pools BLAS limités à un thread. L’environnement enfant est construit
sur liste blanche : aucun jeton API, DSN PostgreSQL, proxy ou secret AWS hérité.
Les erreurs de limite système et crashes remontent `PROCESS_LIMIT_OR_CRASH` ;
la limite murale donne `WALL_TIME_LIMIT`.

Variables opérateur :

- `SIGNALFLOW_DATABASE_URL` : base PostgreSQL applicative.
- `SIGNALFLOW_DATASET_ROOT` : même stockage privé que l’API, lecture seule pour le worker.
- `SIGNALFLOW_JOB_ROOT` : stockage privé durable des tentatives ; sauvegarder avec la base.
- `SIGNALFLOW_JOB_WALL_SECONDS`, `SIGNALFLOW_JOB_CPU_SECONDS`,
  `SIGNALFLOW_JOB_MEMORY_BYTES` : bornes opérateur, jamais paramètres HTTP.

Installer les deux distributions avec les extras ML/monitoring/postgres et API
worker. Sauvegarder la base puis appliquer `alembic upgrade head` (`0004_jobs`).
Lancer `signalflow-worker`, ou `signalflow-worker --once` pour un passage.
SQLite n’est admis en CLI qu’avec `--allow-sqlite` pour les tests/développement
mono-worker ; les garanties de concurrence reposent sur PostgreSQL.

Le profil Compose `jobs` ajoute API et worker sans démarrer automatiquement ces
services ni modifier les volumes existants :

```sh
docker compose -f compose.app.yaml --profile jobs build
docker compose -f compose.app.yaml --profile jobs run --rm api python -m alembic upgrade head
# Créer un jeton avec signalflow-api-token si nécessaire, puis :
docker compose -f compose.app.yaml --profile jobs up -d api worker
```

API exposée sur `127.0.0.1:8001`. Le worker Compose est limité à 1 CPU/3 Gio,
128 tâches et un système racine en lecture seule. Les nouveaux volumes datasets
et jobs sont séparés du stockage MLflow existant. Le build Docker et le déploiement
ne sont pas implicites dans la validation Python.

Les limites par fichier ne sont pas un quota total de disque ; surveiller et
archiver les tentatives. Ne pas supprimer les tentatives des jobs dont le tracking
est encore pending. Aucun planificateur distribué, réessai automatique de fit,
registre de modèles ou orchestration ZenML n’est inclus (phases suivantes).

## Validation du 18 novembre 2026

- Entraînement réel builtin et CSV uploadé : succès et métriques accessibles.
- Reprise après publication MLflow et perte du résultat métier : une seule
  tentative, un seul run moteur, résultat réconcilié sans nouveau fit.
- SIGKILL du superviseur : enfant arrêté, bail expiré récupéré, état terminal.
- Annulation en file et en exécution, ancien propriétaire refusé, timeout mural,
  limites noyau CPU/mémoire/fichier, environnement sans secrets.
- Outbox rejouée sans dupliquer l’événement d’acquittement ; SSE reprenable et
  accès interprojets refusé ; intégrité du CSV vérifiée avant entraînement.
- PostgreSQL jetable : cinq cas validés (migrations/transactions configuration,
  éditions concurrentes, soumission idempotente/deux prétendants au job,
  publications CSV concurrentes, `alembic check` sans écart). Base de test supprimée.
- Deux wheels construites dans `/tmp/signalflow-phase6-wheels`, présence du worker
  et de son entry point vérifiée. Profil Compose validé avec `config --quiet`.

Le build d’image Docker, le déploiement et les mesures de charge en production
n’ont pas été exécutés. Aucun volume applicatif existant n’a été migré.

Suite complète : **407 tests réussis** en 229,53 s, aucun avertissement. Le cas
supplémentaire du parcours CSV uploadé a ensuite été validé séparément
(**1 réussi**). `git diff --check` : aucun problème.
