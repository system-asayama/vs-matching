"""トーナメント組み合わせのロジック（DB非依存・純粋関数）。

流れ:
  1. 強さスコアで選手を並べてシード順を決める
  2. 標準シード配置（1位と2位が決勝で当たる配置）で枠に割り当てる
  3. 同じ道場同士の1回戦を、近いシード同士の入れ替えで回避する
  4. 1回戦の各組に体重差・帯差などの注意を付ける
"""
from dataclasses import dataclass, field
from datetime import date
from math import ceil, log2

from models import BELTS, Fighter


# ---------------------------------------------------------------------------
# 強さスコア
# ---------------------------------------------------------------------------
def strength_score(f: Fighter) -> float:
    """帯・経験年数・戦績から強さを1つの数値にする（大きいほど強い）。

    帯1段階 = 10点、経験1年 = 2点（上限20年）、1勝 = 3点、1敗 = -1点、
    勝率ボーナス最大10点。
    """
    score = f.belt_rank * 10
    score += min(f.experience_years or 0, 20) * 2
    score += f.wins * 3 - f.losses * 1
    if f.total_bouts > 0:
        score += (f.wins / f.total_bouts) * 10
    return round(score, 1)


def score_breakdown(f: Fighter) -> list[tuple[str, float]]:
    parts = [
        (f"帯 {f.belt}", f.belt_rank * 10),
        (f"経験 {f.experience_years:g}年", min(f.experience_years or 0, 20) * 2),
        (f"戦績 {f.record}", f.wins * 3 - f.losses * 1),
    ]
    if f.total_bouts > 0:
        parts.append(("勝率ボーナス", round(f.wins / f.total_bouts * 10, 1)))
    return parts


# ---------------------------------------------------------------------------
# シード配置
# ---------------------------------------------------------------------------
def bracket_size_for(n: int) -> int:
    """n 人に必要な枠数（2の累乗、最低2）。"""
    return max(2, 2 ** ceil(log2(n))) if n > 1 else 2


def seed_order(size: int) -> list[int]:
    """枠の並び順に対応するシード番号（1始まり）を返す。

    size=8 -> [1, 8, 4, 5, 2, 7, 3, 6]
    隣り合う2枠が1回戦の1試合。1位と2位は決勝まで当たらない。
    """
    order = [1]
    while len(order) < size:
        m = len(order) * 2 + 1
        order = [x for s in order for x in (s, m - s)]
    return order


@dataclass
class Slot:
    fighter: Fighter | None
    seed: int | None  # None = 不戦勝枠（空き）


@dataclass
class Bracket:
    size: int
    slots: list[Slot]
    scores: dict[int, float]
    swaps: list[str] = field(default_factory=list)  # 同道場回避で行った入れ替えの説明

    def pairs(self) -> list[tuple[Slot, Slot]]:
        return [(self.slots[i], self.slots[i + 1]) for i in range(0, self.size, 2)]


def _same_dojo(a: Fighter | None, b: Fighter | None) -> bool:
    if a is None or b is None:
        return False
    da, db_ = (a.dojo or "").strip(), (b.dojo or "").strip()
    return bool(da) and da == db_


def generate(fighters: list[Fighter], avoid_same_dojo: bool = True) -> Bracket:
    """選手リストからトーナメント配置を作る。"""
    scores = {f.id: strength_score(f) for f in fighters}
    # 強い順 → 同点は経験年数 → 名前で安定ソート
    ranked = sorted(fighters, key=lambda f: (-scores[f.id], -(f.experience_years or 0), f.name))
    n = len(ranked)
    size = bracket_size_for(n)
    order = seed_order(size)
    slots = [Slot(ranked[s - 1], s) if s <= n else Slot(None, None) for s in order]
    bracket = Bracket(size=size, slots=slots, scores=scores)

    if avoid_same_dojo and n >= 4:
        _avoid_same_dojo(bracket)
    return bracket


def _pair_index(i: int) -> int:
    return i // 2


def _conflict(slots: list[Slot], i: int) -> bool:
    """枠 i を含む1回戦の組が同道場か。"""
    j = i ^ 1
    return _same_dojo(slots[i].fighter, slots[j].fighter)


def _avoid_same_dojo(bracket: Bracket, max_passes: int = 5) -> None:
    """同道場同士の1回戦を、シードが近い相手との入れ替えで解消する。

    入れ替え対象は「下位シードの方」。入れ替え先はシード差が小さい順に探し、
    入れ替えた結果どちらの組にも新たな同道場対戦が生まれない場合だけ採用する。
    """
    slots = bracket.slots
    for _ in range(max_passes):
        changed = False
        for i in range(0, bracket.size, 2):
            if not _conflict(slots, i):
                continue
            a, b = slots[i], slots[i + 1]
            # 下位シード（seed 番号が大きい方）を動かす
            mover_idx = i if (a.seed or 0) > (b.seed or 0) else i + 1
            mover = slots[mover_idx]
            candidates = [
                k for k in range(bracket.size)
                if _pair_index(k) != _pair_index(mover_idx) and slots[k].fighter is not None
            ]
            candidates.sort(key=lambda k: abs((slots[k].seed or 0) - (mover.seed or 0)))
            for k in candidates:
                slots[mover_idx], slots[k] = slots[k], slots[mover_idx]
                if not _conflict(slots, mover_idx) and not _conflict(slots, k):
                    bracket.swaps.append(
                        f"{mover.fighter.name}（{mover.fighter.dojo}）と"
                        f"{slots[mover_idx].fighter.name}（{slots[mover_idx].fighter.dojo}）の枠を入れ替え"
                    )
                    changed = True
                    break
                slots[mover_idx], slots[k] = slots[k], slots[mover_idx]  # 戻す
        if not changed:
            break


# ---------------------------------------------------------------------------
# 注意事項
# ---------------------------------------------------------------------------
def pair_warnings(
    a: Fighter | None, b: Fighter | None, weight_tolerance_kg: float = 5.0, on: date | None = None
) -> list[str]:
    """1回戦の組み合わせについての注意（同道場・体重差・帯差・年齢差）。"""
    if a is None or b is None:
        return []
    w: list[str] = []
    if _same_dojo(a, b):
        w.append("同じ道場")
    if a.weight_kg is not None and b.weight_kg is not None:
        diff = abs(a.weight_kg - b.weight_kg)
        if diff > weight_tolerance_kg:
            w.append(f"体重差 {diff:.1f}kg")
    if a.height_cm is not None and b.height_cm is not None and abs(a.height_cm - b.height_cm) > 15:
        w.append(f"身長差 {abs(a.height_cm - b.height_cm):.0f}cm")
    if abs(a.belt_rank - b.belt_rank) >= 4:
        w.append(f"帯差 {BELTS[a.belt_rank]}/{BELTS[b.belt_rank]}")
    aa, ab = a.age_at(on), b.age_at(on)
    if aa is not None and ab is not None and abs(aa - ab) >= 5:
        w.append(f"年齢差 {abs(aa - ab)}歳")
    return w


def round_label(round_no: int, total_rounds: int) -> str:
    remaining = total_rounds - round_no
    if remaining == 0:
        return "決勝"
    if remaining == 1:
        return "準決勝"
    if remaining == 2:
        return "準々決勝"
    return f"{round_no}回戦"
