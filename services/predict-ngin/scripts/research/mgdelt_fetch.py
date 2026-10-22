"""mgdelt step 2: fetch GDELT DOC 2.0 data for the panel (resumable; cached per URL).

usage: python mgdelt_fetch.py <split> [timeline|artlist|both]
- timeline: one `timelinevolraw` query per unique (speaker, term) pair over the whole split span
  (daily bins: value = articles matching speaker AND term, norm = all articles GDELT monitored).
- artlist: one `artlist` query per (speaker, term, D) over [D-3d 00:00, D 00:00) UTC, 250 most
  recent articles (domain + seendate) -> breadth / HHI. Order is a fixed random permutation
  (seed 7), so a partial fetch is an outcome-blind random subsample.
"""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from mgdelt_common import OUT, get  # noqa: E402

SPAN = {"train": ("20241101000000", "20260202000000"), "test": ("20251125000000", "20261015000000")}


def tl_params(spk, term, split):
    a, b = SPAN[split]
    return dict(query=f"{spk} {term}", mode="timelinevolraw", format="json", startdatetime=a, enddatetime=b)


def al_params(spk, term, D):
    e = pd.Timestamp(D, tz="UTC")
    s = e - pd.Timedelta(days=3)
    return dict(query=f"{spk} {term}", mode="artlist", format="json", maxrecords="250", sort="datedesc",
                startdatetime=s.strftime("%Y%m%d%H%M%S"), enddatetime=e.strftime("%Y%m%d%H%M%S"))


def main():
    split = sys.argv[1]
    what = sys.argv[2] if len(sys.argv) > 2 else "both"
    p = pd.read_parquet(OUT + "panel.parquet")
    p = p[(p.split == split) & p.term_q.notna()]
    log = open(OUT + f"fetch_{split}.log", "a")
    if what in ("timeline", "both"):
        pairs = p[["spk_q", "term_q"]].drop_duplicates().sample(frac=1, random_state=7)
        t0 = time.time()
        for i, (s, t) in enumerate(pairs.itertuples(index=False)):
            r = get(tl_params(s, t, split))
            if i % 50 == 0:
                print(f"timeline {i}/{len(pairs)} {time.time()-t0:.0f}s ok={r is not None}", file=log, flush=True)
    if what in ("artlist", "both"):
        trip = p[["spk_q", "term_q", "Dstr"]].drop_duplicates().sample(frac=1, random_state=7)
        t0 = time.time()
        for i, (s, t, D) in enumerate(trip.itertuples(index=False)):
            r = get(al_params(s, t, D))
            if i % 50 == 0:
                print(f"artlist {i}/{len(trip)} {time.time()-t0:.0f}s ok={r is not None}", file=log, flush=True)
    print("done", what, file=log, flush=True)


if __name__ == "__main__":
    main()
