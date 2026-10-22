"""nowc: parse DOL weekly claims press-release PDFs (real-time advance values) + national XML (SF, history).

Outputs data/vendor/nowc/dol/realtime.parquet (one row per release) and national.parquet.
"""
from __future__ import annotations
import re, subprocess, datetime as dt
from pathlib import Path
import xml.etree.ElementTree as ET
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data/vendor/nowc/dol"


def num(s):
    return float(s.replace(",", ""))


N = r"(\d{1,3}(?:,\d{3})+)"


def parse_pdf(p: Path):
    txt = subprocess.run(["pdftotext", str(p), "-"], capture_output=True, text=True, errors="ignore").stdout
    t = re.sub(r"\s+", "", txt)
    rel = dt.datetime.strptime(p.stem, "%m%d%y").date()
    week = rel - dt.timedelta(days=(rel.weekday() - 5) % 7)  # Saturday before release
    sa = re.search(r"advancefigureforseasonallyadjustedinitialclaimswas" + N, t)
    nsa = re.search(r"unadjusted,totaled" + N + r"intheweekending.{3,25}?,an?(increase|decrease)of" + N, t)
    sfx = re.search(r"seasonalfactorshadexpectedan?(increase|decrease)of" + N + r"\(or(-?[\d.]+)percent\)", t)
    i0 = t.find("previousweek'slevelwas")
    seg = t[i0:i0 + 120] if i0 >= 0 else ""
    rev = re.match(r"previousweek'slevelwasrevised(?:up|down)by" + N + r"?\d*from" + N + r"to" + N, seg)
    unrev = re.match(r"previousweek'slevelwasunrevisedat" + N, seg)
    if not sa or not nsa:
        return None
    nsa_adv = num(nsa.group(1)); ch = num(nsa.group(3)) * (1 if nsa.group(2) == "increase" else -1)
    prev = num(rev.group(3)) if rev else (num(unrev.group(1)) if unrev else None)
    prev_adv = num(rev.group(2)) if rev else prev
    sf_pct = float(sfx.group(3)) if sfx else None
    return dict(release=rel, week=week, sa_adv=num(sa.group(1)), sa_prev_rev=prev, sa_prev_adv=prev_adv,
                nsa_adv=nsa_adv, nsa_prev_rev=nsa_adv - ch, sf_exp_pct=sf_pct)


def parse_xml():
    root = ET.parse(D / "us_weekly.xml").getroot()
    rows = []
    for w in root.findall("week"):
        ic = w.find("InitialClaims")
        def g(t):
            v = (ic.find(t).text or "").replace(",", "").replace("\xa0", "").strip()
            return float(v) if v else None
        rows.append(dict(week=dt.datetime.strptime(w.find("weekEnded").text, "%m/%d/%Y").date(),
                         nsa=g("NSA"), sf=g("SF"), sa=g("SA")))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    rows = [r for p in sorted((D / "press").glob("*.pdf")) if (r := parse_pdf(p))]
    rt = pd.DataFrame(rows).sort_values("week")
    bad = sorted({p.stem for p in (D / "press").glob("*.pdf")} - {r["release"].strftime("%m%d%y") for r in rows})
    print("parsed", len(rt), "failed", bad)
    nat = parse_xml()
    rt = rt.merge(nat[["week", "sf"]], on="week", how="left")
    rt["sa_from_sf"] = rt.nsa_adv / (rt.sf / 100)
    print(rt.tail(10).to_string()); print(rt.isna().sum().to_dict())
    print("abs(sa_adv - nsa_adv/sf) max (2026):", (rt[rt.week >= dt.date(2026, 3, 1)].sa_from_sf - rt.sa_adv).abs().max())
    rt.to_parquet(D / "realtime.parquet"); nat.to_parquet(D / "national.parquet")
