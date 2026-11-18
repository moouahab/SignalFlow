"""Jetons hachés, isolation projet et versions append-only."""
from datetime import datetime, timedelta, timezone

import pytest

from signalflow.persistence.errors import ForbiddenError, NotFoundError, VersionConflict
from signalflow.persistence.models import Project
from signalflow.persistence.repositories import (
    ConfigurationRepository, ProjectRepository, TokenRepository, UserRepository,
)
from signalflow.persistence.session import session_scope
from signalflow_api.auth.identity import hash_token

DOCUMENT = {'training': {'seed': 1}, 'dataset': {'source': 'iris'}}


def _users(session):
    return UserRepository(session)


def test_token_resolution_and_revocation(factory):
    with session_scope(factory) as session:
        user = _users(session).ensure('alice', 'admin')
        TokenRepository(session).create(user.id, hash_token('token-alice-123'), 'portable')
    with session_scope(factory) as session:
        record = TokenRepository(session).find(hash_token('token-alice-123'))
        assert record['subject'] == 'alice'
        assert record['role'] == 'admin'
    with session_scope(factory) as session:
        assert TokenRepository(session).revoke(hash_token('token-alice-123')) is True
    with session_scope(factory) as session:
        assert TokenRepository(session).find(hash_token('token-alice-123')) is None


def test_project_visibility_and_membership(factory):
    with session_scope(factory) as session:
        alice = _users(session).ensure('alice', 'editor')
        bob = _users(session).ensure('bob', 'editor')
        first = ProjectRepository(session).create('projet-a', 'A', '', alice)
        ProjectRepository(session).add_member(first, 'bob', 'viewer')
        ProjectRepository(session).create('projet-b', 'B', '', bob)
    with session_scope(factory) as session:
        alice = _users(session).by_subject('alice')
        bob = _users(session).by_subject('bob')
        projects = ProjectRepository(session)
        assert [p.key for p in projects.list_for_user(alice)] == ['projet-a']
        assert [p.key for p in projects.list_for_user(bob)] == ['projet-a', 'projet-b']
        with pytest.raises(NotFoundError):
            projects.visible('projet-b', alice)
        project, membership = projects.visible('projet-a', alice)
        projects.require_project_role(membership, False, 'admin', 'editor')
        _, bob_membership = projects.visible('projet-a', bob)
        with pytest.raises(ForbiddenError):
            projects.require_project_role(bob_membership, False, 'admin', 'editor')


def test_versions_are_append_only_and_conflicts_arbitrated(factory):
    with session_scope(factory) as session:
        alice = _users(session).ensure('alice', 'admin')
        project = ProjectRepository(session).create('projet-a', 'A', '', alice)
        project_id = project.id
    with session_scope(factory) as session:
        alice = _users(session).by_subject('alice')
        project = session.get(Project, project_id)
        versions = ConfigurationRepository(session)
        first = versions.create_version(project, DOCUMENT, 'a' * 64, alice, None)
        assert first.version == 1 and first.parent_id is None
        second = versions.create_version(project, dict(DOCUMENT), 'b' * 64, alice, 1)
        assert second.version == 2 and second.parent_id == first.id
    with session_scope(factory) as session:
        alice = _users(session).by_subject('alice')
        project = ProjectRepository(session).by_key('projet-a')
        versions = ConfigurationRepository(session)
        with pytest.raises(VersionConflict):
            versions.create_version(project, DOCUMENT, 'c' * 64, alice, 1)
        assert versions.count(project) == 2
        assert [v.version for v in versions.list_versions(project)] == [1, 2]


def test_failed_transaction_leaves_no_partial_version(factory):
    with session_scope(factory) as session:
        alice = _users(session).ensure('alice', 'admin')
        project = ProjectRepository(session).create('projet-a', 'A', '', alice)
        project_id = project.id
    with pytest.raises(RuntimeError):
        with session_scope(factory) as session:
            alice = _users(session).by_subject('alice')
            project = session.get(Project, project_id)
            ConfigurationRepository(session).create_version(project, DOCUMENT, 'd' * 64, alice, None)
            raise RuntimeError('panne simulée')
    with session_scope(factory) as session:
        project = ProjectRepository(session).by_key('projet-a')
        assert ConfigurationRepository(session).count(project) == 0


def test_token_expiration_and_legacy_compatibility(factory):
    """Les jetons expirés sont refusés ; une ancienne entrée reste révoquable."""
    now = datetime.now(timezone.utc)
    with session_scope(factory) as session:
        user = _users(session).ensure('alice', 'admin')
        repo = TokenRepository(session)
        repo.create(user.id, hash_token('expired-token-123'), 'expired',
                    expires_at=now - timedelta(days=1))
        repo.create(user.id, hash_token('valid-token-123'), 'valid',
                    expires_at=now + timedelta(days=1))
        repo.create(user.id, hash_token('legacy-token-123'), 'legacy')
        with pytest.raises(ValueError, match='expiration'):
            repo.create(user.id, hash_token('bad-timezone-123'), 'invalid',
                        expires_at=datetime.now())
    with session_scope(factory) as session:
        repo = TokenRepository(session)
        assert repo.find(hash_token('expired-token-123')) is None
        assert repo.find(hash_token('valid-token-123'))['subject'] == 'alice'
        assert repo.find(hash_token('legacy-token-123'))['subject'] == 'alice'
        assert repo.revoke(hash_token('legacy-token-123'))
        assert repo.find(hash_token('legacy-token-123')) is None


def test_version_snapshot_is_independent_from_input(factory):
    """La version stockée ne peut pas être modifiée via l'objet source."""
    document = {"training": {"seed": 42}, "dataset": {"items": ["iris"]}}
    with session_scope(factory) as session:
        alice = _users(session).ensure('alice', 'admin')
        project = ProjectRepository(session).create('projet-snapshot', 'Snapshot', '', alice)
        ConfigurationRepository(session).create_version(project, document, 'f' * 64, alice, None)
    document['training']['seed'] = 999
    document['dataset']['items'].append('malicious-change')
    with session_scope(factory) as session:
        project = ProjectRepository(session).by_key('projet-snapshot')
        stored = ConfigurationRepository(session).get_version(project, 1)
        assert stored.document['training']['seed'] == 42
        assert stored.document['dataset']['items'] == ['iris']


def test_unique_version_collision_keeps_session_usable(factory, monkeypatch):
    """Une contrainte UNIQUE concurrente n'empoisonne pas la transaction."""
    with session_scope(factory) as session:
        alice = _users(session).ensure('alice', 'admin')
        project = ProjectRepository(session).create('collision', 'Collision', '', alice)
        versions = ConfigurationRepository(session)
        first = versions.create_version(project, DOCUMENT, 'a' * 64, alice, None)
        versions.create_version(project, DOCUMENT, 'b' * 64, alice, 1)

        # Forcer la lecture d'une version périmée : on simule le cas où un
        # autre éditeur a déjà inséré la version 2 entre lecture et insertion.
        monkeypatch.setattr(versions, 'latest', lambda _project: first)
        with pytest.raises(VersionConflict, match='déjà publiée'):
            versions.create_version(project, DOCUMENT, 'c' * 64, alice, 1)

        # Après l'exception, le SAVEPOINT a été annulé, pas la transaction.
        assert ConfigurationRepository(session).count(project) == 2
        assert [v.version for v in ConfigurationRepository(session).list_versions(project)] == [1, 2]
    # Le commit implicite doit réussir ; l'état reste lisible ensuite.
    with session_scope(factory) as session:
        project = ProjectRepository(session).by_key('collision')
        assert ConfigurationRepository(session).count(project) == 2
