"""Orchestration du pipeline fixe et de son cycle de vie MLflow."""

from __future__ import annotations

import json
import hashlib
import platform
import time
import uuid
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from signalflow.config.loader.errors import ConfigError

from .config import load_run_configuration
from .errors import PipelineError
from .registry import create_registry
from signalflow.monitoring import NullInstrumentation
from signalflow.observability.session import TelemetrySession, LoggedInstrumentation

if TYPE_CHECKING:
    from signalflow.monitoring.local import LocalMonitor
    from .adapters.mlflow import MLflowTracker
    from .adapters.sklearn import SklearnTrainer, TrainingResult


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _environment() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    implementation = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        implementation.update(str(path.relative_to(root)).encode())
        implementation.update(path.read_bytes())
    return {
        "signalflow_source_sha256": implementation.hexdigest(),
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": {name: version(name) for name in (
            "scikit-learn", "numpy", "scipy", "joblib", "mlflow-skinny",
            "SQLAlchemy", "alembic", "PyYAML", "jsonschema", "threadpoolctl",
        )},
        "training_threads": 1,
    }


def _save_results(result: TrainingResult, destination: Path) -> None:
    _write_json(destination / "metrics.json", result.metrics)
    _write_json(destination / "split_indices.json", result.split.as_dict())
    _write_json(destination / "dataset.json", {
        "sha256": result.dataset.sha256,
        "rows": len(result.dataset.y), "features": result.dataset.features,
        "classes": result.model.classes_.tolist(),
        "split_sizes": {name: len(indices) for name, indices in result.split.as_dict().items()},
    })


def _record_failure(tracker: MLflowTracker, run_id: str, destination: Path, error: BaseException) -> None:
    # Préserver l'erreur initiale même si le disque ou le backend est défaillant.
    try:
        _write_json(destination / "report.json", {
            "status": "FAILED", "run_id": run_id,
            "error_code": getattr(error, "code", "PIPELINE_ERROR"),
        })
        tracker.log(run_id, {}, {}, destination)
    except Exception:
        error.add_note("le rapport d'échec n'a pas pu être enregistré complètement")
    try:
        tracker.finish(run_id, "FAILED")
    except Exception:
        error.add_note("le statut FAILED n'a pas pu être confirmé dans MLflow")


def run_training(
    config_dir: str | Path | None = None, *, output_dir: str | Path,
    training_override: str | None = None,
) -> dict[str, Any]:
    """Entraîner, tracer et éventuellement mesurer une expérience locale.

    Args:
        config_dir: dossier de configuration ou défaut historique.
        output_dir: destination explicite des runs et du backend MLflow.
        training_override: fichier training relatif au dossier de configuration.
    Returns:
        Rapport JSON ; monitoring ajouté uniquement si metrics=local est actif.
    Raises:
        ConfigError: configuration, dépendance, entraînement ou suivi invalide.
        PipelineError: problème d'écriture ou erreur inattendue.
        KeyboardInterrupt: interruption après arrêt du collecteur et tentative FAILED.
    Side effects:
        Entraîne, écrit les artefacts et MLflow local ; démarre un thread de collecte
        uniquement si activé. Le total mesuré exclut la publication finale de ses
        propres mesures, mais comprend le suivi normal et la terminaison MLflow.
    """
    started_at = time.perf_counter()
    registry = create_registry()
    configuration = load_run_configuration(config_dir, registry=registry, training_override=training_override)
    monitor: LocalMonitor | None = None
    observation = None
    output = Path(output_dir).expanduser().resolve()
    destination = output / "runs" / uuid.uuid4().hex
    telemetry = TelemetrySession(configuration.monitoring, destination.name)
    run_id = None
    tracker = None
    monitoring_started = False
    try:
        telemetry.start()
        telemetry.event("configuration_loaded")
        telemetry.event("configuration_validated")
        if configuration.monitoring is not None and configuration.monitoring.get("enabled", True):
            metric_tools = registry.initialize([tool for tool in configuration.loaded.tools if tool.category == "metrics"])
            monitor = cast("LocalMonitor | None", metric_tools.get("metrics"))
        observer = monitor if monitor is not None else NullInstrumentation()
        if telemetry.logger is not None:
            observer = LoggedInstrumentation(observer, telemetry)
        if monitor is not None:
            monitor.start(configuration.monitoring)
            monitoring_started = True
        with observer.stage("initialization"):
            tools = registry.initialize([tool for tool in configuration.loaded.tools if tool.category != "metrics"])
            trainer = cast("SklearnTrainer", tools["training"])
            tracker = cast("MLflowTracker", tools["experiment_tracking"])
            destination.mkdir(parents=True, exist_ok=False)
            _write_json(destination / "configuration.json", configuration.snapshot())
            _write_json(destination / "environment.json", _environment())
            run_id = tracker.start(configuration.tracking, output)
            telemetry.run_id = run_id
            parameters = {
                **configuration.training["model"]["parameters"],
                "seed": configuration.training["seed"], "model": "logistic_regression",
                "solver": "lbfgs", "imputer": "median", "scaler": "standard",
                **{f"split_{key}": value for key, value in configuration.dataset["split"].items()},
            }
            tracker.log(run_id, parameters, {}, destination)
        result = trainer.train(configuration.training, configuration.dataset, configuration.dataset_path,
                               instrumentation=observer)
        report = {
            "status": "FINISHED", "run_id": run_id,
            "experiment_name": configuration.tracking["experiment_name"],
            "seed": configuration.training["seed"],
            "hyperparameters": configuration.training["model"]["parameters"],
            "metrics": result.metrics, "training_duration_seconds": result.duration_seconds,
            "artifacts": str(destination), "model": str(destination / "model.joblib"),
            "tracking_uri": f"sqlite:///{output / 'tracking' / 'mlflow.db'}",
        }
        with observer.stage("artifact_save"):
            trainer.save(result, destination)
            _save_results(result, destination)
            _write_json(destination / "report.json", report)
        with observer.stage("tracking_export"):
            tracker.log(run_id, {"dataset_sha256": result.dataset.sha256}, {
                **result.metrics, "training_duration_seconds": result.duration_seconds,
            }, destination)
            tracker.finish(run_id)
        if monitor is not None:
            observation = monitor.stop(pipeline_started_at=started_at)
            report["monitoring"] = {"complete": observation["complete"], "metrics": observation["metrics"]}
            _write_json(destination / "monitoring.json", observation)
            _write_json(destination / "report.json", report)
            # MLflow accepte la publication des métriques d'un run terminé. Seuls
            # les petits rapports sont copiés ici, pas une seconde copie du modèle.
            tracker.log_observations(run_id, observation["metrics"], [
                destination / "monitoring.json", destination / "report.json",
            ])
        duration = observation["metrics"]["pipeline_total_seconds"] if observation else time.perf_counter() - started_at
        telemetry.finish(True, duration, observation)
        if telemetry.publisher is not None or telemetry.logger is not None:
            report["observability"] = {"warnings": telemetry.errors, "publication_io_seconds": telemetry.io_seconds}
        return report
    except (Exception, KeyboardInterrupt) as error:
        if monitoring_started:
            try:
                observation = monitor.stop(pipeline_started_at=started_at)
                if destination.is_dir():
                    _write_json(destination / "monitoring.json", observation)
                    if run_id is not None and tracker is not None:
                        tracker.log_observations(run_id, observation["metrics"], [destination / "monitoring.json"])
            except Exception:
                error.add_note("la publication des mesures partielles a échoué")
        if run_id is not None and tracker is not None:
            _record_failure(tracker, run_id, destination, error)
        telemetry.finish(False, time.perf_counter() - started_at, observation)
        if isinstance(error, (ConfigError, KeyboardInterrupt)):
            raise
        raise PipelineError("échec du pipeline ou de l'écriture des artefacts") from None
