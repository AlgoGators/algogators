"""Check the release_timestamp anchor: cross-correlate audio of two independent live streams of the same event.

If both VODs start at their YouTube actualStartTime, then lag (seconds into B where A's chunk appears) should equal
(release_A + tA) - release_B - tA ... i.e. implied_wall_A(t) - implied_wall_B(t') = 0. Reports the disagreement.
Output: data/vendor/asr/anchor_check.csv
"""
from __future__ import annotations

import json
import wave

import numpy as np
import pandas as pd

from asr_common import ANCHOR_CHECK, AUDIO, OUT

FS = 1000  # envelope sample rate


def env(path):
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
        sr = w.getframerate()
    # 4 kHz band-limited samples -> abs -> 1 kHz mean envelope (speech amplitude modulation is codec-robust)
    k = sr // FS
    x = np.abs(x[: len(x) // k * k]).reshape(-1, k).mean(1)
    return x - x.mean()


def xcorr_lag(a_chunk, b):
    n = len(b) + len(a_chunk)
    nf = 1 << (n - 1).bit_length()
    c = np.fft.irfft(np.fft.rfft(b, nf) * np.conj(np.fft.rfft(a_chunk, nf)), nf)[: len(b) - len(a_chunk)]
    i = int(np.argmax(c))
    srt = np.sort(c)[::-1]
    return i / FS, float(c[i] / (np.std(c) + 1e-9))


def main():
    rows = []
    for ev, vid in ANCHOR_CHECK.items():
        ma = json.loads((AUDIO / f"{ev}.json").read_text()); mb = json.loads((AUDIO / f"{ev}__check.json").read_text())
        a = env(AUDIO / f"{ev}.wav"); b = env(AUDIO / f"{ev}__check.wav")
        for t0 in (600, 1200, 1800):  # 60 s chunks of A at these offsets
            ch = a[t0 * FS:(t0 + 60) * FS]
            lag, z = xcorr_lag(ch, b)
            wall_a = ma["release_timestamp"] + t0
            wall_b = mb["release_timestamp"] + lag
            rows.append(dict(event=ev, a=ma["channel"], b=mb["channel"], tA=t0, tB=lag, z=round(z, 1),
                             anchor_disagree_s=round(wall_a - wall_b, 1)))
    r = pd.DataFrame(rows)
    r.to_csv(OUT / "anchor_check.csv", index=False)
    print(r.to_string())


if __name__ == "__main__":
    main()
