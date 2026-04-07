import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from app.extensions import db
from app.models import Archive, ImportJob
from app.services.importer import import_archive_file
from app.services.ops_metrics import record_operation_metric


VALID_JOB_STATUSES = {"queued", "running", "completed", "failed"}


def requeue_stalled_jobs(stalled_minutes: int) -> int:
    threshold_minutes = max(1, int(stalled_minutes))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
    stalled_jobs = (
        ImportJob.query.filter(
            ImportJob.status == "running",
            ImportJob.started_at.isnot(None),
            ImportJob.started_at < cutoff,
        )
        .order_by(ImportJob.id.asc())
        .all()
    )
    if not stalled_jobs:
        return 0

    archive_ids = set()
    for job in stalled_jobs:
        job.status = "queued"
        job.progress_percent = 0
        job.finished_at = None
        job.last_error = (
            f"Job recuperado automaticamente após {threshold_minutes} minuto(s) em execução."
        )
        archive_ids.add(job.archive_id)

    archives = Archive.query.filter(Archive.id.in_(archive_ids)).all() if archive_ids else []
    for archive in archives:
        if archive.status in {"running", "processing"}:
            archive.status = "queued"
            archive.notes = "Job em execução foi reencaminhado automaticamente para a fila."

    db.session.commit()
    return len(stalled_jobs)


def enqueue_import_job(
    archive: Archive,
    stored_path: str,
    original_filename: str,
    import_mode: str,
    max_retries: int,
) -> ImportJob:
    archive.status = "queued"
    archive.notes = None
    job = ImportJob(
        archive_id=archive.id,
        status="queued",
        import_mode=import_mode,
        original_filename=original_filename,
        stored_path=stored_path,
        max_retries=max(0, max_retries),
    )
    db.session.add(job)
    db.session.commit()
    from flask import current_app

    current_app.logger.info(
        "import_job_enqueued job_id=%s archive_id=%s mode=%s retries=%s",
        job.id,
        archive.id,
        import_mode,
        job.max_retries,
    )
    return job


def retry_import_job(job_id: int) -> tuple[bool, str]:
    job = db.session.get(ImportJob, job_id)
    if not job:
        return False, "Job não encontrado."
    if job.status != "failed":
        return False, "Somente jobs com falha podem ser reenfileirados."
    if not job.stored_path or not Path(job.stored_path).exists():
        return False, "Arquivo original não está mais disponível para retry."

    job.status = "queued"
    job.progress_percent = 0
    job.last_error = None
    job.started_at = None
    job.finished_at = None
    archive = db.session.get(Archive, job.archive_id)
    if archive:
        archive.status = "queued"
        archive.notes = None
    db.session.commit()
    return True, "Job reenfileirado com sucesso."


def process_next_queued_job() -> ImportJob | None:
    now = datetime.now(timezone.utc)
    next_job_id = db.session.execute(
        text(
            """
            SELECT id
            FROM import_jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        )
    ).scalar()
    if not next_job_id:
        return None

    updated = db.session.execute(
        text(
            """
            UPDATE import_jobs
            SET status = 'running',
                started_at = :now,
                progress_percent = 5,
                attempt_count = attempt_count + 1
            WHERE id = :job_id AND status = 'queued'
            """
        ),
        {"job_id": next_job_id, "now": now},
    ).rowcount
    db.session.commit()
    if updated != 1:
        return None

    process_job(next_job_id)
    return db.session.get(ImportJob, next_job_id)


def process_job(job_id: int) -> None:
    from flask import current_app

    job = db.session.get(ImportJob, job_id)
    if not job or job.status != "running":
        return
    started_at = job.started_at or datetime.now(timezone.utc)

    archive = db.session.get(Archive, job.archive_id)
    if not archive:
        job.status = "failed"
        job.last_error = "Arquivo vinculado ao job não existe."
        job.finished_at = datetime.now(timezone.utc)
        db.session.commit()
        record_operation_metric(
            operation_type="import_job",
            status="failed",
            source="worker",
            archive_id=job.archive_id,
            started_at=started_at,
            finished_at=job.finished_at,
            attempt_count=job.attempt_count,
            error_message=job.last_error,
        )
        return

    if not job.stored_path or not os.path.exists(job.stored_path):
        archive.status = "failed"
        archive.notes = "Arquivo de importação não encontrado no disco."
        job.status = "failed"
        job.last_error = "Arquivo de importação não encontrado no disco."
        job.progress_percent = 100
        job.finished_at = datetime.now(timezone.utc)
        db.session.commit()
        record_operation_metric(
            operation_type="import_job",
            status="failed",
            source="worker",
            archive_id=job.archive_id,
            started_at=started_at,
            finished_at=job.finished_at,
            attempt_count=job.attempt_count,
            error_message=job.last_error,
        )
        return

    archive.status = "processing"
    archive.notes = None
    db.session.commit()

    try:
        result = import_archive_file(
            archive=archive,
            file_path=job.stored_path,
            original_filename=job.original_filename,
            import_mode=job.import_mode,
        )
        job.status = "completed"
        job.progress_percent = 100
        job.total_items = result.get("total_items", 0)
        job.processed_items = result.get("processed_items", result.get("total_items", 0))
        job.imported_count = result.get("imported_count", 0)
        job.duplicate_count = result.get("duplicate_count", 0)
        job.reply_count = result.get("reply_count", 0)
        job.last_error = None
        job.finished_at = datetime.now(timezone.utc)
        db.session.commit()
        record_operation_metric(
            operation_type="import_job",
            status="success",
            source="worker",
            archive_id=job.archive_id,
            started_at=started_at,
            finished_at=job.finished_at,
            attempt_count=job.attempt_count,
            total_items=job.total_items,
            processed_items=job.processed_items,
            imported_count=job.imported_count,
            duplicate_count=job.duplicate_count,
            reply_count=job.reply_count,
        )
        current_app.logger.info(
            (
                "import_job_completed job_id=%s archive_id=%s mode=%s imported=%s duplicated=%s "
                "replies=%s total=%s processed=%s attempts=%s"
            ),
            job.id,
            job.archive_id,
            job.import_mode,
            job.imported_count,
            job.duplicate_count,
            job.reply_count,
            job.total_items,
            job.processed_items,
            job.attempt_count,
        )

        if os.path.exists(job.stored_path):
            os.remove(job.stored_path)
    except Exception as exc:
        db.session.rollback()
        job = db.session.get(ImportJob, job_id)
        archive = db.session.get(Archive, job.archive_id) if job else None
        if not job:
            return

        job.last_error = str(exc)
        job.progress_percent = 100
        job.finished_at = datetime.now(timezone.utc)
        if job.attempt_count <= job.max_retries:
            job.status = "queued"
            job.progress_percent = 0
            job.finished_at = None
            if archive:
                archive.status = "queued"
        else:
            job.status = "failed"
            if archive:
                archive.status = "failed"
                archive.notes = str(exc)
        db.session.commit()
        record_operation_metric(
            operation_type="import_job",
            status="failed",
            source="worker",
            archive_id=job.archive_id,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            attempt_count=job.attempt_count,
            total_items=job.total_items,
            processed_items=job.processed_items,
            imported_count=job.imported_count,
            duplicate_count=job.duplicate_count,
            reply_count=job.reply_count,
            error_message=job.last_error,
        )
        current_app.logger.exception(
            "import_job_failed job_id=%s archive_id=%s attempt=%s/%s",
            job.id,
            job.archive_id,
            job.attempt_count,
            job.max_retries + 1,
        )
