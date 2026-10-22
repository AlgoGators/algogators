import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "research"))
from wx_tape import parse_markets  # noqa: E402
from wx_fv_om import OMFair  # noqa: E402


def test_ticker_strikes():
    df = pd.DataFrame({"ticker": ["KXHIGHNY-25AUG01-T79", "KXHIGHNY-25AUG01-B79.5", "KXHIGHNY-25AUG01-B81.5",
                                  "KXHIGHNY-25AUG01-T82"], "event_ticker": ["KXHIGHNY-25AUG01"] * 4})
    p = parse_markets(df).set_index("ticker")
    assert p.loc["KXHIGHNY-25AUG01-T79", "lo"] == -np.inf and p.loc["KXHIGHNY-25AUG01-T79", "hi"] == 78
    assert (p.loc["KXHIGHNY-25AUG01-B79.5", ["lo", "hi"]] == [79, 80]).all()
    assert p.loc["KXHIGHNY-25AUG01-T82", "lo"] == 83 and np.isinf(p.loc["KXHIGHNY-25AUG01-T82", "hi"])


def test_bracket_probs_sum_to_one_and_floor():
    mu = np.full(4, 80.0); s = np.full(4, 2.0); nu = np.full(4, 6.0)
    lo = np.array([-np.inf, 79, 81, 83]); hi = np.array([78, 80, 82, np.inf])
    p = OMFair.prob(mu, s, nu, lo, hi)
    assert abs(p.sum() - 1) < 1e-9
    q = OMFair.prob(mu, s, nu, lo, hi, floor_=np.full(4, 81.0))
    assert q[0] == 0 and q[1] == 0 and abs(q.sum() - 1) < 1e-9
