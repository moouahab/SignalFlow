# Phase 8 — comparaison et registre de modèles local

Les expériences restent les jobs du projet. MLflow possède les versions de
modèles et les alias ; les tables applicatives conservent les demandes humaines
de promotion, leurs références et les intentions de déplacement d'alias.

## Configuration

Installer les extras ML du moteur (`pip install '.[ml]' ./web/api`). Le packaging
sklearn fonctionne avec **mlflow-skinny 3.18.0 et pandas 3.1.1** ; le serveur MLflow
complet n'est pas nécessaire. pandas est désormais une dépendance de l'extra ML.
Mettre également à jour le runtime ZenML avec les contraintes du dépôt.

Appliquer `SIGNALFLOW_DATABASE_URL=... alembic upgrade head` sur la base applicative.
La migration `0005_models` ajoute deux tables, sans modifier les runs historiques.
Configurer l'API avec les chemins opérateur :

```sh
export SIGNALFLOW_JOB_ROOT=/chemin/prive/jobs
export SIGNALFLOW_REGISTRY_ROOT=/chemin/prive/registry
```

L'API doit lire les tentatives du worker ; le registre est un répertoire privé
en écriture. `compose.app.yaml` fournit le montage des jobs en lecture seule et
un volume distinct pour le registre. Les montages API et worker doivent désigner
les mêmes données. Aucun déploiement ni migration de base utilisateur n'est
effectué automatiquement par cette phase.

## Comparer

`POST /api/v1/projects/{key}/experiments/compare` :

```json
{"run_ids":["<job-1>","<job-2>"],"metric":"validation_accuracy"}
```

Deux à cinquante jobs distincts, tous autorisés dans le projet, sont requis.
La réponse conserve l'ordre demandé, les métriques et hyperparamètres, et signale
les différences de dataset, partition, environnement ou cible. Une mesure absente,
un run non réussi ou une provenance historique inconnue interdit de déclarer les
runs comparables. Les métriques test restent consultables avec l'avertissement
`TEST_METRIC_NOT_FOR_SELECTION`. Aucun classement ni promotion automatique.

## Promouvoir explicitement

`POST /api/v1/projects/{key}/models` :

```json
{"name":"iris","run_id":"<job-id>","reason":"Choix humain après examen de la validation"}
```

Le champ `run_id` désigne l'identifiant métier du job, comme dans les routes runs.
Il faut un job `succeeded`, un tracking synchronisé et un run MLflow `FINISHED`.
La permission globale `model:write` et le rôle projet editor/admin sont requis.

Le moteur produit le package depuis le modèle en mémoire, avec prétraitement,
signature d'entrée/sortie et dépendances versionnées. Les données utilisées pour
inférer la signature proviennent uniquement du train et ne sont pas publiées.
Le format cloudpickle est explicite pour être identique dans les runtimes locaux
et ZenML. Les empreintes du package et de sa provenance sont conservées dans le
résultat durable du job.

La promotion ne charge aucun pickle : elle vérifie les empreintes et chemins,
copie les fichiers dans le registre, puis crée une version MLflow. Les noms sont
préfixés par l'identifiant interne du projet. La réponse expose une URI
`models:/sf_<project-id>_<name>/<version>`, la provenance et l'auteur ; aucun chemin
local. Avec `MLFLOW_REGISTRY_URI=sqlite:////chemin/prive/registry/registry.db`, cette
URI est résoluble par les clients MLflow disposant du stockage local. Charger
un modèle demeure réservé à un consommateur de confiance.

La paire nom logique/job est idempotente : une nouvelle demande renvoie la même
version et conserve le premier auteur/motif. Une intention est commitée avant
l'appel MLflow ; après une panne, une répétition retrouve la version grâce à son
tag `signalflow.registration_id`. Conserver les tentatives tant qu'une promotion
est incomplète. Les versions n'ont aucune route de remplacement ou suppression.

Les anciens runs restent lisibles et comparables avec avertissement. Ils ne sont
pas convertis automatiquement : sans package fiable, la promotion répond
`MODEL_PACKAGE_REQUIRED`. Un nouvel entraînement est nécessaire ; aucun import
de fichier joblib, de chemin ou d'URI arbitraire n'est accepté.

## Versions et alias

- `GET /projects/{key}/models` : noms ayant une demande de promotion.
- `GET /projects/{key}/models/{name}/versions` : versions lues depuis MLflow.
- `GET /projects/{key}/models/{name}/aliases/{alias}` : version actuelle ou `null`.
- `PUT /projects/{key}/models/{name}/aliases/{alias}` : déplacement explicite.
- `GET /projects/{key}/models/{name}/audit` : les 100 dernières intentions d'alias.

Ces chemins sont préfixés par `/api/v1`. Exemple de déplacement :

```json
{"version":"2","expected_version":"1","reason":"Nouvelle validation approuvée"}
```

Pour créer un alias, fournir explicitement `expected_version: null`. Si l'alias
a changé, la réponse est `409 ALIAS_CONFLICT` : relire avant de décider. Le journal
enregistre une **intention**, pas une garantie de succès si le processus s'arrête
pendant l'appel MLflow ; le GET d'alias reste la source de vérité. Le verrou de
fichier sérialise promotions et déplacements entre processus partageant le même
registre local, y compris entre instances API.

Ce contrat suppose un stockage local commun avec verrouillage POSIX fonctionnel
et des écritures exclusivement via l'API. Les écritures directes MLflow, un registre
distant et des déploiements multi-nœuds ne sont pas couverts par cette garantie.
Sauvegarder ensemble la base applicative, le registre et ses packages.

## Validation

```sh
.venv/bin/python -m pytest tests/models tests/ml tests/persistence tests/jobs/test_worker.py -q
ZENML_CONFIG_PATH=/tmp/phase8-zenml ZENML_ANALYTICS_OPT_IN=false \
  /chemin/runtime-zenml/bin/python -m pytest --zenml tests/orchestration -q
```

Références : [format sklearn et signature](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.sklearn.html)
et [versions et alias du client MLflow](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.client.html).

Vérifications du 19 novembre 2026 : les quatre tests runtime ZenML passent en
48,85 s après ajout de pandas, et `pip check` ne signale aucune incompatibilité.
Le rechargement pyfunc avec MLflow-skinny a également produit des prédictions.
Le package vérifié venait du runtime API : MLflow a signalé la différence de
version cloudpickle (3.1.2 contre 3.1.3 dans ZenML), sans échec de prédiction.
Pour déployer un modèle, utiliser les dépendances exactes de son package.

Le dernier passage ciblé registre/OpenAPI/migration compte **9 tests réussis en
41,49 s**, dont création concurrente, reprise après publication, alias concurrents,
refus des symlinks et artefacts altérés, permissions et références interprojets.
La suite moteur/persistence/worker a produit 101 succès et cinq échecs corrigés
(normalisation d'alias et assertions des nouveaux artefacts). Les contrôles API,
parité YAML/objets et reprise worker ont ensuite produit 64 succès ; leur seule
assertion OpenAPI restante a été corrigée et validée dans les neuf tests finaux.
La configuration Compose est valide (`docker compose ... config --quiet`) ;
aucune reconstruction Docker ni validation PostgreSQL supplémentaire n'a été lancée.
