"""Demandes de promotion et intentions d'alias, sans dupliquer le registre MLflow."""
from alembic import op
import sqlalchemy as sa

revision = '0005_models'
down_revision = '0004_jobs'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('sf_model_registrations',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('project_id', sa.String(32), sa.ForeignKey('sf_projects.id'), nullable=False),
        sa.Column('job_id', sa.String(32), sa.ForeignKey('sf_jobs.id'), nullable=False),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('author_id', sa.String(32), sa.ForeignKey('sf_users.id'), nullable=False),
        sa.Column('reason', sa.String(1024), nullable=False),
        sa.Column('version', sa.String(32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('project_id', 'name', 'job_id', name='uq_sf_model_job'))
    op.create_index('ix_sf_model_registrations_project_id', 'sf_model_registrations', ['project_id'])
    op.create_table('sf_model_alias_events',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('project_id', sa.String(32), sa.ForeignKey('sf_projects.id'), nullable=False),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('alias', sa.String(64), nullable=False),
        sa.Column('previous_version', sa.String(32), nullable=True),
        sa.Column('version', sa.String(32), nullable=False),
        sa.Column('author_id', sa.String(32), sa.ForeignKey('sf_users.id'), nullable=False),
        sa.Column('reason', sa.String(1024), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_sf_model_alias_events_project_id', 'sf_model_alias_events', ['project_id'])


def downgrade():
    op.drop_table('sf_model_alias_events')
    op.drop_table('sf_model_registrations')
