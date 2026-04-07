import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required
from werkzeug.utils import secure_filename

from app.extensions import db
from app.forms import ApiSyncForm, ArchiveCreateForm, ArchiveUploadForm
from app.models import Archive, ImportJob, ImportLog, OperationMetric
from app.services.authz import admin_required
from app.services.importer import import_archive_file
from app.services.i18n import t
from app.services.import_queue import enqueue_import_job, retry_import_job
from app.services.x_api_sync import sync_archive_from_x_api


archives_bp = Blueprint("archives", __name__, url_prefix="/archives")


@archives_bp.route("/")
@login_required
def list_archives():
    page = _as_int(request.args.get("page"), default=1, minimum=1)
    per_page = current_app.config["ITEMS_PER_PAGE"]
    page_data = Archive.query.order_by(Archive.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return render_template("archives/list.html", archives=page_data.items, page_data=page_data)


@archives_bp.route("/create", methods=["GET", "POST"])
@login_required
@admin_required
def create_archive():
    form = ArchiveCreateForm()
    if form.validate_on_submit():
        archive = Archive(
            name=form.name.data.strip(),
            description=(form.description.data or "").strip() or None,
            status="pending",
        )
        db.session.add(archive)
        db.session.commit()
        flash(t("flash.archive_created"), "success")
        return redirect(url_for("archives.import_archive", archive_id=archive.id))
    return render_template("archives/create.html", form=form)


@archives_bp.route("/<int:archive_id>")
@login_required
def archive_detail(archive_id: int):
    archive = db.get_or_404(Archive, archive_id)
    detail_per_page = max(10, min(100, int(current_app.config.get("ARCHIVE_DETAIL_ITEMS_PER_PAGE", 20))))
    logs_page_num = _as_int(request.args.get("logs_page"), default=1, minimum=1)
    ops_page_num = _as_int(request.args.get("ops_page"), default=1, minimum=1)
    jobs_page_num = _as_int(request.args.get("jobs_page"), default=1, minimum=1)

    logs_page = (
        ImportLog.query.filter_by(archive_id=archive.id)
        .order_by(ImportLog.created_at.desc(), ImportLog.id.desc())
        .paginate(page=logs_page_num, per_page=detail_per_page, error_out=False)
    )
    ops_page = (
        OperationMetric.query.filter_by(archive_id=archive.id)
        .order_by(OperationMetric.started_at.desc(), OperationMetric.id.desc())
        .paginate(page=ops_page_num, per_page=detail_per_page, error_out=False)
    )
    jobs_page = (
        ImportJob.query.filter_by(archive_id=archive.id)
        .order_by(ImportJob.created_at.desc(), ImportJob.id.desc())
        .paginate(page=jobs_page_num, per_page=detail_per_page, error_out=False)
    )

    return render_template(
        "archives/detail.html",
        archive=archive,
        logs_page=logs_page,
        ops_page=ops_page,
        jobs_page=jobs_page,
    )


@archives_bp.route("/<int:archive_id>/import", methods=["GET", "POST"])
@login_required
@admin_required
def import_archive(archive_id: int):
    archive = db.get_or_404(Archive, archive_id)
    form = ArchiveUploadForm()

    if form.validate_on_submit():
        uploaded = form.archive_file.data
        original_name = secure_filename(uploaded.filename or f"archive-{archive.id}.zip")
        suffix = Path(original_name).suffix.lower()
        if suffix.lstrip(".") not in current_app.config["ALLOWED_EXTENSIONS"]:
            flash(t("flash.invalid_extension"), "error")
            return redirect(url_for("archives.import_archive", archive_id=archive.id))

        stored_name = f"{archive.id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex}{suffix}"
        target_path = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name)
        uploaded.save(target_path)

        try:
            if current_app.config["IMPORT_ASYNC"]:
                job = enqueue_import_job(
                    archive=archive,
                    stored_path=target_path,
                    original_filename=original_name,
                    import_mode=form.import_mode.data,
                    max_retries=current_app.config["IMPORT_MAX_RETRIES"],
                )
                flash(
                    t("flash.import_queued", job_id=job.id, mode=job.import_mode),
                    "success",
                )
                return redirect(url_for("archives.archive_detail", archive_id=archive.id))

            result = import_archive_file(
                archive=archive,
                file_path=target_path,
                original_filename=original_name,
                import_mode=form.import_mode.data,
            )
            flash(
                t(
                    "flash.import_completed",
                    mode=result["import_mode"],
                    imported=result["imported_count"],
                    duplicated=result["duplicate_count"],
                    replies=result["reply_count"],
                ),
                "success",
            )
            if result["warnings"]:
                flash(t("flash.import_warnings"), "warning")
        except Exception as exc:
            current_app.logger.exception("import_archive_failed archive_id=%s", archive.id)
            flash(t("flash.import_failed"), "error")
        finally:
            if not current_app.config["IMPORT_ASYNC"] and os.path.exists(target_path):
                os.remove(target_path)
        return redirect(url_for("archives.archive_detail", archive_id=archive.id))

    return render_template("archives/import.html", archive=archive, form=form)


@archives_bp.route("/jobs/<int:job_id>/retry", methods=["POST"])
@login_required
@admin_required
def retry_job(job_id: int):
    job = db.get_or_404(ImportJob, job_id)
    ok, message = retry_import_job(job.id)
    flash(message, "success" if ok else "error")
    return redirect(url_for("archives.archive_detail", archive_id=job.archive_id))


@archives_bp.route("/<int:archive_id>/api-sync", methods=["GET", "POST"])
@login_required
@admin_required
def api_sync(archive_id: int):
    archive = db.get_or_404(Archive, archive_id)
    form = ApiSyncForm()
    if request.method == "POST" and not current_app.config["X_API_ENABLED"]:
        flash(t("flash.api_disabled"), "error")
        return redirect(url_for("archives.api_sync", archive_id=archive.id))

    if form.validate_on_submit():
        try:
            result = sync_archive_from_x_api(
                archive=archive,
                username=(form.username.data or "").strip() or None,
                user_id=(form.user_id.data or "").strip() or None,
                import_mode=form.import_mode.data,
                include_replies=bool(form.include_replies.data),
                incremental_sync=bool(form.incremental_sync.data),
                include_conversation_replies=bool(form.include_conversation_replies.data),
                max_pages=form.max_pages.data,
                max_results=form.max_results.data,
                conversation_max_pages=form.conversation_max_pages.data,
                conversation_reply_window_days=form.conversation_reply_window_days.data,
            )
            flash(
                t(
                    "flash.api_sync_completed",
                    username=result.get("source_username") or "-",
                    since_id=result.get("since_id_used") or "-",
                    fetched=result.get("fetched_raw_tweets", 0),
                    conversation_replies=result.get("fetched_conversation_replies", 0),
                    imported=result.get("imported_count", 0),
                    duplicated=result.get("duplicate_count", 0),
                    pages=result.get("pages_loaded", 0),
                    timeline_pages=result.get("timeline_pages_loaded", 0),
                    conversation_pages=result.get("conversation_pages_loaded", 0),
                ),
                "success",
            )
            return redirect(url_for("archives.archive_detail", archive_id=archive.id))
        except Exception as exc:
            current_app.logger.exception("api_sync_route_failed archive_id=%s", archive.id)
            flash(t("flash.api_sync_failed"), "error")

    return render_template(
        "archives/api_sync.html",
        archive=archive,
        form=form,
        api_enabled=current_app.config["X_API_ENABLED"],
        sync_state=archive.api_sync_state,
    )


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
