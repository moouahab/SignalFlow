# Ollama dans SignalFlow / MLflow

Cette intégration ajoute quatre capacités autour d'une instance Ollama **déjà accessible**
(URL distante configurable) :

1. `llm=ollama` comme fournisseur du registre d'outils ;
2. un appel Ollama tracé dans MLflow (run + métriques + artefact JSON + trace native) ;
3. un modèle Ollama au format **MLflow pyfunc**, versionné dans le Model Registry ;
4. les services `ollama` et `mlflow` (UI + API) dans `compose.yaml`.

## 1. Configuration

`config/tools.yaml` sélectionne le fournisseur ; `config/llm.yaml` contient les
paramètres métier. Aucun secret n'est écrit dans ces fichiers.

```yaml
# config/tools.yaml
llm:
  provider: ollama
  enabled: true          # false pour le pipeline d'entraînement classique
  config: ./llm.yaml
```

```yaml
# config/llm.yaml
ollama:
  base_url: "http://ollama.mon-domaine:11434"   # ou http://127.0.0.1:11434
  model: "llama3.2:3b"
  timeout_seconds: 60
  max_response_bytes: 1048576
  options: {temperature: 0.2, top_p: 0.9, num_predict: 512}
  # api_key_env: OLLAMA_API_KEY   # nom de variable uniquement, si un proxy l'exige

tracking:
  experiment_name: "ollama-chat"
  run_name: "ollama-run"
  # tracking_uri: "http://127.0.0.1:5000"       # serveur MLflow du compose
  native_traces: true
  log_prompts: true
  registered_model_name: "ollama_llama32_3b"
```

`base_url` accepte uniquement `http(s)://hôte[/préfixe]`, sans identifiants ni
query string. `tracking_uri` accepte `sqlite://`, `file://` et `http(s)://`.
Cette URI est une décision explicite de l'opérateur dans `llm.yaml` : contrairement
au pipeline d'entraînement, `tools.yaml` ne peut toujours pas choisir une URI réseau.

Mettre `log_prompts: false` pour masquer prompts et réponses dans les artefacts et
les traces (seules les métriques restent lisibles).

## 2. Utilisation

```bash
# dépendances : pip install -e ".[ml,llm]"
python -m signalflow ollama --config-dir ./config --health
python -m signalflow ollama --config-dir ./config --models

python -m signalflow ollama --config-dir ./config \
  --output-dir ./outputs/ollama --chat "Explique le bridage d'un signal" \
  --system "Réponds en français."

python -m signalflow ollama --config-dir ./config \
  --output-dir ./outputs/ollama --generate "Donne trois idées de features..."
```

Sans CLI :

```python
from signalflow.llm import run_ollama_chat

report = run_ollama_chat("./config", "./outputs/ollama", "Bonjour", register=False)
print(report["run_id"], report["trace_ids"], report["response"])
```

Chaque appel crée `outputs/ollama/runs/<uuid>/` avec `traces/1/call.json` et
`report.json`, puis un run MLflow avec :

- paramètres : `provider`, `model`, `endpoint`, `option.*` ;
- mesures : `ollama.latency_seconds`, `ollama.prompt_tokens`,
  `ollama.completion_tokens`, `ollama.tokens_per_second` ;
- artefacts : `traces/<n>/call.json` et `ollama/report.json` ;
- traces natives MLflow 3 liées au run (`ollama.chat` / `ollama.generate`).

`native_traces: true` aligne temporairement l'URI globale MLflow sur celle du suivi,
vidage des traces inclus, puis la restaure sous verrou. Mettre `false` pour conserver
uniquement les runs et artefacts, sans aucune modification de l'état global MLflow.

## 3. Modèle pyfunc et Model Registry

Avec `registered_model_name` renseigné, l'enregistreur :

1. sauvegarde `mlflow.pyfunc.save_model` dans `runs/<uuid>/model/` ;
2. copie ce dossier dans les artefacts du run (`artifact_path=model`) ;
3. crée/réutilise le modèle enregistré et publie une version.

```python
import mlflow.pyfunc

model = mlflow.pyfunc.load_model("outputs/ollama/runs/<uuid>/model")
print(model.predict(["Bonjour", "Au revoir"]))
```

Le prédicteur interroge l'instance Ollama configurée à l'inférence : le modèle
MLflow ne contient pas de poids, seulement la configuration et l'adaptateur.
Un serveur MLflow externe peut servir le modèle avec
`mlflow models serve -m models:/ollama_llama32_3b/1 -p 5001`, à condition que
l'instance Ollama soit joignable depuis ce serveur.

## 4. Docker Compose

```bash
docker compose up -d mlflow          # UI sur http://127.0.0.1:5000
docker compose up -d ollama          # seulement si pas d'instance distante
docker compose exec ollama ollama pull llama3.2:3b
docker compose ps                    # healthchecks /health et ollama list
```

Les volumes `mlflow-data` et `ollama-data` conservent respectivement la base
SQLite/les artefacts et les modèles téléchargés. Pour que les runs et traces
apparaissent dans l'UI, décommenter `tracking_uri` dans `llm.yaml` comme indiqué
ci-dessus. Le Healthcheck MLflow interroge `/health`.

Si vous utilisez déjà une instance distante, ne démarrez pas le service `ollama` :
renseignez seulement son URL dans `llm.yaml`.

## 5. Assistant MLflow ↔ Ollama Windows (WSL)

L'UI MLflow utilisée ici tourne dans WSL avec l'unique environnement `.venv`, sur
le port `5002`, avec le backend SQLite `outputs/ollama/tracking/mlflow.db`. Aucun
second Ollama n'est démarré : l'assistant parle à l'instance Windows.

### Cause de l'erreur `localhost:11434`

MLflow 3.18 lit la configuration de l'assistant dans
`~/.mlflow/assistant/config.json`. Le fournisseur Ollama utilise
`providers["ollama"].base_url`, sinon son défaut `http://localhost:11434`.
Il ignore les variables `OLLAMA_HOST` et `OLLAMA_BASE_URL`. Une configuration
avec `"base_url": null` et `"model": "default"` suffit donc à produire
`Connection refused — Cannot connect to Ollama`, même quand SignalFlow fonctionne
déjà avec l'URL Windows.

Le fournisseur met aussi sa configuration en cache dans le processus MLflow :
après un changement d'adresse, il faut redémarrer le serveur.

### Configuration automatique

Deux scripts locaux, sans effet sur les pipelines SignalFlow :

```bash
# Vérifie la détection sans écrire (sortie : URL joignable + modèles)
.venv/bin/python scripts/configure_mlflow_assistant_ollama.py --dry-run

# Écrit la configuration native dans ~/.mlflow/assistant/config.json
.venv/bin/python scripts/configure_mlflow_assistant_ollama.py

# Configure puis démarre l'UI sur http://localhost:5002
./scripts/start_mlflow_assistant_ui.sh
```

La détection essaie successivement :

1. `--base-url` si fourni ;
2. `OLLAMA_BASE_URL` puis `OLLAMA_HOST` ;
3. la passerelle IPv4 par défaut de WSL (`ip -4 route show default`), qui est
   l'hôte Windows en mode NAT.

Chaque candidat est validé par `GET /api/tags` avant écriture. Le script exige la
présence de `qwen3.5:9b` et utilise l'API native
`AssistantConfig.load() / set_provider() / save()`. Les permissions écrites sont
`allow_edit_files=false`, `allow_read_docs=true`, `full_access=false`.

L'assistant utilise `qwen3.5:9b` plutôt que `llama3.2:3b` : ce dernier, trop petit,
ne respecte pas le prompt système de MLflow Assistant et hallucine des appels
`Read`/`Write` avec de fausses données. Le pipeline SignalFlow continue d'utiliser
`llama3.2:3b` dans `config/llm.yaml` ; seules les deux configurations sont distinctes.

Si l'URL ne peut pas être détectée, la forcer explicitement :

```bash
.venv/bin/python scripts/configure_mlflow_assistant_ollama.py \
  --base-url http://<hote-windows-joignable>:11434
```

### Vérification sur le serveur 5002

```bash
curl -fsS http://127.0.0.1:5002/ajax-api/3.0/mlflow/assistant/config | python3 -m json.tool
curl -fsS http://127.0.0.1:5002/ajax-api/3.0/mlflow/assistant/providers | python3 -m json.tool
curl -fsS http://127.0.0.1:5002/ajax-api/3.0/mlflow/assistant/providers/ollama/health
```

La première réponse doit contenir
`"model": "qwen3.5:9b"`, `"selected": true`, une `base_url` distante joignable,
et `full_access: false`. La seconde doit montrer Ollama sélectionné avec
`qwen3.5:9b`. Le healthcheck doit répondre `{"status":"ok"}`.

Dans le navigateur : ouvrir `http://localhost:5002`, ouvrir **MLflow Assistant**,
sélectionner **Ollama** avec **`qwen3.5:9b`**, envoyer **Bonjour**, puis vérifier
qu'une réponse arrive sans appel d'outil, sans demande d'autorisation de fichier
et sans erreur `localhost:11434`.

### Après redémarrage de Windows ou WSL

1. Démarrer Ollama sur Windows et attendre que `qwen3.5:9b` soit disponible.
2. Relancer `./scripts/start_mlflow_assistant_ui.sh` depuis WSL.
3. Si MLflow répondait déjà sur `5002`, redémarrer le serveur : le cache du
   fournisseur conserve l'ancienne adresse jusqu'au redémarrage.
4. Rouvrir `http://localhost:5002` et refaire le test « Bonjour ».

L'adresse Windows n'est pas figée : le script la redétecte à chaque démarrage.
Gardez Ollama lié à l'interface WSL/privée (pas d'exposition Internet, pas de
port forwarding public) et ne démarrez pas le service `ollama` de Compose quand
l'instance Windows est utilisée.

## 6. Limites

- Une réponse Ollama est tronquée à 64 000 caractères et un corps HTTP à
  `max_response_bytes` (8 MiB maximum).
- Le chat accepte au plus 50 messages textuels ; les rôles sont limités à
  `system`, `user`, `assistant`, `tool`.
- Aucun streaming : les appels utilisent `stream: false`.
- Le registre MLflow local (`sqlite`) doit être partagé avec le serveur UI pour
  voir les versions : utiliser le `tracking_uri` HTTP du compose.
