# Phase 7 — orchestration ZenML locale

Le backend optionnel `zenml` exécute six étapes observables : `load`, `split`,
`preprocessing`, `training`, `evaluation`, `save`. Le runner Python existant reste
responsable de l’unique run MLflow, de ses rapports et de la télémétrie globale.
Il ne s’agit pas d’une étape ZenML enveloppant tout `run_training_spec`.

## Runtime isolé obligatoire avec le worker

Version choisie : **ZenML 0.98.0**, vérifiée avec Python 3.14, MLflow-skinny 3.18.0,
scikit-learn 1.9.1, SQLAlchemy 2.0.58 et Pydantic 2.12.5. Le fichier
`constraints/zenml-py314.txt` fixe les versions de l’environnement neuf testé.

ZenML impose Pydantic <=2.12.5 alors que notre API demande >=2.14. L’installation
commune a été essayée et rejetée par pip : ne pas contourner le résolveur avec
`--no-deps`, ni rétrograder l’environnement API. Le worker conserve son runtime
API et lance uniquement l’enfant dans un second environnement :

```sh
python3.14 -m venv /chemin/prive/zenml-runtime
/chemin/prive/zenml-runtime/bin/pip install \
  -c constraints/zenml-py314.txt '.[ml,monitoring,zenml]'
/chemin/prive/zenml-runtime/bin/pip check

# Avec les variables PostgreSQL/datasets/jobs décrites dans jobs.md :
export SIGNALFLOW_ORCHESTRATOR=zenml
export SIGNALFLOW_ZENML_PYTHON=/chemin/prive/zenml-runtime/bin/python
signalflow-worker
```

Ces variables sont choisies par l’opérateur, jamais par un client HTTP.
`local` reste le défaut. Le backend choisi est enregistré dans `request.json`
pour la reprise ; garder le runtime ZenML disponible tant qu’une tentative ou
son outbox en a besoin. L’API n’importe pas ZenML.

Chaque tentative possède son propre `ZENML_CONFIG_PATH`, ses métadonnées SQLite
et son stockage local. Aucune configuration de compte ZenML existante n’est
modifiée. La télémétrie analytique ZenML est désactivée. Le profil Compose
existant reste local : son image ne contient pas le second runtime ; un lancement
ZenML utilise actuellement le worker hôte configuré comme ci-dessus.

Pour l’API Python, dans le runtime ZenML :

```python
from signalflow.ml.contracts import ExecutionContext
from signalflow.ml.runner import run_training_spec

report = run_training_spec(spec, ExecutionContext(
    output_dir="/chemin/prive/resultats", dataset_root="/chemin/prive/datasets",
    orchestration="zenml",
))
print(report.to_dict()["orchestration"]["execution_id"])
```

Définir `ZENML_CONFIG_PATH` avant le premier import ZenML pour isoler également
ce mode Python. Le seul orchestrateur accepté est `local`, sur le même hôte et
le même système de fichiers que le moteur.

## Calculs, artefacts et cache

Le modèle, le prétraitement, le fit et les métriques sont extraits dans
`orchestration/local.py`, partagé par sklearn local et les étapes ZenML.
Imputation, normalisation et apprentissage utilisent exclusivement les indices
train ; validation/test ne servent qu’à évaluer. Même seed, partitions et
métriques que l’exécution locale.

Les étapes échangent des références privées vers des fichiers, pas les grands
tableaux sérialisés dans les paramètres ZenML. NumPy lit les données sans pickle ;
les seuls joblib chargés sont les modèles produits par les étapes dans leur
répertoire privé. Ils ne sont jamais fournis par le client. Une étape recharge
les données nécessaires ; ce premier backend ne vise pas l’exécution distribuée.

Le cache est désactivé au niveau pipeline **et** étapes : les références de
fichiers seules ne permettent pas de prouver la validité d’un cache. Deux jobs
identiques créent deux exécutions distinctes ; la déduplication des soumissions
reste assurée par la clé d’idempotence applicative.

Une erreur transitoire de lecture peut rejouer `load` une fois. Le fit et la
publication MLflow n’ont pas de retry automatique. Une erreur d’étape termine le
run MLflow en FAILED, sans produire de faux résultat réussi. Le suivi technique
des six étapes est natif ZenML ; l’instrumentation locale détaillée du trainer
n’est pas dupliquée dans ces étapes.

## Annulation et reprise

Les limites CPU/mémoire/temps et la supervision de phase 6 s’appliquent aussi à
l’enfant ZenML. Après arrêt confirmé du processus, l’outbox réconcilie MLflow
et les métadonnées ZenML : étapes encore actives et pipeline passent à `stopped`
ou `failed`. L’exécution annulée ne continue pas à entraîner en arrière-plan.

ZenML 0.98.0 recalcule le statut d’un pipeline statique depuis ses étapes : une
mise à jour directe en `CANCELLED` est ignorée et le run reste `running`. La
réconciliation demande donc `STOPPING` **avant** de clore les étapes non
terminées, puis relit le run et exige `STOPPED` (ou `CANCELLED`) ; sans relecture
conforme, l’outbox n’est pas acquittée et réessaiera. Seul le run identifié par
`output/orchestration/*/execution.json` de la tentative courante est modifié ;
un pipeline créé avant cette écriture n’a aucun run à réconcilier. Les étapes
`CANCELLING`/`STOPPING` sont fermées avec le statut cohérent, ce qui évite qu’une
étape réessayable (`load`) soit requalifiée en `RETRYING`. Un état terminal déjà
atteint n’est jamais réécrit.

Le rapport et le résultat HTTP contiennent `orchestration.backend` et
`orchestration.execution_id` (aucun chemin). La reprise d’un succès conserve cet
identifiant depuis le rapport, sans relancer le pipeline. Les métadonnées et
artefacts privés doivent être sauvegardés avec les tentatives.

## Sources et validation

Contrat de version : [ZenML 0.98.0 sur PyPI](https://pypi.org/project/zenml/0.98.0/).
Politique de cache : [documentation ZenML](https://docs.zenml.io/user-guides/starter-guide/cache-previous-executions).
Les API de retry et de réconciliation ont été vérifiées dans le package 0.98.0
installé, puis exercées par les tests.

Les tests opt-in du runtime s’exécutent ainsi, depuis le dépôt :

```sh
ZENML_CONFIG_PATH=/tmp/signalflow-zenml-tests ZENML_ANALYTICS_OPT_IN=false \
  /chemin/prive/zenml-runtime/bin/python -m pytest --zenml tests/orchestration -q
SIGNALFLOW_TEST_ZENML_PYTHON=/chemin/prive/zenml-runtime/bin/python \
  .venv/bin/python -m pytest tests/jobs/test_zenml_worker.py -q
```

Installer pytest dans le runtime pour les tests. Le déploiement, un serveur ZenML
central, les stacks distantes et le cache partagé ne font pas partie de cette phase.

Validation du 19 novembre 2026 : installation neuve réussie, `pip check` sans
incompatibilité ; 3 tests pipeline/parité/échec réussis en 42,93 s, puis le test de
retry réussi en 13,97 s. Le test d’intégration succès + annulation avec deux
runtimes a réussi en 78,21 s. Les deux wheels ont été construites dans
`/tmp/signalflow-phase7-wheels`. Aucun service utilisateur n’a été déployé.

Suite de non-régression après le correctif d’annulation : **419 réussis,
1 ignoré** en 324,03 s. Le test ignoré est celui du worker ZenML, validé
séparément avec son interpréteur dédié. `git diff --check` : aucun problème.

Le correctif de réconciliation a été validé en parallèle par dix tests
`tests/jobs/test_zenml_synchronization.py` avec un faux store reproduisant la
sémantique 0.98.0 (CANCELLED ignoré, RETRYING, étapes terminales, outbox non
acquittée), puis par la pile Docker isolée décrite dans
[zenml_docker_validation.md](zenml_docker_validation.md) : run annulé relu
`stopped`, étapes terminées et MLflow `KILLED`.

