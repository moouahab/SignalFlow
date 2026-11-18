"""Schéma applicatif initial : utilisateurs, projets et versions.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Créer les tables applicatives, sans toucher à MLflow."""
    op.create_table(
        "sf_users",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("subject", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("subject", name="uq_sf_users_subject"),
    )
    op.create_index("ix_sf_users_subject", "sf_users", ["subject"], unique=True)

    op.create_table(
        "sf_tokens",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("sf_users.id", name="fk_sf_tokens_user"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_sf_tokens_hash"),
    )
    op.create_index("ix_sf_tokens_user_id", "sf_tokens", ["user_id"])
    op.create_index("ix_sf_tokens_token_hash", "sf_tokens", ["token_hash"], unique=True)

    op.create_table(
        "sf_projects",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(512), nullable=False),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("sf_users.id", name="fk_sf_projects_creator"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key", name="uq_sf_projects_key"),
    )
    op.create_index("ix_sf_projects_key", "sf_projects", ["key"], unique=True)

    op.create_table(
        "sf_memberships",
        sa.Column("project_id", sa.String(32), sa.ForeignKey("sf_projects.id", name="fk_sf_memberships_project"), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("sf_users.id", name="fk_sf_memberships_user"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "sf_configuration_versions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("sf_projects.id", name="fk_sf_versions_project"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("author_id", sa.String(32), sa.ForeignKey("sf_users.id", name="fk_sf_versions_author"), nullable=False),
        sa.Column("parent_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "version", name="uq_sf_config_project_version"),
    )
    op.create_index("ix_sf_configuration_versions_project_id", "sf_configuration_versions", ["project_id"])


def downgrade() -> None:
    """Supprimer uniquement les tables applicatives créées ci-dessus."""
    op.drop_index("ix_sf_configuration_versions_project_id", table_name="sf_configuration_versions")
    op.drop_table("sf_configuration_versions")
    op.drop_table("sf_memberships")
    op.drop_index("ix_sf_projects_key", table_name="sf_projects")
    op.drop_table("sf_projects")
    op.drop_index("ix_sf_tokens_token_hash", table_name="sf_tokens")
    op.drop_index("ix_sf_tokens_user_id", table_name="sf_tokens")
    op.drop_table("sf_tokens")
    op.drop_index("ix_sf_users_subject", table_name="sf_users")
    op.drop_table("sf_users")
