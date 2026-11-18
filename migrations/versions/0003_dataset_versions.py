"""Versions immuables de datasets CSV."""
from alembic import op
import sqlalchemy as sa

revision = '0003_dataset_versions'
down_revision = '0002_token_expiration'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('sf_dataset_versions',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('project_id', sa.String(32), sa.ForeignKey('sf_projects.id'), nullable=False),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('target', sa.String(128), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('columns', sa.JSON(), nullable=False),
        sa.Column('author_id', sa.String(32), sa.ForeignKey('sf_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('project_id', 'name', 'version', name='uq_sf_dataset_version'))
    op.create_index('ix_sf_dataset_versions_project_id', 'sf_dataset_versions', ['project_id'])


def downgrade():
    op.drop_table('sf_dataset_versions')
