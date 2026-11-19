"""Jobs, tentatives, journal durable et outbox de réconciliation."""
from alembic import op
import sqlalchemy as sa

revision = '0004_jobs'
down_revision = '0003_dataset_versions'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('sf_jobs',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('project_id', sa.String(32), sa.ForeignKey('sf_projects.id'), nullable=False),
        sa.Column('configuration_id', sa.String(32), sa.ForeignKey('sf_configuration_versions.id'), nullable=False),
        sa.Column('dataset_id', sa.String(32), sa.ForeignKey('sf_dataset_versions.id'), nullable=True),
        sa.Column('author_id', sa.String(32), sa.ForeignKey('sf_users.id'), nullable=False),
        sa.Column('idempotency_key', sa.String(128), nullable=False),
        sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('event_seq', sa.Integer(), nullable=False),
        sa.Column('owner', sa.String(32), nullable=True),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempt_id', sa.String(32), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('tracking_sync', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('project_id', 'idempotency_key', name='uq_sf_job_idempotency'))
    op.create_index('ix_sf_jobs_project_id', 'sf_jobs', ['project_id'])
    op.create_index('ix_sf_jobs_state', 'sf_jobs', ['state'])
    op.create_table('sf_job_attempts',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('job_id', sa.String(32), sa.ForeignKey('sf_jobs.id'), nullable=False, unique=True),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('sf_job_events',
        sa.Column('job_id', sa.String(32), sa.ForeignKey('sf_jobs.id'), primary_key=True),
        sa.Column('sequence', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('sf_job_outbox',
        sa.Column('job_id', sa.String(32), sa.ForeignKey('sf_jobs.id'), primary_key=True),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    for table in ('sf_job_outbox', 'sf_job_events', 'sf_job_attempts', 'sf_jobs'):
        op.drop_table(table)
