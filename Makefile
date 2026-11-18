# Raccourcis locaux pour SignalFlow.
# Un seul environnement virtuel est utilisé :
#   .venv -> SignalFlow, tests, API, CLI LLM, MLflow 3.18 et Assistant

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV_DIR ?= .venv
PYTHON ?= $(VENV_DIR)/bin/python
PIP ?= $(PYTHON) -m pip
PYTEST ?= $(PYTHON) -m pytest
MLFLOW_PYTHON ?= $(PYTHON)

CONFIG_DIR ?= config
OLLAMA_CONFIG_DIR ?= config
CLASSIFICATION_DIR ?= examples/classification
BENCHMARK_DIR ?= examples/benchmark

OUTPUT_DIR ?= outputs/demo
BENCH_OUTPUT_DIR ?= outputs/baseline
OLLAMA_OUTPUT_DIR ?= outputs/ollama

PROMPT ?= Bonjour
SYSTEM ?=
BASELINE ?=
EXTRA_ARGS ?=

MLFLOW_PORT ?= 5002
API_PORT ?= 8000
MLFLOW_LOG ?= outputs/ollama/tracking/mlflow-ui-5002.log
ASSISTANT_SCRIPT ?= scripts/configure_mlflow_assistant_ollama.py
START_UI ?= scripts/start_mlflow_assistant_ui.sh
# Services d'observabilité uniquement : ollama/mlflow Docker restent arrêtés.
OBSERVABILITY_SERVICES ?= prometheus grafana loki alloy node-exporter cadvisor signalflow-exporter
# Base applicative Phase 4 : stack séparée, volume distinct de MLflow/observabilité.
APP_COMPOSE ?= compose.app.yaml
POSTGRES_URL ?= postgresql+psycopg://signalflow:signalflow-local@127.0.0.1:5433/signalflow_app

OLLAMA_BASE_ARGS = --config-dir $(OLLAMA_CONFIG_DIR) --output-dir $(OLLAMA_OUTPUT_DIR)
ifneq ($(strip $(SYSTEM)),)
OLLAMA_CHAT_ARGS = $(OLLAMA_BASE_ARGS) --chat "$(PROMPT)" --system "$(SYSTEM)"
else
OLLAMA_CHAT_ARGS = $(OLLAMA_BASE_ARGS) --chat "$(PROMPT)"
endif

.PHONY: help quickstart demo bench chat ui start stop restart health logs \
	venv venvs shell mlflow-shell python-info install install-api check test test-api \
	train train-regularized benchmark benchmark-compare \
	ollama-health ollama-models ollama-chat ollama-generate ollama-no-register \
	assistant-check assistant-config assistant-health \
	mlflow-ui mlflow-ui-bg mlflow-health mlflow-logs \
	observability-prepare observability-up observability-down \
	app-db-up app-db-down app-migrate api-token test-postgres \
	api urls status wheel

help: ## Affiche les cibles disponibles
	@printf 'Démarrage rapide : make install → make demo → make ui → make health\n\n'
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make \033[36m<cible>\033[0m\n\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\nVariables utiles : PROMPT, SYSTEM, OUTPUT_DIR, BASELINE, MLFLOW_PORT, API_PORT\n"

# --- Commandes courtes -------------------------------------------------------
quickstart: install mlflow-ui-bg urls ## Installe tout puis lance l'UI et affiche les URLs

demo: train train-regularized ## Lance les deux expériences Iris (C=1 et C=0.05)

bench: benchmark ## Alias court de benchmark

chat: ollama-chat ## Alias court de ollama-chat (PROMPT="...")

ui: mlflow-ui-bg ## Démarre MLflow en arrière-plan sur 5002

start: ui ## Alias de ui

stop: ## Arrête le serveur MLflow sur le port choisi
	@pids="$$(ss -ltnp 2>/dev/null | grep ':$(MLFLOW_PORT)' | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u)"; \
	if [ -n "$$pids" ]; then \
		kill $$pids; \
		printf 'MLflow arrêté (PID %s)\n' "$$pids"; \
	else \
		printf 'Aucun serveur MLflow sur le port %s\n' "$(MLFLOW_PORT)"; \
	fi

restart: stop ## Redémarre proprement le serveur MLflow
	@for i in $$(seq 1 20); do \
		if ! ss -ltn 2>/dev/null | grep -q ':$(MLFLOW_PORT)'; then break; fi; \
		sleep 1; \
	done
	@$(MAKE) mlflow-ui-bg

health: mlflow-health assistant-health ollama-health ## Vérifie MLflow, l'assistant et Ollama

logs: mlflow-logs ## Alias court de mlflow-logs

venv: ## Crée .venv s'il n'existe pas
	@test -x "$(PYTHON)" || python3 -m venv "$(VENV_DIR)"

venvs: ## Affiche l'unique environnement utilisé
	@printf 'Environnement unique : %s\n' "$(VENV_DIR)"

shell: venv ## Ouvre un shell avec .venv activé
	@bash -c 'source "$(VENV_DIR)/bin/activate"; exec bash'

mlflow-shell: shell ## Alias de shell : tout tourne dans le même .venv

python-info: ## Affiche l'interpréteur utilisé par make
	$(PYTHON) -c 'import sys; print("executable:", sys.executable); print("prefix:", sys.prefix)'

install: venv ## Installe SignalFlow, l'API, PostgreSQL et MLflow dans .venv
	$(PIP) install -e '.[dev,ml,monitoring,observability,llm,postgres]' -e 'web/api[test,postgres]' 'mlflow==3.18.0'
	$(PIP) check

install-api: venv ## Installe uniquement l'API FastAPI locale dans .venv
	$(PIP) install -e 'web/api[test]'

check: ## Vérifie les dépendances de .venv
	$(PIP) check

test: ## Lance la suite pytest complète
	$(PYTEST) -q

test-api: ## Lance uniquement les tests de l'API HTTP
	$(PYTEST) -q web/api/tests

train: ## Entraîne la démo Iris (C=1.0)
	$(PYTHON) -m signalflow train \
		--config-dir $(CLASSIFICATION_DIR) \
		--output-dir $(OUTPUT_DIR) \
		$(EXTRA_ARGS)

train-regularized: ## Entraîne la démo Iris (C=0.05)
	$(PYTHON) -m signalflow train \
		--config-dir $(CLASSIFICATION_DIR) \
		--training ./training-regularized.yaml \
		--output-dir $(OUTPUT_DIR) \
		$(EXTRA_ARGS)

benchmark: ## Lance le benchmark CPU/RAM
	$(PYTHON) -m signalflow benchmark \
		--config-dir $(BENCHMARK_DIR) \
		--output-dir $(BENCH_OUTPUT_DIR) \
		$(EXTRA_ARGS)

benchmark-compare: ## Compare avec un baseline (BASELINE=chemin/vers/benchmark.json)
	@test -n "$(BASELINE)" || { printf 'Passer BASELINE=chemin/vers/benchmark.json\n' >&2; exit 2; }
	$(PYTHON) -m signalflow benchmark \
		--config-dir $(BENCHMARK_DIR) \
		--output-dir $(BENCH_OUTPUT_DIR) \
		--baseline "$(BASELINE)" \
		$(EXTRA_ARGS)

ollama-health: ## Teste l'instance Ollama distante
	$(PYTHON) -m signalflow ollama --config-dir $(OLLAMA_CONFIG_DIR) --health

ollama-models: ## Liste les modèles Ollama distants
	$(PYTHON) -m signalflow ollama --config-dir $(OLLAMA_CONFIG_DIR) --models

ollama-chat: ## Appel /api/chat tracé (PROMPT="...")
	$(PYTHON) -m signalflow ollama $(OLLAMA_CHAT_ARGS)

ollama-generate: ## Appel /api/generate tracé (PROMPT="...")
	$(PYTHON) -m signalflow ollama $(OLLAMA_BASE_ARGS) --generate "$(PROMPT)"

ollama-no-register: ## Chat tracé sans version de modèle (PROMPT="...")
	$(PYTHON) -m signalflow ollama $(OLLAMA_CHAT_ARGS) --no-register

assistant-check: ## Vérifie la détection Ollama de l'assistant sans écrire
	$(MLFLOW_PYTHON) $(ASSISTANT_SCRIPT) --dry-run

assistant-config: ## Écrit la configuration native de l'assistant
	$(MLFLOW_PYTHON) $(ASSISTANT_SCRIPT)

assistant-health: ## Affiche la configuration et le healthcheck de l'assistant
	curl -fsS "http://127.0.0.1:$(MLFLOW_PORT)/ajax-api/3.0/mlflow/assistant/config" | $(PYTHON) -m json.tool
	curl -fsS "http://127.0.0.1:$(MLFLOW_PORT)/ajax-api/3.0/mlflow/assistant/providers/ollama/health"; printf '\n'

mlflow-ui: ## Configure puis démarre MLflow au premier plan sur 5002
	MLFLOW_PORT=$(MLFLOW_PORT) $(START_UI)

mlflow-ui-bg: ## Démarre MLflow en arrière-plan avec logs
	@mkdir -p "$(dir $(MLFLOW_LOG))"
	MLFLOW_PORT=$(MLFLOW_PORT) nohup $(START_UI) > "$(MLFLOW_LOG)" 2>&1 &
	@printf 'MLflow lancé en arrière-plan ; logs : %s\n' "$(MLFLOW_LOG)"

mlflow-health: ## Vérifie que MLflow répond sur le port choisi
	curl -fsS "http://127.0.0.1:$(MLFLOW_PORT)/health"; printf '\n'

mlflow-logs: ## Affiche les 80 dernières lignes du log MLflow
	tail -n 80 "$(MLFLOW_LOG)"

observability-prepare: ## Prépare les fichiers de la stack d'observabilité
	$(PYTHON) scripts/prepare_observability.py

observability-up: ## Démarre uniquement Grafana/Prometheus/Loki/Alloy (pas de 2e Ollama)
	docker compose up -d --build $(OBSERVABILITY_SERVICES)

observability-down: ## Arrête la stack d'observabilité
	docker compose down

app-db-up: ## Démarre le PostgreSQL applicatif dédié (port 5433)
	docker compose -f $(APP_COMPOSE) up -d --build

app-db-down: ## Arrête le PostgreSQL applicatif sans supprimer son volume
	docker compose -f $(APP_COMPOSE) down

app-migrate: ## Applique les migrations Alembic à la base applicative
	SIGNALFLOW_DATABASE_URL='$(POSTGRES_URL)' $(PYTHON) -m alembic upgrade head

api-token: ## Crée un jeton local : make api-token SUBJECT=admin ROLE=admin
	@test -n "$(SUBJECT)" || { printf 'SUBJECT est requis\n' >&2; exit 1; }
	SIGNALFLOW_DATABASE_URL='$(POSTGRES_URL)' $(VENV_DIR)/bin/signalflow-api-token \
		--subject "$(SUBJECT)" --role "$(or $(ROLE),admin)"

test-postgres: ## Lance les tests contre un vrai PostgreSQL dédié
	SIGNALFLOW_DATABASE_URL='$(POSTGRES_URL)' $(PYTEST) --postgres -q tests/postgres

api: ## Lance l'API FastAPI locale connectée à la base applicative
	SIGNALFLOW_DATABASE_URL='$(POSTGRES_URL)' $(PYTHON) -m signalflow_api --port $(API_PORT)

urls: ## Affiche les URLs locales utiles
	@printf 'MLflow Assistant : http://localhost:%s\n' "$(MLFLOW_PORT)"
	@printf 'API FastAPI      : http://127.0.0.1:%s/docs\n' "$(API_PORT)"
	@printf 'Grafana          : http://localhost:3000\n'
	@printf 'Prometheus       : http://localhost:9090\n'

status: ## Affiche l'état Git et le listener MLflow
	git status --short
	@ss -ltnp 2>/dev/null | grep ':$(MLFLOW_PORT)' || true

wheel: ## Construit la wheel SignalFlow dans dist/
	$(PIP) wheel . --no-deps -w dist/
