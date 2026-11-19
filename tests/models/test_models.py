"""Parcours réel MLflow, refus d'artefacts, isolation et reprise de promotion."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import select

from tests.api.test_project_isolation import clients, DOCUMENT
from signalflow.persistence.models import Job, ModelRegistration
from signalflow.persistence.session import session_scope
from signalflow_api.experiments.comparison import compare
from signalflow_api.jobs.executor import safe_result
from signalflow_api.models.mlflow_registry import MLflowRegistry

BASE = '/api/v1/projects/models'


@pytest.fixture
def setup(clients, tmp_path):
    root = clients('root')
    assert root.post('/api/v1/projects', json={'key': 'models', 'name': 'Models'}).status_code == 201
    assert root.post(BASE + '/configurations', json={'configuration': DOCUMENT}).status_code == 201
    root.app.state.settings = replace(root.app.state.settings,
        job_root=str(tmp_path / 'jobs'), registry_root=str(tmp_path / 'registry'))
    return root, clients, tmp_path


def create_run(root, tmp_path, *, train=True):
    from signalflow.ml.contracts import ExecutionContext
    from signalflow.ml.runner import run_training_spec
    from signalflow.ml.validation import validate_training_spec
    response = root.post(BASE + '/runs', json={'configuration_version': 1},
                         headers={'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 202, response.text
    identity = response.json()['id']
    if train:
        attempt = uuid.uuid4().hex
        report = run_training_spec(validate_training_spec(DOCUMENT), ExecutionContext(
            tmp_path / 'jobs' / attempt / 'output')).to_dict()
        with session_scope(root.app.state.session_factory) as session:
            job = session.get(Job, identity)
            job.state, job.tracking_sync, job.attempt_id = 'succeeded', 'synced', attempt
            job.result = safe_result(report)
        return identity, report
    return identity, None


def promotion(identity, name='iris'):
    return {'name': name, 'run_id': identity, 'reason': 'Validation humaine des métriques de validation.'}


def test_real_promotion_idempotency_resolution_and_alias_race(setup, monkeypatch):
    root, clients, tmp_path = setup
    identity, report = create_run(root, tmp_path)
    # Une promotion ne désérialise jamais le joblib du run.
    import joblib
    monkeypatch.setattr(joblib, 'load', lambda *a, **kw: pytest.fail('untrusted deserialization'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: root.post(BASE + '/models', json=promotion(identity)), range(2)))
    response = responses[0]
    assert response.status_code == 201, response.text
    first = response.json()
    assert responses[1].status_code == 201 and responses[1].json() == first
    assert first['version'] == '1' and first['status'] == 'READY'
    assert first['provenance']['dataset_sha256'] == report['model_package']['provenance']['dataset_sha256']
    assert root.post(BASE + '/models', json=promotion(identity)).json() == first
    assert len(root.get(BASE + '/models/iris/versions').json()) == 1
    assert clients('bob').post(BASE + '/models', json=promotion(identity)).status_code == 404
    assert clients('bob').get(BASE + '/models/iris/versions').status_code == 404
    assert root.post(BASE + '/members', json={'subject': 'alice', 'role': 'viewer'}).status_code == 201
    assert clients('alice').post(BASE + '/models', json=promotion(identity)).status_code == 403
    # Deux déplacements portant sur le même état initial : un seul est accepté.
    payload = {'version': '1', 'expected_version': None, 'reason': 'Choix validé'}
    def move(_):
        return root.put(BASE + '/models/iris/aliases/champion', json=payload).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(move, range(2))) == [200, 409]
    assert root.get(BASE + '/models/iris/aliases/champion').json()['version'] == '1'
    assert len(root.get(BASE + '/models/iris/audit').json()) == 1
    assert root.put(BASE + '/models/iris/aliases/champion', json={
        'version': '1', 'reason': 'Sans précondition'}).status_code == 422
    assert root.put(BASE + '/models/iris/aliases/champion', json={
        **payload, 'version': '2', 'expected_version': '1'}).status_code == 404
    assert 'tmp/' not in json.dumps(first)
    # Résolution models:/ depuis le registre central et prédictions réelles.
    import mlflow
    import mlflow.pyfunc
    from sklearn.datasets import load_iris
    old = mlflow.get_registry_uri()
    old_tracking = mlflow.get_tracking_uri()
    try:
        uri = f'sqlite:///{tmp_path / "registry/registry.db"}'
        mlflow.set_tracking_uri(uri)
        mlflow.set_registry_uri(uri)
        model = mlflow.pyfunc.load_model(first['uri'])
        assert model.predict(load_iris().data[:5]).shape == (5,)
    finally:
        mlflow.set_tracking_uri(old_tracking)
        mlflow.set_registry_uri(old)


def test_promotion_recovers_after_external_commit(setup, monkeypatch):
    root, _, tmp_path = setup
    identity, _ = create_run(root, tmp_path)
    original = MLflowRegistry.register
    def interrupted(self, *args):
        original(self, *args)
        raise RuntimeError('connection lost after commit')
    monkeypatch.setattr(MLflowRegistry, 'register', interrupted)
    assert root.post(BASE + '/models', json=promotion(identity)).status_code == 503
    with session_scope(root.app.state.session_factory) as session:
        assert session.scalar(select(ModelRegistration)).version is None
    monkeypatch.setattr(MLflowRegistry, 'register', original)
    result = root.post(BASE + '/models', json=promotion(identity))
    assert result.status_code == 201, result.text
    assert result.json()['version'] == '1'
    assert len(root.get(BASE + '/models/iris/versions').json()) == 1


def test_reject_untrusted_historical_and_unfinished(setup):
    root, _, tmp_path = setup
    queued, _ = create_run(root, tmp_path, train=False)
    assert root.post(BASE + '/models', json=promotion(queued)).json()['code'] == 'RUN_NOT_SUCCEEDED'
    identity, report = create_run(root, tmp_path)
    package = Path(report['artifacts']) / 'mlflow_model'
    original = (package / 'model.pkl').read_bytes()
    (package / 'model.pkl').write_bytes(b'not a trusted model')
    response = root.post(BASE + '/models', json=promotion(identity))
    assert response.status_code == 409 and response.json()['code'] == 'MODEL_UNTRUSTED'
    outside = tmp_path / 'outside.pkl'
    outside.write_bytes(original)
    (package / 'model.pkl').unlink()
    (package / 'model.pkl').symlink_to(outside)
    assert root.post(BASE + '/models', json=promotion(identity)).json()['code'] == 'MODEL_UNTRUSTED'
    with session_scope(root.app.state.session_factory) as session:
        job = session.get(Job, identity)
        result = dict(job.result)
        result.pop('model_package')
        job.result = result
    assert root.get(BASE + '/runs/' + identity).status_code == 200
    assert root.post(BASE + '/models', json=promotion(identity)).json()['code'] == 'MODEL_PACKAGE_REQUIRED'
    assert root.post(BASE + '/models', json={**promotion(identity), 'source': '/tmp/evil'}).status_code == 422


def test_comparison_authorization_and_legacy(setup):
    root, clients, tmp_path = setup
    one, _ = create_run(root, tmp_path, train=False)
    two, _ = create_run(root, tmp_path, train=False)
    payload = {'run_ids': [one, two]}
    result = root.post(BASE + '/experiments/compare', json=payload)
    assert result.status_code == 200
    assert not result.json()['comparable']
    assert {'METRIC_MISSING', 'RUN_NOT_SUCCEEDED', 'PROVENANCE_MISSING'} <= {
        warning['code'] for warning in result.json()['warnings']}
    assert clients('bob').post(BASE + '/experiments/compare', json=payload).status_code == 404
    assert root.post(BASE + '/experiments/compare', json={'run_ids': [one, 'f' * 32]}).status_code == 404
    assert root.post(BASE + '/experiments/compare', json={'run_ids': [one, one]}).status_code == 422
    # Même un administrateur ne peut mélanger les références de deux projets.
    assert root.post('/api/v1/projects', json={'key': 'other', 'name': 'Other'}).status_code == 201
    other = '/api/v1/projects/other'
    assert root.post(other + '/configurations', json={'configuration': DOCUMENT}).status_code == 201
    foreign = root.post(other + '/runs', json={'configuration_version': 1},
                        headers={'Idempotency-Key': 'foreign'}).json()['id']
    assert root.post(BASE + '/experiments/compare', json={'run_ids': [one, foreign]}).status_code == 404
    assert root.post(BASE + '/models', json=promotion(foreign)).status_code == 404


@pytest.mark.parametrize('field,code', [('dataset_sha256', 'DATASET_MISMATCH'),
    ('split_sha256', 'SPLIT_MISMATCH'), ('environment_sha256', 'ENVIRONMENT_MISMATCH')])
def test_comparison_conditions_and_test_selection(field, code):
    provenance = {'dataset_sha256': 'a', 'split_sha256': 'b', 'environment_sha256': 'c'}
    job = SimpleNamespace(id='a', state='succeeded', configuration_id='c', dataset_id=None,
        snapshot=DOCUMENT, result={'metrics': {'validation_accuracy': .9, 'test_accuracy': .8},
                                   'model_package': {'provenance': provenance}})
    other = deepcopy(job)
    other.id = 'b'
    assert compare([job, other], 'validation_accuracy')['comparable']
    other.result['model_package']['provenance'][field] = 'different'
    assert code in {w['code'] for w in compare([job, other], 'validation_accuracy')['warnings']}
    assert 'TEST_METRIC_NOT_FOR_SELECTION' in {
        w['code'] for w in compare([job, other], 'test_accuracy')['warnings']}
