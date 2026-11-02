"""Paper trading page: are the paper runners alive, and what have they done?

Four tabs (each shows how to set up its source when it is missing):

0. **Performance** - pick a run: charts of its latest status (equity scenarios vs benchmark,
   P&L and cost attribution), then a P&L series from its files (a saved spec, the standard
   ``performance.jsonl``, or chosen on the page) with return, annualised return, Sharpe (with
   its standard error), Sortino, P(Sharpe > 0), max drawdown, volatility, hit rate, profit
   factor and costs as a share of gross P&L; charts of equity vs benchmark, drawdown, P&L per
   period, gross vs net (the transaction-cost drag), costs by type and rolling Sharpe; and a
   table comparing groups (rule, model, strategy ...). See :mod:`predict_ngin.viz.performance`.
1. **Runners** - one row per paper runner (``<data>/paper_trading/*/status.json`` plus folders in
   ``config/paper_monitor.local.yaml``) with last activity, age and problems (stale, last pass
   not ok, kill switch, missed boundary). Pick a runner to see its status fields: numbers as
   metrics, P&L-style dicts as tables, positions as a table. Then pick any of its journal files
   (jsonl / csv / parquet / json) to see its last rows and chart a numeric column over time,
   optionally cumulative and split by a column (e.g. ``kind`` or ``coin``).
2. **Order journal** - events written by :mod:`predict_ngin.execution` brokers (dry run, paper,
   live): counts by event, paper/live fills with running notional, request latency.
3. **Collectors** - the heartbeat file, if one is written: each service's state and data age.

Files are re-read on every rerun (they are small); tick *Auto-refresh* to rerun every minute.
"""

from __future__ import annotations

import datetime as dt
import glob
import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from predict_ngin.viz import _ui
from predict_ngin.viz import paper_sources as ps
from predict_ngin.viz import performance as perf


def _fmt_age(h) -> str:
    if h is None or pd.isna(h):
        return "-"
    return f"{h * 60:.0f} min" if h < 1 else f"{h:.1f} h" if h < 48 else f"{h / 24:.1f} d"


def _runners() -> None:
    runs = ps.discover_runs()
    if not runs:
        st.info(
            "No paper runners found. A runner shows up here when it writes "
            "`<data>/paper_trading/<name>/status.json` (see `predict_ngin.paper.runner`). Other "
            "folders can be listed in `config/paper_monitor.local.yaml` "
            "(copy `config/paper_monitor.example.yaml`)."
        )
        return
    now = dt.datetime.now(dt.timezone.utc)
    table = pd.DataFrame([ps.health(r, now) for r in runs])
    bad = int((~table["ok"]).sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Runners", len(table))
    c2.metric("Healthy", len(table) - bad)
    c3.metric("Need attention", bad)
    show = table.assign(
        age=table["age_h"].map(_fmt_age), ok=table["ok"].map({True: "✅", False: "⚠️"})
    )
    st.dataframe(
        show[["ok", "run", "mode", "age", "last_activity", "passes", "next", "problems"]],
        hide_index=True,
        width="stretch",
    )

    name = st.selectbox("Runner", [r.name for r in runs], key="paper_run")
    run = next(r for r in runs if r.name == name)
    st.caption(f"`{run.dir}`  (stale after {run.stale_hours:g} h)")
    fname, doc = ps.read_status(run)
    if doc:
        st.markdown(f"**{fname}**")
        sc = {
            k: v
            for k, v in ps.scalars(doc).items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        if sc:
            cols = st.columns(min(4, len(sc)))
            for i, (k, v) in enumerate(sc.items()):
                cols[i % len(cols)].metric(k, f"{v:,.4g}")
        nd = ps.number_dicts(doc)
        if nd:
            st.dataframe(pd.DataFrame(nd).T.rename_axis("field"), width="stretch")
        for k, t in ps.table_dicts(doc).items():
            st.markdown(f"**{k}**")
            st.dataframe(t, width="stretch")
        with st.expander("All status fields"):
            st.json(doc, expanded=False)

    files = ps.run_files(run)
    if files.empty:
        st.info("No data files in this folder yet.")
        return
    with st.expander(f"Files ({len(files)})"):
        st.dataframe(files, hide_index=True, width="stretch")
    tables = files[files["kind"].isin(["jsonl", "csv", "parquet", "json"])]
    if tables.empty:
        return
    default = next((i for i, p in enumerate(tables["path"]) if p.endswith(".jsonl")), 0)
    path = st.selectbox("File", list(tables["path"]), index=default, key=f"paper_file_{name}")
    n = st.slider("Last rows", 50, 20000, 1000, step=50, key="paper_rows")
    try:
        df = ps.read_table(run.dir / path, n)
    except Exception as e:  # a half-written or odd file should not break the page
        st.warning(f"Could not read {path}: {type(e).__name__}: {e}")
        return
    st.caption(f"{len(df):,} rows shown")
    tcol = ps.time_column(df)
    nums = ps.numeric_columns(df)
    if tcol and nums:
        c1, c2, c3 = st.columns([2, 1, 1])
        ys = c1.multiselect("Chart", nums, default=nums[:1], key=f"paper_y_{name}_{path}")
        cats = [c for c in df.columns if df[c].dtype == object and 1 < df[c].nunique() <= 20]
        split = c2.selectbox("Split by", ["-", *cats], key=f"paper_split_{name}_{path}")
        cum = c3.checkbox("Cumulative", key=f"paper_cum_{name}_{path}")
        if ys:
            d = df.sort_values(tcol)
            keys = [] if split == "-" else [split]
            long = d.melt(id_vars=[tcol, *keys], value_vars=ys, var_name="series")
            if cum:
                long["value"] = long.groupby(["series", *keys])["value"].cumsum()
            color = "series" if not keys else split
            fig = px.line(
                long,
                x=tcol,
                y="value",
                color=color,
                line_dash="series" if keys and len(ys) > 1 else None,
                markers=len(d) < 200,
            )
            fig.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, width="stretch")
    st.dataframe(df.iloc[::-1], width="stretch", height=360)


def _orders() -> None:
    days = st.slider("Days", 1, 90, 14, key="journal_days")
    df = ps.journal_frame(days=days)
    if df.empty:
        st.info(
            "No order journal yet. Brokers from `predict_ngin.execution` (dry run, paper or live) "
            "write `<data>/live/journal/YYYY-MM-DD.jsonl`; see docs/EXECUTION.md."
        )
        return
    modes = sorted(df["mode"].dropna().unique())
    pick = st.multiselect("Mode", modes, default=[m for m in modes if m != "dry_run"] or modes)
    df = df[df["mode"].isin(pick)]
    counts = ps.order_counts(df)
    st.dataframe(
        counts.pivot_table(index=["mode", "venue"], columns="event", values="n", fill_value=0),
        width="stretch",
    )
    f = ps.fills(df)
    if not f.empty:
        st.markdown("**Fills**")
        fig = px.line(f, x="ts", y="cum_notional", color="mode", line_dash="venue", markers=True)
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="$ filled")
        st.plotly_chart(fig, width="stretch")
        cols = [
            c
            for c in [
                "ts",
                "mode",
                "venue",
                "market",
                "outcome",
                "action",
                "price",
                "quantity",
                "order_id",
            ]
            if c in f.columns
        ]
        st.dataframe(f[cols].iloc[::-1], hide_index=True, width="stretch")
    if "ms" in df.columns:
        lat = df[df["ms"].notna() & df["event"].isin(["placed", "cancelled"])]
        if not lat.empty:
            st.markdown("**Request latency (ms)**")
            st.dataframe(
                lat.groupby(["mode", "venue", "event"])["ms"].describe()[["count", "50%", "max"]],
                width="stretch",
            )
    with st.expander("Last events"):
        st.dataframe(df.iloc[::-1].head(500), hide_index=True, width="stretch")


def _collectors() -> None:
    meta, svc = ps.heartbeat()
    if not meta:
        st.info("No heartbeat file (`<data>/heartbeat/status.json`).")
        return
    when = ps._parse_time(meta.get("ts"))
    age = None if when is None else (dt.datetime.now(dt.timezone.utc) - when).total_seconds() / 3600
    c1, c2 = st.columns(2)
    c1.metric("Heartbeat", "ok" if meta.get("ok") else "problems")
    c2.metric("Written", _fmt_age(age) + " ago")
    for p in meta.get("problems") or []:
        st.warning(p)
    if not svc.empty:
        if "mode" in svc.columns:
            svc = svc.sort_values(["mode", "service"], ascending=[False, True])
        st.dataframe(svc, hide_index=True, width="stretch")


# ------------------------------------------------------------------------------------ performance
@st.cache_data(ttl=60, show_spinner=False)
def _load_perf(run_dir: str, spec_json: str, stamp: float) -> pd.DataFrame:
    return perf.load(Path(run_dir), perf.Spec(**json.loads(spec_json)))


def _flat(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flat(v, key + "."))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = v
    return out


def _snapshot(run: ps.Run) -> None:
    """Charts of the latest status: equity scenarios vs benchmark, P&L and cost attribution."""
    _, doc = ps.read_status(run)
    groups = {k: _flat(v) for k, v in doc.items() if isinstance(v, dict) and _flat(v)}
    groups = {k: v for k, v in groups.items() if len(v) <= 40 and k != "positions"}
    if not groups:
        return
    st.markdown("**Latest status** (as of the runner's last pass)")
    bench = doc.get("tbill_benchmark") or doc.get("benchmark")
    cols = st.columns(min(3, len(groups)))
    for i, (k, v) in enumerate(groups.items()):
        d = pd.DataFrame({"item": list(v), "value": list(v.values())})
        fig = px.bar(d, x="value", y="item", orientation="h", title=k, text_auto=".4~s")
        if k == "equity" and isinstance(bench, (int, float)):
            fig.add_vline(x=bench, line_dash="dash", annotation_text="benchmark")
            lo, hi = min(d["value"].min(), bench), max(d["value"].max(), bench)
            fig.update_xaxes(range=[lo - 0.002 * abs(lo), hi + 0.002 * abs(hi)])
        fig.update_layout(
            height=80 + 28 * len(d),
            margin=dict(l=10, r=10, t=40, b=10),
            yaxis_title=None,
            xaxis_title=None,
        )
        cols[i % len(cols)].plotly_chart(fig, width="stretch")


def _spec_builder(run: ps.Run, default: Optional[perf.Spec]) -> Optional[perf.Spec]:
    files = ps.run_files(run, depth=3)
    tables = files[files["kind"].isin(["jsonl", "csv", "parquet", "json"])]
    if tables.empty:
        st.info("This run has no table files to compute performance from.")
        return None
    with st.expander("Series settings", expanded=default is None):
        if default is None:
            st.caption(
                "Pick the file and columns that hold P&L. Save a setting for this run under "
                "`performance:` in `config/paper_monitor.local.yaml` (see the example file), or "
                "have the runner call `predict_ngin.paper.runner.append_performance` each pass."
            )
        paths = list(tables["path"])
        globs = sorted(
            {
                str(Path(p).parent / ("*" + Path(p).suffix))
                for p in paths
                if Path(p).parent != Path(".")
            }
        )
        options = (globs + paths) if default is None else [default.file] + globs + paths
        key = f"perf_{run.name}"
        file = st.selectbox("File (or pattern)", list(dict.fromkeys(options)), key=key + "_file")
        sample = sorted(glob.glob(str(run.dir / file)))
        if not sample:
            st.warning("No file matches.")
            return None
        head = ps.read_table(Path(sample[-1]), 2000, max_level=None)
        cols = list(head.columns)
        nums = ps.numeric_columns(head)
        times = [
            c
            for c in cols
            if c in ps.TIME_COLUMNS
            or "ts" in c.lower()
            or "time" in c.lower()
            or c.lower().endswith("day")
        ]
        d = default if default is not None and default.file == file else None
        kinds = ["pnl", "cum_pnl", "equity"]
        labels = {"pnl": "P&L per row", "cum_pnl": "cumulative P&L", "equity": "equity level"}
        c1, c2, c3 = st.columns(3)
        tcol = c1.multiselect(
            "Time column(s)",
            cols,
            default=[c for c in (d.time if d else times[:1]) if c in cols],
            key=key + "_t",
        )
        kind = c2.radio(
            "Values are",
            kinds,
            index=kinds.index(d.kind if d else "pnl"),
            format_func=labels.get,
            key=key + "_k",
        )
        pnl = c3.multiselect(
            "P&L / equity column(s)",
            nums,
            default=[c for c in (d.pnl if d else []) if c in nums],
            key=key + "_p",
        )
        c1, c2, c3 = st.columns(3)
        costs = c1.multiselect(
            "Cost columns",
            nums,
            default=[c for c in (d.costs if d else perf.guess_costs(nums)) if c in nums],
            key=key + "_c",
        )
        inside = c2.checkbox(
            "P&L is already net of costs", value=d.costs_in_pnl if d else True, key=key + "_ci"
        )
        cats = [c for c in cols if head[c].dtype == object and 1 < head[c].nunique() <= 60]
        gi = cats.index(d.group) + 1 if d and d.group in cats else 0
        group = c3.selectbox("Compare by", ["-", *cats], index=gi, key=key + "_g")
        where = dict(d.where) if d else {}
        fcol = st.selectbox("Keep only rows where", ["-", *cats], key=key + "_fc")
        if fcol != "-":
            vals = sorted(head[fcol].dropna().astype(str).unique())
            where[fcol] = st.multiselect("...equals", vals, default=vals[:1], key=key + "_fv")
        if not tcol or not pnl:
            st.info("Pick a time column and at least one P&L column.")
            return None
    return perf.Spec(
        file=file,
        time=tcol,
        pnl=pnl,
        costs=costs,
        kind=kind,
        costs_in_pnl=inside,
        where=where,
        group=None if group == "-" else group,
        capital=d.capital if d else None,
        benchmark=d.benchmark if d else None,
        costs_cumulative=d.costs_cumulative if d else False,
    )


def _pct(x) -> str:
    return "-" if x is None or not np.isfinite(x) else f"{x:+.2%}"


def _num(x, f: str = "{:.2f}") -> str:
    return "-" if x is None or not np.isfinite(x) else f.format(x)


def _performance() -> None:
    runs = ps.discover_runs()
    if not runs:
        st.info("No paper runners found (see the Runners tab).")
        return
    name = st.selectbox("Run", [r.name for r in runs], key="perf_run")
    run = next(r for r in runs if r.name == name)
    _snapshot(run)
    cfg = run.perf_spec()
    default = perf.Spec.from_config(cfg) if cfg else perf.standard_spec(run.dir)
    spec = _spec_builder(run, default)
    if spec is None:
        return
    _, doc = ps.read_status(run)
    cap0 = spec.capital or doc.get("start_capital") or doc.get("capital")
    c1, c2, c3 = st.columns(3)
    capital = c1.number_input(
        "Capital ($, for returns)",
        min_value=0.0,
        value=float(cap0 or 1000.0),
        step=100.0,
        key=f"perf_cap_{name}",
    )
    bucket = c2.radio(
        "Period",
        ["h", "D", "W"],
        index=1,
        horizontal=True,
        format_func={"h": "hour", "D": "day", "W": "week"}.get,
        key=f"perf_bucket_{name}",
    )
    spec.capital = capital or None
    stamp = max((p.stat().st_mtime for p in run.dir.glob(spec.file)), default=0.0)
    df = _load_perf(str(run.dir), json.dumps(vars(spec), default=str), stamp)
    if df.empty:
        st.warning("No rows with a usable time in the chosen file(s).")
        return
    gval = "All"
    if spec.group and spec.group in df.columns:
        groups = ["All", *df[spec.group].astype(str).value_counts().index[:200]]
        gval = c3.selectbox(spec.group, groups, key=f"perf_gv_{name}")
    sub = df if gval == "All" else df[df[spec.group].astype(str) == gval]
    per = perf.period_table(sub, spec, bucket)
    m = perf.metrics(per, spec.capital, bucket, spec.costs)
    if not m:
        st.warning("Nothing to measure.")
        return
    if m["periods"] < 30:
        st.warning(
            f"Only {m['periods']} periods: Sharpe, Sortino and P(Sharpe > 0) are noise at this "
            f"length (the Sharpe standard error is {m['sharpe_se']:.1f})."
        )
    r1 = st.columns(6)
    r1[0].metric("Net P&L", _ui.fmt_usd(m["net_pnl"]))
    r1[1].metric("Return", _pct(m.get("total_return")))
    r1[2].metric("Ann. return", _pct(m.get("ann_return")))
    r1[3].metric(
        "Sharpe (ann.)", _num(m["sharpe"]), f"± {m['sharpe_se']:.2f} SE", delta_color="off"
    )
    r1[4].metric("Sortino", _num(m["sortino"]))
    r1[5].metric("P(Sharpe > 0)", _num(m["psr_gt0"], "{:.0%}"))
    r2 = st.columns(6)
    r2[0].metric(
        "Max drawdown",
        _ui.fmt_usd(m["max_drawdown"]),
        _pct(m.get("max_drawdown_pct")),
        delta_color="off",
    )
    r2[1].metric("Ann. vol", _pct(m.get("ann_vol")))
    r2[2].metric("Hit rate", _num(m["hit_rate"], "{:.0%}"))
    r2[3].metric("Profit factor", _num(m["profit_factor"]))
    share = m["costs_pct_gross"]
    r2[4].metric(
        "Costs",
        _ui.fmt_usd(m["costs"]),
        f"{share:.0%} of gross" if np.isfinite(share) else None,
        delta_color="off",
    )
    r2[5].metric("Periods", f"{m['periods']:,}", f"{m['days']:.1f} days", delta_color="off")

    lay = dict(margin=dict(l=10, r=10, t=40, b=10), height=320)
    eq = per[["equity"]].copy()
    if "benchmark" in per.columns and per["benchmark"].notna().any():
        eq["benchmark"] = per["benchmark"].ffill()
    fig = px.line(eq, title="Equity", markers=len(eq) < 60)
    fig.update_layout(**lay, legend_title=None, yaxis_title="$")
    st.plotly_chart(fig, width="stretch")
    c1, c2 = st.columns(2)
    dd = (per["equity"] - per["equity"].cummax()).rename("drawdown")
    fig = px.area(dd, title="Drawdown ($)")
    fig.update_layout(**lay, showlegend=False, yaxis_title="$")
    c1.plotly_chart(fig, width="stretch")
    bars = per["net"].rename("net").reset_index()
    bars["up"] = bars["net"] >= 0
    fig = px.bar(
        bars,
        x="period",
        y="net",
        title="Net P&L per period",
        color="up",
        color_discrete_map={True: "#2a9d8f", False: "#e76f51"},
    )
    fig.update_layout(**lay, showlegend=False, yaxis_title="$")
    c2.plotly_chart(fig, width="stretch")
    c1, c2 = st.columns(2)
    drag = pd.DataFrame({"gross": per["gross"].cumsum(), "net": per["net"].cumsum()})
    cost_cols = [c for c in spec.costs if c in per.columns]
    if cost_cols:
        drag["costs"] = -per[cost_cols].sum(axis=1).cumsum()
    fig = px.line(drag, title="Cumulative gross vs net (transaction-cost drag)")
    fig.update_layout(**lay, legend_title=None, yaxis_title="$")
    c1.plotly_chart(fig, width="stretch")
    if cost_cols:
        cb = pd.DataFrame({"cost": cost_cols, "usd": [per[c].sum() for c in cost_cols]})
        fig = px.bar(
            cb, x="usd", y="cost", orientation="h", title="Costs by type", text_auto=".3~s"
        )
        fig.update_layout(**lay, yaxis_title=None)
    else:
        fig = px.histogram(per["net"], nbins=40, title="Distribution of period P&L")
        fig.update_layout(**lay, showlegend=False)
    c2.plotly_chart(fig, width="stretch")
    if len(per) >= 20:
        w = max(10, min(60, len(per) // 4))
        base = per["equity"].shift(1).fillna(spec.capital or 0.0)
        r = per["net"] / base.where(base > 0) if spec.capital else per["net"]
        roll = r.rolling(w).mean() / r.rolling(w).std() * np.sqrt(perf.PERIODS[bucket])
        fig = px.line(roll.rename("rolling Sharpe"), title=f"Rolling Sharpe ({w} periods)")
        fig.update_layout(**lay, showlegend=False)
        st.plotly_chart(fig, width="stretch")
    if spec.group and gval == "All":
        cmp_ = perf.compare(df, spec, bucket)
        if not cmp_.empty:
            st.markdown(f"**By {spec.group}**")
            st.dataframe(cmp_, hide_index=True, width="stretch")
    with st.expander("All metrics"):
        st.dataframe(pd.Series(m, name="value").to_frame(), width="stretch")
    with st.expander("Period table"):
        st.dataframe(per.iloc[::-1], width="stretch")


def _body() -> None:
    t0, t1, t2, t3 = st.tabs(["Performance", "Runners", "Order journal", "Collectors"])
    with t0:
        _performance()
    with t1:
        _runners()
    with t2:
        _orders()
    with t3:
        _collectors()


def render() -> None:
    st.header("Paper trading")
    auto = st.checkbox("Auto-refresh every minute", key="paper_auto")
    if auto:
        st.fragment(run_every=60)(_body)()
    else:
        _body()
