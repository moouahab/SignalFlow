# Baseline CPU/RAM locale

Installer les extras `ml,monitoring`, puis depuis la racine du dépôt :

```bash
signalflow benchmark --config-dir examples/benchmark --output-dir outputs/baseline
```

120 000 lignes synthétiques × 120 caractéristiques, 3 classes, sans réseau.
Deux configurations C=1 et C=0.05, une répétition d'échauffement puis trois mesurées
par configuration. Les huit runs utilisent des processus neufs et les mêmes données
et partitions. Le dossier de configuration peut être copié hors du dépôt.

Les rapports `benchmark.json` et `benchmark.md` sont sous
`outputs/baseline/benchmarks/<uuid>/`, avec les runs et leur suivi MLflow local.
Le RSS maximal échantillonné n'est pas un pic mémoire garanti. Les signaux de
régression sont heuristiques ; aucune optimisation n'est appliquée automatiquement.

Voir [la méthode et toutes les limites](../../docs/observability.md).

## Lecture et reproduction

Le tableau Markdown présente moyenne et écart-type de l'entraînement (prétraitement
et fit), du fit seul, du pipeline, de la commande complète, du temps CPU, du RSS,
de l'accuracy et du F1 de validation. Le JSON conserve aussi les valeurs brutes,
les résultats sur test, les conditions et les statistiques détaillées.

```bash
signalflow benchmark --config-dir examples/benchmark --output-dir outputs/new \
  --baseline /chemin/vers/benchmark.json
```

Les conditions doivent être compatibles ; une autre version de Python, des
bibliothèques ou un autre matériel peuvent empêcher la comparaison historique.
Ne pas lancer d'autres tâches lourdes volontairement pendant les mesures. Le
protocole ne contrôle toutefois pas toute la charge de l'hôte.

Préparer cette campagne avant l'entretien. Pour le direct, garder
[l'exemple Iris](../classification/README.md). Les mesures réellement obtenues et
leur interprétation sont dans [la référence](../../docs/benchmark_reference.md).

## Publication vers la stack locale

Cet exemple active le monitoring psutil, les métriques persistantes et les logs JSON.
Depuis la racine du dépôt, exécuter `python scripts/prepare_observability.py` puis
`make observability-up` pour les visualiser dans Grafana. L’entraînement
fonctionne aussi avec la stack arrêtée ; les publications restent sur disque.
Le dossier partagé est `outputs/observability`, indépendamment de `--output-dir`.
Hors dépôt, définir `SIGNALFLOW_OBSERVABILITY_DIR` vers ce dossier absolu.
Pour conserver seulement les mesures locales, désactiver `prometheus.enabled` et
`logging.enabled` dans monitoring.yaml. Voir [le guide d’exploitation](../../docs/observability_stack.md).
