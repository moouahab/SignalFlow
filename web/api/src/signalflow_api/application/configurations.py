"""Versions de configuration append-only, comparaison et export YAML."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import yaml
from sqlalchemy.orm import sessionmaker

from signalflow.persistence.errors import ForbiddenError, NotFoundError, VersionConflict
from signalflow.persistence.repositories import ConfigurationRepository, ProjectRepository, UserRepository
from signalflow.persistence.session import session_scope

from ..auth.policy import permissions_for
from ..dependencies import CurrentUser
from ..errors import ApiError
from ..schemas.models import TrainingPayload
from ..schemas.projects import (
    ConfigurationCreate, ConfigurationDiffResponse, ConfigurationListResponse,
    ConfigurationVersionResponse,
)
from ..services.training import validate_payload


def _digest(document: dict[str, Any]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _translate(error: Exception) -> ApiError:
    if isinstance(error, VersionConflict):
        return ApiError(409, 'VERSION_CONFLICT', 'Une version concurrent a deja ete publiee.')
    if isinstance(error, ForbiddenError):
        return ApiError(403, 'PROJECT_FORBIDDEN', 'Acces refuse sur ce projet.')
    if isinstance(error, NotFoundError):
        return ApiError(404, 'VERSION_NOT_FOUND', 'Ressource introuvable.')
    return ApiError(500, 'CONFIGURATION_ERROR', 'La version ne peut pas etre traitee.')


def _require(current: CurrentUser, permission: str) -> None:
    if permission not in permissions_for(current.role):
        raise ApiError(403, 'PERMISSION_DENIED', 'Permission insuffisante.')


def _changed_paths(left: Any, right: Any, path: str = '') -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        changed: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f'{path}.{key}' if path else key
            if key not in left or key not in right:
                changed.append(f'$.{child}')
            else:
                changed.extend(_changed_paths(left[key], right[key], child))
        return changed
    return [] if left == right else [f'$.{path}' if path else '$']


class ConfigurationService:
    """Création, lecture, comparaison et export de versions immuables."""

    def __init__(self, factory: sessionmaker) -> None:
        self.factory = factory

    def create(self, current: CurrentUser, key: str, payload: ConfigurationCreate) -> ConfigurationVersionResponse:
        _require(current, 'config:write')
        validated = validate_payload(payload.configuration)
        document = validated.model_dump(exclude_unset=True)
        digest = _digest(document)
        try:
            with session_scope(self.factory) as session:
                user = UserRepository(session).get(current.identity)
                if user is None:
                    raise NotFoundError('identite introuvable')
                projects = ProjectRepository(session)
                project, membership = projects.visible(key, user, current.is_admin)
                projects.require_project_role(membership, current.is_admin, 'admin', 'editor')
                version = ConfigurationRepository(session).create_version(
                    project, document, digest, user, payload.expected_version
                )
                return ConfigurationVersionResponse(
                    project_key=project.key, version=version.version, digest=version.digest,
                    author=user.subject, parent_version=version.version - 1 if version.parent_id else None,
                    created_at=version.created_at, configuration=validated,
                )
        except (ForbiddenError, NotFoundError, VersionConflict) as error:
            raise _translate(error) from None

    def list(self, current: CurrentUser, key: str) -> ConfigurationListResponse:
        _require(current, 'config:read')
        try:
            with session_scope(self.factory) as session:
                user = UserRepository(session).get(current.identity)
                if user is None:
                    raise NotFoundError('identite introuvable')
                project, _ = ProjectRepository(session).visible(key, user, current.is_admin)
                versions = ConfigurationRepository(session).list_versions(project)
                return ConfigurationListResponse(versions=[self._response(project.key, item, user.subject)
                                                            for item in versions])
        except (ForbiddenError, NotFoundError) as error:
            raise _translate(error) from None

    def get(self, current: CurrentUser, key: str, version: int) -> ConfigurationVersionResponse:
        _require(current, 'config:read')
        try:
            with session_scope(self.factory) as session:
                user = UserRepository(session).get(current.identity)
                if user is None:
                    raise NotFoundError('identite introuvable')
                project, _ = ProjectRepository(session).visible(key, user, current.is_admin)
                row = ConfigurationRepository(session).get_version(project, version)
                if row is None:
                    raise NotFoundError('version introuvable')
                return self._response(project.key, row, user.subject)
        except (ForbiddenError, NotFoundError) as error:
            raise _translate(error) from None

    def export(self, current: CurrentUser, key: str, version: int) -> str:
        response = self.get(current, key, version)
        document = response.configuration.model_dump(exclude_unset=True)
        return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)

    def compare(self, current: CurrentUser, key: str, source: int, target: int) -> ConfigurationDiffResponse:
        _require(current, 'config:read')
        try:
            with session_scope(self.factory) as session:
                user = UserRepository(session).get(current.identity)
                if user is None:
                    raise NotFoundError('identite introuvable')
                project, _ = ProjectRepository(session).visible(key, user, current.is_admin)
                repository = ConfigurationRepository(session)
                left = repository.get_version(project, source)
                right = repository.get_version(project, target)
                if left is None or right is None:
                    raise NotFoundError('version introuvable')
                return ConfigurationDiffResponse(
                    from_version=source, to_version=target,
                    changed=_changed_paths(left.document, right.document),
                )
        except (ForbiddenError, NotFoundError) as error:
            raise _translate(error) from None

    @staticmethod
    def _response(project_key: str, row, author: str) -> ConfigurationVersionResponse:
        return ConfigurationVersionResponse(
            project_key=project_key, version=row.version, digest=row.digest, author=author,
            parent_version=row.version - 1 if row.parent_id else None,
            created_at=row.created_at, configuration=TrainingPayload.model_validate(row.document),
        )
