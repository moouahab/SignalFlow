"""Benchmark séquentiel en processus neufs, sans réutilisation d'état ML."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from typing import Any
import uuid

from signalflow.config.loader import schema, yaml_loader
from signalflow.config.loader.errors import ConfigError
from signalflow.config.loader.files import read_text
from signalflow.config.loader.paths import resolve_path
from signalflow.ml.config import load_run_configuration
from signalflow.ml.errors import PipelineError
from signalflow.ml.registry import create_registry
from .statistics import aggregate, compare
from signalflow.observability.session import TelemetrySession, destination


class BenchmarkError(PipelineError):
    """Benchmark invalide, interrompu ou mesures non comparables."""

    code = "BENCHMARK_ERROR"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(read_text(path, max_bytes=16 * 1024 * 1024))
        if not isinstance(result, dict):
            raise ValueError("objet requis")
        return result
    except (ValueError, TypeError):
        raise BenchmarkError("rapport JSON invalide") from None


def _load_spec(base: Path, reference: str) -> dict[str, Any]:
    path = resolve_path(reference, base_dir=base)
    schema_path = resolve_path("./schemas/benchmark.schema.json", base_dir=base)
    contract = schema.load_schema(schema_path, max_bytes=1048576)
    document = yaml_loader.load_project(path, max_bytes=1048576)
    schema.validate(document, contract, schema_path=schema_path).raise_if_invalid(path)
    names = [item["name"] for item in document["configurations"]]
    if len(names) != len(set(names)) or not math.isfinite(document["regression_threshold_percent"]):
        raise BenchmarkError("noms dupliqués ou seuil de régression non fini")
    seeds = set()
    for item in document["configurations"]:
        config = load_run_configuration(base, registry=create_registry(), training_override=item["training"])
        if config.monitoring is None or not config.monitoring.get("enabled", True):
            raise BenchmarkError("le benchmark requiert metrics=local activé")
        seeds.add(config.training["seed"])
    if len(seeds) != 1:
        raise BenchmarkError("les configurations doivent utiliser la même seed de séparation")
    return document


def _execute(base: Path, training: str, output: Path, timeout: int, telemetry_directory: Path | None = None) -> tuple[dict[str, Any], float]:
    environment = dict(os.environ)
    if telemetry_directory is not None:
        environment["SIGNALFLOW_OBSERVABILITY_DIR"] = str(telemetry_directory)
    # Le sous-processus utilise exactement le code du parent, installé ou en src.
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    environment.update({name: "1" for name in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
    )})
    environment["PYTHONHASHSEED"] = "0"
    command = [sys.executable, "-m", "signalflow", "train", "--config-dir", str(base),
               "--training", training, "--output-dir", str(output)]
    started = time.perf_counter()
    try:
        result = subprocess.run(command, cwd=output, env=environment, text=True, capture_output=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise BenchmarkError("délai maximal d'une répétition dépassé ; processus arrêté") from None
    if result.returncode != 0:
        raise BenchmarkError("échec d'une répétition ; consulter les artefacts du run")
    try:
        report = json.loads(result.stdout)
    except ValueError:
        raise BenchmarkError("la commande n'a pas produit un rapport JSON") from None
    if report.get("observability", {}).get("warnings"):
        print("SIGNALFLOW_OBSERVABILITY_WARNING: benchmark_child_publication_failed", file=sys.stderr)
    return report, time.perf_counter() - started


def _normalize(report: dict[str, Any], elapsed: float) -> tuple[dict[str, Any], dict[str, Any]]:
    directory = Path(report["artifacts"])
    observation = _read_json(directory / "monitoring.json")
    if report["status"] != "FINISHED" or not observation["complete"]:
        raise BenchmarkError("run échoué ou mesures incomplètes")
    dataset = _read_json(directory / "dataset.json")
    configuration = _read_json(directory / "configuration.json")
    environment = _read_json(directory / "environment.json")
    # L'empreinte est conservée, mais ne bloque pas une comparaison historique
    # destinée précisément à détecter une régression d'une nouvelle implémentation.
    implementation = environment.pop("signalflow_source_sha256", None)
    hardware = dict(observation["hardware"])
    hardware.pop("system_memory_available_bytes_at_start", None)
    conditions = {
        "dataset_sha256": dataset["sha256"],
        "split_sha256": hashlib.sha256((directory / "split_indices.json").read_bytes()).hexdigest(),
        "dataset_configuration": configuration["dataset"], "seed": configuration["training"]["seed"],
        "environment": environment, "hardware": hardware,
        "monitoring_configuration": configuration["monitoring"],
        "process_policy": "fresh_process_per_run; BLAS=1; PYTHONHASHSEED=0; sequential",
    }
    metrics = {**observation["metrics"], "command_wall_seconds": elapsed,
               "training_duration_seconds": report["training_duration_seconds"],
               **{f"ml_{key}": value for key, value in report["metrics"].items()}}
    if any(not isinstance(value, (float, int)) or not math.isfinite(value) for value in metrics.values()):
        raise BenchmarkError("mesures non numériques ou non finies")
    return {
        "run_id": report["run_id"], "artifacts": str(directory), "metrics": metrics,
        "training": configuration["training"],
        "signalflow_source_sha256": implementation,
        "sampled_window_complete": True,
        "observability_warnings": report.get("observability", {}).get("warnings", []),
    }, conditions


def _write_report(directory: Path, report: dict[str, Any]) -> None:
    (directory / "benchmark.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = ["# Benchmark SignalFlow", "", f"Statut : {report['status']}", "",
             "Moyenne ± écart-type échantillon ; RSS = maximum échantillonné, pas pic garanti.", "",
             "| Configuration | Entraînement (s) | Fit (s) | Pipeline (s) | Commande (s) | CPU (s) | RSS max (MiB) | Accuracy validation | F1 validation |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, group in report.get("summary", {}).items():
        stats = group["metrics"]
        cells = []
        for metric, divisor in (("training_duration_seconds", 1), ("stage_model_fit_seconds", 1),
                                ("pipeline_total_seconds", 1), ("command_wall_seconds", 1),
                                ("process_cpu_seconds", 1), ("process_rss_sampled_max_bytes", 1048576),
                                ("ml_validation_accuracy", 1),
                                ("ml_validation_f1_macro", 1)):
            stat = stats[metric]
            cells.append(f"{stat['mean']/divisor:.4f} ± {stat['stdev']/divisor:.4f}")
        lines.append("| " + " | ".join([name, *cells]) + " |")
    lines.extend(["", "## Limites", ""] + [f"- {line}" for line in report["limitations"]])
    for section, title in (("comparisons", "Comparaisons entre configurations"),
                           ("historical_comparisons", "Comparaison historique")):
        if not report.get(section):
            continue
        lines.extend(["", f"## {title} (heuristique, pas test statistique)", ""])
        for name, results in report[section].items():
            flags = [metric for metric, details in results.items() if details["potential_regression"]]
            lines.append(f"- {name} : {', '.join(flags) if flags else 'aucun signal selon le seuil et la dispersion observés'}")
    (directory / "benchmark.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _historical(report: dict[str, Any], baseline: Path, threshold: float) -> dict[str, Any]:
    previous = _read_json(baseline)
    if previous.get("status") != "FINISHED" or previous.get("conditions") != report["conditions"]:
        raise BenchmarkError("baseline historique incompatible : données, partitions, matériel ou versions différents")
    if set(previous.get("summary", {})) != set(report["summary"]):
        raise BenchmarkError("configurations différentes de la baseline historique")
    comparisons = {}
    for name, group in report["summary"].items():
        if previous["summary"][name]["training"] != group["training"]:
            raise BenchmarkError("hyperparamètres différents de la baseline historique")
        for stats in previous["summary"][name]["metrics"].values():
            if stats["count"] < 2 or not all(math.isfinite(stats[key]) and stats[key] >= 0 for key in ("mean", "stdev")):
                raise BenchmarkError("statistiques de baseline historique invalides")
        comparisons[name] = compare(previous["summary"][name]["metrics"], group["metrics"], threshold)
    return comparisons


def run_benchmark(
    config_dir: str | Path, *, output_dir: str | Path,
    benchmark_reference: str = "./benchmark.yaml", baseline: str | Path | None = None,
) -> dict[str, Any]:
    """Comparer des configurations avec répétitions isolées et ordre maîtrisé.

    Args:
        config_dir: dossier contenant projet, benchmark et schémas.
        output_dir: destination explicite des rapports et runs MLflow.
        benchmark_reference: référence relative sécurisée vers le contrat benchmark.
        baseline: rapport benchmark.json historique facultatif, local et explicite.
    Returns:
        Rapport avec observations brutes, agrégats, conditions et signaux de régression.
    Raises:
        ConfigError/BenchmarkError: validation, exécution ou comparabilité refusée.
        KeyboardInterrupt: interruption après sauvegarde du rapport partiel.
        OSError: impossibilité de lire/écrire les rapports.
    Side effects:
        Lance les entraînements séquentiellement en sous-processus neufs, écrit les
        runs et rapports. Aucune connexion réseau ajoutée. Un échec interrompt la
        campagne ; il n'est jamais retiré silencieusement des statistiques.
    """
    base = Path(config_dir).expanduser().resolve()
    spec = _load_spec(base, benchmark_reference)
    directory = Path(output_dir).expanduser().resolve() / "benchmarks" / uuid.uuid4().hex
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except OSError:
        raise BenchmarkError("impossible de créer le dossier du benchmark") from None
    report: dict[str, Any] = {
        "schema_version": "0.0.1", "status": "RUNNING", "directory": str(directory),
        "specification": spec, "runs": [], "summary": {}, "comparisons": {},
        "limitations": [
            "RSS sampled maximum is a lower bound; short peaks can be missed; shared pages included.",
            "Process CPU includes collector threads, excludes children; one busy core is 100%.",
            "System totals are context, not process consumption; cgroup quotas not measured.",
            "Iris is too short for reliable performance conclusions; synthetic data is not a production workload.",
            "Fresh processes do not clear OS caches; host contention and CPU frequency are uncontrolled.",
            "Warmups are excluded; reported dispersion is sample stdev, not confidence intervals.",
            "Regression flags are heuristic (>threshold and >2 observed stdev); no optimization claim.",
            "Sampling callback cost excludes total observer perturbation. Telemetry publication excluded from pipeline total but included in command wall time.",
        ],
    }
    settings = load_run_configuration(base, registry=create_registry(),
                                      training_override=spec["configurations"][0]["training"]).monitoring
    telemetry = TelemetrySession(settings, directory.name, job="benchmark")
    telemetry_directory = destination(settings) if telemetry.publisher or telemetry.logger else None
    benchmark_started = time.perf_counter()
    rng = random.Random(spec["order_seed"])
    try:
        telemetry.start()
        rounds = spec["warmup_repetitions"] + spec["repetitions"]
        for repetition in range(rounds):
            order = list(spec["configurations"])
            rng.shuffle(order)
            for item in order:
                result, elapsed = _execute(base, item["training"], directory, spec["timeout_seconds"], telemetry_directory)
                run, conditions = _normalize(result, elapsed)
                if "conditions" in report and conditions != report["conditions"]:
                    raise BenchmarkError("les conditions, données ou partitions ont changé entre répétitions")
                report["conditions"] = conditions
                implementation = run.get("signalflow_source_sha256")
                if "implementation_sha256" in report and implementation != report["implementation_sha256"]:
                    raise BenchmarkError("le code SignalFlow a changé pendant la campagne")
                report["implementation_sha256"] = implementation
                run.update(name=item["name"], round=repetition, warmup=repetition < spec["warmup_repetitions"])
                report["runs"].append(run)
                _write_report(directory, report)
        for item in spec["configurations"]:
            runs = [run for run in report["runs"] if run["name"] == item["name"] and not run["warmup"]]
            if any(run["training"] != runs[0]["training"] for run in runs):
                raise BenchmarkError("hyperparamètres modifiés pendant la campagne")
            report["summary"][item["name"]] = {"training": runs[0]["training"], "metrics": aggregate(runs)}
        reference = report["summary"][spec["configurations"][0]["name"]]["metrics"]
        report["comparisons"] = {
            item["name"]: compare(reference, report["summary"][item["name"]]["metrics"], spec["regression_threshold_percent"])
            for item in spec["configurations"][1:]
        }
        if baseline is not None:
            report["historical_comparisons"] = _historical(report, Path(baseline), spec["regression_threshold_percent"])
        report["status"] = "FINISHED"
        _write_report(directory, report)
    except (Exception, KeyboardInterrupt) as error:
        report["status"] = "INTERRUPTED" if isinstance(error, KeyboardInterrupt) else "FAILED"
        report["error_code"] = getattr(error, "code", "BENCHMARK_ERROR")
        try:
            _write_report(directory, report)
        except OSError:
            error.add_note("le rapport partiel du benchmark n'a pas pu être écrit")
        telemetry.finish(False, time.perf_counter() - benchmark_started)
        if isinstance(error, (ConfigError, KeyboardInterrupt)):
            raise
        raise BenchmarkError("échec du benchmark ou rapport de référence invalide") from None
    telemetry.finish(True, time.perf_counter() - benchmark_started)
    return report
