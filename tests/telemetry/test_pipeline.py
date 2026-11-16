"""Intégration ML réelle avec publications locales et services distants inaccessibles."""
import json
from pathlib import Path
import socket
import threading

import pytest
import yaml
pytest.importorskip("mlflow")
pytest.importorskip("sklearn")
pytest.importorskip("psutil")

from mlflow.tracking import MlflowClient

from signalflow.ml.runner import run_training
from signalflow.ml.errors import TrainingError
from signalflow.observability.metrics import read_state, MetricsPublisher


def test_training_offline_metrics_logs_and_mlflow(telemetry_config, tmp_path, monkeypatch):
    def offline(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', offline)
    report = run_training(telemetry_config, output_dir=tmp_path/'run')
    assert report['status'] == 'FINISHED'
    assert report['observability']['warnings'] == []
    assert report['monitoring']['complete']
    assert not any(t.name == 'signalflow-monitor' for t in threading.enumerate())
    state = read_state(tmp_path/'telemetry/metrics.json')
    assert state['counts']['train']['succeeded'] == 1
    assert state['rss_bytes'] == report['monitoring']['metrics']['process_rss_sampled_max_bytes']
    events = [json.loads(line) for line in (tmp_path/'telemetry/events.jsonl').read_text().splitlines()]
    assert {'train_started','configuration_loaded','configuration_validated','train_succeeded'} <= {e['event'] for e in events}
    assert {'dataset_load','model_fit','predict_evaluate','artifact_save'} <= {e['stage'] for e in events}
    assert any(e['run_id'] == report['run_id'] for e in events)
    client = MlflowClient(tracking_uri=report['tracking_uri'])
    assert client.get_run(report['run_id']).info.status == 'FINISHED'
    assert client.get_run(report['run_id']).data.metrics['validation_accuracy'] == report['metrics']['validation_accuracy']


def test_local_monitoring_disabled_but_export_enabled(telemetry_config, tmp_path):
    path = telemetry_config/'monitoring.yaml'
    settings = yaml.safe_load(path.read_text())
    settings['enabled'] = False
    path.write_text(yaml.safe_dump(settings))
    report = run_training(telemetry_config, output_dir=tmp_path/'run')
    assert report['status'] == 'FINISHED' and 'monitoring' not in report
    state = read_state(tmp_path/'telemetry/metrics.json')
    assert state['counts']['train']['succeeded'] == 1
    assert state['monitoring_enabled'] == 0


def test_disk_failure_does_not_fail_valid_training(telemetry_config, tmp_path, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError('SECRET')
    monkeypatch.setattr(MetricsPublisher, 'publish', fail)
    report = run_training(telemetry_config, output_dir=tmp_path/'run')
    assert report['status'] == 'FINISHED'
    assert report['observability']['warnings'] == ['metrics_publish_failed']
    assert 'SECRET' not in capsys.readouterr().err
    client = MlflowClient(tracking_uri=report['tracking_uri'])
    assert client.get_run(report['run_id']).info.status == 'FINISHED'


def test_failed_training_logged_and_counted(telemetry_config, tmp_path):
    path = telemetry_config/'training.yaml'
    training = yaml.safe_load(path.read_text())
    training['model']['parameters']['max_iter'] = 1
    path.write_text(yaml.safe_dump(training))
    with pytest.raises(TrainingError):
        run_training(telemetry_config, output_dir=tmp_path/'run')
    state = read_state(tmp_path/'telemetry/metrics.json')
    assert state['counts']['train']['failed'] == 1
    events = [json.loads(line) for line in (tmp_path/'telemetry/events.jsonl').read_text().splitlines()]
    assert events[-1]['event'] == 'train_failed'
    assert any(e['event']=='stage_failed' and e['stage']=='model_fit' for e in events)
    assert not any(t.name == 'signalflow-monitor' for t in threading.enumerate())
