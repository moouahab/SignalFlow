#!/usr/bin/env bash
# Démarre l'UI MLflow 3.18 avec l'assistant configuré pour Ollama (Windows/WSL).
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${SIGNALFLOW_VENV_DIR:-${MLFLOW_VENV_DIR:-$ROOT_DIR/.venv}}"
PYTHON="$VENV_DIR/bin/python"
MLFLOW="$VENV_DIR/bin/mlflow"
DB_PATH="$ROOT_DIR/outputs/ollama/tracking/mlflow.db"
PORT="${MLFLOW_PORT:-5002}"

for required in "$PYTHON" "$MLFLOW"; do
  if [[ ! -x "$required" ]]; then
    echo "Exécutable manquant : $required" >&2
    exit 1
  fi
done

if [[ ! -f "$DB_PATH" ]]; then
  echo "Base de tracking introuvable : $DB_PATH" >&2
  exit 1
fi

# Rafraîchit l'adresse Windows détectée. À faire avant chaque démarrage : le
# fournisseur Ollama met sa configuration en cache dans le processus MLflow.
"$PYTHON" "$ROOT_DIR/scripts/configure_mlflow_assistant_ollama.py"

if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "MLflow répond déjà sur http://127.0.0.1:${PORT}."
  echo "La configuration a été rafraîchie ; redémarrez le serveur si l'adresse Ollama a changé."
  exit 0
fi

echo "Démarrage de MLflow sur http://127.0.0.1:${PORT}"
exec "$MLFLOW" server \
  --backend-store-uri "sqlite:///$DB_PATH" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --workers 1
