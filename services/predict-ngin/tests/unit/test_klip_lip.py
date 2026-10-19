import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "research"))
import klip_lip as L  # noqa: E402


def test_docs_payout_example():
    # $100 pool, 10,000 snapshots, 8,000 non-excluded, 20% period score -> $16.00
    assert L.payout(100.0, [0.4] * 8000, 10000) == 16.0


def test_excluded_when_side_short():
    q, o, t = L.side_score([0.5], [900], 1000, 0.5)
    assert not q
    s, ex = L.snapshot_share(([0.5], [2000]), ([0.4], [900]), 1000, 0.5)
    assert ex and s == 0


def test_alone_at_better_price_takes_side():
    # we post 1000 at 0.04 above an existing 0.03 x 735: we reach target alone
    q, o, t = L.side_score([0.03, 0.02], [735, 409], 1000, 0.5, 0.04, 1000)
    assert q and o == t == 1000


def test_discount_below_reference():
    # ref = 0.50 (200 there); 800 two ticks below -> 0.25 each
    q, o, t = L.side_score([0.50, 0.48], [200, 800], 1000, 0.5)
    assert q and abs(t - (200 + 800 * 0.25)) < 1e-9


def test_join_behind_queue_level_vs_partial():
    # 1000 already at 0.50, we join behind with 100: 'level' counts us, 'partial' excludes us
    _, o_lv, t_lv = L.side_score([0.50], [1000], 1000, 0.5, 0.50, 100, mode="level")
    _, o_pt, t_pt = L.side_score([0.50], [1000], 1000, 0.5, 0.50, 100, mode="partial")
    assert abs(o_lv / t_lv - 100 / 1100) < 1e-9
    assert o_pt == 0
