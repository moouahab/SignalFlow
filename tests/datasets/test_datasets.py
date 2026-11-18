import io
import os
import time

import pytest
from fastapi.testclient import TestClient

from tests.api.test_project_isolation import clients
from signalflow_api.datasets.storage import Storage
from signalflow_api.datasets.validation import validate
from signalflow_api.errors import ApiError
from signalflow_api.app import create_app
from signalflow_api.settings import Settings

CSV = b'x,label\n1,a\n2,b\n3,a\n'
URL = '/api/v1/projects/demo/datasets'


@pytest.fixture
def setup(clients, tmp_path):
    root = clients('root')
    settings = root.app.state.settings
    root.app.state.settings = Settings(database_url=settings.database_url, dataset_root=str(tmp_path / 'csv'))
    # Rebuild so middleware sees the same settings.
    root = TestClient(create_app(root.app.state.settings), base_url='http://127.0.0.1', headers=root.headers)
    root.post('/api/v1/projects', json={'key': 'demo', 'name': 'Demo'})
    return root, clients, tmp_path / 'csv'


def upload(client, data=CSV, filename='data.csv', mime='text/csv'):
    return client.post(URL, data={'name': 'train', 'target': 'label'}, files={'file': (filename, data, mime)})


def test_versions_restart_preview_and_permissions(setup):
    root, clients, path = setup
    first = upload(root)
    assert first.status_code == 201, first.text
    a = first.json()
    b = upload(root, CSV.replace(b'3,a', b'4,b')).json()
    assert (a['version'], b['version']) == (1, 2)
    assert a['sha256'] != b['sha256']
    assert 'path' not in a
    assert root.get(f'{URL}/{a["id"]}/download').content == CSV
    assert root.get(f'{URL}/{a["id"]}/preview?limit=1').json()['rows'] == [['1', 'a']]
    restarted = TestClient(create_app(root.app.state.settings), base_url='http://127.0.0.1', headers=root.headers)
    assert len(restarted.get(URL).json()) == 2
    bob = clients('bob')
    for suffix in ('', f'/{a["id"]}', f'/{a["id"]}/preview', f'/{a["id"]}/download'):
        assert bob.get(URL + suffix).status_code == 404
    assert upload(bob).status_code == 404
    root.post('/api/v1/projects/demo/members', json={'subject': 'bob', 'role': 'viewer'})
    assert upload(bob).status_code == 403
    assert bob.get(URL).status_code == 200


@pytest.mark.parametrize('data', [b'', b'PK\x03\x04', b'x,label\n\xff,a', b'x,x\n1,a',
    b'x,label\n"unfinished', b'x,label\n1,a\n2,a\n3,a', b'x,label\ninf,a\n2,b\n3,a',
    b'x,label\n,a\n,b\n,a', b'x,label\n1,a,extra\n2,b\n3,a',
    b'x,label\nhello,a\n2,b\n3,a', b'x,label\n1,\n2,b\n3,a'])
def test_invalid_csv_cleanup(setup, data):
    root, _, path = setup
    assert upload(root, data).status_code == 422
    assert list(path.iterdir()) == []
    assert root.get(URL).json() == []


def test_mime_and_filename(setup):
    root, _, _ = setup
    assert upload(root, filename='data.zip').status_code == 415
    assert upload(root, mime='application/zip').status_code == 415
    assert upload(root, b'\x80\x04pickle', mime='text/csv').status_code == 422
    assert upload(root, filename='../../data.csv').status_code == 201


def test_storage_limits_symlink_and_disk_failure(tmp_path, monkeypatch):
    storage = Storage(tmp_path / 'store')
    with pytest.raises(ApiError) as exc:
        storage.stage(io.BytesIO(b'x' * 100), 99)
    assert exc.value.status == 413
    assert not list(storage.root.iterdir())
    with pytest.raises(ValueError):
        storage.open('../outside')
    key = 'a' * 32
    storage.path(key).symlink_to(tmp_path / 'outside')
    with pytest.raises(OSError):
        storage.open(key)
    storage.discard(key)
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(OSError):
        storage.stage(io.BytesIO(CSV), 1024)
    assert not list(storage.root.iterdir())


def test_cleanup_preserves_references(setup):
    from signalflow_api.datasets.cleanup import collect
    root, _, path = setup
    row = upload(root).json()
    storage = Storage(path)
    orphan, _, _ = storage.stage(io.BytesIO(CSV), 1024)
    for p in path.iterdir():
        os.utime(p, (time.time() - 90000,) * 2)
    factory = root.app.state.session_factory
    assert collect(factory, storage) == [orphan]
    assert storage.path(orphan).exists()
    collect(factory, storage, apply=True)
    assert storage.path(row['id']).exists()
    assert not storage.path(orphan).exists()


def test_streaming_limit_without_content_length(setup):
    import asyncio
    from signalflow_api.middleware import RequestBoundary
    called = False
    async def app(scope, receive, send):
        nonlocal called
        called = True
        while (await receive()).get('more_body'):
            pass
    middleware = RequestBoundary(app, Settings(max_dataset_bytes=100))
    messages = iter([{'type': 'http.request', 'body': b'x' * 9000, 'more_body': True}] * 2)
    sent = []
    async def receive():
        return next(messages)
    async def send(message):
        sent.append(message)
    asyncio.run(middleware({'type': 'http', 'method': 'POST', 'path': URL, 'headers': [
        (b'host', b'localhost'), (b'content-type', b'multipart/form-data; boundary=x')]}, receive, send))
    assert called
    assert sent[0]['status'] == 413


def test_file_limit_and_database_failure(setup, monkeypatch):
    from sqlalchemy.orm import Session
    root, _, path = setup
    settings = root.app.state.settings
    small = TestClient(create_app(Settings(database_url=settings.database_url,
        dataset_root=str(path), max_dataset_bytes=10)), base_url='http://127.0.0.1', headers=root.headers)
    assert upload(small).status_code == 413
    assert not list(path.iterdir())
    original = Session.flush
    def fail_dataset(session, *args, **kwargs):
        from signalflow.persistence.models import DatasetVersion
        if any(isinstance(row, DatasetVersion) for row in session.new):
            raise OSError('database failure')
        return original(session, *args, **kwargs)
    monkeypatch.setattr(Session, 'flush', fail_dataset)
    assert upload(root).status_code == 500
    assert not list(path.iterdir())
    assert root.get(URL).json() == []


def test_symbolic_root_and_numeric_missing_values(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        Storage(link)
    columns, count = validate(io.BytesIO(b'x,label\n,a\n2,b\n3,a\n'), 'label')
    assert columns == ['x', 'label'] and count == 3


def test_exclusive_creation_preserves_existing_file(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from signalflow_api.datasets import storage as module
    store = Storage(tmp_path)
    key = 'b' * 32
    store.path(key).write_bytes(CSV)
    monkeypatch.setattr(module.uuid, 'uuid4', lambda: SimpleNamespace(hex=key))
    with pytest.raises(FileExistsError):
        store.stage(io.BytesIO(b'replacement'), 1024)
    assert store.path(key).read_bytes() == CSV
