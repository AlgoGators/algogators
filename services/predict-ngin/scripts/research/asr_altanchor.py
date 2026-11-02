"""Tighter audio->market bound from alternative live streams of the same event.

Any live VOD's frame at VOD time s cannot reach YouTube before release_timestamp + s (start-trimming only makes it
later). So for every stream b of the event: d_b = J_move - (release_b + s_b) is an upper bound on how far the market
sweep lags the moment that stream's audio existed on YouTube. Using the primary stream's ASR word times s_a and the
audio cross-correlation lag (s_b = s_a + k_b): d_b = d_a + [(release_a + s_a) - (release_b + s_b)].
The tightest (smallest) d_b over streams bounds the lead of ANY YouTube-fed detector:
    lead <= min_b d_b - ASR latency - viewer delay - order latency.
Output: data/vendor/asr/alt_anchor.csv
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd

from asr_anchorcheck import env, xcorr_lag, FS
from asr_common import AUDIO, OUT
from asr_fetch import fetch

ALTS = {
    "KXFEDMENTION-26JAN": ["114SlPvQ75E", "BoCyyoZUL7Q", "Orz2fe4YzSc"],
    "KXFEDMENTION-26MAR": ["-nKnDlwfeqM", "eJGg5AaVD0g", "Rcsoo0tzU9w", "rXef0jYuaYU"],
    "KXFEDMENTION-26APR": ["m8A1gyA753Y", "zYb8evyHtQ0", "qOkm6DIAbZ0", "5Ok-aNXduCY"],
    "KXFEDMENTION-26JUN": ["hPhQL_3Y3xw", "FmOfRTZLyrE", "2kI62fC3DXM"],
    "KXFEDMENTION-26JUL": ["w-70LWP6QMA", "qSwatN3r6Hs", "7CcWIjtSTWQ", "kLzUcirVvHE"],
    "KXFEDMENTION-26SEP": ["hz1_cR1A4kM", "2TQhR0qfDsU", "Cz_kkOPuJvs"],
}


def main():
    sp = pd.read_csv(OUT / "spread.csv")
    sp = sp[sp.prov == "dg"].set_index("event")
    rows = []
    for ev, ids in ALTS.items():
        ma = json.loads((AUDIO / f"{ev}.json").read_text())
        a = env(AUDIO / f"{ev}.wav")
        for vid in ids:
            name = f"{ev}__alt_{vid}"
            try:
                fetch(name, vid)
            except Exception as e:  # noqa: BLE001
                rows.append(dict(event=ev, alt=vid, err=str(e)[:120]))
                print(ev, vid, "fetch failed", file=sys.stderr)
                continue
            time.sleep(2)
            mb = json.loads((AUDIO / f"{name}.json").read_text())
            b = env(AUDIO / f"{name}.wav")
            dis, zs = [], []
            for t0 in (300, 900, 1500):
                ch = a[t0 * FS:(t0 + 60) * FS]
                if len(ch) < 60 * FS or len(b) <= len(ch):
                    continue
                lag, z = xcorr_lag(ch, b)
                dis.append((ma["release_timestamp"] + t0) - (mb["release_timestamp"] + lag))
                zs.append(z)
            ok = len(dis) >= 2 and np.ptp(dis) < 2.0 and min(zs) > 8
            dis_m = float(np.median(dis)) if dis else np.nan
            rows.append(dict(event=ev, alt=vid, channel=mb.get("channel"), release=mb["release_timestamp"],
                             disagree_s=dis_m, z_min=min(zs) if zs else np.nan, consistent=ok,
                             d_primary=sp.d_med.get(ev, np.nan),
                             d_alt=(sp.d_med.get(ev, np.nan) + dis_m) if ok else np.nan))
            print(rows[-1], file=sys.stderr)
    r = pd.DataFrame(rows)
    r.to_csv(OUT / "alt_anchor.csv", index=False)
    print(r.round(2).to_string())
    print(r.groupby("event").d_alt.min().round(2))


if __name__ == "__main__":
    main()
