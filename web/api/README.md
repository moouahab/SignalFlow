# API locale SignalFlow — phase 3

Ce projet Python indépendant fournit une API de **validation**, sans entraînement
HTTP, base de données ni file de jobs. Il dépend du package moteur SignalFlow et
ne nécessite pas les extras scikit-learn/MLflow pour valider une configuration.

Depuis la racine du dépôt, dans un environnement Python ≥ 3.11 :

```bash
python -m pip install -e .
python -m pip install -e 'web/api[test]'
signalflow-api --port 8000
```

Écoute : **127.0.0.1 uniquement**. Documentation interactive :
http://127.0.0.1:8000/docs ; contrat : http://127.0.0.1:8000/openapi.json.

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/training/schema
curl http://127.0.0.1:8000/api/v1/algorithms
curl -X POST http://127.0.0.1:8000/api/v1/training/validate \
  -H 'Content-Type: application/json' --data-binary @web/api/examples/iris.json
python -m pytest web/api/tests -q
```

Une validation réussie renvoie `{"valid":true,"configuration":{...}}`, contenant
la spécification validée. Elle ne charge pas le dataset et ne crée pas d'expérience.
Le même document envoyé à `POST /api/v1/trainings` renvoie **501**
(`TRAINING_EXECUTION_NOT_IMPLEMENTED`), après validation. Aucun job n'est créé.

Les configurations CSV, destinations de fichiers et exports actifs sont refusés
dans ce contrat HTTP. Les interfaces Python/CLI conservent leurs fonctionnalités.

Cette API est **anonyme et locale**, pas prête pour Internet. Aucun CORS universel,
aucun fournisseur d'identité, aucun stockage utilisateur. Le mode opérateur
`Settings(require_authentication=True)` ferme les routes métier avec 503 jusqu'à
l'intégration d'un véritable fournisseur d'identité. Les jetons Authorization
ne sont pas acceptés comme preuves d'identité dans le mode local.

Architecture : `src/signalflow_api/{app,settings,middleware,errors,dependencies}.py`,
`routes/`, `schemas/`, `services/`. Rapport, contrats détaillés et limites :
[docs/phase3_fastapi.md](../../docs/phase3_fastapi.md).
