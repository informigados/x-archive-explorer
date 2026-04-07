from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager


def utcnow():
    return datetime.now(timezone.utc)


post_hashtags = db.Table(
    "post_hashtags",
    db.Column("post_id", db.Integer, db.ForeignKey("posts.id"), primary_key=True),
    db.Column("hashtag_id", db.Integer, db.ForeignKey("hashtags.id"), primary_key=True),
)


post_mentions = db.Table(
    "post_mentions",
    db.Column("post_id", db.Integer, db.ForeignKey("posts.id"), primary_key=True),
    db.Column("mention_id", db.Integer, db.ForeignKey("mentions.id"), primary_key=True),
)


post_urls = db.Table(
    "post_urls",
    db.Column("post_id", db.Integer, db.ForeignKey("posts.id"), primary_key=True),
    db.Column("url_id", db.Integer, db.ForeignKey("urls.id"), primary_key=True),
)


class User(UserMixin, db.Model):
    __tablename__ = "users"
    ROLE_ADMIN = "admin"
    ROLE_VIEWER = "viewer"
    ROLE_CHOICES = (ROLE_ADMIN, ROLE_VIEWER)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_VIEWER, index=True)
    is_system_default = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return (self.role or self.ROLE_VIEWER).strip().lower() == self.ROLE_ADMIN


class LoginRateLimitState(db.Model):
    __tablename__ = "login_rate_limit_states"

    key = db.Column(db.String(255), primary_key=True)
    failure_count = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    blocked_until = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


class Archive(db.Model):
    __tablename__ = "archives"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    source_type = db.Column(db.String(50), default="x_archive", nullable=False)
    original_filename = db.Column(db.String(255), nullable=True)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=True)
    status = db.Column(db.String(30), default="pending", nullable=False)
    total_posts = db.Column(db.Integer, default=0, nullable=False)
    total_replies = db.Column(db.Integer, default=0, nullable=False)
    notes = db.Column(db.Text, nullable=True)

    posts = db.relationship("Post", backref="archive", lazy=True, cascade="all, delete-orphan")
    logs = db.relationship(
        "ImportLog",
        backref="archive",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="ImportLog.created_at.desc()",
    )
    jobs = db.relationship(
        "ImportJob",
        backref="archive",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="ImportJob.created_at.desc()",
    )
    api_sync_state = db.relationship(
        "ApiSyncState",
        backref="archive",
        lazy=True,
        uselist=False,
        cascade="all, delete-orphan",
    )
    operation_metrics = db.relationship(
        "OperationMetric",
        backref="archive",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="OperationMetric.started_at.desc()",
    )


class Post(db.Model):
    __tablename__ = "posts"
    __table_args__ = (
        db.UniqueConstraint("archive_id", "external_post_id", name="uq_archive_external_post"),
    )

    id = db.Column(db.Integer, primary_key=True)
    archive_id = db.Column(db.Integer, db.ForeignKey("archives.id"), nullable=False, index=True)
    external_post_id = db.Column(db.String(64), nullable=False, index=True)
    author_handle = db.Column(db.String(100), nullable=True, index=True)
    author_display_name = db.Column(db.String(255), nullable=True)
    text_raw = db.Column(db.Text, nullable=True)
    text_normalized = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    is_reply = db.Column(db.Boolean, default=False, nullable=False, index=True)
    reply_to_post_id = db.Column(db.String(64), nullable=True)
    reply_to_user_handle = db.Column(db.String(100), nullable=True)
    conversation_id = db.Column(db.String(64), nullable=True, index=True)
    has_media = db.Column(db.Boolean, default=False, nullable=False)
    has_links = db.Column(db.Boolean, default=False, nullable=False)
    language = db.Column(db.String(20), nullable=True, index=True)
    source_app = db.Column(db.String(255), nullable=True)
    raw_json = db.Column(db.Text, nullable=True)
    inserted_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    hashtags = db.relationship("Hashtag", secondary=post_hashtags, lazy="joined")
    mentions = db.relationship("Mention", secondary=post_mentions, lazy="joined")
    urls = db.relationship("Url", secondary=post_urls, lazy="joined")
    media = db.relationship("Media", backref="post", lazy=True, cascade="all, delete-orphan")


class Hashtag(db.Model):
    __tablename__ = "hashtags"

    id = db.Column(db.Integer, primary_key=True)
    tag = db.Column(db.String(120), unique=True, nullable=False, index=True)


class Mention(db.Model):
    __tablename__ = "mentions"

    id = db.Column(db.Integer, primary_key=True)
    handle = db.Column(db.String(120), unique=True, nullable=False, index=True)


class Url(db.Model):
    __tablename__ = "urls"

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.Text, nullable=False, unique=True)
    expanded_url = db.Column(db.Text, nullable=True)
    domain = db.Column(db.String(255), nullable=True, index=True)


class Media(db.Model):
    __tablename__ = "media"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    media_type = db.Column(db.String(50), nullable=True)
    media_url = db.Column(db.Text, nullable=True)
    local_path = db.Column(db.Text, nullable=True)
    preview_path = db.Column(db.Text, nullable=True)


class ImportLog(db.Model):
    __tablename__ = "import_logs"

    id = db.Column(db.Integer, primary_key=True)
    archive_id = db.Column(db.Integer, db.ForeignKey("archives.id"), nullable=False, index=True)
    level = db.Column(db.String(20), nullable=False, default="info")
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class ImportJob(db.Model):
    __tablename__ = "import_jobs"

    id = db.Column(db.Integer, primary_key=True)
    archive_id = db.Column(db.Integer, db.ForeignKey("archives.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="queued", index=True)
    import_mode = db.Column(db.String(20), nullable=False, default="merge")
    original_filename = db.Column(db.String(255), nullable=True)
    stored_path = db.Column(db.Text, nullable=False)
    progress_percent = db.Column(db.Integer, nullable=False, default=0)
    total_items = db.Column(db.Integer, nullable=False, default=0)
    processed_items = db.Column(db.Integer, nullable=False, default=0)
    imported_count = db.Column(db.Integer, nullable=False, default=0)
    duplicate_count = db.Column(db.Integer, nullable=False, default=0)
    reply_count = db.Column(db.Integer, nullable=False, default=0)
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    max_retries = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)


class ApiSyncState(db.Model):
    __tablename__ = "api_sync_states"

    id = db.Column(db.Integer, primary_key=True)
    archive_id = db.Column(db.Integer, db.ForeignKey("archives.id"), nullable=False, unique=True, index=True)
    source_username = db.Column(db.String(100), nullable=True)
    source_user_id = db.Column(db.String(100), nullable=True)
    since_id = db.Column(db.String(64), nullable=True, index=True)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_sync_status = db.Column(db.String(20), nullable=True)
    last_sync_error = db.Column(db.Text, nullable=True)


class OperationMetric(db.Model):
    __tablename__ = "operation_metrics"

    id = db.Column(db.Integer, primary_key=True)
    archive_id = db.Column(db.Integer, db.ForeignKey("archives.id"), nullable=True, index=True)
    operation_type = db.Column(db.String(40), nullable=False, index=True)
    source = db.Column(db.String(40), nullable=True)
    status = db.Column(db.String(20), nullable=False, index=True)
    started_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    attempt_count = db.Column(db.Integer, nullable=False, default=1)
    total_items = db.Column(db.Integer, nullable=False, default=0)
    processed_items = db.Column(db.Integer, nullable=False, default=0)
    imported_count = db.Column(db.Integer, nullable=False, default=0)
    duplicate_count = db.Column(db.Integer, nullable=False, default=0)
    reply_count = db.Column(db.Integer, nullable=False, default=0)
    fetched_count = db.Column(db.Integer, nullable=False, default=0)
    pages_loaded = db.Column(db.Integer, nullable=False, default=0)
    error_message = db.Column(db.Text, nullable=True)
