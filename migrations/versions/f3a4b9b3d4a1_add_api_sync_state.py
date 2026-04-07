"""add api sync state

Revision ID: f3a4b9b3d4a1
Revises: 9f2b6c6ce4f3
Create Date: 2026-04-07 10:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f3a4b9b3d4a1"
down_revision = "9f2b6c6ce4f3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_sync_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("archive_id", sa.Integer(), nullable=False),
        sa.Column("source_username", sa.String(length=100), nullable=True),
        sa.Column("source_user_id", sa.String(length=100), nullable=True),
        sa.Column("since_id", sa.String(length=64), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=20), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["archive_id"], ["archives.id"]),
        sa.UniqueConstraint("archive_id"),
    )
    op.create_index("ix_api_sync_states_archive_id", "api_sync_states", ["archive_id"], unique=True)
    op.create_index("ix_api_sync_states_since_id", "api_sync_states", ["since_id"], unique=False)


def downgrade():
    op.drop_index("ix_api_sync_states_since_id", table_name="api_sync_states")
    op.drop_index("ix_api_sync_states_archive_id", table_name="api_sync_states")
    op.drop_table("api_sync_states")
