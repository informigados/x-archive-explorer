"""add login rate limit state table

Revision ID: 8b1f6d2a77c4
Revises: 3c8f9e71aa22
Create Date: 2026-04-07 17:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8b1f6d2a77c4"
down_revision = "3c8f9e71aa22"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "login_rate_limit_states",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_login_rate_limit_states_blocked_until",
        "login_rate_limit_states",
        ["blocked_until"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_login_rate_limit_states_blocked_until", table_name="login_rate_limit_states")
    op.drop_table("login_rate_limit_states")

