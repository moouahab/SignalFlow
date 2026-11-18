"""Façades d'exécution Ollama : listage, chat suivi et modèle MLflow."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from signalflow.config.loader.errors import ConfigError
from signalflow.ml.registry import create_registry

from .adapters.ollama import OllamaClient
from .config import LLMConfiguration, load_llm_configuration
from .errors import LLMError
from .tracing import OllamaTracker

Reporter = Callable[[OllamaTracker], dict[str, Any]]


def _write_json(path: Path, value: Any) -> None:
    """Écrire un rapport JSON déterministe et lisible."""
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _setup(config_dir: str | Path | None, output_dir: str | Path) -> tuple[LLMConfiguration, Path, Path]:
    """Valider la configuration et créer le dossier de run."""
    configuration = load_llm_configuration(config_dir, registry=create_registry())
    output = Path(output_dir).expanduser().resolve()
    destination = output / "runs" / uuid.uuid4().hex
    destination.mkdir(parents=True, exist_ok=False)
    return configuration, output, destination


def _execute(
    config_dir: str | Path | None, output_dir: str | Path, action: Reporter,
    *, register: bool,
) -> dict[str, Any]:
    """Exécuter une action avec cycle de vie MLflow complet."""
    configuration, output, destination = _setup(config_dir, output_dir)
    client = OllamaClient(configuration.ollama)
    tracker = OllamaTracker(client, configuration.tracking, destination, output)
    run_id = None
    started = time.perf_counter()
    try:
        run_id = tracker.start()
        report = action(tracker)
        if register and configuration.tracking.get("registered_model_name"):
            from .model import log_model, register_model, save_model

            model_dir = save_model(destination / "model", configuration.ollama)
            source = log_model(tracker.tracker.client, run_id, model_dir)
            report["model"] = {
                "path": str(model_dir), "source": source,
                "name": configuration.tracking["registered_model_name"],
                "version": register_model(
                    tracker.tracker.client, run_id, source,
                    configuration.tracking["registered_model_name"],
                ),
            }
        report["status"] = "FINISHED"
        report["run_id"] = run_id
        report["trace_ids"] = list(tracker.trace_ids)
        report["duration_seconds"] = round(time.perf_counter() - started, 6)
        report["artifacts"] = str(destination)
        report["tracking_uri"] = tracker.tracker.client.tracking_uri
        _write_json(destination / "report.json", report)
        tracker.log_report(destination / "report.json")
        tracker.finish()
        return report
    except (Exception, KeyboardInterrupt) as error:
        try:
            _write_json(destination / "report.json", {"status": "FAILED", "run_id": run_id})
        except Exception:
            pass
        if run_id is not None:
            try:
                tracker.finish("FAILED")
            except Exception:
                pass
        if isinstance(error, (ConfigError, KeyboardInterrupt, LLMError)):
            raise
        raise LLMError("échec du pipeline Ollama") from None


def list_models(config_dir: str | Path | None = None) -> dict[str, Any]:
    """Lister les modèles de l'instance Ollama configurée."""
    configuration = load_llm_configuration(config_dir, registry=create_registry())
    models = OllamaClient(configuration.ollama).tags()
    return {"provider": "ollama", "models": models}


def health(config_dir: str | Path | None = None) -> dict[str, Any]:
    """Tester la disponibilité de l'instance Ollama configurée."""
    configuration = load_llm_configuration(config_dir, registry=create_registry())
    available = OllamaClient(configuration.ollama).health()
    return {"provider": "ollama", "available": available}


def run_ollama_chat(
    config_dir: str | Path | None, output_dir: str | Path, prompt: str, *,
    system: str | None = None, options: dict[str, Any] | None = None,
    register: bool = True,
) -> dict[str, Any]:
    """Générer une réponse de chat et la tracer dans MLflow.

    Args:
        config_dir: dossier de configuration contenant tools.yaml et llm.yaml.
        output_dir: racine des runs et du suivi MLflow.
        prompt: dernier message utilisateur, non vide.
        system: message système optionnel, placé en tête.
        options: paramètres de génération surchargants ceux du YAML.
        register: enregistrer aussi le modèle dans le Model Registry.
    Returns:
        Rapport JSON avec run_id, trace_ids, mesures et réponse.
    Raises:
        ConfigError ou LLMError: configuration, réseau ou suivi indisponible.
    Side effects:
        Appelle l'instance distante, écrit des artefacts et un run MLflow.
    """

    def action(tracker: OllamaTracker) -> dict[str, Any]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        result = tracker.chat(messages, options=options)
        return {"endpoint": "chat", "response": result["text"], "metrics": {
            "model": result["model"], "latency_seconds": result["latency_seconds"],
            "prompt_tokens": result["prompt_eval_count"],
            "completion_tokens": result["eval_count"],
            "tokens_per_second": result["tokens_per_second"],
        }}

    return _execute(config_dir, output_dir, action, register=register)


def run_ollama_generate(
    config_dir: str | Path | None, output_dir: str | Path, prompt: str, *,
    options: dict[str, Any] | None = None, register: bool = True,
) -> dict[str, Any]:
    """Générer une réponse à partir d'un prompt et la tracer dans MLflow."""

    def action(tracker: OllamaTracker) -> dict[str, Any]:
        result = tracker.generate(prompt, options=options)
        return {"endpoint": "generate", "response": result["text"], "metrics": {
            "model": result["model"], "latency_seconds": result["latency_seconds"],
            "prompt_tokens": result["prompt_eval_count"],
            "completion_tokens": result["eval_count"],
            "tokens_per_second": result["tokens_per_second"],
        }}

    return _execute(config_dir, output_dir, action, register=register)
