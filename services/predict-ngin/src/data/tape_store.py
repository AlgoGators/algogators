"""Shared, memory-mapped, ticker-indexed tape store for research backtests (research only, no orders).

Why: the research studies (mkgap_*, mrev_*, audit_flb_*, ...) each re-read the same Parquet tape through DuckDB with
`where hash(ticker) % n = k ... order by ticker, created_time`. A hash predicate cannot be pushed down, so EVERY chunk
decodes the whole file and re-sorts it (profile: ~73% of mkgap_causal wall time; the fill engine is ~1%).

What: a one-time build turns a tape into a normalised on-disk layout

    <cache>/<name>/cols/<col>.npy   one contiguous array per PRINT column, opened with np.load(mmap_mode="r")
    <cache>/<name>/tickers.parquet  one row per ticker: ticker, code, row_start, row_end, + per-ticker attributes
    <cache>/<name>/meta.json        source path/size/mtime, row count, schema, sort key  (stale -> rebuilt)

Rows are sorted by (ticker, sort_key...) and tickers are contiguous, so a ticker set is a set of row ranges (a
pre-built index: no scan, no sort). Columns are memory-mapped, so N strategies / N worker processes opening the same
store share one copy in the OS page cache instead of holding N private copies. Per-ticker constants (event, series,
close_ts, settle_ts, win, fee_type, ...) are stored once per ticker instead of once per print.

API (see docs/ENGOPT_BACKTEST_ENGINE.md):
    store = TapeStore.build_from_parquet(src, "kalshi_base", print_cols=[...], ticker_cols=[...], sort_cols=[...])
    store = TapeStore.open("kalshi_base")                        # cheap: mmaps, reads the small ticker table
    for ch in store.chunks(target_rows=4_000_000, tickers=codes): # ticker-aligned, zero-copy when contiguous
        ts = ch["ts"]; tk = ch.ticker_code                       # numpy arrays
    store.duckdb(con, "prints")                                  # optional SQL over the mmap'd columns
"""
from __future__ import annotations

import json
import math
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
FORMAT_VERSION = 1
TICKER_CODE = "_tk"  # name of the int32 ticker-code print column


def default_cache_root() -> Path:
    """$PREDICT_NGIN_TAPE_CACHE, else <repo>/data/cache/tapes (data/ is git-ignored)."""
    env = os.environ.get("PREDICT_NGIN_TAPE_CACHE")
    return Path(env) if env else ROOT / "data" / "cache" / "tapes"


def _src_fingerprint(src: Path) -> dict:
    st = src.stat()
    return {"path": src.resolve().as_posix(), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


class TapeNotSorted(ValueError):
    pass


@dataclass
class Chunk:
    """A ticker-aligned slice of the tape. Columns are numpy arrays: zero-copy views of the mmap when the chunk is
    one contiguous row range, otherwise a gathered copy of just these rows."""
    store: "TapeStore"
    codes: np.ndarray                 # ticker codes in this chunk (sorted)
    rows: slice | np.ndarray          # slice (contiguous) or int64 row index
    _cache: dict = field(default_factory=dict, repr=False)

    def __len__(self) -> int:
        r = self.rows
        return (r.stop - r.start) if isinstance(r, slice) else len(r)

    def __getitem__(self, col: str) -> np.ndarray:
        if col not in self._cache:
            self._cache[col] = self.store.col(col)[self.rows]
        return self._cache[col]

    @property
    def ticker_code(self) -> np.ndarray:
        return self[TICKER_CODE]

    def tattr(self, name: str) -> np.ndarray:
        """Per-print value of a per-ticker attribute (broadcast through the ticker code)."""
        key = "@" + name
        if key not in self._cache:
            self._cache[key] = self.store.ticker_attr(name)[self.ticker_code]
        return self._cache[key]

    def filter(self, mask: np.ndarray) -> "Chunk":
        """Row subset (keeps order). Tickers that lose all rows stay listed in `codes` (harmless)."""
        base = np.arange(self.rows.start, self.rows.stop) if isinstance(self.rows, slice) else self.rows
        c = Chunk(self.store, self.codes, base[np.asarray(mask, bool)])
        for k, v in self._cache.items():  # carry already-materialised columns
            c._cache[k] = v[mask]
        return c


class TapeStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.meta = json.loads((self.path / "meta.json").read_text())
        self.tickers = pd.read_parquet(self.path / "tickers.parquet")
        self.n_rows = int(self.meta["n_rows"])
        self._cols: dict[str, np.ndarray] = {}
        self._tattr: dict[str, np.ndarray] = {}
        self.row_start = self.tickers["row_start"].to_numpy(np.int64)
        self.row_end = self.tickers["row_end"].to_numpy(np.int64)

    # ------------------------------------------------------------------ open / build
    @classmethod
    def open(cls, name: str | Path, cache_root: Path | None = None) -> "TapeStore":
        p = Path(name)
        if not (p / "meta.json").exists():
            p = (cache_root or default_cache_root()) / str(name)
        if not (p / "meta.json").exists():
            raise FileNotFoundError(f"no tape store at {p}; build it with TapeStore.build_from_parquet")
        return cls(p)

    @classmethod
    def is_fresh(cls, name: str, src: str | Path, cache_root: Path | None = None) -> bool:
        p = (cache_root or default_cache_root()) / name / "meta.json"
        if not p.exists():
            return False
        m = json.loads(p.read_text())
        return m.get("format") == FORMAT_VERSION and m.get("source") == _src_fingerprint(Path(src))

    @classmethod
    def build_from_parquet(cls, src: str | Path, name: str, *, print_cols: Sequence[str],
                           ticker_cols: Sequence[str] = (), sort_cols: Sequence[str] = (),
                           ticker_col: str = "ticker", cache_root: Path | None = None,
                           batch_rows: int = 1 << 20, rebuild: bool = False,
                           check_ticker_constant: bool = True, presort: bool = False,
                           verbose: bool = True) -> "TapeStore":
        """Stream a Parquet tape ALREADY sorted by (ticker_col, *sort_cols) into a store. Memory: O(batch_rows).

        print_cols: fixed-width, null-free columns kept per print (numeric / bool). Strings are not allowed here:
            put per-ticker strings in ticker_cols, or dictionary-encode them upstream.
        ticker_cols: columns constant within a ticker (verified when check_ticker_constant), stored once per ticker.
        sort_cols: the within-ticker order the source is sorted by (verified; raises TapeNotSorted otherwise).
        presort: source is NOT sorted -> first write a sorted copy of the needed columns into the cache with DuckDB
            (out-of-core, 2 threads, 2.5 GB) and build from that. Ties on the sort key are broken by source row
            order, so the result is deterministic.
        Rebuilds automatically when the source size/mtime changed; otherwise returns the existing store."""
        src = Path(src)
        root = cache_root or default_cache_root()
        out = root / name
        if not rebuild and cls.is_fresh(name, src, root):
            return cls(out)
        t0 = time.time()
        read_path = src
        if presort:
            root.mkdir(parents=True, exist_ok=True)
            read_path = root / (name + ".sorted.parquet")
            sort_parquet(src, read_path, [ticker_col, *sort_cols], columns=list(dict.fromkeys(
                [ticker_col, *sort_cols, *print_cols, *ticker_cols])), temp_dir=root / "duck_tmp")
        pf = pq.ParquetFile(read_path)
        schema = pf.schema_arrow
        for c in list(print_cols) + list(ticker_cols) + list(sort_cols) + [ticker_col]:
            if schema.get_field_index(c) < 0:
                raise KeyError(f"{c} not in {src}")
        for c in print_cols:
            t = schema.field(c).type
            if not (pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_boolean(t)):
                raise TypeError(f"print column {c} has type {t}; only fixed-width numeric/bool are memory-mapped")
        n = pf.metadata.num_rows
        tmp = root / (name + ".building")
        if tmp.exists():
            shutil.rmtree(tmp)
        (tmp / "cols").mkdir(parents=True)
        mm = {c: np.lib.format.open_memmap(tmp / "cols" / f"{c}.npy", mode="w+",
                                           dtype=schema.field(c).type.to_pandas_dtype(), shape=(n,))
              for c in print_cols}
        mm[TICKER_CODE] = np.lib.format.open_memmap(tmp / "cols" / f"{TICKER_CODE}.npy", mode="w+",
                                                    dtype=np.int32, shape=(n,))
        read_cols = list(dict.fromkeys([ticker_col, *sort_cols, *print_cols, *ticker_cols]))
        tk_names: list = []; tk_start: list = []; tk_attr: dict = {c: [] for c in ticker_cols}
        prev_key = None  # (ticker, sort values...) of the previous row, python objects
        pos = 0; code = -1
        for batch in pf.iter_batches(batch_size=batch_rows, columns=read_cols):
            m = batch.num_rows
            if m == 0:
                continue
            tkr = batch.column(ticker_col)
            if tkr.null_count:
                raise ValueError("null ticker")
            # --- sortedness: lexicographic on (ticker, *sort_cols), within batch and across the boundary
            keys = [tkr] + [batch.column(c) for c in sort_cols]
            if m > 1:
                ok = _lex_nondecreasing(keys)
                if not ok:
                    raise TapeNotSorted(f"{src} is not sorted by {(ticker_col, *sort_cols)} near row {pos}")
            first = tuple(k[0].as_py() for k in keys)
            if prev_key is not None and first < prev_key:
                raise TapeNotSorted(f"{src} is not sorted by {(ticker_col, *sort_cols)} at row {pos}")
            prev_key = tuple(k[m - 1].as_py() for k in keys)
            # --- ticker runs
            chg = np.zeros(m, bool)
            if m > 1:
                chg[1:] = pc.not_equal(tkr.slice(1), tkr.slice(0, m - 1)).to_numpy(zero_copy_only=False)
            new_first = not tk_names or tkr[0].as_py() != tk_names[-1]
            chg[0] = new_first
            starts = np.nonzero(chg)[0]
            if check_ticker_constant and ticker_cols:
                for c in ticker_cols:
                    a = batch.column(c)
                    if m > 1:
                        d = _neq_null_aware(a.slice(1), a.slice(0, m - 1))
                        bad = d & ~chg[1:]
                        if bad.any():
                            i = int(np.nonzero(bad)[0][0]) + 1
                            raise ValueError(f"ticker column {c} not constant within ticker {tkr[i].as_py()}")
                    if not new_first and tk_attr[c] and not _same(a[0].as_py(), tk_attr[c][-1]):
                        raise ValueError(f"ticker column {c} not constant within ticker {tkr[0].as_py()}")
            codes = np.cumsum(chg).astype(np.int32) + code
            code = int(codes[-1])
            mm[TICKER_CODE][pos:pos + m] = codes
            sel = pa.array(starts)
            tk_names.extend(pc.take(tkr, sel).to_pylist())
            tk_start.extend((starts + pos).tolist())
            for c in ticker_cols:
                tk_attr[c].extend(pc.take(batch.column(c), sel).to_pylist())
            for c in print_cols:
                a = batch.column(c)
                if a.null_count:
                    raise ValueError(f"print column {c} has nulls; coalesce upstream or make it a ticker column")
                mm[c][pos:pos + m] = a.to_numpy(zero_copy_only=False)
            pos += m
            if verbose and (pos // batch_rows) % 20 == 0:
                print(f"  build {name}: {pos:,}/{n:,} rows, {len(tk_names):,} tickers, {time.time() - t0:.0f}s",
                      flush=True)
        assert pos == n, (pos, n)
        for c in list(mm):
            mm.pop(c).flush()  # drop every memmap reference before the rename (Windows keeps mapped files locked)
        starts = np.asarray(tk_start, np.int64)
        ends = np.append(starts[1:], n)
        tt = pd.DataFrame({"ticker": tk_names, "code": np.arange(len(tk_names), dtype=np.int32),
                           "row_start": starts, "row_end": ends, **{c: tk_attr[c] for c in ticker_cols}})
        tt.to_parquet(tmp / "tickers.parquet", index=False)
        meta = {"format": FORMAT_VERSION, "name": name, "source": _src_fingerprint(src), "n_rows": n,
                "n_tickers": len(tt), "ticker_col": ticker_col, "sort_cols": list(sort_cols),
                "print_cols": list(print_cols), "ticker_cols": list(ticker_cols),
                "dtypes": {c: str(np.dtype(schema.field(c).type.to_pandas_dtype())) for c in print_cols},
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "build_s": round(time.time() - t0, 1)}
        (tmp / "meta.json").write_text(json.dumps(meta, indent=1))
        if out.exists():
            shutil.rmtree(out)
        tmp.rename(out)
        if presort:
            del pf
            read_path.unlink(missing_ok=True)  # the store now holds everything the sorted copy had
        if verbose:
            print(f"  built {name}: {n:,} rows, {len(tt):,} tickers in {time.time() - t0:.0f}s -> {out}", flush=True)
        return cls(out)

    # ------------------------------------------------------------------ access
    def col(self, name: str) -> np.ndarray:
        """Whole-tape memory-mapped column (read-only, zero-copy)."""
        if name not in self._cols:
            self._cols[name] = np.load(self.path / "cols" / f"{name}.npy", mmap_mode="r")
        return self._cols[name]

    def ticker_attr(self, name: str) -> np.ndarray:
        """Per-ticker attribute as a numpy array indexed by ticker code."""
        if name not in self._tattr:
            self._tattr[name] = self.tickers[name].to_numpy()
        return self._tattr[name]

    def codes_where(self, mask) -> np.ndarray:
        """Ticker codes where a boolean mask (or callable on the ticker table) is true -- the predicate pushdown:
        filters on per-ticker attributes never touch the print columns."""
        if callable(mask):
            mask = mask(self.tickers)
        return self.tickers["code"].to_numpy()[np.asarray(mask, bool)]

    def codes_for(self, tickers: Iterable[str]) -> np.ndarray:
        idx = pd.Index(self.tickers["ticker"])
        c = idx.get_indexer(list(tickers))
        return np.sort(c[c >= 0]).astype(np.int32)

    def rows_for(self, codes: np.ndarray) -> np.ndarray:
        """Row index (int64, sorted) of all prints of the given ticker codes."""
        codes = np.sort(np.asarray(codes, np.int64))
        s = self.row_start[codes]; e = self.row_end[codes]
        ln = e - s
        if len(ln) == 0:
            return np.zeros(0, np.int64)
        off = np.repeat(s - np.concatenate([[0], np.cumsum(ln)[:-1]]), ln)
        return np.arange(ln.sum(), dtype=np.int64) + off

    def chunk(self, codes: np.ndarray) -> Chunk:
        codes = np.sort(np.asarray(codes, np.int64))
        if len(codes) and codes[-1] - codes[0] + 1 == len(codes):  # contiguous -> zero-copy slice
            return Chunk(self, codes, slice(int(self.row_start[codes[0]]), int(self.row_end[codes[-1]])))
        return Chunk(self, codes, self.rows_for(codes))

    def chunks(self, target_rows: int = 4_000_000, tickers: np.ndarray | None = None,
               n_chunks: int | None = None) -> Iterator[Chunk]:
        """Ticker-aligned chunks of ~target_rows prints (or exactly n_chunks of ~equal rows). A ticker is never split.
        tickers: optional ticker-code subset (from codes_where / codes_for)."""
        codes = np.arange(len(self.tickers)) if tickers is None else np.sort(np.asarray(tickers, np.int64))
        if len(codes) == 0:
            return
        ln = (self.row_end - self.row_start)[codes]
        cs = np.cumsum(ln)
        total = int(cs[-1])
        if n_chunks:
            target_rows = max(1, -(-total // n_chunks))
        bounds = np.searchsorted(cs, np.arange(target_rows, total, target_rows), side="left") + 1
        bounds = np.unique(np.concatenate([[0], bounds[bounds < len(codes)], [len(codes)]]))
        for a, b in zip(bounds[:-1], bounds[1:]):
            yield self.chunk(codes[a:b])

    def chunk_specs(self, target_rows: int = 4_000_000, tickers: np.ndarray | None = None,
                    n_chunks: int | None = None) -> list[np.ndarray]:
        """Same partition as chunks(), as lists of ticker codes (cheap to pickle to worker processes)."""
        return [c.codes for c in self.chunks(target_rows, tickers, n_chunks)]

    def duckdb(self, con, view: str = "prints", cols: Sequence[str] | None = None):
        """Register the mmap'd print columns (+ ticker code) as a DuckDB view (zero-copy for numeric columns;
        bool columns are bit-packed by Arrow, one copy). Join the ticker table registered as <view>_tickers."""
        cols = list(cols) if cols is not None else [TICKER_CODE] + list(self.meta["print_cols"])
        tbl = pa.table({c: pa.array(self.col(c)) for c in cols})
        con.register(view, tbl)
        con.register(view + "_tickers", self.tickers)
        return tbl


# ---------------------------------------------------------------------- helpers
def sort_parquet(src: Path, dst: Path, keys: Sequence[str], columns: Sequence[str] | None = None,
                 memory_limit: str = "2500MB", threads: int = 2, temp_dir: Path | None = None) -> Path:
    """Out-of-core sort of a Parquet file by `keys` with DuckDB; ties broken by source row number (stable)."""
    import duckdb
    con = duckdb.connect()
    td = Path(temp_dir) if temp_dir else dst.parent / "duck_tmp"
    td.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET memory_limit='{memory_limit}'; SET threads={threads}; SET enable_progress_bar=false; "
                f"SET preserve_insertion_order=false; SET temp_directory='{td.as_posix()}'")
    cols = ", ".join(f'"{c}"' for c in columns) if columns else "*"
    order = ", ".join(f'"{k}"' for k in keys) + ", file_row_number"
    tmp = dst.with_suffix(".tmp.parquet")
    con.execute(f"COPY (SELECT {cols} FROM read_parquet('{Path(src).as_posix()}', file_row_number=true) "
                f"ORDER BY {order}) TO '{tmp.as_posix()}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 1048576)")
    con.close()
    os.replace(tmp, dst)
    shutil.rmtree(td, ignore_errors=True)
    return dst

def _neq_null_aware(a: pa.Array, b: pa.Array) -> np.ndarray:
    ne = pc.not_equal(a, b).fill_null(False).to_numpy(zero_copy_only=False)
    na = pc.is_null(a).to_numpy(zero_copy_only=False); nb = pc.is_null(b).to_numpy(zero_copy_only=False)
    return ne | (na != nb)


def _same(x, y) -> bool:
    return (x is None and y is None) or x == y or (isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y))


def _lex_nondecreasing(keys: list) -> bool:
    """True if rows are non-decreasing in lexicographic order of the key arrays (no nulls in keys)."""
    m = len(keys[0])
    eq_prefix = np.ones(m - 1, bool)  # rows i, i+1 equal on all previous keys
    for k in keys:
        a = k.slice(1); b = k.slice(0, m - 1)
        lt = pc.less(a, b).fill_null(True).to_numpy(zero_copy_only=False)
        if (lt & eq_prefix).any():
            return False
        eq_prefix &= pc.equal(a, b).fill_null(False).to_numpy(zero_copy_only=False)
        if not eq_prefix.any():
            break
    return True
