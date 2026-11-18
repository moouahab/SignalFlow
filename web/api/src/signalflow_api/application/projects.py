"""Gestion des projets et des appartenances persistantes."""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from signalflow.persistence.errors import AlreadyExistsError, ForbiddenError, NotFoundError
from signalflow.persistence.repositories import ProjectRepository, UserRepository
from signalflow.persistence.session import session_scope

from ..auth.policy import permissions_for
from ..dependencies import CurrentUser
from ..errors import ApiError
from ..schemas.projects import MembershipResponse, ProjectCreate, ProjectResponse


def _translate(error: Exception) -> ApiError:
    if isinstance(error, AlreadyExistsError):
        return ApiError(409, 'PROJECT_ALREADY_EXISTS', 'La cle de projet est deja utilisee.')
    if isinstance(error, ForbiddenError):
        return ApiError(403, 'PROJECT_FORBIDDEN', 'Acces refuse sur ce projet.')
    if isinstance(error, NotFoundError):
        return ApiError(404, 'PROJECT_NOT_FOUND', 'Ressource introuvable.')
    return ApiError(500, 'PROJECT_ERROR', 'Le projet ne peut pas etre traite.')


def _require_manage(current: CurrentUser) -> None:
    if not current.is_admin and 'project:manage' not in permissions_for(current.role):
        raise ApiError(403, 'PERMISSION_DENIED', 'Permission insuffisante.')


def _require_read(current: CurrentUser) -> None:
    if not permissions_for(current.role).intersection({'project:read', 'config:read'}):
        raise ApiError(403, 'PERMISSION_DENIED', 'Permission insuffisante.')


class ProjectService:
    """Opérations sur les projets, avec contrôle de visibilité."""

    def __init__(self, factory: sessionmaker) -> None:
        self.factory = factory

    def create(self, current: CurrentUser, payload: ProjectCreate) -> ProjectResponse:
        _require_manage(current)
        try:
            with session_scope(self.factory) as session:
                users = UserRepository(session)
                creator = users.get(current.identity)
                if creator is None:
                    raise NotFoundError('identite introuvable')
                project = ProjectRepository(session).create(
                    payload.key, payload.name, payload.description, creator
                )
                return ProjectResponse(key=project.key, name=project.name,
                                       description=project.description, created_at=project.created_at)
        except (AlreadyExistsError, ForbiddenError, NotFoundError) as error:
            raise _translate(error) from None

    def list(self, current: CurrentUser) -> list[ProjectResponse]:
        _require_read(current)
        try:
            with session_scope(self.factory) as session:
                user = UserRepository(session).get(current.identity)
                if user is None:
                    return []
                projects = ProjectRepository(session).list_for_user(user, current.is_admin)
                return [ProjectResponse(key=item.key, name=item.name, description=item.description,
                                        created_at=item.created_at) for item in projects]
        except (ForbiddenError, NotFoundError) as error:
            raise _translate(error) from None

    def get(self, current: CurrentUser, key: str) -> ProjectResponse:
        _require_read(current)
        try:
            with session_scope(self.factory) as session:
                user = UserRepository(session).get(current.identity)
                if user is None:
                    raise NotFoundError('identite introuvable')
                project, _ = ProjectRepository(session).visible(key, user, current.is_admin)
                return ProjectResponse(key=project.key, name=project.name,
                                       description=project.description, created_at=project.created_at)
        except (ForbiddenError, NotFoundError) as error:
            raise _translate(error) from None

    def add_member(self, current: CurrentUser, key: str, subject: str, role: str) -> MembershipResponse:
        _require_manage(current)
        try:
            with session_scope(self.factory) as session:
                repository = ProjectRepository(session)
                user = UserRepository(session).get(current.identity)
                if user is None:
                    raise NotFoundError('identite introuvable')
                project, membership = repository.visible(key, user, current.is_admin)
                repository.require_project_role(membership, current.is_admin, 'admin')
                repository.add_member(project, subject, role)
                return MembershipResponse(subject=subject, role=role)
        except (AlreadyExistsError, ForbiddenError, NotFoundError) as error:
            raise _translate(error) from None

    def members(self, current: CurrentUser, key: str) -> list[MembershipResponse]:
        _require_read(current)
        try:
            with session_scope(self.factory) as session:
                user = UserRepository(session).get(current.identity)
                if user is None:
                    raise NotFoundError('identite introuvable')
                repository = ProjectRepository(session)
                project, membership = repository.visible(key, user, current.is_admin)
                repository.require_project_role(membership, current.is_admin, 'admin', 'editor', 'viewer')
                return [MembershipResponse(subject=subject, role=role)
                        for subject, role in repository.members(project)]
        except (ForbiddenError, NotFoundError) as error:
            raise _translate(error) from None
