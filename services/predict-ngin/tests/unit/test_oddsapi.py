"""The Odds API collector: a failed quota read is an error, never "0 credits left"."""

import json

import requests

from predict_ngin.capture import oddsapi
from predict_ngin.capture.runner import EXIT_NO_DATA, EXIT_OK


class _Resp:
    def __init__(self, code, headers, text=""):
        self.status_code, self.headers, self.text = code, headers, text


def _run(monkeypatch, tmp_path, resp, dry_run=True):
    calls = []

    def fake_get(self, url, params=None, timeout=None):
        calls.append(url)
        return resp

    monkeypatch.setattr(oddsapi, "root", lambda: tmp_path)
    monkeypatch.setattr(oddsapi, "api_key", lambda: "k")
    monkeypatch.setattr(requests.Session, "get", fake_get)
    code = oddsapi.one_pass("basketball_nba", oddsapi.BOOKS, oddsapi.MARKETS, 40, dry_run)
    return code, calls


def test_failed_quota_read_is_an_error(monkeypatch, tmp_path):
    code, calls = _run(monkeypatch, tmp_path, _Resp(500, {}, "oops"), dry_run=False)
    assert code == EXIT_NO_DATA
    assert len(calls) == 1  # never went on to the paid call
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["ok"] is False and st["http"] == 500 and st["error"] == "quota check failed"


def test_missing_quota_header_is_an_error(monkeypatch, tmp_path):
    code, _ = _run(monkeypatch, tmp_path, _Resp(200, {}), dry_run=False)
    assert code == EXIT_NO_DATA


def test_quota_read_ok(monkeypatch, tmp_path):
    code, _ = _run(monkeypatch, tmp_path, _Resp(200, {"x-requests-remaining": "497"}))
    assert code == EXIT_OK
