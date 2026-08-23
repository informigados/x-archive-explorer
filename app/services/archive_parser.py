import json
import re
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zipfile import BadZipFile, ZipFile

from dateutil import parser as dt_parser
from flask import current_app, has_app_context


SUPPORTED_EXTENSIONS = {".zip", ".json", ".js"}
JSON_LIKE_EXTENSIONS = {".json", ".js"}
TWEET_FILE_HINTS = ("tweet", "tweets")
DEFAULT_ARCHIVE_MEMBER_MAX_FILE_BYTES = 25 * 1024 * 1024
DEFAULT_ARCHIVE_MAX_TOTAL_BYTES = 200 * 1024 * 1024


def parse_archive_file(file_path: str) -> dict[str, Any]:
    path = Path(file_path)
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError("Formato não suportado. Use ZIP, JSON ou JS.")

    docs = _load_documents(path)
    default_author = _extract_default_author(docs)

    posts: list[dict[str, Any]] = []
    warnings: list[str] = []
    for name, doc in docs:
        for tweet in _extract_tweets(doc, name):
            normalized = _normalize_tweet(tweet, default_author)
            if normalized:
                posts.append(normalized)

    if not posts:
        warnings.append("Nenhum post reconhecido no arquivo enviado.")

    return {
        "posts": posts,
        "files_processed": len(docs),
        "warnings": warnings,
        "default_author": default_author,
    }


def _load_documents(path: Path) -> list[tuple[str, Any]]:
    if path.suffix.lower() in JSON_LIKE_EXTENSIONS:
        content = path.read_text(encoding="utf-8", errors="ignore")
        parsed = _parse_json_like(content)
        return [(path.name, parsed)] if parsed is not None else []

    documents: list[tuple[str, Any]] = []
    member_max_bytes, total_max_bytes = _get_archive_member_limits()
    total_bytes_read = 0
    try:
        with ZipFile(path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue

                member = info.filename.replace("\\", "/")
                suffix = Path(member).suffix.lower()
                if suffix not in JSON_LIKE_EXTENSIONS:
                    continue

                lowered = member.lower()
                if not any(hint in lowered for hint in ("tweet", "account", "profile")):
                    continue

                if info.file_size > member_max_bytes:
                    continue
                if total_bytes_read + info.file_size > total_max_bytes:
                    break

                content = zf.read(info).decode("utf-8", errors="ignore")
                total_bytes_read += info.file_size
                parsed = _parse_json_like(content)
                if parsed is not None:
                    documents.append((member, parsed))
    except BadZipFile as exc:
        raise ValueError("Arquivo ZIP inválido ou corrompido.") from exc

    return documents


def _parse_json_like(content: str) -> Any | None:
    payload = content.strip().lstrip("\ufeff")
    if not payload:
        return None

    # X archive files can be in window.YTD.* = [...] format.
    if payload.startswith("window.YTD."):
        eq_pos = payload.find("=")
        if eq_pos == -1:
            return None
        payload = payload[eq_pos + 1 :].strip()
        if payload.endswith(";"):
            payload = payload[:-1]

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _extract_default_author(documents: list[tuple[str, Any]]) -> str | None:
    for name, doc in documents:
        if "account" not in name.lower():
            continue

        for item in _as_list(doc):
            account = item.get("account") if isinstance(item, dict) else None
            if not isinstance(account, dict):
                continue
            username = account.get("username")
            if username:
                return _normalize_handle(username)
    return None


def _extract_tweets(doc: Any, name: str) -> list[dict[str, Any]]:
    items = _as_list(doc)
    tweets: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("tweet"), dict):
            tweets.append(item["tweet"])
            continue
        if "id" in item or "id_str" in item:
            tweets.append(item)

    if not tweets and isinstance(doc, dict):
        for key in ("tweets", "items", "results", "data"):
            nested = doc.get(key)
            if not isinstance(nested, list):
                continue
            for item in nested:
                if isinstance(item, dict):
                    candidate = item.get("tweet", item)
                    if isinstance(candidate, dict):
                        tweets.append(candidate)
            if tweets:
                break

    if not tweets:
        lowered = name.lower()
        if any(hint in lowered for hint in TWEET_FILE_HINTS) and isinstance(doc, dict):
            if isinstance(doc.get("tweet"), dict):
                tweets.append(doc["tweet"])
            elif "id" in doc or "id_str" in doc:
                tweets.append(doc)
    return tweets


def _normalize_tweet(tweet: dict[str, Any], default_author: str | None) -> dict[str, Any] | None:
    external_id = str(tweet.get("id_str") or tweet.get("id") or "").strip()
    if not external_id:
        return None

    text_raw = tweet.get("full_text") or tweet.get("text") or ""
    text_normalized = re.sub(r"\s+", " ", unescape(text_raw)).strip()

    entities = _as_dict(tweet.get("entities"))
    user = _as_dict(tweet.get("user"))
    author_doc = _as_dict(tweet.get("author"))

    hashtags = [_normalize_tag(h.get("text")) for h in _as_list(entities.get("hashtags"))]
    hashtags = sorted({tag for tag in hashtags if tag})

    mentions = [_normalize_handle(m.get("screen_name")) for m in _as_list(entities.get("user_mentions"))]
    mentions = sorted({handle for handle in mentions if handle})

    urls: list[dict[str, str | None]] = []
    for entry in _as_list(entities.get("urls")):
        if not isinstance(entry, dict):
            continue
        original_url = entry.get("url")
        expanded_url = entry.get("expanded_url") or original_url
        if not expanded_url:
            continue
        domain = urlparse(expanded_url).netloc.lower() or None
        urls.append(
            {
                "url": original_url or expanded_url,
                "expanded_url": expanded_url,
                "domain": domain,
            }
        )

    direct_url = tweet.get("url") or tweet.get("tweet_url")
    if isinstance(direct_url, str) and direct_url and all(item["expanded_url"] != direct_url for item in urls):
        urls.append(
            {
                "url": direct_url,
                "expanded_url": direct_url,
                "domain": urlparse(direct_url).netloc.lower() or None,
            }
        )

    extended_entities = _as_dict(tweet.get("extended_entities"))
    media_entities = _as_list(extended_entities.get("media")) or _as_list(entities.get("media"))
    media_entities = [*media_entities, *_as_list(tweet.get("media"))]
    media: list[dict[str, str | None]] = []
    for entry in media_entities:
        if not isinstance(entry, dict):
            continue
        media_url = entry.get("media_url_https") or entry.get("media_url") or entry.get("url")
        media.append(
            {
                "media_type": entry.get("type"),
                "media_url": media_url,
                "local_path": None,
                "preview_path": media_url,
            }
        )

    source_html = tweet.get("source")
    source_app = re.sub("<[^<]+?>", "", source_html).strip() if isinstance(source_html, str) else None

    created_at = _parse_datetime(tweet.get("created_at"))
    reply_to_post = str(tweet.get("in_reply_to_status_id_str") or tweet.get("in_reply_to_status_id") or "").strip()
    reply_to_post = reply_to_post or None

    author = (
        _normalize_handle(tweet.get("user_handle"))
        or _normalize_handle(user.get("screen_name"))
        or _normalize_handle(author_doc.get("handle"))
        or _normalize_handle(author_doc.get("username"))
        or _normalize_handle(author_doc.get("screen_name"))
        or default_author
    )

    return {
        "external_post_id": external_id,
        "author_handle": author,
        "author_display_name": user.get("name") or author_doc.get("name") or author_doc.get("display_name"),
        "text_raw": text_raw,
        "text_normalized": text_normalized,
        "created_at": created_at,
        "is_reply": bool(reply_to_post),
        "reply_to_post_id": reply_to_post,
        "reply_to_user_handle": _normalize_handle(tweet.get("in_reply_to_screen_name")),
        "conversation_id": str(tweet.get("conversation_id_str") or tweet.get("conversation_id") or "").strip()
        or None,
        "has_media": bool(media),
        "has_links": bool(urls),
        "language": tweet.get("lang") or tweet.get("language"),
        "source_app": source_app,
        "raw_json": json.dumps(tweet, ensure_ascii=False) if _should_store_raw_json() else None,
        "hashtags": hashtags,
        "mentions": mentions,
        "urls": urls,
        "media": media,
    }


def _parse_datetime(raw: str | None):
    if not raw:
        return None

    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError):
        pass

    try:
        return dt_parser.parse(raw)
    except (ValueError, TypeError, OverflowError):
        return None


def _normalize_tag(value: str | None) -> str | None:
    if not value:
        return None
    tag = value.strip().lstrip("#").lower()
    return tag or None


def _normalize_handle(value: str | None) -> str | None:
    if not value:
        return None
    handle = value.strip().lstrip("@").lower()
    return handle or None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _get_archive_member_limits() -> tuple[int, int]:
    if not has_app_context():
        return DEFAULT_ARCHIVE_MEMBER_MAX_FILE_BYTES, DEFAULT_ARCHIVE_MAX_TOTAL_BYTES
    member_max = int(current_app.config.get("ARCHIVE_MEMBER_MAX_FILE_BYTES", DEFAULT_ARCHIVE_MEMBER_MAX_FILE_BYTES))
    total_max = int(current_app.config.get("ARCHIVE_MAX_TOTAL_BYTES", DEFAULT_ARCHIVE_MAX_TOTAL_BYTES))
    member_max = max(1, member_max)
    total_max = max(member_max, total_max)
    return member_max, total_max


def _should_store_raw_json() -> bool:
    if not has_app_context():
        return True
    return bool(current_app.config.get("STORE_RAW_JSON", True))
