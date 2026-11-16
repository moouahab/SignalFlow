"""Mesures réellement collectées et envoyées à MLflow, sans seuils de performance."""

import json
from pathlib import Path
import threading

import pytest
import yaml

pytest.importorskip("sklearn")
pytest.importorskip("mlflow")
pytest.importorskip("psutil")

from mlflow.tracking import MlflowClient
from signalflow.ml.runner import run_training
from signalflow.ml.errors import TrainingError
from signalflow.benchmarking import run_benchmark


def test_monitoring_mlflow_and_stage_coverage(monitored_config, tmp_path):
    report = run_training(monitored_config, output_dir=tmp_path / "out")
    observation = json.loads(Path(report["artifacts"], "monitoring.json").read_text())
    assert observation["complete"]
    for stage in ("dataset_load", "data_split", "preprocessing", "model_fit", "predict_evaluate", "artifact_save"):
        assert observation["metrics"][f"stage_{stage}_seconds"] >= 0
    assert observation["metrics"]["process_rss_sampled_max_bytes"] >= observation["metrics"]["process_rss_start_bytes"]
    assert observation["metrics"]["sampling_count"] >= 2
    client = MlflowClient(tracking_uri=report["tracking_uri"])
    run = client.get_run(report["run_id"])
    assert run.info.status == "FINISHED"
    assert run.data.metrics["monitoring.stage_model_fit_seconds"] == observation["metrics"]["stage_model_fit_seconds"]
    assert "monitoring.json" in {artifact.path for artifact in client.list_artifacts(report["run_id"])}
    assert not any(thread.name == "signalflow-monitor" for thread in threading.enumerate())


def test_failed_training_preserves_partial_observations(monitored_config, tmp_path):
    path = monitored_config / "training.yaml"
    training = yaml.safe_load(path.read_text())
    training["model"]["parameters"]["max_iter"] = 1
    path.write_text(yaml.safe_dump(training))
    output = tmp_path / "out"
    with pytest.raises(TrainingError):
        run_training(monitored_config, output_dir=output)
    artifact, = (output / "runs").glob("*/monitoring.json")
    observation = json.loads(artifact.read_text())
    assert "model_fit" in observation["failed_stages"]
    client = MlflowClient(tracking_uri=f"sqlite:///{output / 'tracking/mlflow.db'}")
    experiment = client.get_experiment_by_name("signalflow-classification")
    run, = client.search_runs([experiment.experiment_id])
    assert run.info.status == "FAILED"
    assert "monitoring.stage_model_fit_seconds" in run.data.metrics
    assert not any(thread.name == "signalflow-monitor" for thread in threading.enumerate())


def test_interruption_stops_collector(monitored_config, tmp_path, monkeypatch):
    from signalflow.ml.adapters.sklearn import SklearnTrainer
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(SklearnTrainer, "train", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_training(monitored_config, output_dir=tmp_path / "out")
    assert not any(thread.name == "signalflow-monitor" for thread in threading.enumerate())
    path, = (tmp_path / "out/runs").glob("*/report.json")
    assert json.loads(path.read_text())["status"] == "FAILED"


def test_monitoring_disabled_retains_old_artifact_contract(monitored_config, tmp_path):
    enabled = run_training(monitored_config, output_dir=tmp_path / "out")
    path = monitored_config / "tools.yaml"
    tools = yaml.safe_load(path.read_text())
    tools["tools"]["metrics"]["enabled"] = False
    path.write_text(yaml.safe_dump(tools))
    report = run_training(monitored_config, output_dir=tmp_path / "out")
    assert "monitoring" not in report
    assert not Path(report["artifacts"], "monitoring.json").exists()
    assert report["metrics"] == enabled["metrics"]


def test_incomplete_measurements_refused_by_benchmark(monitored_config, tmp_path):
    from signalflow.benchmarking.runner import _normalize, BenchmarkError
    report = run_training(monitored_config, output_dir=tmp_path / "out")
    path = Path(report["artifacts"], "monitoring.json")
    observation = json.loads(path.read_text())
    observation["complete"] = False
    path.write_text(json.dumps(observation))
    with pytest.raises(BenchmarkError):
        _normalize(report, 1.0)


def test_real_benchmark_processes_and_fingerprints(monitored_config, tmp_path):
    path = monitored_config / "benchmark.yaml"
    spec = yaml.safe_load(path.read_text())
    spec.update(repetitions=2, warmup_repetitions=0)
    path.write_text(yaml.safe_dump(spec))
    report = run_benchmark(monitored_config, output_dir=tmp_path / "out")
    assert report["status"] == "FINISHED"
    assert len(report["runs"]) == 4
    assert len({run["run_id"] for run in report["runs"]}) == 4
    assert report["summary"]["baseline"]["metrics"]["training_duration_seconds"]["mean"] > 0
    assert len(report["conditions"]["dataset_sha256"]) == 64
    assert len(report["conditions"]["split_sha256"]) == 64
    assert report["summary"]["baseline"]["metrics"]["stage_model_fit_seconds"]["count"] == 2
    for run in report["runs"]:
        assert run["metrics"]["command_wall_seconds"] >= run["metrics"]["pipeline_total_seconds"]


def test_synthetic_dataset_reproducible():
    from signalflow.ml.adapters.data import load_dataset
    import numpy as np
    source = {"type": "synthetic", "n_samples": 200, "n_features": 10,
              "n_informative": 5, "n_redundant": 2, "n_classes": 3, "random_state": 123}
    first, second = load_dataset(source), load_dataset(source)
    assert first.X.shape == (200, 10)
    assert first.sha256 == second.sha256
    np.testing.assert_array_equal(first.X, second.X)
