"""Prise atomique, bail renouvelable et clôture protégée par jeton de propriété."""
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import and_, or_, select, update
from signalflow.persistence.models import Job, JobAttempt, JobOutbox
from signalflow.persistence.session import session_scope
from .events import record
from .states import TERMINAL, transition


def now():
    return datetime.now(timezone.utc)


class Queue:
    def __init__(self, factory, lease_seconds=30):
        self.factory, self.lease_seconds = factory, lease_seconds

    def claim(self):
        with session_scope(self.factory) as session:
            expired = and_(Job.state.in_(['running', 'recovering', 'cancel_requested']), Job.lease_until < now())
            job = session.scalar(select(Job).where(or_(Job.state == 'queued', expired))
                .order_by(Job.created_at, Job.id).with_for_update(skip_locked=True).limit(1))
            if job is None:
                return None
            recovery = job.state != 'queued'
            job.owner = uuid.uuid4().hex
            job.lease_until = now() + timedelta(seconds=self.lease_seconds)
            if not recovery:
                transition(job, 'running')
                job.attempt_id = uuid.uuid4().hex
                session.add(JobAttempt(id=job.attempt_id, job_id=job.id, state='running'))
                record(session, job, 'running')
            elif job.state == 'running':
                transition(job, 'recovering')
                record(session, job, 'recovering')
            session.flush()
            return job, recovery

    def heartbeat(self, identity, owner):
        with session_scope(self.factory) as session:
            result = session.execute(update(Job).where(Job.id == identity, Job.owner == owner,
                Job.lease_until > now(), Job.state.not_in(TERMINAL)).values(
                lease_until=now() + timedelta(seconds=self.lease_seconds)))
            if result.rowcount != 1:
                return None
            return session.get(Job, identity).state

    def finish(self, identity, owner, outcome):
        with session_scope(self.factory) as session:
            job = session.scalar(select(Job).where(Job.id == identity, Job.owner == owner,
                Job.lease_until > now(), Job.state.not_in(TERMINAL)).with_for_update())
            if job is None:
                return False
            state = 'cancelled' if job.state == 'cancel_requested' else outcome['state']
            if state not in TERMINAL:
                raise ValueError('non-terminal outcome')
            transition(job, state)
            job.result = outcome.get('result') if state == 'succeeded' else None
            job.error_code = outcome.get('error_code') if state == 'failed' else None
            job.lease_until = None
            job.owner = None
            job.tracking_sync = 'pending'
            attempt = session.get(JobAttempt, job.attempt_id)
            attempt.state, attempt.finished_at = state, now()
            record(session, job, state)
            session.add(JobOutbox(job_id=job.id, state=state))
            return True

    def pending(self):
        with session_scope(self.factory) as session:
            return list(session.execute(select(Job.id, Job.attempt_id, JobOutbox.state).join(
                JobOutbox, JobOutbox.job_id == Job.id).where(JobOutbox.delivered_at.is_(None)).limit(10)))

    def delivered(self, identity):
        with session_scope(self.factory) as session:
            job = session.scalar(select(Job).where(Job.id == identity).with_for_update())
            item = session.get(JobOutbox, identity)
            if item is not None and item.delivered_at is None:
                item.delivered_at = now()
                job.tracking_sync = 'synced'
                record(session, job, 'tracking_synced')
