"""mgdelt step 2b (Amendment 2): speaker-and-term co-mention counts from the free raw GDELT 2.1 GKG files.

The DOC API is throttled for this IP (see the doc), so we read the static GKG files instead
(data.gdeltproject.org, no API, no rate limit). One file per hour (the :00 15-minute batch) is used,
a fixed 1-in-4 time subsample, so counts and the exposure both come from the same files.

Per GKG record (one news article, GDELT crawl time = file timestamp):
- speaker hit: speaker regex on V1 Persons + V2.1 AllNames (lower case);
- term hit (only for that speaker's market terms): whole-word regex (optional plural / possessive)
  on page title + AllNames + Persons + Organizations + Locations + Quotations (themes excluded). GKG carries no full text, so this is a proxy for "term in article".
Output per UTC day (resumable): data/vendor/mgdelt/gkg/<YYYYMMDD>.parquet with columns
  day, speaker, term_q, domain (one row per article x speaker x term hit), and
  data/vendor/mgdelt/gkg/<YYYYMMDD>.json with total docs, docs per speaker, docs per (speaker, domain) for breadth.

usage: python mgdelt_gkg.py <start YYYY-MM-DD> <end YYYY-MM-DD> [workers<=2]
       python mgdelt_gkg.py <days.csv> [workers<=2]
"""
import io
import json
import os
import re
import sys
import time
import urllib.request
import zipfile
from collections import Counter
from multiprocessing import Pool

import pandas as pd

OUT = "data/vendor/mgdelt/gkg/"
URL = "https://data.gdeltproject.org/gdeltv2/{}.gkg.csv.zip"

# speaker -> regex on persons/allnames (lower case). Fixed before any count was computed.
SPK_RX = {
    "Donald_Trump": r"\b(donald )?trump\b(?! jr)", "Zohran_Mamdani": r"\bmamdani\b", "JD_Vance": r"\b(jd |j\.d\. )?vance\b",
    "Karoline_Leavitt": r"\bleavitt\b", "Bernie_Sanders": r"\bbernie sanders\b", "Jerome_Powell": r"\b(jerome|jay) powell\b",
    "MrBeast": r"\bmr ?beast\b|\bjimmy donaldson\b", "Gavin_Newsom": r"\bnewsom\b", "Keir_Starmer": r"\bstarmer\b",
    "Taylor_Swift": r"\btaylor swift\b", "Kamala_Harris": r"\bkamala\b", "Scott_Bessent": r"\bbessent\b",
    "Kathy_Hochul": r"\bhochul\b", "Joe_Biden": r"\bbiden\b", "Tim_Walz": r"\bwalz\b", "Jensen_Huang": r"\bjensen huang\b",
    "Melania_Trump": r"\bmelania\b", "Sam_Altman": r"\bsam altman\b", "Pete_Hegseth": r"\bhegseth\b",
    "Alexandria_Ocasio-Cortez": r"\bocasio-?cortez\b", "Jamie_Dimon": r"\bdimon\b", "Volodymyr_Zelenskyy": r"\bzelensk",
    "Mark_Zuckerberg": r"\bzuckerberg\b", "Gianni_Infantino": r"\binfantino\b", "Joe_Rogan": r"\bjoe rogan\b",
    "Benjamin_Netanyahu": r"\bnetanyahu\b", "Chuck_Schumer": r"\bschumer\b", "Marco_Rubio": r"\brubio\b",
    "Christopher_Waller": r"\bchristopher waller\b", "Mike_Johnson": r"\bmike johnson\b", "Stephen_Miran": r"\bmiran\b",
    "Howard_Lutnick": r"\blutnick\b",
}
SPK_C = {k: re.compile(v) for k, v in SPK_RX.items()}
TITLE_RX = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)


def term_rx(alts):
    parts = [re.escape(a.lower()) + r"(?:'s|s|es)?" for a in alts]
    return re.compile(r"(?<![\w])(?:" + "|".join(parts) + r")(?![\w])")


def load_terms():
    p = pd.read_parquet("data/vendor/mgdelt/panel.parquet", columns=["speaker", "term_q", "alts"])
    p = p.dropna(subset=["term_q"]).drop_duplicates(["speaker", "term_q"])
    T = {}
    for r in p.itertuples():
        T.setdefault(r.speaker, []).append((r.term_q, term_rx(r.alts.split(" / "))))
    return T


TERMS = None


def blob_of(f):
    m = TITLE_RX.search(f[26]) if len(f) > 26 else None
    title = m.group(1) if m else ""
    names = re.sub(r",\d+", "", f[23]) if len(f) > 23 else ""
    quotes = f[22] if len(f) > 22 else ""
    locs = " ".join(x.split("#")[1] for x in f[9].split(";") if "#" in x)
    # Themes are NOT used: GKG theme codes carry taxonomy prefixes (TAX_, WB_, EPU_, SOC_) that
    # falsely match words such as "Tax" (found on a pre-outcome sanity check of 2026-01-22).
    return " | ".join([title, names, f[11], f[13], locs, quotes]).lower()


def fetch(stamp):
    for k in range(5):
        try:
            with urllib.request.urlopen(URL.format(stamp), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(5 * (k + 1))
        except Exception:
            time.sleep(5 * (k + 1))
    return None


def do_day(day):
    global TERMS
    if TERMS is None:
        TERMS = load_terms()
    tag = day.strftime("%Y%m%d")
    if os.path.exists(OUT + tag + ".json"):
        return tag, "cached"
    rows, total, nfiles = [], 0, 0
    spk_docs, spk_dom = Counter(), Counter()
    for h in range(24):
        stamp = f"{tag}{h:02d}0000"
        z = fetch(stamp)
        if z is None:
            continue
        try:
            zf = zipfile.ZipFile(io.BytesIO(z))
            txt = zf.read(zf.namelist()[0]).decode("utf-8", "replace")
        except Exception:
            continue
        nfiles += 1
        for line in txt.split("\n"):
            f = line.split("\t")
            if len(f) < 24:
                continue
            total += 1
            pn = (f[11] + " | " + f[23]).lower()
            hits = [s for s, c in SPK_C.items() if c.search(pn)]
            if not hits:
                continue
            blob = None
            dom = f[3]
            for s in hits:
                spk_docs[s] += 1
                spk_dom[(s, dom)] += 1
                tl = TERMS.get(s)
                if not tl:
                    continue
                if blob is None:
                    blob = blob_of(f)
                for tq, rx in tl:
                    if rx.search(blob):
                        rows.append((tag, s, tq, dom, h))
    pd.DataFrame(rows, columns=["day", "speaker", "term_q", "domain", "hour"]).to_parquet(OUT + tag + ".parquet",
                                                                                         index=False)
    meta = dict(day=tag, total=total, nfiles=nfiles, spk_docs=dict(spk_docs),
                spk_dom=[[s, d, n] for (s, d), n in spk_dom.items()])
    with open(OUT + tag + ".json", "w") as fh:
        json.dump(meta, fh)
    return tag, f"files={nfiles} docs={total} hits={len(rows)}"


def main():
    os.makedirs(OUT, exist_ok=True)
    if sys.argv[1].endswith(".csv"):  # list of needed days (union of [D-59, D-1] over decision days)
        days = sorted(pd.to_datetime(pd.read_csv(sys.argv[1]).iloc[:, 0]))[::-1]
        w = min(int(sys.argv[2]) if len(sys.argv) > 2 else 2, 2)
    else:
        a, b = pd.Timestamp(sys.argv[1]), pd.Timestamp(sys.argv[2])
        w = min(int(sys.argv[3]) if len(sys.argv) > 3 else 2, 2)
        days = list(pd.date_range(a, b, freq="D"))[::-1]  # newest first: most decision days are late
    log = open(OUT + "run.log", "a")
    t0 = time.time()
    with Pool(w) as pool:
        for i, (tag, msg) in enumerate(pool.imap_unordered(do_day, days)):
            print(f"{tag} {msg} [{i+1}/{len(days)} {time.time()-t0:.0f}s]", file=log, flush=True)


if __name__ == "__main__":
    main()
