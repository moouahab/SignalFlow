"""Modèles ORM : projets, membres, jetons hachés et versions immuables."""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _identifier() -> str:
    """Identifiant texte portable entre PostgreSQL et SQLite."""
    return uuid.uuid4().hex


def _now() -> datetime:
    """Horodatage UTC conscient, stocké de façon portable."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Déclaration commune, sans fonctionnalité automatique."""


class User(Base):
    """Identité locale persistée ; le rôle global est contrôlé par l'opérateur."""

    __tablename__ = "sf_users"
    __table_args__ = (
        UniqueConstraint("subject", name="uq_sf_users_subject"),
        Index("ix_sf_users_subject", "subject", unique=True),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_identifier)
    subject: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tokens: Mapped[list["Token"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    memberships: Mapped[list["Membership"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Token(Base):
    """Jeton opaque stocké uniquement sous forme d'empreinte SHA-256."""

    __tablename__ = "sf_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_sf_tokens_hash"),
        Index("ix_sf_tokens_token_hash", "token_hash", unique=True),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_identifier)
    user_id: Mapped[str] = mapped_column(ForeignKey("sf_users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="tokens")


class Project(Base):
    """Projet métier, distinct de toute configuration statique du moteur."""

    __tablename__ = "sf_projects"
    __table_args__ = (
        UniqueConstraint("key", name="uq_sf_projects_key"),
        Index("ix_sf_projects_key", "key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_identifier)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(512), default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("sf_users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    versions: Mapped[list["ConfigurationVersion"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ConfigurationVersion.version"
    )


class Membership(Base):
    """Appartenance et rôle persistant d'un utilisateur à un projet."""

    __tablename__ = "sf_memberships"

    project_id: Mapped[str] = mapped_column(ForeignKey("sf_projects.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("sf_users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class ConfigurationVersion(Base):
    """Version append-only d'une configuration validée, avec parent et empreinte."""

    __tablename__ = "sf_configuration_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_sf_config_project_version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_identifier)
    project_id: Mapped[str] = mapped_column(ForeignKey("sf_projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    author_id: Mapped[str] = mapped_column(ForeignKey("sf_users.id"))
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="versions")


class DatasetVersion(Base):
    """CSV publié, immuable ; la clé de stockage est un UUID interne."""

    __tablename__ = 'sf_dataset_versions'
    __table_args__ = (UniqueConstraint('project_id', 'name', 'version',
                                       name='uq_sf_dataset_version'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_identifier)
    project_id: Mapped[str] = mapped_column(ForeignKey('sf_projects.id'), index=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    target: Mapped[str] = mapped_column(String(128))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    row_count: Mapped[int] = mapped_column(Integer)
    columns: Mapped[list] = mapped_column(JSON)
    author_id: Mapped[str] = mapped_column(ForeignKey('sf_users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    """Soumission durable et snapshot indépendant de la configuration."""
    __tablename__ = 'sf_jobs'
    __table_args__ = (UniqueConstraint('project_id', 'idempotency_key', name='uq_sf_job_idempotency'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_identifier)
    project_id: Mapped[str] = mapped_column(ForeignKey('sf_projects.id'), index=True)
    configuration_id: Mapped[str] = mapped_column(ForeignKey('sf_configuration_versions.id'))
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey('sf_dataset_versions.id'), nullable=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('sf_users.id'))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_digest: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24), default='queued', index=True)
    event_seq: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracking_sync: Mapped[str] = mapped_column(String(16), default='none')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class JobAttempt(Base):
    __tablename__ = 'sf_job_attempts'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_identifier)
    job_id: Mapped[str] = mapped_column(ForeignKey('sf_jobs.id'), unique=True)
    state: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobEvent(Base):
    __tablename__ = 'sf_job_events'
    job_id: Mapped[str] = mapped_column(ForeignKey('sf_jobs.id'), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class JobOutbox(Base):
    """Réconciliation MLflow rejouable, créée dans la transaction terminale."""
    __tablename__ = 'sf_job_outbox'
    job_id: Mapped[str] = mapped_column(ForeignKey('sf_jobs.id'), primary_key=True)
    state: Mapped[str] = mapped_column(String(24))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelRegistration(Base):
    """Demande humaine durable ; MLflow reste propriétaire des versions et alias."""
    __tablename__ = 'sf_model_registrations'
    __table_args__ = (UniqueConstraint('project_id', 'name', 'job_id', name='uq_sf_model_job'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_identifier)
    project_id: Mapped[str] = mapped_column(ForeignKey('sf_projects.id'), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('sf_jobs.id'))
    name: Mapped[str] = mapped_column(String(64))
    author_id: Mapped[str] = mapped_column(ForeignKey('sf_users.id'))
    reason: Mapped[str] = mapped_column(String(1024))
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ModelAliasEvent(Base):
    """Intention auditable avant écriture MLflow ; aucune copie d'alias courant."""
    __tablename__ = 'sf_model_alias_events'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_identifier)
    project_id: Mapped[str] = mapped_column(ForeignKey('sf_projects.id'), index=True)
    name: Mapped[str] = mapped_column(String(64))
    alias: Mapped[str] = mapped_column(String(64))
    previous_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[str] = mapped_column(String(32))
    author_id: Mapped[str] = mapped_column(ForeignKey('sf_users.id'))
    reason: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
