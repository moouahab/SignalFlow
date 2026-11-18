# Exploiter la stack d’observabilité locale

SignalFlow tourne sur l’hôte Linux ; Docker héberge les services de consultation.
MLflow conserve les expériences, paramètres, scores et modèles. Prometheus conserve
les métriques techniques agrégées ; Loki conserve les événements. Aucun des deux
ne remplace MLflow.

## Installation et démarrage

Prérequis : Python ≥ 3.11, Docker Engine et Compose v2 sous Linux/WSL2,
accès aux images lors de la première installation. Depuis la racine du dépôt :

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev,ml,monitoring,observability]'
make observability-prepare
docker compose config --quiet
make observability-up
signalflow train --config-dir examples/classification --output-dir outputs/demo
```

La préparation crée le dossier partagé et un mot de passe administrateur Grafana
aléatoire dans `secrets/grafana_password`, sans remplacer un secret existant.
Elle est nécessaire avant le premier démarrage : Docker ne doit pas créer un
répertoire de logs appartenant à root. Les démarrages suivants utilisent simplement
`make observability-up`, qui ne démarre que les services d'observabilité : les
services optionnels `ollama` et `mlflow` de la compose restent arrêtés. Attendre la disponibilité de Loki et quelques scrapes
(5 secondes d’intervalle) avant de chercher le nouveau run.

- Grafana : <http://localhost:3000>, consultation anonyme **Viewer**, dossier SignalFlow.
- Prometheus : <http://localhost:9090>, requête `signalflow_trainings_succeeded_total`.
- Grafana Explore / Loki : `{service_name="signalflow"} | json`.
- Administration Grafana : utilisateur `admin`, secret local créé ci-dessus.
- MLflow : `tracking/mlflow.db` sous le dossier de sortie, consultation par
  `MlflowClient` décrite dans [le guide de démonstration](demo_entretien.md).
  L’extra `mlflow-skinny` ne fournit pas une interface MLflow intégrée.

Les ports publiés sont limités à la boucle locale. Cette stack n’est pas une
installation multi-utilisateur exposable sur Internet : pas de TLS ni d’authentification
Loki/Prometheus. Ne pas élargir les bindings sans revoir ce modèle de sécurité.

## Chemin des données et jobs courts

```text
Pipeline hôte ── psutil existant ── rapport local + MLflow
       │
       ├─ agrégats JSON atomiques ── exporteur persistant ── Prometheus ── Grafana
       └─ événements JSONL ── Alloy, file persistante ── Loki ──────────── Grafana
```

Le septième service `signalflow-exporter` complète les six services demandés.
Il lit un snapshot partagé, sans monter le socket Docker. L’entraînement ne lance
aucun serveur HTTP et n’effectue aucune requête vers la stack. Un verrou interprocessus
borne l’attente à 250 ms ; une écriture temporaire, fsync puis remplacement atomique
publie les agrégats. Le snapshot reste présent après la fin du processus et les
redémarrages de la stack. Les démarrages et terminaisons sont publiés séparément.
Un processus tué brutalement peut donc rester compté comme démarré sans terminaison.

Le snapshot ne contient aucun identifiant de run. Sa taille et ses valeurs sont
validées ; un fichier corrompu déclenche un diagnostic, sans remise à zéro silencieuse.
Les histogrammes conservent des bornes fixes de 10 ms à 1 800 s et un bucket +Inf.
Un snapshot absent donne des compteurs nuls et `snapshot_available=0` ; illisible,
il donne `snapshot_read_success=0`. Les compteurs sont cumulatifs depuis la création
du fichier, pas depuis le démarrage du conteneur. Ne pas supprimer ce fichier pour
« nettoyer » un dashboard sans accepter une réinitialisation des compteurs.

Le format HTTP négocie Prometheus ou OpenMetrics avec le client officiel Python.
L’extra `observability` est requis pour lancer l’exporteur Python, pas pour écrire
les snapshots/logs depuis le pipeline. Son image contient la dépendance nécessaire.

## Configuration compatible

Les exemples classification et benchmark activent les publications. Une ancienne
configuration sans les nouvelles clés garde le comportement historique.

```yaml
schema_version: '0.0.1'
enabled: true
interval_seconds: 0.05
max_samples: 10000
directory: ./outputs/observability
prometheus:
  enabled: true
logging:
  enabled: true
  max_bytes: 10485760
  backups: 3
```

`enabled` contrôle la sonde locale ; les deux blocs contrôlent indépendamment les
publications. `tools.metrics.enabled: false` désactive l’ensemble et évite de lire
monitoring.yaml. Le benchmark exige la collecte locale active. Le JSON Schema
refuse les clés inconnues, types incorrects et bornes invalides. Les chemins YAML
sont relatifs au répertoire de travail, confinés à celui-ci ; traversées et liens
sortants produisent `ObservabilityConfigError`. Aucune commande n’est exécutée
à partir du YAML.

Le montage Compose lit **le dossier outputs/observability du dépôt**. Pour lancer
une wheel ou un exemple depuis un autre répertoire, définir explicitement :

```bash
export SIGNALFLOW_OBSERVABILITY_DIR=/chemin/absolu/SignalFlow/outputs/observability
```

Cette variable relève de l’opérateur et autorise une destination absolue. Le parent
d’un benchmark propage sa destination aux processus enfants. Changer seulement
`--output-dir` déplace les artefacts ML, pas les publications de la stack.

## Métriques et périmètres

| Séries (préfixe `signalflow_`) | Sens |
| --- | --- |
| `trainings_{started,succeeded,failed}_total` | Entraînements validés démarrés / terminés |
| `benchmarks_{started,succeeded,failed}_total` | Campagnes, distinctes de leurs entraînements |
| `train_duration_seconds`, `benchmark_duration_seconds` | Histogrammes de durée totale d’exécution instrumentée |
| `phase_duration_seconds{phase=…}` | Histogrammes des huit phases fixes |
| `process_cpu_seconds_total` | Somme des temps CPU des processus entraînés sur leurs fenêtres de collecte |
| `process_rss_sampled_max_bytes` | Maximum RSS observé du dernier entraînement terminé |
| `collection_complete`, `monitoring_enabled` | État de la dernière collecte locale |
| `last_publication_timestamp_seconds` | Date UTC de publication, utile pour détecter une donnée ancienne |
| `snapshot_available`, `snapshot_read_success` | Disponibilité et lisibilité de la source persistante |

Les histogrammes fournissent `_bucket`, `_sum`, `_count`. Une durée moyenne cumulée
est `_sum / _count` ; un débit utilise `rate(..._total[5m])`. Sur un job rare, les
compteurs et moyennes cumulées sont plus lisibles qu’un débit sur une courte fenêtre.
La jauge RSS ne représente pas tous les runs simultanément : les détails par run
restent dans MLflow/monitoring.json. L’identifiant n’est pas un label Prometheus.

La sonde psutil mesure le **processus Python**, threads inclus, enfants exclus.
Le maximum RSS échantillonné peut manquer un pic. Les durées de phases et le temps
CPU viennent des observations existantes, sans second collecteur. Les frontières
exactes sont décrites dans [observability.md](observability.md).

Node Exporter mesure le **Linux visible du moteur Docker**. Sous WSL2, ce n’est
pas l’hôte Windows. Avec Docker Desktop, il peut s’agir de sa VM Linux plutôt que
de la distribution utilisateur. cAdvisor mesure les **conteneurs**. Aucune métrique
GPU n’est inventée.

## Logs, reprise et diagnostics

Chaque ligne JSON contient date UTC, niveau, composant, phase, type d’événement,
execution_id, run_id éventuel et message fixe. Les étapes dataset, fit, évaluation,
sauvegarde et tracking ont un début et une fin ; un échec produit `stage_failed`
ou `train_failed`. La configuration chargée/validée est signalée après validation :
un YAML invalide donne une erreur CLI typée avant création d’une session de télémétrie.
Aucun contenu de dataset, paramètre secret ou texte brut d’exception n’est recopié.

Rotation : 10 Mio par fichier, trois sauvegardes, verrou partagé entre écrivains.
Alloy lit aussi les fichiers tournés. Son lecteur filelog conserve ses positions
sur disque et transmet au pipeline OTLP avec une file persistante de 10 000 événements,
fsync actif et reprise sans délai global d’abandon sur erreur transitoire. Il n’y a
pas de batch en mémoire placé avant cette file. Une saturation applique une
contre-pression au lecteur. Le label Loki est `service_name=signalflow` ; les
identifiants restent des attributs structurés, utilisables par `| json`.

Les composants Alloy `otelcol.receiver.filelog` et `otelcol.storage.file` sont
**public preview** dans la version épinglée. Leur reprise est testée sur cette
installation, mais doit être revalidée avant toute montée de version. Le pipeline
initial `loki.write` avec WAL expérimental n’a pas satisfait le test de reprise ;
il a été remplacé. La livraison est au moins une fois : des doublons sont possibles.
Une migration de lecteur peut relire les fichiers conservés. La rétention locale,
l’espace disque et les erreurs permanentes d’ingestion limitent la garantie : aucune
promesse de conservation infinie pendant une panne.

Une panne de publication locale apparaît sur stderr via
`SIGNALFLOW_OBSERVABILITY_WARNING` et dans `observability.warnings` du résultat CLI ;
elle n’annule pas l’entraînement. `publication_io_seconds` mesure le temps des
opérations de publication, pas tout leur effet sur le calcul. Une erreur réseau de
la stack n’affecte pas le processus ML. Une panne d’écriture locale peut faire
perdre une publication ; elle n’est pas silencieuse. Consulter aussi les logs Alloy
pour les rejets permanents et les métriques internes de sa file.

## Réseau, permissions et particularités WSL2

Les services utilisent le réseau dédié `signalflow-observability`, sauf Node Exporter
qui utilise le réseau et le PID de l’hôte pour observer ses interfaces. Il écoute
sur l’adresse de la passerelle Docker, `172.17.0.1:9100` par défaut. Si elle diffère,
configurer `NODE_EXPORTER_BIND_ADDRESS` dans `.env` (voir `.env.example`) et vérifier
la correspondance avec `host.docker.internal:host-gateway`. Un `localhost` interne
ne pointe jamais vers le processus hôte. Aucun port Loki/Alloy/cAdvisor/exporteur
SignalFlow n’est publié sur l’hôte.

Tous les services suppriment leurs capabilities et activent no-new-privileges.
Les racines sont en lecture seule, les stockages nécessaires utilisent des volumes.
Aucun `privileged: true`. cAdvisor a néanmoins accès aux sockets Docker/containerd
pour découvrir les conteneurs : **un montage de socket `:ro` ne limite pas les droits
de l’API**. Cela impose de faire confiance à l’image et constitue une limite de
sécurité locale à revoir pour un hébergement partagé.

Les montages hôte sont statiques en lecture seule ; `rslave` a échoué sur cette
racine WSL2 non partagée. Redémarrer l’exporteur pour découvrir de nouveaux montages.
Les collecteurs disque par conteneur sont désactivés : les parcours overlayfs
produisaient des erreurs de permissions/coût inutile. Les CPU, mémoire et réseau
par conteneur ainsi que les disques de l’hôte sont vérifiés. Docker rootless,
Docker Desktop, des chemins de sockets différents ou d’autres pilotes de stockage
peuvent nécessiter une adaptation ; ne pas conclure à partir du seul état « running ».

## Dashboards, alertes et exploitation

Trois dashboards, rafraîchis toutes les 15 secondes, et les datasources sont
provisionnés : Overview, Infrastructure &
Resources, Logs & Diagnostics. Le filtre execution_id permet de retrouver les logs
sans accroître la cardinalité des séries. La corrélation avec les métriques agrégées
se fait temporellement ; les détails exacts par run sont dans MLflow.

Prometheus évalue les alertes de cible absente, snapshot illisible, collecte
incomplète et nouvel échec d’entraînement. Grafana provisionne aussi une règle de
cible indisponible. Aucune destination de notification externe n’est configurée.
Les healthchecks utilisent les commandes réellement présentes ; Loki et Alloy
sont contrôlés par leurs endpoints `/ready` et `/-/ready` et les scrapes, sans
inventer de commande curl dans des images qui ne la possèdent pas.

```bash
docker compose ps
docker compose logs --tail=100 alloy loki
docker compose stop
docker compose up -d
```

Les volumes conservent Prometheus (15 jours / 2 Go), Loki (7 jours), Grafana et Alloy.
Sauvegarder également `outputs/observability` pour conserver compteurs et logs bruts.
Les limites mémoire cumulées ne représentent pas la consommation réelle. Ne pas
utiliser `down -v` lors d’un redémarrage normal : cela supprime les volumes persistants.

## Vérification reproductible

```bash
python -m pytest -q
mkdir -p outputs/observability-validation
signalflow train --config-dir examples/classification \
  --output-dir outputs/observability-validation/demo \
  > outputs/observability-validation/demo-report.json
python -m pytest --compose tests/compose -q
python scripts/measure_observability_overhead.py --repetitions 3
```

Les tests ordinaires n’exigent ni Docker ni Internet. Les tests `--compose` vérifient
les APIs et **arrêtent temporairement la stack dédiée**, puis la relancent même en
cas d’échec. Ne pas les lancer pendant une démonstration. Le script de surcoût refuse
d’écraser son dossier de résultats ; choisir `--output-dir` pour une autre campagne.
Voir [le rapport de validation](observability_validation.md) pour les résultats
réellement obtenus et les limites restantes.

Références officielles : [filelog Alloy](https://grafana.com/docs/alloy/latest/reference/components/otelcol/otelcol.receiver.filelog/),
[stockage persistant](https://grafana.com/docs/alloy/latest/reference/components/otelcol/otelcol.storage.file/),
[export OTLP HTTP](https://grafana.com/docs/alloy/latest/reference/components/otelcol/otelcol.exporter.otlphttp/),
[ingestion OTLP Loki](https://grafana.com/docs/loki/latest/send-data/otel/).
