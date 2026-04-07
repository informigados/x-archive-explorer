"""add user roles for rbac

Revision ID: 3c8f9e71aa22
Revises: 7a9d5e3c2b1f
Create Date: 2026-04-07 16:35:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3c8f9e71aa22"
down_revision = "7a9d5e3c2b1f"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("role", sa.String(length=20), nullable=False, server_default="viewer")
        )

    connection = op.get_bind()
    connection.execute(sa.text("UPDATE users SET role = 'admin' WHERE role IS NULL OR role = ''"))
    connection.execute(sa.text("UPDATE users SET role = 'admin' WHERE is_system_default = 1"))
    connection.execute(sa.text("UPDATE users SET role = 'viewer' WHERE role NOT IN ('admin', 'viewer')"))
    op.create_index("ix_users_role", "users", ["role"], unique=False)


def downgrade():
    op.drop_index("ix_users_role", table_name="users")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("role")

