# SignalFlow

SignalFlow standardise les entraînements de classification locaux : une configuration
validée décrit l'expérience, le pipeline produit un modèle et MLflow conserve les
paramètres, résultats et fichiers nécessaires pour comprendre chaque exécution.
Le monitoring CPU/RAM et les benchmarks permettent de mesurer les coûts avant
d'envisager une optimisation.

**Version actuelle : pipeline local scikit-learn, suivi MLflow SQLite, monitoring
psutil, benchmarks séquentiels et stack Docker Compose d’observabilité.** Utilisation en Python ou en ligne de commande.

## Architecture

```text
CLI train / benchmark
        │
        ├── Configuration Engine : YAML strict + JSON Schema + chemins confinés
        ├── Tool Registry : adaptateurs autorisés, imports différés
        └── Pipeline ML
              ├── Données → train / validation / test
              ├── Imputation → standardisation → régression logistique
              ├── Évaluation → modèle joblib + rapports JSON
              ├── MLflow : paramètres, métriques, artefacts et statut
              └── Monitoring local : durées, CPU, RSS

Benchmark → processus neuf par entraînement → statistiques + comparaison
```

Les responsabilités sont séparées dans `src/signalflow/{config,tools,ml,monitoring,benchmarking,observability}`.
Le pipeline livré a des étapes fixes ; le registre permet d'ajouter des adaptateurs
par du code Python de confiance, sans importer un module nommé dans le YAML.

## Fonctionnalités disponibles

- Configuration externe validée, erreurs typées, refus des clés YAML dupliquées,
  lectures bornées et références confinées au dossier de configuration.
- Datasets intégrés Iris, Wine et Breast Cancer, CSV numériques locaux (32 Mio
  maximum), ou classification synthétique reproductible.
- Partitions train/validation/test, stratification facultative, prétraitement
  appris uniquement sur train et régression logistique (`C`, `max_iter`, seed).
- Accuracy, précision, rappel et F1 macro sur validation et test.
- Pipeline complet sauvegardé en joblib, configuration effective, versions,
  empreinte du dataset et indices des partitions conservés.
- Suivi MLflow local sans serveur, mesures CPU/RSS et durées par phase facultatives.
- Benchmarks avec échauffements, répétitions, ordre mélangé reproductible,
  moyenne, médiane, écart-type, min/max et comparaison historique compatible.

## Prérequis et installation

Python **3.11 ou plus récent**. Le pipeline fonctionne sans serveur ni GPU.
Docker Engine et Compose v2 sont nécessaires pour la stack de visualisation.
Le moteur de configuration dépend de PyYAML et jsonschema ; les dépendances ML,
monitoring et tests restent dans des extras séparés.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev,ml,monitoring,observability]'
python -m pip check
signalflow --help
```

Pour la configuration seule : `python -m pip install -e .`.
Les versions exactes utilisées pour la validation figurent dans le
[rapport de stabilisation](docs/stabilisation.md).

## Entraînement léger pour la démonstration

Depuis la racine du dépôt :

```bash
signalflow train --config-dir examples/classification --output-dir outputs/demo
signalflow train --config-dir examples/classification \
  --training ./training-regularized.yaml --output-dir outputs/demo
```

Les deux expériences utilisent Iris, seed=42 et les mêmes partitions 60/20/20.
Elles comparent C=1 et C=0.05 ; comparer la validation, réserver le test au bilan
final. `python -m signalflow` est équivalent à `signalflow`.
Le JSON de résultat est écrit sur stdout, les diagnostics sur stderr.

## Benchmark CPU/RAM

```bash
signalflow benchmark --config-dir examples/benchmark --output-dir outputs/baseline
```

L'exemple génère 120 000 lignes × 120 caractéristiques et compare deux
régularisations : un échauffement puis trois répétitions mesurées par configuration.
Les huit entraînements sont séquentiels et utilisent des processus neufs.
Consulter `outputs/baseline/benchmarks/<uuid>/benchmark.md` et `benchmark.json`.

```bash
signalflow benchmark --config-dir examples/benchmark --output-dir outputs/new \
  --baseline /chemin/vers/benchmark.json
```

La comparaison historique exige des conditions compatibles. Le maximum RSS est
échantillonné, pas un pic garanti ; les alertes de régression sont heuristiques.
Voir [le protocole et les limites](docs/observability.md) et les
[résultats réellement mesurés](docs/benchmark_reference.md).

## Visualisation et logs avec Docker Compose

```bash
python scripts/prepare_observability.py
docker compose up -d --build
signalflow train --config-dir examples/classification --output-dir outputs/demo
```

Ouvrir [Grafana](http://localhost:3000) : les trois dashboards et les sources
Prometheus/Loki sont provisionnés automatiquement. [Prometheus](http://localhost:9090)
conserve les métriques des jobs terminés grâce à un exporteur persistant de snapshots.
Alloy lit les logs JSON partagés ; Node Exporter mesure le Linux hôte et cAdvisor
les conteneurs. MLflow garde les expériences ML. Aucun appel réseau à cette stack
n’est nécessaire dans le processus d’entraînement.

Consulter [le guide d’exploitation](docs/observability_stack.md) pour les chemins
hôte/conteneurs, les permissions, les particularités WSL2, les alertes et la reprise.
Les [résultats de validation](docs/observability_validation.md) distinguent les tests
unitaires des vérifications Docker réelles. Les composants de lecture/file persistante
Alloy sont épinglés en public preview ; leur reprise doit être revalidée à chaque mise à jour.

## Suivi MLflow et fichiers produits

Chaque entraînement crée `runs/<uuid>/` sous `--output-dir`, avec `model.joblib`,
`configuration.json`, `environment.json`, `dataset.json`, `split_indices.json`,
`metrics.json` et `report.json`. `monitoring.json` s'ajoute si le monitoring est actif.

MLflow utilise `tracking/mlflow.db` et `tracking/artifacts/` dans le même dossier
de sortie. Il conserve paramètres, métriques, fichiers et statut FINISHED/FAILED.
Le client Python permet de consulter les expériences sans serveur ni interface web.
La dépendance `mlflow-skinny` ne fournit pas ici de tableau de bord intégré.
Le modèle est un artefact joblib, pas une entrée du Model Registry.

Le [guide d'entretien](docs/demo_entretien.md) fournit les commandes de consultation
MLflow et de rechargement d'un modèle de confiance.

## Configuration et extension

Les configurations utilisateur ne sont pas embarquées dans la wheel. Copier un
dossier d'exemple complet, avec `schemas/`, pour l'utiliser hors du dépôt.
`config/` est un exemple du moteur de configuration seul : ses outils sont désactivés.

La résolution utilise `config_dir`, puis `SIGNALFLOW_CONFIG`, puis `./config`.
Les deux derniers sont fixés à l'import du chargeur. Les chemins référencés, y compris
`--training`, sont relatifs au dossier de configuration.

```python
from signalflow.config.loader import load_configuration
from signalflow.tools import ToolRegistry

registry = ToolRegistry()
configuration = load_configuration("config", registry=registry)
instances = registry.initialize(configuration.tools)  # {} avec config/ livré
```

Le registre générique ne contient aucune factory. Le pipeline enregistre explicitement
scikit-learn, MLflow et `metrics=local`. Les noms génériques Pandas, Docker et Trivy ne désignent pas des adaptateurs ML
opérationnels. Prometheus et Loki sont intégrés par la couche d’observabilité
et Compose, indépendamment des factories du registre.
Voir [les outils](docs/tools.md) et [les contrats du pipeline](docs/pipeline.md).

## Tests et distribution

```bash
python -m pytest -q
python -m pip wheel . --no-deps -w dist/
python -m pip install 'dist/signalflow-1.0.0-py3-none-any.whl[ml,monitoring,observability]'
```

Installer tous les extras pour éviter que les tests nécessitant ML/psutil soient
ignorés. La validation complète inclut aussi une wheel installée hors du dépôt,
des entraînements réels, un rechargement et un benchmark : voir le
[rapport de vérification](docs/stabilisation.md).

## Limites actuelles

- Données tabulaires numériques et régression logistique uniquement ; pas de
  variables catégorielles natives, texte, images, découpage temporel ou par groupes.
- Pas de tuning automatique, validation croisée ou choix automatique du modèle.
- Suivi local uniquement ; pas de registre de modèles, service de prédiction,
  reprise de run, détection de dérive ou surveillance de production.
- Pas d’interface web métier ni de déploiement du pipeline dans Kubernetes ;
  Grafana fournit la visualisation technique. Pas de PyTorch, GPU ou calcul distribué.
- Les durées dépendent de la machine et de sa charge. Une seed ne garantit pas
  des résultats identiques entre toutes les versions et tous les matériels.
- Les données brutes ne sont pas archivées ; leur empreinte ne les remplace pas.
- Les intégrations ne constituent pas une isolation de sécurité contre un autre
  processus modifiant simultanément les fichiers locaux.

## Feuille de route

1. Conserver une validation reproductible, documenter les mesures et intégrer les
   sources stabilisées à Git après revue.
2. Élargir les datasets et les protocoles d'évaluation selon des besoins concrets.
3. Étudier de nouveaux adaptateurs, dont PyTorch/GPU, avec mesures et contrats propres.
4. Envisager le service de prédiction et l'orchestration après validation du socle.

Ces évolutions sont prévues, pas disponibles dans la version actuelle.
