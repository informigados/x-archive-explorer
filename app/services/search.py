from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from flask import current_app
from sqlalchemy import text

from app.extensions import db
from app.models import Post


@dataclass
class SearchPage:
    items: list[Post]
    total: int
    page: int
    per_page: int

    @property
    def pages(self) -> int:
        if self.total == 0:
            return 0
        return (self.total + self.per_page - 1) // self.per_page

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def prev_num(self) -> int:
        return max(1, self.page - 1)

    @property
    def next_num(self) -> int:
        return min(self.pages, self.page + 1) if self.pages else 1


def ensure_fts_table() -> None:
    if not _supports_sqlite_fts5():
        return
    db.session.execute(
        text(
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
    )
    db.session.commit()


def refresh_archive_search_index(archive_id: int) -> None:
    if not _supports_sqlite_fts5():
        return
    db.session.execute(
        text(
            """
            DELETE FROM posts_fts
            WHERE post_id IN (
                SELECT id FROM posts WHERE archive_id = :archive_id
            )
            """
        ),
        {"archive_id": archive_id},
    )

    db.session.execute(
        text(
            """
            INSERT INTO posts_fts (post_id, text_normalized, author_handle, hashtags, mentions)
            SELECT
                p.id,
                COALESCE(p.text_normalized, ''),
                COALESCE(p.author_handle, ''),
                COALESCE(GROUP_CONCAT(DISTINCT h.tag), ''),
                COALESCE(GROUP_CONCAT(DISTINCT m.handle), '')
            FROM posts p
            LEFT JOIN post_hashtags ph ON ph.post_id = p.id
            LEFT JOIN hashtags h ON h.id = ph.hashtag_id
            LEFT JOIN post_mentions pm ON pm.post_id = p.id
            LEFT JOIN mentions m ON m.id = pm.mention_id
            WHERE p.archive_id = :archive_id
            GROUP BY p.id
            """
        ),
        {"archive_id": archive_id},
    )
    db.session.commit()


def search_posts(filters: dict, page: int, per_page: int) -> SearchPage:
    query = _build_posts_query(filters)
    total = query.count()
    items = (
        query.offset((max(page, 1) - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return SearchPage(items=items, total=total, page=max(page, 1), per_page=per_page)


def search_all_posts(filters: dict, limit: int | None = None) -> list[Post]:
    query = _build_posts_query(filters)
    effective_limit = limit if limit is not None else int(current_app.config.get("EXPORT_MAX_POSTS", 5000))
    effective_limit = max(1, effective_limit)
    return query.limit(effective_limit).all()


def iterate_all_posts(filters: dict, limit: int | None = None, batch_size: int = 250) -> Iterator[Post]:
    query = _build_posts_query(filters)
    effective_limit = limit if limit is not None else int(current_app.config.get("EXPORT_MAX_POSTS", 5000))
    remaining = max(1, int(effective_limit))
    offset = 0
    chunk_size = max(50, min(1000, int(batch_size)))

    while remaining > 0:
        rows = query.offset(offset).limit(min(chunk_size, remaining)).all()
        if not rows:
            break
        for post in rows:
            yield post
        batch_count = len(rows)
        offset += batch_count
        remaining -= batch_count
        if batch_count < chunk_size:
            break


def _build_posts_query(filters: dict):
    query = Post.query
    query = _apply_filters(query, filters)

    keyword = (filters.get("q") or "").strip()
    if keyword:
        query = _apply_keyword(query, keyword)

    sort_mode = filters.get("sort", "newest")
    if sort_mode == "oldest":
        return query.order_by(Post.created_at.asc(), Post.id.asc())
    return query.order_by(Post.created_at.desc(), Post.id.desc())


def _apply_filters(query, filters: dict):
    archive_id = filters.get("archive_id")
    if archive_id:
        query = query.filter(Post.archive_id == archive_id)

    author = (filters.get("author") or "").strip().lower()
    if author:
        query = query.filter(Post.author_handle == author)

    language = (filters.get("language") or "").strip().lower()
    if language:
        query = query.filter(Post.language == language)

    date_from = _parse_date(filters.get("date_from"))
    if date_from:
        query = query.filter(Post.created_at >= date_from)

    date_to = _parse_date(filters.get("date_to"), end_of_day=True)
    if date_to:
        query = query.filter(Post.created_at <= date_to)

    if filters.get("reply_only"):
        query = query.filter(Post.is_reply.is_(True))
    if filters.get("media_only"):
        query = query.filter(Post.has_media.is_(True))
    if filters.get("links_only"):
        query = query.filter(Post.has_links.is_(True))

    return query


def _apply_keyword(query, keyword: str):
    if not _supports_sqlite_fts5():
        return query.filter(Post.text_normalized.ilike(f"%{keyword}%"))

    matched_ids: list[int] = []
    try:
        matched_ids = list(
            db.session.execute(
                text("SELECT post_id FROM posts_fts WHERE posts_fts MATCH :term"),
                {"term": keyword},
            ).scalars()
        )
    except Exception:
        matched_ids = []

    if matched_ids:
        return query.filter(Post.id.in_(matched_ids))
    return query.filter(Post.text_normalized.ilike(f"%{keyword}%"))


def _parse_date(value: str | None, end_of_day: bool = False):
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        if end_of_day:
            return dt.replace(hour=23, minute=59, second=59)
        return dt
    except ValueError:
        return None


def _supports_sqlite_fts5() -> bool:
    try:
        return db.engine.dialect.name == "sqlite"
    except Exception:
        return False
