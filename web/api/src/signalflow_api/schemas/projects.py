"""Enveloppes Pydantic des projets, membres et versions de configuration."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import TrainingPayload

ROLE = Literal['viewer', 'editor', 'admin']


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    key: str = Field(min_length=2, max_length=64, pattern=r'^[a-z0-9][a-z0-9-]*$')
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default='', max_length=512)


class ProjectResponse(BaseModel):
    key: str
    name: str
    description: str
    created_at: datetime


class MembershipCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    subject: str = Field(min_length=1, max_length=128)
    role: ROLE


class MembershipResponse(BaseModel):
    subject: str
    role: str


class ConfigurationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: int | None = Field(default=None, ge=1)
    configuration: TrainingPayload


class ConfigurationVersionResponse(BaseModel):
    project_key: str
    version: int
    digest: str
    author: str
    parent_version: int | None
    created_at: datetime
    configuration: TrainingPayload


class ConfigurationListResponse(BaseModel):
    versions: list[ConfigurationVersionResponse]


class ConfigurationDiffResponse(BaseModel):
    from_version: int
    to_version: int
    changed: list[str]
