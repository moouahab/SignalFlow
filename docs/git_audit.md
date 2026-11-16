# Audit Git et périmètre de stabilisation

## État initial vérifié

Le dépôt comportait 19 fichiers suivis, 11 fichiers suivis modifiés et 71 fichiers
non suivis. Les sources ML, monitoring, benchmarking, leurs tests, les schémas et
la documentation étaient déjà présents localement mais absents de l’index Git.
Les modifications existantes ont été examinées ; leur diff initial est conservé
localement dans `outputs/stabilisation/preexisting.patch`.

## Sources préexistantes non suivies à inclure après revue

Ces fichiers sont nécessaires au projet ou à ses exemples/tests/documentation.
Ils ne sont pas des artefacts générés. Aucun `git add`, commit ou push n’a été exécuté.

```text
config/schemas/benchmark.schema.json
config/schemas/dataset.schema.json
config/schemas/mlops.schema.json
config/schemas/monitoring.schema.json
config/schemas/tools.schema.json
config/schemas/training.schema.json
config/tools.yaml
docs/observability.md
docs/pipeline.md
docs/tools.md
examples/benchmark/README.md
examples/benchmark/benchmark.yaml
examples/benchmark/dataset.yaml
examples/benchmark/mlops.yaml
examples/benchmark/monitoring.yaml
examples/benchmark/project.yaml
examples/benchmark/schemas/benchmark.schema.json
examples/benchmark/schemas/dataset.schema.json
examples/benchmark/schemas/mlops.schema.json
examples/benchmark/schemas/monitoring.schema.json
examples/benchmark/schemas/project.schema.json
examples/benchmark/schemas/tools.schema.json
examples/benchmark/schemas/training.schema.json
examples/benchmark/tools.yaml
examples/benchmark/training-regularized.yaml
examples/benchmark/training.yaml
examples/classification/README.md
examples/classification/dataset.yaml
examples/classification/mlops.yaml
examples/classification/project.yaml
examples/classification/schemas/benchmark.schema.json
examples/classification/schemas/dataset.schema.json
examples/classification/schemas/mlops.schema.json
examples/classification/schemas/monitoring.schema.json
examples/classification/schemas/project.schema.json
examples/classification/schemas/tools.schema.json
examples/classification/schemas/training.schema.json
examples/classification/tools.yaml
examples/classification/training-regularized.yaml
examples/classification/training.yaml
src/signalflow/__main__.py
src/signalflow/benchmarking/__init__.py
src/signalflow/benchmarking/runner.py
src/signalflow/benchmarking/statistics.py
src/signalflow/cli.py
src/signalflow/config/loader/tools.py
src/signalflow/ml/__init__.py
src/signalflow/ml/adapters/__init__.py
src/signalflow/ml/adapters/data.py
src/signalflow/ml/adapters/mlflow.py
src/signalflow/ml/adapters/sklearn.py
src/signalflow/ml/config.py
src/signalflow/ml/errors.py
src/signalflow/ml/registry.py
src/signalflow/ml/runner.py
src/signalflow/monitoring/__init__.py
src/signalflow/monitoring/local.py
src/signalflow/monitoring/resources.py
src/signalflow/monitoring/timing.py
src/signalflow/tools/__init__.py
src/signalflow/tools/registry.py
tests/config/test_tools.py
tests/ml/conftest.py
tests/ml/test_config.py
tests/ml/test_pipeline.py
tests/ml/test_training.py
tests/observability/conftest.py
tests/observability/test_benchmark.py
tests/observability/test_collector.py
tests/observability/test_configuration.py
tests/observability/test_integration.py
```

## Artefacts exclus

Les dossiers `.venv/`, `build/`, `dist/`, `outputs/`, les caches Python/pytest,
les modèles joblib et les fichiers `.env`/clés/secrets sont exclus par `.gitignore`.
La stabilisation ajoute les wheels et SQLite (`*.db`, `*.sqlite`, `*.sqlite3`,
avec fichiers annexes `-wal`, `-shm`, etc.), même hors des sorties habituelles.
Les contrôles `git check-ignore` confirment ces exclusions. Aucun modèle, base,
wheel, cache ou fichier de clé n’apparaît dans les fichiers suivis ou candidats.
Une recherche de signatures usuelles de clés privées et jetons n’a trouvé aucun
candidat ; cette vérification limitée n’est pas une certification exhaustive.

Les rapports bruts, bases, modèles et dépendances téléchargées restent dans
`outputs/stabilisation/` ou dans le dossier temporaire de validation. Seuls les
résultats commentés sont repris dans la documentation. Aucun artefact préexistant
n’a été supprimé.

## Modifications de cette mission

- `.gitignore` : SQLite et wheels exclus partout.
- `config/project.yaml` : suppression d’espaces en fin de ligne sur une ligne vide préexistante.
- `README.md`, `docs/pipeline.md`, `docs/tools.md`, `docs/observability.md` et les deux README d’exemples : documentation actualisée.
- `src/signalflow/benchmarking/runner.py` : erreurs d’écriture typées, durée totale d’entraînement agrégée, rapport Markdown complet et comparaison historique visible.
- `src/signalflow/monitoring/local.py` : échec du démarrage du thread traduit en erreur métier.
- `src/signalflow/ml/registry.py` et `src/signalflow/cli.py` : docstrings et diagnostic d’interruption cohérents avec les deux commandes.
- `tests/observability/test_benchmark.py`, `test_collector.py`, `test_integration.py` : régressions et durée agrégée vérifiées.

## Documents créés

- `docs/demo_entretien.md` : déroulé de dix minutes, commandes et fonctions Python.
- `docs/benchmark_reference.md` : mesures réelles et limites.
- `docs/stabilisation.md` : résultats, commandes et risques restants.
- `docs/git_audit.md` : présent inventaire.

## État Git attendu à la livraison

Les modifications restent dans l’espace de travail ; les sources nouvelles restent
non suivies tant que leur ajout n’est pas approuvé. Il faut relire la liste avec
`git status --short --untracked-files=all`, puis sélectionner les sources, tests,
exemples, schémas et documents. Ne pas forcer l’ajout des fichiers ignorés.
L’index est laissé inchangé. Aucune publication n’a eu lieu.
