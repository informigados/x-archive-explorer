import json
from datetime import datetime, timedelta, timezone
from typing import Any

from dateutil import parser as dt_parser
from flask import current_app

from app.extensions import db
from app.models import ApiSyncState, Archive
from app.services.importer import import_normalized_posts
from app.services.ops_metrics import record_operation_metric
from app.services.x_api_client import XApiClient


def sync_archive_from_x_api(
    archive: Archive,
    username: str | None = None,
    user_id: str | None = None,
    import_mode: str = "merge",
    include_replies: bool = True,
    incremental_sync: bool = True,
    include_conversation_replies: bool = False,
    max_pages: int = 3,
    max_results: int = 100,
    conversation_max_pages: int = 3,
    conversation_reply_window_days: int = 30,
) -> dict[str, Any]:
    if not current_app.config["X_API_ENABLED"]:
        raise ValueError("Integração com API X está desabilitada no ambiente.")
    sync_started = datetime.now(timezone.utc)

    client = XApiClient(
        bearer_token=current_app.config["X_API_BEARER_TOKEN"],
        base_url=current_app.config["X_API_BASE_URL"],
        timeout_seconds=current_app.config["X_API_TIMEOUT_SECONDS"],
        max_retries=current_app.config["X_API_MAX_RETRIES"],
        backoff_seconds=current_app.config["X_API_BACKOFF_SECONDS"],
        max_backoff_seconds=current_app.config["X_API_MAX_BACKOFF_SECONDS"],
        adaptive_rate_limit_enabled=current_app.config["X_API_ADAPTIVE_RATE_LIMIT_ENABLED"],
        rate_limit_max_wait_seconds=current_app.config["X_API_RATE_LIMIT_MAX_WAIT_SECONDS"],
    )

    resolved = client.resolve_user(username=username, user_id=user_id)
    resolved_user_id = str(resolved.get("id") or "").strip()
    resolved_username = str(resolved.get("username") or "").strip().lower() or None
    resolved_name = resolved.get("name")
    if not resolved_user_id:
        raise RuntimeError("Usuário não encontrado na API X.")

    sync_state = _get_or_create_sync_state(archive)
    since_id = None
    if incremental_sync and import_mode != "overwrite":
        since_id = sync_state.since_id

    try:
        pages_loaded = 0
        next_token = None
        raw_tweets: list[dict[str, Any]] = []
        media_lookup: dict[str, dict[str, Any]] = {}
        user_lookup: dict[str, dict[str, Any]] = {}
        user_tweet_max_id = since_id

        max_pages = max(1, min(int(max_pages), 50))
        for _ in range(max_pages):
            payload = client.get_user_tweets(
                user_id=resolved_user_id,
                max_results=max_results,
                pagination_token=next_token,
                since_id=since_id,
            )
            pages_loaded += 1
            data = _append_payload_data(payload, raw_tweets, media_lookup, user_lookup)
            for tweet in data:
                tweet_id = str(tweet.get("id") or "").strip()
                user_tweet_max_id = _max_tweet_id(user_tweet_max_id, tweet_id)

            next_token = ((payload.get("meta") or {}).get("next_token")) or None
            if not next_token:
                break

        conversation_reply_count = 0
        conversation_pages = 0
        if include_conversation_replies and resolved_username:
            conversation_ids = sorted(
                {
                    str(item.get("conversation_id") or "").strip()
                    for item in raw_tweets
                    if str(item.get("conversation_id") or "").strip()
                }
            )
            conversation_ids = conversation_ids[: min(len(conversation_ids), 20)]
            conversation_max_pages = max(1, min(int(conversation_max_pages), 20))
            conversation_reply_window_days = max(0, min(int(conversation_reply_window_days), 365))
            conversation_start_time = None
            if conversation_reply_window_days > 0:
                conversation_start_time = (
                    datetime.now(timezone.utc) - timedelta(days=conversation_reply_window_days)
                ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            for conversation_id in conversation_ids:
                query = f"conversation_id:{conversation_id} -from:{resolved_username}"
                conversation_next_token = None
                for _ in range(conversation_max_pages):
                    payload = client.search_recent_tweets(
                        query=query,
                        max_results=max_results,
                        pagination_token=conversation_next_token,
                        start_time=conversation_start_time,
                    )
                    conversation_pages += 1
                    data = _append_payload_data(payload, raw_tweets, media_lookup, user_lookup)
                    conversation_reply_count += len(data)
                    conversation_next_token = ((payload.get("meta") or {}).get("next_token")) or None
                    if not conversation_next_token:
                        break

        total_pages_loaded = pages_loaded + conversation_pages

        normalized_posts = []
        seen_ids = set()
        for tweet in raw_tweets:
            normalized = _normalize_api_tweet(
                tweet=tweet,
                default_author_handle=resolved_username,
                default_author_display_name=resolved_name,
                user_lookup=user_lookup,
                media_lookup=media_lookup,
            )
            if not normalized:
                continue
            if not include_replies and normalized["is_reply"]:
                continue
            external_id = normalized["external_post_id"]
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)
            normalized_posts.append(normalized)

        import_result = import_normalized_posts(
            archive=archive,
            posts_data=normalized_posts,
            import_mode=import_mode,
            original_filename=archive.original_filename,
            files_processed=total_pages_loaded,
            warnings=[],
            source_label="api_x",
            media_assets={},
        )
        import_result["source_username"] = resolved_username
        import_result["source_user_id"] = resolved_user_id
        import_result["pages_loaded"] = total_pages_loaded
        import_result["timeline_pages_loaded"] = pages_loaded
        import_result["conversation_pages_loaded"] = conversation_pages
        import_result["fetched_raw_tweets"] = len(raw_tweets)
        import_result["fetched_conversation_replies"] = conversation_reply_count
        import_result["conversation_reply_window_days"] = conversation_reply_window_days
        import_result["since_id_used"] = since_id

        sync_state.source_username = resolved_username
        sync_state.source_user_id = resolved_user_id
        sync_state.since_id = _max_tweet_id(sync_state.since_id, user_tweet_max_id)
        sync_state.last_synced_at = datetime.now(timezone.utc)
        sync_state.last_sync_status = "success"
        sync_state.last_sync_error = None
        db.session.commit()
        sync_finished = datetime.now(timezone.utc)
        elapsed = (sync_finished - sync_started).total_seconds()
        record_operation_metric(
            operation_type="api_sync",
            status="success",
            source="api_x",
            archive_id=archive.id,
            started_at=sync_started,
            finished_at=sync_finished,
            total_items=len(normalized_posts),
            processed_items=len(normalized_posts),
            imported_count=import_result.get("imported_count", 0),
            duplicate_count=import_result.get("duplicate_count", 0),
            reply_count=import_result.get("reply_count", 0),
            fetched_count=import_result.get("fetched_raw_tweets", 0),
            pages_loaded=import_result.get("pages_loaded", 0),
        )
        current_app.logger.info(
            (
                "sync_api_x_ok archive_id=%s user_id=%s username=%s imported=%s duplicated=%s "
                "fetched=%s replies_conv=%s pages=%s elapsed_s=%.3f incremental=%s since_id_used=%s"
            ),
            archive.id,
            resolved_user_id,
            resolved_username,
            import_result.get("imported_count", 0),
            import_result.get("duplicate_count", 0),
            import_result.get("fetched_raw_tweets", 0),
            import_result.get("fetched_conversation_replies", 0),
            import_result.get("pages_loaded", 0),
            elapsed,
            incremental_sync,
            since_id or "-",
        )
        return import_result
    except Exception as exc:
        db.session.rollback()
        sync_state = _get_or_create_sync_state(archive)
        sync_state.last_synced_at = datetime.now(timezone.utc)
        sync_state.last_sync_status = "failed"
        sync_state.last_sync_error = str(exc)
        db.session.commit()
        sync_finished = datetime.now(timezone.utc)
        elapsed = (sync_finished - sync_started).total_seconds()
        record_operation_metric(
            operation_type="api_sync",
            status="failed",
            source="api_x",
            archive_id=archive.id,
            started_at=sync_started,
            finished_at=sync_finished,
            error_message=str(exc),
        )
        current_app.logger.exception(
            "sync_api_x_failed archive_id=%s username=%s user_id=%s elapsed_s=%.3f",
            archive.id,
            username or "-",
            user_id or "-",
            elapsed,
        )
        raise


def _normalize_api_tweet(
    tweet: dict[str, Any],
    default_author_handle: str | None,
    default_author_display_name: str | None,
    user_lookup: dict[str, dict[str, Any]],
    media_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    external_id = str(tweet.get("id") or "").strip()
    if not external_id:
        return None

    entities = tweet.get("entities") or {}
    hashtags = sorted(
        {
            str(item.get("tag") or "").strip().lstrip("#").lower()
            for item in (entities.get("hashtags") or [])
            if isinstance(item, dict)
        }
        - {""}
    )
    mentions = sorted(
        {
            str(item.get("username") or "").strip().lstrip("@").lower()
            for item in (entities.get("mentions") or [])
            if isinstance(item, dict)
        }
        - {""}
    )

    urls: list[dict[str, str | None]] = []
    for item in entities.get("urls") or []:
        if not isinstance(item, dict):
            continue
        expanded = item.get("expanded_url") or item.get("url")
        if not expanded:
            continue
        domain = None
        try:
            domain = expanded.split("/")[2].lower()
        except Exception:
            domain = None
        urls.append(
            {
                "url": item.get("url") or expanded,
                "expanded_url": expanded,
                "domain": domain,
            }
        )

    media: list[dict[str, str | None]] = []
    attachments = tweet.get("attachments") or {}
    for media_key in attachments.get("media_keys") or []:
        media_obj = media_lookup.get(media_key) or {}
        media.append(
            {
                "media_type": media_obj.get("type"),
                "media_url": media_obj.get("url") or media_obj.get("preview_image_url"),
                "local_path": None,
                "preview_path": media_obj.get("preview_image_url") or media_obj.get("url"),
            }
        )

    referenced = tweet.get("referenced_tweets") or []
    reply_to_post_id = None
    for item in referenced:
        if isinstance(item, dict) and item.get("type") == "replied_to":
            reply_to_post_id = str(item.get("id") or "").strip() or None
            break

    author_id = str(tweet.get("author_id") or "").strip() or None
    author_obj = user_lookup.get(author_id or "")
    author_handle = (
        str((author_obj or {}).get("username") or "").strip().lower()
        or default_author_handle
    )
    author_display_name = (author_obj or {}).get("name") or default_author_display_name

    reply_to_user_handle = None
    in_reply_to_user_id = str(tweet.get("in_reply_to_user_id") or "").strip() or None
    if in_reply_to_user_id:
        reply_user_obj = user_lookup.get(in_reply_to_user_id) or {}
        reply_to_user_handle = str(reply_user_obj.get("username") or "").strip().lower() or None

    text_raw = str(tweet.get("text") or "")
    created_at = None
    raw_created = tweet.get("created_at")
    if raw_created:
        try:
            created_at = dt_parser.parse(str(raw_created))
        except Exception:
            created_at = None

    return {
        "external_post_id": external_id,
        "author_handle": author_handle,
        "author_display_name": author_display_name,
        "text_raw": text_raw,
        "text_normalized": " ".join(text_raw.split()),
        "created_at": created_at,
        "is_reply": bool(reply_to_post_id or tweet.get("in_reply_to_user_id")),
        "reply_to_post_id": reply_to_post_id,
        "reply_to_user_handle": reply_to_user_handle,
        "conversation_id": str(tweet.get("conversation_id") or "").strip() or None,
        "has_media": bool(media),
        "has_links": bool(urls),
        "language": tweet.get("lang"),
        "source_app": tweet.get("source"),
        "raw_json": json.dumps(tweet, ensure_ascii=False) if _should_store_raw_json() else None,
        "hashtags": hashtags,
        "mentions": mentions,
        "urls": urls,
        "media": media,
    }


def _append_payload_data(
    payload: dict[str, Any],
    raw_tweets: list[dict[str, Any]],
    media_lookup: dict[str, dict[str, Any]],
    user_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    data = payload.get("data") or []
    includes = payload.get("includes") or {}
    media_entries = includes.get("media") or []
    user_entries = includes.get("users") or []

    for media in media_entries:
        media_key = media.get("media_key")
        if media_key:
            media_lookup[media_key] = media

    for user in user_entries:
        user_id = str(user.get("id") or "").strip()
        if user_id:
            user_lookup[user_id] = user

    for tweet in data:
        if isinstance(tweet, dict):
            raw_tweets.append(tweet)
    return [item for item in data if isinstance(item, dict)]


def _max_tweet_id(current: str | None, candidate: str | None) -> str | None:
    if not candidate:
        return current
    if not current:
        return candidate
    try:
        return candidate if int(candidate) > int(current) else current
    except (ValueError, TypeError):
        return max(current, candidate)


def _get_or_create_sync_state(archive: Archive) -> ApiSyncState:
    state = archive.api_sync_state
    if state:
        return state
    state = ApiSyncState(archive_id=archive.id)
    db.session.add(state)
    db.session.commit()
    return state


def _should_store_raw_json() -> bool:
    return bool(current_app.config.get("STORE_RAW_JSON", True))
