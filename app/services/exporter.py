import csv
import io
import json
from collections.abc import Iterable
from datetime import datetime

from flask import Response, render_template, stream_with_context

from app.models import Post


def serialize_post(post: Post) -> dict:
    return {
        "id": post.id,
        "archive_id": post.archive_id,
        "external_post_id": post.external_post_id,
        "author_handle": post.author_handle,
        "author_display_name": post.author_display_name,
        "text_raw": post.text_raw,
        "text_normalized": post.text_normalized,
        "created_at": post.created_at.isoformat() if post.created_at else None,
        "is_reply": post.is_reply,
        "reply_to_post_id": post.reply_to_post_id,
        "reply_to_user_handle": post.reply_to_user_handle,
        "conversation_id": post.conversation_id,
        "has_media": post.has_media,
        "has_links": post.has_links,
        "language": post.language,
        "source_app": post.source_app,
        "hashtags": [item.tag for item in post.hashtags],
        "mentions": [item.handle for item in post.mentions],
        "urls": [item.expanded_url or item.url for item in post.urls],
    }


def export_csv_response(posts: list[Post]) -> Response:
    return export_csv_stream_response(posts)


def export_csv_stream_response(posts: Iterable[Post]) -> Response:
    filename = f"x-archive-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "id",
                "external_post_id",
                "author_handle",
                "text",
                "created_at",
                "is_reply",
                "has_media",
                "has_links",
                "language",
                "hashtags",
                "mentions",
                "urls",
            ]
        )
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)

        for post in posts:
            writer.writerow(
                [
                    post.id,
                    post.external_post_id,
                    post.author_handle or "",
                    post.text_raw or "",
                    post.created_at.isoformat() if post.created_at else "",
                    str(post.is_reply),
                    str(post.has_media),
                    str(post.has_links),
                    post.language or "",
                    ",".join(item.tag for item in post.hashtags),
                    ",".join(item.handle for item in post.mentions),
                    ",".join((item.expanded_url or item.url or "") for item in post.urls),
                ]
            )
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    return Response(
        stream_with_context(generate()),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def export_json_response(posts: list[Post]) -> Response:
    return export_json_stream_response(posts)


def export_json_stream_response(posts: Iterable[Post]) -> Response:
    filename = f"x-archive-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"

    def generate():
        first = True
        yield "[\n"
        for post in posts:
            serialized = json.dumps(serialize_post(post), ensure_ascii=False)
            if not first:
                yield ",\n"
            yield serialized
            first = False
        yield "\n]\n"

    return Response(
        stream_with_context(generate()),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def export_html_response(posts: list[Post], filters: dict) -> Response:
    html = render_template("search/export.html", posts=posts, filters=filters)
    filename = f"x-archive-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
    return Response(
        html,
        mimetype="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
