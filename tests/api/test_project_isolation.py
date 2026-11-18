"""Isolation réelle entre utilisateurs et projets, versions et conflits."""
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from signalflow.persistence.repositories import TokenRepository, UserRepository
from signalflow.persistence.session import create_database_engine, init_database, session_scope
from signalflow_api.app import create_app
from signalflow_api.auth.identity import hash_token
from signalflow_api.settings import Settings

DOCUMENT = {
    'training': {'schema_version': '0.0.1', 'seed': 42,
                 'model': {'type': 'logistic_regression', 'parameters': {'C': 1., 'max_iter': 1000}}},
    'dataset': {'schema_version': '0.0.1', 'source': {'type': 'builtin', 'name': 'iris'},
                'split': {'validation': .2, 'test': .2, 'stratify': True}},
    'tracking': {'schema_version': '0.0.1', 'experiment_name': 'api', 'run_name': 'iris'},
}
TOKENS = {'root': 'root-token-123', 'alice': 'alice-token-123', 'bob': 'bob-token-123'}


@pytest.fixture
def clients(tmp_path):
    url = f'sqlite:///{tmp_path}/app.db'
    engine = create_database_engine(url)
    init_database(engine)
    with session_scope(sessionmaker(bind=engine, expire_on_commit=False)) as session:
        users = UserRepository(session)
        for subject, role in (('root', 'admin'), ('alice', 'editor'), ('bob', 'editor')):
            user = users.ensure(subject, role)
            TokenRepository(session).create(user.id, hash_token(TOKENS[subject]), subject)
    engine.dispose()

    def build(subject):
        return TestClient(create_app(Settings(database_url=url)), base_url='http://127.0.0.1',
                          headers={'authorization': f'Bearer {TOKENS[subject]}'})

    return build


def _create_project(client, key, name):
    response = client.post('/api/v1/projects', json={'key': key, 'name': name})
    assert response.status_code == 201, response.text
    return response.json()


def test_users_only_see_their_projects(clients):
    root, alice, bob = clients('root'), clients('alice'), clients('bob')
    _create_project(root, 'projet-a', 'A')
    _create_project(root, 'projet-b', 'B')
    assert root.post('/api/v1/projects/projet-a/members', json={
        'subject': 'alice', 'role': 'editor'}).status_code == 201
    assert root.post('/api/v1/projects/projet-b/members', json={
        'subject': 'bob', 'role': 'editor'}).status_code == 201
    assert [p['key'] for p in alice.get('/api/v1/projects').json()] == ['projet-a']
    assert [p['key'] for p in bob.get('/api/v1/projects').json()] == ['projet-b']
    assert alice.get('/api/v1/projects/projet-b').status_code == 404
    assert bob.get('/api/v1/projects/projet-a').status_code == 404


def test_versions_conflict_history_comparison_and_export(clients):
    root, alice, bob = clients('root'), clients('alice'), clients('bob')
    _create_project(root, 'projet-a', 'A')
    root.post('/api/v1/projects/projet-a/members', json={'subject': 'alice', 'role': 'editor'})
    root.post('/api/v1/projects/projet-a/members', json={'subject': 'bob', 'role': 'viewer'})

    first = alice.post('/api/v1/projects/projet-a/configurations', json={'configuration': DOCUMENT})
    assert first.status_code == 201, first.text
    assert first.json()['version'] == 1
    assert first.json()['parent_version'] is None
    changed = dict(DOCUMENT)
    changed['training'] = dict(DOCUMENT['training'])
    changed['training']['seed'] = 7
    second = alice.post('/api/v1/projects/projet-a/configurations', json={
        'expected_version': 1, 'configuration': changed})
    assert second.status_code == 201, second.text
    assert second.json()['version'] == 2
    assert second.json()['parent_version'] == 1

    stale = alice.post('/api/v1/projects/projet-a/configurations', json={
        'expected_version': 1, 'configuration': DOCUMENT})
    assert stale.status_code == 409
    assert stale.json()['code'] == 'VERSION_CONFLICT'
    history = alice.get('/api/v1/projects/projet-a/configurations')
    assert history.status_code == 200
    assert [item['version'] for item in history.json()['versions']] == [1, 2]

    diff = alice.get('/api/v1/projects/projet-a/configurations/compare?source=1&target=2')
    assert diff.status_code == 200
    assert diff.json()['changed'] == ['$.training.seed']

    exported = alice.get('/api/v1/projects/projet-a/configurations/2/yaml')
    assert exported.status_code == 200
    assert exported.headers['content-type'].startswith('application/x-yaml')
    assert yaml.safe_load(exported.text)['training']['seed'] == 7

    assert bob.get('/api/v1/projects/projet-a/configurations/2').status_code == 200
    # L'auteur est celui qui a publié la version, pas le lecteur connecté.
    assert bob.get('/api/v1/projects/projet-a/configurations/2').json()['author'] == 'alice'
    assert [row['author'] for row in root.get('/api/v1/projects/projet-a/configurations').json()['versions']] == ['alice', 'alice']
    denied = bob.post('/api/v1/projects/projet-a/configurations', json={'configuration': DOCUMENT})
    assert denied.status_code == 403
