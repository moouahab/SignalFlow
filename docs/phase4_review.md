# SignalFlow — revue et correctifs de la phase 4

## Provenance

Revue réalisée à partir de l'archive fournie par l'utilisateur, exportée depuis
la branche Git `phase4` (commit indiqué dans la conversation : `f2723cc`).
Aucun service du poste utilisateur n'a été démarré ou supprimé ; une base
PostgreSQL **jetable** a été créée puis supprimée dans le conteneur existant
pour la validation finale du 18/11/2026.

## Correctifs réalisés

- **Traçabilité des auteurs** : les routes de lecture d'une version et de liste
  des versions récupèrent désormais l'auteur stocké (`author_id`) plutôt que
  le lecteur authentifié. Un test HTTP a reproduit le défaut avant correction
  et passe après correction.
- **Packaging FastAPI** : la dépendance SQLAlchemy requise à l'import est
  explicitement déclarée ; l'extra PostgreSQL apporte Alembic.
- **Jetons** : nouvelle colonne `expires_at` via migration additive
  `0002_token_expiration`. Les nouveaux jetons créés par la CLI ont une durée
  de validité de 30 jours (réglable entre 1 et 365 jours). La résolution refuse
  les jetons expirés. Les jetons plus anciens sont conservés sans expiration
  jusqu'à révocation explicite.
- **Concordance ORM/migrations** : alignement des déclarations de clés étrangères
  et des contraintes d'unicité sur le schéma effectivement créé par `0001` ;
  aucun changement destructeur de tables n'a été ajouté.
- **Documentation** : rédaction de `docs/database.md` et actualisation du
  README de l'API, des contrats et de la feuille de route.
- **Tests** : contrôle d'immutabilité des snapshots, non-régression de l'auteur,
  expiration des jetons, contrôle d'alignement Alembic sur base SQLite jetable,
  et test de concurrence PostgreSQL opt-in (écrit puis exécuté avec succès sur
  une base jetable, voir la validation réelle ci-dessous).

## Validations dans cet environnement

- Suite complète avec la pile installée (MLflow 3.18, Starlette 1.10) :
  **378 réussis, aucun avertissement** (`python -m pytest -q`).
- Les deux échecs historiques (`test_native_trace_creates_span` et
  `test_optional_imports_and_missing_dependency`) avaient été reproduits sur
  l'archive initiale non modifiée ; ils ne se produisent plus avec la pile
  actuelle et ne constituent pas une régression du correctif.
- Tests ciblés API/persistance : **67 réussis** (SQLite isolée).
- `alembic upgrade head` puis `alembic check` sur base SQLite temporaire :
  **réussis, aucun écart**.

## Validation PostgreSQL réelle (18/11/2026)

Base neuve `signalflow_verify_7ca398c1` créée puis supprimée dans le conteneur
`signalflow-app-postgres-1` (PostgreSQL 17, `127.0.0.1:5433`). La base
applicative `signalflow_app` n'a pas été touchée.

1. **Migrations** : `alembic upgrade head` → `0002_token_expiration (head)` et
   6 tables créées ; `alembic check` → `No new upgrade operations detected.` ;
   `alembic current` → `0002_token_expiration (head)`.
2. **Tests opt-in** :
   `SIGNALFLOW_DATABASE_URL=<dsn jetable> python -m pytest -q --postgres tests/postgres`
   → **2 réussis en 3,99 s**, dont `test_concurrent_publication_has_single_winner`
   (`['conflict', 'created']`, versions `[1, 2]`).
3. **Redémarrage et droits** : version 1 créée par `alice` dans un premier
   processus, puis relue dans un **second processus** (nouvelle application et
   nouveau moteur DB) → version et auteur conservés, historique `[1]` ;
   écriture refusée à un lecteur (403) et projet non partagé invisible (404).

Logs conservés dans `/tmp/opencode/` : `pg_alembic_upgrade.log`,
`pg_alembic_check.log`, `pg_alembic_current.log`, `pg_tests.log`,
`pg_api_write.log`, `pg_api_read.log`.

## Reste à faire avant déploiement applicatif

1. Vérifier les dépendances Python et construire les deux wheels.
2. Sauvegarder la base applicative avant toute application réelle de la
   migration `0002` : les contrôles ci-dessus n'ont concerné qu'une base jetable.

La phase 4 est validée sur PostgreSQL de test ; elle ne doit pas être annoncée
comme déployée en production avant ces deux points.

## Hors périmètre

Le correctif a été intégré par l'utilisateur dans le commit `9d411f1` ; aucun
push n'a été effectué. Pas de modification des volumes ou données
MLflow/PostgreSQL applicatifs de l'utilisateur (seule une base jetable a été
créée puis supprimée). Pas d'interface graphique, upload dataset,
jobs d'entraînement asynchrones, OIDC, interface HTTP de révocation des tokens,
pagination ni endpoint HTTP de suppression de projet. Ces fonctions nécessitent
un développement et des tests spécifiques.
