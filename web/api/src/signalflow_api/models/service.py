"""Promotion explicite et alias avec comparaison de version, sans chargement de modèle."""
from contextlib import contextmanager
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from signalflow.persistence.models import ModelAliasEvent, ModelRegistration
from ..errors import ApiError
from ..jobs.service import JobService
from .mlflow_registry import MLflowRegistry, source_package
from .registry import registry_lock

Name = Annotated[str, Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')]
Version = Annotated[str, Field(pattern=r'^[1-9][0-9]{0,15}$')]


class PromoteModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: Name
    run_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    reason: str = Field(min_length=1, max_length=1024, pattern=r'\S')


class MoveAlias(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Version
    expected_version: Version | None
    reason: str = Field(min_length=1, max_length=1024, pattern=r'\S')


@contextmanager
def backend(settings):
    try:
        with registry_lock(settings) as root:
            yield MLflowRegistry(root)
    except ApiError:
        raise
    except Exception:
        raise ApiError(503, 'REGISTRY_UNAVAILABLE', 'Registre indisponible ; réessayer la même demande.') from None


class ModelService(JobService):
    def promote(self, current, key, payload):
        # Contrôle d'accès avant toute création de stockage ou import MLflow.
        with self.access(current, key, write=True):
            pass
        with backend(self.settings) as registry:
            with self.access(current, key, write=True) as (session, project):
                job = self._job(session, project, payload.run_id)
                if job.state != 'succeeded' or job.tracking_sync != 'synced':
                    raise ApiError(409, 'RUN_NOT_SUCCEEDED', 'Un run réussi et synchronisé est requis.')
                name = registry.name(project.id, payload.name)
                registration = session.scalar(select(ModelRegistration).where(
                    ModelRegistration.project_id == project.id,
                    ModelRegistration.name == payload.name, ModelRegistration.job_id == job.id))
                if registration and registration.version:
                    return registry.public(registry.client.get_model_version(name, registration.version))
                package, manifest = source_package(self.settings, job)
                if registration is None:
                    registration = ModelRegistration(project_id=project.id, job_id=job.id,
                        name=payload.name, author_id=current.identity, reason=payload.reason)
                    session.add(registration)
                    session.flush()
                identity = registration.id
                # L'intention est commitée AVANT l'effet externe. En cas de panne,
                # les tags MLflow permettent de retrouver la même version.
            version = registry.register(name, registration, job, package, manifest)
            with self.access(current, key, write=True) as (session, project):
                session.get(ModelRegistration, identity).version = str(version.version)
            return registry.public(version)

    def list_models(self, current, key):
        with self.access(current, key) as (session, project):
            names = list(session.scalars(select(ModelRegistration.name).where(
                ModelRegistration.project_id == project.id).distinct().order_by(ModelRegistration.name)))
        return [{'name': name} for name in names]

    def versions(self, current, key, name):
        with self.access(current, key) as (session, project):
            internal = MLflowRegistry.name(project.id, name)
        with backend(self.settings) as registry:
            return [registry.public(value) for value in registry.versions(internal)]

    def get_alias(self, current, key, name, alias):
        with self.access(current, key) as (session, project):
            internal = MLflowRegistry.name(project.id, name)
        with backend(self.settings) as registry:
            return {'alias': alias, 'version': registry.alias(internal, alias)}

    def move_alias(self, current, key, name, alias, payload):
        if alias.lower() in ('latest',) or (alias.startswith('v') and alias[1:].isdigit()):
            raise ApiError(422, 'INVALID_ALIAS', 'Alias réservé.')
        with self.access(current, key, write=True):
            pass
        with backend(self.settings) as registry:
            with self.access(current, key, write=True) as (session, project):
                internal = registry.name(project.id, name)
                # Seules les versions promues et confirmées dans ce projet sont éligibles.
                row = session.scalar(select(ModelRegistration).where(
                    ModelRegistration.project_id == project.id, ModelRegistration.name == name,
                    ModelRegistration.version == payload.version))
                if row is None:
                    raise ApiError(404, 'MODEL_VERSION_NOT_FOUND', 'Version introuvable.')
                version = registry.client.get_model_version(internal, payload.version)
                if version.status != 'READY':
                    raise ApiError(409, 'MODEL_NOT_READY', 'Version non disponible.')
                previous = registry.alias(internal, alias)
                if previous != payload.expected_version:
                    raise ApiError(409, 'ALIAS_CONFLICT', 'Alias modifié ; relire sa version avant de réessayer.')
                session.add(ModelAliasEvent(project_id=project.id, name=name, alias=alias,
                    previous_version=previous, version=payload.version,
                    author_id=current.identity, reason=payload.reason))
            registry.client.set_registered_model_alias(internal, alias, payload.version)
            actual = registry.alias(internal, alias)
            if actual != payload.version:
                raise ApiError(409, 'ALIAS_CONFLICT', 'Écriture concurrente du registre.')
            return {'alias': alias, 'version': actual}

    def audit(self, current, key, name):
        with self.access(current, key) as (session, project):
            rows = session.scalars(select(ModelAliasEvent).where(
                ModelAliasEvent.project_id == project.id, ModelAliasEvent.name == name)
                .order_by(ModelAliasEvent.created_at.desc()).limit(100))
            return [{'id': row.id, 'alias': row.alias, 'previous_version': row.previous_version,
                     'version': row.version, 'author_id': row.author_id, 'reason': row.reason,
                     'created_at': row.created_at, 'kind': 'alias_change_requested'} for row in rows]
