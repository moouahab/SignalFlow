# Benchmark de référence — 14 novembre 2026

## Protocole réellement exécuté

Campagne exécutée avec la wheel stabilisée dans un virtualenv vierge, hors du dépôt.
Le dataset synthétique de `examples/benchmark` contient 120 000 lignes × 120
caractéristiques (50 informatives, 20 redondantes), 3 classes, seed de génération 123.
La seed de séparation vaut 42 ; les partitions stratifiées sont 60/20/20.
Deux régressions logistiques : C=1 (`baseline`) et C=0,05 (`regularized`),
`max_iter=1000`. Un échauffement puis trois répétitions mesurées par configuration.
Huit processus neufs, séquentiels, ordre mélangé avec seed=42, BLAS/OpenMP limités
à un thread. Monitoring demandé toutes les 50 ms, jusqu’à 10 000 points conservés.

```bash
signalflow benchmark --config-dir examples/benchmark --output-dir results/reference
```

Les huit runs sont FINISHED, avec collecte complète. Les empreintes de données
et partitions ont été contrôlées par le benchmark. Les statistiques ont ensuite
été recalculées depuis les six runs mesurés et confrontées au rapport. Le client
MLflow a confirmé les huit statuts, les métriques et les huit artefacts par run.

## Résultats

Moyenne ± écart-type d’échantillon (ddof=1), trois répétitions par colonne.
Les métriques de classification sont des fractions entre 0 et 1.

| Mesure | C=1 | C=0,05 |
| --- | ---: | ---: |
| Commande complète (s) | 12.540 ± 0.751 | 12.520 ± 0.872 |
| Pipeline (s) | 10.896 ± 0.669 | 10.890 ± 0.810 |
| Entraînement : prétraitement + fit (s) | 3.551 ± 0.399 | 3.308 ± 0.182 |
| Fit seul (s) | 0.864 ± 0.027 | 0.787 ± 0.027 |
| Temps CPU (s) | 10.683 ± 0.662 | 10.690 ± 0.811 |
| RSS maximal observé (Mio) | 732.408 ± 0.646 | 732.294 ± 0.849 |
| Accuracy validation | 0.897125 ± 0.000000 | 0.897000 ± 0.000000 |
| F1 macro validation | 0.897023 ± 0.000000 | 0.896899 ± 0.000000 |
| Accuracy test | 0.901583 ± 0.000000 | 0.901500 ± 0.000000 |
| F1 macro test | 0.901504 ± 0.000000 | 0.901423 ± 0.000000 |

## Observations brutes hors échauffements

| Configuration | Bloc | Commande (s) | Entraînement (s) | CPU (s) | RSS max (Mio) |
| --- | ---: | ---: | ---: | ---: | ---: |
| regularized | 1 | 11.602038 | 3.106728 | 9.860000 | 731.574219 |
| baseline | 1 | 13.042633 | 3.897752 | 11.090000 | 732.160156 |
| baseline | 2 | 12.900668 | 3.639947 | 11.040000 | 733.140625 |
| regularized | 2 | 12.622508 | 3.357470 | 10.730000 | 733.230469 |
| regularized | 3 | 13.336887 | 3.460228 | 11.480000 | 732.078125 |
| baseline | 3 | 11.677429 | 3.115019 | 9.920000 | 731.921875 |

## Interprétation

C=0,05 présente une moyenne d’entraînement inférieure dans cette campagne, mais
la différence est modeste par rapport à la dispersion observée. Le coût complet
de commande et le temps CPU sont pratiquement identiques en moyenne. Les scores
de validation diffèrent d’environ 0,000125 en accuracy (0,0125 point de pourcentage).
Ces observations ne démontrent aucun gain global ni supériorité générale.
Ne pas sélectionner les hyperparamètres à partir des scores de test.

Aucun signal de régression potentielle n’est déclenché par la règle configurée
(hausse >15 % et supérieure à deux fois le plus grand écart-type). L’absence de
signal n’est ni une preuve d’équivalence, ni un test statistique de supériorité.

## Environnement et traçabilité

- Python : `3.13.12`.
- Plateforme : `Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.43`.
- CPU visible : `AMD Ryzen 5 5600H with Radeon Graphics` ; 4 CPU logiques, 2 cœurs physiques déclarés, affinité 4.
- RAM système visible : 3.82 Gio ; ce total ne mesure pas un quota de conteneur.
- psutil : `7.2.2`.
- Empreinte des sources Python : `aff733dba99376c4bb80ddfa3d642ad37d59254a5c71ba61caef5a60b6763c57`.
- Empreinte du dataset : `4d04d93e3a5491220cd335f2a97d45c3441cd25f36e8e5f5c2c126dcefa05ad4`.
- Empreinte des partitions : `0b4387bb96fe6694cab27775eef275fb92afddb95ceaf176d9e49474a0f30d9c`.

| Dépendance | Version |
| --- | --- |
| scikit-learn | 1.9.1 |
| numpy | 2.5.4 |
| scipy | 1.18.1 |
| joblib | 1.6.0 |
| mlflow-skinny | 3.18.0 |
| SQLAlchemy | 2.1.8 |
| alembic | 1.20.1 |
| PyYAML | 6.0.3 |
| jsonschema | 4.26.0 |
| threadpoolctl | 3.8.0 |

## Limites des mesures

- La commande inclut le démarrage Python et la publication finale de télémétrie. Le pipeline les exclut ; le fit exclut le prétraitement.
- Le temps CPU est celui du processus et de ses threads, collecteur compris. Les enfants sont exclus. Ce n’est pas le taux d’occupation CPU global de la machine.
- Le RSS inclut les pages partagées et le runtime ML. Le maximum échantillonné est une borne inférieure du vrai pic ; les pics entre deux lectures peuvent être manqués.
- Les trois répétitions permettent de décrire la dispersion, pas d’établir un intervalle de confiance fiable ni une garantie de gain.
- La seed fixe les données et les partitions, pas la durée. Les processus neufs ne purgent pas les caches OS ; fréquence CPU, affinité et activité de l’hôte ne sont pas maîtrisées.
- La suite pytest était terminée avant cette campagne. Une consultation des artefacts Iris a eu lieu pendant la campagne ; cette activité locale et toute autre contention de l’hôte peuvent contribuer à la dispersion.
- Les données synthétiques rendent le fit mesurable, mais ne représentent pas une charge de production ou des données Everdian.
- Les échauffements sont exclus. Les scores identiques entre répétitions reflètent les mêmes seeds, pas une estimation de l’incertitude de généralisation.

## Preuves locales et reproduction

Le rapport intégral, les identifiants, les moyennes, médianes, écarts-types, min/max
et coefficients de variation sont dans `outputs/stabilisation/reference.json`.
Les contrôles sont dans `outputs/stabilisation/benchmark-verification.json`.
Les artefacts sont aussi archivés sous `outputs/stabilisation/installed-results/`.
Les rapports/SQLite archivés conservent les chemins absolus de l’exécution temporaire :
ce sont des preuves locales, pas une archive MLflow relocalisable automatiquement.
Les sources et configurations de l’exemple permettent une nouvelle exécution.

Ces fichiers générés sont ignorés par Git. Ce document conserve les mesures
commentées ; recalculer une référence sur la machine de démonstration et vérifier
les conditions avant toute comparaison historique. Utiliser Iris pour le direct
et préparer le grand benchmark avant les dix minutes de présentation.
