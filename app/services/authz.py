from __future__ import annotations

from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user

from app.services.i18n import t


def is_admin_user(user) -> bool:
    if not user:
        return False
    return bool(getattr(user, "is_admin", False))


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not is_admin_user(current_user):
            flash(t("flash.admin_required"), "error")
            return redirect(url_for("main.dashboard"))
        return view_func(*args, **kwargs)

    return wrapped

