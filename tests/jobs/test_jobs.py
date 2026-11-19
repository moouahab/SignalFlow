from datetime import timedelta
import json

from sqlalchemy import select
from signalflow.persistence.models import Job, JobAttempt, JobEvent, JobOutbox
from signalflow.persistence.session import session_scope
from signalflow_api.jobs.queue import now
from signalflow_api.jobs.worker import environment

URL = '/api/v1/projects/jobs/runs'


def submit(client, key='one', version=1, **extra):
    response = client.post(URL, json={'configuration_version': version, **extra}, headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    return response.json()


def test_idempotency_snapshot_permissions_cancel_sse(jobs):
    root, clients, worker = jobs
    job = submit(root)
    assert submit(root)['id'] == job['id']
    conflict = root.post(URL, json={'configuration_version': 2}, headers={'Idempotency-Key': 'one'})
    assert conflict.status_code == 409
    assert len(root.get(URL).json()) == 1
    assert root.post(URL, json={'configuration_version': 1}).status_code == 422
    bob = clients('bob')
    for suffix in ('', '/' + job['id'], '/' + job['id'] + '/events'):
        assert bob.get(URL + suffix).status_code == 404
    assert bob.post(URL + '/' + job['id'] + '/cancel', json={}).status_code == 404
    root.post('/api/v1/projects/jobs/members', json={'subject': 'bob', 'role': 'viewer'})
    assert bob.get(URL + '/' + job['id']).status_code == 200
    assert bob.post(URL, json={'configuration_version': 1}, headers={'Idempotency-Key': 'bob'}).status_code == 403
    assert root.post(URL + '/' + job['id'] + '/cancel', json={}).json()['state'] == 'cancelled'
    replay = root.get(URL + '/' + job['id'] + '/events', headers={'Last-Event-ID': '1'})
    assert 'event: cancelled' in replay.text and 'event: queued' not in replay.text
    assert worker.queue.claim() is None
    assert worker.tick() is False


def test_leases_fencing_and_cancel_race(jobs):
    root, _, worker = jobs
    identity = submit(root)['id']
    first, recovery = worker.queue.claim()
    assert not recovery and worker.queue.claim() is None
    with session_scope(worker.queue.factory) as session:
        session.get(Job, identity).lease_until = now() - timedelta(seconds=1)
    second, recovery = worker.queue.claim()
    assert recovery and second.attempt_id == first.attempt_id and first.owner != second.owner
    assert worker.queue.heartbeat(identity, first.owner) is None
    assert not worker.queue.finish(identity, first.owner, {'state': 'succeeded'})
    root.post(URL + '/' + identity + '/cancel', json={})
    assert worker.queue.finish(identity, second.owner, {'state': 'succeeded', 'result': {'run_id': 'fake'}})
    row = root.get(URL + '/' + identity).json()
    assert row['state'] == 'cancelled' and row['result'] is None
    with session_scope(worker.queue.factory) as session:
        assert session.scalar(select(JobAttempt)).state == 'cancelled'
        assert session.get(JobOutbox, identity).state == 'cancelled'
        assert [e.kind for e in session.scalars(select(JobEvent).order_by(JobEvent.sequence))] == [
            'queued', 'running', 'recovering', 'cancel_requested', 'cancelled']
    assert not worker.queue.finish(identity, second.owner, {'state': 'failed'})


def test_environment_does_not_forward_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'secret')
    monkeypatch.setenv('SIGNALFLOW_DATABASE_URL', 'secret')
    monkeypatch.setenv('MLFLOW_TRACKING_TOKEN', 'secret')
    assert 'secret' not in json.dumps(environment(tmp_path))


def test_worker_lock_and_recovery_before_launch(jobs):
    import fcntl
    root, _, worker = jobs
    identity = submit(root)['id']
    lock = (worker.root / '.worker.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX)
    assert worker.tick() is False
    lock.close()
    job, _ = worker.queue.claim()
    with session_scope(worker.queue.factory) as session:
        session.get(Job, identity).lease_until = now() - timedelta(seconds=1)
    assert worker.tick()
    result = root.get(URL + '/' + identity).json()
    assert result['state'] == 'failed' and result['error_code'] == 'WORKER_LOST'
    assert worker.queue.claim() is None


def test_outbox_retry_is_idempotent(jobs, monkeypatch):
    root, _, worker = jobs
    identity = submit(root)['id']
    job, _ = worker.queue.claim()
    directory = worker.prepare(job)
    assert worker.queue.finish(job.id, job.owner, {'state': 'failed', 'error_code': 'TEST'})
    monkeypatch.setattr(worker, 'child', lambda *args: False)
    worker.dispatch()
    assert root.get(URL + '/' + identity).json()['tracking_sync'] == 'pending'
    monkeypatch.setattr(worker, 'child', lambda *args: True)
    worker.dispatch()
    worker.dispatch()
    with session_scope(worker.queue.factory) as session:
        events = list(session.scalars(select(JobEvent).where(JobEvent.kind == 'tracking_synced')))
        assert len(events) == 1
    assert root.get(URL + '/' + identity).json()['tracking_sync'] == 'synced'


def test_wheel_runtime_does_not_inherit_api_site_packages(tmp_path, monkeypatch):
    import signalflow
    import signalflow_api
    monkeypatch.setattr(signalflow, '__file__', '/usr/local/lib/python3.14/site-packages/signalflow/__init__.py')
    monkeypatch.setattr(signalflow_api, '__file__', '/usr/local/lib/python3.14/site-packages/signalflow_api/__init__.py')
    assert environment(tmp_path)['PYTHONPATH'] == ''
