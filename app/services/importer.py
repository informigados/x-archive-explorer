import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Any
from zipfile import BadZipFile, ZipFile

from flask import current_app
from sqlalchemy import text

from app.extensions import db
from app.models import Archive, Hashtag, ImportLog, Media, Mention, Post, Url
from app.services.archive_parser import parse_archive_file
from app.services.ops_metrics import record_operation_metric
from app.services.search import refresh_archive_search_index


VALID_IMPORT_MODES = {"merge", "overwrite"}
MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".webm", ".m4v"}


def import_archive_file(
    archive: Archive,
    file_path: str,
    original_filename: str | None = None,
    import_mode: str = "merge",
) -> dict[str, Any]:
    media_assets = _extract_zip_media_assets(file_path, archive.id)
    parsed = parse_archive_file(file_path)
    return import_normalized_posts(
        archive=archive,
        posts_data=parsed["posts"],
        import_mode=import_mode,
        original_filename=original_filename or Path(file_path).name,
        files_processed=parsed["files_processed"],
        warnings=parsed["warnings"],
        source_label="arquivo",
        media_assets=media_assets,
    )


def import_normalized_posts(
    archive: Archive,
    posts_data: list[dict[str, Any]],
    import_mode: str = "merge",
    original_filename: str | None = None,
    files_processed: int = 0,
    warnings: list[str] | None = None,
    source_label: str = "importação",
    media_assets: dict[str, str] | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    total_items = len(posts_data)
    processed_items = 0
    import_mode = (import_mode or "merge").strip().lower()
    if import_mode not in VALID_IMPORT_MODES:
        raise ValueError("Modo de importação inválido. Use merge ou overwrite.")
    if warnings is None:
        warnings = []
    if media_assets is None:
        media_assets = {}

    archive.status = "processing"
    db.session.commit()
    _add_log(
        archive.id,
        "info",
        f"Início da importação ({source_label}/{import_mode}): {started_at.isoformat()}",
    )

    try:
        existing_total_posts = Post.query.filter_by(archive_id=archive.id).count()
        existing_total_replies = Post.query.filter_by(archive_id=archive.id, is_reply=True).count()

        if import_mode == "overwrite":
            _clear_archive_posts(archive.id)
            existing_total_posts = 0
            existing_total_replies = 0
            existing_ids = set()
        else:
            existing_ids = {
                row[0]
                for row in db.session.query(Post.external_post_id)
                .filter(Post.archive_id == archive.id)
                .all()
            }

        hashtag_cache: dict[str, Hashtag] = {}
        mention_cache: dict[str, Mention] = {}
        url_cache: dict[str, Url] = {}
        _prime_entity_caches(posts_data, hashtag_cache, mention_cache, url_cache)
        _precreate_missing_entities(posts_data, hashtag_cache, mention_cache, url_cache)

        imported_count = 0
        duplicate_count = 0
        reply_count = 0

        for post_data in posts_data:
            processed_items += 1
            external_id = post_data["external_post_id"]
            if external_id in existing_ids:
                duplicate_count += 1
                continue
            existing_ids.add(external_id)

            post = Post(
                archive_id=archive.id,
                external_post_id=external_id,
                author_handle=post_data.get("author_handle"),
                author_display_name=post_data.get("author_display_name"),
                text_raw=post_data.get("text_raw"),
                text_normalized=post_data.get("text_normalized"),
                created_at=post_data.get("created_at"),
                is_reply=post_data.get("is_reply", False),
                reply_to_post_id=post_data.get("reply_to_post_id"),
                reply_to_user_handle=post_data.get("reply_to_user_handle"),
                conversation_id=post_data.get("conversation_id"),
                has_media=post_data.get("has_media", False),
                has_links=post_data.get("has_links", False),
                language=post_data.get("language"),
                source_app=post_data.get("source_app"),
                raw_json=post_data.get("raw_json"),
            )
            if post.is_reply:
                reply_count += 1

            for tag in post_data.get("hashtags", []):
                if tag not in hashtag_cache:
                    hashtag_cache[tag] = _get_or_create_hashtag(tag)
                post.hashtags.append(hashtag_cache[tag])

            for handle in post_data.get("mentions", []):
                if handle not in mention_cache:
                    mention_cache[handle] = _get_or_create_mention(handle)
                post.mentions.append(mention_cache[handle])

            for url_data in post_data.get("urls", []):
                key = url_data.get("expanded_url") or url_data.get("url")
                if not key:
                    continue
                if key not in url_cache:
                    url_cache[key] = _get_or_create_url(
                        url=url_data.get("url") or key,
                        expanded_url=url_data.get("expanded_url"),
                        domain=url_data.get("domain"),
                    )
                post.urls.append(url_cache[key])

            for media_data in post_data.get("media", []):
                local_path = _resolve_local_media_path(media_data.get("media_url"), media_assets)
                post.media.append(
                    Media(
                        media_type=media_data.get("media_type"),
                        media_url=media_data.get("media_url"),
                        local_path=local_path,
                        preview_path=media_data.get("preview_path"),
                    )
                )

            db.session.add(post)
            imported_count += 1

        if original_filename is not None:
            archive.original_filename = original_filename
        archive.imported_at = datetime.now(timezone.utc)
        archive.status = "completed"
        archive.total_posts = existing_total_posts + imported_count
        archive.total_replies = existing_total_replies + reply_count
        archive.notes = None
        db.session.commit()

        if import_mode == "overwrite":
            _cleanup_orphan_entities()
        refresh_archive_search_index(archive.id)

        _add_log(
            archive.id,
            "info",
            (
                f"Importação concluída ({source_label}/{import_mode}). "
                f"Lidos={len(posts_data)} importados={imported_count} "
                f"duplicados={duplicate_count} replies_importadas={reply_count} arquivos_lidos={files_processed}"
            ),
        )
        for warning in warnings:
            _add_log(archive.id, "warning", warning)
        finished_at = datetime.now(timezone.utc)
        duration_seconds = (finished_at - started_at).total_seconds()
        record_operation_metric(
            operation_type="import",
            status="success",
            source=source_label,
            archive_id=archive.id,
            started_at=started_at,
            finished_at=finished_at,
            total_items=total_items,
            processed_items=processed_items,
            imported_count=imported_count,
            duplicate_count=duplicate_count,
            reply_count=reply_count,
        )
        current_app.logger.info(
            (
                "import_ok archive_id=%s source=%s mode=%s total=%s imported=%s duplicated=%s "
                "replies=%s files=%s duration_s=%.3f"
            ),
            archive.id,
            source_label,
            import_mode,
            total_items,
            imported_count,
            duplicate_count,
            reply_count,
            files_processed,
            duration_seconds,
        )

        return {
            "import_mode": import_mode,
            "total_items": total_items,
            "processed_items": processed_items,
            "imported_count": imported_count,
            "duplicate_count": duplicate_count,
            "reply_count": reply_count,
            "files_processed": files_processed,
            "warnings": warnings,
            "duration_seconds": duration_seconds,
        }
    except Exception as exc:
        db.session.rollback()
        archive.status = "failed"
        archive.notes = str(exc)
        db.session.commit()
        _add_log(archive.id, "error", f"Falha na importação: {exc}")
        record_operation_metric(
            operation_type="import",
            status="failed",
            source=source_label,
            archive_id=archive.id,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            total_items=total_items,
            processed_items=processed_items,
            error_message=str(exc),
        )
        current_app.logger.exception(
            "import_failed archive_id=%s source=%s mode=%s",
            archive.id,
            source_label,
            import_mode,
        )
        raise


def _add_log(archive_id: int, level: str, message: str) -> None:
    db.session.add(ImportLog(archive_id=archive_id, level=level, message=message))
    db.session.commit()


def _clear_archive_posts(archive_id: int) -> None:
    db.session.execute(
        text(
            """
            DELETE FROM media
            WHERE post_id IN (SELECT id FROM posts WHERE archive_id = :archive_id)
            """
        ),
        {"archive_id": archive_id},
    )
    db.session.execute(
        text(
            """
            DELETE FROM post_hashtags
            WHERE post_id IN (SELECT id FROM posts WHERE archive_id = :archive_id)
            """
        ),
        {"archive_id": archive_id},
    )
    db.session.execute(
        text(
            """
            DELETE FROM post_mentions
            WHERE post_id IN (SELECT id FROM posts WHERE archive_id = :archive_id)
            """
        ),
        {"archive_id": archive_id},
    )
    db.session.execute(
        text(
            """
            DELETE FROM post_urls
            WHERE post_id IN (SELECT id FROM posts WHERE archive_id = :archive_id)
            """
        ),
        {"archive_id": archive_id},
    )
    db.session.execute(text("DELETE FROM posts WHERE archive_id = :archive_id"), {"archive_id": archive_id})
    db.session.execute(
        text(
            """
            DELETE FROM posts_fts
            WHERE post_id NOT IN (SELECT id FROM posts)
            """
        )
    )
    db.session.commit()


def _get_or_create_hashtag(tag: str) -> Hashtag:
    existing = Hashtag.query.filter_by(tag=tag).first()
    if existing:
        return existing
    entity = Hashtag(tag=tag)
    db.session.add(entity)
    db.session.flush()
    return entity


def _get_or_create_mention(handle: str) -> Mention:
    existing = Mention.query.filter_by(handle=handle).first()
    if existing:
        return existing
    entity = Mention(handle=handle)
    db.session.add(entity)
    db.session.flush()
    return entity


def _get_or_create_url(url: str, expanded_url: str | None, domain: str | None) -> Url:
    existing = Url.query.filter_by(url=url).first()
    if existing:
        return existing
    entity = Url(url=url, expanded_url=expanded_url, domain=domain)
    db.session.add(entity)
    db.session.flush()
    return entity


def _cleanup_orphan_entities() -> None:
    db.session.execute(
        text(
            """
            DELETE FROM hashtags
            WHERE id NOT IN (SELECT hashtag_id FROM post_hashtags)
            """
        )
    )
    db.session.execute(
        text(
            """
            DELETE FROM mentions
            WHERE id NOT IN (SELECT mention_id FROM post_mentions)
            """
        )
    )
    db.session.execute(
        text(
            """
            DELETE FROM urls
            WHERE id NOT IN (SELECT url_id FROM post_urls)
            """
        )
    )
    db.session.commit()


def _prime_entity_caches(
    posts_data: list[dict[str, Any]],
    hashtag_cache: dict[str, Hashtag],
    mention_cache: dict[str, Mention],
    url_cache: dict[str, Url],
) -> None:
    tags = set()
    handles = set()
    url_keys = set()
    for post_data in posts_data:
        tags.update(post_data.get("hashtags", []))
        handles.update(post_data.get("mentions", []))
        for url_data in post_data.get("urls", []):
            key = url_data.get("expanded_url") or url_data.get("url")
            if key:
                url_keys.add(key)

    if tags:
        for entity in Hashtag.query.filter(Hashtag.tag.in_(tags)).all():
            hashtag_cache[entity.tag] = entity
    if handles:
        for entity in Mention.query.filter(Mention.handle.in_(handles)).all():
            mention_cache[entity.handle] = entity
    if url_keys:
        for entity in Url.query.filter((Url.url.in_(url_keys)) | (Url.expanded_url.in_(url_keys))).all():
            if entity.url:
                url_cache[entity.url] = entity
            if entity.expanded_url:
                url_cache[entity.expanded_url] = entity


def _precreate_missing_entities(
    posts_data: list[dict[str, Any]],
    hashtag_cache: dict[str, Hashtag],
    mention_cache: dict[str, Mention],
    url_cache: dict[str, Url],
) -> None:
    missing_tags = {
        tag
        for post_data in posts_data
        for tag in post_data.get("hashtags", [])
        if tag and tag not in hashtag_cache
    }
    if missing_tags:
        new_hashtags = [Hashtag(tag=tag) for tag in sorted(missing_tags)]
        db.session.add_all(new_hashtags)
        db.session.flush()
        for entity in new_hashtags:
            hashtag_cache[entity.tag] = entity

    missing_handles = {
        handle
        for post_data in posts_data
        for handle in post_data.get("mentions", [])
        if handle and handle not in mention_cache
    }
    if missing_handles:
        new_mentions = [Mention(handle=handle) for handle in sorted(missing_handles)]
        db.session.add_all(new_mentions)
        db.session.flush()
        for entity in new_mentions:
            mention_cache[entity.handle] = entity

    missing_urls: dict[str, dict[str, str | None]] = {}
    for post_data in posts_data:
        for url_data in post_data.get("urls", []):
            key = url_data.get("expanded_url") or url_data.get("url")
            if not key or key in url_cache or key in missing_urls:
                continue
            missing_urls[key] = {
                "url": url_data.get("url") or key,
                "expanded_url": url_data.get("expanded_url"),
                "domain": url_data.get("domain"),
            }

    if missing_urls:
        new_urls = [
            Url(
                url=payload["url"] or key,
                expanded_url=payload["expanded_url"],
                domain=payload["domain"],
            )
            for key, payload in missing_urls.items()
        ]
        db.session.add_all(new_urls)
        db.session.flush()
        for entity in new_urls:
            if entity.url:
                url_cache[entity.url] = entity
            if entity.expanded_url:
                url_cache[entity.expanded_url] = entity


def _extract_zip_media_assets(file_path: str, archive_id: int) -> dict[str, str]:
    source = Path(file_path)
    if source.suffix.lower() != ".zip":
        return {}

    upload_root = Path(current_app.config["UPLOAD_FOLDER"])
    media_root = upload_root / "media" / f"archive_{archive_id}"
    media_root.mkdir(parents=True, exist_ok=True)

    max_file_bytes = max(1, int(current_app.config.get("MEDIA_MAX_FILE_BYTES", 25 * 1024 * 1024)))
    max_total_bytes = max(max_file_bytes, int(current_app.config.get("MEDIA_MAX_TOTAL_BYTES", 500 * 1024 * 1024)))
    max_files = max(1, int(current_app.config.get("MEDIA_MAX_FILES", 5000)))
    extracted_files = 0
    extracted_total_bytes = 0
    mapping: dict[str, str] = {}
    try:
        with ZipFile(source, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue

                member = info.filename.replace("\\", "/")
                member_path = Path(member)
                suffix = member_path.suffix.lower()
                if suffix not in MEDIA_EXTENSIONS:
                    continue

                lowered = member.lower()
                if "tweet_media" not in lowered and "/media/" not in lowered:
                    continue

                if ".." in member_path.parts or member_path.is_absolute():
                    current_app.logger.warning("import_media_skip_unsafe_path archive_id=%s member=%s", archive_id, member)
                    continue

                # Guardrails against oversized archives and zip bomb patterns.
                if info.file_size > max_file_bytes:
                    current_app.logger.warning(
                        "import_media_skip_too_large archive_id=%s member=%s size=%s limit=%s",
                        archive_id,
                        member,
                        info.file_size,
                        max_file_bytes,
                    )
                    continue
                if extracted_files >= max_files:
                    current_app.logger.warning(
                        "import_media_limit_files_reached archive_id=%s extracted=%s limit=%s",
                        archive_id,
                        extracted_files,
                        max_files,
                    )
                    break
                if extracted_total_bytes + info.file_size > max_total_bytes:
                    current_app.logger.warning(
                        "import_media_limit_total_reached archive_id=%s total=%s next=%s limit=%s",
                        archive_id,
                        extracted_total_bytes,
                        info.file_size,
                        max_total_bytes,
                    )
                    break

                original_name = member_path.name
                safe_name = _stable_media_name(archive_id, original_name, member)
                target = media_root / safe_name

                if not target.exists():
                    with zf.open(info, "r") as source_fp, target.open("wb") as target_fp:
                        shutil.copyfileobj(source_fp, target_fp, length=1024 * 64)
                    extracted_files += 1
                    extracted_total_bytes += info.file_size

                relative = target.relative_to(upload_root).as_posix()
                if original_name.lower() not in mapping:
                    mapping[original_name.lower()] = relative
    except BadZipFile as exc:
        raise ValueError("Arquivo ZIP inválido ou corrompido.") from exc
    return mapping


def _stable_media_name(archive_id: int, original_name: str, member: str) -> str:
    suffix = Path(original_name).suffix.lower()
    stem = Path(original_name).stem
    digest = hashlib.sha1(f"{archive_id}:{member}".encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    normalized_stem = "".join(ch for ch in stem if ch.isalnum() or ch in {"-", "_"}).strip("_") or "media"
    return f"{normalized_stem}-{digest}{suffix}"


def _resolve_local_media_path(media_url: str | None, media_assets: dict[str, str]) -> str | None:
    if not media_url:
        return None
    parsed = urlparse(media_url)
    filename = Path(parsed.path).name.lower()
    if not filename:
        return None
    return media_assets.get(filename)
