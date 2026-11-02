"""Reconciliation report: estimated LIP share/credit (from the pilot journal) vs actual payouts.

    venv/Scripts/python.exe -m src.live.lip_pilot.report data/live/lip_pilot/*.jsonl [--payouts payouts.csv]

Kalshi documents no LIP payout endpoint (docs.kalshi.com llms.txt, 2026-10-15). Actual credits come from:
- ``--payouts`` CSV with columns ``market_ticker,amount_usd`` (optionally ``program_id``, ``source``), copied from the
  Kalshi account history / statement; and/or
- the implied credit from read-only ``GET /portfolio/balance`` snapshots the live pilot journals every minute:
  implied = (last balance - first balance) + fill cost paid - settlement revenue (``--settlement-usd``).
  Settlement happens the next morning, so run the report after settlement with the settlement figure.
Prereg stop rule (docs/LIPSCAN_PREREG.md section 6): credits below 50% of the model.
"""
from __future__ import annotations

import argparse
import csv
import glob
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .journal import read_jsonl


def load_records(paths: Iterable[str]) -> List[dict]:
    out: List[dict] = []
    for pat in paths:
        for p in sorted(glob.glob(pat)) or [pat]:
            out += read_jsonl(Path(p))
    return out


def load_payouts(path: Optional[str]) -> Dict[str, float]:
    if not path:
        return {}
    pay: Dict[str, float] = defaultdict(float)
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pay[row["market_ticker"].strip()] += float(row["amount_usd"])
    return dict(pay)


def summarise(records: List[dict], payouts: Dict[str, float], settlement_usd: float = 0.0) -> dict:
    est: Dict[str, float] = defaultdict(float)
    prog: Dict[str, str] = {}
    snaps: Dict[str, int] = defaultdict(int)
    excl: Dict[str, int] = defaultdict(int)
    share_sum: Dict[str, float] = defaultdict(float)
    last_by_run: Dict[tuple, float] = {}
    run = None
    fills: Dict[str, Dict[str, float]] = defaultdict(lambda: {"yes": 0.0, "no": 0.0, "cost": 0.0})
    balances: List[float] = []
    modes = set()
    for r in records:
        k = r.get("kind")
        if k == "start":
            run = r.get("run_id")
            modes.add(r.get("mode"))
        elif k == "snapshot":
            t = r["ticker"]
            last_by_run[(run, t)] = float(r.get("est_reward_usd") or 0.0)   # cumulative within a run
            prog[t] = r.get("program_id", "")
            snaps[t] += 1
            excl[t] += int(bool(r.get("excluded")))
            share_sum[t] += float(r.get("share_yes") or 0) + float(r.get("share_no") or 0)
        elif k == "fill" and r.get("ours"):
            f = fills[r["ticker"]]
            f[r["side"]] += float(r["count"])
            f["cost"] += float(r["count"]) * float(r["px"])
        elif k == "reconcile" and isinstance(r.get("balance"), dict):
            b = r["balance"].get("balance")
            if b is not None:
                balances.append(float(b) / 100.0)   # GET /portfolio/balance: cents
    for (_, t), v in last_by_run.items():
        est[t] += v
    tickers = sorted(set(est) | set(payouts) | set(fills))
    rows = []
    for t in tickers:
        e, a = est.get(t, 0.0), payouts.get(t)
        rows.append({
            "market_ticker": t, "program_id": prog.get(t, ""), "snapshots": snaps.get(t, 0),
            "excluded_frac": round(excl[t] / snaps[t], 4) if snaps.get(t) else None,
            "mean_snapshot_score": round(share_sum[t] / snaps[t], 5) if snaps.get(t) else None,
            "est_credit_usd": round(e, 4), "est_below_1usd_minimum": e < 1.0,
            "actual_usd": a, "actual_over_est": round(a / e, 3) if a is not None and e > 0 else None,
            "yes_filled": fills[t]["yes"] if t in fills else 0.0, "no_filled": fills[t]["no"] if t in fills else 0.0,
            "fill_cost_usd": round(fills[t]["cost"], 4) if t in fills else 0.0,
        })
    tot_est = sum(r["est_credit_usd"] for r in rows)
    tot_act = sum(v for v in payouts.values()) if payouts else None
    fill_cost = sum(r["fill_cost_usd"] for r in rows)
    implied = None
    if len(balances) >= 2:
        implied = round(balances[-1] - balances[0] + fill_cost - settlement_usd, 4)
    ratio = (tot_act / tot_est) if (tot_act is not None and tot_est > 0) else None
    return {"rows": rows, "modes": sorted(m for m in modes if m), "total_est_usd": round(tot_est, 4),
            "total_actual_usd": tot_act, "implied_credit_from_balance_usd": implied,
            "actual_over_est": None if ratio is None else round(ratio, 3),
            "prereg_stop_credits_below_half_model": None if ratio is None else ratio < 0.5}


def render(s: dict) -> str:
    lines = [f"# LIP pilot reconciliation (modes: {', '.join(s['modes']) or 'n/a'})", "",
             "| market | snapshots | excl. | mean score | est $ | <$1 min | actual $ | actual/est | fills Y/N | fill $ |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in s["rows"]:
        actual = "" if r["actual_usd"] is None else f"{r['actual_usd']:.2f}"
        ratio = "" if r["actual_over_est"] is None else str(r["actual_over_est"])
        lines.append(f"| {r['market_ticker']} | {r['snapshots']} | {r['excluded_frac']} | {r['mean_snapshot_score']} | "
                     f"{r['est_credit_usd']:.4f} | {'yes' if r['est_below_1usd_minimum'] else 'no'} | {actual} | "
                     f"{ratio} | {r['yes_filled']:.0f}/{r['no_filled']:.0f} | {r['fill_cost_usd']:.2f} |")
    lines += ["", f"- Total estimated credit: ${s['total_est_usd']:.4f}",
              f"- Total actual (payouts CSV): {s['total_actual_usd'] if s['total_actual_usd'] is not None else 'n/a'}",
              f"- Implied credit from balance snapshots: {s['implied_credit_from_balance_usd'] if s['implied_credit_from_balance_usd'] is not None else 'n/a'}",
              f"- Actual / estimated: {s['actual_over_est'] if s['actual_over_est'] is not None else 'n/a'}",
              f"- Prereg stop (credits < 50% of model): {s['prereg_stop_credits_below_half_model']}"]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("journals", nargs="+")
    ap.add_argument("--payouts", default=None)
    ap.add_argument("--settlement-usd", type=float, default=0.0)
    ap.add_argument("--csv-out", default=None)
    a = ap.parse_args(argv)
    s = summarise(load_records(a.journals), load_payouts(a.payouts), a.settlement_usd)
    print(render(s))
    if a.csv_out:
        with open(a.csv_out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(s["rows"][0].keys()) if s["rows"] else ["market_ticker"])
            w.writeheader()
            w.writerows(s["rows"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
