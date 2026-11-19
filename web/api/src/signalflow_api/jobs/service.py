"""Soumettre, consulter et annuler sans exécuter le moteur dans l’API."""
import copy
import hashlib
import json

from sqlalchemy import select
from pydantic import BaseModel, ConfigDict, Field

from signalflow.persistence.models import ConfigurationVersion, DatasetVersion, Job, JobEvent, Project
from ..datasets.service import DatasetService
from ..errors import ApiError
from .events import record
from .states import TERMINAL, transition


class SubmitJob(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    configuration_version: int = Field(ge=1)
    dataset_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')


def public(job):
    return {key: getattr(job, key) for key in ('id', 'configuration_id', 'dataset_id',
        'state', 'attempt_id', 'result', 'error_code', 'tracking_sync', 'created_at', 'updated_at')}


class JobService(DatasetService):
    def submit(self, current, key, payload, idempotency_key):
        digest = hashlib.sha256(json.dumps(payload.model_dump(), sort_keys=True).encode()).hexdigest()
        with self.access(current, key, write=True) as (session, project):
            session.execute(select(Project.id).where(Project.id == project.id).with_for_update()).scalar_one()
            existing = session.scalar(select(Job).where(Job.project_id == project.id,
                                                        Job.idempotency_key == idempotency_key))
            if existing:
                if existing.request_digest != digest:
                    raise ApiError(409, 'IDEMPOTENCY_CONFLICT', 'Clé déjà utilisée pour une autre soumission.')
                return public(existing)
            config = session.scalar(select(ConfigurationVersion).where(
                ConfigurationVersion.project_id == project.id,
                ConfigurationVersion.version == payload.configuration_version))
            if config is None:
                raise ApiError(404, 'CONFIGURATION_NOT_FOUND', 'Ressource introuvable.')
            snapshot = copy.deepcopy(config.document)
            if payload.dataset_id:
                dataset = session.scalar(select(DatasetVersion).where(
                    DatasetVersion.id == payload.dataset_id, DatasetVersion.project_id == project.id))
                if dataset is None:
                    raise ApiError(404, 'DATASET_NOT_FOUND', 'Ressource introuvable.')
                snapshot['dataset']['source'] = {'type': 'csv', 'path': './dataset.csv', 'target': dataset.target}
                snapshot['_dataset_sha256'] = dataset.sha256
            job = Job(project_id=project.id, configuration_id=config.id, dataset_id=payload.dataset_id,
                author_id=current.identity, idempotency_key=idempotency_key, request_digest=digest,
                snapshot=snapshot, state='queued', event_seq=0)
            session.add(job)
            session.flush()
            record(session, job, 'queued')
            return public(job)

    def _job(self, session, project, identity, lock=False):
        query = select(Job).where(Job.project_id == project.id, Job.id == identity)
        job = session.scalar(query.with_for_update() if lock else query)
        if job is None:
            raise ApiError(404, 'JOB_NOT_FOUND', 'Ressource introuvable.')
        return job

    def get(self, current, key, identity):
        with self.access(current, key) as (session, project):
            return public(self._job(session, project, identity))

    def list(self, current, key, offset=0, limit=100):
        with self.access(current, key) as (session, project):
            return [public(job) for job in session.scalars(select(Job).where(
                Job.project_id == project.id).order_by(Job.created_at, Job.id).offset(offset).limit(limit))]

    def cancel(self, current, key, identity):
        with self.access(current, key, write=True) as (session, project):
            job = self._job(session, project, identity, lock=True)
            if job.state not in TERMINAL and job.state != 'cancel_requested':
                transition(job, 'cancelled' if job.state == 'queued' else 'cancel_requested')
                record(session, job, job.state)
            return public(job)

    def events(self, current, key, identity, after=0):
        with self.access(current, key) as (session, project):
            job = self._job(session, project, identity)
            return [{'id': event.sequence, 'event': event.kind, 'created_at': event.created_at.isoformat()}
                    for event in session.scalars(select(JobEvent).where(
                        JobEvent.job_id == job.id, JobEvent.sequence > after)
                        .order_by(JobEvent.sequence).limit(100))]
