"""Picklable toy strategies for tests/unit/test_runner.py (process pools need importable classes)."""
import numpy as np

from src.data.fills import causal_fills


class FillCount:
    def __init__(self, model):
        self.model = model
        self.name = f"fills_{model}"

    def on_chunk(self, ch, ctx):
        f = ctx.memo("fills", lambda: causal_fills(ch.ticker_code, ch["ts"], ch["yc"], ch["tyes"], ch["qty"]))
        f = f.by_model(self.model)
        return int(len(f)), int(f.fq.sum())

    def finalize(self, parts):
        return tuple(int(x) for x in np.sum(np.array(parts, dtype=np.int64).reshape(-1, 2), axis=0))


class Volume:
    name = "volume"

    def on_chunk(self, ch, ctx):
        return float(ch["qty"].sum())

    def finalize(self, parts):
        return sum(parts)
