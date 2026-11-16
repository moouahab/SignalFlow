"""Statistiques et orchestration : aucune assertion de vitesse dépendante du matériel."""

import copy
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from signalflow.benchmarking import runner
from signalflow.benchmarking.statistics import summarize, aggregate, compare, REGRESSION_METRICS


def test_sample_statistics():
    result = summarize([1.0, 2.0, 3.0])
    assert result == {"count": 3, "mean": 2.0, "median": 2.0, "stdev": 1.0,
                      "min": 1.0, "max": 3.0, "cv_percent": 50.0}
    with pytest.raises(ValueError):
        summarize([1])


def test_regressions_use_threshold_and_dispersion():
    reference = {key: summarize([10., 10., 10.]) for key in REGRESSION_METRICS}
    slower = {key: summarize([15., 15., 15.]) for key in REGRESSION_METRICS}
    assert all(item["potential_regression"] for item in compare(reference, slower, 10).values())
    noisy = {key: summarize([5., 15., 25.]) for key in REGRESSION_METRICS}
    assert not any(item["potential_regression"] for item in compare(reference, noisy, 10).values())
    assert not any(item["potential_regression"] for item in compare(reference, slower, 60).values())


def test_mismatching_metrics_rejected():
    with pytest.raises(ValueError):
        aggregate([{"metrics": {"a": 1}}, {"metrics": {"b": 1}}])


@pytest.fixture
def fake_execution(monkeypatch):
    calls = []

    def execute(base, training, output, timeout, telemetry_directory=None):
        calls.append(training)
        value = 2.0 if 'regularized' in training else 1.0
        metrics = {key: value for key in REGRESSION_METRICS}
        metrics["ml_validation_f1_macro"] = 0.9
        metrics["ml_validation_accuracy"] = 0.9
        metrics["training_duration_seconds"] = value
        return {"run_id": str(len(calls)), "artifacts": str(output), "metrics": metrics,
                "training": {"seed": 42, "C": value}}, value

    monkeypatch.setattr(runner, "_execute", execute)
    monkeypatch.setattr(runner, "_normalize", lambda report, elapsed: (report, {"dataset": "fixed"}))
    return calls


def test_repetitions_warmup_and_reproducible_order(monitored_config, tmp_path, fake_execution):
    first = runner.run_benchmark(monitored_config, output_dir=tmp_path / "first")
    order = list(fake_execution)
    fake_execution.clear()
    second = runner.run_benchmark(monitored_config, output_dir=tmp_path / "second")
    assert fake_execution == order
    assert len(first["runs"]) == 8
    assert sum(run["warmup"] for run in first["runs"]) == 2
    for group in first["summary"].values():
        assert group["metrics"]["stage_model_fit_seconds"]["count"] == 3
    assert first["summary"] == second["summary"]
    assert Path(first["directory"], "benchmark.json").is_file()
    assert Path(first["directory"], "benchmark.md").is_file()
    assert first["comparisons"]["regularized"]["stage_model_fit_seconds"]["potential_regression"]


def test_historical_baseline_and_incompatible_conditions(monitored_config, tmp_path, fake_execution):
    first = runner.run_benchmark(monitored_config, output_dir=tmp_path / "first")
    baseline = Path(first["directory"]) / "benchmark.json"
    second = runner.run_benchmark(monitored_config, output_dir=tmp_path / "second", baseline=baseline)
    assert not any(item["potential_regression"] for group in second["historical_comparisons"].values() for item in group.values())
    data = json.loads(baseline.read_text())
    data["conditions"]["dataset"] = "different"
    baseline.write_text(json.dumps(data))
    with pytest.raises(runner.BenchmarkError):
        runner.run_benchmark(monitored_config, output_dir=tmp_path / "third", baseline=baseline)


@pytest.mark.parametrize("error,status", [(runner.BenchmarkError("failed child"), "FAILED"), (KeyboardInterrupt(), "INTERRUPTED")])
def test_partial_report_on_error_and_interruption(monitored_config, tmp_path, monkeypatch, error, status):
    def fail(*args):
        raise error
    monkeypatch.setattr(runner, "_execute", fail)
    with pytest.raises(type(error)):
        runner.run_benchmark(monitored_config, output_dir=tmp_path / "out")
    path, = (tmp_path / "out/benchmarks").glob("*/benchmark.json")
    report = json.loads(path.read_text())
    assert report["status"] == status
    assert report["summary"] == {}


def test_changed_conditions_not_aggregated(monitored_config, tmp_path, monkeypatch, fake_execution):
    count = 0
    def normalize(report, elapsed):
        nonlocal count
        count += 1
        return report, {"dataset": count}
    monkeypatch.setattr(runner, "_normalize", normalize)
    with pytest.raises(runner.BenchmarkError):
        runner.run_benchmark(monitored_config, output_dir=tmp_path)


def test_timeout_is_explicit(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('child', 1)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(runner.BenchmarkError, match="délai"):
        runner._execute(tmp_path, "./training.yaml", tmp_path, 1)


def test_output_path_error_is_typed(monitored_config, tmp_path):
    output = tmp_path / "file"
    output.write_text("not a directory")
    with pytest.raises(runner.BenchmarkError, match="créer le dossier"):
        runner.run_benchmark(monitored_config, output_dir=output)


def test_final_report_failure_is_typed(monitored_config, tmp_path, fake_execution, monkeypatch):
    original = runner._write_report

    def fail_final(directory, report):
        if report["status"] == "FINISHED":
            raise OSError("disk failure")
        original(directory, report)

    monkeypatch.setattr(runner, "_write_report", fail_final)
    with pytest.raises(runner.BenchmarkError):
        runner.run_benchmark(monitored_config, output_dir=tmp_path / "out")
    path, = (tmp_path / "out/benchmarks").glob("*/benchmark.json")
    assert json.loads(path.read_text())["status"] == "FAILED"


def test_markdown_exposes_measurements_and_historical_comparison(monitored_config, tmp_path, fake_execution):
    first = runner.run_benchmark(monitored_config, output_dir=tmp_path / "first")
    second = runner.run_benchmark(monitored_config, output_dir=tmp_path / "second",
                                  baseline=Path(first["directory"]) / "benchmark.json")
    markdown = Path(second["directory"], "benchmark.md").read_text()
    for label in ("Entraînement (s)", "CPU (s)", "Accuracy validation", "Comparaison historique"):
        assert label in markdown
