from datetime import timedelta
import threading
import time

from sqlalchemy import select, func
from signalflow.persistence.models import Job, JobAttempt
from signalflow.persistence.session import session_scope
from signalflow_api.jobs.queue import now
from signalflow_api.jobs.worker import Limits
from tests.jobs.test_jobs import URL, submit


def test_real_training_recovery_after_mlflow_publication(jobs, monkeypatch):
    root, _, worker = jobs
    identity = submit(root)['id']
    finish = worker.queue.finish
    monkeypatch.setattr(worker.queue, 'finish', lambda *args: False)
    assert worker.tick()
    with session_scope(worker.queue.factory) as session:
        job = session.get(Job, identity)
        directory = worker.root / job.attempt_id
        assert (directory / 'result.json').exists()
        # Panne après publication MLflow et avant réception du résultat métier.
        (directory / 'result.json').unlink()
        job.lease_until = now() - timedelta(seconds=1)
    monkeypatch.setattr(worker.queue, 'finish', finish)
    assert worker.tick()
    result = root.get(URL + '/' + identity).json()
    assert result['state'] == 'succeeded', result
    assert result['tracking_sync'] == 'synced'
    assert result['result']['metrics']['test_accuracy'] > .8
    assert set(result['result']) == {'run_id', 'metrics', 'model_package'}
    assert result['result']['model_package']['format'] == 1
    with session_scope(worker.queue.factory) as session:
        assert session.scalar(select(func.count()).select_from(JobAttempt)) == 1
    assert len(list((directory / 'output' / 'runs').iterdir())) == 1


def test_wall_limit_and_live_cancellation(jobs):
    root, _, worker = jobs
    worker.limits = Limits(wall_seconds=1)
    identity = submit(root)['id']
    assert worker.tick()
    result = root.get(URL + '/' + identity).json()
    assert result['state'] == 'failed' and result['error_code'] == 'WALL_TIME_LIMIT'
    worker.limits = Limits()
    identity = submit(root, 'cancel')['id']
    thread = threading.Thread(target=worker.tick)
    thread.start()
    deadline = time.monotonic() + 20
    while root.get(URL + '/' + identity).json()['state'] == 'queued':
        assert time.monotonic() < deadline
        time.sleep(.05)
    assert root.get('/api/v1/health').status_code == 200
    assert root.post(URL + '/' + identity + '/cancel', json={}).status_code == 200
    thread.join(timeout=30)
    assert not thread.is_alive()
    result = root.get(URL + '/' + identity).json()
    assert result['state'] == 'cancelled'


def test_dataset_digest_checked_before_training(jobs):
    from signalflow.persistence.models import DatasetVersion
    root, _, worker = jobs
    with session_scope(worker.queue.factory) as session:
        job = submit(root)['id']
        row = session.get(Job, job)
        dataset = DatasetVersion(project_id=row.project_id, name='csv', version=1, target='y',
            sha256='f' * 64, size_bytes=3, row_count=3, columns=['x', 'y'], author_id=row.author_id)
        session.add(dataset)
        session.flush()
        row.dataset_id = dataset.id
        row.snapshot = {**row.snapshot, '_dataset_sha256': dataset.sha256}
        worker.datasets.path(dataset.id).write_bytes(b'bad')
    assert worker.tick()
    result = root.get(URL + '/' + job).json()
    assert result['state'] == 'failed' and result['error_code'] == 'WORKER_ERROR'
    assert not list(worker.root.glob('*/output'))


def test_uploaded_dataset_version_runs(jobs):
    from dataclasses import replace
    root, _, worker = jobs
    root.app.state.settings = replace(root.app.state.settings, dataset_root=str(worker.datasets.root))
    content = 'x,label\n' + ''.join(f'{i},{"a" if i < 50 else "b"}\n' for i in range(100))
    uploaded = root.post('/api/v1/projects/jobs/datasets', data={'name': 'sample', 'target': 'label'},
        files={'file': ('sample.csv', content.encode(), 'text/csv')})
    assert uploaded.status_code == 201, uploaded.text
    dataset = uploaded.json()
    identity = submit(root, dataset_id=dataset['id'])['id']
    assert worker.tick()
    row = root.get(URL + '/' + identity).json()
    assert row['state'] == 'succeeded', row
    assert row['dataset_id'] == dataset['id']
    assert row['result']['metrics']['test_accuracy'] >= .8
    assert (worker.root / row['attempt_id'] / 'dataset.csv').read_text() == content
