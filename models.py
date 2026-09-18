"""データベースモデル定義。"""
from datetime import date, datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def utcnow() -> datetime:
    # DB には naive な UTC で保存する
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ロール（権限）
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)
ROLE_LABELS = {ROLE_ADMIN: "管理者", ROLE_USER: "利用者"}


class User(db.Model):
    """ログインユーザー。admin（管理者） / user（利用者）の2ロール。"""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(120), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_USER)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username} ({self.role})>"


# ---------------------------------------------------------------------------
# 空手トーナメント
# ---------------------------------------------------------------------------
GENDERS = (("male", "男性"), ("female", "女性"))
GENDER_LABELS = dict(GENDERS)

# 帯・段位（弱い順）。インデックスを強さ計算に使う
BELTS = (
    "無級", "10級", "9級", "8級", "7級", "6級", "5級", "4級", "3級", "2級", "1級",
    "初段", "二段", "三段", "四段", "五段", "六段以上",
)
BELT_INDEX = {b: i for i, b in enumerate(BELTS)}


class Fighter(db.Model):
    """選手。管理者が登録する。"""

    __tablename__ = "fighters"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    kana = db.Column(db.String(120), nullable=False, default="")
    gender = db.Column(db.String(10), nullable=False, default="male")
    birth_date = db.Column(db.Date, nullable=True)
    height_cm = db.Column(db.Float, nullable=True)
    weight_kg = db.Column(db.Float, nullable=True)
    belt = db.Column(db.String(20), nullable=False, default="無級")
    experience_years = db.Column(db.Float, nullable=False, default=0)
    wins = db.Column(db.Integer, nullable=False, default=0)
    losses = db.Column(db.Integer, nullable=False, default=0)
    draws = db.Column(db.Integer, nullable=False, default=0)
    dojo = db.Column(db.String(120), nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def age_at(self, on: date | None = None) -> int | None:
        if self.birth_date is None:
            return None
        on = on or date.today()
        years = on.year - self.birth_date.year
        if (on.month, on.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return years

    @property
    def gender_label(self) -> str:
        return GENDER_LABELS.get(self.gender, self.gender)

    @property
    def belt_rank(self) -> int:
        return BELT_INDEX.get(self.belt, 0)

    @property
    def total_bouts(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def record(self) -> str:
        return f"{self.wins}勝{self.losses}敗{self.draws}分"


class Tournament(db.Model):
    """大会。複数の階級（Division）を持つ。"""

    __tablename__ = "tournaments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    event_date = db.Column(db.Date, nullable=True)
    venue = db.Column(db.String(200), nullable=False, default="")
    notes = db.Column(db.Text, nullable=False, default="")
    is_published = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    divisions = db.relationship(
        "Division", backref="tournament", cascade="all, delete-orphan", order_by="Division.id"
    )


class Division(db.Model):
    """階級（性別・年齢・体重・帯で選手を絞る）。1階級 = 1トーナメント表。"""

    __tablename__ = "divisions"

    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey("tournaments.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    gender = db.Column(db.String(10), nullable=True)  # None = 指定なし
    min_age = db.Column(db.Integer, nullable=True)
    max_age = db.Column(db.Integer, nullable=True)
    min_weight = db.Column(db.Float, nullable=True)
    max_weight = db.Column(db.Float, nullable=True)
    min_belt = db.Column(db.Integer, nullable=True)  # BELT_INDEX
    max_belt = db.Column(db.Integer, nullable=True)
    avoid_same_dojo = db.Column(db.Boolean, nullable=False, default=True)
    weight_tolerance_kg = db.Column(db.Float, nullable=False, default=5.0)
    bracket_size = db.Column(db.Integer, nullable=True)  # 生成後に確定（2の累乗）

    entries = db.relationship(
        "Entry", backref="division", cascade="all, delete-orphan", order_by="Entry.seed"
    )
    matches = db.relationship(
        "Match", backref="division", cascade="all, delete-orphan", order_by="(Match.round, Match.index)"
    )

    def matches_fighter(self, f: "Fighter", on: date | None = None) -> bool:
        """絞り込み条件に選手が合致するか。"""
        if self.gender and f.gender != self.gender:
            return False
        age = f.age_at(on)
        if self.min_age is not None and (age is None or age < self.min_age):
            return False
        if self.max_age is not None and (age is None or age > self.max_age):
            return False
        if self.min_weight is not None and (f.weight_kg is None or f.weight_kg < self.min_weight):
            return False
        if self.max_weight is not None and (f.weight_kg is None or f.weight_kg > self.max_weight):
            return False
        if self.min_belt is not None and f.belt_rank < self.min_belt:
            return False
        if self.max_belt is not None and f.belt_rank > self.max_belt:
            return False
        return True

    @property
    def has_results(self) -> bool:
        return any(m.winner_id is not None and not m.is_bye for m in self.matches)

    @property
    def rounds(self) -> int:
        return max((m.round for m in self.matches), default=0)


class Entry(db.Model):
    """階級への出場エントリー。シードと強さスコアを持つ。"""

    __tablename__ = "entries"
    __table_args__ = (db.UniqueConstraint("division_id", "fighter_id"),)

    id = db.Column(db.Integer, primary_key=True)
    division_id = db.Column(db.Integer, db.ForeignKey("divisions.id"), nullable=False)
    fighter_id = db.Column(db.Integer, db.ForeignKey("fighters.id"), nullable=False)
    seed = db.Column(db.Integer, nullable=True)
    score = db.Column(db.Float, nullable=True)

    fighter = db.relationship("Fighter")


class Match(db.Model):
    """トーナメントの1試合。round=1 が1回戦。index は回戦内の位置（0始まり）。"""

    __tablename__ = "matches"
    __table_args__ = (db.UniqueConstraint("division_id", "round", "index"),)

    id = db.Column(db.Integer, primary_key=True)
    division_id = db.Column(db.Integer, db.ForeignKey("divisions.id"), nullable=False)
    round = db.Column(db.Integer, nullable=False)
    index = db.Column(db.Integer, nullable=False)
    fighter1_id = db.Column(db.Integer, db.ForeignKey("fighters.id"), nullable=True)
    fighter2_id = db.Column(db.Integer, db.ForeignKey("fighters.id"), nullable=True)
    winner_id = db.Column(db.Integer, db.ForeignKey("fighters.id"), nullable=True)
    is_bye = db.Column(db.Boolean, nullable=False, default=False)

    fighter1 = db.relationship("Fighter", foreign_keys=[fighter1_id])
    fighter2 = db.relationship("Fighter", foreign_keys=[fighter2_id])
    winner = db.relationship("Fighter", foreign_keys=[winner_id])

    @property
    def is_ready(self) -> bool:
        return self.fighter1_id is not None and self.fighter2_id is not None

    def slot_of(self, fighter_id: int) -> int | None:
        if self.fighter1_id == fighter_id:
            return 1
        if self.fighter2_id == fighter_id:
            return 2
        return None
