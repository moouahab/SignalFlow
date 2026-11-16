"""Contrats, persistance, unités, concurrence et journalisation sans Docker/réseau."""
import concurrent.futures
import json
from pathlib import Path
import time

import pytest
import yaml

pytest.importorskip("prometheus_client")

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.openmetrics.exposition import generate_latest as openmetrics
from prometheus_client.openmetrics.parser import text_string_to_metric_families

from signalflow.ml.config import load_run_configuration
from signalflow.ml.registry import create_registry
from signalflow.config.loader.errors import ConfigError
from signalflow.observability.metrics import MetricsPublisher, read_state
from signalflow.observability.exporter import SnapshotCollector, application
from signalflow.observability.logging import JsonEvents
from signalflow.observability.session import TelemetrySession, destination, ObservabilityConfigError
from signalflow.observability.storage import locked


def observation():
    return {'complete': True, 'metrics': {'process_cpu_seconds': 0.25,
            'process_rss_sampled_max_bytes': 123456, 'stage_model_fit_seconds': 0.04}}


def publish_one(directory):
    publisher = MetricsPublisher(Path(directory))
    publisher.publish('train', 'started')
    publisher.publish('train', 'succeeded', 0.1, observation())


@pytest.mark.parametrize('field,value', [
    ('enabled', 'yes'), ('directory', '../outside'), ('directory', '/tmp/unsafe'),
    ('prometheus', {'enabled': True, 'url': 'https://example.com'}),
    ('logging', {'enabled': True, 'max_bytes': 0}),
    ('logging', {'enabled': True, 'backups': 0}),
    ('logging', {'enabled': True, 'command': 'touch secret'}),
])
def test_configuration_rejects_invalid_values(telemetry_config, field, value):
    path = telemetry_config / 'monitoring.yaml'
    data = yaml.safe_load(path.read_text())
    data[field] = value
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError):
        load_run_configuration(telemetry_config, registry=create_registry())


def test_destination_rejects_external_symlink(tmp_path, monkeypatch):
    monkeypatch.delenv('SIGNALFLOW_OBSERVABILITY_DIR', raising=False)
    working = tmp_path / 'working'
    working.mkdir()
    (working / 'out').symlink_to(tmp_path)
    monkeypatch.chdir(working)
    with pytest.raises(ObservabilityConfigError):
        destination({'directory': './out'})


def test_metrics_survive_process_exit_and_concurrency(tmp_path):
    # Une publication par processus, sans socket ni serveur persistant dans les jobs.
    with concurrent.futures.ProcessPoolExecutor(max_workers=3) as pool:
        list(pool.map(publish_one, [str(tmp_path)] * 6))
    state = read_state(tmp_path / 'metrics.json')
    assert state['counts']['train'] == {'started': 6, 'succeeded': 6, 'failed': 0}
    assert state['cpu_seconds'] == 1.5
    assert state['histograms']['train']['count'] == 6
    assert not list(tmp_path.glob('.snapshot-*'))


def test_prometheus_and_openmetrics_units_and_labels(tmp_path):
    publish_one(str(tmp_path))
    registry = CollectorRegistry()
    registry.register(SnapshotCollector(tmp_path))
    text = generate_latest(registry).decode()
    assert 'signalflow_trainings_succeeded_total 1.0' in text
    assert 'signalflow_process_cpu_seconds_total 0.25' in text
    assert 'signalflow_process_rss_sampled_max_bytes 123456.0' in text
    assert 'signalflow_phase_duration_seconds_count{phase="model_fit"} 1.0' in text
    assert 'run_id' not in text and 'execution_id' not in text
    formatted = openmetrics(registry).decode()
    assert formatted.endswith('# EOF\n')
    families = list(text_string_to_metric_families(formatted))
    assert any(f.name == 'signalflow_train_duration_seconds' for f in families)


def test_wsgi_negotiates_openmetrics(tmp_path):
    publish_one(str(tmp_path))
    headers = []
    app = application(tmp_path)
    body = b''.join(app({'PATH_INFO': '/metrics', 'REQUEST_METHOD': 'GET', 'QUERY_STRING': '',
                         'HTTP_ACCEPT': 'application/openmetrics-text; version=1.0.0'},
                        lambda status, values: headers.extend(values)))
    assert 'application/openmetrics-text' in dict(headers)['Content-Type']
    assert body.endswith(b'# EOF\n')


def test_corrupt_snapshot_is_not_reset_or_hidden(tmp_path):
    path = tmp_path / 'metrics.json'
    path.write_text('{broken')
    with pytest.raises(ValueError):
        MetricsPublisher(tmp_path).publish('train', 'started')
    assert path.read_text() == '{broken'
    registry = CollectorRegistry()
    registry.register(SnapshotCollector(tmp_path))
    text = generate_latest(registry).decode()
    assert 'signalflow_snapshot_read_success 0.0' in text
    assert 'signalflow_trainings_succeeded_total' not in text


def test_lock_timeout_and_release(tmp_path):
    with locked(tmp_path, '.metrics.lock'):
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            with locked(tmp_path, '.metrics.lock', timeout=0.02):
                pass
        assert time.monotonic() - started < 1
    MetricsPublisher(tmp_path).publish('train', 'started')


def test_json_events_rotation_and_no_run_label(tmp_path):
    logger = JsonEvents(tmp_path, max_bytes=600, backups=2)
    for _ in range(20):
        logger.write('stage_finished', 'execution-123', stage='model_fit', run_id='mlflow-123')
    files = list(tmp_path.glob('events.jsonl*'))
    assert len(files) == 3
    for path in files:
        for line in path.read_text().splitlines():
            event = json.loads(line)
            assert event['timestamp'].endswith('+00:00')
            assert event['execution_id'] == 'execution-123'
            assert event['run_id'] == 'mlflow-123'
            assert event['stage'] == 'model_fit'
            assert set(event) == {'timestamp','level','component','stage','event','execution_id','run_id','message'}


def test_disabled_telemetry_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv('SIGNALFLOW_OBSERVABILITY_DIR', str(tmp_path / 'never-created'))
    session = TelemetrySession(None, 'id')
    session.start()
    session.event('event')
    session.finish(True, 1)
    assert not (tmp_path / 'never-created').exists()


def test_publication_failure_is_visible_and_nonfatal(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('SIGNALFLOW_OBSERVABILITY_DIR', str(tmp_path))
    def fail(*args, **kwargs):
        raise OSError('SECRET must not be logged')
    monkeypatch.setattr(MetricsPublisher, 'publish', fail)
    session = TelemetrySession({'prometheus': {'enabled': True}}, 'id')
    session.start()
    session.finish(True, 0.1)
    assert session.errors == ['metrics_publish_failed']
    error = capsys.readouterr().err
    assert error.count('metrics_publish_failed') == 1
    assert 'SECRET' not in error


def test_finish_is_idempotent_and_failure_counts(tmp_path, monkeypatch):
    monkeypatch.setenv('SIGNALFLOW_OBSERVABILITY_DIR', str(tmp_path))
    session = TelemetrySession({'prometheus': {'enabled': True}}, 'id')
    session.start()
    session.finish(False, 0.1)
    session.finish(False, 0.2)
    assert read_state(tmp_path/'metrics.json')['counts']['train']['failed'] == 1


@pytest.mark.parametrize('corruption', ['nan', 'labels', 'buckets', 'nested_types'])
def test_corrupt_values_do_not_produce_invalid_exposition(tmp_path, corruption):
    publish_one(str(tmp_path))
    path = tmp_path/'metrics.json'
    state = json.loads(path.read_text())
    if corruption == 'nan':
        state['cpu_seconds'] = float('nan')
    elif corruption == 'labels':
        state['counts']['train']['unique-run-id'] = 1
    elif corruption == 'nested_types':
        state['counts']['train'] = ['started', 'succeeded', 'failed']
    else:
        state['histograms']['train']['buckets'] = []
    path.write_text(json.dumps(state))
    registry = CollectorRegistry()
    registry.register(SnapshotCollector(tmp_path))
    text = generate_latest(registry).decode()
    assert 'signalflow_snapshot_read_success 0.0' in text
    assert 'unique-run-id' not in text
