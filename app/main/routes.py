import time
from threading import Lock
from urllib.parse import urljoin, urlparse

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, text

from app.extensions import db
from app.models import Archive, Hashtag, Mention, Post, post_hashtags, post_mentions
from app.services.i18n import set_current_language
from app.services.ops_metrics import get_operation_metrics_summary, get_recent_operation_metrics


main_bp = Blueprint("main", __name__)
_DASHBOARD_CACHE_LOCK = Lock()
_DASHBOARD_CACHE: dict[str, tuple[float, dict]] = {}


@main_bp.route("/")
def root():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@main_bp.route("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200


@main_bp.route("/readyz")
def readyz():
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify({"status": "ready", "database": "ok"}), 200
    except Exception:
        current_app.logger.exception("readiness_check_failed")
        db.session.rollback()
        return jsonify({"status": "not-ready", "database": "error"}), 503


@main_bp.route("/favicon.ico")
def favicon():
    return send_from_directory(
        current_app.static_folder,
        "img/favicon.svg",
        mimetype="image/svg+xml",
    )


@main_bp.route("/dashboard")
@login_required
def dashboard():
    archives_page = _as_int(request.args.get("archives_page"), default=1, minimum=1)
    archives_per_page = max(4, min(20, int(current_app.config.get("DASHBOARD_ARCHIVES_PER_PAGE", 8))))
    archives_page_data = Archive.query.order_by(Archive.id.desc()).paginate(
        page=archives_page,
        per_page=archives_per_page,
        error_out=False,
    )

    metrics_hours = 24
    stats = _get_dashboard_stats(hours=metrics_hours)

    return render_template(
        "dashboard.html",
        archives_page_data=archives_page_data,
        archives=archives_page_data.items,
        total_posts=stats["total_posts"],
        total_replies=stats["total_replies"],
        with_links=stats["with_links"],
        with_media=stats["with_media"],
        min_date=stats["min_date"],
        max_date=stats["max_date"],
        top_hashtags=stats["top_hashtags"],
        top_mentions=stats["top_mentions"],
        ops_summary=stats["ops_summary"],
        recent_ops=stats["recent_ops"],
        metrics_hours=metrics_hours,
    )


@main_bp.route("/ops/metrics")
@login_required
def ops_metrics():
    hours = request.args.get("hours", default=24, type=int) or 24
    limit = request.args.get("limit", default=20, type=int) or 20
    summary = get_operation_metrics_summary(hours=hours)
    recent = get_recent_operation_metrics(hours=hours, limit=limit)

    return jsonify(
        {
            "window_hours": max(1, min(hours, 24 * 30)),
            "summary": summary,
            "recent": [
                {
                    "id": metric.id,
                    "archive_id": metric.archive_id,
                    "operation_type": metric.operation_type,
                    "source": metric.source,
                    "status": metric.status,
                    "started_at": metric.started_at.isoformat() if metric.started_at else None,
                    "finished_at": metric.finished_at.isoformat() if metric.finished_at else None,
                    "duration_ms": metric.duration_ms,
                    "attempt_count": metric.attempt_count,
                    "total_items": metric.total_items,
                    "processed_items": metric.processed_items,
                    "imported_count": metric.imported_count,
                    "duplicate_count": metric.duplicate_count,
                    "reply_count": metric.reply_count,
                    "fetched_count": metric.fetched_count,
                    "pages_loaded": metric.pages_loaded,
                    "error_message": metric.error_message,
                }
                for metric in recent
            ],
        }
    )


@main_bp.route("/language", methods=["POST"])
def set_language():
    lang = request.form.get("lang")
    set_current_language(lang or "")
    next_url = request.form.get("next") or request.referrer or url_for("main.root")
    if _is_safe_redirect_target(next_url):
        return redirect(next_url)
    return redirect(url_for("main.root"))


def _is_safe_redirect_target(target: str) -> bool:
    host_url = request.host_url
    reference = urlparse(host_url)
    test_url = urlparse(urljoin(host_url, target))
    return test_url.scheme in {"http", "https"} and reference.netloc == test_url.netloc


def _as_int(value, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed


def _get_dashboard_stats(hours: int) -> dict:
    ttl_seconds = max(5, int(current_app.config.get("DASHBOARD_CACHE_TTL_SECONDS", 30)))
    cache_key = f"dashboard:{hours}"
    now = time.monotonic()

    with _DASHBOARD_CACHE_LOCK:
        cached = _DASHBOARD_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

    data = {
        "total_posts": db.session.query(func.count(Post.id)).scalar() or 0,
        "total_replies": db.session.query(func.count(Post.id)).filter(Post.is_reply.is_(True)).scalar() or 0,
        "with_links": db.session.query(func.count(Post.id)).filter(Post.has_links.is_(True)).scalar() or 0,
        "with_media": db.session.query(func.count(Post.id)).filter(Post.has_media.is_(True)).scalar() or 0,
        "top_hashtags": (
            db.session.query(Hashtag.tag, func.count(post_hashtags.c.post_id).label("count"))
            .join(post_hashtags, post_hashtags.c.hashtag_id == Hashtag.id)
            .group_by(Hashtag.id)
            .order_by(func.count(post_hashtags.c.post_id).desc())
            .limit(10)
            .all()
        ),
        "top_mentions": (
            db.session.query(Mention.handle, func.count(post_mentions.c.post_id).label("count"))
            .join(post_mentions, post_mentions.c.mention_id == Mention.id)
            .group_by(Mention.id)
            .order_by(func.count(post_mentions.c.post_id).desc())
            .limit(10)
            .all()
        ),
        "ops_summary": get_operation_metrics_summary(hours=hours),
        "recent_ops": get_recent_operation_metrics(hours=hours, limit=10),
    }
    data["min_date"], data["max_date"] = db.session.query(func.min(Post.created_at), func.max(Post.created_at)).one()

    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE[cache_key] = (now + ttl_seconds, data)
    return data
