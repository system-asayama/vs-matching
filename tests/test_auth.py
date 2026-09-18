"""ログインシステムの動作テスト（SQLite インメモリ）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# create_app は環境変数を見るので import 前にテスト用に固定する
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.pop("ADMIN_USERNAME", None)
os.environ.pop("ADMIN_PASSWORD", None)

import app as app_module  # noqa: E402
from models import ROLE_ADMIN, ROLE_USER, User, db  # noqa: E402


@pytest.fixture
def client():
    flask_app = app_module.create_app()
    flask_app.config.update(TESTING=True)
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        with flask_app.test_client() as c:
            yield c
        db.session.remove()


def setup_admin(client, username="admin", password="password123"):
    return client.post(
        "/setup",
        data={"username": username, "password": password, "confirm": password},
        follow_redirects=True,
    )


def login(client, username, password):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=True)


def logout(client):
    return client.post("/logout", follow_redirects=True)


# --- ブートストラップ ------------------------------------------------------
def test_redirects_to_setup_when_no_admin(client):
    r = client.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/setup")
    r = client.get("/login")
    assert r.status_code == 302 and r.headers["Location"].endswith("/setup")


def test_setup_creates_first_admin_and_logs_in(client):
    r = setup_admin(client)
    assert "最初の管理者を登録しました" in r.get_data(as_text=True)
    assert User.query.filter_by(role=ROLE_ADMIN).count() == 1
    # ログイン済みで利用者管理へ
    assert "利用者管理" in r.get_data(as_text=True)


def test_setup_disabled_after_admin_exists(client):
    setup_admin(client)
    logout(client)
    r = client.get("/setup")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    r = client.post("/setup", data={"username": "x", "password": "password123", "confirm": "password123"})
    assert r.status_code == 302
    assert User.query.count() == 1


def test_setup_rejects_short_or_mismatched_password(client):
    r = client.post("/setup", data={"username": "a", "password": "short", "confirm": "short"})
    assert "8文字以上" in r.get_data(as_text=True)
    r = client.post("/setup", data={"username": "a", "password": "password123", "confirm": "different1"})
    assert "一致しません" in r.get_data(as_text=True)
    assert User.query.count() == 0


# --- 利用者登録は管理者のみ -------------------------------------------------
def test_no_self_registration_route(client):
    setup_admin(client)
    logout(client)
    assert client.get("/register").status_code == 404
    r = client.get("/admin/users/new")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_admin_creates_user_and_user_can_login(client):
    setup_admin(client)
    r = client.post(
        "/admin/users/new",
        data={"username": "taro", "display_name": "太郎", "password": "userpass1", "confirm": "userpass1", "role": ROLE_USER},
        follow_redirects=True,
    )
    assert "「太郎」を登録しました" in r.get_data(as_text=True)
    logout(client)

    r = login(client, "taro", "userpass1")
    body = r.get_data(as_text=True)
    assert "ようこそ、太郎 さん" in body
    assert "ダッシュボード" in body
    # 利用者は管理画面に入れない
    assert client.get("/admin/users").status_code == 403
    assert client.get("/admin/users/new").status_code == 403


def test_user_cannot_create_users(client):
    setup_admin(client)
    client.post("/admin/users/new", data={"username": "u1", "password": "userpass1", "confirm": "userpass1"})
    logout(client)
    login(client, "u1", "userpass1")
    r = client.post("/admin/users/new", data={"username": "u2", "password": "userpass1", "confirm": "userpass1"})
    assert r.status_code == 403
    assert User.query.filter_by(username="u2").first() is None


def test_duplicate_username_rejected(client):
    setup_admin(client)
    r = client.post("/admin/users/new", data={"username": "admin", "password": "userpass1", "confirm": "userpass1"})
    assert r.status_code == 400
    assert "既に使われています" in r.get_data(as_text=True)


# --- ログイン ---------------------------------------------------------------
def test_wrong_password_rejected(client):
    setup_admin(client)
    logout(client)
    r = login(client, "admin", "wrongpass1")
    assert "正しくありません" in r.get_data(as_text=True)
    assert client.get("/dashboard").status_code == 302


def test_inactive_user_cannot_login(client):
    setup_admin(client)
    client.post("/admin/users/new", data={"username": "u1", "password": "userpass1", "confirm": "userpass1"})
    uid = User.query.filter_by(username="u1").one().id
    client.post(f"/admin/users/{uid}/toggle")
    assert db.session.get(User, uid).is_active is False
    logout(client)
    r = login(client, "u1", "userpass1")
    assert "正しくありません" in r.get_data(as_text=True)


# --- 最後の管理者の保護 -----------------------------------------------------
def test_last_admin_protected(client):
    setup_admin(client)
    admin_id = User.query.filter_by(username="admin").one().id
    # 自分自身
    r = client.post(f"/admin/users/{admin_id}/delete", follow_redirects=True)
    assert "自分自身は削除できません" in r.get_data(as_text=True)
    r = client.post(f"/admin/users/{admin_id}/role", data={"role": ROLE_USER}, follow_redirects=True)
    assert "自分自身のロールは変更できません" in r.get_data(as_text=True)
    assert db.session.get(User, admin_id).is_admin

    # 2人目の管理者を作り、その人でログインして最初の管理者を降格 → 可能
    client.post("/admin/users/new", data={"username": "admin2", "password": "password123", "confirm": "password123", "role": ROLE_ADMIN})
    logout(client)
    login(client, "admin2", "password123")
    r = client.post(f"/admin/users/{admin_id}/role", data={"role": ROLE_USER}, follow_redirects=True)
    assert "利用者に変更しました" in r.get_data(as_text=True)
    # これで admin2 が最後の管理者。最初の管理者(今は利用者)からは操作できない
    logout(client)
    login(client, "admin", "password123")
    admin2_id = User.query.filter_by(username="admin2").one().id
    assert client.post(f"/admin/users/{admin2_id}/delete").status_code == 403


def test_admin_reset_password_and_user_settings(client):
    setup_admin(client)
    client.post("/admin/users/new", data={"username": "u1", "password": "userpass1", "confirm": "userpass1"})
    uid = User.query.filter_by(username="u1").one().id
    client.post(f"/admin/users/{uid}/password", data={"password": "newpass123"})
    logout(client)
    login(client, "u1", "newpass123")
    r = client.post(
        "/settings",
        data={"display_name": "花子", "current_password": "newpass123", "new_password": "another123", "confirm": "another123"},
        follow_redirects=True,
    )
    assert "設定を更新しました" in r.get_data(as_text=True)
    logout(client)
    assert "ようこそ、花子 さん" in login(client, "u1", "another123").get_data(as_text=True)
