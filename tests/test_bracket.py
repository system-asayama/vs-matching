"""組み合わせロジックのテスト（DB不要）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bracket as br  # noqa: E402
from models import Fighter  # noqa: E402


def mk(id_, name, dojo="", belt="無級", years=0, wins=0, losses=0, weight=None):
    f = Fighter(name=name, dojo=dojo, belt=belt, experience_years=years, wins=wins, losses=losses, draws=0, weight_kg=weight)
    f.id = id_
    return f


def test_seed_order_standard():
    assert br.seed_order(2) == [1, 2]
    assert br.seed_order(4) == [1, 4, 2, 3]
    assert br.seed_order(8) == [1, 8, 4, 5, 2, 7, 3, 6]


def test_bracket_size():
    assert br.bracket_size_for(2) == 2
    assert br.bracket_size_for(3) == 4
    assert br.bracket_size_for(5) == 8
    assert br.bracket_size_for(8) == 8
    assert br.bracket_size_for(9) == 16


def test_strength_score_orders_by_belt_then_record():
    weak = mk(1, "白帯", belt="無級")
    mid = mk(2, "茶帯", belt="1級", years=3, wins=2, losses=2)
    strong = mk(3, "黒帯", belt="初段", years=8, wins=10, losses=1)
    assert br.strength_score(weak) < br.strength_score(mid) < br.strength_score(strong)


def test_top_seeds_get_byes_and_are_separated():
    fs = [mk(i, f"f{i}", belt=b) for i, b in enumerate(["初段", "1級", "3級", "5級", "8級"], start=1)]
    b = br.generate(fs, avoid_same_dojo=False)
    assert b.size == 8
    # 1位シードは枠0、2位シードは下半分の先頭
    assert b.slots[0].fighter.name == "f1"
    assert b.slots[4].fighter.name == "f2"
    # 5人なので3つの不戦勝枠。上位3シードの相手が空き
    byes = [i for i, s in enumerate(b.slots) if s.fighter is None]
    assert len(byes) == 3
    assert b.slots[1].fighter is None  # f1 の相手
    assert b.slots[5].fighter is None  # f2 の相手
    assert b.slots[7].fighter is None  # f3 の相手（seed6の枠）


def test_same_dojo_avoided_in_first_round():
    # 4人。シード順で組むと A道場同士が1回戦で当たる配置にする
    # スコア: a1 > b1 > a2 > b2 → seed [1,4,2,3] = a1 vs b2, b1 vs a2 → 問題なし。
    # 逆に a1 > a2 > b1 > b2 → [1,4,2,3] = a1 vs b2, a2 vs b1 → 問題なし。
    # 同道場が当たるのは a1 > b1 > b2 > a2 → a1 vs a2, b1 vs b2 のケース。
    fs = [
        mk(1, "a1", dojo="A", belt="初段"),
        mk(2, "b1", dojo="B", belt="1級"),
        mk(3, "b2", dojo="B", belt="2級"),
        mk(4, "a2", dojo="A", belt="3級"),
    ]
    naive = br.generate(fs, avoid_same_dojo=False)
    pairs = [(p[0].fighter.dojo, p[1].fighter.dojo) for p in naive.pairs()]
    assert ("A", "A") in pairs and ("B", "B") in pairs

    fixed = br.generate(fs, avoid_same_dojo=True)
    pairs = [(p[0].fighter.dojo, p[1].fighter.dojo) for p in fixed.pairs()]
    assert all(x != y for x, y in pairs)
    assert fixed.swaps  # 入れ替えの説明がある
    # 全員がちゃんと残っている
    names = sorted(s.fighter.name for s in fixed.slots if s.fighter)
    assert names == ["a1", "a2", "b1", "b2"]


def test_same_dojo_unavoidable_keeps_everyone():
    fs = [mk(i, f"x{i}", dojo="同じ") for i in range(1, 7)]
    b = br.generate(fs, avoid_same_dojo=True)
    names = sorted(s.fighter.name for s in b.slots if s.fighter)
    assert names == [f"x{i}" for i in range(1, 7)]


def test_pair_warnings():
    a = mk(1, "a", dojo="A", belt="初段", weight=70)
    b = mk(2, "b", dojo="A", belt="8級", weight=58)
    w = br.pair_warnings(a, b, weight_tolerance_kg=5)
    assert any("同じ道場" in x for x in w)
    assert any("体重差 12.0kg" in x for x in w)
    assert any("帯差" in x for x in w)
    assert br.pair_warnings(a, None) == []


def test_round_label():
    assert br.round_label(1, 1) == "決勝"
    assert br.round_label(1, 3) == "準々決勝"
    assert br.round_label(1, 4) == "1回戦"
    assert br.round_label(2, 3) == "準決勝"
    assert br.round_label(3, 3) == "決勝"
    assert br.round_label(2, 4) == "準々決勝"
