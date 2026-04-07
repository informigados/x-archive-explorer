import os
import re
import secrets
import sys
import time
from pathlib import Path
from uuid import uuid4

import click
from flask import Flask, g, redirect, render_template, request
from flask_login import current_user
from sqlalchemy import inspect, or_
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config
from app.extensions import csrf, db, login_manager, migrate
from app.models import User
from app.services.import_queue import process_next_queued_job, requeue_stalled_jobs
from app.services.i18n import get_current_language, get_language_html_code, get_language_options, t
from app.services.ops_metrics import prune_operation_metrics
from app.services.search import ensure_fts_table

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    _apply_secret_key_policy(app)

    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Faça login para continuar."
    register_proxy_support(app)
    register_request_observability(app)
    register_https_enforcement(app)

    register_blueprints(app)
    register_commands(app)
    register_error_handlers(app)
    register_context_processors(app)
    register_security_headers(app)
    running_db_command = _is_flask_db_command()

    with app.app_context():
        if app.config["AUTO_CREATE_SCHEMA"] and not running_db_command:
            db.create_all()
            ensure_fts_table()
        if not running_db_command:
            ensure_admin_user(app)

    return app


def register_blueprints(app: Flask) -> None:
    from app.archives.routes import archives_bp
    from app.auth.routes import auth_bp
    from app.main.routes import main_bp
    from app.search.routes import search_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(archives_bp)
    app.register_blueprint(search_bp)


def ensure_admin_user(app: Flask) -> None:
    if not _table_exists("users"):
        app.logger.warning("Tabela 'users' inexistente. Execute 'flask db upgrade' antes de iniciar o login.")
        return

    has_default_flag = _users_has_default_flag()
    has_role_column = _users_has_role_column()
    if not has_default_flag:
        raise RuntimeError(
            "Esquema de usuários desatualizado. Execute 'flask --app run.py db upgrade' antes de iniciar o sistema."
        )
    if not has_role_column:
        raise RuntimeError(
            "Esquema de papéis de usuário desatualizado. Execute 'flask --app run.py db upgrade' antes de iniciar o sistema."
        )

    first_user = User.query.order_by(User.id.asc()).first()
    if first_user:
        dirty = False
        default_user = User.query.filter_by(is_system_default=True).first()
        if not default_user:
            first_user.is_system_default = True
            default_user = first_user
            dirty = True

        for user in User.query.all():
            normalized_role = (user.role or "").strip().lower()
            if normalized_role not in User.ROLE_CHOICES:
                user.role = User.ROLE_VIEWER
                dirty = True

        if default_user and default_user.role != User.ROLE_ADMIN:
            default_user.role = User.ROLE_ADMIN
            dirty = True

        if dirty:
            db.session.commit()
        return

    email = os.environ.get("XAE_ADMIN_EMAIL", "admin@localhost").strip().lower()
    username = (os.environ.get("XAE_ADMIN_USERNAME", "admin") or "admin").strip().lower()
    password = os.environ.get("XAE_ADMIN_PASSWORD")
    if not password:
        if not _is_development_environment(app):
            raise RuntimeError(
                "XAE_ADMIN_PASSWORD é obrigatório fora de desenvolvimento. Defina a variável e reinicie o sistema."
            )
        password = "change-this-password"
        app.logger.warning(
            "Admin inicial criado sem XAE_ADMIN_PASSWORD em desenvolvimento. "
            "A senha padrão de desenvolvimento foi aplicada e deve ser alterada imediatamente. E-mail: %s",
            email,
        )

    admin = User(name=username, email=email, role=User.ROLE_ADMIN)
    if has_default_flag:
        admin.is_system_default = True
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()


def register_commands(app: Flask) -> None:
    @app.cli.command("create-admin")
    @click.option("--email", required=True, help="Email do administrador")
    @click.option("--name", default="admin", show_default=True)
    @click.option("--password", required=True, help="Senha do administrador")
    def create_admin(email: str, name: str, password: str) -> None:
        email = email.strip().lower()
        name = name.strip().lower()
        existing = User.query.filter_by(email=email).first()
        if existing:
            raise click.ClickException("Já existe usuário com este e-mail.")
        existing_username = User.query.filter_by(name=name).first()
        if existing_username:
            raise click.ClickException("Já existe usuário com este nome.")

        user = User(name=name, email=email, role=User.ROLE_ADMIN)
        if _users_has_default_flag():
            user.is_system_default = False
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin criado: {email}")

    @app.cli.command("reset-user-password")
    @click.option("--identity", required=True, help="E-mail ou username do usuário")
    @click.option("--password", required=True, help="Nova senha do usuário")
    def reset_user_password(identity: str, password: str) -> None:
        normalized = identity.strip().lower()
        user = User.query.filter(or_(User.email == normalized, User.name == normalized)).first()
        if not user:
            raise click.ClickException("Usuário não encontrado para o identificador informado.")

        user.set_password(password)
        db.session.commit()
        click.echo(f"Senha redefinida com sucesso para: {user.email}")

    @app.cli.command("run-import-worker")
    @click.option("--once", is_flag=True, help="Processa no máximo um job e encerra.")
    @click.option("--max-jobs", default=100, show_default=True, type=int)
    @click.option("--sleep", "sleep_seconds", default=None, type=float)
    def run_import_worker(once: bool, max_jobs: int, sleep_seconds: float | None) -> None:
        processed = 0
        idle_sleep = sleep_seconds if sleep_seconds is not None else app.config["IMPORT_WORKER_SLEEP_SECONDS"]
        recovered = requeue_stalled_jobs(app.config["IMPORT_STALLED_JOB_MINUTES"])
        click.echo("Worker de importação iniciado.")
        if recovered:
            click.echo(f"Jobs travados recuperados automaticamente: {recovered}.")
        while True:
            job = process_next_queued_job()
            if job:
                processed += 1
                click.echo(f"Job #{job.id} processado com status={job.status}.")
            else:
                if once:
                    break
                time.sleep(max(0.2, idle_sleep))

            if once:
                break
            if processed >= max_jobs:
                break

        click.echo(f"Worker finalizado. Jobs processados: {processed}.")

    @app.cli.command("prune-op-metrics")
    @click.option("--days", default=None, type=int, help="Dias de retenção para métricas operacionais.")
    def prune_op_metrics(days: int | None) -> None:
        retention_days = days if days is not None else app.config["OPS_METRICS_RETENTION_DAYS"]
        deleted = prune_operation_metrics(retention_days)
        click.echo(f"Métrica(s) removida(s): {deleted}. Retenção aplicada: {retention_days} dia(s).")


def _table_exists(table_name: str) -> bool:
    return inspect(db.engine).has_table(table_name)


def _users_has_default_flag() -> bool:
    if not _table_exists("users"):
        return False
    columns = {column["name"] for column in inspect(db.engine).get_columns("users")}
    return "is_system_default" in columns


def _users_has_role_column() -> bool:
    if not _table_exists("users"):
        return False
    columns = {column["name"] for column in inspect(db.engine).get_columns("users")}
    return "role" in columns


def _is_flask_db_command() -> bool:
    normalized_args = [arg.strip().lower() for arg in sys.argv[1:]]
    if "db" not in normalized_args:
        return False
    db_index = normalized_args.index("db")
    db_subcommands = {
        "init",
        "migrate",
        "revision",
        "merge",
        "upgrade",
        "downgrade",
        "show",
        "history",
        "heads",
        "branches",
        "current",
        "stamp",
        "check",
    }
    if db_index + 1 >= len(normalized_args):
        return True
    return normalized_args[db_index + 1] in db_subcommands


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def too_large(_error):
        return render_template("errors/413.html"), 413

    @app.errorhandler(500)
    def internal_error(_error):
        db.session.rollback()
        return render_template("errors/500.html"), 500


def register_context_processors(app: Flask) -> None:
    @app.context_processor
    def inject_i18n():
        return {
            "t": t,
            "current_language": get_current_language(),
            "language_options": get_language_options(),
            "language_html_code": get_language_html_code(),
        }


def register_security_headers(app: Flask) -> None:
    @app.after_request
    def apply_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "img-src 'self' data: https:; "
                "media-src 'self' https:; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; "
                "font-src 'self'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            ),
        )
        if app.config.get("ENABLE_HSTS") and _request_is_secure(app):
            response.headers.setdefault("Strict-Transport-Security", _build_hsts_value(app))
        if _is_sensitive_response(request.path):
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("Pragma", "no-cache")
            response.headers.setdefault("Expires", "0")
        return response


def register_request_observability(app: Flask) -> None:
    app.logger.setLevel(app.config.get("LOG_LEVEL", "INFO"))

    @app.before_request
    def attach_request_context():
        g.request_start = time.perf_counter()
        g.request_id = _resolve_request_id(app)

    @app.after_request
    def append_request_id_and_access_log(response):
        request_id = getattr(g, "request_id", None) or uuid4().hex
        response.headers.setdefault(app.config.get("REQUEST_ID_HEADER", "X-Request-ID"), request_id)

        if app.config.get("ACCESS_LOG_ENABLED"):
            duration_ms = max(0, int((time.perf_counter() - getattr(g, "request_start", time.perf_counter())) * 1000))
            user_id = getattr(current_user, "id", None) if getattr(current_user, "is_authenticated", False) else None
            app.logger.info(
                "access request_id=%s method=%s path=%s status=%s duration_ms=%s user_id=%s ip=%s",
                request_id,
                request.method,
                request.path,
                response.status_code,
                duration_ms,
                user_id,
                _get_client_ip(app),
            )
        return response


def register_proxy_support(app: Flask) -> None:
    if not app.config.get("TRUST_PROXY_HEADERS"):
        return
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=max(0, int(app.config.get("PROXY_FIX_X_FOR", 1))),
        x_proto=max(0, int(app.config.get("PROXY_FIX_X_PROTO", 1))),
        x_host=max(0, int(app.config.get("PROXY_FIX_X_HOST", 0))),
        x_port=max(0, int(app.config.get("PROXY_FIX_X_PORT", 0))),
        x_prefix=max(0, int(app.config.get("PROXY_FIX_X_PREFIX", 0))),
    )


def register_https_enforcement(app: Flask) -> None:
    @app.before_request
    def enforce_https():
        if not app.config.get("FORCE_HTTPS"):
            return None
        if _request_is_secure(app):
            return None

        host = (request.host.split(":", 1)[0] if request.host else "").strip().lower()
        if host in {"localhost", "127.0.0.1", "::1"}:
            return None

        if request.method in {"GET", "HEAD"}:
            secure_url = request.url.replace("http://", "https://", 1)
            return redirect(secure_url, code=301)
        return "HTTPS required", 400


def _is_development_environment(app: Flask) -> bool:
    return str(app.config.get("ENVIRONMENT", "development")).strip().lower() == "development"


def _apply_secret_key_policy(app: Flask) -> None:
    weak_values = {"", "dev-change-me", "change-me", "changeme", "default", "secret", "123456"}
    configured = str(app.config.get("SECRET_KEY") or "").strip()
    from_env = str(os.environ.get("XAE_SECRET_KEY") or "").strip()
    configured_is_weak = configured.lower() in weak_values
    env_is_missing = from_env == ""

    if _is_development_environment(app):
        if env_is_missing or configured_is_weak:
            app.config["SECRET_KEY"] = secrets.token_urlsafe(48)
            app.logger.warning(
                "XAE_SECRET_KEY ausente ou fraca em desenvolvimento. Uma chave efêmera foi gerada para esta execução."
            )
        return

    if env_is_missing or configured_is_weak:
        raise RuntimeError(
            "XAE_SECRET_KEY ausente ou fraca para ambiente não-desenvolvimento. Defina um segredo forte antes de iniciar."
        )


def _request_is_secure(app: Flask) -> bool:
    if request.is_secure:
        return True
    if app.config.get("TRUST_PROXY_HEADERS"):
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
        if forwarded_proto == "https":
            return True
    return False


def _build_hsts_value(app: Flask) -> str:
    max_age = max(0, int(app.config.get("HSTS_MAX_AGE_SECONDS", 31536000)))
    value = f"max-age={max_age}"
    if app.config.get("HSTS_INCLUDE_SUBDOMAINS"):
        value += "; includeSubDomains"
    if app.config.get("HSTS_PRELOAD"):
        value += "; preload"
    return value


def _is_sensitive_response(path: str) -> bool:
    lowered_path = (path or "").lower()
    if lowered_path.startswith("/static/"):
        return False
    if lowered_path in {"/healthz", "/readyz", "/favicon.ico"}:
        return False
    if lowered_path.startswith("/auth"):
        return True
    try:
        return bool(getattr(current_user, "is_authenticated", False))
    except Exception:
        return False


def _resolve_request_id(app: Flask) -> str:
    incoming = ""
    if app.config.get("REQUEST_ID_ACCEPT_INCOMING"):
        header_name = app.config.get("REQUEST_ID_HEADER", "X-Request-ID")
        incoming = (request.headers.get(header_name) or "").strip()
    if REQUEST_ID_PATTERN.fullmatch(incoming):
        return incoming
    return uuid4().hex


def _get_client_ip(app: Flask) -> str:
    if app.config.get("TRUST_PROXY_HEADERS"):
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip().lower() or "unknown"
    return (request.remote_addr or "unknown").strip().lower()
