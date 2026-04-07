from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import LoginRateLimitState


def check_login_allowed(
    key: str,
    *,
    max_attempts: int,
    window_seconds: int,
    block_seconds: int,
    now: float | None = None,
) -> tuple[bool, int]:
    del max_attempts, block_seconds  # consumed by record_login_failure; unused here by design
    timestamp = _resolve_now(now)
    try:
        state = db.session.get(LoginRateLimitState, key)
        if not state:
            return True, 0

        changed = _reset_window_if_expired(state, timestamp, window_seconds)
        blocked_until = _as_utc(state.blocked_until)
        if blocked_until and blocked_until > timestamp:
            retry_after = int((blocked_until - timestamp).total_seconds())
            return False, max(1, retry_after)

        if blocked_until and blocked_until <= timestamp:
            state.blocked_until = None
            changed = True

        if state.failure_count <= 0 and state.blocked_until is None:
            db.session.delete(state)
            db.session.commit()
            return True, 0

        if changed:
            state.updated_at = timestamp
            db.session.commit()
        return True, 0
    except Exception:
        db.session.rollback()
        return True, 0


def record_login_failure(
    key: str,
    *,
    max_attempts: int,
    window_seconds: int,
    block_seconds: int,
    now: float | None = None,
) -> None:
    timestamp = _resolve_now(now)
    max_attempts = max(1, int(max_attempts))
    window_seconds = max(1, int(window_seconds))
    block_seconds = max(1, int(block_seconds))

    try:
        state = db.session.get(LoginRateLimitState, key)
        if not state:
            state = LoginRateLimitState(
                key=key,
                failure_count=0,
                window_started_at=timestamp,
                blocked_until=None,
            )
            db.session.add(state)

        _reset_window_if_expired(state, timestamp, window_seconds)
        if not state.window_started_at:
            state.window_started_at = timestamp

        state.failure_count = int(state.failure_count or 0) + 1
        if state.failure_count >= max_attempts:
            state.blocked_until = timestamp + timedelta(seconds=block_seconds)
            state.failure_count = 0
            state.window_started_at = timestamp

        state.updated_at = timestamp
        db.session.commit()
    except Exception:
        db.session.rollback()


def record_login_success(key: str) -> None:
    try:
        state = db.session.get(LoginRateLimitState, key)
        if not state:
            return
        db.session.delete(state)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _reset_window_if_expired(state: LoginRateLimitState, now: datetime, window_seconds: int) -> bool:
    window_seconds = max(1, int(window_seconds))
    window_started_at = _as_utc(state.window_started_at)
    blocked_until = _as_utc(state.blocked_until)
    if window_started_at and (window_started_at + timedelta(seconds=window_seconds)) <= now:
        state.failure_count = 0
        state.window_started_at = now
        if blocked_until and blocked_until <= now:
            state.blocked_until = None
        return True
    return False


def _resolve_now(value: float | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(value, timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
