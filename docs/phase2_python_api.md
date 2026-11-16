# Phase 2 — API Python indépendante des fichiers YAML

## Périmètre livré

Une nouvelle façade permet de valider une spécification en mémoire puis d’entraîner
sans charger ni créer de YAML. Le moteur sklearn, le suivi MLflow et le collecteur
psutil sont réutilisés. La CLI, `run_training(config_dir, *, output_dir,
training_override)` et son résultat dictionnaire restent compatibles.

Cette phase n’ajoute ni API HTTP, ni base PostgreSQL, ni gestion de projets ou de
jobs. L’API Python est synchronique, destinée à du code de confiance. La prochaine
phase devra définir les DTO HTTP et contrôler les permissions et destinations.

## Exemple exécutable

```bash
python examples/train_from_objects.py --output-dir outputs/python-demo
```

Ce script fonctionne aussi après copie hors du dépôt, avec la wheel et ses extras
ML installés. Aucune copie de `config/`, `project.yaml` ou `tools.yaml` n’est requise.

```python
from signalflow.ml.contracts import ExecutionContext
from signalflow.ml.runner import run_training_spec
from signalflow.ml.validation import validate_training_spec

spec = validate_training_spec({
    "training": {
        "schema_version": "0.0.1", "seed": 42,
        "model": {"type": "logistic_regression",
                  "parameters": {"C": 1.0, "max_iter": 1000}},
    },
    "dataset": {
        "schema_version": "0.0.1",
        "source": {"type": "builtin", "name": "iris"},
        "split": {"validation": 0.2, "test": 0.2, "stratify": True},
    },
    "tracking": {
        "schema_version": "0.0.1",
        "experiment_name": "python-api", "run_name": "iris",
    },
})
report = run_training_spec(spec, ExecutionContext(output_dir="outputs/python-demo"))
print(report.run_id, report.status, report.metrics)
print(report.to_dict())
```

## Contrats publics

| API | Entrée | Sortie et effets | Erreurs |
| --- | --- | --- | --- |
| `validate_training_spec(document)` | Mapping JSON avec training, dataset, tracking ; monitoring facultatif/null | `TrainingSpec`, sans écriture ni SDK ML ; lecture des schémas embarqués | `ConfigValidationError` pour structure, `PipelineError` pour valeurs non JSON/non finies ou relations incohérentes ; erreur typée de destination monitoring |
| `TrainingSpec.to_dict()` | Aucune | Copie profonde du document, modifiable indépendamment | `PipelineError` si représentation forgée invalide |
| `ExecutionContext(output_dir, dataset_root=None)` | Chemins de confiance choisis par l’appelant | Contexte frozen ; chemins résolus immédiatement, aucun dossier créé | `PipelineError` pour chemin non résolvable/type invalide |
| `run_training_spec(spec, context)` | Valeur validée et contexte explicite | `TrainingReport` ; modèle, rapports et SQLite MLflow écrits ; monitoring facultatif | Même famille `ConfigError`/`PipelineError` et interruption que la CLI ; erreurs de confinement CSV |
| `TrainingReport` | Produit par le moteur | `run_id`, `status`, copie de `metrics`, `to_dict()` | Les échecs d’entraînement restent des exceptions, pas un faux rapport réussi |

La clé s’appelle `tracking` dans l’enveloppe Python ; son contenu correspond au
contrat historique de `mlops.yaml`. Aucun défaut de C, seed, nom d’expérience ou
partition n’est inventé. Les versions de schéma restent explicites dans chaque bloc.
Les fournisseurs sont ceux du pipeline actuel : scikit-learn, MLflow et monitoring
local facultatif. Aucun nom de module ou commande arbitraire n’est accepté.

Les représentations internes de `TrainingSpec` et `TrainingReport` sont des chaînes
JSON immuables, capturées sans référence aux dictionnaires d’origine. Utiliser le
validateur pour construire une spécification ; le runner revalide même une instance
construite directement. Modifier le document initial, un `to_dict()` ou les métriques
retournées ne change pas la valeur capturée. Ce mécanisme simple ne dépend ni de
FastAPI ni de Pydantic. Le stockage de versions et les digests persistants sont
réservés à la phase 4.

`RunConfiguration.snapshot()` retourne désormais une copie profonde. Les anciens
objets `RunConfiguration`/`LoadedConfiguration` conservent leurs champs dictionnaires
pour compatibilité : ils ne sont pas rendus profondément immuables. Le nouveau
contrat d’entrée `TrainingSpec` est indépendant de ces objets historiques.

## CSV, chemins et monitoring

Pour un CSV, le bloc dataset contient par exemple :

```python
"source": {"type": "csv", "path": "./iris.csv", "target": "target"}
```

Fournir `ExecutionContext(output_dir="outputs/csv", dataset_root="/dossier/donnees")`.
L’absence de racine est une erreur ; traversées hors racine et liens symboliques
sortants sont refusés avant création des artefacts. Le CSV reste soumis au parseur
numérique et à la limite de 32 Mio existants. Ce confinement n’isole pas un autre
processus hostile autorisé à modifier simultanément les fichiers.

Le bloc facultatif `monitoring` conserve le schéma existant. Son absence/null signifie
aucun monitoring ni export. Il peut contenir `enabled`, `interval_seconds`,
`max_samples`, `prometheus` et `logging`. Les destinations `directory` et
`SIGNALFLOW_OBSERVABILITY_DIR` gardent leurs règles historiques : la racine de
publication relative est celle du répertoire de travail, pas `output_dir`.

Cette API est locale et de confiance : une future API HTTP devra choisir elle-même
les chemins, filtrer les options d’infrastructure et masquer les chemins absolus
des rapports. `ExecutionContext` n’est pas un objet à désérialiser directement depuis
une requête utilisateur. Aucune protection multi-utilisateur n’est annoncée ici.

## Organisation et compatibilité

- `ml/contracts.py` : valeurs et contexte sans SDK ML.
- `ml/validation.py` : validation des objets par schémas embarqués et règles métier
  partagées avec le chargeur YAML.
- `config/schemas/*.schema.json` dans le package : training, dataset, mlops et
  monitoring, inclus dans la wheel via les données de package setuptools.
- `ml/config.py` : lecture des YAML et schémas locaux conservée ; règles sémantiques
  déléguées et snapshots indépendants.
- `ml/runner.py` : deux façades, un seul `_run_configuration` pour tout le cycle de vie.

Les copies sous `config/schemas` et dans les deux exemples restent disponibles pour
les usages historiques. Un test exige leur égalité avec les schémas embarqués.
Une modification de contrat doit mettre à jour ces copies ensemble. La façade YAML
continue à lire les schémas locaux explicitement référencés ; la façade objet ne
permet jamais au document appelant de choisir son propre validateur.

Les artefacts restent les mêmes. Pour une exécution par objets, `configuration.json`
indique `project.origin = "python"` et `sources = "memory"` pour les blocs fournis ;
il ne prétend pas avoir lu un projet YAML. Les identifiants, chemins, durées et
métadonnées d’origine varient naturellement entre deux runs. Les scores, partitions,
empreintes de données et coefficients sont les éléments comparés par les tests.

Les valeurs non JSON et non finies sont refusées avant validation de structure dans
la façade objet. Pour `Infinity`, le type précis d’erreur peut donc différer de la
façade YAML, qui le refuse via la borne JSON Schema ; les deux restent des erreurs
métier typées. Les erreurs historiques de la CLI ne sont pas modifiées volontairement.

## Validation de la livraison

Résultats exécutés le 16 novembre 2026 :
Aucun test Docker de panne n’est requis pour ce changement du moteur partagé ; la
stack et ses volumes restent inchangés. La fragilité temporelle du test Loki,
identifiée en phase 1, n’est pas corrigée dans cette phase.


| Contrôle | Résultat |
| --- | --- |
| Tests ciblés de configuration historiques | 49 réussis en 4,47 s |
| Nouveaux tests d’API Python | 28 réussis en 43,40 s |
| Suite complète | **280 réussis, aucun ignoré, en 157,15 s** |
| Construction wheel | Succès, quatre schémas embarqués |
| Installation dans un virtualenv vierge hors dépôt | Succès hors réseau, avec les wheels de dépendances déjà disponibles |
| `pip check`, import depuis site-packages, aide CLI | Succès |
| Exemple Python hors dépôt avec chargeur YAML bloqué | FINISHED, aucun YAML créé dans le répertoire de travail |
| Rechargement joblib et consultation MLflow | Succès, prédictions Iris initiales `[0, 0, 0]` |
| CLI YAML de la même wheel | FINISHED, métriques identiques à l’exécution par objets |
| `git diff --check` | Succès |

Les tests de parité comparent Iris, CSV et synthétique : scores, empreintes de données,
indices de partitions et coefficients du classificateur identiques. Les chemins,
identifiants et durées sont volontairement exclus de cette égalité. Des tests
vérifient aussi les erreurs typées, snapshots indépendants, valeurs forgées,
confinement CSV, imports optionnels, succès/échec avec publication et arrêt du thread.

Commandes principales :

```bash
.venv/bin/python -m pytest tests/ml/test_config.py tests/observability/test_configuration.py -q
.venv/bin/python -m pytest tests/ml/test_object_api.py -q
.venv/bin/python -m pytest -q
python3 -m pip wheel . --no-deps --no-build-isolation --no-index \
  -w outputs/phase2-validation/dist
python3 outputs/phase2-validation/verify_wheel.py
```

Le dernier script est une preuve locale générée pour cette validation, conservée
avec les logs dans `outputs/phase2-validation/` (ignoré par Git). L’exemple maintenu
et réutilisable est `examples/train_from_objects.py`. L’environnement isolé utilisé
est `/tmp/signalflow-phase2-hehwmb9h` ; aucune dépendance n’a été modifiée dans le
virtualenv du projet. L’installation complète a utilisé `--no-index` et les caches
de wheels locaux ; ce n’est pas un test de téléchargement depuis une machine neuve.

Les deux entraînements Iris de la wheel obtiennent accuracy et F1 macro de
validation **0,9333333333**, également sur le test. Aucun gain de performance n’est
annoncé : cette phase porte sur le contrat d’entrée et la compatibilité.

### Fichiers de cette phase

Créés :

- `src/signalflow/ml/contracts.py`
- `src/signalflow/ml/validation.py`
- `src/signalflow/config/schemas/training.schema.json`
- `src/signalflow/config/schemas/dataset.schema.json`
- `src/signalflow/config/schemas/mlops.schema.json`
- `src/signalflow/config/schemas/monitoring.schema.json`
- `tests/ml/test_object_api.py`
- `examples/train_from_objects.py`
- `docs/phase2_python_api.md`

Modifiés : `src/signalflow/ml/config.py`, `src/signalflow/ml/runner.py`,
`pyproject.toml`, `README.md`, `docs/pipeline.md`, `docs/roadmap_finalisation.md`.
Les modifications préexistantes des autres fichiers ont été conservées.

### Limites et suite

Le calcul reste synchronique et local ; les datasets CSV restent numériques. Les
copies de schémas sont contrôlées par tests, sans générateur ajouté. Les chemins et
options de publication relèvent encore du code opérateur. Les rapports sont des
contrats Python locaux, sans redaction HTTP ni RBAC. Ces frontières devront être
traitées par la phase 3, qui **n’a pas été commencée**. Aucun commit ni publication.
