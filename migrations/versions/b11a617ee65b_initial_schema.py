"""initial schema

Revision ID: b11a617ee65b
Revises: 
Create Date: 2026-04-07 09:11:13.985786

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b11a617ee65b'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "archives",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("total_posts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_replies", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "hashtags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tag", sa.String(length=120), nullable=False),
    )
    op.create_index("ix_hashtags_tag", "hashtags", ["tag"], unique=True)

    op.create_table(
        "mentions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("handle", sa.String(length=120), nullable=False),
    )
    op.create_index("ix_mentions_handle", "mentions", ["handle"], unique=True)

    op.create_table(
        "urls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("expanded_url", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_urls_url", "urls", ["url"], unique=True)
    op.create_index("ix_urls_domain", "urls", ["domain"], unique=False)

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("archive_id", sa.Integer(), nullable=False),
        sa.Column("external_post_id", sa.String(length=64), nullable=False),
        sa.Column("author_handle", sa.String(length=100), nullable=True),
        sa.Column("author_display_name", sa.String(length=255), nullable=True),
        sa.Column("text_raw", sa.Text(), nullable=True),
        sa.Column("text_normalized", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_reply", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reply_to_post_id", sa.String(length=64), nullable=True),
        sa.Column("reply_to_user_handle", sa.String(length=100), nullable=True),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("has_media", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_links", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("source_app", sa.String(length=255), nullable=True),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("inserted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["archive_id"], ["archives.id"]),
        sa.UniqueConstraint("archive_id", "external_post_id", name="uq_archive_external_post"),
    )
    op.create_index("ix_posts_archive_id", "posts", ["archive_id"], unique=False)
    op.create_index("ix_posts_external_post_id", "posts", ["external_post_id"], unique=False)
    op.create_index("ix_posts_author_handle", "posts", ["author_handle"], unique=False)
    op.create_index("ix_posts_created_at", "posts", ["created_at"], unique=False)
    op.create_index("ix_posts_is_reply", "posts", ["is_reply"], unique=False)
    op.create_index("ix_posts_conversation_id", "posts", ["conversation_id"], unique=False)
    op.create_index("ix_posts_language", "posts", ["language"], unique=False)

    op.create_table(
        "import_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("archive_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["archive_id"], ["archives.id"]),
    )
    op.create_index("ix_import_logs_archive_id", "import_logs", ["archive_id"], unique=False)

    op.create_table(
        "media",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=50), nullable=True),
        sa.Column("media_url", sa.Text(), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("preview_path", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
    )
    op.create_index("ix_media_post_id", "media", ["post_id"], unique=False)

    op.create_table(
        "post_hashtags",
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("hashtag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["hashtag_id"], ["hashtags.id"]),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
        sa.PrimaryKeyConstraint("post_id", "hashtag_id"),
    )

    op.create_table(
        "post_mentions",
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("mention_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["mention_id"], ["mentions.id"]),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
        sa.PrimaryKeyConstraint("post_id", "mention_id"),
    )

    op.create_table(
        "post_urls",
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("url_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
        sa.ForeignKeyConstraint(["url_id"], ["urls.id"]),
        sa.PrimaryKeyConstraint("post_id", "url_id"),
    )

    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
            post_id UNINDEXED,
            text_normalized,
            author_handle,
            hashtags,
            mentions,
            tokenize='unicode61'
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS posts_fts")
    op.drop_table("post_urls")
    op.drop_table("post_mentions")
    op.drop_table("post_hashtags")
    op.drop_index("ix_media_post_id", table_name="media")
    op.drop_table("media")
    op.drop_index("ix_import_logs_archive_id", table_name="import_logs")
    op.drop_table("import_logs")
    op.drop_index("ix_posts_language", table_name="posts")
    op.drop_index("ix_posts_conversation_id", table_name="posts")
    op.drop_index("ix_posts_is_reply", table_name="posts")
    op.drop_index("ix_posts_created_at", table_name="posts")
    op.drop_index("ix_posts_author_handle", table_name="posts")
    op.drop_index("ix_posts_external_post_id", table_name="posts")
    op.drop_index("ix_posts_archive_id", table_name="posts")
    op.drop_table("posts")
    op.drop_index("ix_urls_domain", table_name="urls")
    op.drop_index("ix_urls_url", table_name="urls")
    op.drop_table("urls")
    op.drop_index("ix_mentions_handle", table_name="mentions")
    op.drop_table("mentions")
    op.drop_index("ix_hashtags_tag", table_name="hashtags")
    op.drop_table("hashtags")
    op.drop_table("archives")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
