"""認証ヘルパー（セッション管理・アクセス制御デコレータ）。"""
from functools import wraps

from flask import abort, flash, redirect, request, session, url_for

from models import User, db, utcnow


def current_user() -> User | None:
    user_id = session.get("user_id")
    if user_id is None:
        return None
    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        session.clear()
        return None
    return user


def login_user(user: User) -> None:
    session.clear()
    session["user_id"] = user.id
    user.last_login_at = utcnow()
    db.session.commit()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            flash("ログインが必要です。", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            flash("ログインが必要です。", "error")
            return redirect(url_for("login", next=request.path))
        if not user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def safe_next(default: str) -> str:
    nxt = request.args.get("next") or request.form.get("next") or ""
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return default
