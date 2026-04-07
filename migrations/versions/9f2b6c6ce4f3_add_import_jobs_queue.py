"""add import jobs queue

Revision ID: 9f2b6c6ce4f3
Revises: b11a617ee65b
Create Date: 2026-04-07 09:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9f2b6c6ce4f3"
down_revision = "b11a617ee65b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("archive_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("import_mode", sa.String(length=20), nullable=False, server_default="merge"),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["archive_id"], ["archives.id"]),
    )
    op.create_index("ix_import_jobs_archive_id", "import_jobs", ["archive_id"], unique=False)
    op.create_index("ix_import_jobs_status", "import_jobs", ["status"], unique=False)


def downgrade():
    op.drop_index("ix_import_jobs_status", table_name="import_jobs")
    op.drop_index("ix_import_jobs_archive_id", table_name="import_jobs")
    op.drop_table("import_jobs")
