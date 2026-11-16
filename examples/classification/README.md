# Classification locale avec suivi MLflow

Depuis la racine du dépôt, après installation de `.[ml]` :

```bash
signalflow train --config-dir examples/classification --output-dir outputs/demo
signalflow train --config-dir examples/classification \
  --training ./training-regularized.yaml --output-dir outputs/demo
```

Les deux runs utilisent Iris, seed=42, et un découpage stratifié 60/20/20.
Ils diffèrent par C : 1.0 contre 0.05. Comparer les métriques de validation.

Le dossier est autonome : il peut être copié hors du dépôt et passé comme
`--config-dir` absolu. Le modèle et le suivi SQLite sont créés sous `--output-dir`.

Voir [la documentation du pipeline](../../docs/pipeline.md) pour le CSV local,
les artefacts, les validations, l'API Python et les limites.

## Démonstration légère

Cet exemple n'active pas le monitoring : `.[ml]` suffit. Il est destiné à la
vérification fonctionnelle et à l'entraînement en direct en entretien. Iris est trop
court pour conclure sur les performances CPU/RAM. Utiliser
[l'exemple benchmark](../benchmark/README.md) pour ces mesures.

La sortie standard contient un rapport JSON ; les diagnostics MLflow vont sur
stderr. Le modèle complet est dans le chemin `model` du rapport. Ne recharger que
les fichiers joblib produits par une source de confiance. Consulter le
[guide de démonstration](../../docs/demo_entretien.md) pour les commandes de
rechargement et de consultation du suivi MLflow.

## Publication vers la stack locale

Cet exemple active le monitoring psutil, les métriques persistantes et les logs JSON.
Depuis la racine du dépôt, exécuter `python scripts/prepare_observability.py` puis
`docker compose up -d --build` pour les visualiser dans Grafana. L’entraînement
fonctionne aussi avec la stack arrêtée ; les publications restent sur disque.
Le dossier partagé est `outputs/observability`, indépendamment de `--output-dir`.
Hors dépôt, définir `SIGNALFLOW_OBSERVABILITY_DIR` vers ce dossier absolu.
Pour conserver seulement les mesures locales, désactiver `prometheus.enabled` et
`logging.enabled` dans monitoring.yaml. Voir [le guide d’exploitation](../../docs/observability_stack.md).
