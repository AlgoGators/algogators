# Frozen copies of the runner pieces replaced in step 5.8 (2026-10-15), verbatim as of commit 1277cb0.
# Do not edit; tests/unit/test_capture_runner.py compares against these.
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

# status_doc's helpers (_iso, EVAL_DATE, next_boundary) are bound from perps_carry_paper by the test.
_iso = EVAL_DATE = next_boundary = None


# ---------------------------------------------------------------- scripts/live/perps_carry_paper.py:630-679
def _atomic_write(p: Path, obj) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    os.replace(tmp, p)


class Lock:
    def __init__(self, p: Path, stale_s: int = 900):
        self.p, self.stale_s, self.held = p, stale_s, False

    def __enter__(self):
        if self.p.exists() and time.time() - self.p.stat().st_mtime > self.stale_s:
            self.p.unlink(missing_ok=True)
        try:
            fd = os.open(self.p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.held = True
        except FileExistsError:
            self.held = False
        return self

    def __exit__(self, *a):
        if self.held:
            self.p.unlink(missing_ok=True)


def status_doc(state: dict, out: dict, http, now: datetime) -> dict:
    sm = out["summary"]
    return {"name": "perps-carry-paper", "mode": "paper", "ok": True, "last_run": _iso(now),
            "prereg": "docs/PERPS_CARRY_FORWARD_PREREG.md", "forward_start": state["start_utc"],
            "eval_date": _iso(EVAL_DATE), "next_boundary": _iso(next_boundary(now)), "passes": state["passes"],
            "positions": {c: {k: v for k, v in p.items() if k in ("open", "contracts", "perp_entry", "mark",
                                                                    "margin_ratio", "liq_dist", "funding")}
                          for c, p in sm["positions"].items()},
            "cum_pnl_vs_tbill": {k: round(v, 2) for k, v in sm["excess"].items()},
            "equity": {k: round(v, 2) for k, v in sm["equity"].items()}, "tbill_benchmark": round(sm["benchmark"], 2),
            "attribution": sm["attribution"], "boundaries": {"decided": state["n_boundaries_decided"],
                                                              "missed": state["n_boundaries_missed"],
                                                              "deferred": state["n_deferred"]},
            "kill": state["kill"], "requests": http.n, "bytes": http.bytes, "http_errors": http.errors}




def error_status(now_iso: str) -> dict:   # perps_carry_paper.main, /margin/markets unavailable
    return {"name": "perps-carry-paper", "mode": "paper", "ok": False,
            "last_run": now_iso, "error": "/margin/markets unavailable"}


# ---------------------------------------------------------------- scripts/research/perps_funding_collect.py
def funding_last_ok(out: Path, ok, receipt, rows, est, c, added):   # run(), lines 208-212
    tmp = out / "last_ok.json.tmp"
    if ok:
        tmp.write_text(json.dumps({"last_ok": receipt, "markets": len(rows), "estimates": len(est),
                                   "requests": c.n, "bytes": c.bytes, "http_errors": c.errors, "new_prints": added}))
        os.replace(tmp, out / "last_ok.json")
