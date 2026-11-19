# Validation Docker ZenML et limite du distribué

La stack de test est définie dans `compose.zenml-test.yaml`, sous le projet
`signalflow-zenml-check`. Elle utilise ses propres volumes et aucun port hôte.
L’image cible `zenml` de `web/api/Dockerfile` embarque Python 3.14 et deux runtimes :
API dans Python système, ZenML dans `/opt/zenml`. Les services applicatifs
existants gardent explicitement la cible `local`.

## Reproduire le test

```sh
docker compose -f compose.zenml-test.yaml build worker
docker compose -f compose.zenml-test.yaml up -d --scale worker=2 api worker
docker compose -f compose.zenml-test.yaml run --rm --no-deps validate
docker compose -f compose.zenml-test.yaml restart api worker
docker compose -f compose.zenml-test.yaml run --rm --no-deps validate \
  python /validation.py --verify-restart
# Nettoyage de cette stack de test uniquement :
docker compose -f compose.zenml-test.yaml down -v --remove-orphans
```

Le script `tests/docker/validate_zenml.py` vérifie le chargement d’un CSV via
HTTP, sa version persistante, deux soumissions simultanées idempotentes, une seule
tentative malgré deux workers, six étapes ZenML terminées, des métriques réelles,
l’annulation d’un pipeline actif, la synchronisation ZenML/MLflow et la conservation
du résultat après redémarrage. Les credentials sont propres à la stack jetable.

## Corrections découvertes lors de la préparation

Le `PYTHONPATH` du worker ne doit pas injecter le `site-packages` de l’API dans
le runtime ZenML. Avec des wheels installées, cela annulait l’isolation des deux
versions de Pydantic. Les chemins de sources éditables restent pris en charge.
L’enfant charge l’exécuteur avec `runpy.run_path` afin de ne pas ajouter le dossier
`jobs/` au chemin d’import : son `queue.py` masquerait le module standard `queue`.
Les 12 tests ciblés de jobs/reprise/limites passent avec ces corrections.

## Correctif annulation — validation isolée du 19 novembre 2026

Pour ne pas toucher à la stack `signalflow-zenml-check` déjà en cours, la
validation a été rejouée avec un **projet Compose distinct** et des volumes
entièrement neufs, ainsi qu’un tag d’image propre (un overlay temporaire
`/tmp/opencode/zenml-fix-override.yaml` ne redéfinit que `image:` des quatre
services applicatifs) :

```sh
docker compose -p signalflow-zenml-fix \
  -f compose.zenml-test.yaml -f /tmp/opencode/zenml-fix-override.yaml \
  build worker
docker compose -p signalflow-zenml-fix \
  -f compose.zenml-test.yaml -f /tmp/opencode/zenml-fix-override.yaml \
  up -d --scale worker=2 api worker
docker compose -p signalflow-zenml-fix \
  -f compose.zenml-test.yaml -f /tmp/opencode/zenml-fix-override.yaml \
  run --rm --no-deps validate
```

Résultat du build : `Image signalflow-zenml-fix:local Built`, avec les deux
`pip check` sans dépendance cassée (runtime API Pydantic 2.14.3, runtime ZenML
Pydantic 2.12.5 / ZenML 0.98.0). Résultat de la validation :

```
ZENML_TERMINAL_STATUS_OK completed
MLFLOW_TERMINAL_STATUS_OK FINISHED
CSV_TRAINING_SIX_STEPS_IDEMPOTENCY_TWO_WORKERS_OK
ZENML_TERMINAL_STATUS_OK stopped
MLFLOW_TERMINAL_STATUS_OK KILLED
ACTIVE_PIPELINE_CANCELLATION_AND_RECONCILIATION_OK
```

Le run ZenML annulé est donc bien **`stopped`** et non plus `running` ; toutes
ses étapes sont terminées, et le run MLflow correspondant est `KILLED`. Le
script vérifie aussi que l’identifiant du run appartient bien à l’attempt via
`output/orchestration/*/execution.json`.

Redémarrage des API/workers de cette stack isolée, puis relecture :

```
docker compose -p signalflow-zenml-fix \
  -f compose.zenml-test.yaml -f /tmp/opencode/zenml-fix-override.yaml \
  restart api worker
docker compose -p signalflow-zenml-fix \
  -f compose.zenml-test.yaml -f /tmp/opencode/zenml-fix-override.yaml \
  run --rm --no-deps validate python /validation.py --verify-restart
```

```
API_WORKER_RESTART_PERSISTENCE_OK
```

Une première exécution a échoué sur une incohérence interne du validateur
(`check_zenml(row, 'completed')` alors que la fonction attend le statut métier
`succeeded`) ; l’appel a été corrigé en `check_zenml(row, 'succeeded')` et le
scénario complet a été rejoué avec succès. Aucune donnée ni volume n’a été
supprimé : la pile isolée reste disponible pour inspection. Nettoyage éventuel,
uniquement après autorisation :

```sh
docker compose -p signalflow-zenml-fix \
  -f compose.zenml-test.yaml -f /tmp/opencode/zenml-fix-override.yaml \
  down -v --remove-orphans
```

## Exécution distribuée : non prise en charge

Deux conteneurs workers partageant le même volume ne constituent pas un pipeline
ML distribué. Le verrou commun sérialise les entraînements. Les six étapes ZenML
s’exécutent toujours avec l’orchestrateur local, dans un seul processus enfant.

Un contrôle négatif vérifie le refus d’un orchestrateur déclaré `kubernetes` ;
il ne lance aucun cluster Kubernetes et n’est pas une preuve d’exécution distante.
Le code utilise des références à des fichiers locaux entre étapes et refuse les
orchestrateurs distants avant entraînement. Pour tester réellement le distribué,
il faut d’abord adapter les artefacts à un stockage partagé distant, les contrats
d’étapes, le lancement distant, les permissions et la supervision/annulation.
Aucune exécution multi-nœuds n’est annoncée comme validée.
