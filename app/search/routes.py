import os
from urllib.parse import urlencode
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, render_template, request, send_file, url_for
from flask_login import login_required

from app.extensions import db
from app.models import Archive, Post
from app.services.exporter import (
    export_csv_stream_response,
    export_html_response,
    export_json_stream_response,
)
from app.services.search import iterate_all_posts, search_all_posts, search_posts


search_bp = Blueprint("search", __name__)


@search_bp.route("/search")
@login_required
def search_view():
    filters = _extract_filters(request.args)
    page = _as_int(request.args.get("page"), default=1, minimum=1)
    per_page = current_app.config["ITEMS_PER_PAGE"]

    archives = _load_archive_filter_options(filters.get("archive_id"))
    page_data = search_posts(filters, page=page, per_page=per_page)

    return render_template(
        "search/index.html",
        archives=archives,
        filters=filters,
        page_data=page_data,
        make_query=_make_query,
        media_src=_media_src,
        media_is_video=_media_is_video,
    )


@search_bp.route("/posts/<int:post_id>")
@login_required
def post_detail(post_id: int):
    post = db.get_or_404(Post, post_id)
    conversation_page = None
    if post.conversation_id:
        conversation_page_num = _as_int(request.args.get("conversation_page"), default=1, minimum=1)
        conversation_per_page = max(
            1,
            min(200, int(current_app.config.get("CONVERSATION_ITEMS_PER_PAGE", 50))),
        )
        conversation_page = (
            Post.query.filter_by(conversation_id=post.conversation_id)
            .order_by(Post.created_at.asc(), Post.id.asc())
            .paginate(page=conversation_page_num, per_page=conversation_per_page, error_out=False)
        )
    return render_template(
        "search/detail.html",
        post=post,
        conversation_page=conversation_page,
        conversation_posts=conversation_page.items if conversation_page else [],
        media_src=_media_src,
        media_is_video=_media_is_video,
    )


@search_bp.route("/media/<path:media_path>")
@login_required
def media_file(media_path: str):
    upload_root = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    target = (upload_root / media_path).resolve()
    if not _is_path_within_root(upload_root, target):
        abort(404)
    if not target.is_file():
        abort(404)
    return send_file(target)


@search_bp.route("/export/csv")
@login_required
def export_csv() -> Response:
    filters = _extract_filters(request.args)
    posts = iterate_all_posts(filters)
    return export_csv_stream_response(posts)


@search_bp.route("/export/json")
@login_required
def export_json() -> Response:
    filters = _extract_filters(request.args)
    posts = iterate_all_posts(filters)
    return export_json_stream_response(posts)


@search_bp.route("/export/html")
@login_required
def export_html() -> Response:
    filters = _extract_filters(request.args)
    posts = search_all_posts(filters)
    return export_html_response(posts, filters)


def _extract_filters(args) -> dict:
    return {
        "q": (args.get("q") or "").strip(),
        "archive_id": _as_int(args.get("archive_id")),
        "date_from": (args.get("date_from") or "").strip(),
        "date_to": (args.get("date_to") or "").strip(),
        "author": (args.get("author") or "").strip().lower(),
        "language": (args.get("language") or "").strip().lower(),
        "reply_only": _as_bool(args.get("reply_only")),
        "media_only": _as_bool(args.get("media_only")),
        "links_only": _as_bool(args.get("links_only")),
        "sort": "oldest" if args.get("sort") == "oldest" else "newest",
    }


def _as_int(value, default=None, minimum=None):
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
        if minimum is not None and parsed < minimum:
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def _as_bool(value) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def _make_query(filters: dict, **overrides) -> str:
    params = filters.copy()
    params.update(overrides)
    cleaned = {}
    for key, value in params.items():
        if value in (None, "", False):
            continue
        if value is True:
            cleaned[key] = "1"
        else:
            cleaned[key] = str(value)
    return urlencode(cleaned)


def _media_src(media) -> str | None:
    if getattr(media, "local_path", None):
        return url_for("search.media_file", media_path=media.local_path)
    if getattr(media, "preview_path", None):
        return media.preview_path
    if getattr(media, "media_url", None):
        return media.media_url
    return None


def _media_is_video(media, src: str | None = None) -> bool:
    media_type = (getattr(media, "media_type", "") or "").lower()
    if "video" in media_type or "animated_gif" in media_type:
        return True
    if not src:
        src = _media_src(media)
    if not src:
        return False
    lowered = src.lower()
    return lowered.endswith(".mp4") or lowered.endswith(".mov") or lowered.endswith(".webm") or lowered.endswith(".m4v")


def _is_path_within_root(root: Path, target: Path) -> bool:
    root_str = os.path.normcase(str(root.resolve()))
    target_str = os.path.normcase(str(target.resolve()))
    try:
        common = os.path.commonpath([root_str, target_str])
    except ValueError:
        return False
    return common == root_str


def _load_archive_filter_options(selected_archive_id: int | None) -> list[Archive]:
    max_options = max(20, min(1000, int(current_app.config.get("ARCHIVE_FILTER_MAX_OPTIONS", 300))))
    archives = Archive.query.order_by(Archive.id.desc()).limit(max_options).all()
    if selected_archive_id and all(item.id != selected_archive_id for item in archives):
        selected_archive = db.session.get(Archive, selected_archive_id)
        if selected_archive:
            archives.append(selected_archive)
    archives.sort(key=lambda item: item.id, reverse=True)
    return archives
