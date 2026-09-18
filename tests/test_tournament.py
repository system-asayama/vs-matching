"""大会・階級・トーナメント表の画面/ルートのテスト。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.pop("ADMIN_USERNAME", None)
os.environ.pop("ADMIN_PASSWORD", None)

import app as app_module  # noqa: E402
from models import Division, Entry, Fighter, Match, Tournament, db  # noqa: E402


@pytest.fixture
def client():
    flask_app = app_module.create_app()
    flask_app.config.update(TESTING=True)
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        with flask_app.test_client() as c:
            c.post("/setup", data={"username": "admin", "password": "password123", "confirm": "password123"})
            yield c
        db.session.remove()


def add_fighter(client, name, dojo, belt="1級", weight=65, gender="male", **kw):
    data = {"name": name, "dojo": dojo, "belt": belt, "weight_kg": weight, "gender": gender,
            "experience_years": kw.get("years", 1), "wins": kw.get("wins", 0), "losses": kw.get("losses", 0), "draws": 0}
    r = client.post("/admin/fighters/new", data=data)
    assert r.status_code == 302
    return Fighter.query.filter_by(name=name).one()


def make_division(client, name="一般男子 -70kg", **kw):
    client.post("/admin/tournaments/new", data={"name": "テスト大会", "event_date": "2026-10-01"})
    t = Tournament.query.one()
    data = {"name": name, "gender": "male", "max_weight": 70, "avoid_same_dojo": "on", "weight_tolerance_kg": 5}
    data.update(kw)
    client.post(f"/admin/tournaments/{t.id}/divisions/new", data=data)
    return Division.query.one()


def test_fighter_crud_and_score_listed(client):
    f = add_fighter(client, "山田太郎", "A道場", belt="初段", wins=5)
    r = client.get("/admin/fighters")
    assert "山田太郎" in r.get_data(as_text=True)
    client.post(f"/admin/fighters/{f.id}/edit", data={"name": "山田次郎", "belt": "二段", "gender": "male"})
    assert db.session.get(Fighter, f.id).name == "山田次郎"
    client.post(f"/admin/fighters/{f.id}/delete")
    assert db.session.get(Fighter, f.id) is None


def test_csv_import(client):
    csv = "﻿氏名,ふりがな,性別,生年月日,身長cm,体重kg,帯,経験年数,勝,敗,分,道場,備考\n" \
          "佐藤一郎,さとう,男性,2001-05-05,170,64,1級,4,3,1,0,B道場,\n" \
          "鈴木花子,すずき,女性,,160,55,初段,6,8,2,0,C道場,メモ\n"
    r = client.post("/admin/fighters/import", data={"file": (__import__("io").BytesIO(csv.encode("utf-8")), "f.csv")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "2名を取り込みました" in r.get_data(as_text=True)
    hanako = Fighter.query.filter_by(name="鈴木花子").one()
    assert hanako.gender == "female" and hanako.belt == "初段" and hanako.weight_kg == 55


def test_auto_entries_respects_division_filters(client):
    add_fighter(client, "a", "A", weight=65)
    add_fighter(client, "b", "B", weight=80)  # 体重オーバー
    add_fighter(client, "c", "C", weight=60, gender="female")  # 性別違い
    d = make_division(client)
    r = client.post(f"/admin/divisions/{d.id}/entries/auto", follow_redirects=True)
    assert "1名を追加しました" in r.get_data(as_text=True)
    assert [e.fighter.name for e in d.entries] == ["a"]


def test_generate_bracket_with_byes_and_advance(client):
    names = [("a", "A", "初段"), ("b", "B", "1級"), ("c", "C", "3級"), ("d", "D", "5級"), ("e", "E", "8級")]
    for n, dojo, belt in names:
        add_fighter(client, n, dojo, belt=belt)
    d = make_division(client)
    client.post(f"/admin/divisions/{d.id}/entries/auto")
    r = client.post(f"/admin/divisions/{d.id}/generate", follow_redirects=True)
    assert "5名・8枠" in r.get_data(as_text=True)

    d = db.session.get(Division, d.id)
    assert d.bracket_size == 8 and d.rounds == 3
    first = [m for m in d.matches if m.round == 1]
    assert len(first) == 4
    byes = [m for m in first if m.is_bye]
    assert len(byes) == 3
    # 不戦勝の選手は2回戦に進んでいる
    second = [m for m in d.matches if m.round == 2]
    advanced = {m.fighter1_id for m in second} | {m.fighter2_id for m in second}
    for m in byes:
        assert m.winner_id in advanced
    # シードは強さ順
    seeds = {e.fighter.name: e.seed for e in d.entries}
    assert seeds["a"] == 1 and seeds["b"] == 2 and seeds["e"] == 5

    # 唯一の実試合（d vs e）の勝者を入力 → 2回戦へ進む
    real = next(m for m in first if not m.is_bye)
    r = client.post(f"/admin/matches/{real.id}/winner", data={"winner_id": real.fighter1_id}, follow_redirects=True)
    assert "勝者を" in r.get_data(as_text=True)
    nxt = next(m for m in d.matches if m.round == 2 and m.index == real.index // 2)
    assert real.fighter1_id in (nxt.fighter1_id, nxt.fighter2_id)

    # 結果入力後はエントリー変更不可、作り直しも不可
    r = client.post(f"/admin/divisions/{d.id}/generate", follow_redirects=True)
    assert "リセットしてください" in r.get_data(as_text=True)
    # 結果取消 → 2回戦から外れる
    client.post(f"/admin/matches/{real.id}/clear")
    nxt = next(m for m in d.matches if m.round == 2 and m.index == real.index // 2)
    assert real.fighter1_id not in (nxt.fighter1_id, nxt.fighter2_id)


def test_same_dojo_avoidance_and_manual_swap(client):
    # スコア順: a1 > b1 > b2 > a2 → 標準配置だと a1 vs a2, b1 vs b2 になる
    add_fighter(client, "a1", "A", belt="初段")
    add_fighter(client, "b1", "B", belt="1級")
    add_fighter(client, "b2", "B", belt="2級")
    add_fighter(client, "a2", "A", belt="3級")
    d = make_division(client)
    client.post(f"/admin/divisions/{d.id}/entries/auto")
    r = client.post(f"/admin/divisions/{d.id}/generate", follow_redirects=True)
    assert "同道場回避" in r.get_data(as_text=True)
    d = db.session.get(Division, d.id)
    first = [m for m in d.matches if m.round == 1]
    assert all(m.fighter1.dojo != m.fighter2.dojo for m in first)

    # 手動入れ替えで同道場に戻す → 注意マークが出る
    a1 = Fighter.query.filter_by(name="a1").one()
    m_a1 = next(m for m in first if m.slot_of(a1.id))
    opp = m_a1.fighter2 if m_a1.fighter1_id == a1.id else m_a1.fighter1
    a2 = Fighter.query.filter_by(name="a2").one()
    r = client.post(f"/admin/divisions/{d.id}/swap", data={"fighter_a": opp.id, "fighter_b": a2.id}, follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "入れ替えました" in body and "同じ道場" in body


def test_champion_after_final(client):
    add_fighter(client, "x", "A", belt="初段")
    add_fighter(client, "y", "B", belt="1級")
    d = make_division(client)
    client.post(f"/admin/divisions/{d.id}/entries/auto")
    client.post(f"/admin/divisions/{d.id}/generate")
    d = db.session.get(Division, d.id)
    final = d.matches[0]
    assert d.rounds == 1 and final.is_ready
    client.post(f"/admin/matches/{final.id}/winner", data={"winner_id": final.fighter2_id})
    r = client.get(f"/admin/divisions/{d.id}")
    assert "優勝: y" in r.get_data(as_text=True)


def test_user_sees_only_published(client):
    add_fighter(client, "x", "A")
    add_fighter(client, "y", "B")
    d = make_division(client)
    t = d.tournament
    client.post("/admin/users/new", data={"username": "u", "password": "password123", "confirm": "password123"})
    client.post("/logout")
    client.post("/login", data={"username": "u", "password": "password123"})
    assert client.get(f"/tournaments/{t.id}").status_code == 404
    assert client.get("/admin/tournaments").status_code == 403
    assert client.get("/admin/fighters").status_code == 403
    # 管理者が公開
    client.post("/logout")
    client.post("/login", data={"username": "admin", "password": "password123"})
    client.post(f"/admin/tournaments/{t.id}/publish")
    client.post("/logout")
    client.post("/login", data={"username": "u", "password": "password123"})
    r = client.get(f"/tournaments/{t.id}")
    assert r.status_code == 200 and "テスト大会" in r.get_data(as_text=True)


def test_delete_tournament_cascades(client):
    add_fighter(client, "x", "A")
    add_fighter(client, "y", "B")
    d = make_division(client)
    client.post(f"/admin/divisions/{d.id}/entries/auto")
    client.post(f"/admin/divisions/{d.id}/generate")
    assert Match.query.count() == 1 and Entry.query.count() == 2
    client.post(f"/admin/tournaments/{d.tournament_id}/delete")
    assert Tournament.query.count() == 0 and Division.query.count() == 0
    assert Entry.query.count() == 0 and Match.query.count() == 0
    assert Fighter.query.count() == 2  # 選手は残る
