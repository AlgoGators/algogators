"""Daily Kalshi market-feature watch (read-only; public REST only; never places orders).

Logs, once per UTC day, the appearance and evolution of new Kalshi market features so they can be tested once
they have volume:
  1. settlement floors: markets whose settlement_bounds_type is not "default" (docs: "floor", with
     settlement_floor_dollars), every floor change (a raise cancels resting YES orders at or below the floor);
  2. scalar / payout-curve contracts: market_type "scalar", strike_type "functional" or a functional_strike
     payout schedule, plus every market of watched series (default *SCALAR* tickers, e.g. MLBGAMESSCALAR);
  3. new series (diff of GET /series against the state file) with category, fee type, settlement sources,
     a sample rules_primary and first-day volume, flagging 15-minute / hourly / perpetual series; new perps
     markets from GET /margin/markets; fee / frequency changes of existing series; new market or series fields;
  4. daily volume and open interest for every tracked floor and scalar market, also after it leaves the
     open/unopened scan (fetched by ticker until it is finalized).

Output: data/vendor/featwatch/YYYY-MM-DD.jsonl (one JSON object per line, field "kind") and
data/vendor/featwatch/state.json (running state). Both are written to temp files and renamed, state last.
A day whose file exists and is recorded in the state is skipped (exit 0) unless --force is given; a forced
re-run of the same day gives the same diff because the state keeps first-seen dates and dated histories.

The first run (empty state) is a baseline: it records every series as seen and flags none as new. Use
--seed-series PATH (a saved GET /series JSON, {"series": [...]} with optional "fetched_utc") to diff the
first run against an older snapshot instead.

Load: about 240 GET /markets pages (limit 1000, mve_filter=exclude, statuses open and unopened, about 500 MB),
one GET /series (about 20 MB) and a few dozen small calls, at most 2 requests/s, with 429/5xx backoff.
Pages are processed one at a time, so memory stays near the size of the series list.

Usage: venv/bin/python -I scripts/live/market_feature_watch.py [--force] [--out-dir DIR] [--seed-series PATH]
Exit status: 0 on success or already done, 1 on failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "vendor" / "featwatch"
if str(ROOT) not in sys.path:  # predict_ngin lives at the repo root
    sys.path.append(str(ROOT))
from predict_ngin.markets.tickers import series_of as _series_of  # noqa: E402
from predict_ngin.venues.kalshi.endpoints import BASE_URL as BASE  # noqa: E402  (defined once)
USER_AGENT = "predict-ngin-featwatch/1.0 (read-only research collector)"
MIN_INTERVAL_S = 0.5  # <= 2 requests per second
MAX_RETRIES = 8
PAGE_LIMIT = 1000
SCAN_STATUSES = ("unopened", "open")
WATCH_SERIES = ("KXMLBGAMESSCALAR", "MLBGAMESSCALAR")
FOLLOWUP_DAYS = 7
MAX_NEW_SERIES_DETAIL = 300  # sample-market fetches for new series per run
TEXT_MAX = 2000
FAST_FREQ = {"fifteen_min": "fifteen_min", "hourly": "hourly"}
DONE_STATUSES = {"finalized", "settled"}

MARKET_KEEP = (
    "ticker", "event_ticker", "title", "status", "market_type", "strike_type", "settlement_bounds_type",
    "settlement_floor_dollars", "notional_value_dollars", "yes_bid_dollars", "yes_ask_dollars",
    "last_price_dollars", "volume_fp", "volume_24h_fp", "open_interest_fp", "open_time", "close_time",
    "settlement_value_dollars", "result", "price_level_structure", "price_ranges",
)
SCALAR_EXTRA = ("floor_strike", "cap_strike", "functional_strike", "custom_strike", "expiration_value")


# ----------------------------------------------------------------------------------------------- HTTP layer
class ApiError(RuntimeError):
    pass


class Api:
    """Public Kalshi REST GETs with a global rate limit and 429/5xx/network backoff."""

    def __init__(self, base: str = BASE, client: httpx.Client | None = None,
                 min_interval: float = MIN_INTERVAL_S, max_retries: int = MAX_RETRIES,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.base = base.rstrip("/")
        self.client = client or httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT})
        self.min_interval, self.max_retries = min_interval, max_retries
        self.sleep, self.clock = sleep, clock
        self._last = -1e18
        self.n_requests = 0
        self.n_retries = 0
        self.bytes = 0

    def get(self, path: str, params: dict | None = None) -> dict:
        url = path if path.startswith("http") else self.base + path
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = self.clock()
            self.n_requests += 1
            try:
                r = self.client.get(url, params=params)
            except httpx.TransportError as e:
                err, retry_after = f"{type(e).__name__}", None
            else:
                if r.status_code == 200:
                    self.bytes += len(r.content)
                    return r.json()
                if r.status_code != 429 and r.status_code < 500:
                    raise ApiError(f"GET {path} -> HTTP {r.status_code}")
                err, retry_after = f"HTTP {r.status_code}", r.headers.get("Retry-After")
            if attempt == self.max_retries:
                raise ApiError(f"GET {path} failed after {attempt + 1} attempts ({err})")
            self.n_retries += 1
            try:
                backoff = float(retry_after) if retry_after else 0.0
            except ValueError:
                backoff = 0.0
            self.sleep(max(backoff, min(60.0, 2.0 ** attempt)))
        raise AssertionError("unreachable")

    def paginate(self, path: str, key: str, params: dict | None = None) -> Iterator[dict]:
        params = dict(params or {})
        seen: set[str] = set()
        while True:
            d = self.get(path, params)
            items = d.get(key) or []
            yield from items
            cur = d.get("cursor") or ""
            if not cur or not items or cur in seen:
                return
            seen.add(cur)
            params["cursor"] = cur


# ----------------------------------------------------------------------------------------------- helpers
def num(x: Any) -> float | None:
    if x in (None, ""):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def series_of(event_ticker: str | None) -> str:
    return _series_of(event_ticker or "")  # predict_ngin.markets.tickers (step 5.5)


def is_floor(m: dict) -> bool:
    return m.get("settlement_bounds_type") not in (None, "", "default") or num(m.get("settlement_floor_dollars")) is not None


def is_scalar(m: dict) -> bool:
    return (m.get("market_type") == "scalar" or m.get("strike_type") == "functional"
            or bool(m.get("functional_strike")))


def payout_hash(m: dict) -> str:
    blob = json.dumps([m.get(k) for k in ("functional_strike", "floor_strike", "cap_strike", "notional_value_dollars",
                                          "price_ranges")], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def clip(s: Any) -> Any:
    return s[:TEXT_MAX] if isinstance(s, str) else s


def market_row(m: dict, scalar: bool = False) -> dict:
    row = {k: m.get(k) for k in MARKET_KEEP if k in m}
    row["series_ticker"] = series_of(m.get("event_ticker"))
    if scalar:
        row.update({k: m.get(k) for k in SCALAR_EXTRA if k in m})
        row["rules_primary"] = clip(m.get("rules_primary"))
        row["rules_secondary"] = clip(m.get("rules_secondary"))
        row["payout_hash"] = payout_hash(m)
    return row


def series_flags(s: dict) -> list[str]:
    flags = []
    freq = (s.get("frequency") or "").lower()
    if freq in FAST_FREQ:
        flags.append(FAST_FREQ[freq])
    text = f"{s.get('ticker', '')} {s.get('title', '')}".lower()
    if freq not in FAST_FREQ and ("15 min" in text or "15-min" in text or "15min" in text):
        flags.append("fifteen_min?")
    if s.get("ticker", "").upper().endswith("PERP") or "perpetual" in text:
        flags.append("perpetual")
    if "scalar" in text or "payout shape" in text:
        flags.append("scalar")
    return flags


def compact_series(s: dict) -> dict:
    return {
        "category": s.get("category"), "fee_type": s.get("fee_type"), "fee_multiplier": s.get("fee_multiplier"),
        "frequency": s.get("frequency"),
    }


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def last_before(history: list, day: str) -> list | None:
    prev = [h for h in history if h[0] < day]
    return prev[-1] if prev else None


def peak_rss_mb() -> float | None:
    try:
        import resource  # POSIX only
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)  # Linux: KiB
    except Exception:
        return None


# ----------------------------------------------------------------------------------------------- run
def run(api: Api, out_dir: Path, today: date, force: bool = False, seed_series: Path | None = None,
        watch_series: tuple[str, ...] = WATCH_SERIES, log: Callable[[str], None] = print) -> dict:
    """One daily run. Returns the run summary; raises on failure."""
    out_dir.mkdir(parents=True, exist_ok=True)
    day = today.isoformat()
    out_path = out_dir / f"{day}.jsonl"
    state_path = out_dir / "state.json"
    state = load_state(state_path)
    if not force and out_path.exists() and day in state.get("completed_days", []):
        log(f"featwatch {day}: already done ({out_path.name})")
        return {"skipped": True, "date": day}

    t0 = time.time()
    rec: list[dict] = []
    emit = lambda kind, **kw: rec.append({"date": day, "kind": kind, **kw})  # noqa: E731

    # ---------------- series diff
    series_state: dict = state.setdefault("series", {})
    if not series_state and seed_series is not None:
        seed = json.loads(seed_series.read_text(encoding="utf-8"))
        seed_day = (seed.get("fetched_utc") or datetime.fromtimestamp(seed_series.stat().st_mtime, timezone.utc)
                    .strftime("%Y-%m-%d"))[:10]
        for s in seed.get("series") or []:
            series_state[s["ticker"]] = {"first_seen": seed_day, "baseline": True, **compact_series(s)}
        state["seed"] = {"path": seed_series.name, "date": seed_day, "n": len(series_state)}
        log(f"seeded {len(series_state)} series from {seed_series.name} ({seed_day})")
    baseline = not series_state

    raw = api.get("/series", {"include_volume": "true"}).get("series") or []
    known_sfields = set(state.get("series_fields", []))
    cur_series: dict[str, dict] = {}
    sfields: set[str] = set()
    new_series: list[dict] = []
    for s in raw:
        sfields.update(s)
        t = s.get("ticker")
        if not t:
            continue
        c = compact_series(s)
        c["volume_fp"] = s.get("volume_fp")
        c["flags"] = series_flags(s)
        cur_series[t] = c
        old = series_state.get(t)
        if old is None or (old.get("first_seen") == day and not old.get("baseline")):
            if not baseline:
                new_series.append({
                    "series_ticker": t, "title": s.get("title"), **compact_series(s), "categories": s.get("categories"),
                    "tags": s.get("tags"), "settlement_sources": s.get("settlement_sources"),
                    "contract_url": s.get("contract_url"), "volume_fp": s.get("volume_fp"), "flags": c["flags"],
                })
        elif not baseline:
            changed = {k: [old.get(k), c[k]] for k in ("fee_type", "fee_multiplier", "frequency", "category")
                       if old.get(k) != c[k] and k in old}
            if changed:
                emit("series_changed", series_ticker=t, changes=changed)
    del raw
    if known_sfields and sfields - known_sfields:
        emit("new_series_field", fields=sorted(sfields - known_sfields))
    state["series_fields"] = sorted(known_sfields | sfields)
    removed = sorted(set(series_state) - set(cur_series))

    # sample market (rules_primary) for each new series
    for i, ns in enumerate(new_series):
        if i >= MAX_NEW_SERIES_DETAIL:
            ns["rules_primary"] = None
            ns["detail_skipped"] = True
            continue
        ms = api.get("/markets", {"series_ticker": ns["series_ticker"], "limit": 5}).get("markets") or []
        m = next((m for m in ms if m.get("rules_primary")), ms[0] if ms else {})
        ns["sample_market"] = m.get("ticker")
        ns["rules_primary"] = clip(m.get("rules_primary"))
        ns["market_type"] = m.get("market_type")
        ns["settlement_bounds_type"] = m.get("settlement_bounds_type")
        emit("new_series", **ns)

    # follow-up volume for recently new series
    for t, old in series_state.items():
        if old.get("baseline") or t not in cur_series:
            continue
        age = (today - date.fromisoformat(old["first_seen"])).days
        if 1 <= age <= FOLLOWUP_DAYS:
            emit("series_followup", series_ticker=t, day_n=age, first_seen=old["first_seen"],
                 volume_fp=cur_series[t]["volume_fp"], flags=cur_series[t]["flags"])

    for t, c in cur_series.items():
        old = series_state.get(t)
        first = old["first_seen"] if old else day
        series_state[t] = {"first_seen": first, **({"baseline": True} if (baseline or (old or {}).get("baseline"))
                                                    else {}), **{k: c[k] for k in ("category", "fee_type",
                                                                                     "fee_multiplier", "frequency")}}
    for t in removed:
        series_state.pop(t, None)

    # ---------------- market scan (floors, scalars, new fields)
    floors: dict = state.setdefault("floors", {})
    scalars: dict = state.setdefault("scalars", {})
    known_mfields = set(state.get("market_fields", []))
    mfields: set[str] = set()
    bounds_types: dict[str, int] = {}
    n_scanned = 0
    seen_today: set[str] = set()

    def handle_market(m: dict, watched: bool = False) -> None:
        t = m.get("ticker")
        if not t or t in seen_today:
            return
        fl, sc = is_floor(m), is_scalar(m) or watched
        if not (fl or sc):
            return
        seen_today.add(t)
        if fl:
            fv = num(m.get("settlement_floor_dollars"))
            ent = floors.get(t)
            if ent is None:
                ent = floors[t] = {"series": series_of(m.get("event_ticker")), "first_seen": day, "history": []}
                emit("new_floor_market", **market_row(m))
            prev = last_before(ent["history"], day)
            ent["history"] = [h for h in ent["history"] if h[0] < day] + [[day, fv]]
            ent["status"] = m.get("status")
            if prev is not None and prev[1] != fv:
                delta = None if prev[1] is None or fv is None else round(fv - prev[1], 6)
                emit("floor_change", ticker=t, series_ticker=ent["series"], prev_date=prev[0], prev_floor=prev[1],
                     floor=fv, delta=delta, direction="raise" if (delta or 0) > 0 else "lower" if (delta or 0) < 0
                     else "changed", status=m.get("status"))
            emit("floor_market", **market_row(m))
        if sc:
            ph = payout_hash(m)
            ent = scalars.get(t)
            if ent is None:
                ent = scalars[t] = {"series": series_of(m.get("event_ticker")), "first_seen": day, "payout": []}
                emit("new_scalar_market", ticker=t, series_ticker=ent["series"], watched=watched)
            prev = last_before(ent["payout"], day)
            ent["payout"] = [h for h in ent["payout"] if h[0] < day] + [[day, ph]]
            ent["status"] = m.get("status")
            if prev is not None and prev[1] != ph:
                emit("payout_change", ticker=t, series_ticker=ent["series"], prev_date=prev[0],
                     functional_strike=m.get("functional_strike"), price_ranges=m.get("price_ranges"))
            emit("scalar_market", watched=watched, **market_row(m, scalar=True))

    for status in SCAN_STATUSES:
        for m in api.paginate("/markets", "markets", {"status": status, "mve_filter": "exclude",
                                                      "limit": PAGE_LIMIT}):
            n_scanned += 1
            mfields.update(m)
            bt = str(m.get("settlement_bounds_type"))
            bounds_types[bt] = bounds_types.get(bt, 0) + 1
            handle_market(m)
        log(f"scanned {status}: {n_scanned} markets so far, {api.n_requests} requests")

    # watched (payout-curve) series: every market, any status
    watch = sorted({w for w in watch_series if w in cur_series}
                   | {t for t, c in cur_series.items() if "scalar" in c["flags"]})
    for w in watch:
        for m in api.paginate("/markets", "markets", {"series_ticker": w, "limit": PAGE_LIMIT}):
            mfields.update(m)
            handle_market(m, watched=True)

    # tracked markets that left the scan: fetch by ticker until finalized
    missing = sorted(t for t, e in {**floors, **scalars}.items()
                     if t not in seen_today and e.get("status") not in DONE_STATUSES)
    for i in range(0, len(missing), 100):
        chunk = missing[i:i + 100]
        for m in api.get("/markets", {"tickers": ",".join(chunk), "limit": 100}).get("markets") or []:
            handle_market(m, watched=m.get("ticker") in scalars and not is_scalar(m))

    if known_mfields and mfields - known_mfields:
        emit("new_market_field", fields=sorted(mfields - known_mfields))
    state["market_fields"] = sorted(known_mfields | mfields)

    # ---------------- per-series aggregates (volume / OI) for floor and scalar markets
    agg: dict[tuple[str, str], dict] = {}
    for r in rec:
        if r["kind"] in ("floor_market", "scalar_market"):
            a = agg.setdefault((r["kind"].split("_")[0], r["series_ticker"]),
                               {"n_markets": 0, "volume_fp": 0.0, "volume_24h_fp": 0.0, "open_interest_fp": 0.0})
            a["n_markets"] += 1
            for k in ("volume_fp", "volume_24h_fp", "open_interest_fp"):
                a[k] += num(r.get(k)) or 0.0
    for (feat, s), a in sorted(agg.items()):
        emit("feature_series", feature=feat, series_ticker=s, **{k: round(v, 2) if isinstance(v, float) else v
                                                                  for k, v in a.items()})

    # ---------------- perpetual futures (public /margin/markets); a failure here is recorded, not fatal
    perps_state: dict = state.setdefault("perps", {})
    perps_error = None
    n_perps = 0
    try:
        for p in api.paginate("/margin/markets", "markets", {"limit": 1000}):
            t = p.get("ticker")
            if not t:
                continue
            n_perps += 1
            row = {k: p.get(k) for k in ("ticker", "title", "asset_class", "status", "contract_size", "price",
                                         "volume", "volume_24h", "volume_24h_notional_value_dollars",
                                         "open_interest", "open_interest_notional_value_dollars")}
            if t not in perps_state:
                perps_state[t] = {"first_seen": day, **({"baseline": True} if not state.get("perps_seen") else {})}
            pe = perps_state[t]
            if pe["first_seen"] == day and not pe.get("baseline"):
                emit("new_perp", **row)
            emit("perp", **row)
        if n_perps:
            state["perps_seen"] = True
    except ApiError as e:
        perps_error = str(e)

    summary = {
        "baseline": baseline, "seed": state.get("seed"), "n_series": len(cur_series), "n_new_series": len(new_series),
        "n_removed_series": len(removed), "removed_series": removed[:200],
        "new_fast_series": [n["series_ticker"] for n in new_series if {"fifteen_min", "hourly", "fifteen_min?"}
                            & set(n["flags"])],
        "new_perpetual_series": [n["series_ticker"] for n in new_series if "perpetual" in n["flags"]],
        "n_markets_scanned": n_scanned, "settlement_bounds_types": bounds_types,
        "n_floor_markets": sum(1 for r in rec if r["kind"] == "floor_market"),
        "n_floor_series": len({s for f, s in agg if f == "floor"}),
        "n_scalar_markets": sum(1 for r in rec if r["kind"] == "scalar_market"),
        "n_scalar_series": len({s for f, s in agg if f == "scalar"}), "watched_series": watch,
        "n_floor_changes": sum(1 for r in rec if r["kind"] == "floor_change"),
        "n_perps": n_perps, "perps_error": perps_error,
        "requests": api.n_requests, "retries": api.n_retries, "mb_downloaded": round(api.bytes / 1e6, 1),
        "runtime_s": round(time.time() - t0, 1), "peak_rss_mb": peak_rss_mb(),
        "finished_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    rec.insert(0, {"date": day, "kind": "run", **summary})

    days = [d for d in state.get("completed_days", []) if d != day] + [day]
    state["completed_days"] = days[-400:]
    state["last_run"] = summary["finished_utc"]
    atomic_write(out_path, "".join(json.dumps(r, separators=(",", ":"), default=str) + "\n" for r in rec))
    atomic_write(state_path, json.dumps(state, separators=(",", ":"), sort_keys=True))
    log(f"featwatch {day}: {len(rec)} records -> {out_path}")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--force", action="store_true", help="re-run a day that is already done")
    ap.add_argument("--seed-series", type=Path, help="older GET /series JSON to diff the first run against")
    ap.add_argument("--date", help="UTC day to label the run (default today; for tests/backfill of labels only)")
    a = ap.parse_args(argv)
    if (a.out_dir / "STOP").exists():
        print("STOP file present; exiting")
        return 0
    today = date.fromisoformat(a.date) if a.date else utc_today()
    try:
        s = run(Api(), a.out_dir, today, force=a.force, seed_series=a.seed_series)
    except Exception as e:  # noqa: BLE001 - any failure must exit non-zero for systemd
        print(f"featwatch failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if not s.get("skipped"):
        print(json.dumps({k: v for k, v in s.items() if k != "removed_series"}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
