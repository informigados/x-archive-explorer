"""add operation metrics

Revision ID: 2d6d9f8c1a10
Revises: f3a4b9b3d4a1
Create Date: 2026-04-07 10:45:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2d6d9f8c1a10"
down_revision = "f3a4b9b3d4a1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operation_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("archive_id", sa.Integer(), nullable=True),
        sa.Column("operation_type", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("total_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_loaded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["archive_id"], ["archives.id"]),
    )
    op.create_index("ix_operation_metrics_archive_id", "operation_metrics", ["archive_id"], unique=False)
    op.create_index(
        "ix_operation_metrics_operation_type",
        "operation_metrics",
        ["operation_type"],
        unique=False,
    )
    op.create_index("ix_operation_metrics_started_at", "operation_metrics", ["started_at"], unique=False)
    op.create_index("ix_operation_metrics_status", "operation_metrics", ["status"], unique=False)


def downgrade():
    op.drop_index("ix_operation_metrics_status", table_name="operation_metrics")
    op.drop_index("ix_operation_metrics_started_at", table_name="operation_metrics")
    op.drop_index("ix_operation_metrics_operation_type", table_name="operation_metrics")
    op.drop_index("ix_operation_metrics_archive_id", table_name="operation_metrics")
    op.drop_table("operation_metrics")
