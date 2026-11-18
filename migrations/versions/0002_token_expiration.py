"""Expiration des jetons locaux (migration additive, sans effacement).

Revision ID: 0002_token_expiration
Revises: 0001_initial
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_token_expiration"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Conserver tous les jetons existants ; les anciens ont expires_at=NULL."""
    op.add_column("sf_tokens", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("sf_tokens", "expires_at")
