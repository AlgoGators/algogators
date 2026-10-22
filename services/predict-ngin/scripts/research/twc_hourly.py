"""twc: hourly temperature markets (Kalshi Weather Index era). Pre-registered in docs/TWC_HOURLY_PREREG.md.

  H1  any trading after the reading time S? (trade tape + recorder)
  H2  intra-hour index-convergence taker: decision at S-k min, info = canonical (t <= tau-300s) or pending
      (station readings with received_at <= tau), fair value from train-only empirical dI distribution.
  H3  spike/QC fade (descriptive + taker).
Inputs: data/vendor/twc/{hourly_markets.parquet, index/<city>/*.parquet, candles/*.parquet, trades/*.parquet}.
"""
from __future__ import annotations

import glob
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "data" / "vendor" / "twc"
TRAIN_END = "2026-09-30"   # pre-registered
KS = [1, 2, 3, 5, 10]
THETAS = [0.02, 0.04, 0.08]
C = 10

CITY = {"MIA": "miami", "NYC": "nyc", "CHI": "chicago", "CHIMETRO": "chicago", "LAX": "la-coastal",
        "LA": "la-coastal", "DAL": "dfw", "DFW": "dfw", "HOU": "houston", "PHIL": "phl-delaware-valley",
        "PHL": "phl-delaware-valley", "SEA": "puget-sound", "SFO": "sf-bay", "SF": "sf-bay", "BOS": "greater-boston",
        "DET": "southeast-michigan", "KC": "kansas-city", "MIN": "minneapolis-st-paul", "MSP": "minneapolis-st-paul"}


def city_of(series: str, rules: str) -> str | None:
    core = series.replace("KXTEMP", "")
    for suf in ("HS", "H"):
        if core.endswith(suf):
            core = core[: -len(suf)]
            break
    if core in CITY:
        return CITY[core]
    r = (rules or "").lower()
    for k, c in [("miami", "miami"), ("new york", "nyc"), ("chicago", "chicago"), ("los angeles", "la-coastal"),
                 ("dallas", "dfw"), ("houston", "houston"), ("philadelphia", "phl-delaware-valley"),
                 ("seattle", "puget-sound"), ("puget", "puget-sound"), ("san francisco", "sf-bay"), ("bay area", "sf-bay"),
                 ("boston", "greater-boston"), ("detroit", "southeast-michigan"), ("michigan", "southeast-michigan"),
                 ("kansas city", "kansas-city"), ("minneapolis", "minneapolis-st-paul")]:
        if k in r:
            return c
    return None


def fee(p, c=C):
    return math.ceil(round(0.07 * c * p * (1 - p) * 100, 6)) / 100 / c   # $ per contract


def num(s):
    return pd.to_numeric(s, errors="coerce")


def load_markets():
    h = pd.read_parquet(OUT / "hourly_markets.parquet")
    h["era"] = np.where(h.rules_primary.str.contains("Synoptic|Weather Index", na=False), "KWI",
                        np.where(h.rules_primary.str.contains("Weather Company", na=False), "TWC", "other"))
    h["city"] = [city_of(s, r) for s, r in zip(h.series, h.rules_primary)]
    h["S"] = pd.to_datetime(h.close_time, utc=True)
    h["date"] = h.S.dt.tz_convert("America/New_York").dt.date.astype(str)
    h["xv"] = num(h.expiration_value)
    h["fl"] = num(h.floor_strike)
    h["cp"] = num(h.cap_strike)
    h["win"] = (h.result == "yes").astype(float)
    h.loc[~h.result.isin(["yes", "no"]), "win"] = np.nan
    return h


def yes_if(st, fl, cp, v):
    if st == "greater":
        return v > fl
    if st == "less":
        return v < cp
    if st == "between":
        return (v >= fl) & (v <= cp)
    return np.nan


def load_index(city):
    fs = sorted(glob.glob(str(OUT / "index" / city / "2*.parquet")))
    if not fs:
        return None, None
    d = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    d = d[d.t.notna()]
    cal = json.load(open(OUT / "index" / city / "calibrations.json"))["calibrations"]
    return d, cal


def calib_at(cal, t_ms):
    cur = None
    for c in sorted(cal, key=lambda x: x["effective_at_ms"]):
        if c["effective_at_ms"] <= t_ms:
            cur = c
    return cur


def canonical(d):
    c = d.drop_duplicates("t")[["t", "v", "status"]]
    c = c[c.status.isin(["normal", "degraded"]) & c.v.notna()].sort_values("t")
    return c


def pending_series(d, cal):
    """Per (minute t, station): offset-adjusted temp in degC and receipt time. Index_P(t | tau) is computed at
    query time from the stations received by tau (>= 4 members)."""
    s = d[d.station_id.notna() & d.temp_f.notna() & d.received_at_ms.notna()].copy()
    s["x_c"] = (s.temp_f - 32) / 1.8
    offs = {}
    for c in cal:
        offs[c["effective_at_ms"]] = ({x["station_id"]: x["offset_c"] for x in c["stations"]}, c["city_reference_c"])
    keys = sorted(offs)
    idx = np.searchsorted(keys, s.t.values, side="right") - 1
    s["b"] = [offs[keys[i]][0].get(st, np.nan) if i >= 0 else np.nan for i, st in zip(idx, s.station_id)]
    s["Bc"] = [offs[keys[i]][1] if i >= 0 else np.nan for i in idx]
    s["z"] = s.x_c - s.b + s.Bc
    return s[["t", "station_id", "z", "received_at_ms", "code", "x_c"]]


def h1_after_close(h):
    out = {}
    tf = sorted(glob.glob(str(OUT / "trades" / "*.parquet")))
    if tf:
        t = pd.concat([pd.read_parquet(f) for f in tf], ignore_index=True)
        t["ts"] = pd.to_datetime(t.created_time, utc=True)
        t = t.merge(h[["ticker", "S", "event_ticker"]], on="ticker", how="left")
        t["after"] = t.ts > t.S
        ev = t.groupby("event_ticker").agg(n=("ts", "size"), after=("after", "sum"),
                                          last_min_before=("ts", lambda x: x.max()))
        out = dict(events=len(ev), prints=int(len(t)), prints_after_S=int(t.after.sum()),
                   events_with_after=int((ev.after > 0).sum()),
                   last_print_minus_S_s=(t.groupby("event_ticker").ts.max() - t.groupby("event_ticker").S.first())
                   .dt.total_seconds().describe().round(1).to_dict())
    return out


def build_dI(canon, train_mask_t, gmax=20):
    """Empirical I(t+g)-I(t) distribution per g (minutes) on train minutes only."""
    s = canon.set_index("t").v
    res = {}
    tt = canon.t.values
    tr = canon.t[train_mask_t(canon.t)].values
    sv = pd.Series(s.values, index=s.index)
    for g in range(1, gmax + 1):
        fut = sv.reindex(tr + g * 60000).values
        cur = sv.reindex(tr).values
        dd = fut - cur
        res[g] = np.sort(dd[~np.isnan(dd)])
    return res


def p_yes(dist, i_last, st, fl, cp):
    if dist is None or len(dist) < 50:
        return np.nan
    fut = i_last + dist
    if st == "greater":
        return float(np.mean(fut > fl + 0.005))
    if st == "less":
        return float(np.mean(fut < cp - 0.005))
    return float(np.mean((fut >= fl) & (fut <= cp)))



def avail_table(d, cal):
    """Per event minute t: canonical v (avail t+300 s) and the 'pending' reconstruction: raw offset-adjusted mean of
    station readings received by t+300 s, available at R(t) = receipt of the q-th member (q = ceil(0.8 N))."""
    cn = canonical(d).set_index("t")
    ps = pending_series(d, cal)
    ps = ps[ps.received_at_ms <= ps.t + 300000]
    nmem = {c["effective_at_ms"]: len(c["stations"]) for c in cal}
    keys = sorted(nmem)
    g = ps.sort_values("received_at_ms").groupby("t")
    agg = g.agg(zc=("z", "mean"), n=("z", "size"), rec=("received_at_ms", lambda x: list(x)))
    nm = np.array([nmem[keys[max(0, np.searchsorted(keys, t, side="right") - 1)]] for t in agg.index])
    q = np.ceil(0.8 * nm).astype(int)
    agg["R"] = [r[qq - 1] if len(r) >= qq else np.nan for r, qq in zip(agg.rec, q)]
    agg["vP"] = agg.zc * 1.8 + 32
    out = cn[["v"]].join(agg[["vP", "R", "n"]], how="outer")
    out["availC"] = out.index + 300000
    return out.drop(columns=[]).reset_index().rename(columns={"index": "t"})


class Known:
    """latest (t, value) known at tau, vectorised: variant C uses availC=t+300 s, variant P uses R(t)."""

    def __init__(self, at):
        c = at[at.v.notna()].sort_values("availC")
        self.cA, self.cT, self.cV = c.availC.values, c.t.values, c.v.values
        p = at[at.vP.notna() & at.R.notna()].sort_values("R")
        self.pR = p.R.values
        tmax = np.maximum.accumulate(p.t.values)
        self.pT = tmax
        self.vmap = dict(zip(p.t.values, p.vP.values))

    def get(self, tau, variant):
        if variant == "C":
            i = np.searchsorted(self.cA, tau, side="right") - 1
            return (np.nan, np.nan) if i < 0 else (self.cT[i], self.cV[i])
        i = np.searchsorted(self.pR, tau, side="right") - 1
        if i < 0:
            return np.nan, np.nan
        t = self.pT[i]
        return t, self.vmap[t]


def load_bcandles():
    fs = sorted(glob.glob(str(OUT / "bcandles" / "*.parquet")))
    c = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    for col in ["bid_c", "ask_c", "px_c", "vol", "oi"]:
        c[col] = num(c[col])
    return c


def cluster_ci(df, col, key="day", n=2000, seed=0):
    rng = np.random.default_rng(seed)
    g = df.groupby(key)[col].agg(["sum", "size"])
    if len(g) < 3:
        return (np.nan, np.nan)
    s, z = g["sum"].values, g["size"].values
    idx = rng.integers(0, len(g), size=(n, len(g)))
    m = s[idx].sum(1) / z[idx].sum(1)
    return tuple(np.round(np.percentile(m, [2.5, 97.5]) * 100, 2))


def h2(h):
    cd = load_bcandles()
    cand = {(a, int(b)): (c, d_, e) for a, b, c, d_, e in zip(cd.ticker, cd.ts, cd.bid_c, cd.ask_c, cd.vol)}
    res = []
    for city in ["miami", "nyc", "chicago", "la-coastal"]:
        d, cal = load_index(city)
        if d is None:
            print("no index for", city); continue
        at = avail_table(d, cal).sort_values("t")
        ok = at.v.notna() & at.vP.notna()
        dv = (at.vP - at.v)[ok].abs()
        trn = at.t <= pd.Timestamp(TRAIN_END + " 23:59", tz="America/New_York").timestamp() * 1000
        print(f"{city}: pending-reconstruction |vP-v|<=0.05F on {np.mean(dv[trn[ok]] <= 0.05):.3f} of train minutes; "
              f"R-t median {np.nanmedian(at.R - at.t)/1000:.0f}s p90 {np.nanpercentile((at.R - at.t).dropna(), 90)/1000:.0f}s")
        cn = at[at.v.notna()][["t", "v"]]
        dist = build_dI(cn, lambda t: t <= pd.Timestamp(TRAIN_END + " 23:59", tz="America/New_York").timestamp() * 1000,
                        gmax=25)
        mk = h[(h.city == city) & (h.era == "KWI") & h.win.notna()]
        kn = Known(at)
        for r in mk.itertuples():
            S = int(r.S.timestamp() * 1000)
            for k in KS:
                tau = S - k * 60000
                key = (r.ticker, tau // 1000)
                if key not in cand:
                    continue
                bid, ask, cvol = cand[key]
                for var in ("C", "P"):
                    t0, i0 = kn.get(tau, var)
                    if np.isnan(t0):
                        continue
                    gmin = int(round((S - t0) / 60000))
                    p = p_yes(dist.get(gmin), i0, r.strike_type, r.fl, r.cp)
                    res.append(dict(city=city, ticker=r.ticker, event=r.event_ticker, date=r.date, k=k, var=var,
                                    p=p, bid=bid, ask=ask, win=r.win, g=gmin, i0=i0, K=r.fl,
                                    vol=cvol))
    x = pd.DataFrame(res)
    x.to_parquet(OUT / "h2_decisions.parquet", index=False)
    return x


def h2_eval(x):
    x = x.copy()
    x["split"] = np.where(x.date <= TRAIN_END, "train", "test")
    x["day"] = x.date
    rows = []
    for th in THETAS:
        y = x.copy()
        fy = y.ask.map(lambda a: fee(a) if 0 < a < 1 else np.nan)
        fn = (1 - y.bid).map(lambda a: fee(a) if 0 < a < 1 else np.nan)
        ey = y.p - y.ask - fy
        en = (1 - y.p) - (1 - y.bid) - fn
        buy_y = (ey >= th) & (y.ask > 0) & (y.ask < 1)
        buy_n = (en >= th) & (y.bid > 0) & (y.bid < 1) & ~buy_y
        y["pnl"] = np.where(buy_y, y.win - y.ask - fy, np.where(buy_n, (1 - y.win) - (1 - y.bid) - fn, np.nan))
        y["px"] = np.where(buy_y, y.ask, np.where(buy_n, 1 - y.bid, np.nan))
        t = y[y.pnl.notna()]
        for (k, var, sp), g in t.groupby(["k", "var", "split"]):
            rows.append(dict(theta=th, k=k, var=var, split=sp, n=len(g), days=g.day.nunique(),
                             c_per=round(g.pnl.mean() * 100, 2), ci=cluster_ci(g, "pnl"),
                             avg_px=round(g.px.mean(), 3), usd_10lots=round(g.pnl.sum() * C, 1)))
    r = pd.DataFrame(rows)
    return r


def calib_check(x):
    """Market vs model calibration per k (mid-price), Brier, train/test."""
    x = x[(x.bid > 0) & (x.ask < 1) & (x.ask > x.bid)].copy()
    x["mid"] = (x.bid + x.ask) / 2
    x["split"] = np.where(x.date <= TRAIN_END, "train", "test")
    return x.groupby(["var", "k", "split"]).apply(lambda g: pd.Series(dict(
        n=len(g), brier_mkt=((g.mid - g.win) ** 2).mean(), brier_model=((g.p - g.win) ** 2).mean(),
        spread=(g.ask - g.bid).mean()))).round(4)


def main():
    h = load_markets()
    print("== hourly markets by series/era")
    print(h.groupby(["series", "era"]).agg(n=("ticker", "size"), ev=("event_ticker", "nunique"), d0=("date", "min"),
                                            d1=("date", "max"), vol=("volume_fp", lambda x: num(x).sum()),
                                            city=("city", "first")).to_string())
    st = h[h.win.notna()]
    print("strike types:", st.strike_type.value_counts().to_dict())
    chk = [yes_if(a, b, c, v) for a, b, c, v in zip(st.strike_type, st.fl, st.cp, st.xv)]
    st = st.assign(chk=chk)
    print("result consistent with expiration_value & strike:", (st.chk.astype(float) == st.win).mean().round(4), len(st))
    print("settlement lag (settlement_ts - S) minutes:",
          ((pd.to_datetime(st.settlement_ts, utc=True) - st.S).dt.total_seconds() / 60).describe().round(1).to_dict())
    print("== H1", h1_after_close(h))

    # ---- index vs settlement
    rows = []
    for city in sorted(h.city.dropna().unique()):
        d, cal = load_index(city)
        if d is None:
            continue
        cn = canonical(d)
        sv = cn.set_index("t").v
        e = h[(h.city == city) & (h.era == "KWI") & h.xv.notna()].drop_duplicates("event_ticker")
        for r in e.itertuples():
            S = int(r.S.timestamp() * 1000)
            prior = cn[(cn.t <= S) & (cn.t >= S - 3600000)]
            v = prior.v.iloc[-1] if len(prior) else np.nan
            rows.append(dict(city=city, event=r.event_ticker, S=r.S, xv=r.xv, idx=v,
                             age_min=(S - prior.t.iloc[-1]) / 60000 if len(prior) else np.nan))
    iv = pd.DataFrame(rows)
    if len(iv):
        iv["match"] = (iv.xv - iv.idx).abs() < 0.006
        print("== settlement value vs canonical index point at S (KWI era):")
        print(iv.groupby("city").agg(n=("match", "size"), match=("match", "mean"), nan=("idx", lambda x: x.isna().mean()),
                                     age=("age_min", "mean")).to_string())
        print(iv[~iv.match].head(10).to_string())
        iv.to_parquet(OUT / "hourly_index_vs_settle.parquet", index=False)
    if "--h2" in sys.argv:
        x = h2(h)
        print("== H2 decisions", len(x))
        print(calib_check(x).to_string())
        r = h2_eval(x)
        r.to_csv(OUT / "h2_results.csv", index=False)
        print(r.to_string())


if __name__ == "__main__":
    main()
