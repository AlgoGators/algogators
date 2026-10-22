import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "research"))

from negrisk_common import buy_all_yes, fee_per_share, sell_all_yes  # noqa: E402


def test_fee_formula_matches_docs_table():
    # docs.polymarket.com fee table: 100 shares @ 0.50, sports rate 0.05 -> $1.25 ; crypto 0.07 @0.10 -> $0.63
    assert math.isclose(100 * fee_per_share(0.5, 0.05), 1.25)
    assert round(100 * fee_per_share(0.10, 0.07), 2) == 0.63
    assert fee_per_share(0.3, 0.0) == 0.0
    assert math.isclose(fee_per_share(0.3, 0.04), fee_per_share(0.7, 0.04))  # symmetric


def test_buy_all_yes_depth_walk_no_fees():
    asks = [[(0.30, 10), (0.35, 100)], [(0.30, 20)], [(0.35, 50)]]
    f = buy_all_yes(asks, [0, 0, 0])
    # first 10 sets at 0.95 (edge 0.05), next 10 at 1.00 (edge 0) -> stop
    assert math.isclose(f.top_edge, 0.05)
    assert math.isclose(f.shares, 10)
    assert math.isclose(f.profit, 0.5)
    assert math.isclose(f.capital, 9.5)
    sh, pr = f.at_capital(4.75)
    assert math.isclose(sh, 5) and math.isclose(pr, 0.25)


def test_buy_all_yes_fees_kill_small_edge():
    asks = [[(0.49, 100)], [(0.49, 100)]]
    f = buy_all_yes(asks, [0.05, 0.05])
    # gross edge 0.02, fees 2*0.05*0.49*0.51 = 0.02499 -> negative
    assert f.top_edge < 0 and f.shares == 0


def test_sell_side_convert_edge_and_capital():
    bids = [[(0.60, 10)], [(0.45, 30)]]
    f = sell_all_yes(bids, [0, 0])
    assert math.isclose(f.top_edge, 0.05)
    assert math.isclose(f.shares, 10)
    # buy NO at 0.40 and 0.55 -> capital 0.95 per set, convert -> 1 USDC
    assert math.isclose(f.capital, 9.5)
    assert math.isclose(f.profit, 0.5)


def test_min_size_filter():
    f = buy_all_yes([[(0.4, 3)], [(0.5, 3)]], [0, 0], min_shares=5)
    assert f.shares == 0 and f.profit == 0


def test_min_size_filter_also_clears_capital_curve():
    f = buy_all_yes([[(0.4, 3)], [(0.5, 3)]], [0, 0], min_shares=5)
    assert f.at_capital(1000) == (0.0, 0.0)


def test_topbook_edges_vectorised():
    import numpy as np
    from negrisk_common import episodes, topbook_edges

    A = np.array([[0.30, 0.30, 0.35], [0.30, 0.40, 0.35], [np.nan, 0.3, 0.3]])
    B = np.array([[0.29, 0.29, 0.34], [0.55, 0.50, 0.01], [0.2, 0.2, 0.2]])
    S = np.full_like(A, 10.0)
    buy, sell, bsz, ssz, k = topbook_edges(A, B, S, S, [0, 0, 0])
    assert np.isclose(buy[0], 0.05) and buy[1] < 0 and np.isnan(buy[2])
    assert np.isclose(sell[1], 0.06) and k[1] == 3  # all three bids positive -> sum - 1
    assert episodes(np.arange(3), buy) == [(0, 1)]
