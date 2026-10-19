"""nowc: fetch SEC EDGAR earnings press releases (8-K item 2.02, exhibit 99.x) for Kalshi earnings-mention companies.

Free public API, <= 4 req/s, generic contact User-Agent. Output:
  data/vendor/nowc/edgar/filings.parquet  (ticker, cik, accession, accepted (UTC), form, items, doc)
  data/vendor/nowc/edgar/text/<accession>.txt  (plain text of the EX-99.1 press release)
"""
from __future__ import annotations
import re, time, json, html, sys
from pathlib import Path
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/vendor/nowc/edgar"; (OUT / "text").mkdir(parents=True, exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "predict-ngin research research@example.org"
_last = [0.0]
ALIAS = {"ADOBE": "ADBE", "ALIBABA": "BABA", "COINBASE": "COIN", "DOLLARGENERAL": "DG", "LUCID": "LCID",
         "TSMC": "TSM", "AIR": "AIR"}


def get(url, retries=5):
    for i in range(retries):
        w = 0.26 - (time.time() - _last[0])
        if w > 0: time.sleep(w)
        _last[0] = time.time()
        try:
            r = S.get(url, timeout=30)
        except requests.RequestException:
            time.sleep(2 ** i); continue
        if r.status_code in (429, 503) or r.status_code >= 500:
            time.sleep(5 * 2 ** i); continue
        if r.status_code == 404:
            return None
        r.raise_for_status(); return r
    return None


def to_text(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


def main():
    m = pd.read_parquet(ROOT / "data/vendor/fin_D/api_earnmention_markets.parquet", columns=["event_ticker"])
    ser = sorted(set(m.event_ticker.str.split("-").str[0]))
    tick = {s: ALIAS.get(re.sub(r"^(KXEARNINGSMENTION|KXMENTIONEARN)", "", s), re.sub(r"^(KXEARNINGSMENTION|KXMENTIONEARN)", "", s)) for s in ser}
    ct = get("https://www.sec.gov/files/company_tickers.json").json()
    cik = {v["ticker"].upper(): int(v["cik_str"]) for v in ct.values()}
    fp = OUT / "filings.parquet"
    rows = pd.read_parquet(fp).to_dict("records") if fp.exists() else []
    have = {r["ticker"] for r in rows}
    missing = []
    for s, t in sorted(set(tick.items()), key=lambda x: x[1]):
        if t in have: continue
        c = cik.get(t)
        if c is None:
            missing.append(t); continue
        r = get(f"https://data.sec.gov/submissions/CIK{c:010d}.json")
        if r is None: missing.append(t); continue
        j = r.json(); rec = j["filings"]["recent"]
        df = pd.DataFrame({k: rec[k] for k in ["accessionNumber", "form", "items", "acceptanceDateTime", "primaryDocument", "filingDate"]})
        df = df[(df.filingDate >= "2023-06-01") & (((df.form == "8-K") & df["items"].str.contains("2.02")) | (df.form == "6-K"))]
        for a in df.itertuples():
            acc = a.accessionNumber.replace("-", "")
            idx = get(f"https://www.sec.gov/Archives/edgar/data/{c}/{acc}/index.json")
            if idx is None: continue
            items = idx.json()["directory"]["item"]
            names = [it["name"] for it in items if it["name"].lower().endswith((".htm", ".html", ".txt"))]
            ex = [n for n in names if re.search(r"ex[-_]?99|exhibit99|ex991|dex99|pressrelease|earnings", n.lower()) and "index" not in n.lower()]
            if not ex:
                continue
            ex = sorted(ex, key=lambda n: (0 if re.search(r"99[-_.]?1|991", n.lower()) else 1, n))[0]
            doc = get(f"https://www.sec.gov/Archives/edgar/data/{c}/{acc}/{ex}")
            if doc is None: continue
            txt = to_text(doc.text)
            if a.form == "6-K" and not re.search(r"(?i)(quarter|results|revenue)", txt[:3000]):
                continue
            (OUT / "text" / f"{acc}.txt").write_text(txt, encoding="utf-8")
            rows.append(dict(ticker=t, series=s, cik=c, accession=acc, form=a.form, items=a.items,
                             accepted=a.acceptanceDateTime, doc=ex, nchar=len(txt)))
        print(t, sum(r["ticker"] == t for r in rows), flush=True)
        pd.DataFrame(rows).to_parquet(fp)
    print("missing", missing)
    json.dump(missing, open(OUT / "missing.json", "w"))


if __name__ == "__main__":
    main()
