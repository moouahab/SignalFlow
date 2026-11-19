"""Concurrence réelle des soumissions, prises de bail et publications CSV."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import io
import uuid

import pytest
from sqlalchemy import select
from starlette.datastructures import Headers, UploadFile

from tests.postgres.test_postgres import factory, database_url
from tests.api.test_project_isolation import DOCUMENT
from signalflow.persistence.repositories import UserRepository, ProjectRepository, ConfigurationRepository
from signalflow.persistence.models import Job
from signalflow.persistence.session import session_scope
from signalflow_api.dependencies import CurrentUser
from signalflow_api.settings import Settings
from signalflow_api.jobs.service import JobService, SubmitJob
from signalflow_api.jobs.queue import Queue
from signalflow_api.datasets.service import DatasetService

pytestmark = pytest.mark.postgres


def setup_project(factory):
    key = 'jobs-' + uuid.uuid4().hex[:12]
    with session_scope(factory) as session:
        user = UserRepository(session).ensure(key, 'admin')
        project = ProjectRepository(session).create(key, 'Jobs', '', user)
        ConfigurationRepository(session).create_version(project, DOCUMENT, '0' * 64, user, None)
        current = CurrentUser(user.id, user.subject, user.role)
    return key, current


def test_idempotent_submission_and_two_claimers(factory):
    key, current = setup_project(factory)
    barrier = Barrier(2)
    def submit(_):
        barrier.wait(timeout=10)
        return JobService(factory, Settings()).submit(current, key, SubmitJob(configuration_version=1), 'same')['id']
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(submit, range(2)))
    assert ids[0] == ids[1]
    def claim(_):
        barrier.wait(timeout=10)
        return Queue(factory).claim()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(claim, range(2)))
    assert sum(item is not None for item in results) == 1
    job, _ = next(item for item in results if item is not None)
    assert Queue(factory).finish(job.id, job.owner, {'state': 'failed', 'error_code': 'TEST'})


def test_dataset_concurrent_versions(factory, tmp_path):
    key, current = setup_project(factory)
    settings = Settings(dataset_root=str(tmp_path))
    barrier = Barrier(2)
    def upload(_):
        file = UploadFile(io.BytesIO(b'x,label\n1,a\n2,b\n3,a\n'), filename='data.csv',
                          headers=Headers({'content-type': 'text/csv'}))
        barrier.wait(timeout=10)
        return DatasetService(factory, settings).upload(current, key, 'train', 'label', file)['version']
    with ThreadPoolExecutor(2) as pool:
        versions = list(pool.map(upload, range(2)))
    assert sorted(versions) == [1, 2]


def test_schema_matches_migrations(factory, database_url):
    import os
    import subprocess
    import sys
    from pathlib import Path
    result = subprocess.run([sys.executable, '-m', 'alembic', 'check'],
        cwd=Path(__file__).resolve().parents[2],
        env=dict(os.environ, SIGNALFLOW_DATABASE_URL=database_url),
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
