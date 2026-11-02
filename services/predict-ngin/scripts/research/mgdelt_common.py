"""mgdelt: shared helpers for the GDELT DOC 2.0 news-attention study of Kalshi mention markets.

Research only. Read-only public GDELT API (free). Polite: >= GAP seconds between requests,
exponential back-off on the "Please limit requests" reply. Every response is cached on disk
(keyed by the request URL), so every script that uses this is resumable.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

OUT = "data/vendor/mgdelt/"
CACHE = OUT + "raw/"
API = "https://api.gdeltproject.org/api/v2/doc/doc"
GAP = float(os.environ.get("MGDELT_GAP", "20.0"))
UA = "predict-ngin-research/0.1 (read-only academic study)"
_last = [0.0]

# Speaker query strings (series -> GDELT phrase). Fixed from series titles only (no outcomes).
SPEAKER_Q = {
    "Donald_Trump": '"Trump"', "Zohran_Mamdani": '"Mamdani"', "JD_Vance": '"Vance"',
    "Karoline_Leavitt": '"Leavitt"', "Bernie_Sanders": '"Bernie Sanders"', "Jerome_Powell": '"Powell"',
    "MrBeast": '"MrBeast"', "Gavin_Newsom": '"Newsom"', "Keir_Starmer": '"Starmer"',
    "Taylor_Swift": '"Taylor Swift"', "Kamala_Harris": '"Kamala Harris"', "Scott_Bessent": '"Bessent"',
    "Kathy_Hochul": '"Hochul"', "Joe_Biden": '"Biden"', "Tim_Walz": '"Walz"', "Jensen_Huang": '"Jensen Huang"',
    "Melania_Trump": '"Melania"', "Sam_Altman": '"Sam Altman"', "Pete_Hegseth": '"Hegseth"',
    "Alexandria_Ocasio-Cortez": '"Ocasio-Cortez"', "Jamie_Dimon": '"Dimon"', "Volodymyr_Zelenskyy": '"Zelensky"',
    "Mark_Zuckerberg": '"Zuckerberg"', "Gianni_Infantino": '"Infantino"', "Joe_Rogan": '"Joe Rogan"',
    "Benjamin_Netanyahu": '"Netanyahu"', "Chuck_Schumer": '"Schumer"', "Marco_Rubio": '"Rubio"',
    "Christopher_Waller": '"Waller"', "Mike_Johnson": '"Speaker Johnson"', "Stephen_Miran": '"Miran"',
    "Howard_Lutnick": '"Lutnick"',
}


def _key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()


def get(params: dict, max_tries: int = 40) -> dict | None:
    """GET the DOC API with caching. Returns parsed JSON ({} for an empty result) or None on a
    permanent error (the error text is cached as {"_error": ...})."""
    os.makedirs(CACHE, exist_ok=True)
    url = API + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    fn = CACHE + _key(url) + ".json"
    if os.path.exists(fn):
        with open(fn, encoding="utf-8") as f:
            return json.load(f)
    wait = 40.0
    for _ in range(max_tries):
        dt = time.time() - _last[0]
        if dt < GAP:
            time.sleep(GAP - dt)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                txt = r.read().decode("utf-8", "replace")
        except Exception as e:  # network / 5xx
            txt = f"__EXC__ {e}"
        s = txt.strip()
        if s.startswith("Please limit requests") or s.startswith("__EXC__"):
            time.sleep(wait)
            wait = min(wait * 1.5, 180)
            continue
        if s == "" or s == "{}":
            obj = {}
        else:
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                # GDELT returns plain-text errors for bad queries (too short / too common words)
                obj = {"_error": s[:500]}
        obj["_url"] = url
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        return obj
    return None


def clean_terms(word: str) -> list[str]:
    """Market Word -> list of alternative phrases (" / " separated); count suffix like '3+ times' removed."""
    import re
    w = re.sub(r"\s*\(?\d+\+?\s*times?\)?\s*$", "", str(word)).strip()
    alts = [a.strip().strip('"') for a in w.split("/")]
    return [a for a in alts if len(a) >= 2]


def term_query(alts: list[str]) -> str:
    # GDELT rejects short quoted phrases ("Gaza", "NHS"): quote only multi-word or >= 5-char terms
    q = [f'"{a}"' if (" " in a or len(a) >= 5) else a for a in alts]
    return q[0] if len(q) == 1 else "(" + " OR ".join(q) + ")"
