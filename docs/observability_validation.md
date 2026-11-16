# Rapport de validation — observabilité SignalFlow

Ce rapport distingue les vérifications exécutées des fichiers simplement présents.
Les preuves locales sont conservées sous `outputs/observability-validation/`, ignoré
par Git, ainsi que `outputs/observability-overhead/measurements.json`.

## Audit et périmètre

Le pipeline scikit-learn, MLflow SQLite, le monitoring psutil et le benchmarking
existaient avant cette mission. La suite initiale a donné **227 tests réussis**.
L’arborescence, pyproject, le registre, le runner ML, les schémas monitoring et les
modifications locales ont été examinés avant l’intégration. Les sources nécessaires
ont été conservées. Les modèles, bases SQLite, wheels, caches, logs, sorties et
secrets restent exclus par `.gitignore` ; `.env.example` est une source partageable.

Lors de la reprise de session, le dépôt contenait déjà le commit `26bd91a`
d’intégration observabilité. Aucun commit ni publication n’a été créé par cette
exécution ; les modifications constatées, notamment les permissions Alloy et les
montages cAdvisor, ont été préservées puis vérifiées. Le diff final est disponible
pour revue, sans staging automatique.

## Composants livrés

| Emplacement | Responsabilité |
| --- | --- |
| `compose.yaml`, `.dockerignore`, `.env.example` | Stack locale, stockage, réseau, permissions et paramètres opérateur |
| `infrastructure/observability/prometheus/` | Scrapes et règles d’alerte |
| `infrastructure/observability/loki/` | Stockage local, ingestion et rétention |
| `infrastructure/observability/alloy/` | Lecture JSON, positions et file persistantes, export OTLP |
| `infrastructure/observability/grafana/` | Deux datasources, trois dashboards et règle d’alerte provisionnés |
| `infrastructure/observability/exporter/` | Image minimale de lecture des métriques persistantes |
| `src/signalflow/observability/{storage,metrics,logging,session,exporter}.py` | Persistance bornée, formats, tolérance aux erreurs et exposition HTTP |
| `src/signalflow/ml/{config,runner}.py`, `benchmarking/runner.py` | Branchement sur les observations existantes, propagation aux enfants |
| `pyproject.toml`, configurations et schémas monitoring | Extra optionnel et validation compatible |
| `tests/telemetry/`, `tests/compose/`, `tests/conftest.py` | Contrats hors réseau et intégration Docker opt-in |
| `scripts/{prepare_observability,generate_dashboards,measure_observability_overhead}.py` | Installation locale, dashboards reproductibles, mesure de coût |
| README, docs pipeline/tools/observability/demo et README des exemples | Fonctionnalités réelles, exploitation, limites et démonstration |

## Défauts détectés et corrections

- Un job court ne peut pas garantir un scrape avant sa terminaison : publication
  atomique d’agrégats persistants, lus par un service permanent.
- La corruption des nombres, buckets ou structures JSON ne doit pas générer une
  exposition invalide : validation bornée, diagnostic explicite, aucun reset caché.
- Les permissions issues d’un umask restrictif empêchaient potentiellement Alloy
  non-root de lire les événements : fichiers de publication lisibles par le lecteur,
  secret Grafana protégé par son répertoire parent et montage dédié.
- Le montage racine avec propagation `rslave` ne démarrait pas sur cette racine
  WSL2 : montage statique en lecture seule et limite de découverte documentée.
- Des montages `/run/user` produisaient des doublons de métriques filesystem :
  exclusion des pseudo-arbres non pertinents.
- Le suivi réseau devait porter sur l’hôte Linux, pas le namespace du conteneur :
  exception réseau hôte de Node Exporter, écoute limitée à la passerelle Docker.
- Le pilote Docker/containerd nécessitait les sockets et chemins adaptés pour
  cAdvisor ; les collecteurs disque par conteneur coûtaient inutilement et
  produisaient des erreurs. CPU, mémoire et réseau restent réellement collectés.
- La première stratégie Alloy avec WAL `loki.write` n’a pas rejoué les événements
  du test panne + redémarrage : remplacée par filelog et une file OTLP persistante.
  Les fichiers bruts étaient conservés ; aucun succès fictif n’a été retenu.
- L’API Loki `query_range` inclut les attributs structurés dans ses résultats :
  vérification des labels indexés via `/series`, sans confondre attribut et index.

## Tests et commandes exécutés

La suite finale hors Docker donne **252 tests réussis**, aucun ignoré, en
133,73 secondes : les 227 tests historiques et 25 tests de télémétrie. Elle couvre
validation YAML/chemins, concurrence, persistance, unités/OpenMetrics, corruption,
rotation JSON, désactivation, erreurs locales, nettoyage et compatibilité MLflow.
Les tests d’intégration Docker sont séparés : **8 réussis en 95,46 secondes**.
Ils vérifient les six targets, les séries hôte/conteneurs, la négociation OpenMetrics,
MLflow, Loki, les requêtes des dashboards, les endpoints et alertes, puis la reprise
après arrêt de Loki/redémarrage d’Alloy et après arrêt complet de la stack.
L’attente du premier scrape est bornée après redémarrage : « running » ne suffit pas.
Les logs des deux entraînements hors ligne ont été récupérés et les compteurs conservés.

Commandes de vérification :

```bash
.venv/bin/python -m pytest -q
docker compose config --quiet
docker compose up -d --build
.venv/bin/python -m pytest --compose tests/compose -q
python3 -m pip wheel . --no-deps --no-build-isolation --no-index \
  -w outputs/observability-validation/dist
```

La wheel finale a été construite puis installée hors réseau dans un virtualenv
vierge, sans accès aux site-packages du dépôt. `pip check`, l’import depuis
`site-packages`, l’aide CLI, un entraînement Iris depuis `/tmp`, le rechargement
joblib et la consultation MLflow ont réussi. Run isolé :
`59c12e7dba8d4fc4a55565f992581169`. L’installation respecte notamment SQLAlchemy
2.0.57, contrairement à l’environnement de travail préexistant qui contenait 2.1.8 ;
celui-ci n’a pas été modifié. Les wheels de SQLAlchemy, greenlet et prometheus-client
ont complété le cache local, puis l’installation a utilisé `--no-index`.

La démo Iris initiale a terminé avec le run MLflow
`0345a30dbfda4c3b94fb5c96bafb9850` et F1 macro de validation 0,933333.
Un cas volontaire `max_iter=1` a produit l’erreur typée `TRAINING_ERROR`, les
événements d’échec et le compteur correspondant. Il s’agit d’un scénario négatif
attendu, pas d’un entraînement présenté comme réussi.

La finalisation du benchmark est également présente dans Loki et Prometheus ;
les huit runs MLflow ont le statut FINISHED et les mêmes scores que le rapport.
Les dashboards ont été ouverts dans Chromium : données réelles visibles, événements
d’échec volontaire visibles, aucune erreur JavaScript ni panneau introuvable.
Le contrôle ciblé des requêtes a été rejoué après les ajustements de présentation :
**1 test réussi en 16,41 s**. Un essai simultané aux captures/rechargements de dashboards
avait dépassé le timeout de 10 s d’une requête API ; le passage isolé a réussi.
Cette latence ponctuelle n’est pas assimilée à une garantie de temps de réponse.
Le rafraîchissement des dashboards est réglé à 15 s pour limiter les requêtes
concurrentes ; les scrapes Prometheus restent à 5 s.
Le tableau d’alertes utilise une requête instantanée pour ne pas présenter des
alertes historiques comme encore actives. Les captures restent dans le dossier de preuves.

Les six targets Prometheus ont été interrogées, ainsi que des séries CPU, RAM,
disques et réseau hôte et CPU/mémoire/réseau des conteneurs. Le format OpenMetrics
est négocié réellement par HTTP, avec terminaison `# EOF`. Les deux datasources,
les trois dashboards et leurs requêtes ont été vérifiés par API. Les quatre règles
Prometheus ont l’état `health=ok` ; la règle Grafana `signalflow-target-down` est
présente via l’API d’administration. Loki `/ready` et Alloy `/-/ready` répondent 200
après disponibilité ; le démarrage Loki peut initialement renvoyer 503.

## Benchmark comparatif réel depuis la wheel

Campagne `51b04160498c48f2b87fca7c62748066`, statut **FINISHED**, huit runs :
un échauffement et trois répétitions par configuration. Dataset synthétique
120 000 × 120, mêmes partitions, seeds, versions et un thread ; C=1 (`baseline`)
contre C=0,05 (`regularized`). Observabilité complète active et stack démarrée.
Aucun test lourd concurrent pendant la campagne finale.

```bash
/tmp/signalflow-observability-wheel-r1o1z6zm/venv/bin/signalflow benchmark \
  --config-dir examples/benchmark \
  --output-dir outputs/observability-validation/benchmark-final
```

| Configuration | Entraînement (s) | Fit (s) | Pipeline (s) | Commande (s) | CPU (s) | RSS max (MiB) | Accuracy validation | F1 validation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 3.5206 ± 0.2483 | 0.8341 ± 0.0304 | 10.5280 ± 0.3591 | 12.0217 ± 0.3503 | 10.3167 ± 0.3365 | 700.7982 ± 1.1246 | 0.8971 ± 0.0000 | 0.8970 ± 0.0000 |
| regularized | 3.8680 ± 0.4343 | 0.7669 ± 0.0090 | 10.9163 ± 0.6990 | 12.4974 ± 0.8333 | 10.7067 ± 0.7006 | 701.2552 ± 0.4828 | 0.8970 ± 0.0000 | 0.8969 ± 0.0000 |

Les scores sont stables entre répétitions. La configuration régularisée présente
un fit moyen plus court, mais pas un coût global inférieur démontré ; son temps
commande moyen est même légèrement supérieur, avec dispersion. Aucun signal de
régression selon l’heuristique du benchmark, et **aucun vainqueur global déclaré**.
Les temps incluent des périmètres distincts : entraînement = prétraitement + fit,
pipeline = orchestration instrumentée, commande = processus entier. Le CPU est celui
du processus ; le RSS est le maximum échantillonné, non un pic garanti.

Une première campagne interrompue par terminaison du processus conservait un rapport
partiel RUNNING avec deux échauffements. Elle est exclue de ces statistiques ; ses
sorties sont conservées. Cela illustre la limite documentée des kills brutaux,
qui ne passent pas par le traitement Python des exceptions.

## Mesure du coût d’observabilité

Dataset synthétique : 120 000 lignes, 120 caractéristiques, trois classes ; mêmes
seeds, partitions, C=1 et un thread numérique. Processus neufs, un échauffement et
**trois répétitions mesurées par mode**, ordre mélangé reproductible. Stack active
pour tous les modes. La mesure inclut interpréteur, imports, MLflow et terminaison.
Les douze exécutions ont donné les mêmes métriques ML. Cette campagne a utilisé
l’environnement de travail préexistant (SQLAlchemy 2.1.8), avant le dernier
renforcement défensif de validation des structures du snapshot. Le benchmark
comparatif et la validation de wheel utilisent, eux, SQLAlchemy 2.0.57 conformément
à pyproject ; les deux campagnes ne sont pas assimilées à une même mesure.

| Mode | Durée commande moyenne ± écart-type (s) | Médiane (s) |
| --- | ---: | ---: |
| Désactivé | 17,623 ± 4,882 | 15,685 |
| Monitoring psutil seul | 17,232 ± 3,741 | 19,251 |
| Monitoring + métriques + logs | 15,362 ± 2,168 | 16,560 |

Le temps cumulé des opérations de publication du mode complet est
**23,700 ± 4,009 ms** (trois répétitions). Il ne mesure pas toute la perturbation
possible du GIL, des allocations ou du stockage. La différence de moyenne globale
est inférieure à la dispersion observée : **aucun gain ni surcoût global fiable
ne peut être conclu** avec cette campagne. La machine WSL2 n’est pas dédiée,
fréquences CPU et charge extérieure ne sont pas contrôlées. Les opérations légères
de préparation de la distribution faisaient aussi partie de l’environnement de
travail ; ces résultats décrivent une démonstration locale, pas un laboratoire isolé.

## Limites et suites recommandées

Aucun défaut bloquant n’est resté ouvert dans le périmètre Linux WSL2 vérifié.
Les restrictions suivantes restent explicites ; elles ne sont pas assimilées à une
validation de production ou à une garantie sur toute installation Docker.

- Les composants Alloy filelog/storage sont public preview dans la version épinglée.
  Conserver les tests de reprise et les rejouer avant toute mise à jour.
- Reprise au moins une fois, doublons possibles ; rétention locale et capacité disque
  bornent les pannes absorbables. Une erreur permanente d’ingestion peut rejeter des
  événements : surveiller les diagnostics Alloy.
- Un kill brutal peut laisser un compteur démarré sans terminaison. Une erreur
  d’écriture locale est signalée mais ne peut garantir la publication manquante.
- Les métriques Prometheus sont agrégées, sans identifiants de run ; utiliser MLflow
  pour le détail et les scores. RSS échantillonné, enfants non mesurés par psutil.
- Linux visible du moteur Docker, pas Windows ; compatibilité Docker Desktop/rootless
  et autres pilotes non démontrée sur cette installation.
- Accès cAdvisor aux sockets du moteur : `:ro` ne retire pas les privilèges d’API.
  Les services locaux ne constituent pas une plateforme partagée durcie.
- Alertes provisionnées, sans destinataire de notification externe configuré.
- Pas de PyTorch, GPU, tuning automatique, Kubernetes ou orchestration distribuée.

Avant extension : relancer le scénario depuis un clone vierge sur la machine de
présentation, conserver les versions validées, puis augmenter le nombre de répétitions
sur un hôte peu chargé pour estimer un faible surcoût. Ne pas convertir les résultats
actuels en promesse de gain de performance.

## Versions de la stack validée

Prometheus 3.16.0, Grafana 13.3.1, Loki 3.7.10, Alloy 1.22.0,
Node Exporter 1.12.1, cAdvisor 0.60.6 ; image exporteur Python 3.13.12-slim,
client Prometheus 0.26.0. Installation Python isolée : scikit-learn 1.9.1,
NumPy 2.5.4, MLflow skinny 3.18.0, SQLAlchemy 2.0.57 et psutil 7.2.2.
Docker Engine 29.5.3, cgroup v2, pilote overlayfs
sur Linux WSL2. Cette liste décrit la machine testée, pas toutes les installations Docker.

## Inventaire Git à la remise

Par rapport à l’état présent lors de la reprise, fichiers modifiés :

- `README.md`
- `compose.yaml`
- `docs/demo_entretien.md`
- `docs/observability.md`
- `docs/pipeline.md`
- `docs/tools.md`
- `examples/benchmark/README.md`
- `examples/classification/README.md`
- `infrastructure/observability/alloy/config.alloy`
- `infrastructure/observability/grafana/dashboards/signalflow-logs.json`
- `infrastructure/observability/grafana/dashboards/signalflow-overview.json`
- `infrastructure/observability/grafana/dashboards/signalflow-resources.json`
- `scripts/generate_dashboards.py`
- `scripts/prepare_observability.py`
- `src/signalflow/observability/exporter.py`
- `src/signalflow/observability/logging.py`
- `src/signalflow/observability/metrics.py`
- `src/signalflow/observability/session.py`
- `tests/telemetry/test_pipeline.py`
- `tests/telemetry/test_publication.py`

Nouveaux fichiers non suivis (à examiner avant ajout volontaire à Git) :

- `.env.example`
- `docs/observability_stack.md`
- `docs/observability_validation.md`
- `scripts/measure_observability_overhead.py`
- `tests/compose/test_recovery.py`
- `tests/compose/test_stack.py`
- `tests/conftest.py`
