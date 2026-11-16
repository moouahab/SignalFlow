# Rapport de stabilisation — 14 novembre 2026

## Périmètre et état Git

La mission stabilise les fonctionnalités locales existantes, sans ajout de moteur
ML, interface web, service distant ou orchestrateur. Les interfaces publiques et
les configurations des exemples sont conservées. Aucun commit, ajout à l'index ou
push n'a été effectué.

L'[audit Git](git_audit.md) donne la liste complète des sources préexistantes non
suivies et des fichiers touchés. Les changements précédents sont préservés ; seul
un espace de fin de ligne sur une ligne vide de `config/project.yaml` a été nettoyé.
Les sources nouvelles restent à intégrer à Git après revue explicite.

## Problèmes détectés et corrections

| Constat | Correction |
| --- | --- |
| README indiquant encore une absence d'observabilité | Fonctionnalités locales, architecture, installation, MLflow, limites et feuille de route documentés |
| Bases SQLite et wheels potentiellement visibles hors des répertoires ignorés | Règles `.gitignore` spécifiques, avec fichiers annexes SQLite |
| Création du dossier de benchmark hors du traitement d'erreurs métier | `BenchmarkError` explicite au lieu d'un traceback OS brut |
| Écriture du rapport final hors du traitement d'erreurs | Erreur typée et tentative de conservation d'un rapport FAILED |
| Durée complète d'entraînement absente des agrégats du benchmark | `training_duration_seconds` conservé et agrégé, sans modifier la mesure existante |
| Rapport lisible incomplet | CPU, accuracy, durée d'entraînement et comparaison historique exposés dans le Markdown |
| Échec du démarrage du thread remontant en erreur générique | `MonitoringError` explicite et état du collecteur laissé non démarré |
| Docstrings évoquant seulement deux adaptateurs ou l'entraînement seul | Registre et CLI décrivent aussi monitoring et benchmark |
| Absence de support d'entretien et de référence commentée | Guide de dix minutes, tableau des fonctions et rapport de benchmark |

Quatre tests de régression ont été ajoutés. Le test d'intégration du benchmark
vérifie aussi la présence d'une durée d'entraînement positive dans les agrégats.

## Revue du code prioritaire

- `ml/runner.py` : orchestration séparée des adaptateurs, arrêt du collecteur et
  tentative de statut FAILED en cas d'erreur ; conservation de l'erreur initiale
  si la publication échoue. Aucun refactoring nécessaire pour cette livraison.
- `ml/adapters/sklearn.py` : prétraitement appris sur train, prédiction sans refit,
  pipeline complet sauvegardé et non-convergence explicite. Contrats conservés.
- `ml/adapters/mlflow.py` : client local sans session globale, URI SQLite imposée,
  emplacement d'artefacts vérifié, erreurs métier. Aucun serveur lancé.
- `monitoring/local.py` : thread joint à l'arrêt, échantillons bornés, agrégats
  maintenus après saturation, panne de sonde signalée comme collecte incomplète.
- `benchmarking/runner.py` : sous-processus neufs, timeout, contrôle des empreintes,
  ordre reproductible et conservation des rapports partiels. Erreurs d'écriture corrigées.
- `tools/registry.py` : catalogue par instance, factories Python de confiance,
  validation avant initialisation et erreurs nettoyées. Pas d'import piloté par YAML.
- `cli.py` : aide sans SDK ML, JSON sur stdout, diagnostics sur stderr,
  codes 0/1/130, argparse pour les erreurs de syntaxe.

Les fonctions publiques de ces modules sont annotées et documentées. Les charges
JSON restent des `dict[str, Any]` validés par schéma : aucun grand changement de
modèle de types n'a été introduit uniquement pour le style. La gestion des chemins
et les tests de confinement existants sont conservés.

## Tests automatisés

Commande : `.venv/bin/python -m pytest -q`.

| État | Résultat | Temps |
| --- | --- | --- |
| Avant correction | 223 réussis, 0 ignoré, 1 avertissement | 110,36 s |
| Tests ciblés après correction | 22 réussis | 3,65 s |
| Suite complète après correction | 227 réussis, 0 ignoré, 1 avertissement | 127,38 s |

L'avertissement est `SADeprecationWarning` : MLflow utilise la stratégie SQLAlchemy
`noload`, dépréciée depuis SQLAlchemy 2.1. Il provient d'une dépendance et n'a pas
été masqué. Aucun échec fonctionnel observé dans cette validation.

Les journaux complets sont conservés localement dans
`outputs/stabilisation/tests-before.txt` et `tests-after.txt`.

## Construction et installation isolée

Construction avec les outils disponibles du Python système, sans télécharger le
backend de build :

```bash
python3 -m pip wheel . --no-deps --no-build-isolation --no-index \
  -w outputs/stabilisation/dist
```

Wheel produite : `signalflow-1.0.0-py3-none-any.whl`.
SHA-256 : `d41cd5037a77cee3d7062be4153e3afa3d49d7c8149c398ffd7fdf922dd1b8af`.
Les 36 entrées de l'archive ne contiennent que le package et ses métadonnées :
aucune configuration utilisateur, base, modèle ou donnée d'exemple embarquée.

Un virtualenv vierge a été créé sous `/tmp`, sans accès aux site-packages système.
Les dépendances exactes du virtualenv de travail ont été téléchargées, puis
installées avec la wheel via `--no-index --find-links` dans cet environnement neuf.
Il ne s'agit pas d'un environnement partageant les dépendances par `PYTHONPATH`.
Le gel inclut aussi les dépendances de développement déjà présentes, dont pandas ;
cela ne les rend pas obligatoires dans le contrat du package.

```bash
python -m pip install --no-index --find-links /chemin/wheelhouse \
  -r /chemin/dependencies.txt '/chemin/signalflow-1.0.0-py3-none-any.whl[ml,monitoring]'
python -m pip check
signalflow --help
```

Résultat de `pip check` : `No broken requirements found.` L'import a été vérifié
sous `venv/lib/python3.13/site-packages/signalflow/__init__.py`. Les commandes ont
été lancées hors du dépôt, avec les configurations copiées, sans `PYTHONPATH`
externe et avec `PYTHONNOUSERSITE=1`. Le benchmark définit lui-même le chemin du
package installé pour ses enfants, conformément à son contrat.

## Commandes fonctionnelles vérifiées hors dépôt

Dans l'environnement isolé, les chemins ci-dessous désignent les copies autonomes :

```bash
signalflow train --config-dir examples/classification --output-dir results/demo
python -m signalflow train --config-dir examples/classification \
  --training ./training-regularized.yaml --output-dir results/demo
signalflow train --config-dir examples/monitored-iris --output-dir results/monitored
signalflow benchmark --config-dir examples/benchmark --output-dir results/reference
```

`examples/monitored-iris` est une copie de l'exemple benchmark dans le dossier
**temporaire de validation**, dont seul le dataset a été remplacé par celui d'Iris.
Ce n'est pas un dossier supplémentaire du dépôt.

Les deux premiers entraînements ont le même dataset et les mêmes indices.
Les modèles rechargés reproduisent accuracy et F1 sur validation et test.
Le client MLflow confirme FINISHED, paramètres/métriques et sept artefacts chacun.
Le run instrumenté conserve les mêmes résultats et un huitième artefact
`monitoring.json`, des échantillons CPU/RSS et une collecte complète tracée dans MLflow.

| Iris | Accuracy validation | F1 macro validation | Accuracy test | F1 macro test |
| --- | ---: | ---: | ---: | ---: |
| C=1 | 0,933333 | 0,933333 | 0,933333 | 0,933333 |
| C=0,05 | 0,866667 | 0,865320 | 0,833333 | 0,829497 |

Le guide d'entretien contient les commandes de rechargement et de consultation
MLflow. Les contrôles exécutés les complètent par des assertions sur les métriques,
les artefacts et les partitions. Le protocole et les résultats du benchmark sont
dans [la référence mesurée](benchmark_reference.md).

## Limites et points non résolus

- L'avertissement MLflow/SQLAlchemy demeure ; suivre sa correction en amont avant
  d'adopter d'autres versions. Aucun contournement silencieux ajouté.
- Validation réalisée sur Python 3.13/Linux WSL2. Python >=3.11 est déclaré, mais
  aucune matrice multi-versions ou multi-OS n'a été exécutée ici.
- Le monitoring ne mesure pas les enfants, les quotas cgroup ou la VRAM. Le maximum
  RSS échantillonné peut manquer des pics ; la charge de l'hôte n'est pas isolée.
- Trois répétitions mesurées par configuration fournissent une première référence,
  pas une preuve statistique ni une charge de production représentative.
- Une panne disque/MLflow peut empêcher la sauvegarde d'un rapport d'échec.
- Le registre n'assure pas de rollback générique des factories ; les intégrations
  actuelles sont locales, sans lifecycle de services distribués.
- Le pipeline reste fixe et séquentiel, avec modèle unique et données numériques.
  Pas de serving, registre de modèles, tuning, GPU ou Kubernetes.
- Les nouveaux modules restent non suivis jusqu'à leur intégration Git autorisée.

## Prochaines étapes recommandées

1. Relire l'inventaire Git et intégrer uniquement sources, tests, exemples et docs.
2. Répéter la démonstration de dix minutes avec le benchmark déjà calculé.
3. Ajouter ultérieurement une validation CI sur les versions Python supportées.
4. Élargir la référence à des données représentatives et davantage de répétitions
   avant toute conclusion d'optimisation ou tout ajout de moteur GPU.

## Contrôles finaux et preuves conservées

- `git diff --check` : aucune erreur ; index Git inchangé.
- Liens locaux du README, des documents et des README d'exemples : tous résolus.
- Sources Python contenues dans la wheel comparées octet par octet aux fichiers
  finaux de `src/signalflow` : identiques.
- Huit runs du benchmark vérifiés dans MLflow, huit collectes complètes et huit
  artefacts par run. Moyennes et écarts-types recalculés depuis les six runs mesurés.
- Aucun incident fonctionnel bloquant restant observé dans les parcours validés.

Les preuves locales sont regroupées dans `outputs/stabilisation/` : journaux des
tests, build et installation ; gel des dépendances ; rapports Iris ; rapport brut
`reference.json` ; contrôles `artifacts-verification.json` et
`benchmark-verification.json` ; scripts de vérification et copie des résultats
sous `installed-results/`. La wheel est dans `dist/` à l'intérieur de ce dossier.
Ces éléments sont ignorés par Git. Les rapports et bases archivés gardent leurs
chemins d'origine temporaires ; relancer les commandes documentées pour produire
un nouveau suivi MLflow durable sous `outputs/demo` sur la machine de présentation.
