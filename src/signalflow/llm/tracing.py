"""Suivi MLflow des appels Ollama : runs locaux ou distants et traces natives."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .adapters.ollama import OllamaClient
from .errors import OllamaTrackingError

if TYPE_CHECKING:
    from signalflow.ml.adapters.mlflow import MLflowTracker

_REDACTED = "<redacted>"


class MlflowTraceSession:
    """Aligne temporairement l'URI globale MLflow sur celle du suivi.

    L'export des traces MLflow lit l'URI globale au moment de l'envoi, même
    quand les spans sont créés via MlflowClient. Le contexte la positionne puis
    la restaure après vidage ; un verrou évite deux sessions concurrentes.
    """

    _lock = threading.Lock()

    def __init__(self, tracking_uri: str) -> None:
        self.tracking_uri = tracking_uri
        self.previous: str | None = None
        self.active = False

    def __enter__(self) -> MlflowTraceSession:
        """Positionner l'URI globale si elle diffère, sous verrou."""
        import mlflow

        with self._lock:
            self.previous = mlflow.get_tracking_uri()
            if self.previous != self.tracking_uri:
                mlflow.set_tracking_uri(self.tracking_uri)
            self.active = True
        return self

    def __exit__(self, *_exc: object) -> None:
        """Vider les traces puis restaurer l'URI initiale, sous verrou."""
        import mlflow

        with self._lock:
            try:
                if hasattr(mlflow, "flush_trace_async_logging"):
                    mlflow.flush_trace_async_logging()
            finally:
                if self.previous is not None and mlflow.get_tracking_uri() != self.previous:
                    mlflow.set_tracking_uri(self.previous)
                self.active = False


class OllamaTracker:
    """Un run MLflow par appel Ollama, avec mesures, artefact et trace native."""

    def __init__(
        self, client: OllamaClient, settings: dict[str, Any], destination: Path,
        output: Path, *, tracker: MLflowTracker | None = None,
    ) -> None:
        """Conserver le client, les réglages et le dossier de rapport.

        Args:
            client: client Ollama déjà configuré.
            settings: sous-dictionnaire ``tracking`` de llm.yaml validé.
            destination: dossier de sortie de ce run, créé par l'appelant.
            output: racine abstraite contenant tracking/ et runs/.
            tracker: adaptateur MLflow injectable pour les tests.
        Returns:
            None.
        Raises:
            OllamaTrackingError: suivi requis absent.
        Side effects:
            Aucun réseau ; le run n'est créé qu dans start().
        """
        if tracker is None:
            from signalflow.ml.adapters.mlflow import MLflowTracker

            tracker = MLflowTracker(settings.get("tracking_uri"))
        self.client = client
        self.settings = settings
        self.destination = destination
        self.output = output
        self.tracker = tracker
        self.run_id: str | None = None
        self.experiment_id: str | None = None
        self.trace_ids: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self._root_spans: dict[str, str] = {}
        self._session: MlflowTraceSession | None = None

    def start(self) -> str:
        """Créer le run et ouvrir la session de traces natives si demandé."""
        try:
            self.run_id = self.tracker.start(self.settings, self.output)
            mlflow_client = self.tracker.client
            self.experiment_id = mlflow_client.get_run(self.run_id).info.experiment_id
            if self.settings.get("native_traces", True):
                self._session = MlflowTraceSession(mlflow_client.tracking_uri)
                self._session.__enter__()
        except Exception:
            raise OllamaTrackingError("impossible de démarrer le suivi MLflow") from None
        return self.run_id

    def log_report(self, path: Path) -> None:
        """Ajouter un petit rapport JSON aux artefacts du run."""
        if self.run_id is None or self.tracker.client is None:
            raise OllamaTrackingError("le run MLflow n'est pas démarré")
        try:
            self.tracker.client.log_artifact(self.run_id, str(path), artifact_path="ollama")
        except Exception:
            raise OllamaTrackingError("impossible de publier le rapport Ollama") from None

    def finish(self, status: str = "FINISHED") -> None:
        """Fermer la session de traces puis terminer le run."""
        if self._session is not None:
            self._session.__exit__()
            self._session = None
        if self.run_id is not None:
            try:
                self.tracker.finish(self.run_id, status)
            except Exception:
                raise OllamaTrackingError("impossible de terminer le run MLflow") from None

    def _visible(self, value: Any) -> Any:
        """Masquer prompts et réponses si l'option de confidentialité est active."""
        if self.settings.get("log_prompts", True):
            return value
        return _REDACTED

    def _record(
        self, kind: str, request: Any, result: dict[str, Any],
        options: dict[str, Any] | None, trace_id: str | None,
    ) -> dict[str, Any]:
        """Enregistrer paramètres, mesures, artefact JSON et span LLM."""
        if self.run_id is None:
            raise OllamaTrackingError("le run MLflow n'est pas démarré")
        call: dict[str, Any] = {
            "kind": kind, "model": result["model"], "text": self._visible(result["text"]),
            "prompt_eval_count": result["prompt_eval_count"], "eval_count": result["eval_count"],
            "latency_seconds": result["latency_seconds"], "trace_id": trace_id,
        }
        self.calls.append(call)
        call_dir = self.destination / "traces" / str(len(self.calls))
        call_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "kind": kind, "provider": "ollama", "model": result["model"],
            "request": self._visible(request), "response": {"text": self._visible(result["text"])},
            "metrics": {key: result[key] for key in (
                "latency_seconds", "prompt_eval_count", "eval_count", "tokens_per_second",
                "total_duration_ns", "prompt_eval_duration_ns", "eval_duration_ns",
            )},
            "trace_id": trace_id, "run_id": self.run_id,
        }
        (call_dir / "call.json").write_text(
            json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8",
        )
        parameters = {
            "provider": "ollama", "model": result["model"], "endpoint": kind,
            **{f"option.{key}": str(value) for key, value in (options or {}).items()},
        }
        metrics = {
            "ollama.latency_seconds": result["latency_seconds"],
            "ollama.prompt_tokens": result["prompt_eval_count"],
            "ollama.completion_tokens": result["eval_count"],
        }
        if result["tokens_per_second"] is not None:
            metrics["ollama.tokens_per_second"] = result["tokens_per_second"]
        try:
            self.tracker.log(self.run_id, parameters, metrics, call_dir)
        except Exception:
            raise OllamaTrackingError("échec de publication du run Ollama") from None

        if trace_id is not None:
            self._record_span(trace_id, kind, request, result)
        return call

    def _record_span(
        self, trace_id: str, kind: str, request: Any, result: dict[str, Any],
    ) -> None:
        """Ajouter un span LLM à la trace native existante."""
        try:
            mlflow_client = self.tracker.client
            span = mlflow_client.start_span(
                name=f"ollama.{kind}", trace_id=trace_id,
                parent_id=self._root_spans[trace_id], span_type="LLM",
                inputs=self._visible(request),
                attributes={"model": result["model"], "provider": "ollama"},
            )
            mlflow_client.end_span(
                trace_id=trace_id, span_id=span.span_id,
                outputs={"text": self._visible(result["text"])},
                attributes={
                    "prompt_tokens": str(result["prompt_eval_count"]),
                    "completion_tokens": str(result["eval_count"]),
                    "latency_seconds": str(result["latency_seconds"]),
                },
            )
        except Exception:
            # La trace reste disponible via le run et l'artefact JSON.
            self.calls[-1]["span_error"] = True

    def _start_trace(self, kind: str, request: Any) -> str | None:
        """Démarrer une trace native liée au run, si l'API est disponible."""
        if self._session is None or self.tracker.client is None:
            return None
        try:
            span = self.tracker.client.start_trace(
                name=f"ollama.{kind}", inputs=self._visible(request),
                experiment_id=self.experiment_id, run_id=self.run_id,
                attributes={"provider": "ollama", "model": self.client.model},
            )
        except Exception:
            return None
        self._root_spans[span.trace_id] = span.span_id
        self.trace_ids.append(span.trace_id)
        return span.trace_id

    def _end_trace(self, trace_id: str | None, result: dict[str, Any] | None) -> None:
        """Terminer une trace native, en erreur si l'appel a échoué."""
        if trace_id is None or self.tracker.client is None:
            return
        attributes = {}
        if result is not None:
            attributes["latency_seconds"] = str(result["latency_seconds"])
        try:
            self.tracker.client.end_trace(
                trace_id=trace_id,
                outputs={"text": self._visible(result["text"])} if result else None,
                attributes=attributes, status="OK" if result else "ERROR",
            )
        except Exception:
            pass

    def chat(
        self, messages: list[dict[str, str]], *, options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Appeler Ollama en chat et publier le résultat dans MLflow."""
        trace_id = self._start_trace("chat", messages)
        started = time.perf_counter()
        try:
            result = self.client.chat(messages, options=options)
        except Exception:
            self._end_trace(trace_id, None)
            raise
        result["latency_seconds"] = round(time.perf_counter() - started, 6)
        self._record("chat", messages, result, options, trace_id)
        self._end_trace(trace_id, result)
        return result

    def generate(
        self, prompt: str, *, options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Appeler Ollama en génération et publier le résultat dans MLflow."""
        trace_id = self._start_trace("generate", prompt)
        started = time.perf_counter()
        try:
            result = self.client.generate(prompt, options=options)
        except Exception:
            self._end_trace(trace_id, None)
            raise
        result["latency_seconds"] = round(time.perf_counter() - started, 6)
        self._record("generate", prompt, result, options, trace_id)
        self._end_trace(trace_id, result)
        return result
