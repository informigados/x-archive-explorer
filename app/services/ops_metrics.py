from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func

from app.extensions import db
from app.models import OperationMetric


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def record_operation_metric(
    *,
    operation_type: str,
    status: str,
    source: str | None = None,
    archive_id: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    attempt_count: int = 1,
    total_items: int = 0,
    processed_items: int = 0,
    imported_count: int = 0,
    duplicate_count: int = 0,
    reply_count: int = 0,
    fetched_count: int = 0,
    pages_loaded: int = 0,
    error_message: str | None = None,
) -> None:
    try:
        started = _as_utc(started_at or utcnow())
        finished = _as_utc(finished_at) if finished_at else None
        if finished is None and status in {"success", "failed"}:
            finished = utcnow()

        duration_ms = None
        if finished:
            duration_ms = int((finished - started).total_seconds() * 1000)
            if duration_ms < 0:
                duration_ms = 0

        metric = OperationMetric(
            archive_id=archive_id,
            operation_type=operation_type,
            source=source,
            status=status,
            started_at=started,
            finished_at=finished,
            duration_ms=duration_ms,
            attempt_count=max(1, int(attempt_count)),
            total_items=max(0, int(total_items)),
            processed_items=max(0, int(processed_items)),
            imported_count=max(0, int(imported_count)),
            duplicate_count=max(0, int(duplicate_count)),
            reply_count=max(0, int(reply_count)),
            fetched_count=max(0, int(fetched_count)),
            pages_loaded=max(0, int(pages_loaded)),
            error_message=(error_message or "").strip()[:4000] or None,
        )
        db.session.add(metric)
        db.session.commit()
    except Exception:
        db.session.rollback()
        from flask import current_app

        current_app.logger.exception(
            "ops_metric_record_failed type=%s status=%s archive_id=%s",
            operation_type,
            status,
            archive_id,
        )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_operation_metrics_summary(hours: int = 24) -> list[dict[str, Any]]:
    window_hours = max(1, min(int(hours), 24 * 30))
    cutoff = utcnow() - timedelta(hours=window_hours)

    rows = (
        db.session.query(
            OperationMetric.operation_type.label("operation_type"),
            func.count(OperationMetric.id).label("total"),
            func.sum(case((OperationMetric.status == "success", 1), else_=0)).label("success"),
            func.sum(case((OperationMetric.status == "failed", 1), else_=0)).label("failed"),
            func.avg(OperationMetric.duration_ms).label("avg_duration_ms"),
        )
        .filter(OperationMetric.started_at >= cutoff)
        .group_by(OperationMetric.operation_type)
        .order_by(OperationMetric.operation_type.asc())
        .all()
    )

    return [
        {
            "operation_type": row.operation_type,
            "total": int(row.total or 0),
            "success": int(row.success or 0),
            "failed": int(row.failed or 0),
            "avg_duration_ms": int(row.avg_duration_ms or 0),
        }
        for row in rows
    ]


def get_recent_operation_metrics(hours: int = 24, limit: int = 20) -> list[OperationMetric]:
    window_hours = max(1, min(int(hours), 24 * 30))
    max_rows = max(1, min(int(limit), 200))
    cutoff = utcnow() - timedelta(hours=window_hours)
    return (
        OperationMetric.query.filter(OperationMetric.started_at >= cutoff)
        .order_by(OperationMetric.started_at.desc())
        .limit(max_rows)
        .all()
    )


def prune_operation_metrics(retention_days: int) -> int:
    days = max(1, int(retention_days))
    cutoff = utcnow() - timedelta(days=days)
    deleted = (
        db.session.query(OperationMetric)
        .filter(OperationMetric.started_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.session.commit()
    return int(deleted or 0)
