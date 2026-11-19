"""Validation fonctionnelle exécutée uniquement dans compose.zenml-test.yaml."""
import concurrent.futures
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

import requests
from sqlalchemy import func, select
from signalflow.persistence.models import JobAttempt
from signalflow.persistence.repositories import UserRepository, TokenRepository
from signalflow.persistence.session import create_session_factory, session_scope
from signalflow_api.auth.identity import hash_token

STATE = Path('/data/check/state.json')
FACTORY = create_session_factory(os.environ['SIGNALFLOW_DATABASE_URL'])


def call(method, path, token, expected=200, **kwargs):
    headers = {'Host': '127.0.0.1', 'Authorization': f'Bearer {token}'}
    headers.update(kwargs.pop('headers', {}))
    response = requests.request(method, 'http://api:8000/api/v1' + path,
                                headers=headers, timeout=10, **kwargs)
    assert response.status_code == expected, (path, response.status_code, response.text)
    return response.json()


def wait_api():
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            call('GET', '/health', '')
            return
        except (requests.RequestException, AssertionError):
            time.sleep(1)
    raise AssertionError('API unavailable')


def wait_job(path, token, target):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        row = call('GET', path, token)
        if row['state'] in ('succeeded', 'failed', 'cancelled') and row['tracking_sync'] == 'synced':
            assert row['state'] == target, row
            return row
        time.sleep(.5)
    raise AssertionError(('job timeout', row))


def check_zenml(row, state):
    directory = Path('/data/jobs') / row['attempt_id']
    references = sorted((directory / 'output/orchestration').glob('*/execution.json'))
    assert references, 'référence ZenML absente de la tentative'
    execution_id = json.loads(references[0].read_text())['execution_id']
    # ZenML 0.98.0 recalcule le statut d'un pipeline statique depuis ses étapes :
    # un CANCELLED demandé est ignoré, la fin d'annulation est STOPPED.
    expected = {'succeeded': 'completed', 'cancelled': 'stopped', 'failed': 'failed'}[state]
    code = '''
from zenml.client import Client
from signalflow.orchestration.zenml_adapter import ZenMLTrainer
from signalflow.ml.errors import TrainingError
from types import SimpleNamespace
from unittest.mock import patch
import sys
runs = Client().list_pipeline_runs().items
assert len(runs) == 1, runs
run = Client().get_pipeline_run(runs[0].id)
assert str(run.id) == sys.argv[2], (run.id, sys.argv[2])
assert run.status.value == sys.argv[1], run.status
if sys.argv[1] == 'completed':
    assert len(run.steps) == 6
    assert all(step.status.value == 'completed' for step in run.steps.values())
else:
    assert all(step.status.is_finished for step in run.steps.values())
# Test explicite de la limite actuelle, sans prétendre lancer Kubernetes.
remote = SimpleNamespace(active_stack=SimpleNamespace(orchestrator=SimpleNamespace(flavor='kubernetes')))
with patch('zenml.client.Client', return_value=remote):
    try:
        ZenMLTrainer('/tmp/not-created').train({}, {})
    except TrainingError as error:
        assert 'local' in str(error)
    else:
        raise AssertionError('remote orchestrator unexpectedly accepted')
print('ZENML_TERMINAL_STATUS_OK', sys.argv[1])
'''
    environment = dict(os.environ, ZENML_CONFIG_PATH=str(directory / 'zenml'),
                       ZENML_ANALYTICS_OPT_IN='false', ZENML_ENABLE_RICH_TRACEBACK='false')
    subprocess.run(['/opt/zenml/bin/python', '-c', code, expected, execution_id],
                   env=environment, check=True, timeout=90)


def check_mlflow(row, status):
    database = Path('/data/jobs') / row['attempt_id'] / 'output/tracking/mlflow.db'
    code = '''
from mlflow.tracking import MlflowClient
import sys
client = MlflowClient(tracking_uri='sqlite:///' + sys.argv[1])
runs = client.search_runs([exp.experiment_id for exp in client.search_experiments()])
assert len(runs) == 1, runs
assert runs[0].info.status == sys.argv[2], runs[0].info.status
print('MLFLOW_TERMINAL_STATUS_OK', sys.argv[2])
'''
    subprocess.run([sys.executable, '-c', code, str(database), status], check=True, timeout=90)


def main():
    wait_api()
    if '--verify-restart' in sys.argv:
        state = json.loads(STATE.read_text())
        row = call('GET', state['path'], state['token'])
        assert row['state'] == 'succeeded'
        assert row['result']['orchestration']['execution_id'] == state['execution_id']
        with session_scope(FACTORY) as session:
            assert session.scalar(select(func.count()).select_from(JobAttempt).where(JobAttempt.job_id == row['id'])) == 1
        assert len(list((Path('/data/jobs') / row['attempt_id'] / 'output/runs').iterdir())) == 1
        print('API_WORKER_RESTART_PERSISTENCE_OK', flush=True)
        return
    token = secrets.token_urlsafe(32)
    key = 'docker-' + secrets.token_hex(4)
    with session_scope(FACTORY) as session:
        user = UserRepository(session).ensure(key, 'admin')
        TokenRepository(session).create(user.id, hash_token(token), 'docker-validation')
    call('POST', '/projects', token, 201, json={'key': key, 'name': 'Docker ZenML validation'})
    project = '/projects/' + key
    document = {
        'training': {'schema_version': '0.0.1', 'seed': 42,
            'model': {'type': 'logistic_regression', 'parameters': {'C': 1., 'max_iter': 1000}}},
        'dataset': {'schema_version': '0.0.1', 'source': {'type': 'builtin', 'name': 'iris'},
                    'split': {'validation': .2, 'test': .2, 'stratify': True}},
        'tracking': {'schema_version': '0.0.1', 'experiment_name': 'docker', 'run_name': 'csv'},
    }
    call('POST', project + '/configurations', token, 201, json={'configuration': document})
    csv = 'x,label\n' + ''.join(f'{i},{"a" if i < 50 else "b"}\n' for i in range(100))
    dataset = call('POST', project + '/datasets', token, 201,
                   data={'name': 'csv', 'target': 'label'}, files={'file': ('data.csv', csv, 'text/csv')})
    payload = {'configuration_version': 1, 'dataset_id': dataset['id']}
    def submit(_):
        return call('POST', project + '/runs', token, 202, json=payload, headers={'Idempotency-Key': 'same'})
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        submissions = list(pool.map(submit, range(2)))
    assert submissions[0]['id'] == submissions[1]['id']
    path = project + '/runs/' + submissions[0]['id']
    row = wait_job(path, token, 'succeeded')
    assert row['result']['metrics']['test_accuracy'] >= .8
    with session_scope(FACTORY) as session:
        assert session.scalar(select(func.count()).select_from(JobAttempt).where(JobAttempt.job_id == row['id'])) == 1
    check_zenml(row, 'succeeded')
    check_mlflow(row, 'FINISHED')
    print('CSV_TRAINING_SIX_STEPS_IDEMPOTENCY_TWO_WORKERS_OK', flush=True)
    STATE.write_text(json.dumps({'path': path, 'token': token,
        'execution_id': row['result']['orchestration']['execution_id']}))
    STATE.chmod(0o600)
    cancelled = call('POST', project + '/runs', token, 202, json=payload,
                     headers={'Idempotency-Key': 'cancel'})
    path = project + '/runs/' + cancelled['id']
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        row = call('GET', path, token)
        if row['attempt_id'] and list((Path('/data/jobs') / row['attempt_id'] / 'output/orchestration').glob('*/execution.json')):
            break
        time.sleep(.05)
    else:
        raise AssertionError('ZenML did not start before cancellation')
    call('POST', path + '/cancel', token, json={})
    
    row = wait_job(path, token, 'cancelled')
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        row = call('GET', path, token)
        if row['tracking_sync'] == 'synced':
            break
        time.sleep(0.2)
    else:
        raise AssertionError(
            "Synchronisation de l'outbox non terminée"
        )

    check_zenml(row, 'cancelled')
    check_mlflow(row, 'KILLED')
    print('ACTIVE_PIPELINE_CANCELLATION_AND_RECONCILIATION_OK', flush=True)


if __name__ == '__main__':
    main()
