"""harden user identity and default flag

Revision ID: 7a9d5e3c2b1f
Revises: 2d6d9f8c1a10
Create Date: 2026-04-07 14:35:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7a9d5e3c2b1f"
down_revision = "2d6d9f8c1a10"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_system_default", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    connection = op.get_bind()

    # Normalize usernames and guarantee uniqueness before adding unique index.
    rows = connection.execute(sa.text("SELECT id, name FROM users ORDER BY id ASC")).fetchall()
    seen: set[str] = set()
    for row in rows:
        user_id = row[0]
        raw_name = (row[1] or "").strip().lower()
        base_name = raw_name or f"user{user_id}"
        candidate = base_name
        suffix = 2
        while candidate in seen:
            candidate = f"{base_name}-{suffix}"
            suffix += 1
        seen.add(candidate)
        connection.execute(
            sa.text("UPDATE users SET name = :name WHERE id = :user_id"),
            {"name": candidate, "user_id": user_id},
        )

    inspector = sa.inspect(connection)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("users")}
    if "ix_users_name" not in existing_indexes:
        op.create_index("ix_users_name", "users", ["name"], unique=True)

    has_default = connection.execute(
        sa.text("SELECT id FROM users WHERE is_system_default = 1 ORDER BY id ASC LIMIT 1")
    ).first()
    if not has_default:
        first_user = connection.execute(sa.text("SELECT id FROM users ORDER BY id ASC LIMIT 1")).first()
        if first_user:
            connection.execute(
                sa.text("UPDATE users SET is_system_default = 1 WHERE id = :user_id"),
                {"user_id": first_user[0]},
            )


def downgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("users")}
    if "ix_users_name" in existing_indexes:
        op.drop_index("ix_users_name", table_name="users")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("is_system_default")
