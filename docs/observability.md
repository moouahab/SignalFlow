# Observabilité locale et baseline des entraînements

La stack Prometheus/Grafana/Loki/Alloy et les exporteurs système sont désormais
intégrés. Voir [installation et exploitation](observability_stack.md) et
[validation réelle](observability_validation.md). Cette page précise les mesures
psutil et le protocole de benchmark, réutilisés sans second collecteur.

Cette version mesure avant d'optimiser : aucun changement de nombre de threads,
GPU, modèle ou hyperparamètres n'est choisi automatiquement selon les résultats.
Le pipeline et le suivi MLflow restent ceux de la classification locale.

## Utilisation

```bash
python -m pip install -e '.[dev,ml,monitoring]'

signalflow train --config-dir examples/benchmark --output-dir outputs/measured
signalflow benchmark --config-dir examples/benchmark --output-dir outputs/baseline
```

Le benchmark écrit `benchmarks/<uuid>/benchmark.json` (données brutes et statistiques)
et `benchmark.md` (rapport lisible), avec tous les runs MLflow sous le même dossier.
Le rapport JSON est aussi retourné sur stdout. Chaque entraînement a sa propre
configuration, son modèle et son identifiant de run. Aucun serveur n'est démarré.

Pour comparer une exécution ultérieure à une baseline enregistrée :

```bash
signalflow benchmark --config-dir examples/benchmark --output-dir outputs/new \
  --baseline /chemin/vers/benchmark.json
```

La comparaison historique refuse les datasets, partitions, seeds, configurations
de mesure, versions de dépendances, matériels visibles ou hyperparamètres différents.
L'empreinte du code SignalFlow est enregistrée, mais peut différer **entre deux
campagnes historiques** afin de détecter une régression de code. Elle doit rester
constante à l'intérieur d'une campagne.

## Activation déclarative

`tools.yaml` sélectionne le fournisseur explicitement enregistré par le pipeline :

```yaml
tools:
  training:
    provider: scikit-learn
    enabled: true
  experiment_tracking:
    provider: mlflow
    enabled: true
  metrics:
    provider: local
    enabled: true
```

`project.yaml` référence les paramètres spécialisés :

```yaml
config:
  monitoring:
    path: ./monitoring.yaml
    schema: ./schemas/monitoring.schema.json
    description: Mesures locales CPU et RSS
    required: true
    enabled: true
```

`monitoring.yaml` :

```yaml
schema_version: "0.0.1"
interval_seconds: 0.05
max_samples: 10000
```

Période demandée : 50 ms. Minimum autorisé : 20 ms, maximum : 10 s.
`max_samples` borne les observations brutes conservées en mémoire, pas la fenêtre
observée : après saturation, les agrégats continuent d'être mis à jour. Le nombre
de points non conservés est explicite. Le défaut de l'exemple limite la série à
10 000 dictionnaires ; il ne constitue pas une garantie de coût nul.

Mettre `tools.metrics.enabled: false` désactive les lectures de ressources et le
thread, sans importer psutil ni lire monitoring.yaml. L'absence de metrics conserve
également le comportement historique. Aucun monitoring.json n'est alors ajouté.
Le benchmark exige le monitoring actif et des mesures complètes. Une référence
monitoring active requise mais absente produit une erreur typée. Les fichiers
chargés réutilisent le chargeur YAML strict, JSON Schema et le résolveur sécurisé.
Le registre générique reste inchangé : le fournisseur local est enregistré par
`signalflow.ml.registry.create_registry()`, comme les vrais adaptateurs ML.

## Sens des métriques

Toutes les durées utilisent `time.perf_counter()`, horloge monotone adaptée aux
intervalles. Elles ne sont pas calculées à partir de dates civiles.

| Métrique | Périmètre |
| --- | --- |
| stage_initialization_seconds | Imports/initialisation des SDK, préparation des premiers artefacts et du run MLflow |
| stage_dataset_load_seconds | Chargement ou génération, validation des données et empreinte du contenu |
| stage_data_split_seconds | Séparation des indices et vérification des classes du train |
| stage_preprocessing_seconds | Extraction du train, fit_transform de l'imputation et du scaler sur train uniquement |
| stage_model_fit_seconds | Fit du classificateur sur les caractéristiques du train transformées |
| stage_predict_evaluate_seconds | Transformation et prédictions validation/test, puis calcul des métriques ML |
| stage_artifact_save_seconds | Sauvegarde locale du modèle, des indices, des résultats et du rapport |
| stage_tracking_export_seconds | Export normal des résultats/modèle dans MLflow et terminaison du run |
| pipeline_total_seconds | Entrée dans run_training, validation comprise, jusqu'à la fin du suivi normal et l'arrêt du collecteur |
| command_wall_seconds | Benchmark seulement : lancement à terminaison complète du processus enfant, démarrage Python et publication finale compris |

Le total pipeline exclut uniquement la publication finale de ses propres mesures
et le démarrage de l'interpréteur. Cette frontière évite une mesure qui devrait
s'inclure elle-même à l'infini. Le temps complet de commande est mesuré par le
parent du benchmark. Les étapes ne couvrent pas nécessairement tout le total :
validation initiale, construction d'objets, code d'orchestration et petites opérations
intermédiaires restent inclus dans le total.

L'ancien `training_duration_seconds` reste disponible : durée du prétraitement
appris et du fit, ensemble. Les transformations sont les mêmes composants que
ceux du Pipeline sauvegardé ; aucune seconde phase d'apprentissage n'est ajoutée.
L'évaluation transforme les holdouts sans fit. Les tests de non-fuite historiques
continuent de passer.

### Mémoire : RSS du processus, pas consommation totale du système

- `process_rss_start_bytes` et `process_rss_end_bytes` : RSS aux bornes de collecte.
- `process_rss_sampled_max_bytes` : **maximum observé parmi les échantillons**, une
  borne inférieure du véritable maximum de RSS sur la fenêtre. Un pic entre deux
  lectures peut être manqué ; ce champ n'est jamais présenté comme un pic garanti.
- `process_rss_sample_mean_bytes` : moyenne arithmétique des observations, pas une
  intégrale temporelle ni une moyenne pondérée par les intervalles.

Le RSS inclut les pages résidentes partagées et exclut la mémoire swappée. Ce n'est
ni la mémoire Python uniquement, ni USS/PSS, ni un pic mémoire attribuable au seul
modèle. Le processus, ses SDK, son dataset et son collecteur sont inclus. Les enfants
ne sont pas comptés. Aucun `ru_maxrss` de toute la vie du processus n'est assimilé
à un pic spécifique à l'entraînement.

### CPU : compteurs du processus, pas utilisation CPU de l'hôte

`process_cpu_seconds` est la différence des temps cumulés user+system du processus
entre la première et la dernière lecture. Les threads du processus, dont celui du
collecteur, sont inclus ; les processus enfants sont exclus.

`process_cpu_mean_percent = 100 × delta_cpu_seconds / resource_window_seconds`.
Un cœur occupé vaut 100 % ; plusieurs threads peuvent produire plus de 100 %.
La valeur n'est pas divisée par le nombre de CPU de la machine.
`process_cpu_sampled_max_percent` est le maximum des ratios entre deux lectures
consécutives, et non un pic instantané. Les compteurs CPU ont une résolution finie :
les intervalles très courts, notamment le dernier, peuvent amplifier leur bruit.

La première lecture fournit une référence réelle ; aucun zéro initial artificiel
de `cpu_percent()` n'est utilisé. Les timestamps de chaque observation permettent
de vérifier les intervalles effectivement obtenus. L'ordonnanceur et le GIL peuvent
retarder le thread : `sampling_max_gap_seconds` rend ces retards visibles.

### Fenêtre, environnement et coût

Le collecteur démarre après la validation des configurations et avant les imports
lourds. `resource_window_seconds` décrit sa fenêtre réelle, différente du total
pipeline. Une dernière lecture suit l'arrêt du thread.

`monitoring.json` sépare les observations du processus des métadonnées système :
CPU logiques/physiques visibles, taille d'affinité du processus, modèle CPU,
architecture, mémoire système totale et disponible au début, plateforme, version
psutil. Les quotas cgroup ne sont pas mesurés ; les valeurs visibles ne prouvent
pas les ressources allouées à un conteneur. Il n'y a pas de métrique d'utilisation
CPU système. `environment.json` conserve Python, les versions logicielles, le
nombre de threads d'entraînement et une empreinte SHA-256 des sources Python SignalFlow.
Aucune variable d'environnement, commande avec credentials ou donnée secrète n'est
collectée.

`sampling_read_cpu_seconds` mesure le CPU des callbacks de collecte avec
`time.thread_time()`. `sampling_read_wall_seconds` mesure leur temps écoulé, y
compris les désordonnancements. Ces mesures **ne quantifient pas toute la perturbation**
induite par l'observateur, ses allocations, le GIL ou les horloges de chaque étape.
Le coût est limité par la période minimale, le stockage borné, l'absence de réseau
et l'absence d'export pendant le fit. Il n'est pas soustrait artificiellement des
résultats. Une campagne off/local/full mesure désormais ce coût global ; voir le
[rapport d’observabilité](observability_validation.md), sans en déduire une garantie universelle.

## MLflow et erreurs

Les mesures sont publiées sous le préfixe `monitoring.`. Le fichier monitoring.json
contient aussi les échantillons, la méthode, le matériel et les étapes échouées.
`monitoring.collection_complete` vaut 1 pour une collecte complète et 0 sinon.
Les hyperparamètres, métriques ML, configuration, dataset et modèle restent tracés.

Une lecture initiale impossible lève MonitoringError. Une panne ultérieure de la
sonde arrête sa collecte, garde les mesures partielles et marque `complete: false`.
L'entraînement peut terminer, mais le benchmark refuse ces mesures ; il ne les
agrège jamais comme si elles étaient complètes. Une erreur ou interruption du
pipeline arrête et rejoint le thread, puis tente de tracer les mesures partielles
et le statut MLflow FAILED. Une défaillance du backend peut empêcher leur sauvegarde.
Les messages d'exception des sondes ne sont pas copiés dans les rapports.

## Protocole du benchmark

L'exemple génère localement, avec scikit-learn et seed=123, **120 000 lignes,
120 caractéristiques, 50 informatives, 20 redondantes et 3 classes**. Il ne nécessite
aucun téléchargement. Seed de séparation=42, train/validation/test=60/20/20.
Les deux modèles utilisent C=1 et C=0.05, max_iter=1000. La génération fait partie
de dataset_load ; ce benchmark n'est donc pas un test d'I/O disque.

```yaml
schema_version: "0.0.1"
configurations:
  - name: baseline
    training: ./training.yaml
  - name: regularized
    training: ./training-regularized.yaml
repetitions: 3
warmup_repetitions: 1
order_seed: 42
regression_threshold_percent: 15
timeout_seconds: 180
```

Chaque bloc contient une répétition de chaque configuration dans un ordre mélangé
avec une seed explicite. Les blocs d'échauffement précèdent les blocs mesurés et
restent traçables, mais sont exclus des statistiques. Le temps de création initiale
de la base MLflow est ainsi hors des répétitions mesurées de l'exemple.

Chaque run utilise un processus Python neuf. Le parent impose les variables de
threads BLAS/OpenMP à 1 et PYTHONHASHSEED=0 ; le pipeline limite aussi ses opérations
numériques à un thread. L'affinité, les caches OS, les fréquences CPU et la charge
de la machine ne sont pas contrôlés. Les runs sont séquentiels, sans concurrence
introduite par SignalFlow. Un processus neuf ne signifie pas « cache disque froid ».

Les empreintes des valeurs du dataset et des indices de séparation doivent être
identiques pour **tous** les runs, échauffements compris. Les versions, le matériel
visible et les paramètres de mesure sont également vérifiés. Les seeds de séparation
différentes sont refusées avant exécution. Un timeout, une erreur ou des conditions
incompatibles interrompent la campagne et produisent un rapport partiel FAILED ou
INTERRUPTED, sans retirer discrètement les runs en échec.

Pour chaque métrique : effectif, moyenne, médiane, écart-type d'échantillon (ddof=1),
minimum, maximum et coefficient de variation. Au moins deux répétitions sont exigées ;
les trois répétitions de l'exemple établissent une première baseline, pas une preuve
statistique générale.

Un drapeau de **régression potentielle** exige une hausse de moyenne supérieure au
seuil configuré ET à deux fois le plus grand écart-type des deux groupes. Cette règle
est une heuristique explicite, pas un test statistique ni un intervalle de confiance.
Les comparaisons portent sur fit, pipeline, commande, CPU et RSS maximal échantillonné.
Entre deux hyperparamètres différents, elles décrivent un compromis de coût ; elles
ne prouvent pas une régression logicielle. Comparer une baseline historique compatible
sert à surveiller les modifications de code. Ne jamais sélectionner un modèle seulement
sur sa vitesse : les métriques de validation restent disponibles dans le rapport.

**Iris est trop court pour conclure solidement sur les performances.** Il est conservé
pour les tests fonctionnels rapides. Même ce dataset synthétique plus grand ne représente
pas toutes les charges de production ; multiplier les répétitions et choisir des données
représentatives avant toute décision d'optimisation.

## Architecture et extensions

- `monitoring/timing.py` : interface Instrumentation.stage et implémentation nulle.
- `monitoring/resources.py` : contrat de sonde et mesures psutil du processus.
- `monitoring/local.py` : chronométrage, thread de collecte, agrégation et limites.
- `ml/registry.py` : enregistrement explicite du fournisseur metrics/local.
- `ml/adapters/sklearn.py` : contextes de phases, sans import psutil ni MLflow.
- `ml/runner.py` : cycle de vie du collecteur et publication finale.
- `ml/adapters/mlflow.py` : export de dictionnaires numériques et de fichiers,
  sans dépendre du collecteur.
- `benchmarking/statistics.py` : statistiques et signaux, sans entraînement.
- `benchmarking/runner.py` : validation, isolation, ordre, comparabilité et rapports.

L'interface d'étape peut être utilisée par un futur moteur PyTorch. Les sondes sont
séparées des modèles et du tracking. Aucune valeur GPU/VRAM fictive n'est produite ;
un futur adaptateur devra fournir ses propres méthodes et limites. Prometheus et
Grafana sont démarrés séparément par Compose ; Kubernetes et les entraînements
distribués restent absents.

Référence de mesure : [documentation psutil](https://psutil.readthedocs.io/stable/).

## Livraison stabilisée et référence mesurée

Le benchmark agrège également `training_duration_seconds` (prétraitement + fit),
au même titre que les durées de phase. Le tableau Markdown expose entraînement,
fit, pipeline, commande, temps CPU, RSS maximal observé, accuracy et F1 de validation,
avec moyenne et écart-type. Les huit métriques ML, dont celles sur test, restent
accessibles dans le JSON. Les comparaisons historiques figurent aussi dans le Markdown.

Les erreurs de création du dossier de sortie ou de publication du rapport final
sont traduites en `BenchmarkError`. Un rapport partiel FAILED est tenté si le dossier
reste accessible ; un disque inaccessible ne permet pas de garantir sa sauvegarde.

La [campagne de référence](benchmark_reference.md) décrit les valeurs réellement
observées, les versions et les limites d'interprétation. Le
[rapport de stabilisation](stabilisation.md) distingue les tests automatisés des
commandes exécutées sur une wheel installée dans un virtualenv vierge hors dépôt.
