# SignalFlow Web API — FastAPI

API HTTP locale de validation et de gestion des projets / configurations.
Le paquet Python `signalflow-web-api` est distinct du moteur `signalflow`.

## Installation

```bash
python -m pip install -e .
python -m pip install -e 'web/api[postgres,test]'
```

L'extra `postgres` installe le driver PostgreSQL et Alembic ; SQLAlchemy est
une dépendance de l'API elle-même. La base PostgreSQL applicative est distincte
et isolée du stockage MLflow ; voir [database.md](../../docs/database.md).

## Démarrage local

```bash
make app-db-up
make app-migrate
make api-token SUBJECT=admin ROLE=admin
make api
```

L'API écoute sur `127.0.0.1:8000` ; documentation interactive :
http://127.0.0.1:8000/docs. Les routes métier nécessitent un token Bearer.
Le conteneur et les mots de passe par défaut sont réservés au **développement
local**. Ne pas exposer l'API sur Internet.

Principaux contrats : santé, schéma et validation de configurations, catalogue
des algorithmes, projets, appartenances, versions et export YAML. Aucun appel
HTTP ne lance un entraînement : `POST /api/v1/trainings` renvoie `501`.

Références : [Contrats HTTP](../../docs/api.md) et
[Base PostgreSQL](../../docs/database.md).
