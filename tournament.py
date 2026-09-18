"""選手管理・大会・階級・トーナメント表のルーティング（Blueprint）。"""
import csv
import io
from datetime import date

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

import bracket as br
from auth import admin_required, current_user, login_required
from models import (
    BELTS,
    GENDERS,
    Division,
    Entry,
    Fighter,
    Match,
    Tournament,
    db,
)

bp = Blueprint("tournament", __name__)


# ---------------------------------------------------------------------------
# 入力ヘルパー
# ---------------------------------------------------------------------------
def _str(name: str) -> str:
    return (request.form.get(name) or "").strip()


def _float(name: str) -> float | None:
    v = _str(name)
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _int(name: str) -> int | None:
    v = _float(name)
    return int(v) if v is not None else None


def _date(name: str) -> date | None:
    v = _str(name)
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _fill_fighter(f: Fighter) -> str | None:
    """フォームの内容を選手に反映。エラーがあればメッセージを返す。"""
    f.name = _str("name")
    if not f.name:
        return "氏名を入力してください。"
    f.kana = _str("kana")
    f.gender = _str("gender") if _str("gender") in dict(GENDERS) else "male"
    f.birth_date = _date("birth_date")
    f.height_cm = _float("height_cm")
    f.weight_kg = _float("weight_kg")
    f.belt = _str("belt") if _str("belt") in BELTS else "無級"
    f.experience_years = _float("experience_years") or 0
    f.wins = _int("wins") or 0
    f.losses = _int("losses") or 0
    f.draws = _int("draws") or 0
    f.dojo = _str("dojo")
    f.notes = _str("notes")
    return None


def _fill_division(d: Division) -> str | None:
    d.name = _str("name")
    if not d.name:
        return "階級名を入力してください。"
    g = _str("gender")
    d.gender = g if g in dict(GENDERS) else None
    d.min_age = _int("min_age")
    d.max_age = _int("max_age")
    d.min_weight = _float("min_weight")
    d.max_weight = _float("max_weight")
    d.min_belt = _int("min_belt")
    d.max_belt = _int("max_belt")
    d.avoid_same_dojo = request.form.get("avoid_same_dojo") == "on"
    d.weight_tolerance_kg = _float("weight_tolerance_kg") or 5.0
    return None


def _dojos() -> list[str]:
    rows = db.session.query(Fighter.dojo).filter(Fighter.dojo != "").distinct().order_by(Fighter.dojo).all()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# 選手管理（管理者）
# ---------------------------------------------------------------------------
@bp.route("/admin/fighters")
@admin_required
def fighters():
    q = (request.args.get("q") or "").strip()
    query = Fighter.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Fighter.name.ilike(like), Fighter.kana.ilike(like), Fighter.dojo.ilike(like)))
    rows = query.order_by(Fighter.dojo.asc(), Fighter.name.asc()).all()
    scored = [(f, br.strength_score(f)) for f in rows]
    return render_template("fighters.html", fighters=scored, q=q)


@bp.route("/admin/fighters/new", methods=["GET", "POST"])
@admin_required
def fighter_new():
    if request.method == "POST":
        f = Fighter()
        error = _fill_fighter(f)
        if error:
            flash(error, "error")
            return render_template("fighter_form.html", f=f, belts=BELTS, genders=GENDERS, dojos=_dojos()), 400
        db.session.add(f)
        db.session.commit()
        flash(f"選手「{f.name}」を登録しました。", "success")
        return redirect(url_for("tournament.fighters"))
    return render_template("fighter_form.html", f=Fighter(), belts=BELTS, genders=GENDERS, dojos=_dojos())


@bp.route("/admin/fighters/<int:fighter_id>/edit", methods=["GET", "POST"])
@admin_required
def fighter_edit(fighter_id):
    f = db.session.get(Fighter, fighter_id) or abort(404)
    if request.method == "POST":
        error = _fill_fighter(f)
        if error:
            flash(error, "error")
            return render_template("fighter_form.html", f=f, belts=BELTS, genders=GENDERS, dojos=_dojos()), 400
        db.session.commit()
        flash(f"選手「{f.name}」を更新しました。", "success")
        return redirect(url_for("tournament.fighters"))
    return render_template(
        "fighter_form.html", f=f, belts=BELTS, genders=GENDERS, dojos=_dojos(), breakdown=br.score_breakdown(f)
    )


@bp.route("/admin/fighters/<int:fighter_id>/delete", methods=["POST"])
@admin_required
def fighter_delete(fighter_id):
    f = db.session.get(Fighter, fighter_id) or abort(404)
    if Entry.query.filter_by(fighter_id=f.id).count():
        flash(f"「{f.name}」は大会にエントリー中のため削除できません。先にエントリーを外してください。", "error")
        return redirect(url_for("tournament.fighters"))
    db.session.delete(f)
    db.session.commit()
    flash(f"選手「{f.name}」を削除しました。", "success")
    return redirect(url_for("tournament.fighters"))


CSV_COLUMNS = [
    "氏名", "ふりがな", "性別", "生年月日", "身長cm", "体重kg", "帯", "経験年数", "勝", "敗", "分", "道場", "備考",
]


@bp.route("/admin/fighters/csv-template")
@admin_required
def fighters_csv_template():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    w.writerow(["山田太郎", "やまだたろう", "男性", "2000-04-01", "172", "68.5", "初段", "8", "12", "3", "1", "極真会館○○支部", ""])
    data = "﻿" + buf.getvalue()  # Excel 向け BOM 付き UTF-8
    return Response(data, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=fighters_template.csv"})


@bp.route("/admin/fighters/import", methods=["POST"])
@admin_required
def fighters_import():
    file = request.files.get("file")
    if file is None or not file.filename:
        flash("CSVファイルを選択してください。", "error")
        return redirect(url_for("tournament.fighters"))
    raw = file.read()
    text = None
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        flash("CSVの文字コードを判定できませんでした（UTF-8 か Shift_JIS で保存してください）。", "error")
        return redirect(url_for("tournament.fighters"))

    reader = csv.DictReader(io.StringIO(text))
    created, errors = 0, []
    gender_map = {"男性": "male", "男": "male", "male": "male", "女性": "female", "女": "female", "female": "female"}
    for i, row in enumerate(reader, start=2):
        name = (row.get("氏名") or "").strip()
        if not name:
            continue
        try:
            f = Fighter(
                name=name,
                kana=(row.get("ふりがな") or "").strip(),
                gender=gender_map.get((row.get("性別") or "").strip(), "male"),
                birth_date=date.fromisoformat(row["生年月日"].strip()) if (row.get("生年月日") or "").strip() else None,
                height_cm=float(row["身長cm"]) if (row.get("身長cm") or "").strip() else None,
                weight_kg=float(row["体重kg"]) if (row.get("体重kg") or "").strip() else None,
                belt=(row.get("帯") or "").strip() if (row.get("帯") or "").strip() in BELTS else "無級",
                experience_years=float(row.get("経験年数") or 0),
                wins=int(row.get("勝") or 0),
                losses=int(row.get("敗") or 0),
                draws=int(row.get("分") or 0),
                dojo=(row.get("道場") or "").strip(),
                notes=(row.get("備考") or "").strip(),
            )
            db.session.add(f)
            created += 1
        except (ValueError, KeyError) as e:
            errors.append(f"{i}行目: {e}")
    db.session.commit()
    flash(f"{created}名を取り込みました。", "success")
    for e in errors[:10]:
        flash(e, "error")
    return redirect(url_for("tournament.fighters"))


# ---------------------------------------------------------------------------
# 大会（管理者）
# ---------------------------------------------------------------------------
@bp.route("/admin/tournaments")
@admin_required
def tournaments():
    rows = Tournament.query.order_by(Tournament.event_date.desc().nullslast(), Tournament.id.desc()).all()
    return render_template("tournaments.html", tournaments=rows)


@bp.route("/admin/tournaments/new", methods=["GET", "POST"])
@admin_required
def tournament_new():
    if request.method == "POST":
        t = Tournament(name=_str("name"), event_date=_date("event_date"), venue=_str("venue"), notes=_str("notes"))
        if not t.name:
            flash("大会名を入力してください。", "error")
            return render_template("tournament_form.html", t=t), 400
        db.session.add(t)
        db.session.commit()
        flash(f"大会「{t.name}」を作成しました。次に階級を追加してください。", "success")
        return redirect(url_for("tournament.tournament_detail", tournament_id=t.id))
    return render_template("tournament_form.html", t=Tournament())


@bp.route("/admin/tournaments/<int:tournament_id>")
@admin_required
def tournament_detail(tournament_id):
    t = db.session.get(Tournament, tournament_id) or abort(404)
    return render_template("tournament_detail.html", t=t)


@bp.route("/admin/tournaments/<int:tournament_id>/edit", methods=["GET", "POST"])
@admin_required
def tournament_edit(tournament_id):
    t = db.session.get(Tournament, tournament_id) or abort(404)
    if request.method == "POST":
        t.name, t.event_date, t.venue, t.notes = _str("name"), _date("event_date"), _str("venue"), _str("notes")
        if not t.name:
            flash("大会名を入力してください。", "error")
            return render_template("tournament_form.html", t=t), 400
        db.session.commit()
        flash("大会情報を更新しました。", "success")
        return redirect(url_for("tournament.tournament_detail", tournament_id=t.id))
    return render_template("tournament_form.html", t=t)


@bp.route("/admin/tournaments/<int:tournament_id>/publish", methods=["POST"])
@admin_required
def tournament_publish(tournament_id):
    t = db.session.get(Tournament, tournament_id) or abort(404)
    t.is_published = not t.is_published
    db.session.commit()
    flash("利用者に公開しました。" if t.is_published else "非公開にしました。", "success")
    return redirect(url_for("tournament.tournament_detail", tournament_id=t.id))


@bp.route("/admin/tournaments/<int:tournament_id>/delete", methods=["POST"])
@admin_required
def tournament_delete(tournament_id):
    t = db.session.get(Tournament, tournament_id) or abort(404)
    db.session.delete(t)
    db.session.commit()
    flash(f"大会「{t.name}」を削除しました。", "success")
    return redirect(url_for("tournament.tournaments"))


# ---------------------------------------------------------------------------
# 階級（管理者）
# ---------------------------------------------------------------------------
@bp.route("/admin/tournaments/<int:tournament_id>/divisions/new", methods=["GET", "POST"])
@admin_required
def division_new(tournament_id):
    t = db.session.get(Tournament, tournament_id) or abort(404)
    d = Division(tournament=t)
    if request.method == "POST":
        error = _fill_division(d)
        if error:
            flash(error, "error")
            return render_template("division_form.html", d=d, t=t, belts=BELTS, genders=GENDERS), 400
        db.session.add(d)
        db.session.commit()
        flash(f"階級「{d.name}」を追加しました。条件に合う選手を追加してください。", "success")
        return redirect(url_for("tournament.division_detail", division_id=d.id))
    return render_template("division_form.html", d=d, t=t, belts=BELTS, genders=GENDERS)


@bp.route("/admin/divisions/<int:division_id>/edit", methods=["GET", "POST"])
@admin_required
def division_edit(division_id):
    d = db.session.get(Division, division_id) or abort(404)
    if request.method == "POST":
        error = _fill_division(d)
        if error:
            flash(error, "error")
            return render_template("division_form.html", d=d, t=d.tournament, belts=BELTS, genders=GENDERS), 400
        db.session.commit()
        flash("階級の設定を更新しました。", "success")
        return redirect(url_for("tournament.division_detail", division_id=d.id))
    return render_template("division_form.html", d=d, t=d.tournament, belts=BELTS, genders=GENDERS)


@bp.route("/admin/divisions/<int:division_id>/delete", methods=["POST"])
@admin_required
def division_delete(division_id):
    d = db.session.get(Division, division_id) or abort(404)
    tid = d.tournament_id
    db.session.delete(d)
    db.session.commit()
    flash(f"階級「{d.name}」を削除しました。", "success")
    return redirect(url_for("tournament.tournament_detail", tournament_id=tid))


def _entered_fighter_ids(t: Tournament) -> set[int]:
    """この大会のいずれかの階級にエントリー済みの選手ID。"""
    return {e.fighter_id for d in t.divisions for e in d.entries}


def _division_context(d: Division) -> dict:
    """階級詳細画面に必要なデータをまとめる。"""
    on = d.tournament.event_date
    entered = _entered_fighter_ids(d.tournament)
    entry_ids = {e.fighter_id for e in d.entries}
    all_fighters = Fighter.query.order_by(Fighter.dojo, Fighter.name).all()
    candidates = [f for f in all_fighters if f.id not in entered and d.matches_fighter(f, on)]
    others = [f for f in all_fighters if f.id not in entered and not d.matches_fighter(f, on)]

    rounds: list[list[Match]] = []
    total = d.rounds
    for r in range(1, total + 1):
        rounds.append([m for m in d.matches if m.round == r])
    warnings = {}
    for m in rounds[0] if rounds else []:
        warnings[m.id] = br.pair_warnings(m.fighter1, m.fighter2, d.weight_tolerance_kg, on)
    first_round_fighters = []
    for m in rounds[0] if rounds else []:
        for f in (m.fighter1, m.fighter2):
            if f is not None:
                first_round_fighters.append(f)
    return dict(
        d=d,
        t=d.tournament,
        on=on,
        entries=d.entries,
        entry_ids=entry_ids,
        candidates=candidates,
        others=others,
        rounds=rounds,
        total_rounds=total,
        round_label=br.round_label,
        warnings=warnings,
        first_round_fighters=first_round_fighters,
        strength_score=br.strength_score,
    )


@bp.route("/admin/divisions/<int:division_id>")
@admin_required
def division_detail(division_id):
    d = db.session.get(Division, division_id) or abort(404)
    return render_template("division_detail.html", **_division_context(d))


@bp.route("/admin/divisions/<int:division_id>/entries/auto", methods=["POST"])
@admin_required
def division_auto_entries(division_id):
    """条件に合う未エントリーの選手をまとめて追加する。"""
    d = db.session.get(Division, division_id) or abort(404)
    if d.matches:
        flash("トーナメント表を生成済みです。選手を変更するには先に表をリセットしてください。", "error")
        return redirect(url_for("tournament.division_detail", division_id=d.id))
    entered = _entered_fighter_ids(d.tournament)
    added = 0
    for f in Fighter.query.all():
        if f.id not in entered and d.matches_fighter(f, d.tournament.event_date):
            db.session.add(Entry(division=d, fighter=f))
            added += 1
    db.session.commit()
    flash(f"{added}名を追加しました。", "success")
    return redirect(url_for("tournament.division_detail", division_id=d.id))


@bp.route("/admin/divisions/<int:division_id>/entries/add", methods=["POST"])
@admin_required
def division_add_entry(division_id):
    d = db.session.get(Division, division_id) or abort(404)
    if d.matches:
        flash("トーナメント表を生成済みです。選手を変更するには先に表をリセットしてください。", "error")
        return redirect(url_for("tournament.division_detail", division_id=d.id))
    f = db.session.get(Fighter, _int("fighter_id") or 0)
    if f is None:
        flash("選手を選択してください。", "error")
    elif f.id in _entered_fighter_ids(d.tournament):
        flash(f"「{f.name}」はこの大会の別の階級にエントリー済みです。", "error")
    else:
        db.session.add(Entry(division=d, fighter=f))
        db.session.commit()
        flash(f"「{f.name}」を追加しました。", "success")
    return redirect(url_for("tournament.division_detail", division_id=d.id))


@bp.route("/admin/divisions/<int:division_id>/entries/<int:entry_id>/remove", methods=["POST"])
@admin_required
def division_remove_entry(division_id, entry_id):
    d = db.session.get(Division, division_id) or abort(404)
    if d.matches:
        flash("トーナメント表を生成済みです。選手を変更するには先に表をリセットしてください。", "error")
        return redirect(url_for("tournament.division_detail", division_id=d.id))
    e = db.session.get(Entry, entry_id)
    if e is not None and e.division_id == d.id:
        db.session.delete(e)
        db.session.commit()
        flash(f"「{e.fighter.name}」を外しました。", "success")
    return redirect(url_for("tournament.division_detail", division_id=d.id))


# ---------------------------------------------------------------------------
# トーナメント表の生成・編集・結果（管理者）
# ---------------------------------------------------------------------------
def _clear_bracket(d: Division) -> None:
    for m in list(d.matches):
        db.session.delete(m)
    d.bracket_size = None


def _create_matches_from_slots(d: Division, size: int, slots: list) -> None:
    """1回戦の枠リストから全ラウンドの Match を作り、不戦勝を自動で進める。"""
    total_rounds = size.bit_length() - 1
    matches: dict[tuple[int, int], Match] = {}
    for r in range(1, total_rounds + 1):
        for i in range(size >> r):
            m = Match(division=d, round=r, index=i)
            db.session.add(m)
            matches[(r, i)] = m
    for i in range(0, size, 2):
        m = matches[(1, i // 2)]
        m.fighter1 = slots[i]
        m.fighter2 = slots[i + 1]
    d.bracket_size = size
    db.session.flush()
    # 不戦勝の自動進出
    for i in range(size // 2):
        m = matches[(1, i)]
        if (m.fighter1 is None) != (m.fighter2 is None):
            m.is_bye = True
            _set_winner(m, m.fighter1 or m.fighter2, matches)


def _match_map(d: Division) -> dict[tuple[int, int], Match]:
    return {(m.round, m.index): m for m in d.matches}


def _set_winner(m: Match, winner: Fighter, matches: dict[tuple[int, int], Match]) -> None:
    m.winner = winner
    nxt = matches.get((m.round + 1, m.index // 2))
    if nxt is not None:
        if m.index % 2 == 0:
            nxt.fighter1 = winner
        else:
            nxt.fighter2 = winner


@bp.route("/admin/divisions/<int:division_id>/generate", methods=["POST"])
@admin_required
def division_generate(division_id):
    d = db.session.get(Division, division_id) or abort(404)
    if len(d.entries) < 2:
        flash("トーナメント表を作るには2名以上のエントリーが必要です。", "error")
        return redirect(url_for("tournament.division_detail", division_id=d.id))
    if d.has_results:
        flash("試合結果が入力済みです。作り直すには先に表をリセットしてください。", "error")
        return redirect(url_for("tournament.division_detail", division_id=d.id))

    _clear_bracket(d)
    db.session.flush()
    fighters = [e.fighter for e in d.entries]
    result = br.generate(fighters, avoid_same_dojo=d.avoid_same_dojo)

    seed_of = {s.fighter.id: s.seed for s in result.slots if s.fighter is not None}
    for e in d.entries:
        e.score = result.scores[e.fighter_id]
        e.seed = seed_of[e.fighter_id]
    _create_matches_from_slots(d, result.size, [s.fighter for s in result.slots])
    db.session.commit()

    flash(f"{len(fighters)}名・{result.size}枠のトーナメント表を生成しました。", "success")
    for s in result.swaps:
        flash(f"同道場回避: {s}", "success")
    return redirect(url_for("tournament.division_detail", division_id=d.id))


@bp.route("/admin/divisions/<int:division_id>/reset", methods=["POST"])
@admin_required
def division_reset(division_id):
    d = db.session.get(Division, division_id) or abort(404)
    _clear_bracket(d)
    for e in d.entries:
        e.seed = None
        e.score = None
    db.session.commit()
    flash("トーナメント表をリセットしました。エントリーはそのまま残っています。", "success")
    return redirect(url_for("tournament.division_detail", division_id=d.id))


@bp.route("/admin/divisions/<int:division_id>/swap", methods=["POST"])
@admin_required
def division_swap(division_id):
    """1回戦の2選手の枠を手動で入れ替える。"""
    d = db.session.get(Division, division_id) or abort(404)
    if d.has_results:
        flash("試合結果が入力済みのため入れ替えできません。", "error")
        return redirect(url_for("tournament.division_detail", division_id=d.id))
    a_id, b_id = _int("fighter_a"), _int("fighter_b")
    if not a_id or not b_id or a_id == b_id:
        flash("入れ替える選手を2名選んでください。", "error")
        return redirect(url_for("tournament.division_detail", division_id=d.id))

    first = [m for m in d.matches if m.round == 1]
    ma = next((m for m in first if m.slot_of(a_id)), None)
    mb = next((m for m in first if m.slot_of(b_id)), None)
    if ma is None or mb is None:
        flash("選手が1回戦に見つかりません。", "error")
        return redirect(url_for("tournament.division_detail", division_id=d.id))

    sa, sb = ma.slot_of(a_id), mb.slot_of(b_id)
    fa, fb = db.session.get(Fighter, a_id), db.session.get(Fighter, b_id)
    setattr(ma, f"fighter{sa}", fb)
    setattr(mb, f"fighter{sb}", fa)

    # 不戦勝の再計算（1回戦の勝者と2回戦の枠を作り直す）
    matches = _match_map(d)
    for m in first:
        m.winner = None
        m.is_bye = False
    for m in matches.values():
        if m.round == 2:
            m.fighter1 = m.fighter2 = None
    for m in first:
        if (m.fighter1 is None) != (m.fighter2 is None):
            m.is_bye = True
            _set_winner(m, m.fighter1 or m.fighter2, matches)
    db.session.commit()
    flash(f"「{fa.name}」と「{fb.name}」の枠を入れ替えました。", "success")
    return redirect(url_for("tournament.division_detail", division_id=d.id))


@bp.route("/admin/matches/<int:match_id>/winner", methods=["POST"])
@admin_required
def match_winner(match_id):
    m = db.session.get(Match, match_id) or abort(404)
    d = m.division
    winner_id = _int("winner_id")
    if winner_id not in (m.fighter1_id, m.fighter2_id) or not m.is_ready:
        flash("勝者の指定が不正です。", "error")
        return redirect(url_for("tournament.division_detail", division_id=d.id))
    matches = _match_map(d)
    nxt = matches.get((m.round + 1, m.index // 2))
    if nxt is not None and nxt.winner_id is not None:
        flash("次の試合の結果が入力済みのため変更できません。先に次の試合の結果を取り消してください。", "error")
        return redirect(url_for("tournament.division_detail", division_id=d.id))
    _set_winner(m, db.session.get(Fighter, winner_id), matches)
    db.session.commit()
    flash(f"{br.round_label(m.round, d.rounds)} 第{m.index + 1}試合の勝者を「{m.winner.name}」にしました。", "success")
    return redirect(url_for("tournament.division_detail", division_id=d.id))


@bp.route("/admin/matches/<int:match_id>/clear", methods=["POST"])
@admin_required
def match_clear(match_id):
    m = db.session.get(Match, match_id) or abort(404)
    d = m.division
    if m.is_bye:
        flash("不戦勝は取り消せません。", "error")
        return redirect(url_for("tournament.division_detail", division_id=d.id))
    matches = _match_map(d)
    nxt = matches.get((m.round + 1, m.index // 2))
    if nxt is not None:
        if nxt.winner_id is not None:
            flash("次の試合の結果が入力済みのため取り消せません。", "error")
            return redirect(url_for("tournament.division_detail", division_id=d.id))
        if m.index % 2 == 0:
            nxt.fighter1 = None
        else:
            nxt.fighter2 = None
    m.winner = None
    db.session.commit()
    flash("試合結果を取り消しました。", "success")
    return redirect(url_for("tournament.division_detail", division_id=d.id))


# ---------------------------------------------------------------------------
# 利用者向け閲覧（公開済みの大会のみ）
# ---------------------------------------------------------------------------
@bp.route("/tournaments")
@login_required
def public_tournaments():
    q = Tournament.query
    if not current_user().is_admin:
        q = q.filter_by(is_published=True)
    rows = q.order_by(Tournament.event_date.desc().nullslast(), Tournament.id.desc()).all()
    return render_template("public_tournaments.html", tournaments=rows)


@bp.route("/tournaments/<int:tournament_id>")
@login_required
def public_tournament(tournament_id):
    t = db.session.get(Tournament, tournament_id) or abort(404)
    if not t.is_published and not current_user().is_admin:
        abort(404)
    divisions = []
    for d in t.divisions:
        rounds = [[m for m in d.matches if m.round == r] for r in range(1, d.rounds + 1)]
        divisions.append((d, rounds))
    return render_template("public_tournament.html", t=t, divisions=divisions, round_label=br.round_label)
