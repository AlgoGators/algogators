"""Contract grammar for Kalshi mention markets -> token-sequence matchers.

Rules text (KXFEDMENTION / KXEARNINGSMENTION*): "The exact phrase/word, or a plural or possessive form of the
phrase/word, must be used. Grammatical/tense inflections are otherwise not included." yes_sub_title lists
alternatives separated by " / " and an optional count requirement "(N+ times)".
"""
from __future__ import annotations

import re

STOP_TITLES = {"event does not qualify"}


def norm_tok(w: str) -> str:
    w = w.lower().replace("’", "'")
    w = re.sub(r"[^a-z0-9'+&]", "", w)
    return w.strip("'")


def split_tokens(text: str) -> list[str]:
    # hyphens/slashes split compounds: "six-seater" -> six seater
    return [t for t in (norm_tok(x) for x in re.split(r"[\s\-/]+", text)) if t]


def parse_title(title: str):
    """-> (list of alternative token tuples, min_count)."""
    t = title.strip()
    m = re.search(r"\((\d+)\+\s*times?\)", t, re.I)
    k = int(m.group(1)) if m else 1
    t = re.sub(r"\(.*?\)", "", t).strip()
    alts = [tuple(split_tokens(a)) for a in t.split(" / ") if a.strip()]
    return [a for a in alts if a], k


def inflect_ok(tok: str, target: str) -> bool:
    """tok matches target allowing plural / possessive only."""
    if tok == target:
        return True
    for suf in ("s", "es", "'s", "s'"):
        if tok == target + suf:
            return True
    if target.endswith("y") and tok == target[:-1] + "ies":
        return True
    return False


def find_matches(tokens: list[str], alts) -> list[int]:
    """Start indices in `tokens` where any alternative matches. Last token of a phrase may carry plural/possessive.
    Also matches a one-token target split in two by ASR ("super charger") and a two-token target written closed."""
    hits = set()
    n = len(tokens)
    for alt in alts:
        L = len(alt)
        for i in range(n):
            if i + L <= n and all((inflect_ok(tokens[i + j], alt[j]) if j == L - 1 else tokens[i + j] == alt[j])
                                  for j in range(L)):
                hits.add(i)
            if L == 1 and i + 1 < n and inflect_ok(tokens[i] + tokens[i + 1], alt[0]):
                hits.add(i)
            if L == 2 and inflect_ok(tokens[i], alt[0] + alt[1]):
                hits.add(i)
    return sorted(hits)


def keyterms(titles) -> list[str]:
    out = []
    for t in titles:
        if t.lower().strip() in STOP_TITLES:
            continue
        alts, _ = parse_title(t)
        for a in alts:
            s = " ".join(a)
            if s not in out:
                out.append(s)
    return out


class Detector:
    """Incremental, causal contract-word detector shared by the offline replay and the live harness.

    feed(rw, is_final, words) with words = [(tok, start_s, end_s, speaker)], hypotheses as sent by the ASR
    (Deepgram: per-segment interim/final; AssemblyAI: cumulative turn, final at end_of_turn).
    speaker_filter=True (FOMC): only the running-majority diarized speaker (the Chair) counts.
    Returns new detections: dicts(ticker, policy, rw, s, e, spk, chair). policy 'int' fires on interim or final,
    'fin' on finals only. An occurrence within 1 s of an earlier one is the same occurrence.
    """

    def __init__(self, titles: dict, speaker_filter: bool = False):
        from collections import Counter
        self.parsed = {}
        for tk, title in titles.items():
            if title.lower().strip() in STOP_TITLES:
                continue
            alts, k = parse_title(title)
            if alts:
                self.parsed[tk] = (alts, k)
        self.sf = speaker_filter
        self.finals = []
        self.spk = Counter()
        self.occ = {pol: {t: [] for t in self.parsed} for pol in ("int", "fin")}
        self.fired = set()

    def feed(self, rw, fin, ws):
        new = []
        if fin:
            self.finals.extend(ws)
            for w in ws:
                if w[3] is not None:
                    self.spk[w[3]] += 1
        chair = self.spk.most_common(1)[0][0] if (self.sf and self.spk) else None
        tail = self.finals[-(len(ws) + 6):] if fin else self.finals[-6:] + list(ws)
        seq = [w for w in tail if not (self.sf and chair is not None and w[3] is not None and w[3] != chair)]
        toks = [w[0] for w in seq]
        for pol in (("int", "fin") if fin else ("int",)):
            for t, (alts, k) in self.parsed.items():
                if (t, pol) in self.fired:
                    continue
                o = self.occ[pol][t]
                for i in find_matches(toks, alts):
                    if all(abs(seq[i][1] - x[0]) > 1.0 for x in o):
                        o.append((seq[i][1], seq[i][2], seq[i][3]))
                if len(o) >= k:
                    s, e, spk = sorted(o)[k - 1]
                    self.fired.add((t, pol))
                    new.append(dict(ticker=t, policy=pol, rw=rw, s=s, e=e, spk=spk, chair=chair))
        return new
