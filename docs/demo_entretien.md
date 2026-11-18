# Démonstration SignalFlow — entretien Everdian (10 minutes)

## Préparation avant l'entretien

Installer `.[dev,ml,monitoring,observability]`, activer le virtualenv et se placer à la racine du
projet. Vérifier `signalflow --help`. Préparer le benchmark en amont : sa durée
n'entre pas dans les dix minutes. Conserver les sorties dans `outputs/`, ignoré par Git.

```bash
python scripts/prepare_observability.py
make observability-up
mkdir -p outputs/demo
signalflow benchmark --config-dir examples/benchmark --output-dir outputs/demo-baseline \
  > outputs/demo/benchmark-report.json
```

Ouvrir le README, `src/signalflow/ml/runner.py`,
`docs/benchmark_reference.md` et le `benchmark.md` généré. Vérifier les commandes
ci-dessous avant l'entretien. Ouvrir Grafana sur http://localhost:3000 et vérifier
les targets Prometheus sur http://localhost:9090/targets. Le pipeline reste autonome
si la stack est arrêtée, mais les dashboards nécessitent ses services. Le benchmark est synthétique et n'utilise aucune donnée Everdian.

## Minute 0–2 : problème

« Deux entraînements peuvent sembler comparables alors que leurs données, leur
seed, leurs partitions ou leurs versions diffèrent. SignalFlow rend ces paramètres
explicites, les valide avant l'entraînement et conserve les résultats avec leurs
conditions d'exécution. Il mesure aussi le coût CPU/RAM avant toute optimisation. »

Montrer les petits fichiers `training.yaml` et `dataset.yaml` de classification.
Expliquer la séparation entre configuration d'outils, données et hyperparamètres.
Préciser le périmètre : classification numérique locale, un modèle actuellement.

## Minute 2–4 : architecture

Montrer le schéma du README et suivre un appel à `run_training()` :

1. Le Configuration Engine valide YAML, schémas et chemins.
2. Le Tool Registry instancie les adaptateurs explicitement autorisés.
3. Scikit-learn charge, sépare, apprend le prétraitement sur train et entraîne.
4. L'orchestrateur sauvegarde les résultats et appelle le client MLflow local.
5. Le collecteur facultatif mesure le processus ; il reste indépendant du modèle.
6. Les snapshots et logs locaux sont lus par l’exporteur persistant et Alloy :
   Prometheus/Loki alimentent Grafana sans appel réseau dans le pipeline.

Souligner deux décisions : aucune factory importée depuis un nom arbitraire YAML ;
aucun apprentissage du prétraitement sur validation/test. Le pipeline est fixe,
le registre extensible : ajouter une intégration exige encore du code Python.

## Minute 4–6 : entraînement en direct

```bash
signalflow train --config-dir examples/classification --output-dir outputs/demo \
  > outputs/demo/iris.json
python -m json.tool outputs/demo/iris.json
```

Montrer `status`, `run_id`, les métriques de validation, le chemin du modèle et
`tracking_uri`. Les résultats ne sont pas des valeurs codées en dur.
Si le temps le permet, lancer la seconde régularisation :

```bash
signalflow train --config-dir examples/classification \
  --training ./training-regularized.yaml --output-dir outputs/demo \
  > outputs/demo/iris-regularized.json
```

Expliquer : C plus petit signifie une régularisation plus forte, pas forcément un
meilleur modèle. Iris est choisi pour la rapidité, pas pour mesurer des gains CPU.

Recharger **le modèle que l'on vient de produire**, jamais un joblib inconnu :

```bash
python - <<'PY'
import json
import joblib
from sklearn.datasets import load_iris
report = json.load(open('outputs/demo/iris.json'))
model = joblib.load(report['model'])
print('Prédictions :', model.predict(load_iris().data[:3]).tolist())
PY
```

## Minute 6–8 : suivi et mesures

Dans Grafana, montrer le compteur terminé d’Overview, puis RSS et CPU dans
Infrastructure & Resources. Ouvrir Logs & Diagnostics et retrouver le run ; distinguer
les ressources du processus, du Linux hôte et des conteneurs. Le job est déjà terminé,
mais son snapshot reste scrapable. Garder environ 40 secondes pour cette vue et
utiliser la commande MLflow préparée puis le tableau du benchmark pour le reste.


Consulter MLflow via son client, sans lancer une interface web :

```bash
python - <<'PY'
import json
from mlflow.tracking import MlflowClient
report = json.load(open('outputs/demo/iris.json'))
client = MlflowClient(tracking_uri=report['tracking_uri'])
experiment = client.get_experiment_by_name(report['experiment_name'])
for run in client.search_runs([experiment.experiment_id],
                             order_by=['metrics.validation_f1_macro DESC']):
    print(run.info.run_id, run.info.status, 'C=', run.data.params['C'],
          'F1 validation=', run.data.metrics.get('validation_f1_macro'))
print('Artefacts :', [a.path for a in client.list_artifacts(report['run_id'])])
PY
```

Afficher le rapport du benchmark préparé :

```bash
python - <<'PY'
import json
from pathlib import Path
report = json.load(open('outputs/demo/benchmark-report.json'))
print(Path(report['directory'], 'benchmark.md').read_text())
PY
```

Présenter moyenne **et** écart-type, CPU en secondes, RSS en Mio, accuracy et F1.
Distinguer fit du classificateur, entraînement avec prétraitement, pipeline et
commande complète. Montrer que les empreintes du dataset et des partitions sont
contrôlées entre répétitions. Le RSS est un maximum observé ; les pics courts
peuvent être manqués. Trois répétitions ne permettent pas de généraliser un gain.
Utiliser les conclusions du rapport réel, sans annoncer de vainqueur à l'avance.

## Minute 8–10 : bilan

Décisions techniques : configuration stricte, imports facultatifs, pipeline sklearn
sauvegardé en entier, MLflow SQLite sans infrastructure, mesures locales explicites.

Difficultés : éviter les fuites de données, distinguer temps de fit et coût global,
arrêter le collecteur en cas d'erreur, garantir des conditions comparables et
préserver une erreur initiale si la publication de son rapport échoue aussi.
Le test de panne Loki avec redémarrage d’Alloy a conduit à retenir une file disque
OTLP persistante ; ses composants public preview exigent une revalidation aux mises à jour.

Limites : pas de serving, registre de modèles, GPU, catégories natives, tuning,
réentraînement ou orchestration distribuée. Les données brutes restent à archiver
séparément. La charge de la machine et ses caches influencent les durées.

Suite envisagée : enrichir les protocoles et datasets, puis évaluer de nouveaux
adaptateurs sur une baseline mesurée. PyTorch, optimisation GPU et Kubernetes
restent des évolutions futures. Terminer par les preuves de validation du
[rapport d’observabilité](observability_validation.md).

## Principales fonctions Python

Les exceptions métier héritent de `ConfigError`. La CLI renvoie 1 pour ces erreurs,
130 pour interruption, et argparse renvoie 2 pour une syntaxe invalide.

| Fonction | Rôle et entrées | Sortie | Exceptions principales |
| --- | --- | --- | --- |
| `load_configuration(config_dir, registry, limits)` | Valide projet et outils, sans SDK ni entraînement | `LoadedConfiguration` | `ConfigError`, erreurs de chemins, schémas et fournisseurs |
| `ToolRegistry.register(category, name, factory)` | Enregistre une factory de confiance | `None` | `ToolUnknownError`, `ValueError` |
| `ToolRegistry.initialize(declarations)` | Instancie les outils actifs après validation | Dictionnaire d'instances par catégorie | `ToolUnavailableError`, `ToolInitializationError`, `ToolUnknownError` |
| `load_run_configuration(config_dir, registry, training_override)` | Valide les contrats métier et leurs relations | `RunConfiguration` | `ConfigError`, `PipelineError` |
| `load_dataset(source, path)` | Lit CSV, charge un builtin ou génère les données | `Dataset` : X, y, colonnes, empreinte | `DatasetError`, `ConfigError` |
| `split_dataset(dataset, settings, seed)` | Sépare les indices avant tout fit | `Split` train/validation/test | `DatasetError` |
| `SklearnTrainer.train(training, dataset_config, dataset_path, instrumentation)` | Apprend imputation, scaler et classificateur ; évalue | `TrainingResult` : pipeline, métriques, durée, données et partitions | `TrainingError`, `DatasetError`, `ConfigError` |
| `run_training(config_dir, output_dir, training_override)` | Orchestre les adaptateurs, fichiers et statuts | Rapport JSON sous forme de dictionnaire | `ConfigError`/`PipelineError`, `KeyboardInterrupt` |
| `MLflowTracker.start(settings, output)` | Ouvre le suivi SQLite et crée le run | Identifiant du run | `TrackingError` |
| `MLflowTracker.log(run_id, parameters, metrics, artifacts)` | Enregistre valeurs et fichiers | `None` | `TrackingError` |
| `MLflowTracker.finish(run_id, status)` | Termine le run | `None` | `TrackingError` |
| `LocalMonitor.start(settings)` | Lance la sonde et son thread | `None` | `MonitoringError` |
| `LocalMonitor.stage(name)` | Mesure un bloc, y compris en échec | Gestionnaire de contexte | Propage l'exception du bloc |
| `LocalMonitor.stop(pipeline_started_at)` | Joint le thread et agrège, appel idempotent | Mesures, échantillons, méthode et complétude | `MonitoringError` si non démarré |
| `run_benchmark(config_dir, output_dir, benchmark_reference, baseline)` | Lance les processus et vérifie la comparabilité | Rapport brut et agrégé | `ConfigError`/`BenchmarkError`, `KeyboardInterrupt` |
| `summarize(values)` | Calcule les statistiques de mesures comparables | Moyenne, médiane, écart-type, min/max, CV | `ValueError` si moins de deux valeurs |
| `main(argv)` | Analyse les arguments et affiche le rapport | Code de sortie entier | `SystemExit` pour aide/syntaxe |

Les signatures exactes utilisent des arguments nommés pour les paramètres après
`*` ; les noms ci-dessus décrivent le contrat, pas une invitation à tous les passer
positionnellement. Les docstrings du code précisent les effets de bord.

| Fonction d’observabilité | Entrées et rôle | Sortie / erreurs |
| --- | --- | --- |
| `TelemetrySession.start/finish` | Statut, durée, observations existantes | Publication locale, erreurs d’I/O signalées sans annuler le ML |
| `TelemetrySession.stage` | Observateur et nom de phase autorisé | Contexte délégué, propage l’exception métier |
| `MetricsPublisher.publish` | Type de job, statut, durée, observation | Mise à jour atomique bornée ; erreurs fichier/verrou capturées par la session |
| `JsonEvents.write` | Événement interne et identifiants | Ligne JSON UTC, rotation sous verrou ; erreurs d’I/O |
| `SnapshotCollector.collect` | Snapshot persistant | Familles Prometheus ; jauge d’échec si snapshot invalide |
| `destination` | Paramètres YAML ou variable opérateur | Chemin résolu ; `ObservabilityConfigError` hors racine autorisée |
