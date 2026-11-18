"""Autorisation et publication : fichier durable avant commit de sa référence."""
from contextlib import contextmanager
import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from signalflow.persistence.models import DatasetVersion, Project
from signalflow.persistence.repositories import ProjectRepository, UserRepository
from signalflow.persistence.errors import ForbiddenError, NotFoundError
from signalflow.persistence.session import session_scope
from ..application.projects import _translate
from ..errors import ApiError
from .storage import Storage
from .validation import validate


def metadata(row):
    return {key: getattr(row, key) for key in (
        'id', 'name', 'version', 'target', 'sha256', 'size_bytes',
        'row_count', 'columns', 'author_id', 'created_at')}


class DatasetService:
    def __init__(self, factory, settings):
        self.factory, self.settings = factory, settings

    def storage(self):
        if not self.settings.dataset_root:
            raise ApiError(503, 'DATASET_STORAGE_NOT_CONFIGURED', 'Stockage datasets absent.')
        return Storage(self.settings.dataset_root)

    @contextmanager
    def access(self, current, key, write=False):
        try:
            with session_scope(self.factory) as session:
                user = UserRepository(session).get(current.identity)
                if user is None:
                    raise NotFoundError()
                repo = ProjectRepository(session)
                project, membership = repo.visible(key, user, current.is_admin)
                if write:
                    repo.require_project_role(membership, current.is_admin, 'editor', 'admin')
                yield session, project
        except (ForbiddenError, NotFoundError) as error:
            raise _translate(error) from None

    def upload(self, current, key, name, target, upload):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name) or not 1 <= len(target) <= 128:
            raise ApiError(422, 'INVALID_DATASET_NAME', 'Nom logique ou cible invalide.')
        if (not (upload.filename or '').lower().endswith('.csv')
                or upload.content_type not in ('text/csv', 'application/csv', 'text/plain', 'application/octet-stream')):
            raise ApiError(415, 'CSV_REQUIRED', 'Un fichier CSV est requis.')
        stored = None
        committing = False
        storage = self.storage()
        try:
            with self.access(current, key, write=True) as (session, project):
                stored, size, digest = storage.stage(upload.file, self.settings.max_dataset_bytes)
                with storage.open(stored) as stream:
                    columns, count = validate(stream, target)
                session.execute(select(Project.id).where(Project.id == project.id).with_for_update()).scalar_one()
                version = session.scalar(select(func.max(DatasetVersion.version)).where(
                    DatasetVersion.project_id == project.id, DatasetVersion.name == name)) or 0
                row = DatasetVersion(id=stored, project_id=project.id, name=name,
                    version=version + 1, target=target, sha256=digest, size_bytes=size,
                    row_count=count, columns=columns, author_id=current.identity)
                session.add(row)
                session.flush()
                result = metadata(row)
                committing = True
            return result
        except IntegrityError:
            raise ApiError(409, 'DATASET_VERSION_CONFLICT', 'Publication concurrente, réessayer.') from None
        finally:
            # Un commit incertain peut avoir réussi côté serveur : ne jamais effacer
            # ici un fichier valide. Les orphelins sont collectés hors ligne.
            if stored is not None and not committing:
                storage.discard(stored)

    def list(self, current, key, offset=0, limit=100):
        with self.access(current, key) as (session, project):
            return [metadata(row) for row in session.scalars(select(DatasetVersion).where(
                DatasetVersion.project_id == project.id).order_by(
                DatasetVersion.name, DatasetVersion.version).offset(offset).limit(limit))]

    def get(self, current, key, identity):
        with self.access(current, key) as (session, project):
            row = session.scalar(select(DatasetVersion).where(
                DatasetVersion.project_id == project.id, DatasetVersion.id == identity))
            if row is None:
                raise ApiError(404, 'DATASET_NOT_FOUND', 'Ressource introuvable.')
            return metadata(row)
