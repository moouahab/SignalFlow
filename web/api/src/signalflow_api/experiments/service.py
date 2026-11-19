from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..jobs.service import JobService
from .comparison import compare


class CompareRuns(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    run_ids: list[str] = Field(min_length=2, max_length=50)
    metric: str = Field(default='validation_accuracy', pattern=r'^(validation|test)_(accuracy|precision_macro|recall_macro|f1_macro)$')

    @field_validator('run_ids')
    @classmethod
    def distinct_runs(cls, values):
        if len(set(values)) != len(values):
            raise ValueError('identifiants distincts requis')
        return values


class ExperimentService(JobService):
    def compare(self, current, key, payload):
        with self.access(current, key) as (session, project):
            jobs = [self._job(session, project, identity) for identity in dict.fromkeys(payload.run_ids)]
            return compare(jobs, payload.metric)
