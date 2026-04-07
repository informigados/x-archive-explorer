from urllib.parse import urljoin, urlparse

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_

from app.extensions import db
from app.forms import LoginForm, UserCreateForm, UserEditForm, UserPasswordForm
from app.models import User
from app.services.authz import admin_required, is_admin_user
from app.services.auth_rate_limit import check_login_allowed, record_login_failure, record_login_success
from app.services.i18n import t


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    rate_limit_key = _build_login_rate_limit_key()
    if request.method == "POST":
        allowed, retry_after = check_login_allowed(
            rate_limit_key,
            max_attempts=current_app.config["LOGIN_RATE_LIMIT_MAX_ATTEMPTS"],
            window_seconds=current_app.config["LOGIN_RATE_LIMIT_WINDOW_SECONDS"],
            block_seconds=current_app.config["LOGIN_RATE_LIMIT_BLOCK_SECONDS"],
        )
        if not allowed:
            flash(t("flash.login_rate_limited", seconds=retry_after), "error")
            return render_template("auth/login.html", form=form)

    if form.validate_on_submit():
        identity = (form.email.data or "").strip().lower()
        user = User.query.filter(or_(User.email == identity, User.name == identity)).first()
        if user and user.check_password(form.password.data):
            record_login_success(rate_limit_key)
            login_user(user, remember=False)
            next_page = request.args.get("next")
            if next_page and _is_safe_redirect_target(next_page):
                return redirect(next_page)
            return redirect(url_for("main.dashboard"))
        record_login_failure(
            rate_limit_key,
            max_attempts=current_app.config["LOGIN_RATE_LIMIT_MAX_ATTEMPTS"],
            window_seconds=current_app.config["LOGIN_RATE_LIMIT_WINDOW_SECONDS"],
            block_seconds=current_app.config["LOGIN_RATE_LIMIT_BLOCK_SECONDS"],
        )
        flash(t("flash.invalid_credentials"), "error")
    elif request.method == "POST":
        record_login_failure(
            rate_limit_key,
            max_attempts=current_app.config["LOGIN_RATE_LIMIT_MAX_ATTEMPTS"],
            window_seconds=current_app.config["LOGIN_RATE_LIMIT_WINDOW_SECONDS"],
            block_seconds=current_app.config["LOGIN_RATE_LIMIT_BLOCK_SECONDS"],
        )

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash(t("flash.session_closed"), "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/users")
@login_required
@admin_required
def users_list():
    page = _as_int(request.args.get("page"), default=1, minimum=1)
    per_page = max(5, min(100, int(current_app.config.get("USER_ITEMS_PER_PAGE", current_app.config["ITEMS_PER_PAGE"]))))
    page_data = User.query.order_by(User.id.asc()).paginate(page=page, per_page=per_page, error_out=False)
    default_user = _get_default_user()
    return render_template(
        "auth/users_list.html",
        users=page_data.items,
        page_data=page_data,
        default_user=default_user,
    )


@auth_bp.route("/users/new", methods=["GET", "POST"])
@login_required
@admin_required
def users_create():
    form = UserCreateForm()
    _set_user_role_choices(form)
    if form.validate_on_submit():
        username = _normalize_username(form.username.data)
        email = (form.email.data or "").strip().lower()
        role = _normalize_role(form.role.data)
        password = form.password.data or ""

        if not username:
            flash(t("flash.user_invalid_username"), "error")
            return render_template("auth/users_create.html", form=form)

        if User.query.filter_by(name=username).first():
            flash(t("flash.user_username_taken"), "error")
            return render_template("auth/users_create.html", form=form)

        if User.query.filter_by(email=email).first():
            flash(t("flash.user_email_taken"), "error")
            return render_template("auth/users_create.html", form=form)

        user = User(name=username, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(t("flash.user_created", username=username), "success")
        return redirect(url_for("auth.users_list"))

    return render_template("auth/users_create.html", form=form)


@auth_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def users_edit(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)

    if _is_default_user(user):
        flash(t("flash.user_default_edit_blocked"), "warning")
        return redirect(url_for("auth.users_password", user_id=user.id))

    form = UserEditForm(obj=user)
    _set_user_role_choices(form)
    if form.validate_on_submit():
        username = _normalize_username(form.username.data)
        email = (form.email.data or "").strip().lower()
        role = _normalize_role(form.role.data)
        if not username:
            flash(t("flash.user_invalid_username"), "error")
            return render_template("auth/users_edit.html", form=form, user=user)

        same_username = User.query.filter(User.name == username, User.id != user.id).first()
        if same_username:
            flash(t("flash.user_username_taken"), "error")
            return render_template("auth/users_edit.html", form=form, user=user)

        same_email = User.query.filter(User.email == email, User.id != user.id).first()
        if same_email:
            flash(t("flash.user_email_taken"), "error")
            return render_template("auth/users_edit.html", form=form, user=user)

        if user.is_admin and role != User.ROLE_ADMIN and _is_last_admin(user):
            flash(t("flash.user_last_admin_blocked"), "error")
            return render_template("auth/users_edit.html", form=form, user=user)

        user.name = username
        user.email = email
        user.role = role
        db.session.commit()
        flash(t("flash.user_updated", username=username), "success")
        return redirect(url_for("auth.users_list"))

    if request.method == "GET":
        form.username.data = user.name
        form.email.data = user.email
        form.role.data = user.role
    return render_template("auth/users_edit.html", form=form, user=user)


@auth_bp.route("/users/<int:user_id>/password", methods=["GET", "POST"])
@login_required
def users_password(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)

    if not is_admin_user(current_user) and user.id != current_user.id:
        flash(t("flash.admin_required"), "error")
        return redirect(url_for("main.dashboard"))

    form = UserPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data or "")
        db.session.commit()
        flash(t("flash.user_password_updated", username=user.name), "success")
        return redirect(url_for("auth.users_list"))

    return render_template(
        "auth/users_password.html",
        form=form,
        user=user,
        default_user=_get_default_user(),
        can_manage_users=is_admin_user(current_user),
    )


@auth_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def users_delete(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)

    if _is_default_user(user):
        flash(t("flash.user_default_delete_blocked"), "error")
        return redirect(url_for("auth.users_list"))

    if user.id == current_user.id:
        flash(t("flash.user_self_delete_blocked"), "error")
        return redirect(url_for("auth.users_list"))

    if _is_last_admin(user):
        flash(t("flash.user_last_admin_blocked"), "error")
        return redirect(url_for("auth.users_list"))

    db.session.delete(user)
    db.session.commit()
    flash(t("flash.user_deleted", username=user.name), "success")
    return redirect(url_for("auth.users_list"))


def _get_default_user() -> User | None:
    try:
        default_user = User.query.filter_by(is_system_default=True).order_by(User.id.asc()).first()
        if default_user:
            return default_user
    except Exception:
        db.session.rollback()
    return User.query.order_by(User.id.asc()).first()


def _is_default_user(user: User) -> bool:
    if getattr(user, "is_system_default", False):
        return True
    default_user = _get_default_user()
    return bool(default_user and user.id == default_user.id)


def _normalize_username(value: str | None) -> str:
    return (value or "").strip().lower()


def _normalize_role(value: str | None) -> str:
    role = (value or "").strip().lower()
    return role if role in User.ROLE_CHOICES else User.ROLE_VIEWER


def _set_user_role_choices(form) -> None:
    form.role.choices = [
        (User.ROLE_VIEWER, t("users.role_viewer")),
        (User.ROLE_ADMIN, t("users.role_admin")),
    ]


def _is_last_admin(user: User) -> bool:
    if not user.is_admin:
        return False
    admin_count = User.query.filter_by(role=User.ROLE_ADMIN).count()
    return admin_count <= 1


def _is_safe_redirect_target(target: str) -> bool:
    host_url = request.host_url
    reference = urlparse(host_url)
    test_url = urlparse(urljoin(host_url, target))
    return test_url.scheme in {"http", "https"} and reference.netloc == test_url.netloc


def _build_login_rate_limit_key() -> str:
    trust_proxy = bool(current_app.config.get("TRUST_PROXY_HEADERS"))
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if trust_proxy and forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = (request.remote_addr or "unknown").strip()
    return f"login-ip:{client_ip.lower()}"


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
