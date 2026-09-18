"""vs-matching: 空手トーナメント自動組み合わせアプリ。

- 管理者(admin) / 利用者(user) の2ロール
- 最初の管理者はブートストラップ画面(/setup)または環境変数で登録
- 利用者は管理者だけが登録できる（自己登録は無し）
- 選手・大会・階級の管理と、強さ/体格/道場を考慮したトーナメント表の自動生成
"""
import os

from flask import Flask, flash, redirect, render_template, request, session, url_for

from auth import admin_required, current_user, login_required, login_user, safe_next
from models import ROLE_ADMIN, ROLE_LABELS, ROLE_USER, ROLES, User, db
from tournament import bp as tournament_bp

MIN_PASSWORD_LENGTH = 8


def _normalize_db_url(url: str) -> str:
    # SQLAlchemy は postgres:// を認識しないため postgresql:// に変換
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # CSV 取り込み上限

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = _normalize_db_url(database_url)
    else:
        # DATABASE_URL が無い場合はローカル SQLite にフォールバック
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    with app.app_context():
        db.create_all()
        _maybe_seed_admin()

    _register_routes(app)
    app.register_blueprint(tournament_bp)
    return app


def _maybe_seed_admin() -> None:
    """環境変数 ADMIN_USERNAME / ADMIN_PASSWORD が両方あるときだけ初期管理者を作る。

    自動デプロイ向け。指定が無ければ /setup のブートストラップ画面で登録する。
    """
    username = os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("ADMIN_PASSWORD")
    if not username or not password:
        return
    if User.query.filter_by(username=username).first() is None:
        admin = User(username=username, display_name=username, role=ROLE_ADMIN)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()


def needs_setup() -> bool:
    """管理者が1人も存在しない（ブートストラップが必要な）状態か。"""
    return User.query.filter_by(role=ROLE_ADMIN).count() == 0


def _admin_count() -> int:
    return User.query.filter_by(role=ROLE_ADMIN, is_active=True).count()


def _validate_password(password: str, confirm: str | None = None) -> str | None:
    """問題があればエラーメッセージ、無ければ None を返す。"""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"パスワードは{MIN_PASSWORD_LENGTH}文字以上にしてください。"
    if confirm is not None and password != confirm:
        return "パスワードが一致しません。"
    return None


# ---------------------------------------------------------------------------
# ルーティング
# ---------------------------------------------------------------------------
def _register_routes(app: Flask) -> None:
    @app.context_processor
    def inject_globals():
        return {"current_user": current_user(), "ROLE_LABELS": ROLE_LABELS}

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403, message="この操作を行う権限がありません。"), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, message="ページが見つかりません。"), 404

    @app.route("/")
    def index():
        if needs_setup():
            return redirect(url_for("setup"))
        user = current_user()
        if user is None:
            return redirect(url_for("login"))
        return redirect(url_for("tournament.tournaments" if user.is_admin else "tournament.public_tournaments"))

    # --- ブートストラップ ---------------------------------------------------
    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        """最初の管理者を登録する。管理者が存在すると無効になる。"""
        if not needs_setup():
            flash("初期セットアップは既に完了しています。", "error")
            return redirect(url_for("login"))

        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            display_name = (request.form.get("display_name") or "").strip() or username
            password = request.form.get("password") or ""
            confirm = request.form.get("confirm") or ""

            if not username or not password:
                error = "ログインIDとパスワードを入力してください。"
            else:
                error = _validate_password(password, confirm)

            if error:
                flash(error, "error")
            else:
                admin = User(username=username, display_name=display_name, role=ROLE_ADMIN)
                admin.set_password(password)
                db.session.add(admin)
                db.session.commit()
                login_user(admin)
                flash("最初の管理者を登録しました。", "success")
                return redirect(url_for("admin_users"))

        return render_template("setup.html")

    # --- ログイン / ログアウト ---------------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if needs_setup():
            return redirect(url_for("setup"))
        user = current_user()
        if user is not None:
            return redirect(url_for("index"))

        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""

            user = User.query.filter_by(username=username).first()
            if user is not None and user.is_active and user.check_password(password):
                login_user(user)
                flash(f"ようこそ、{user.display_name} さん。", "success")
                return redirect(safe_next(url_for("index")))

            flash("ログインIDまたはパスワードが正しくありません。", "error")

        return render_template("login.html", next=request.args.get("next", ""))

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("ログアウトしました。", "success")
        return redirect(url_for("login"))

    # --- ログイン後（共通） -------------------------------------------------
    @app.route("/dashboard")
    @login_required
    def dashboard():
        return render_template("dashboard.html", user=current_user())

    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        """自分の表示名とパスワードを変更する。"""
        user = current_user()

        if request.method == "POST":
            current_password = request.form.get("current_password") or ""
            display_name = (request.form.get("display_name") or "").strip()
            new_password = request.form.get("new_password") or ""
            confirm = request.form.get("confirm") or ""

            error = None
            if not user.check_password(current_password):
                error = "現在のパスワードが正しくありません。"
            elif not display_name:
                error = "表示名を入力してください。"
            elif new_password:
                error = _validate_password(new_password, confirm)

            if error:
                flash(error, "error")
            else:
                user.display_name = display_name
                if new_password:
                    user.set_password(new_password)
                db.session.commit()
                flash("設定を更新しました。", "success")
                return redirect(url_for("settings"))

        return render_template("settings.html", user=user)

    # --- 管理者専用：利用者管理 -------------------------------------------
    @app.route("/admin/users")
    @admin_required
    def admin_users():
        users = User.query.order_by(User.created_at.asc(), User.id.asc()).all()
        return render_template("admin_users.html", users=users, roles=ROLES)

    @app.route("/admin/users/new", methods=["GET", "POST"])
    @admin_required
    def admin_create_user():
        """管理者が利用者（または管理者）を登録する。"""
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            display_name = (request.form.get("display_name") or "").strip() or username
            password = request.form.get("password") or ""
            confirm = request.form.get("confirm") or ""
            role = request.form.get("role") or ROLE_USER
            if role not in ROLES:
                role = ROLE_USER

            if not username or not password:
                error = "ログインIDとパスワードを入力してください。"
            elif User.query.filter_by(username=username).first() is not None:
                error = "そのログインIDは既に使われています。"
            else:
                error = _validate_password(password, confirm)

            if error:
                flash(error, "error")
                return render_template("admin_user_form.html", roles=ROLES, form=request.form), 400

            user = User(username=username, display_name=display_name, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f"{ROLE_LABELS[role]}「{display_name}」を登録しました。", "success")
            return redirect(url_for("admin_users"))

        return render_template("admin_user_form.html", roles=ROLES, form={})

    @app.route("/admin/users/<int:user_id>/role", methods=["POST"])
    @admin_required
    def admin_update_role(user_id):
        user = db.session.get(User, user_id)
        if user is None:
            return not_found(None)
        new_role = request.form.get("role")
        if new_role not in ROLES:
            flash("無効なロールです。", "error")
            return redirect(url_for("admin_users"))

        if user.id == current_user().id:
            flash("自分自身のロールは変更できません。", "error")
            return redirect(url_for("admin_users"))
        if user.is_admin and user.is_active and new_role != ROLE_ADMIN and _admin_count() <= 1:
            flash("最後の管理者の権限は変更できません。", "error")
            return redirect(url_for("admin_users"))

        user.role = new_role
        db.session.commit()
        flash(f"「{user.display_name}」のロールを{ROLE_LABELS[new_role]}に変更しました。", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
    @admin_required
    def admin_toggle_user(user_id):
        """利用停止 / 再開を切り替える。"""
        user = db.session.get(User, user_id)
        if user is None:
            return not_found(None)
        if user.id == current_user().id:
            flash("自分自身は停止できません。", "error")
            return redirect(url_for("admin_users"))
        if user.is_admin and user.is_active and _admin_count() <= 1:
            flash("最後の管理者は停止できません。", "error")
            return redirect(url_for("admin_users"))

        user.is_active = not user.is_active
        db.session.commit()
        state = "再開" if user.is_active else "停止"
        flash(f"「{user.display_name}」を{state}しました。", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<int:user_id>/password", methods=["POST"])
    @admin_required
    def admin_reset_password(user_id):
        """管理者が利用者のパスワードを再設定する。"""
        user = db.session.get(User, user_id)
        if user is None:
            return not_found(None)
        password = request.form.get("password") or ""
        error = _validate_password(password)
        if error:
            flash(error, "error")
        else:
            user.set_password(password)
            db.session.commit()
            flash(f"「{user.display_name}」のパスワードを再設定しました。", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_user(user_id):
        user = db.session.get(User, user_id)
        if user is None:
            return not_found(None)
        if user.id == current_user().id:
            flash("自分自身は削除できません。", "error")
            return redirect(url_for("admin_users"))
        if user.is_admin and user.is_active and _admin_count() <= 1:
            flash("最後の管理者は削除できません。", "error")
            return redirect(url_for("admin_users"))

        db.session.delete(user)
        db.session.commit()
        flash(f"「{user.display_name}」を削除しました。", "success")
        return redirect(url_for("admin_users"))


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
