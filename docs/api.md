# API locale SignalFlow — contrats HTTP

L'API est un projet Python installable dans `web/api`, sous le nom de paquet
`signalflow_api`. Elle écoute **uniquement sur `127.0.0.1`**, exige une identité
sur toutes les routes métier et ne lance aucun entraînement HTTP. Les fichiers
de référence sont `web/api/README.md`, [phase3_fastapi.md](phase3_fastapi.md),
[database.md](database.md) et [roadmap_finalisation.md](roadmap_finalisation.md).

## Périmètre

- Validation d'une spécification `training` / `dataset` / `tracking` /
  `monitoring` (facultatif) par le moteur canonique de la phase 2.
- Import et export YAML sous contrôle serveur.
- Projets, appartenances et rôles persistants.
- Versions de configuration immuables, historiques, comparaison et export.
- Aucun appel à `run_training_spec()`, aucun dataset chargé, aucune écriture
  MLflow, aucune file `BackgroundTasks`. `POST /api/v1/trainings` reste réservé
  et répond `501` après validation.

## Démarrage

Depuis la racine du dépôt, dans le seul environnement `.venv` :

```bash
make install                       # SignalFlow + extras + API + PostgreSQL + MLflow
make app-db-up                     # PostgreSQL applicatif dédié, 127.0.0.1:5433
make app-migrate                   # Alembic : schéma applicatif sur cette base seule
make api-token SUBJECT=admin ROLE=admin
make api                           # http://127.0.0.1:8000/docs
```

`make api-token` affiche le jeton brut **une seule fois** ; aucun jeton brut
n'est conservé dans le dépôt, les journaux ou la base. Le champ `SUBJECT` est
obligatoire et `ROLE` vaut `admin` par défaut. Le magasin est
`SIGNALFLOW_DATABASE_URL` (fourni par le Makefile, jamais par une requête HTTP).

Variables d'environnement lues par `Settings.from_env()` :

| Variable | Rôle |
|---|---|
| `SIGNALFLOW_DATABASE_URL` | DSN de la base applicative ; active le résolveur de jetons et les dépôts |
| `SIGNALFLOW_API_TOKENS` | Liste `role:jeton,role:jeton` pour un résolveur statique en mémoire (tests/dépannage) |

Sans source de jetons, les routes métier échouent fermées avec `503` :
`AUTH_NOT_CONFIGURED`. `GET /api/v1/health` reste public et ne vérifie ni Docker,
ni MLflow, ni PostgreSQL.

Documentation interactive : `/docs` ; contrat OpenAPI : `/openapi.json`.
`redoc` est désactivé. La commande `signalflow-api --port 8000` fixe
`host='127.0.0.1'` et `proxy_headers=False` : ne pas la contourner avec un
Uvicorn configuré à la main, ne pas exposer ce service sur Internet.

## Identité et permissions

`Authorization: Bearer <jeton>` est obligatoire sur toutes les routes sauf
`health`. Le jeton est opaque (`secrets.token_urlsafe(32)`, 43 caractères),
stocké uniquement sous forme d'empreinte SHA-256 dans `sf_tokens`. La
comparaison est à temps constant pour le résolveur statique ; le résolveur de
base interroge par empreinte. Les en-têtes `X-User`, `X-Role` ou analogues ne
sont jamais pris en compte.

| Rôle global | Permissions |
|---|---|
| `viewer` | `training:read`, `config:read`, `project:read` |
| `editor` | toutes celles de `viewer` + `training:validate`, `training:create`, `config:write`, `project:write` |
| `admin` | toutes celles de `editor` + `project:manage`, `token:manage` |

Le rôle global est fourni par l'opérateur via la CLI. Un rôle projet
(`viewer` / `editor` / `admin`) est enregistré dans `sf_memberships`. Les
opérations sur un projet exigent à la fois la permission globale et un rôle
projet compatible ; un administrateur global peut voir et administrer tous les
projets. Un projet auquel l'identité n'appartient pas répond `404`, comme un
projet inexistant : son existence n'est pas révélée.

Codes d'authentification : `401 AUTHENTICATION_REQUIRED` (jeton absent),
`401 INVALID_AUTHORIZATION` (en-tête mal formé), `401 INVALID_TOKEN` (jeton
inconnu ou révoqué), `403 PERMISSION_DENIED` (permission globale manquante),
`403 PROJECT_FORBIDDEN` (rôle projet insuffisant), `503 AUTH_NOT_CONFIGURED`
(aucun résolveur), `503 DATABASE_NOT_CONFIGURED` (routes projet sans base).

## Routes

Préfixe commun : `/api/v1`.

| Méthode / chemin | Permission | Résultat |
|---|---|---|
| `GET /health` | aucune | `200` : `status=ok`, `service=signalflow-api`, `mode=authentication-required`, `training_execution=false` |
| `GET /training/schema` | `training:read` | `200` : JSON Schema draft 2020-12 de `TrainingPayload` |
| `GET /algorithms` | `training:read` | `200` : catalogue réel (`logistic_regression`, scikit-learn) et `execution_available=false` |
| `POST /training/validate` | `training:validate` | `200` : `{"valid":true,"configuration":{...}}` |
| `POST /training/import` | `training:validate` | `200` : valide un `{"yaml": "..."}` avec le même contrat que le JSON |
| `POST /training/export` | `training:read` | `200` : `{"yaml":"...","configuration":{...}}` canonique |
| `POST /trainings` | `training:create` | `501 TRAINING_EXECUTION_NOT_IMPLEMENTED` après validation |
| `POST /projects` | `project:manage` | `201` : projet créé, créateur admin du projet |
| `GET /projects` | `project:read` | `200` : liste des projets visibles |
| `GET /projects/{key}` | `project:read` | `200` ou `404` |
| `POST /projects/{key}/members` | `project:manage` | `201` : membre ajouté ou rôle projet modifié |
| `GET /projects/{key}/members` | `project:read` | `200` : membres du projet visible |
| `POST /projects/{key}/configurations` | `config:write` | `201` : nouvelle version, ou `409 VERSION_CONFLICT` |
| `GET /projects/{key}/configurations` | `config:read` | `200` : historique complet, versions croissantes |
| `GET /projects/{key}/configurations/compare?source=1&target=2` | `config:read` | `200` : `changed` (chemins JSON) |
| `GET /projects/{key}/configurations/{version}` | `config:read` | `200` : snapshot immuable |
| `GET /projects/{key}/configurations/{version}/yaml` | `config:read` | `200` : `application/x-yaml` |

Clé de projet : 2 à 64 caractères, `^[a-z0-9][a-z0-9-]*$`. Rôles projet :
`viewer`, `editor`, `admin`. `expected_version` est facultatif et `>= 1` ;
s'il est fourni et ne correspond plus à la dernière version, la publication
est refusée avec `409`. Une version est identifiée par son numéro, son
`digest` SHA-256 du document canonique, son auteur, son `parent_version` et sa
date UTC ; aucun update ni delete n'est exposé.

## Corps et réponses

`TrainingPayload` interdit les champs inconnus et exige `training`, `dataset`,
`tracking`. Les règles ML ne sont pas recopiées dans l'API : le JSON Schema est
projeté depuis les ressources du moteur, puis `validate_training_spec()`
vérifie les invariants. La projection HTTP retire les sources CSV et les
destinations de fichiers ; `monitoring` est facultatif.

Exemple de publication de version (le document `iris.json` doit être enveloppé
dans `configuration`) :

```bash
TOKEN=$(cat /chemin/private/token.txt)
curl -X POST http://127.0.0.1:8000/api/v1/projects/demo/configurations \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data-binary @/tmp/opencode/phase4-config-create.json
```

Le fichier `/tmp/opencode/phase4-config-create.json` contient
`{"configuration": <contenu de web/api/examples/iris.json>}`. Le document nu
renverrait `422` : la clé `configuration` est obligatoire.

Toutes les erreurs partagent la même enveloppe, avec `X-Request-ID` et
`Cache-Control: no-store` :

```json
{
  "code": "VERSION_CONFLICT",
  "message": "Une version concurrent a deja ete publiee.",
  "details": [],
  "request_id": "identifiant-cree-par-le-serveur"
}
```

Aucune valeur rejetée, exception brute, trace ou chemin interne n'est exposé.
Les noms de champs inconnus deviennent `*` dans les détails. Codes principaux :
`400` JSON/Host/Corps invalides ; `401` jeton absent ou invalide ; `403`
origine, permission ou rôle projet refusé ; `408` délai de lecture ; `409`
conflit de projet ou de version ; `413` corps trop grand ; `415` média ou
encodage refusé ; `422` contrat invalide ; `500` erreur interne expurgée ;
`501` exécution non implémentée ; `503` identité ou base non configurée.

## Frontière de sécurité et limites

Le middleware ASGI `RequestBoundary` mesure les octets réellement reçus avant le
parsing FastAPI : 65 536 octets maximum, 5 secondes de lecture, profondeur JSON
32, corps compressés refusés, clés JSON dupliquées et nombres non finis rejetés.
`Host` est limité à `localhost`, `127.0.0.1` ou `[::1]` avec port valide ; un
`Origin` présent doit être exactement identique au schéma et à l'hôte. Il n'y a
ni CORS permissif, ni confiance dans les en-têtes de proxy, ni session
navigateur, ni TLS, ni limitation de débit globale, ni quota utilisateur. La
borne de taille ne constitue pas une protection complète contre le déni de
service. Le corps est borné mais pas chiffré au repos par l'application ; voir
[database.md](database.md) pour la base dédiée.

La validation YAML n'accède jamais au système de fichiers : elle utilise un
`SafeLoader` strict qui refuse les clés dupliquées, puis le même contrat que le
JSON. L'export YAML est produit par le serveur à partir d'une configuration
déjà validée. Les identifiants et rôles sont persistés, mais la révocation et
la rotation des jetons se font uniquement via la couche de dépôt/CLI, pas via
une route HTTP.

## Validation

```bash
make check          # pip check
make test           # suite complète, 374 tests réussis
make test-api       # contrats HTTP uniquement
make test-postgres  # 1 test sur le vrai PostgreSQL dédié
```

Les tests PostgreSQL et Compose sont opt-in (`--postgres`, `--compose`) : la
suite locale ne dépend d'aucun démon. Les tests isolent chaque base SQLite dans
`tmp_path` et ne touchent ni `outputs/*/tracking/mlflow.db` ni les volumes
Docker existants.
