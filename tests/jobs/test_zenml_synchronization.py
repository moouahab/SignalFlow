"""Régression ZenML 0.98.0 : réconcilier un seul run, vérifier, puis acquitter.

Le faux store reproduit les règles observées dans le code de ZenML 0.98.0 :
statut de run statique recalculé depuis les étapes, CANCELLED ignoré,
CANCELLED converti en RETRYING si l'étape est réessayable et le run non
stoppé, et étapes terminées non modifiables.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import sys
import types
import uuid

import pytest
from sqlalchemy import select

from signalflow.persistence.models import Job, JobOutbox
from signalflow.persistence.session import session_scope
from signalflow.orchestration import zenml_adapter
from tests.jobs.test_jobs import submit, URL


class ExecutionStatus(Enum):
    INITIALIZING = 'initializing'
    PROVISIONING = 'provisioning'
    QUEUED = 'queued'
    RUNNING = 'running'
    FAILED = 'failed'
    COMPLETED = 'completed'
    CACHED = 'cached'
    SKIPPED = 'skipped'
    RETRYING = 'retrying'
    RETRIED = 'retried'
    CANCELLING = 'cancelling'
    CANCELLED = 'cancelled'
    PAUSED = 'paused'
    RESUMING = 'resuming'
    STOPPED = 'stopped'
    STOPPING = 'stopping'

    @property
    def is_finished(self):
        return self in {self.FAILED, self.COMPLETED, self.CACHED, self.SKIPPED,
                        self.RETRIED, self.CANCELLED, self.STOPPED}


@dataclass
class PipelineRunUpdate:
    status: object = None


@dataclass
class StepRunUpdate:
    status: object = None
    end_time: object = None


@dataclass
class Step:
    id: uuid.UUID
    name: str
    status: ExecutionStatus
    retriable: bool = False
    end_time: object = None


@dataclass
class Run:
    id: uuid.UUID
    status: ExecutionStatus
    steps: dict = field(default_factory=dict)
    num_steps: int = 6


class Store:
    def __init__(self, run):
        self.runs = {run.id: run}
        self.run_updates = []
        self.step_updates = []
        self.fail_next = False

    @property
    def run(self):
        return next(iter(self.runs.values()))

    def update_run(self, run_id, update):
        self.run_updates.append((uuid.UUID(str(run_id)), update.status))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError('panne temporaire du store')
        run = self.runs[uuid.UUID(str(run_id))]
        if update.status is ExecutionStatus.STOPPING:
            run.status = (ExecutionStatus.STOPPED
                          if all(item.status.is_finished for item in run.steps.values())
                          else ExecutionStatus.STOPPING)
        elif update.status is ExecutionStatus.FAILED:
            run.status = ExecutionStatus.FAILED
        else:
            # ZenML 0.98.0 recalcule depuis les étapes et ignore CANCELLED.
            run.status = ExecutionStatus.RUNNING
        return run

    def update_run_step(self, step_id, update):
        run = self.run
        step = next(item for item in run.steps.values() if item.id == step_id)
        if step.status.is_finished:
            raise RuntimeError('étape terminée non modifiable')
        effective = update.status
        if (effective is ExecutionStatus.CANCELLED and step.retriable
                and run.status not in (ExecutionStatus.STOPPING, ExecutionStatus.STOPPED)):
            effective = ExecutionStatus.RETRYING
        self.step_updates.append((step.name, effective))
        step.status, step.end_time = effective, update.end_time
        if (run.status is ExecutionStatus.STOPPING
                and all(item.status.is_finished for item in run.steps.values())):
            run.status = ExecutionStatus.STOPPED
        return step


class Client:
    # Pas de list_pipeline_runs : la correction ne doit jamais balayer tous les runs.
    def __init__(self, store):
        self.zen_store = store

    def get_pipeline_run(self, run_id):
        return self.zen_store.runs[uuid.UUID(str(run_id))]


@pytest.fixture
def zenml_store(monkeypatch):
    run = Run(uuid.uuid4(), ExecutionStatus.RUNNING)
    store = Store(run)
    client = Client(store)
    package = types.ModuleType('zenml')
    package.__path__ = []
    modules = {
        'zenml': package,
        'zenml.client': types.ModuleType('zenml.client'),
        'zenml.enums': types.ModuleType('zenml.enums'),
        'zenml.models': types.ModuleType('zenml.models'),
    }
    modules['zenml.client'].Client = lambda: client
    modules['zenml.enums'].ExecutionStatus = ExecutionStatus
    modules['zenml.models'].PipelineRunUpdate = PipelineRunUpdate
    modules['zenml.models'].StepRunUpdate = StepRunUpdate
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return store, client


def reference(directory, run_id):
    path = directory / 'output' / 'orchestration' / uuid.uuid4().hex
    path.mkdir(parents=True)
    (path / 'execution.json').write_text(json.dumps({'execution_id': str(run_id)}))


def test_cancel_running_pipeline_uses_zenml_terminal_state(tmp_path, zenml_store):
    store, _ = zenml_store
    run = store.run
    run.steps = {
        'load': Step(uuid.uuid4(), 'load', ExecutionStatus.COMPLETED),
        'split': Step(uuid.uuid4(), 'split', ExecutionStatus.RUNNING),
    }
    reference(tmp_path, run.id)

    zenml_adapter.synchronize('cancelled', tmp_path)

    assert store.runs[run.id].status is ExecutionStatus.STOPPED
    assert run.steps['load'].status is ExecutionStatus.COMPLETED
    assert run.steps['split'].status is ExecutionStatus.CANCELLED
    assert run.steps['split'].end_time is not None
    assert [status for _, status in store.run_updates] == [ExecutionStatus.STOPPING]
    assert [name for name, _ in store.step_updates] == ['split']


def test_partially_executed_retriable_step_is_not_retried(tmp_path, zenml_store):
    store, _ = zenml_store
    run = store.run
    run.steps = {'load': Step(uuid.uuid4(), 'load', ExecutionStatus.RUNNING, retriable=True)}
    reference(tmp_path, run.id)

    zenml_adapter.synchronize('cancelled', tmp_path)

    assert store.runs[run.id].status is ExecutionStatus.STOPPED
    assert store.step_updates == [('load', ExecutionStatus.CANCELLED)]


def test_repeated_synchronization_is_idempotent(tmp_path, zenml_store):
    store, _ = zenml_store
    run = store.run
    run.steps = {'split': Step(uuid.uuid4(), 'split', ExecutionStatus.RUNNING)}
    reference(tmp_path, run.id)

    zenml_adapter.synchronize('cancelled', tmp_path)
    first = (list(store.run_updates), list(store.step_updates))
    zenml_adapter.synchronize('cancelled', tmp_path)

    assert (store.run_updates, store.step_updates) == first
    assert store.runs[run.id].status is ExecutionStatus.STOPPED


def test_transient_failure_is_raised_then_retried(tmp_path, zenml_store):
    store, _ = zenml_store
    run = store.run
    run.steps = {'split': Step(uuid.uuid4(), 'split', ExecutionStatus.RUNNING)}
    reference(tmp_path, run.id)
    store.fail_next = True

    with pytest.raises(RuntimeError, match='panne temporaire'):
        zenml_adapter.synchronize('cancelled', tmp_path)
    assert store.runs[run.id].status is ExecutionStatus.RUNNING
    assert store.step_updates == []

    zenml_adapter.synchronize('cancelled', tmp_path)
    assert store.runs[run.id].status is ExecutionStatus.STOPPED


def test_cancellation_before_pipeline_creation_is_a_noop(tmp_path, zenml_store):
    store, _ = zenml_store
    zenml_adapter.synchronize('cancelled', tmp_path)

    assert store.run_updates == []
    assert store.step_updates == []
    assert store.runs[store.run.id].status is ExecutionStatus.RUNNING


def test_other_pipelines_are_never_modified(tmp_path, zenml_store):
    store, _ = zenml_store
    target = store.run
    target.steps = {'split': Step(uuid.uuid4(), 'split', ExecutionStatus.RUNNING)}
    foreign = Run(uuid.uuid4(), ExecutionStatus.RUNNING,
                  {'load': Step(uuid.uuid4(), 'load', ExecutionStatus.RUNNING)})
    store.runs[foreign.id] = foreign
    reference(tmp_path, target.id)

    zenml_adapter.synchronize('cancelled', tmp_path)

    assert store.runs[foreign.id].status is ExecutionStatus.RUNNING
    assert all(step.status is ExecutionStatus.RUNNING for step in foreign.steps.values())
    assert [run_id for run_id, _ in store.run_updates] == [target.id]


def test_terminal_pipeline_is_preserved(tmp_path, zenml_store):
    store, _ = zenml_store
    run = store.run
    run.status = ExecutionStatus.COMPLETED
    run.steps = {'load': Step(uuid.uuid4(), 'load', ExecutionStatus.COMPLETED)}
    reference(tmp_path, run.id)

    zenml_adapter.synchronize('cancelled', tmp_path)

    assert store.runs[run.id].status is ExecutionStatus.COMPLETED
    assert store.run_updates == [] and store.step_updates == []


def test_failed_pipeline_reaches_failed_status(tmp_path, zenml_store):
    store, _ = zenml_store
    run = store.run
    run.steps = {'training': Step(uuid.uuid4(), 'training', ExecutionStatus.RUNNING)}
    reference(tmp_path, run.id)

    zenml_adapter.synchronize('failed', tmp_path)

    assert store.runs[run.id].status is ExecutionStatus.FAILED
    assert store.step_updates == [('training', ExecutionStatus.FAILED)]


def test_succeeded_requires_completed_run(tmp_path, zenml_store):
    store, _ = zenml_store
    run = store.run
    run.status = ExecutionStatus.COMPLETED
    reference(tmp_path, run.id)
    zenml_adapter.synchronize('succeeded', tmp_path)

    run.status = ExecutionStatus.RUNNING
    with pytest.raises(RuntimeError, match='incomplet'):
        zenml_adapter.synchronize('succeeded', tmp_path)


def test_outbox_stays_pending_until_synchronization_succeeds(jobs, monkeypatch):
    root, _, worker = jobs
    identity = submit(root, 'sync-retry')['id']
    job, _ = worker.queue.claim()
    assert root.post(URL + '/' + identity + '/cancel', json={}).status_code == 200
    directory = worker.root / job.attempt_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'request.json').write_text(json.dumps({'orchestration': 'zenml'}), encoding='utf-8')
    assert worker.queue.finish(job.id, job.owner, {'state': 'cancelled'})

    calls = []

    def flaky(child_directory, mode, job=None):
        calls.append(mode)
        if len(calls) == 1:
            raise RuntimeError('synchronisation indisponible')
        return True

    monkeypatch.setattr(worker, 'child', flaky)
    worker.dispatch()
    with session_scope(worker.queue.factory) as session:
        outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == identity))
        assert outbox.delivered_at is None
        assert session.get(Job, identity).tracking_sync == 'pending'

    worker.dispatch()
    with session_scope(worker.queue.factory) as session:
        outbox = session.scalar(select(JobOutbox).where(JobOutbox.job_id == identity))
        assert outbox.delivered_at is not None
        assert session.get(Job, identity).tracking_sync == 'synced'
