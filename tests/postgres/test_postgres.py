"""Migrations et transactions sur un vrai PostgreSQL dédié."""
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
import sqlalchemy
from sqlalchemy.orm import sessionmaker

from signalflow.persistence.errors import VersionConflict
from signalflow.persistence.repositories import (
    ConfigurationRepository, ProjectRepository, TokenRepository, UserRepository,
)
from signalflow.persistence.session import session_scope
from signalflow_api.auth.identity import hash_token

pytestmark = pytest.mark.postgres

DOCUMENT = {'training': {'seed': 1}, 'dataset': {'source': 'builtin'}}


@pytest.fixture(scope='module')
def database_url():
    url = os.environ.get('SIGNALFLOW_DATABASE_URL')
    if not url or not url.startswith('postgresql'):
        pytest.skip('SIGNALFLOW_DATABASE_URL PostgreSQL requis')
    return url


@pytest.fixture(scope='module')
def factory(database_url):
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, SIGNALFLOW_DATABASE_URL=database_url)
    result = subprocess.run(
        [sys.executable, '-m', 'alembic', 'upgrade', 'head'],
        cwd=root, env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    engine = sqlalchemy.create_engine(database_url, pool_pre_ping=True)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def test_migrations_and_transactional_conflict(factory):
    suffix = uuid.uuid4().hex[:8]
    with session_scope(factory) as session:
        users = UserRepository(session)
        user = users.ensure(f'pg-{suffix}', 'admin')
        TokenRepository(session).create(user.id, hash_token(f'pg-token-{suffix}'), 'pg')
        project = ProjectRepository(session).create(f'pg-projet-{suffix}', 'PG', '', user)
    with session_scope(factory) as session:
        record = TokenRepository(session).find(hash_token(f'pg-token-{suffix}'))
        assert record['subject'] == f'pg-{suffix}'
    with session_scope(factory) as session:
        user = UserRepository(session).by_subject(f'pg-{suffix}')
        project = ProjectRepository(session).by_key(f'pg-projet-{suffix}')
        versions = ConfigurationRepository(session)
        first = versions.create_version(project, DOCUMENT, 'a' * 64, user, None)
        assert first.version == 1
        with pytest.raises(VersionConflict):
            versions.create_version(project, DOCUMENT, 'b' * 64, user, 7)
    with session_scope(factory) as session:
        project = ProjectRepository(session).by_key(f'pg-projet-{suffix}')
        assert ConfigurationRepository(session).count(project) == 1
