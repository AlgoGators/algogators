"""
Rule-based Kalshi <-> Polymarket matcher plus hard contract-term validation.

Text similarity alone (TF-IDF cosine >= 0.25, year-only date boost) pairs
contracts that differ in strike, date, polarity or resolution time, which
creates fake "arbitrage". This module provides:

* ``extract_contract_terms`` / ``validate_pair``: hard checks that two
  contract descriptions refer to the same proposition (same strike/threshold,
  comparator, resolution date within tolerance, polarity, resolution time and
  source). Known mismatch patterns are flagged, e.g. Polymarket crypto
  "above X on <date>" markets resolving on the 12:00 ET (noon) candle vs
  Kalshi's 5pm ET index fixing.
* ``RuleMatcher``: deterministic, family-specific matcher (Fed decisions,
  presidential election, NFL/NBA/MLB/NHL games, Super Bowl / NBA Finals
  futures), ported from the empirical scratchpad work. Ticker structure is
  used instead of free text wherever possible.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# Contract term extraction
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MON_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

_ABOVE = r"\b(above|over|greater than|more than|at least|exceed(?:s|ing)?|higher than|or above|or more|reach(?:es)?|hit(?:s)?)\b|>=|>"
_BELOW = r"\b(below|under|less than|lower than|at most|or below|or less|dip(?:s)? to|fall(?:s)? to)\b|<=|<"
_BETWEEN = r"\bbetween\b"


@dataclass
class ContractTerms:
    strike: Optional[float] = None
    strike_unit: str = ""
    comparator: Optional[str] = None  # above / below / between / None
    date: Optional[pd.Timestamp] = None  # full date if year known
    month: Optional[int] = None
    day: Optional[int] = None
    year: Optional[int] = None
    negated: bool = False
    hour_et: Optional[int] = None  # resolution hour (ET) if stated
    source: Optional[str] = None


def _parse_number(num: str, suffix: str) -> Optional[float]:
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return None
    suffix = (suffix or "").lower()
    mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "mm": 1e6, "b": 1e9, "billion": 1e9,
            "t": 1e12, "trillion": 1e12}.get(suffix, 1.0)
    return v * mult


def _extract_strike(text: str) -> Tuple[Optional[float], str]:
    t = text.lower()
    # money / suffixed / comma-grouped numbers and bps / percent
    pat = re.compile(
        r"(\$)?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(k|m|mm|b|t|thousand|million|billion|trillion|bps|basis points|%|percent)?\b(\+)?"
    )
    best: Optional[Tuple[float, str]] = None
    for m in pat.finditer(t):
        dollar, num, suffix, _plus = m.group(1), m.group(2), m.group(3), m.group(4)
        has_comma = "," in num
        if not (dollar or suffix or has_comma):
            continue
        if suffix in ("bps", "basis points"):
            return _parse_number(num, ""), "bps"
        if suffix in ("%", "percent"):
            return _parse_number(num, ""), "%"
        v = _parse_number(num, suffix or "")
        if v is None:
            continue
        if best is None:
            best = (v, "$" if dollar else "")
    return (best[0], best[1]) if best else (None, "")


def _extract_date(text: str):
    t = text.lower()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return y, mo, d
    m = re.search(_MON_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", t)
    if m:
        mo = _MONTHS[m.group(1)[:3] if m.group(1)[:4] != "sept" else "sep"]
        d = int(m.group(2))
        y = int(m.group(3)) if m.group(3) else None
        if 1 <= d <= 31:
            return y, mo, d
    m = re.search(_MON_RE + r"\s+(\d{4})", t)
    if m:
        return int(m.group(2)), _MONTHS[m.group(1)[:3]], None
    m = re.search(r"(\d{4})\s+" + _MON_RE, t)
    if m:
        return int(m.group(1)), _MONTHS[m.group(2)[:3]], None
    return None, None, None


def _extract_hour_et(text: str) -> Optional[int]:
    t = text.lower()
    if re.search(r"\bnoon\b", t):
        return 12
    if re.search(r"\bmidnight\b", t):
        return 0
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", t)
    if m:
        h = int(m.group(1)) % 12
        if m.group(3).startswith("p"):
            h += 12
        return h
    m = re.search(r"\b(\d{1,2}):(\d{2})\b(?!\s*(?:am|pm))(?:\s*(?:et|est|edt|in the et))?", t)
    if m and ("et" in t or "eastern" in t):
        return int(m.group(1))
    return None


_SOURCES = ["binance", "coinbase", "cf benchmarks", "brti", "kraken", "chainlink", "pyth", "bls", "federal reserve",
            "ap ", "associated press", "fox news", "nyt"]


def _extract_source(text: str) -> Optional[str]:
    t = text.lower()
    for s in _SOURCES:
        if s in t:
            return s.strip()
    return None


def extract_contract_terms(text: str, meta_text: str = "") -> ContractTerms:
    """Extract strike/comparator/date/polarity/resolution time from a contract description."""
    text = text or ""
    full = f"{text} {meta_text or ''}"
    strike, unit = _extract_strike(text)
    low = text.lower()
    if re.search(_BETWEEN, low):
        comp = "between"
    elif re.search(_ABOVE, low):
        comp = "above"
    elif re.search(_BELOW, low):
        comp = "below"
    else:
        comp = None
    y, mo, d = _extract_date(text)
    date = None
    if y and mo and d:
        try:
            date = pd.Timestamp(year=y, month=mo, day=d)
        except ValueError:
            date = None
    negated = bool(re.search(r"\bnot\b|n't\b|\bfail(?:s)? to\b", low))
    return ContractTerms(
        strike=strike, strike_unit=unit, comparator=comp, date=date, month=mo, day=d, year=y,
        negated=negated, hour_et=_extract_hour_et(full), source=_extract_source(full),
    )


@dataclass
class PairValidation:
    ok: bool
    reasons: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)


def validate_pair(
    pm_text: str,
    k_text: str,
    pm_meta: Optional[Dict[str, str]] = None,
    k_meta: Optional[Dict[str, str]] = None,
    pm_end: Optional[pd.Timestamp] = None,
    k_end: Optional[pd.Timestamp] = None,
    date_tolerance_days: float = 1.0,
    end_tolerance_days: float = 3.0,
    strike_rel_tol: float = 1e-6,
    reject_on_source_mismatch: bool = True,
) -> PairValidation:
    """Hard checks that two contracts are the same proposition.

    Rejects on: different strike/threshold, different comparator, different
    explicit date (beyond tolerance), different month/year, opposite polarity,
    different stated resolution hour (e.g. noon vs 5pm ET), different stated
    resolution source, or contract end times further apart than
    ``end_tolerance_days`` when both are supplied.
    """
    pm_meta = pm_meta or {}
    k_meta = k_meta or {}
    a = extract_contract_terms(pm_text, " ".join(str(v) for v in pm_meta.values()))
    b = extract_contract_terms(k_text, " ".join(str(v) for v in k_meta.values()))
    reasons: List[str] = []
    flags: List[str] = []

    if a.strike is not None and b.strike is not None:
        if abs(a.strike - b.strike) > strike_rel_tol * max(abs(a.strike), abs(b.strike), 1.0):
            reasons.append(f"strike mismatch {a.strike:g} vs {b.strike:g}")
    elif (a.strike is None) != (b.strike is None) and (a.comparator in ("above", "below") or b.comparator in ("above", "below")):
        flags.append("strike_missing_one_side")

    if a.comparator and b.comparator and a.comparator != b.comparator:
        reasons.append(f"comparator mismatch {a.comparator} vs {b.comparator}")

    if a.month and b.month and a.month != b.month:
        reasons.append(f"date mismatch (month {a.month} vs {b.month})")
    elif a.day and b.day and abs(a.day - b.day) > date_tolerance_days:
        reasons.append(f"date mismatch (day {a.day} vs {b.day})")
    if a.year and b.year and a.year != b.year:
        reasons.append(f"date mismatch (year {a.year} vs {b.year})")

    if a.negated != b.negated:
        reasons.append("polarity mismatch (one contract is negated)")

    if a.hour_et is not None and b.hour_et is not None and a.hour_et != b.hour_et:
        flags.append("resolution_time_mismatch")
        reasons.append(f"resolution time mismatch ({a.hour_et}:00 vs {b.hour_et}:00 ET)")

    if a.source and b.source and a.source != b.source:
        flags.append("resolution_source_mismatch")
        if reject_on_source_mismatch:
            reasons.append(f"resolution source mismatch ({a.source} vs {b.source})")

    if pm_end is not None and k_end is not None and pd.notna(pm_end) and pd.notna(k_end):
        gap = abs((pd.Timestamp(pm_end) - pd.Timestamp(k_end)).total_seconds()) / 86400.0
        if gap > end_tolerance_days:
            reasons.append(f"resolution date mismatch ({gap:.1f} days apart)")

    # Known pattern: crypto 'above X on <date>' - Polymarket uses the 12:00 ET
    # Binance candle, Kalshi uses a 5pm ET (or 10am/other) index fixing.
    crypto = re.search(r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp)\b", f"{pm_text} {k_text}".lower())
    if crypto and (a.comparator in ("above", "below") or b.comparator in ("above", "below")):
        if "resolution_time_mismatch" not in flags and (a.hour_et is None or b.hour_et is None):
            flags.append("crypto_fixing_time_unverified")

    return PairValidation(ok=not reasons, reasons=reasons, flags=flags)


# ---------------------------------------------------------------------------
# Rule-based matcher
# ---------------------------------------------------------------------------

_MON_CODE = {"January": "JAN", "February": "FEB", "March": "MAR", "April": "APR", "May": "MAY", "June": "JUN",
             "July": "JUL", "August": "AUG", "September": "SEP", "October": "OCT", "November": "NOV",
             "December": "DEC"}
_LEAGUE_SERIES = {"nfl": "KXNFLGAME", "nba": "KXNBAGAME", "mlb": "KXMLBGAME", "nhl": "KXNHLGAME"}
DEFAULT_ELECTION_PAIRS = [("253591", "PRES-2024-DJT"), ("253597", "PRES-2024-KH"), ("253706", "POPVOTE-24-R")]


class RuleMatcher:
    """Deterministic matcher for families with structured Kalshi tickers.

    Polymarket frame needs: id, question; optional slug, end_date.
    Kalshi frame needs: ticker.
    Returns DataFrame: family, pm_id, pm_q, k_ticker, pm_yes_is_k_yes, game_date, pm_end.

    Sports games: the PM YES token is the first team in the slug
    (``nba-lal-bos-2025-03-01`` -> LAL); it maps to the Kalshi ``-LAL`` market.
    Team abbreviations that differ across venues are learnt by co-occurrence on
    the same game date (needs >= ``min_cooccur`` games).
    """

    def __init__(self, election_pairs: Optional[Iterable[Tuple[str, str]]] = None, min_cooccur: int = 3):
        self.election_pairs = list(DEFAULT_ELECTION_PAIRS if election_pairs is None else election_pairs)
        self.min_cooccur = min_cooccur

    def match(self, pm: pd.DataFrame, k: pd.DataFrame) -> pd.DataFrame:
        pm = pm.copy()
        pm["id"] = pm["id"].astype(str)
        for c in ("slug", "end_date"):
            if c not in pm.columns:
                pm[c] = None
        kset = set(k["ticker"].astype(str))
        pairs: List[Dict] = []
        pairs += self._fed(pm, kset)
        pairs += self._election(pm, kset)
        sports, nick = self._sports(pm, k)
        pairs += sports
        pairs += self._futures(pm, kset, nick)
        cols = ["family", "pm_id", "pm_q", "k_ticker", "pm_yes_is_k_yes", "game_date", "pm_end"]
        if not pairs:
            return pd.DataFrame(columns=cols)
        P = pd.DataFrame(pairs)
        for c in cols:
            if c not in P.columns:
                P[c] = None
        P = P.drop_duplicates(["pm_id"]).drop_duplicates(["k_ticker"])
        return P[cols].reset_index(drop=True)

    # -- Fed decision markets ---------------------------------------------
    @staticmethod
    def _fed(pm: pd.DataFrame, kset: set) -> List[Dict]:
        out = []
        for r in pm.itertuples():
            q = r.question or ""
            mm = re.search(r"after (?:(\d{4}) )?(\w+) (?:(\d{4}) )?meeting", q)
            if not ("Fed" in q and mm):
                continue
            yr = mm.group(1) or mm.group(3)
            mon = _MON_CODE.get(mm.group(2))
            if not (yr and mon):
                continue
            code = f"{yr[2:]}{mon}"
            if "No change" in q:
                suf = "H0"
            elif "decreases" in q and "25 bps" in q:
                suf = "C25"
            elif "decreases" in q and "50+ bps" in q:
                suf = "C26"
            elif "increases" in q and "25 bps" in q:
                suf = "H25"
            else:
                continue
            for pre in ("KXFEDDECISION", "FEDDECISION"):
                t = f"{pre}-{code}-{suf}"
                if t in kset:
                    out.append(dict(family="fed", pm_id=r.id, pm_q=q, k_ticker=t, pm_yes_is_k_yes=True,
                                    pm_end=r.end_date))
                    break
        return out

    def _election(self, pm: pd.DataFrame, kset: set) -> List[Dict]:
        q = pm.set_index("id")
        out = []
        for pid, t in self.election_pairs:
            if pid in q.index and t in kset:
                out.append(dict(family="election", pm_id=pid, pm_q=q.loc[pid, "question"], k_ticker=t,
                                pm_yes_is_k_yes=True, pm_end=q.loc[pid, "end_date"]))
        return out

    def _sports(self, pm: pd.DataFrame, k: pd.DataFrame):
        g = pm[pm["slug"].astype(str).str.match(r"^(nfl|nba|mlb|nhl)-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}$", na=False)].copy()
        if g.empty:
            return [], {}
        sp = g["slug"].str.extract(r"^(\w+?)-(\w+?)-(\w+?)-(\d{4}-\d{2}-\d{2})$")
        g[["lg", "a1", "a2", "date"]] = sp.values
        kk = k[k["ticker"].astype(str).str.match(r"^KX(NFL|NBA|MLB|NHL|NCAAF)GAME-", na=False)].copy()
        ks = kk["ticker"].str.extract(r"^(KX[A-Z]+GAME)-(\d{2}[A-Z]{3}\d{2})(?:\d{4})?([A-Z0-9]+)-([A-Z0-9]+)$")
        kk[["ser", "dcode", "combo", "team"]] = ks.values
        kk = kk.dropna(subset=["team"])
        kk["date"] = pd.to_datetime(kk["dcode"], format="%y%b%d", errors="coerce").dt.strftime("%Y-%m-%d")

        def other(combo, team):
            if combo.startswith(team):
                return combo[len(team):]
            if combo.endswith(team):
                return combo[: -len(team)]
            return None

        kk["opp"] = [other(c, t) for c, t in zip(kk["combo"], kk["team"])]
        kg = defaultdict(set)
        kteams = defaultdict(set)
        for r in kk.itertuples():
            kg[(r.ser, r.date)].add(r.team)
            kteams[r.ser].add(r.team)
        co = defaultdict(Counter)
        for r in g.itertuples():
            ser = _LEAGUE_SERIES[r.lg]
            for ab in kg.get((ser, r.date), ()):
                co[(r.lg, r.a1)][ab] += 1
                co[(r.lg, r.a2)][ab] += 1
        amap = {}
        for key, c in co.items():
            if key[1].upper() in kteams[_LEAGUE_SERIES[key[0]]]:
                amap[key] = key[1].upper()
                continue
            (ab, n), = c.most_common(1)
            tot = int(((g["lg"] == key[0]) & ((g["a1"] == key[1]) | (g["a2"] == key[1]))).sum())
            if n >= max(self.min_cooccur, 0.5 * tot):
                amap[key] = ab
        kidx = {(r.ser, r.date, r.team, r.opp): r.ticker for r in kk.itertuples()}
        out, nick = [], {}
        for r in g.itertuples():
            ser = _LEAGUE_SERIES[r.lg]
            A, B = amap.get((r.lg, r.a1)), amap.get((r.lg, r.a2))
            if not A or not B:
                continue
            t = kidx.get((ser, r.date, A, B))
            if t is None:
                continue
            out.append(dict(family="sports_" + r.lg, pm_id=r.id, pm_q=r.question, k_ticker=t,
                            pm_yes_is_k_yes=True, game_date=r.date, pm_end=r.end_date))
            if r.lg in ("nfl", "nba") and " vs. " in str(r.question):
                n1, n2 = [x.strip() for x in str(r.question).split(" vs. ")[:2]]
                nick[(r.lg, n1)] = A
                nick[(r.lg, n2)] = B
        return out, nick

    @staticmethod
    def _futures(pm: pd.DataFrame, kset: set, nick: Dict) -> List[Dict]:
        out = []
        for r in pm.itertuples():
            q = r.question or ""
            mm = re.match(r"Will the (.+) win (?:the )?(Super Bowl (\d{4})|(\d{4}) NBA Finals)\?", q)
            if not mm:
                continue
            team = mm.group(1)
            yr = mm.group(3) or mm.group(4)
            lg = "nfl" if mm.group(3) else "nba"
            ab = None
            for (l, n), a in nick.items():
                if l == lg and team.endswith(n):
                    ab = a
            if not ab:
                continue
            t = f"{'KXSB' if lg == 'nfl' else 'KXNBA'}-{yr[2:]}-{ab}"
            if t in kset:
                out.append(dict(family="futures_" + lg, pm_id=r.id, pm_q=q, k_ticker=t, pm_yes_is_k_yes=True,
                                pm_end=r.end_date))
        return out
