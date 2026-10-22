"""SharpAPI client and recorder against a fake API (no network, no key needed)."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from predict_ngin.capture import sharpapi as rec_mod
from predict_ngin.capture.sharpapi import Recorder
from predict_ngin.venues.sharpapi import SharpAPIClient, SharpAPIError

HOUR_AGO = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()


class Resp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body = status, body
        self.headers = {
            "X-RateLimit-Limit": "1000",
            "X-RateLimit-Remaining": "990",
            "X-Data-Delay": "0",
            **(headers or {}),
        }

    def json(self):
        return self._body


def odds(i, ts="2026-10-17T12:00:00.000Z", price=-110, **kw):
    return {
        "id": f"o{i}",
        "sportsbook": "pinnacle",
        "event_id": "e1",
        "sport": "football",
        "market_type": "moneyline",
        "selection": "Home",
        "odds_american": price,
        "odds_decimal": 1.909,
        "probability": 0.5238,
        "is_live": False,
        "is_active": True,
        "timestamp": ts,
        "line": None,
        "brand_new_field": 7,
        **kw,
    }


class FakeSession:
    """Routes GETs by path; snapshot pages use cursors; delta returns what `deltas` holds."""

    def __init__(self):
        self.headers = {}
        self.calls = []
        self.deltas = []
        self.fail = {}

    def get(self, url, params=None, timeout=None):
        path = url.split("/api/v1")[1]
        self.calls.append((path, dict(params or {})))
        if path in self.fail:
            return self.fail.pop(path)
        if path == "/odds":
            if (params or {}).get("cursor") == "p2":
                return Resp(200, {"data": [odds(3)], "pagination": {"has_more": False}})
            return Resp(
                200,
                {"data": [odds(1), odds(2)], "pagination": {"has_more": True, "next_cursor": "p2"}},
            )
        if path == "/odds/delta":
            return Resp(200, {"data": list(self.deltas), "pagination": {"has_more": False}})
        if path == "/odds/closing":
            assert params["event_id"] == "e1"
            book = {"odds": [odds(9), odds(10)]}
            return Resp(
                200, {"data": {"event_id": "e1", "captured_at": "x", "books": {"pinnacle": book}}}
            )
        if path == "/splits":
            return Resp(403, {"error": {"code": "tier_restricted", "message": "Pro"}})
        if path == "/events":
            return Resp(
                200,
                {
                    "data": [
                        {
                            "id": "e1",
                            "status": (params or {}).get("status", "upcoming"),
                            "books": ["pinnacle"],
                            "start_time": HOUR_AGO,
                            "markets": ["moneyline"],
                        }
                    ],
                    "pagination": {"has_more": False},
                },
            )
        if path in ("/account", "/account/usage"):
            return Resp(200, {"data": {"tier": "sharp", "api_key": "secret", "email": "x@y"}})
        return Resp(200, {"data": []})


@pytest.fixture
def client():
    return SharpAPIClient("k", session=FakeSession(), sleep=lambda s: None)


def test_client_pages_and_adapts_rate(client):
    rows = list(client.pages("/odds"))
    assert [r["id"] for r in rows] == ["o1", "o2", "o3"]
    assert client.per_minute == 1000  # sized from X-RateLimit-Limit
    assert client.session.headers["X-API-Key"] == "k"
    assert client.session.calls[1][1]["cursor"] == "p2"


def test_client_retries_429_and_raises_codes(client):
    s = client.session
    s.fail["/events"] = Resp(429, {"error": {"code": "rate_limited"}}, {"Retry-After": "1"})
    body, _ = client.get("/events")
    assert body["data"][0]["id"] == "e1" and client.n_429 == 1
    with pytest.raises(SharpAPIError) as e:
        client.get("/splits")
    assert e.value.code == "tier_restricted" and e.value.status == 403


def test_missing_key(monkeypatch, tmp_path):
    monkeypatch.delenv("SHARPAPI_KEY", raising=False)
    monkeypatch.setattr("predict_ngin.venues.sharpapi.client.repo_root", lambda: tmp_path)
    with pytest.raises(SharpAPIError, match="missing_api_key"):
        SharpAPIClient(session=FakeSession())


def test_recorder_snapshot_delta_dedupe_and_files(client, tmp_path):
    t = [0.0]
    r = Recorder(client, tmp_path, clock=lambda: t[0])
    client.get("/account")  # learn the tier (1000/min -> paid schedule)
    for _ in range(14):  # snapshot pages interleave with the other jobs
        r.step()
    assert r.snapshots_done == 1 and r.counts["snapshot_rows"] == 3
    assert "splits" in r.disabled  # 403 tier_restricted turns the job off
    assert r.counts["closing_events"] == 1 and r.counts["closing_rows"] == 2
    # a delta repeating a stored row plus one real change
    client.session.deltas = [odds(1), odds(1, ts="2026-10-17T12:00:05.000Z", price=-120)]
    t[0] += 10
    r.step()
    q = [p for path, p in client.session.calls if path == "/odds/delta"][-1]
    assert q["since"] == "2026-10-17T11:59:50.000Z"  # newest timestamp minus the overlap
    assert r.counts["delta_rows"] == 1
    r.sink.flush()
    df = pd.concat(pd.read_parquet(p) for p in tmp_path.glob("*/odds/*.parquet"))
    assert len(df) == 4 and set(df["source"]) == {"snapshot", "delta"}
    assert df["odds_american"].dtype == "float64" and df["is_active"].dtype == "boolean"
    assert df["extra"].str.contains("brand_new_field").all()
    ev = pd.concat(pd.read_parquet(p) for p in tmp_path.glob("*/events/*.parquet"))
    cl = pd.concat(pd.read_parquet(p) for p in tmp_path.glob("*/closing/*.parquet"))
    assert set(cl["book_key"]) == {"pinnacle"} and len(cl) == 2
    assert set(ev["status"]) == {"upcoming", "final"} and ev["books"].iloc[0] == '["pinnacle"]'
    st = r.status()
    assert st["name"] == "sharpapi-recorder" and st["rows_written"]["odds"] == 4
    usage = (tmp_path / "usage.jsonl").read_text()
    assert "secret" not in usage and "x@y" not in usage


def test_run_stops_on_stop_file(monkeypatch, tmp_path):
    monkeypatch.setenv("PREDICT_NGIN_DATA", str(tmp_path))
    monkeypatch.setattr(
        rec_mod,
        "SharpAPIClient",
        lambda: SharpAPIClient("k", session=FakeSession(), sleep=lambda s: None),
    )
    (tmp_path / "sharpapi").mkdir()
    (tmp_path / "sharpapi" / "STOP").write_text("")
    assert rec_mod.run(1, None, {}, None, None) == 0
    assert "STOP file" in (tmp_path / "sharpapi" / "status.json").read_text()


def test_stop_file_at_deadline_and_host_in_names(monkeypatch, tmp_path):
    monkeypatch.setenv("PREDICT_NGIN_DATA", str(tmp_path))
    monkeypatch.setattr(
        rec_mod,
        "SharpAPIClient",
        lambda: SharpAPIClient("k", session=FakeSession(), sleep=lambda s: None),
    )
    assert rec_mod.run(None, "2000-01-01T00:00:00Z", {}, None, None, stop_at_end=True) == 0
    assert "deadline" in (tmp_path / "sharpapi" / "STOP").read_text()
    s = rec_mod.Sink(tmp_path / "s")
    s.add("odds", [odds(1)])
    s.flush()
    (f,) = (tmp_path / "s").glob("*/odds/*.parquet")
    assert f.name.startswith(f"part-{rec_mod.HOST}-")
