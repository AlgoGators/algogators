"""Summary tables for the ASR study (reads outputs of asr_lead.py / asr_eval.run_event)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from asr_common import EVENTS, OUT
from asr_eval import MATCH_S, boot_ci, loo_center, run_event

PRICE = {"dg": 0.0077, "aai": 0.15 / 60}  # $/min list price (Deepgram nova-3 streaming; AssemblyAI streaming)


def main():
    sched = json.loads((OUT / "schedule.json").read_text())
    lat_rows, pr_rows, spread_rows = [], [], []
    for ev in EVENTS:
        for prov in ("dg", "aai"):
            r = run_event(ev, prov, sched[ev])
            if r is None:
                continue
            x, lat, tape = r
            lat_rows.append(dict(event=ev, prov=prov, n=len(lat), int_med=lat.lat_int.median(),
                                 int_p90=lat.lat_int.quantile(.9), fin_med=lat.lat_fin.median(),
                                 fin_p90=lat.lat_fin.quantile(.9)))
            for pol in ("int", "fin"):
                y = x[(x.policy == pol) & (x.title.str.lower() != "event does not qualify")]
                det = y.s.notna()
                tp = int((det & (y.y01 == 1)).sum())
                fp = int((det & (y.y01 == 0)).sum())
                fn = int((~det & (y.y01 == 1)).sum())
                pr_rows.append(dict(event=ev, fam=EVENTS[ev][1], prov=prov, policy=pol, markets=len(y), yes=tp + fn,
                                    tp=tp, fp=fp, fn=fn, fp_titles="; ".join(y[det & (y.y01 == 0)].title),
                                    fn_titles="; ".join(y[~det & (y.y01 == 1)].title)))
                if pol == "int":
                    z = y[(y.y01 == 1) & det & (y.p0 < 0.90) & y.J_move.notna()].copy()
                    z["d"] = z.J_move - z.spoken_wall
                    if len(z) >= 3:
                        res = np.array([row.d - loo_center(z.d.drop(i)) for i, row in z.iterrows()])
                        cen = loo_center(z.d)
                        spread_rows.append(dict(event=ev, prov=prov, n=len(z), d_med=z.d.median(), d_center=cen,
                                                n_matched=int(np.sum(np.abs(res) <= MATCH_S)),
                                                loo_mad=np.median(np.abs(res[np.abs(res) <= MATCH_S])), share_within2s=np.mean(np.abs(res) <= 2),
                                                asr_lat_med=z.asr_lat.median(),
                                                first_speech_minus_sched=x.first_speech_wall.iloc[0] - sched[ev]))
    L = pd.DataFrame(lat_rows)
    P = pd.DataFrame(pr_rows)
    S = pd.DataFrame(spread_rows)
    L.to_csv(OUT / "latency_summary.csv", index=False)
    P.to_csv(OUT / "precision_recall.csv", index=False)
    S.to_csv(OUT / "spread.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 80)
    print(L.round(2).to_string())
    agg = P.groupby(["fam", "prov", "policy"])[["markets", "yes", "tp", "fp", "fn"]].sum()
    agg["precision"] = agg.tp / (agg.tp + agg.fp)
    agg["recall"] = agg.tp / agg.yes
    print(agg.round(3).to_string())
    print(P[["event", "prov", "policy", "tp", "fp", "fn", "fp_titles", "fn_titles"]].to_string())
    print(S.round(2).to_string())
    # minutes / spend
    m = pd.read_json(OUT / "minutes_log.jsonl", lines=True)
    sp = m.groupby("provider").audio_min.sum()
    print("minutes:", sp.round(1).to_dict(), "est $:", {k: round(v * PRICE[k], 2) for k, v in sp.items()})
    # grid
    gp = OUT / "lead_grid.csv"
    if gp.exists():
        G = pd.read_csv(gp)
        Wd = pd.read_csv(OUT / "lead_words.csv")
        FPd = pd.read_csv(OUT / "lead_fps.csv")
        Wd = Wd[Wd.r.abs() <= MATCH_S]
        rows = []
        for (prov, pol, Lg, V), g in G.groupby(["prov", "policy", "L", "V"]):
            w = Wd[(Wd.prov == prov) & (Wd.policy == pol)]
            fpc = FPd[(FPd.prov == prov) & (FPd.policy == pol)]
            # FP cost: each false detection lifts a ladder of the family-median true-sweep cost, lost in full
            med_cost = w.groupby("fam").prize_cost.median()
            fp_cost = fpc.fam.map(med_cost).fillna(0)
            fp_ev = fp_cost.groupby(fpc.event).sum()
            pe = g.groupby("event")[["no_pnl", "prize_pnl"]].sum()
            pe["fp"] = fp_ev.reindex(pe.index).fillna(0)
            pe["net_prize"] = pe.prize_pnl - pe.fp
            tops = g.groupby("ticker").prize_pnl.sum().sort_values(ascending=False)
            rows.append(dict(prov=prov, policy=pol, L=Lg, V=V, words=len(g), events=len(pe),
                             lead_med=g.lead.median(), win_share=g.win.mean(),
                             no_ctr=g.no_ctr.sum(), no_pnl=g.no_pnl.sum(), no_ci=tuple(np.round(boot_ci(pe.no_pnl.values))),
                             prize_pnl=g.prize_pnl.sum(), fp_cost=pe.fp.sum(), net_prize=pe.net_prize.sum(),
                             net_ci=tuple(np.round(boot_ci(pe.net_prize.values))),
                             net_per_event=pe.net_prize.mean(),
                             top1_share=(tops.iloc[0] / tops[tops > 0].sum()) if tops.gt(0).any() else np.nan))
        R = pd.DataFrame(rows)
        R.to_csv(OUT / "lead_grid_summary.csv", index=False)
        print(R.round(2).to_string())

if __name__ == "__main__":
    main()
