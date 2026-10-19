"""Tests for scripts/live/market_feature_watch.py with a mocked HTTP layer (httpx.MockTransport)."""
import json
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "live"))
import market_feature_watch as W  # noqa: E402

D1, D2, D3 = date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 17)


def series(t, freq="daily", vol="0.00", fee="quadratic"):
    return {"ticker": t, "title": t.lower(), "category": "Sports", "categories": ["Sports"], "tags": None,
            "frequency": freq, "fee_type": fee, "fee_multiplier": 1, "settlement_sources": [{"name": "src"}],
            "contract_url": "u", "volume_fp": vol}


def market(t, status="active", **kw):
    m = {"ticker": t, "event_ticker": t.rsplit("-", 1)[0], "status": status, "market_type": "binary",
         "strike_type": "greater", "settlement_bounds_type": "default", "volume_fp": "10.00",
         "volume_24h_fp": "1.00", "open_interest_fp": "5.00", "rules_primary": f"rules {t}"}
    m.update(kw)
    return m


class Exchange:
    """Minimal fake of the public endpoints the watcher uses, with cursor pagination."""

    def __init__(self):
        self.series = [series("KXA"), series("KXB")]
        self.markets = {"open": [market("KXA-1-X"), market("KXA-1-Y"), market("KXB-1-X")], "unopened": []}
        self.perps = [{"ticker": "KXBTCPERP", "title": "BTC", "volume": "1"}]
        self.calls = []
        self.fail_429 = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        p, q = req.url.path.split("/trade-api/v2")[1], dict(req.url.params)
        self.calls.append((p, q))
        if self.fail_429:
            self.fail_429 -= 1
            return httpx.Response(429, headers={"Retry-After": "1"})
        if p == "/series":
            return httpx.Response(200, json={"series": self.series})
        if p == "/margin/markets":
            return httpx.Response(200, json={"markets": self.perps, "cursor": ""})
        if p == "/markets":
            if "tickers" in q:
                want = set(q["tickers"].split(","))
                allm = [m for ms in self.markets.values() for m in ms] + getattr(self, "closed", [])
                return httpx.Response(200, json={"markets": [m for m in allm if m["ticker"] in want], "cursor": ""})
            if "series_ticker" in q:
                allm = [m for ms in self.markets.values() for m in ms] + getattr(self, "closed", [])
                ms = [m for m in allm if m["event_ticker"].split("-")[0] == q["series_ticker"]]
                return httpx.Response(200, json={"markets": ms[: int(q.get("limit", 1000))], "cursor": ""})
            ms = self.markets.get(q["status"], [])
            i = int(q.get("cursor") or 0)
            page = ms[i:i + 2]  # page size 2 to exercise pagination
            nxt = str(i + 2) if i + 2 < len(ms) else ""
            return httpx.Response(200, json={"markets": page, "cursor": nxt})
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def ex():
    return Exchange()


def api(ex):
    return W.Api(client=httpx.Client(transport=httpx.MockTransport(ex.handler)), min_interval=0,
                 sleep=lambda s: None)


def records(out, d):
    return [json.loads(line) for line in (out / f"{d.isoformat()}.jsonl").read_text().splitlines()]


def kinds(recs, k):
    return [r for r in recs if r["kind"] == k]


def test_baseline_then_new_series_diff(ex, tmp_path):
    s1 = W.run(api(ex), tmp_path, D1, log=lambda m: None)
    assert s1["baseline"] and s1["n_new_series"] == 0 and s1["n_series"] == 2
    assert s1["n_markets_scanned"] == 3
    ex.series += [series("KXETH15M", freq="fifteen_min", vol="123.00"), series("KXNEWPERP")]
    ex.markets["open"].append(market("KXETH15M-1-X", rules_primary="Resolves per CF Benchmarks"))
    ex.series = [s for s in ex.series if s["ticker"] != "KXB"]
    s2 = W.run(api(ex), tmp_path, D2, log=lambda m: None)
    assert not s2["baseline"]
    new = {r["series_ticker"]: r for r in kinds(records(tmp_path, D2), "new_series")}
    assert set(new) == {"KXETH15M", "KXNEWPERP"}
    assert new["KXETH15M"]["flags"] == ["fifteen_min"] and new["KXETH15M"]["volume_fp"] == "123.00"
    assert new["KXETH15M"]["rules_primary"] == "Resolves per CF Benchmarks"
    assert new["KXETH15M"]["fee_type"] == "quadratic" and new["KXETH15M"]["category"] == "Sports"
    assert "perpetual" in new["KXNEWPERP"]["flags"]
    assert s2["new_fast_series"] == ["KXETH15M"] and s2["new_perpetual_series"] == ["KXNEWPERP"]
    assert s2["removed_series"] == ["KXB"]
    # day 3: no longer new, followed up with volume
    ex.series[-2]["volume_fp"] = "999.00"
    W.run(api(ex), tmp_path, D3, log=lambda m: None)
    r3 = records(tmp_path, D3)
    assert not kinds(r3, "new_series")
    fu = {r["series_ticker"]: r for r in kinds(r3, "series_followup")}
    assert fu["KXETH15M"]["day_n"] == 1 and fu["KXETH15M"]["volume_fp"] == "999.00"


def test_series_fee_change_detected(ex, tmp_path):
    W.run(api(ex), tmp_path, D1, log=lambda m: None)
    ex.series[0]["fee_type"] = "quadratic_with_maker_fees"
    W.run(api(ex), tmp_path, D2, log=lambda m: None)
    ch = kinds(records(tmp_path, D2), "series_changed")
    assert ch == [{"date": "2026-10-16", "kind": "series_changed", "series_ticker": "KXA",
                   "changes": {"fee_type": ["quadratic", "quadratic_with_maker_fees"]}}]


def test_floor_change_detection(ex, tmp_path):
    ex.markets["open"].append(market("KXSHUT-26-D10", settlement_bounds_type="floor",
                                     settlement_floor_dollars="0.0000"))
    s1 = W.run(api(ex), tmp_path, D1, log=lambda m: None)
    r1 = records(tmp_path, D1)
    assert s1["n_floor_markets"] == 1 and len(kinds(r1, "new_floor_market")) == 1
    assert not kinds(r1, "floor_change")
    ex.markets["open"][-1]["settlement_floor_dollars"] = "0.3000"
    s2 = W.run(api(ex), tmp_path, D2, log=lambda m: None)
    fc = kinds(records(tmp_path, D2), "floor_change")
    assert s2["n_floor_changes"] == 1
    assert fc[0]["prev_floor"] == 0.0 and fc[0]["floor"] == 0.3 and fc[0]["direction"] == "raise"
    assert fc[0]["delta"] == 0.3 and fc[0]["prev_date"] == "2026-10-15"
    # unchanged next day: no change record; lowered after that: "lower"
    W.run(api(ex), tmp_path, D3, log=lambda m: None)
    assert not kinds(records(tmp_path, D3), "floor_change")
    ex.markets["open"][-1]["settlement_floor_dollars"] = "0.2000"
    W.run(api(ex), tmp_path, date(2026, 10, 18), log=lambda m: None)
    fc = kinds(records(tmp_path, date(2026, 10, 18)), "floor_change")
    assert fc[0]["direction"] == "lower" and fc[0]["prev_date"] == "2026-10-17"
    fs = kinds(records(tmp_path, date(2026, 10, 18)), "feature_series")
    assert fs[0]["feature"] == "floor" and fs[0]["series_ticker"] == "KXSHUT" and fs[0]["open_interest_fp"] == 5.0


def test_tracked_market_fetched_after_leaving_scan(ex, tmp_path):
    fm = market("KXSHUT-26-D10", settlement_bounds_type="floor", settlement_floor_dollars="0.1000")
    ex.markets["open"].append(fm)
    W.run(api(ex), tmp_path, D1, log=lambda m: None)
    ex.markets["open"].remove(fm)
    ex.closed = [dict(fm, status="finalized", settlement_value_dollars="0.5000")]
    W.run(api(ex), tmp_path, D2, log=lambda m: None)
    rows = kinds(records(tmp_path, D2), "floor_market")
    assert rows and rows[0]["status"] == "finalized" and rows[0]["settlement_value_dollars"] == "0.5000"
    W.run(api(ex), tmp_path, D3, log=lambda m: None)  # finalized: no longer fetched
    assert not kinds(records(tmp_path, D3), "floor_market")
    assert not any("tickers" in q for p, q in ex.calls[-6:])


def test_scalar_markets_and_watch_series(ex, tmp_path):
    ex.series.append(series("KXMLBGAMESSCALAR"))
    payout = {"0": "0.00", "5": "0.50", "10": "1.00"}
    ex.closed = [market("KXMLBGAMESSCALAR-26OCT15-T", status="closed", strike_type="custom")]
    ex.markets["open"].append(market("KXSHUTDAYS-26-X", market_type="scalar", strike_type="functional",
                                     functional_strike=payout, rules_secondary="rounded to nearest $0.01"))
    s1 = W.run(api(ex), tmp_path, D1, log=lambda m: None)
    rows = {r["ticker"]: r for r in kinds(records(tmp_path, D1), "scalar_market")}
    assert set(rows) == {"KXSHUTDAYS-26-X", "KXMLBGAMESSCALAR-26OCT15-T"}
    assert rows["KXSHUTDAYS-26-X"]["functional_strike"] == payout and not rows["KXSHUTDAYS-26-X"]["watched"]
    assert rows["KXSHUTDAYS-26-X"]["rules_secondary"] == "rounded to nearest $0.01"
    assert rows["KXMLBGAMESSCALAR-26OCT15-T"]["watched"]
    assert s1["n_scalar_series"] == 2 and s1["watched_series"] == ["KXMLBGAMESSCALAR"]
    ex.markets["open"][-1]["functional_strike"] = {"0": "0.00", "10": "1.00"}
    W.run(api(ex), tmp_path, D2, log=lambda m: None)
    pc = kinds(records(tmp_path, D2), "payout_change")
    assert len(pc) == 1 and pc[0]["ticker"] == "KXSHUTDAYS-26-X"


def test_idempotent_per_day_and_force(ex, tmp_path):
    ex.markets["open"].append(market("KXSHUT-26-D10", settlement_bounds_type="floor",
                                     settlement_floor_dollars="0.0000"))
    W.run(api(ex), tmp_path, D1, log=lambda m: None)
    ex.series.append(series("KXNEW"))
    ex.markets["open"][-1]["settlement_floor_dollars"] = "0.3000"
    W.run(api(ex), tmp_path, D2, log=lambda m: None)
    first = (tmp_path / "2026-10-16.jsonl").read_text()
    n_calls = len(ex.calls)
    assert W.run(api(ex), tmp_path, D2, log=lambda m: None) == {"skipped": True, "date": "2026-10-16"}
    assert len(ex.calls) == n_calls  # no HTTP at all on a done day
    W.run(api(ex), tmp_path, D2, force=True, log=lambda m: None)
    again = [json.loads(x) for x in (tmp_path / "2026-10-16.jsonl").read_text().splitlines()]
    strip = lambda rs: [r for r in rs if r["kind"] != "run"]  # noqa: E731
    assert strip(again) == strip([json.loads(x) for x in first.splitlines()])
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["completed_days"] == ["2026-10-15", "2026-10-16"]
    assert st["floors"]["KXSHUT-26-D10"]["history"] == [["2026-10-15", 0.0], ["2026-10-16", 0.3]]
    assert not list(tmp_path.glob("*.tmp"))


def test_new_perp_and_new_market_field(ex, tmp_path):
    W.run(api(ex), tmp_path, D1, log=lambda m: None)
    assert not kinds(records(tmp_path, D1), "new_perp")
    ex.perps.append({"ticker": "KXSOLPERP", "title": "SOL", "volume": "0"})
    ex.markets["open"][0]["payout_curve_id"] = "abc"
    W.run(api(ex), tmp_path, D2, log=lambda m: None)
    r2 = records(tmp_path, D2)
    assert [r["ticker"] for r in kinds(r2, "new_perp")] == ["KXSOLPERP"]
    assert kinds(r2, "new_market_field")[0]["fields"] == ["payout_curve_id"]


def test_seed_series_first_run_diff(ex, tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"fetched_utc": "2026-10-14T00:00:00Z", "series": [series("KXA")]}))
    out = tmp_path / "fw"
    s = W.run(api(ex), out, D1, seed_series=seed, log=lambda m: None)
    assert not s["baseline"] and s["n_new_series"] == 1
    assert [r["series_ticker"] for r in kinds(records(out, D1), "new_series")] == ["KXB"]


def test_429_backoff_and_failure(ex, tmp_path):
    slept = []
    ex.fail_429 = 2
    a = W.Api(client=httpx.Client(transport=httpx.MockTransport(ex.handler)), min_interval=0, sleep=slept.append)
    assert a.get("/series")["series"]
    assert a.n_retries == 2 and slept == [1.0, 2.0]  # Retry-After 1 vs 2**attempt
    ex.fail_429 = 100
    a = W.Api(client=httpx.Client(transport=httpx.MockTransport(ex.handler)), min_interval=0,
              sleep=lambda s: None, max_retries=3)
    with pytest.raises(W.ApiError):
        a.get("/series")


def test_main_exits_nonzero_on_failure(tmp_path, monkeypatch):
    def boom(req):
        return httpx.Response(400)
    real = W.Api
    monkeypatch.setattr(W, "Api", lambda: real(client=httpx.Client(transport=httpx.MockTransport(boom)),
                                               min_interval=0, sleep=lambda s: None))
    assert W.main(["--out-dir", str(tmp_path)]) == 1
    (tmp_path / "STOP").write_text("")
    assert W.main(["--out-dir", str(tmp_path)]) == 0


def test_rate_limit_spacing():
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s
    a = W.Api(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))),
              min_interval=0.5, sleep=sleep, clock=lambda: t[0])
    for _ in range(3):
        a.get("/x")
    assert slept == [0.5, 0.5]
