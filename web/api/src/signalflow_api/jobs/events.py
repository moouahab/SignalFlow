"""Événements et changements d’état partagent la même transaction SQL."""
from datetime import datetime, timezone
from signalflow.persistence.models import JobEvent


def record(session, job, kind):
    job.event_seq += 1
    job.updated_at = datetime.now(timezone.utc)
    session.add(JobEvent(job_id=job.id, sequence=job.event_seq, kind=kind))
