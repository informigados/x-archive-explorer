import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.environ.get("XAE_SECRET_KEY", "dev-change-me")
    ENVIRONMENT = os.environ.get("XAE_ENV", "development")
    DEBUG = ENVIRONMENT == "development"
    PREFERRED_URL_SCHEME = os.environ.get(
        "XAE_PREFERRED_URL_SCHEME",
        "https" if ENVIRONMENT != "development" else "http",
    ).strip().lower()
    AUTO_CREATE_SCHEMA = os.environ.get("XAE_AUTO_CREATE_SCHEMA", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "XAE_DATABASE_URI",
        f"sqlite:///{(BASE_DIR / 'instance' / 'x_archive.db').as_posix()}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = int(os.environ.get("XAE_MAX_UPLOAD_MB", "200")) * 1024 * 1024
    MEDIA_MAX_FILE_BYTES = int(os.environ.get("XAE_MEDIA_MAX_FILE_MB", "25")) * 1024 * 1024
    MEDIA_MAX_TOTAL_BYTES = int(os.environ.get("XAE_MEDIA_MAX_TOTAL_MB", "500")) * 1024 * 1024
    MEDIA_MAX_FILES = int(os.environ.get("XAE_MEDIA_MAX_FILES", "5000"))
    UPLOAD_FOLDER = os.environ.get("XAE_UPLOAD_FOLDER", str(BASE_DIR / "uploads"))
    ALLOWED_EXTENSIONS = {"zip", "json"}
    ITEMS_PER_PAGE = int(os.environ.get("XAE_ITEMS_PER_PAGE", "25"))
    USER_ITEMS_PER_PAGE = int(os.environ.get("XAE_USER_ITEMS_PER_PAGE", "25"))
    ARCHIVE_DETAIL_ITEMS_PER_PAGE = int(os.environ.get("XAE_ARCHIVE_DETAIL_ITEMS_PER_PAGE", "20"))
    CONVERSATION_ITEMS_PER_PAGE = int(os.environ.get("XAE_CONVERSATION_ITEMS_PER_PAGE", "50"))
    ARCHIVE_FILTER_MAX_OPTIONS = int(os.environ.get("XAE_ARCHIVE_FILTER_MAX_OPTIONS", "300"))
    DASHBOARD_ARCHIVES_PER_PAGE = int(os.environ.get("XAE_DASHBOARD_ARCHIVES_PER_PAGE", "8"))
    DASHBOARD_CACHE_TTL_SECONDS = int(os.environ.get("XAE_DASHBOARD_CACHE_TTL_SECONDS", "30"))
    IMPORT_ASYNC = os.environ.get("XAE_IMPORT_ASYNC", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    IMPORT_MAX_RETRIES = int(os.environ.get("XAE_IMPORT_MAX_RETRIES", "2"))
    IMPORT_WORKER_SLEEP_SECONDS = float(os.environ.get("XAE_IMPORT_WORKER_SLEEP_SECONDS", "2"))
    IMPORT_STALLED_JOB_MINUTES = int(os.environ.get("XAE_IMPORT_STALLED_JOB_MINUTES", "30"))
    LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("XAE_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "300"))
    LOGIN_RATE_LIMIT_MAX_ATTEMPTS = int(os.environ.get("XAE_LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "5"))
    LOGIN_RATE_LIMIT_BLOCK_SECONDS = int(os.environ.get("XAE_LOGIN_RATE_LIMIT_BLOCK_SECONDS", "300"))
    ARCHIVE_MEMBER_MAX_FILE_BYTES = int(os.environ.get("XAE_ARCHIVE_MEMBER_MAX_FILE_MB", "25")) * 1024 * 1024
    ARCHIVE_MAX_TOTAL_BYTES = int(os.environ.get("XAE_ARCHIVE_MAX_TOTAL_MB", "200")) * 1024 * 1024
    STORE_RAW_JSON = os.environ.get("XAE_STORE_RAW_JSON", "true").lower() in {"1", "true", "yes", "on"}
    EXPORT_MAX_POSTS = int(os.environ.get("XAE_EXPORT_MAX_POSTS", "5000"))
    X_API_ENABLED = os.environ.get("XAE_X_API_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    X_API_BASE_URL = os.environ.get("XAE_X_API_BASE_URL", "https://api.x.com/2")
    X_API_BEARER_TOKEN = os.environ.get("XAE_X_API_BEARER_TOKEN", "")
    X_API_TIMEOUT_SECONDS = float(os.environ.get("XAE_X_API_TIMEOUT_SECONDS", "20"))
    X_API_MAX_RETRIES = int(os.environ.get("XAE_X_API_MAX_RETRIES", "2"))
    X_API_BACKOFF_SECONDS = float(os.environ.get("XAE_X_API_BACKOFF_SECONDS", "1.0"))
    X_API_MAX_BACKOFF_SECONDS = float(os.environ.get("XAE_X_API_MAX_BACKOFF_SECONDS", "10.0"))
    X_API_ADAPTIVE_RATE_LIMIT_ENABLED = os.environ.get("XAE_X_API_ADAPTIVE_RATE_LIMIT_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    X_API_RATE_LIMIT_MAX_WAIT_SECONDS = float(os.environ.get("XAE_X_API_RATE_LIMIT_MAX_WAIT_SECONDS", "60"))
    OPS_METRICS_RETENTION_DAYS = int(os.environ.get("XAE_OPS_METRICS_RETENTION_DAYS", "90"))
    LOG_LEVEL = os.environ.get("XAE_LOG_LEVEL", "INFO").strip().upper()
    ACCESS_LOG_ENABLED = os.environ.get("XAE_ACCESS_LOG_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    REQUEST_ID_HEADER = os.environ.get("XAE_REQUEST_ID_HEADER", "X-Request-ID").strip() or "X-Request-ID"
    REQUEST_ID_ACCEPT_INCOMING = os.environ.get("XAE_REQUEST_ID_ACCEPT_INCOMING", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    TRUST_PROXY_HEADERS = os.environ.get("XAE_TRUST_PROXY_HEADERS", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    PROXY_FIX_X_FOR = int(os.environ.get("XAE_PROXY_FIX_X_FOR", "1"))
    PROXY_FIX_X_PROTO = int(os.environ.get("XAE_PROXY_FIX_X_PROTO", "1"))
    PROXY_FIX_X_HOST = int(os.environ.get("XAE_PROXY_FIX_X_HOST", "0"))
    PROXY_FIX_X_PORT = int(os.environ.get("XAE_PROXY_FIX_X_PORT", "0"))
    PROXY_FIX_X_PREFIX = int(os.environ.get("XAE_PROXY_FIX_X_PREFIX", "0"))
    FORCE_HTTPS = os.environ.get("XAE_FORCE_HTTPS", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    ENABLE_HSTS = os.environ.get(
        "XAE_ENABLE_HSTS",
        "true" if ENVIRONMENT != "development" else "false",
    ).lower() in {"1", "true", "yes", "on"}
    HSTS_MAX_AGE_SECONDS = int(os.environ.get("XAE_HSTS_MAX_AGE_SECONDS", "31536000"))
    HSTS_INCLUDE_SUBDOMAINS = os.environ.get("XAE_HSTS_INCLUDE_SUBDOMAINS", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    HSTS_PRELOAD = os.environ.get("XAE_HSTS_PRELOAD", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get(
        "XAE_SECURE_COOKIES",
        "true" if ENVIRONMENT != "development" else "false",
    ).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    AUTO_CREATE_SCHEMA = False
    IMPORT_ASYNC = False
    IMPORT_MAX_RETRIES = 0
    X_API_ENABLED = True
    X_API_BASE_URL = "https://api.example.com/2"
    X_API_BEARER_TOKEN = "test-token"
    X_API_TIMEOUT_SECONDS = 5.0
    X_API_MAX_RETRIES = 0
    X_API_BACKOFF_SECONDS = 0.01
    X_API_MAX_BACKOFF_SECONDS = 0.05
    X_API_ADAPTIVE_RATE_LIMIT_ENABLED = True
    X_API_RATE_LIMIT_MAX_WAIT_SECONDS = 5.0
    OPS_METRICS_RETENTION_DAYS = 7
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
