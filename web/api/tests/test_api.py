"""Contrats HTTP et frontières de sécurité, sans réseau ni Docker."""
import asyncio
import json

import pytest
import yaml
pytest.importorskip('fastapi')
pytest.importorskip('httpx')
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from signalflow.ml.validation import validate_training_spec
from signalflow_api.app import create_app
from signalflow_api.auth import static_resolver_from_spec
from signalflow_api.dependencies import Principal, get_principal
from signalflow_api.settings import Settings

ADMIN_TOKEN = 'admin-token-123'


@pytest.fixture
def settings():
    return Settings(token_resolver=static_resolver_from_spec(f'admin:{ADMIN_TOKEN}'))


@pytest.fixture
def document():
    return {
        'training': {'schema_version': '0.0.1', 'seed': 42,
                     'model': {'type': 'logistic_regression', 'parameters': {'C': 1., 'max_iter': 1000}}},
        'dataset': {'schema_version': '0.0.1', 'source': {'type': 'builtin', 'name': 'iris'},
                    'split': {'validation': .2, 'test': .2, 'stratify': True}},
        'tracking': {'schema_version': '0.0.1', 'experiment_name': 'api', 'run_name': 'iris'},
    }


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, base_url='http://127.0.0.1',
                    headers={'authorization': f'Bearer {ADMIN_TOKEN}'}) as client:
        yield client


def test_health(client):
    assert client.get('/api/v1/health').json() == {
        'status': 'ok', 'service': 'signalflow-api', 'mode': 'authentication-required',
        'training_execution': False}


def test_schema_catalog_openapi(client, document):
    schema = client.get('/api/v1/training/schema').json()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document)
    catalog = client.get('/api/v1/algorithms').json()
    assert [a['id'] for a in catalog['algorithms']] == ['logistic_regression']
    assert set(catalog['algorithms'][0]['parameters']['properties']) == {'C', 'max_iter'}
    openapi = client.get('/openapi.json').json()
    assert set(openapi['paths']) == {
        '/api/v1/health', '/api/v1/training/schema', '/api/v1/training/validate',
        '/api/v1/training/import', '/api/v1/training/export',
        '/api/v1/algorithms', '/api/v1/trainings',
        '/api/v1/projects', '/api/v1/projects/{key}', '/api/v1/projects/{key}/members',
        '/api/v1/projects/{key}/configurations',
        '/api/v1/projects/{key}/configurations/compare',
        '/api/v1/projects/{key}/configurations/{version}',
        '/api/v1/projects/{key}/configurations/{version}/yaml',
        '/api/v1/projects/{key}/datasets',
        '/api/v1/projects/{key}/datasets/{identity}',
        '/api/v1/projects/{key}/datasets/{identity}/preview',
        '/api/v1/projects/{key}/datasets/{identity}/download',
        '/api/v1/projects/{key}/runs',
        '/api/v1/projects/{key}/runs/{identity}',
        '/api/v1/projects/{key}/runs/{identity}/cancel',
        '/api/v1/projects/{key}/runs/{identity}/events'}
    assert openapi['components']['schemas']['TrainingPayload']['additionalProperties'] is False
    for path in ('/api/v1/trainings', '/api/v1/training/validate'):
        assert openapi['paths'][path]['post']['responses']['422']['content']['application/json']['schema']['$ref'].endswith('ErrorResponse')


@pytest.mark.parametrize('regularization', [.05, 1., 100.])
def test_engine_parity_no_execution(client, document, regularization, monkeypatch, tmp_path):
    import signalflow.ml.runner as runner
    def forbidden(*args, **kwargs):
        pytest.fail('validation must never train')
    monkeypatch.setattr(runner, 'run_training_spec', forbidden)
    monkeypatch.chdir(tmp_path)
    document['training']['model']['parameters']['C'] = regularization
    result = client.post('/api/v1/training/validate', json=document)
    assert result.status_code == 200, result.text
    assert result.json() == {'valid': True, 'configuration': validate_training_spec(document).to_dict()}
    assert list(tmp_path.iterdir()) == []


def test_creation_is_explicitly_unavailable(client, document):
    response = client.post('/api/v1/trainings', json=document)
    assert response.status_code == 501
    assert response.json()['code'] == 'TRAINING_EXECUTION_NOT_IMPLEMENTED'
    assert 'id' not in response.json()
    assert client.post('/api/v1/trainings', json={}).status_code == 422


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(secret_key='SECRET'),
    lambda d: d['training'].update(unknown='SECRET'),
    lambda d: d['training']['model']['parameters'].update(C=0),
    lambda d: d['training'].update(seed=True),
    lambda d: d['training']['model'].update(type='pytorch'),
    lambda d: d['dataset']['split'].update(validation=.6, test=.6),
    lambda d: d.update(tracking={'uri': '/etc/SECRET'}),
])
def test_invalid_configs(client, document, mutation):
    mutation(document)
    response = client.post('/api/v1/training/validate', json=document)
    assert response.status_code == 422, response.text
    assert 'SECRET' not in response.text
    assert response.json()['request_id'] == response.headers['x-request-id']


@pytest.mark.parametrize('restricted', ['csv', 'directory', 'prometheus', 'logging'])
def test_paths_rejected_before_core(client, document, restricted, monkeypatch):
    import signalflow_api.services.training as service
    def forbidden(*args):
        pytest.fail('unsafe request reached core')
    monkeypatch.setattr(service, 'validate_training_spec', forbidden)
    if restricted == 'csv':
        document['dataset']['source'] = {'type': 'csv', 'path': '/etc/SECRET', 'target': 'label'}
    else:
        document['monitoring'] = {'schema_version': '0.0.1', 'enabled': True, 'interval_seconds': .1, 'max_samples': 100}
        if restricted == 'directory':
            document['monitoring']['directory'] = '/etc/SECRET'
        else:
            document['monitoring'][restricted] = {'enabled': True}
    response = client.post('/api/v1/training/validate', json=document)
    assert response.status_code == 422
    assert 'SECRET' not in response.text


@pytest.mark.parametrize('raw', [b'{', b'{} {}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}',
                               b'{"x":1,"x":2}', b'\xff', b'['*40+b'0'+b']'*40])
def test_malformed_json(client, raw):
    assert client.post('/api/v1/training/validate', content=raw,
                       headers={'content-type': 'application/json'}).status_code == 400


@pytest.mark.parametrize('body', [None, [], 'text', 42])
def test_invalid_envelope(client, body):
    assert client.post('/api/v1/training/validate', content=json.dumps(body),
                       headers={'content-type': 'application/json'}).status_code == 422


@pytest.mark.parametrize('headers,body,status', [
    ({'content-type': 'text/plain'}, b'{}', 415),
    ({'content-type': 'application/json', 'content-encoding': 'gzip'}, b'{}', 415),
    ({'content-type': 'application/json', 'content-length': '999999'}, b'{}', 413),
    ({'content-type': 'application/json', 'content-length': '1'}, b' '*65537, 413),
    ({'content-type': 'application/json', 'origin': 'https://evil.example'}, b'{}', 403),
    ({'content-type': 'application/json', 'host': 'evil.example'}, b'{}', 400),
])
def test_transport_restrictions(client, headers, body, status):
    response = client.post('/api/v1/training/validate', headers=headers, content=body)
    assert response.status_code == status
    assert 'access-control-allow-origin' not in response.headers


def test_same_origin(client, document):
    assert client.post('/api/v1/training/validate', json=document,
                       headers={'origin': 'http://127.0.0.1'}).status_code == 200


def test_auth_extension_and_errors(client, app, document, caplog):
    assert client.get('/api/v1/algorithms', headers={'authorization': 'Bearer SECRET'}).status_code == 401
    app.dependency_overrides[get_principal] = lambda: Principal('reader', frozenset({'training:read'}))
    assert client.get('/api/v1/algorithms').status_code == 200
    assert client.post('/api/v1/training/validate', json=document).status_code == 403
    def broken():
        raise RuntimeError('SECRET traceback /etc/private')
    app.dependency_overrides[get_principal] = broken
    response = client.get('/api/v1/algorithms')
    assert response.status_code == 500
    assert 'SECRET' not in response.text + caplog.text
    assert response.headers['cache-control'] == 'no-store'
    assert client.get('/missing').status_code == 404
    assert client.get('/api/v1/trainings').status_code == 405


def test_auth_required_fails_closed():
    """Sans résolveur, les routes métier restent fermées et health reste public."""
    with TestClient(create_app(Settings()), base_url='http://localhost') as anonymous:
        assert anonymous.get('/api/v1/health').status_code == 200
        assert anonymous.get('/api/v1/algorithms').status_code == 503
        assert anonymous.get('/api/v1/algorithms', headers={
            'authorization': 'Bearer absent'}).status_code == 503


def test_hashed_tokens_and_roles(client, document):
    """Les jetons sont hachés, liés à un rôle et refusés si invalides."""
    response = client.post('/api/v1/training/validate', json=document,
                           headers={'authorization': 'Bearer wrong-token-123'})
    assert response.status_code == 401
    assert response.json()['code'] == 'INVALID_TOKEN'
    viewer = static_resolver_from_spec(f'viewer:viewer-token-123')
    with TestClient(create_app(Settings(token_resolver=viewer)), base_url='http://127.0.0.1',
                    headers={'authorization': 'Bearer viewer-token-123'}) as limited:
        assert limited.get('/api/v1/algorithms').status_code == 200
        assert limited.post('/api/v1/trainings', json=document).status_code == 403


@pytest.mark.parametrize('kwargs', [{'max_request_bytes': 0}, {'body_timeout_seconds': 0}, {'max_json_depth': 0}])
def test_settings_bounds(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)


@pytest.mark.parametrize('mode,expected', [('chunks', 413), ('timeout', 408), ('disconnect', 400)])
def test_asgi_body_boundaries(mode, expected):
    app = create_app(Settings(max_request_bytes=1024, body_timeout_seconds=.01))
    messages = []
    async def receive():
        if mode == 'timeout':
            await asyncio.sleep(1)
        if mode == 'disconnect':
            return {'type': 'http.disconnect'}
        return {'type': 'http.request', 'body': b'x'*700, 'more_body': True}
    async def send(message):
        messages.append(message)
    scope = {'type': 'http', 'method': 'POST', 'path': '/api/v1/training/validate',
             'scheme': 'http', 'headers': [(b'host', b'localhost'), (b'content-type', b'application/json')]}
    asyncio.run(app(scope, receive, send))
    assert messages[0]['status'] == expected


def test_cli_loopback(monkeypatch):
    import uvicorn
    from signalflow_api.__main__ import main
    calls = []
    monkeypatch.setattr('sys.argv', ['signalflow-api'])
    monkeypatch.setattr(uvicorn, 'run', lambda *args, **kwargs: calls.append(kwargs))
    main()
    assert calls[0]['host'] == '127.0.0.1'
    assert calls[0]['proxy_headers'] is False


def test_monitoring_disabled_parity(client, document):
    document['monitoring'] = {
        'schema_version': '0.0.1', 'enabled': False, 'interval_seconds': .1,
        'max_samples': 100, 'logging': {'enabled': False}, 'prometheus': {'enabled': False}}
    response = client.post('/api/v1/training/validate', json=document)
    assert response.status_code == 200
    assert response.json()['configuration'] == validate_training_spec(document).to_dict()


def test_synthetic_parity_and_semantics(client, document):
    document['dataset']['source'] = {
        'type': 'synthetic', 'n_samples': 100, 'n_features': 10,
        'n_informative': 5, 'n_redundant': 2, 'n_classes': 2, 'random_state': 123}
    response = client.post('/api/v1/training/validate', json=document)
    assert response.status_code == 200, response.text
    assert response.json()['configuration'] == validate_training_spec(document).to_dict()
    document['dataset']['source']['n_informative'] = 10
    assert client.post('/api/v1/training/validate', json=document).status_code == 422


def test_yaml_import_export_roundtrip(client, document):
    """L'import YAML est validé par le même contrat que le JSON, puis exporté."""
    text = yaml.safe_dump(document, sort_keys=False)
    imported = client.post('/api/v1/training/import', json={'yaml': text})
    assert imported.status_code == 200, imported.text
    assert imported.json()['configuration'] == validate_training_spec(document).to_dict()
    exported = client.post('/api/v1/training/export', json=imported.json()['configuration'])
    assert exported.status_code == 200, exported.text
    assert yaml.safe_load(exported.json()['yaml']) == validate_training_spec(document).to_dict()


def test_yaml_import_rejects_duplicate_keys_without_leaking(client, document):
    text = yaml.safe_dump(document, sort_keys=False) + 'training: {schema_version: SECRET}\n'
    response = client.post('/api/v1/training/import', json={'yaml': text})
    assert response.status_code == 422
    assert 'SECRET' not in response.text


@pytest.mark.parametrize('host', ['localhost', 'localhost:8000', '[::1]', '[::1]:8000'])
def test_loopback_hosts(client, host):
    assert client.get('/api/v1/health', headers={'host': host}).status_code == 200


@pytest.mark.parametrize('host', ['localhost@evil.example', 'localhost:0', 'localhost:99999', 'localhost/path'])
def test_invalid_hosts(client, host):
    assert client.get('/api/v1/health', headers={'host': host}).status_code == 400
