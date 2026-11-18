# SignalFlow — base applicative PostgreSQL (phase 4)

## Statut et périmètre

Cette base est **distincte de MLflow** : elle conserve les utilisateurs, jetons
hachés, projets, appartenances et versions de configurations de SignalFlow.
Les expériences et modèles MLflow restent dans leurs stockages existants.
Aucun fichier `outputs/**/mlflow.db` n'est migré par Alembic.

L'API FastAPI permet de créer/lister/lire des projets, gérer leurs membres,
créer/lister/lire/comparer/exporter des versions de configuration. L'entraînement
HTTP reste volontairement indisponible (`POST /api/v1/trainings` → HTTP 501).
La suppression, l'édition des projets et la pagination ne sont pas encore
implémentées. Le service est **réservé à un environnement local contrôlé** : il
ne fournit pas encore TLS, protection complète contre le déni de service,
rotation automatique des identifiants ou gestion IAM d'entreprise.

## Architecture des données

| Table | Utilité | Contrainte essentielle |
| --- | --- | --- |
| `sf_users` | identité, nom, rôle global | `subject` unique |
| `sf_tokens` | empreinte SHA-256 du jeton opaque, révocation et expiration | `token_hash` unique ; aucun jeton brut |
| `sf_projects` | projet et créateur | clé `key` unique |
| `sf_memberships` | rôle d'un utilisateur dans un projet | paire `(project_id, user_id)` unique |
| `sf_configuration_versions` | snapshot JSON d'une configuration validée | paire `(project_id, version)` unique |

Le champ `author_id` référence l'auteur **qui a créé la version**, pas
l'utilisateur qui consulte l'historique. Le digest SHA-256 porte sur le JSON
canonique ; la version suivante crée une nouvelle ligne, et les versions
précédentes ne sont jamais modifiées par les routes applicatives.

**Limite :** « append-only » est une discipline applicative, pas une protection
contre une modification par un administrateur de la base. Des privilèges SQL
plus fins et un audit immuable restent nécessaires pour une production régulée.

## Installation locale sans écraser les données existantes

Prérequis : Python 3.11+, Docker Compose, environnement `.venv` avec les extras
`ml` et `postgres` et le paquet API. Dans l'environnement du dépôt :

```bash
make install
make app-db-up
make app-migrate
```

`compose.app.yaml` contient un conteneur PostgreSQL **dédié**, accessible sur
`127.0.0.1:5433` et son propre volume `signalflow-app_app-postgres-data`.
Le Compose propose par défaut le mot de passe **strictement local de
DÉVELOPPEMENT** `signalflow-local`, également présent dans le DSN de démonstration
du Makefile. Il est **interdit** de déployer ces valeurs en production ou
sur un réseau non fiable. Pour une installation durcie, fournir des secrets
propres à l'environnement et adapter `POSTGRES_URL` de manière cohérente.

Les commandes `app-db-down` et `app-db-up` **ne suppriment pas** les volumes.
Ne pas utiliser `docker compose down -v`, ni `docker volume rm` sur une machine
avec des données à conserver. Réaliser une sauvegarde avant toute migration.

`SIGNALFLOW_DATABASE_URL` (ou `POSTGRES_URL` via les cibles Makefile) doit
pointer **uniquement vers la base applicative**, jamais vers MLflow.
Exemple de DSN local de test (ne pas réutiliser comme secret de production) :

```text
postgresql+psycopg://signalflow:<mot-de-passe-local>@127.0.0.1:5433/signalflow_app
```

Le paquet `signalflow-web-api` déclare explicitement SQLAlchemy ; son extra
`postgres` apporte aussi le driver PostgreSQL via SignalFlow et Alembic :

```bash
python -m pip install -e .
python -m pip install -e 'web/api[postgres,test]'
python -m pip check
```

## Migrations Alembic

Depuis la racine du dépôt et avec `SIGNALFLOW_DATABASE_URL` positionnée :

```bash
python -m alembic current
python -m alembic upgrade head
python -m alembic current
```

- `0001_initial` crée les cinq tables et leurs index.
- `0002_token_expiration` ajoute **uniquement** `sf_tokens.expires_at`.
- Les anciens jetons restent dans la base (`expires_at IS NULL`) pour éviter une
  révocation surprise. L'opérateur doit les révoquer ou les remplacer selon sa
  politique de sécurité. **Les nouveaux jetons CLI expirent par défaut à 30 jours.**

Ne pas utiliser `alembic downgrade` sur la base qui contient des données réelles.
Les migrations automatisées sur la base de production nécessitent une sauvegarde,
un plan de restauration et une fenêtre de maintenance adaptée.

## Identité et permissions

Génération d'un jeton opaque à haute entropie, affiché une seule fois :

```bash
make api-token SUBJECT=admin ROLE=admin
# Ou, avec la CLI installée et un DSN configuré :
signalflow-api-token --subject alice --role editor --ttl-days 7
```

Les durées CLI valides sont de **1 à 365 jours**. Le jeton brut ne doit pas
figurer dans un dépôt, une URL, un script public ni un journal. La base ne
conserve que son empreinte ; un jeton révoqué ou expiré n'authentifie plus.
La révocation existe dans `TokenRepository.revoke()` mais n'est **pas encore
exposée dans un endpoint HTTP** : ne pas annoncer de fonction de gestion des
tokens dans une interface utilisateur.

Chaque route métier demande un `Authorization: Bearer <token>`.
Les permissions globales et l'appartenance projet sont contrôlées côté serveur.
Un projet étranger reste invisible (`404`), même si sa clé est devinée.
Les jetons statiques provenant de `SIGNALFLOW_API_TOKENS` sont réservés aux
tests ou au dépannage local et ne remplacent pas l'IAM d'entreprise.

## Exemple de parcours HTTP

Lancer l'API :

```bash
make api
# Documentation : http://127.0.0.1:8000/docs
```

Dans un terminal séparé, après avoir placé le jeton dans une variable locale
sans l'écrire dans l'historique :

```bash
curl -X POST http://127.0.0.1:8000/api/v1/projects \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"key":"demo","name":"Projet démo"}'

curl http://127.0.0.1:8000/api/v1/projects \
  -H "Authorization: Bearer $TOKEN"
```

Les routes `POST /api/v1/projects/demo/configurations`, `GET .../configurations`,
`GET .../configurations/{version}` et `GET .../configurations/compare` sont
explicitées dans [api.md](api.md). La configuration publiée doit passer la
validation existante de SignalFlow ; la base ne déclenche aucun entraînement.

## Vérification

Les tests rapides de persistance utilisent une SQLite **temporaire et isolée** :

```bash
python -m pytest -q tests/persistence tests/api/test_project_isolation.py
```

Les tests PostgreSQL sont opt-in. Ils **appliquent les migrations sur la base
indiquée par le DSN** et écrivent des données de test. Utiliser **exclusivement
une base PostgreSQL jetable dédiée aux tests**, et jamais une base applicative
avec des données importantes :

```bash
SIGNALFLOW_DATABASE_URL='postgresql+psycopg://<utilisateur>:<mot-de-passe>@127.0.0.1:<port>/signalflow_test' \
  python -m pytest -q --postgres tests/postgres
```

La suite SQLite ne démontre pas les transactions/conflits sous charge réelle
PostgreSQL. Pour valider complètement la phase 4 : appliquer les migrations à
une base PostgreSQL isolée, lancer le test de conflit concurrent, créer puis
relire des versions après redémarrage de l'API, et vérifier les permissions.

## Sauvegarde, restauration et limites

Sauvegarder **sans publier les mots de passe** (exemple générique) :

```bash
pg_dump -h 127.0.0.1 -p 5433 -U signalflow -Fc signalflow_app > backup-signalflow-app.dump
```

Conserver le fichier de sauvegarde hors Git et dans un espace chiffré avec des
permissions limitées. Tester la restauration **dans une autre base vide**,
avec les droits appropriés, avant de considérer la sauvegarde comme fiable.
Ne jamais restaurer un dump de démonstration par-dessus une base existante.

Les migrations actuelles n'affectent ni les données MLflow, ni les volumes
Prometheus/Grafana/Loki. Les futures phases ajouteront datasets (5), jobs
asynchrones (6), orchestrations (7), modèle registry (8), interface (9).
