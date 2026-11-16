# Premier pipeline ML local

SignalFlow exécute maintenant un entraînement réel de classification avec
scikit-learn et trace l'expérience dans MLflow local. Ce parcours est distinct du
chargement de configuration : `load_config()` et `load_configuration()` gardent
leurs interfaces et ne lancent aucun entraînement.

Le [monitoring et le benchmarking locaux](observability.md) ajoutent des durées
par phase, des mesures CPU/RSS et des répétitions comparables, de manière facultative.

## Installation et deux expériences

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev,ml]'

signalflow train --config-dir examples/classification --output-dir outputs/demo
signalflow train --config-dir examples/classification \
  --training ./training-regularized.yaml --output-dir outputs/demo
```

`python -m signalflow train ...` est une alternative équivalente. La commande
retourne un rapport JSON sur stdout : statut, identifiant MLflow, hyperparamètres,
seed, huit métriques, durée et chemins des artefacts. Les diagnostics sont sur
stderr. Codes de sortie : 0 pour succès, 1 pour erreur métier, 130 pour interruption.

L'extra `ml` installe scikit-learn, NumPy, joblib, threadpoolctl et le client
`mlflow-skinny` avec SQLAlchemy/Alembic pour SQLite. Aucun serveur distant ni
interface web MLflow n'est nécessaire. Pandas n'est pas requis par ce pipeline.
Les SDK ne sont importés que lors de l'initialisation d'un adaptateur activé.
`signalflow --help` et la validation fonctionnent sans ces extras.

Les deux fichiers d'entraînement utilisent Iris et seed=42 :

| Fichier | C | max_iter |
| --- | ---: | ---: |
| training.yaml | 1.0 | 1000 |
| training-regularized.yaml | 0.05 | 1000 |

Une valeur C plus petite augmente la régularisation. Les deux runs ont exactement
les mêmes partitions et peuvent être comparés sur les métriques **validation**.
Ne pas choisir les hyperparamètres selon le test : celui-ci sert à l'évaluation
finale. Cette première version calcule néanmoins les deux évaluations à chaque
run ; elle n'automatise pas la sélection de modèle.

Comparaison via le client MLflow, sans interface web :

```python
from pathlib import Path
from mlflow.tracking import MlflowClient

uri = f"sqlite:///{Path('outputs/demo/tracking/mlflow.db').resolve()}"
client = MlflowClient(tracking_uri=uri)
experiment = client.get_experiment_by_name("signalflow-classification")
for run in client.search_runs(
    [experiment.experiment_id],
    filter_string="attributes.status = 'FINISHED'",
    order_by=["metrics.validation_f1_macro DESC"],
):
    print(run.info.run_id, run.data.params["C"], run.data.metrics["validation_f1_macro"])
```

## Contrats et sources de vérité

L'exemple complet est dans `examples/classification/`. Les configurations
utilisateur restent externes au package installé.

| Fichier | Responsabilité |
| --- | --- |
| project.yaml | Références vers les fichiers, leurs schémas et étapes autorisées |
| tools.yaml | Sélection/activation de scikit-learn et MLflow |
| training.yaml | Modèle logistic_regression, C, max_iter et seed |
| dataset.yaml | Dataset intégré ou CSV local, fractions et stratification |
| mlops.yaml | Nom d'expérience et nom de run MLflow |

Les schémas canoniques sont dans `config/schemas/`, avec une copie autonome dans
l'exemple. Un test vérifie leur concordance. Tous les documents portent
`schema_version: "0.0.1"` et refusent les propriétés inconnues.

`mlops.yaml` réutilise le nom de la référence tracking déjà présente dans
project.yaml. Le backend est exclusivement local : base SQLite et artefacts sous
`--output-dir`. Aucune URI distante ni credential n'est accepté dans ce contrat.
Le choix de la destination des écritures est explicite sur la commande, hors YAML.

Exemple training :

```yaml
schema_version: "0.0.1"
seed: 42
model:
  type: logistic_regression
  parameters:
    C: 1.0
    max_iter: 1000
```

Les fractions `validation` et `test` sont relatives à **l'ensemble du dataset**.
Leur somme doit être inférieure à 1 ; le reste constitue le train. Les fractions
sont strictement positives. La seed est un entier entre 0 et 2^32-1. C doit être
strictement positif et fini ; les NaN YAML sont aussi refusés sémantiquement.

```yaml
schema_version: "0.0.1"
source:
  type: builtin
  name: iris
split:
  validation: 0.2
  test: 0.2
  stratify: true
```

Autres datasets intégrés : `wine`, `breast_cancer`, tous sans téléchargement.
La source `synthetic` génère aussi des données via `make_classification` ; voir
`examples/benchmark/dataset.yaml` pour ses dimensions et sa seed distincte.
Pour un fichier local, remplacer uniquement `source` :

```yaml
source:
  type: csv
  path: ./observations.csv
  target: label
```

Le CSV doit avoir un en-tête unique, une cible non vide et des caractéristiques
numériques ; les cellules de caractéristiques vides sont imputées. Les infinis,
les colonnes entièrement vides, les lignes incohérentes et les datasets d'une
seule classe sont refusés. Toutes les classes doivent figurer dans le train.
Une stratification impossible faute d'effectifs produit `DatasetError`.
La lecture CSV est bornée à 32 Mio. La cible est retirée des caractéristiques.

Tous les chemins d'entrée sont relatifs au dossier project.yaml, y compris
`--training`. Ils passent par le résolveur sécurisé existant. Les chemins absolus,
les sorties par `..` et les liens externes sont refusés. Les références optionnelles
`tools.*.config`, si présentes, doivent correspondre à celles du projet ; l'override
CLI training est ensuite pris en compte et enregistré dans la configuration effective.

## Exécution et absence de fuite

Le parcours pris en charge est fixe :

```text
validate_config → validate_dataset → validate_model → train → evaluate → validate_artifacts
```

Les autres étapes de project.yaml ne sont jamais ignorées silencieusement : une
liste différente est refusée, de même que `stop_on_failure: false`. Les seuls
outils métier actifs acceptés sont `training=scikit-learn` et
`experiment_tracking=mlflow`, avec `metrics=local` facultatif. Désactiver l'un des
deux outils métier refuse l'exécution avant le fit.
Les déclarations model/testing/security héritées du schéma global de projet sont
désactivées dans l'exemple et ne désignent pas des étapes effectivement exécutées.
Le dossier `config/` historique reste un exemple du seul moteur de configuration ;
pour entraîner, utiliser l'exemple complet de classification.

Le découpage est réalisé sur les indices, avant tout fit. Un seul `Pipeline`
scikit-learn réunit : imputation médiane, StandardScaler et LogisticRegression
(solver lbfgs). Il est ajusté uniquement sur train puis réutilisé sans fit sur
validation/test. Les métriques sont accuracy, précision macro, rappel macro et
F1 macro, avec `zero_division=0`. Une non-convergence est considérée comme un
échec explicite ; augmenter max_iter plutôt que publier silencieusement ce run.

Le test de non-fuite modifie radicalement les caractéristiques de validation/test
et vérifie que les statistiques d'imputation, de standardisation et les coefficients
du modèle restent inchangés. Cette garantie concerne le prétraitement ; elle ne
détecte pas des données déjà contaminées ou des observations corrélées entre groupes.

## Artefacts et suivi

Chaque invocation crée un nouveau dossier `runs/<uuid>/` sous la sortie :

- `configuration.json` : documents effectifs, outils et chemins sources ;
- `environment.json` : versions Python/bibliothèques, plateforme et nombre de threads ;
- `dataset.json` : SHA-256 des valeurs réellement chargées, colonnes, classes, effectifs ;
- `split_indices.json` : indices exacts de chaque partition ;
- `model.joblib` : pipeline complet, prétraitement et classificateur ;
- `metrics.json` et `report.json` : résultats et identité du run.

MLflow reçoit les paramètres, seed, fractions, empreinte du dataset, métriques,
durée du fit et une copie des fichiers. La configuration est tracée avant le fit.
Le backend est `tracking/mlflow.db` et les artefacts sont dans `tracking/artifacts/`.
L'adaptateur utilise un MlflowClient propre : aucune session active ni URI globale
MLflow n'est modifiée. Un échec après création du run tente d'enregistrer un rapport
FAILED et de terminer le run au même statut. Un backend inaccessible peut empêcher
cette dernière mise à jour ; l'erreur initiale n'est pas masquée.

Le modèle est enregistré **comme artefact joblib**, pas comme modèle enregistré
dans MLflow Model Registry ou comme flavor MLflow servant. Ne recharger que des
fichiers joblib de confiance. Pour recharger un modèle issu de ce pipeline :

```python
import joblib
model = joblib.load("/chemin/du/run/model.joblib")
predictions = model.predict(nouvelles_caracteristiques_numeriques)
```

## Architecture Python

```text
signalflow/cli.py                  arguments et rapport de commande
signalflow/__main__.py             python -m signalflow
signalflow/ml/config.py            chargement métier, cohérence, snapshot effectif
signalflow/ml/registry.py          factories sklearn/MLflow explicitement autorisées
signalflow/ml/runner.py            orchestration, artefacts, cycle de vie du run
signalflow/ml/errors.py            erreurs métier héritant de ConfigError
signalflow/ml/adapters/data.py     datasets numériques et partitions
signalflow/ml/adapters/sklearn.py  entraînement, évaluation, sauvegarde
signalflow/ml/adapters/mlflow.py   suivi local indépendant de l'entraînement
```

Le registre par défaut reste un catalogue sans factories. `create_registry()`
construit un registre indépendant enrichi des adaptateurs sklearn, MLflow et monitoring local pour le
pipeline. `load_run_configuration()` appelle `load_configuration()` et réutilise
les fonctions existantes de lecture, de validation JSON et de résolution. Il lit
chaque document et schéma une seule fois, sans second parseur ni SDK ML.
`run_training()` orchestre les adaptateurs. Les APIs historiques ne sont pas modifiées.

## Tests et limites

Vérification de la livraison initiale du pipeline, avant observabilité :
**182 tests réussis, aucun ignoré**, dont les
123 tests initiaux. Un avertissement de dépréciation provient de MLflow avec
SQLAlchemy 2.1 (`noload`) ; aucun échec fonctionnel n'a été observé.
`pip check` ne signale aucune dépendance incohérente.

La wheel a été construite puis installée dans le virtualenv. Les deux commandes
`signalflow train` ont été exécutées depuis un dossier temporaire hors dépôt,
avec une copie autonome des configurations et sans PYTHONPATH. L'import a été
vérifié depuis site-packages, les deux runs ont le statut MLflow FINISHED et
leurs sept artefacts sont présents. Résultats vérifiés sur Iris, seed=42 :

| C | Accuracy validation | F1 macro validation | Accuracy test | F1 macro test |
| ---: | ---: | ---: | ---: | ---: |
| 1.0 | 0.933333 | 0.933333 | 0.933333 | 0.933333 |
| 0.05 | 0.866667 | 0.865320 | 0.833333 | 0.829497 |

Ces résultats ont été obtenus avec scikit-learn 1.9.1, NumPy 2.5.4 et
mlflow-skinny 3.18.0. Les versions complètes figurent dans environment.json de
chaque run. Un entraînement supplémentaire avec l'import de pandas bloqué a
également terminé avec succès.

```bash
python -m pytest -q
```

Les tests de configuration restent exécutables sans extras ML. Les modules de
tests d'entraînement/suivi utilisent `pytest.importorskip` si ces extras ne sont
pas installés ; pour une vérification complète, installer `.[dev,ml]`.

Les contrôles couvrent les contrats, chemins dangereux, dépendances absentes,
initialisation différée, données intégrées/CSV, partitions disjointes et exhaustives,
absence de fuite, entraînement réel, reproductibilité, sauvegarde/rechargement,
artefacts MLflow, statut FAILED, deux expériences comparables et CLI hors dépôt.
Un run complet est également testé en bloquant les connexions réseau.

La seed et la limitation temporaire BLAS à un thread donnent des coefficients et
métriques identiques dans l'environnement testé. Les durées et identifiants de runs
varient ; l'identité bit à bit entre versions de bibliothèques ou matériels n'est
pas garantie. Conserver le dataset, les configurations et les versions enregistrées.
Les données brutes ne sont pas copiées dans MLflow ; leur empreinte ne remplace
pas leur archivage externe.

Cette version ne traite que les données numériques IID et une régression
logistique. Pas de features catégorielles, découpage temporel/par groupe, tuning,
cross-validation, téléchargement de données, registre de modèles, reprise de run,
exécution parallèle, Kubernetes ou GPU. L'initialisation concurrente d'une même
expérience n'est pas gérée ; les exemples lancent les runs séquentiellement.

Références de conception : [prévention des fuites avec scikit-learn Pipeline](https://scikit-learn.org/stable/common_pitfalls)
et [API de suivi MLflow](https://mlflow.org/docs/latest/tracking/tracking-api/).

## Validation de la version stabilisée

Les résultats historiques ci-dessus décrivent la livraison initiale. La validation
actuelle, incluant le monitoring, la wheel et les commandes hors dépôt, est
consignée dans [le rapport de stabilisation](stabilisation.md).
Le [guide de démonstration](demo_entretien.md) présente le parcours en dix minutes.

## Couche d’observabilité

Le pipeline réutilise `LocalMonitor` via `LoggedInstrumentation`. `TelemetrySession`
publie des agrégats locaux et des événements structurés ; l’exporteur persistant et
Alloy assurent les échanges réseau hors du processus ML. Le registre générique ne
change pas et les interfaces MLflow restent conservées. Voir les contrats de
configuration, métriques et reprise dans [le guide de la stack](observability_stack.md).
Les erreurs de publication sont diagnostiquées sans annuler un entraînement valide.
