"""Runs MLflow réels, cycle d'échec et commande hors du dépôt."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import Mock

import pytest
import yaml

pytest.importorskip("sklearn")
pytest.importorskip("mlflow")
pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")

from mlflow.tracking import MlflowClient

from signalflow.ml.errors import TrackingError, TrainingError
from signalflow.ml.runner import run_training


def test_full_pipeline_mlflow_artifacts_and_offline(ml_config, tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network connection forbidden during local training")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    output = tmp_path / "output"
    report = run_training(ml_config, output_dir=output)
    assert report["status"] == "FINISHED"
    client = MlflowClient(tracking_uri=report["tracking_uri"])
    run = client.get_run(report["run_id"])
    assert run.info.status == "FINISHED"
    assert run.data.params["seed"] == "42"
    assert float(run.data.params["C"]) == 1.0
    assert run.data.metrics["test_accuracy"] == report["metrics"]["test_accuracy"]
    assert run.data.metrics["training_duration_seconds"] > 0
    artifact_names = {artifact.path for artifact in client.list_artifacts(report["run_id"])}
    assert artifact_names == {
        "model.joblib", "configuration.json", "environment.json", "metrics.json",
        "dataset.json", "split_indices.json", "report.json",
        "mlflow_model", "model_manifest.json",
    }
    artifact = Path(client.download_artifacts(report["run_id"], "configuration.json"))
    config = json.loads(artifact.read_text())
    assert config["training"]["seed"] == 42
    data = json.loads((Path(report["artifacts"]) / "dataset.json").read_text())
    assert data["split_sizes"] == {"train": 90, "validation": 30, "test": 30}
    assert run.data.params["dataset_sha256"] == data["sha256"]
    # Vérifier le contenu du modèle copié dans MLflow, pas seulement son nom.
    import joblib
    import numpy as np
    from sklearn.datasets import load_iris
    tracked_model = joblib.load(client.download_artifacts(report["run_id"], "model.joblib"))
    local_model = joblib.load(report["model"])
    np.testing.assert_array_equal(tracked_model.predict(load_iris().data), local_model.predict(load_iris().data))


def test_two_configurations_comparable(ml_config, tmp_path):
    output = tmp_path / "output"
    first = run_training(ml_config, output_dir=output)
    second = run_training(ml_config, output_dir=output, training_override="./training-regularized.yaml")
    assert first["run_id"] != second["run_id"]
    assert first["hyperparameters"]["C"] != second["hyperparameters"]["C"]
    assert first["seed"] == second["seed"]
    for file in ("dataset.json", "split_indices.json"):
        assert (Path(first["artifacts"]) / file).read_text() == (Path(second["artifacts"]) / file).read_text()
    client = MlflowClient(tracking_uri=first["tracking_uri"])
    experiment = client.get_experiment_by_name(first["experiment_name"])
    runs = client.search_runs([experiment.experiment_id], order_by=["metrics.validation_f1_macro DESC"])
    assert len(runs) == 2
    assert {float(run.data.params["C"]) for run in runs} == {1.0, 0.05}


def test_training_failure_marks_run_failed(ml_config, tmp_path):
    path = ml_config / "training.yaml"
    training = yaml.safe_load(path.read_text())
    training["model"]["parameters"]["max_iter"] = 1
    path.write_text(yaml.safe_dump(training))
    output = tmp_path / "output"
    with pytest.raises(TrainingError):
        run_training(ml_config, output_dir=output)
    client = MlflowClient(tracking_uri=f"sqlite:///{output / 'tracking/mlflow.db'}")
    experiment = client.get_experiment_by_name("signalflow-classification")
    run, = client.search_runs([experiment.experiment_id])
    assert run.info.status == "FAILED"
    report = json.loads(Path(client.download_artifacts(run.info.run_id, "report.json")).read_text())
    assert report["status"] == "FAILED"
    assert report["error_code"] == "TRAINING_ERROR"
    assert run.data.params["max_iter"] == "1"


def test_tracking_failure_does_not_train(ml_config, tmp_path, monkeypatch):
    from signalflow.ml.adapters.sklearn import SklearnTrainer
    train = Mock(side_effect=AssertionError("must not train without tracking"))
    monkeypatch.setattr(SklearnTrainer, "train", train)
    monkeypatch.setattr(MlflowClient, "log_artifacts", Mock(side_effect=RuntimeError("SECRET")))
    output = tmp_path / "output"
    with pytest.raises(TrackingError) as error:
        run_training(ml_config, output_dir=output)
    assert "SECRET" not in str(error.value)
    train.assert_not_called()
    client = MlflowClient(tracking_uri=f"sqlite:///{output / 'tracking/mlflow.db'}")
    experiment = client.get_experiment_by_name("signalflow-classification")
    run, = client.search_runs([experiment.experiment_id])
    assert run.info.status == "FAILED"


def test_cli_full_training_outside_repository(ml_config, tmp_path):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([
        sys.executable, "-m", "signalflow", "train", "--config-dir", str(ml_config),
        "--training", "./training-regularized.yaml", "--output-dir", str(tmp_path / "cli-output"),
    ], cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(root / "src")),
       text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "FINISHED"
    assert report["hyperparameters"]["C"] == 0.05
    assert Path(report["model"]).is_file()
    assert MlflowClient(tracking_uri=report["tracking_uri"]).get_run(report["run_id"]).info.status == "FINISHED"


def test_cli_error_is_typed_without_traceback(ml_config, tmp_path, capsys):
    from signalflow.cli import main
    code = main(["train", "--config-dir", str(ml_config), "--training", "../escape.yaml",
                 "--output-dir", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 1
    assert "CONFIG_PATH_TRAVERSAL" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "out").exists()


def test_complete_csv_training(ml_config, tmp_path):
    from sklearn.datasets import load_iris
    import csv

    data = load_iris()
    path = ml_config / "observations.csv"
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["a", "b", "c", "d", "label"])
        for index, (features, target) in enumerate(zip(data.data, data.target)):
            row = features.tolist()
            if index % 11 == 0:
                row[0] = ""
            writer.writerow([*row, str(target)])
    dataset_path = ml_config / "dataset.yaml"
    document = yaml.safe_load(dataset_path.read_text())
    document["source"] = {"type": "csv", "path": "./observations.csv", "target": "label"}
    dataset_path.write_text(yaml.safe_dump(document))
    report = run_training(ml_config, output_dir=tmp_path / "csv-output")
    assert report["status"] == "FINISHED"
    assert report["metrics"]["validation_accuracy"] > 0.8


def test_no_mlflow_global_session_changes(ml_config, tmp_path):
    import mlflow

    before = mlflow.get_tracking_uri()
    active_before = mlflow.active_run()
    run_training(ml_config, output_dir=tmp_path / "isolated-output")
    assert mlflow.get_tracking_uri() == before
    assert mlflow.active_run() is active_before
