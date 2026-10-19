"""Canonical YES/NO token normalisation for Polymarket prints.

Why this exists
---------------
Every Polymarket market (one CTF *condition*) has exactly two outcome tokens. Both trade on the CLOB and
the exchange matches them against each other: BUY NO at ``p`` is economically the same trade as SELL YES at
``1 - p``. Most raw files on disk store each print in the units of whichever token changed hands, so a naive
"last price" series flips between ``p`` and ``1 - p`` (docs/STRATEGY_AUDIT.md D1: 79% of Politics /
97% of Economy markets interleave both tokens and 20-21% of consecutive prints jump by more than 0.50).

Convention (verified, see docs/PMFIX_POLYMARKET_TOKEN_NORMALISATION.md section 2)
---------------------------------------------------------------------------------------------
* The "YES-equivalent" token of a market is **outcome index 0** = ``clobTokenIds[0]`` = ``token1`` /
  ``answer1`` in ``data/polymarket/markets.parquet``. For Yes/No markets that is "Yes"; for named-outcome
  markets ("Up"/"Down", "Over"/"Under", team A/team B) it is the *first* listed outcome - check
  ``outcome0_label`` before interpreting it as "yes".
* ``yes_price = price`` for outcome-0 prints and ``1 - price`` for outcome-1 prints.
* ``yes_side`` flips BUY<->SELL for outcome-1 prints.
* Everything is per **condition** (market). Neg-risk / multi-outcome *events* are sets of binary markets:
  each candidate has its own YES/NO pair. Never compute ``1 - p`` across different markets of an event and
  never group an event's markets into one series - group by ``condition_id`` / ``market_id``.

Token identity priority: explicit token id resolved through market metadata (authoritative) > ``outcomeIndex``
> ``nonusdc_side`` > outcome label. Disagreements are flagged in ``token_conflict``.

Main entry points
-----------------
* :class:`TokenMap` - token id -> (condition, market, outcome index, label, neg-risk) from metadata.
* :func:`normalize_trades` - add ``yes_price`` / ``yes_side`` / ``outcome_index`` / ``token_id`` columns to a
  frame from any known source schema (Data-API ticks, on-chain processed trades, raw OrderFilled, the HF
  ``quant.parquet`` which is already YES-normalised, CLOB price history, token-keyed vendor data).
* :func:`mixing_stats` - measure the token-mixing and >0.50 consecutive-jump rates of a series.
* :func:`assert_single_token_series` / :func:`warn_legacy_reader` - guards for readers that cannot be fixed.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

__all__ = [
    "YES_INDEX",
    "EXCHANGE_ADDRESSES",
    "TokenMap",
    "parse_list_field",
    "flip_side",
    "to_yes",
    "detect_schema",
    "normalize_trades",
    "yes_price_frame",
    "ensure_yes_trades",
    "mixing_stats",
    "assert_single_token_series",
    "warn_legacy_reader",
    "LegacyPolymarketReaderWarning",
    "MixedTokenSeriesError",
]

YES_INDEX = 0  # clobTokenIds[0]; verified on 50 random resolved markets (pmfix_verify_yes_index.py)

# OrderFilled rows whose ``taker`` is an exchange contract are the taker order's own aggregate fill; the
# individual maker rows of the same match carry the same volume again (HF ``quant.parquet`` drops them).
EXCHANGE_ADDRESSES = frozenset(
    {
        "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",  # CTF Exchange
        "0xc5d563a36ae78145c45a50134d48a1215220f80a",  # NegRisk CTF Exchange
        "0xe111180000d2663c0091e4f400237545b87b996b",  # CTF Exchange V2 (since 2026-04-28)
        "0xe2222d279d744050d28e00520010520000310f59",  # NegRisk CTF Exchange V2 (since 2026-04-28)
    }
)

from predict_ngin.shared.paths import data_root

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_METADATA = (
    data_root() / "polymarket" / "markets.parquet",
    data_root() / "pmxt" / "markets" / "markets_all.parquet",
)


class LegacyPolymarketReaderWarning(UserWarning):
    """A reader that consumes raw (possibly YES/NO-mixed) Polymarket prices without normalisation."""


class MixedTokenSeriesError(ValueError):
    """A single price series contains prints of both outcome tokens without YES normalisation."""


# --------------------------------------------------------------------------------------------- parsing


def parse_list_field(value) -> list[str]:
    """Parse ``clobTokenIds`` / ``outcomes`` strings in any of the on-disk encodings.

    Handles JSON (``'["a", "b"]'``), Python reprs (``"['a', 'b']"``), the doubled-quote CSV export used in
    ``data/pmxt/markets`` (``'[""""a"""", """"b""""]'``), real lists/arrays and missing values.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, np.ndarray)):
        return [str(v).strip() for v in value]
    if isinstance(value, float) and np.isnan(value):
        return []
    s = str(value).strip()
    if not s:
        return []
    s = s.replace('"', "").replace("'", "").strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [x.strip() for x in s.split(",") if x.strip()]


def flip_side(side):
    """BUY <-> SELL (case preserved for 'buy'/'sell'); anything else passes through unchanged."""
    m = {"BUY": "SELL", "SELL": "BUY", "buy": "sell", "sell": "buy", "Buy": "Sell", "Sell": "Buy"}
    if isinstance(side, pd.Series):
        return side.map(lambda x: m.get(x, x))
    if isinstance(side, np.ndarray):
        return np.array([m.get(x, x) for x in side], dtype=object)
    return m.get(side, side)


def to_yes(price, outcome_index, side=None):
    """Vectorised YES-equivalent price (and side). ``outcome_index`` 1 -> ``1 - price`` and flipped side.

    Rows with unknown ``outcome_index`` (NaN) get NaN ``yes_price`` and ``None`` side - never a silent guess.
    """
    p = np.asarray(price, dtype=float)
    oi = np.asarray(pd.to_numeric(pd.Series(np.asarray(outcome_index, dtype=object)), errors="coerce"), dtype=float)
    yes = np.where(oi == 0, p, np.where(oi == 1, 1.0 - p, np.nan))
    if side is None:
        return yes
    s = np.asarray(side, dtype=object)
    flipped = flip_side(s)
    out_side = np.where(oi == 0, s, np.where(oi == 1, flipped, None))
    return yes, out_side


# --------------------------------------------------------------------------------------------- token map


@dataclass
class TokenMap:
    """token_id -> condition_id, market_id, outcome_index, outcome_label, outcome0_label, neg_risk, complement.

    Built from market metadata (``clobTokenIds`` order). One row per token; each condition has exactly two
    tokens. Conditions listing more or fewer than two tokens are kept out and reported in ``skipped``.
    """

    frame: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(
        columns=["condition_id", "market_id", "outcome_index", "outcome_label", "outcome0_label",
                 "neg_risk", "complement_token_id"]))
    skipped: dict = field(default_factory=dict)
    conflicts: pd.DataFrame = field(default_factory=pd.DataFrame)

    # ---- construction
    @classmethod
    def from_markets(cls, df: pd.DataFrame) -> "TokenMap":
        """Build from a market-metadata frame in any supported schema.

        Supported: ``token1/token2/answer1/answer2`` (HF markets.parquet, by_category markets),
        ``clobTokenIds/outcomes`` (Gamma API: pmxt markets, research markets_filtered.csv),
        ``yes_token/no_token`` (vendor universes, predexon pairs).
        """
        cols = set(df.columns)
        cid = _first(cols, ("condition_id", "conditionId", "pm_condition_id"))
        mid = _first(cols, ("id", "market_id"))
        nr = _first(cols, ("neg_risk", "negRisk", "m_negrisk"))
        n = len(df)
        cond = df[cid].astype(str) if cid else pd.Series([None] * n, index=df.index)
        mkt = df[mid].astype(str) if mid else pd.Series([None] * n, index=df.index)
        negr = df[nr].map(_to_bool) if nr else pd.Series([False] * n, index=df.index)
        skipped: dict = {}
        if {"token1", "token2"} <= cols:
            t0, t1 = df["token1"].astype(str), df["token2"].astype(str)
            l0 = df["answer1"].astype(str) if "answer1" in cols else pd.Series(["outcome0"] * n, index=df.index)
            l1 = df["answer2"].astype(str) if "answer2" in cols else pd.Series(["outcome1"] * n, index=df.index)
            ok = t0.str.len().gt(0) & t1.str.len().gt(0) & ~t0.isin(["None", "nan"]) & ~t1.isin(["None", "nan"])
            skipped["missing_tokens"] = int((~ok).sum())
        elif _first(cols, ("yes_token", "pm_yes_token")):
            yc = _first(cols, ("yes_token", "pm_yes_token"))
            nc = _first(cols, ("no_token", "pm_no_token"))
            t0, t1 = df[yc].astype(str), df[nc].astype(str)
            l0 = pd.Series(["Yes"] * n, index=df.index)
            l1 = pd.Series(["No"] * n, index=df.index)
            ok = t0.str.len().gt(0) & t1.str.len().gt(0) & ~t0.isin(["None", "nan"])
            skipped["missing_tokens"] = int((~ok).sum())
        elif "clobTokenIds" in cols:
            toks = df["clobTokenIds"].map(parse_list_field)
            outs = df["outcomes"].map(parse_list_field) if "outcomes" in cols else toks.map(lambda _: [])
            ntok = toks.map(len)
            ok = ntok.eq(2)
            skipped["not_two_tokens"] = int((ntok.ne(2) & ntok.gt(0)).sum())
            skipped["missing_tokens"] = int(ntok.eq(0).sum())
            t0 = toks.map(lambda x: x[0] if len(x) == 2 else "")
            t1 = toks.map(lambda x: x[1] if len(x) == 2 else "")
            l0 = outs.map(lambda x: x[0] if len(x) == 2 else "outcome0")
            l1 = outs.map(lambda x: x[1] if len(x) == 2 else "outcome1")
        else:
            raise ValueError(f"unrecognised market metadata schema: {sorted(cols)[:20]}")
        base = pd.DataFrame({"condition_id": cond, "market_id": mkt, "neg_risk": negr, "outcome0_label": l0})[ok]
        a = base.assign(token_id=t0[ok].values, outcome_index=0, outcome_label=l0[ok].values,
                        complement_token_id=t1[ok].values)
        b = base.assign(token_id=t1[ok].values, outcome_index=1, outcome_label=l1[ok].values,
                        complement_token_id=t0[ok].values)
        fr = pd.concat([a, b], ignore_index=True)
        return cls._finalise(fr, skipped)

    @classmethod
    def _finalise(cls, fr: pd.DataFrame, skipped: dict) -> "TokenMap":
        fr = fr.copy()
        fr["outcome_index"] = fr["outcome_index"].astype("int8")
        dup = fr.duplicated("token_id", keep=False)
        conflicts = pd.DataFrame()
        if dup.any():
            d = fr[dup]
            bad = d.groupby("token_id").agg(n_idx=("outcome_index", "nunique"), n_cond=("condition_id", "nunique"))
            bad = bad[(bad.n_idx > 1) | (bad.n_cond > 1)]
            if len(bad):
                conflicts = fr[fr.token_id.isin(bad.index)]
                fr = fr[~fr.token_id.isin(bad.index)]
            fr = fr.drop_duplicates("token_id", keep="first")
        fr = fr.set_index("token_id")
        return cls(frame=fr, skipped=skipped, conflicts=conflicts)

    def merge(self, other: "TokenMap") -> "TokenMap":
        """Union; tokens present in both must agree on (condition, outcome index) or they are dropped as conflicts."""
        fr = pd.concat([self.frame.reset_index(), other.frame.reset_index()], ignore_index=True)
        out = self._finalise(fr, {**self.skipped, **{f"other_{k}": v for k, v in other.skipped.items()}})
        out.conflicts = pd.concat([x for x in (self.conflicts, other.conflicts, out.conflicts) if len(x)],
                                  ignore_index=True) if any(len(x) for x in (self.conflicts, other.conflicts, out.conflicts)) else pd.DataFrame()
        return out

    @classmethod
    def load_default(cls, paths: Sequence[Path | str] = DEFAULT_METADATA) -> "TokenMap":
        """Token map from the on-disk metadata (HF markets.parquet + pmxt Gamma markets). Cached per path set."""
        return _load_default_cached(tuple(str(p) for p in paths))

    # ---- queries
    def __len__(self) -> int:
        return len(self.frame)

    def lookup(self, token_ids: Iterable) -> pd.DataFrame:
        """Rows aligned to ``token_ids`` (NaN where unknown)."""
        ids = pd.Index(pd.Series(list(token_ids), dtype=object).astype(str))
        return self.frame.reindex(ids)

    def outcome_index(self, token_ids: Iterable) -> np.ndarray:
        return self.lookup(token_ids)["outcome_index"].to_numpy(dtype=float)

    def yes_token(self, condition_id: str) -> Optional[str]:
        f = self.frame
        hit = f[(f.condition_id == condition_id) & (f.outcome_index == YES_INDEX)]
        return None if hit.empty else str(hit.index[0])

    def market_tokens(self) -> pd.DataFrame:
        """One row per condition: condition_id, market_id, token0, token1, outcome0_label, outcome1_label, neg_risk."""
        f = self.frame.reset_index()
        a = f[f.outcome_index == 0].set_index("condition_id")
        b = f[f.outcome_index == 1].set_index("condition_id")
        out = pd.DataFrame({
            "market_id": a["market_id"], "token0": a["token_id"], "token1": b["token_id"].reindex(a.index),
            "outcome0_label": a["outcome_label"], "outcome1_label": b["outcome_label"].reindex(a.index),
            "neg_risk": a["neg_risk"],
        })
        return out.reset_index()


@lru_cache(maxsize=4)
def _load_default_cached(paths: tuple) -> TokenMap:
    tm: Optional[TokenMap] = None
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        if p.suffix == ".csv":
            df = pd.read_csv(p, low_memory=False)
        else:
            import pyarrow.parquet as pq

            names = set(pq.ParquetFile(p).schema_arrow.names)
            want = [c for c in ("id", "condition_id", "conditionId", "token1", "token2", "answer1", "answer2",
                                "neg_risk", "negRisk", "clobTokenIds", "outcomes") if c in names]
            df = pd.read_parquet(p, columns=want)
        m = TokenMap.from_markets(df)
        tm = m if tm is None else tm.merge(m)
    if tm is None:
        raise FileNotFoundError(f"no Polymarket market metadata found in {paths}")
    return tm


def _first(cols, names):
    for n in names:
        if n in cols:
            return n
    return None


def _to_bool(x) -> bool:
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return False
    return str(x).strip().lower() in ("true", "1", "yes")


# --------------------------------------------------------------------------------------------- normalisation

SCHEMAS = ("quant", "onchain", "onchain_raw", "data_api", "price_history", "token_series", "dune_market_trades")


def detect_schema(df: pd.DataFrame) -> str:
    """Guess which Polymarket source a frame came from (see module doc). Raises if unknown."""
    c = set(df.columns)
    if {"maker_asset_id", "taker_asset_id", "maker_amount_filled", "taker_amount_filled"} <= c:
        return "onchain_raw"
    if {"is_taker_side", "outcome_index"} <= c:
        return "dune_market_trades"
    if "nonusdc_side" in c and {"maker_asset", "taker_asset"} <= c and "asset_id" in c:
        # HF quant.parquet: nonusdc_side relabelled to token1 for every row, but maker/taker_asset keep token2.
        ns = df["nonusdc_side"].astype(str)
        assets = pd.concat([df["maker_asset"].astype(str), df["taker_asset"].astype(str)])
        if len(df) and not ns.eq("token2").any() and assets.eq("token2").any():
            return "quant"
        return "onchain"
    if "nonusdc_side" in c and df["nonusdc_side"].notna().any():
        if "outcomeIndex" in c and df["outcomeIndex"].notna().any():
            return "mixed_generations"
        return "onchain"
    if "outcomeIndex" in c or ("asset" in c and "side" in c):
        return "data_api"
    if "outcome" in c and "price" in c and "market_id" in c:
        return "price_history"
    if _first(c, ("token_id", "asset_id", "symbol", "asset")):
        return "token_series"
    raise ValueError(f"cannot detect Polymarket schema from columns {sorted(c)[:25]}")


def normalize_trades(
    df: pd.DataFrame,
    token_map: Optional[TokenMap] = None,
    schema: str = "auto",
    price_col: str = "price",
    dune_taker_rows: str = "drop",
) -> pd.DataFrame:
    """Return a copy of ``df`` with YES-equivalent columns added.

    Added columns:
      ``token_id`` (str or None), ``outcome_index`` (float 0/1/NaN), ``outcome_label`` (where known),
      ``yes_price`` (float; NaN when token identity is unknown), ``yes_side`` (aggressor/wallet side in YES
      terms; None when unknown), ``token_source`` (how identity was resolved), ``token_conflict`` (bool: the
      source's own token label disagreed with metadata), and for on-chain sources ``is_taker_order_row``
      (the taker order's aggregate OrderFilled row; drop these to avoid double counting volume).

    ``price`` and ``side`` are left untouched so callers can audit the mapping.

    Dune ``polymarket_polygon.market_trades`` (schema ``dune_market_trades``): the TAKER rows of mint/merge matches
    (complementary YES+NO orders) carry the wrong token/side label (uma agent, 2026-10-15: fake 0.92 prints
    vs a true 0.08; maker rows hand-checked correct). By default (``dune_taker_rows="drop"``) taker rows get
    ``yes_price`` NaN and only maker rows are used; ``"keep"`` maps them anyway (unsafe, for diagnostics).
    A frame mixing two source generations (e.g. data/research/<cat>/trades.parquet) is handled row by row.
    """
    if schema == "auto":
        schema = detect_schema(df)
    out = df.copy()
    n = len(out)
    nan = np.full(n, np.nan)
    if schema == "mixed_generations":
        gen_a = out["nonusdc_side"].notna() & out.get("outcomeIndex", pd.Series(np.nan, index=out.index)).isna()
        parts = []
        if gen_a.any():
            parts.append(normalize_trades(out[gen_a], token_map, "onchain", price_col))
        if (~gen_a).any():
            parts.append(normalize_trades(out[~gen_a], token_map, "data_api", price_col))
        return pd.concat(parts).loc[out.index]

    token_id = pd.Series([None] * n, index=out.index, dtype=object)
    oi = pd.Series(nan, index=out.index)
    label = pd.Series([None] * n, index=out.index, dtype=object)
    src = pd.Series(["unknown"] * n, index=out.index, dtype=object)
    conflict = pd.Series(np.zeros(n, dtype=bool), index=out.index)
    side = pd.Series([None] * n, index=out.index, dtype=object)
    price = pd.to_numeric(out[price_col], errors="coerce") if price_col in out.columns else pd.Series(nan, index=out.index)
    already_yes = False

    if schema in ("onchain", "quant"):
        ns = out["nonusdc_side"].astype(str)
        oi_ns = pd.Series(np.where(ns.eq("token1"), 0.0, np.where(ns.eq("token2"), 1.0, np.nan)), index=out.index)
        if schema == "quant":
            # Price, nonusdc_side and directions already expressed for token1 (YES); asset_id is the real token.
            already_yes = True
            oi_ns = pd.Series(np.where(ns.eq("token1"), 0.0, np.nan), index=out.index)
            real_tok = np.where(out["maker_asset"].astype(str).eq("USDC"), out["taker_asset"].astype(str),
                                out["maker_asset"].astype(str))
            oi_real = pd.Series(np.where(real_tok == "token1", 0.0, np.where(real_tok == "token2", 1.0, np.nan)),
                                index=out.index)
        if "asset_id" in out.columns:
            token_id = out["asset_id"].astype(object).where(out["asset_id"].notna(), None)
        oi = oi_ns.copy()
        src[:] = "nonusdc_side"
        if schema == "onchain" and token_map is not None and "asset_id" in out.columns:
            meta = token_map.lookup(token_id.astype(str))
            m_oi = pd.Series(meta["outcome_index"].to_numpy(dtype=float), index=out.index)
            has = m_oi.notna()
            conflict = has & oi_ns.notna() & (m_oi != oi_ns)
            oi = m_oi.where(has, oi_ns)
            src = pd.Series(np.where(has, "token_id", np.where(oi_ns.notna(), "nonusdc_side", "unknown")),
                            index=out.index, dtype=object)
            label = meta["outcome_label"].set_axis(out.index)
        elif schema == "quant":
            oi = oi_real
            src[:] = "quant_asset"
        # aggressor side for the traded token
        taker = out["taker"].astype(str).str.lower() if "taker" in out.columns else pd.Series([""] * n, index=out.index)
        is_tor = taker.isin(EXCHANGE_ADDRESSES)
        out["is_taker_order_row"] = is_tor.to_numpy()
        if "taker_direction" in out.columns and "maker_direction" in out.columns:
            side = pd.Series(np.where(is_tor, out["maker_direction"].astype(str), out["taker_direction"].astype(str)),
                             index=out.index, dtype=object)
            side = side.where(side.isin(["BUY", "SELL"]), None)
    elif schema == "onchain_raw":
        mk = out["maker_asset_id"].astype(str)
        tk = out["taker_asset_id"].astype(str)
        maker_pays_usdc = mk.eq("0")
        token_id = pd.Series(np.where(maker_pays_usdc, tk, mk), index=out.index, dtype=object)
        usdc = np.where(maker_pays_usdc, out["maker_amount_filled"], out["taker_amount_filled"]).astype(float)
        shares = np.where(maker_pays_usdc, out["taker_amount_filled"], out["maker_amount_filled"]).astype(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            price = pd.Series(np.where(shares > 0, usdc / shares, np.nan), index=out.index)
        out["price_raw_onchain"] = price.to_numpy()
        maker_dir = pd.Series(np.where(maker_pays_usdc, "BUY", "SELL"), index=out.index)
        taker = out["taker"].astype(str).str.lower() if "taker" in out.columns else pd.Series([""] * n, index=out.index)
        is_tor = taker.isin(EXCHANGE_ADDRESSES)
        out["is_taker_order_row"] = is_tor.to_numpy()
        side = pd.Series(np.where(is_tor, maker_dir, flip_side(maker_dir)), index=out.index, dtype=object)
        if token_map is None:
            raise ValueError("onchain_raw needs a TokenMap to identify the outcome of each token id")
        meta = token_map.lookup(token_id.astype(str))
        oi = pd.Series(meta["outcome_index"].to_numpy(dtype=float), index=out.index)
        label = meta["outcome_label"].set_axis(out.index)
        src = pd.Series(np.where(oi.notna(), "token_id", "unknown"), index=out.index, dtype=object)
    elif schema == "dune_market_trades":
        oi = pd.to_numeric(out["outcome_index"], errors="coerce").astype(float)
        src = pd.Series(np.where(oi.notna(), "outcome_index", "unknown"), index=out.index, dtype=object)
        if "token_id" in out.columns:
            token_id = out["token_id"].astype(object)
        taker_row = out["is_taker_side"].map(_to_bool).astype(bool)
        out["dune_taker_row"] = taker_row.to_numpy()
        if dune_taker_rows == "drop":
            oi = oi.where(~taker_row, np.nan)
            src = src.where(~taker_row, "dune_taker_row_dropped")
        elif dune_taker_rows != "keep":
            raise ValueError("dune_taker_rows must be 'drop' or 'keep'")
        if "maker_side" in out.columns:
            ms = out["maker_side"].astype(str).str.upper()
            ms = ms.where(ms.isin(["BUY", "SELL"]), None)
            side = pd.Series(flip_side(ms.to_numpy(dtype=object)), index=out.index, dtype=object)  # aggressor
    elif schema == "data_api":
        tcol = _first(out.columns, ("asset", "asset_id", "token_id"))
        if tcol:
            token_id = out[tcol].astype(object).where(out[tcol].notna(), None)
        oi_src = pd.to_numeric(out["outcomeIndex"], errors="coerce") if "outcomeIndex" in out.columns else pd.Series(nan, index=out.index)
        oi = oi_src.copy()
        src = pd.Series(np.where(oi_src.notna(), "outcomeIndex", "unknown"), index=out.index, dtype=object)
        if "outcome" in out.columns:
            label = out["outcome"].astype(object)
        if token_map is not None and tcol:
            meta = token_map.lookup(token_id.astype(str))
            m_oi = pd.Series(meta["outcome_index"].to_numpy(dtype=float), index=out.index)
            has = m_oi.notna()
            conflict = has & oi_src.notna() & (m_oi != oi_src)
            oi = m_oi.where(has, oi_src)
            src = pd.Series(np.where(has, "token_id", src), index=out.index, dtype=object)
        if "side" in out.columns:
            side = out["side"].astype(object).where(out["side"].isin(["BUY", "SELL"]), None)
    elif schema == "price_history":
        lab = out["outcome"].astype(str)
        up = lab.str.upper()
        oi = pd.Series(np.where(up.eq("YES"), 0.0, np.where(up.eq("NO"), 1.0, np.nan)), index=out.index)
        label = lab.astype(object)
        src = pd.Series(np.where(oi.notna(), "outcome_label", "unknown"), index=out.index, dtype=object)
        if oi.isna().any() and token_map is not None and "market_id" in out.columns:
            # Named outcomes: resolve the label through the market's metadata outcome labels.
            f = token_map.frame.reset_index()
            key = f.assign(k=f.market_id.astype(str) + "\x00" + f.outcome_label.astype(str)).set_index("k")["outcome_index"]
            k = out["market_id"].astype(str) + "\x00" + lab
            via = pd.Series(key.reindex(k).to_numpy(dtype=float), index=out.index)
            fill = oi.isna() & via.notna()
            oi = oi.where(~fill, via)
            src = src.where(~fill, "outcome_label_meta")
    elif schema == "token_series":
        tcol = _first(out.columns, ("token_id", "asset_id", "symbol", "asset"))
        token_id = out[tcol].astype(str).astype(object)
        if token_map is None:
            raise ValueError("token_series needs a TokenMap")
        meta = token_map.lookup(token_id)
        oi = pd.Series(meta["outcome_index"].to_numpy(dtype=float), index=out.index)
        label = meta["outcome_label"].set_axis(out.index)
        src = pd.Series(np.where(oi.notna(), "token_id", "unknown"), index=out.index, dtype=object)
        if "side" in out.columns:
            s = out["side"].astype(str).str.upper()
            side = s.where(s.isin(["BUY", "SELL"]), None).astype(object)
    else:
        raise ValueError(f"unknown schema {schema!r}; expected one of {SCHEMAS}")

    if already_yes:
        yes_price = price.to_numpy(dtype=float)
        yes_side = side.to_numpy(dtype=object)
    else:
        yes_price, yes_side = to_yes(price.to_numpy(dtype=float), oi.to_numpy(dtype=float), side.to_numpy(dtype=object))
    out["token_id"] = token_id.to_numpy()
    out["outcome_index"] = oi.to_numpy(dtype=float)
    out["outcome_label"] = label.to_numpy()
    out["yes_price"] = yes_price
    out["yes_side"] = yes_side
    out["token_source"] = src.to_numpy()
    out["token_conflict"] = conflict.to_numpy(dtype=bool)
    return out


def yes_price_frame(
    df: pd.DataFrame,
    token_map: Optional[TokenMap] = None,
    schema: str = "auto",
    drop_unknown: bool = True,
) -> pd.DataFrame:
    """Normalise and return a frame whose ``price`` column IS the YES price (original kept as ``price_raw``).

    Convenience for legacy readers that only understand a ``price`` column. When the frame has a ``side``
    column it is replaced by the YES-equivalent side (original kept as ``side_raw``).
    """
    out = normalize_trades(df, token_map=token_map, schema=schema)
    out["price_raw"] = out["price"] if "price" in out.columns else np.nan
    out["price"] = out["yes_price"]
    if "side" in out.columns:
        out["side_raw"] = out["side"]
        out["side"] = out["yes_side"]
    if drop_unknown:
        out = out[out["yes_price"].notna()]
    return out


def ensure_yes_trades(df: pd.DataFrame, where: str = "reader", drop_unknown: bool = True) -> pd.DataFrame:
    """Idempotent drop-in for legacy trade readers that use ``price`` / ``maker_direction`` / ``taker_direction``.

    * Already normalised (column ``yes_normalized``) -> returned unchanged.
    * On-chain processed rows (``nonusdc_side``) or Data-API rows (``outcomeIndex``) -> ``price`` becomes the YES
      price (original in ``price_raw``), ``maker_direction`` / ``taker_direction`` / ``side`` are flipped for
      outcome-1 rows, ``outcome`` is set to "YES", and rows with unknown token identity are dropped.
    * No token identity at all -> returned unchanged with a :class:`LegacyPolymarketReaderWarning`.
    No metadata lookup is done (fast); the source's own token label is used.
    """
    if df is None or len(df) == 0 or "yes_normalized" in df.columns:
        return df
    if "price" not in df.columns:
        return df
    c = set(df.columns)
    if "nonusdc_side" in c and df["nonusdc_side"].notna().any():
        ns = df["nonusdc_side"].astype(str)
        oi = np.where(ns.eq("token1"), 0.0, np.where(ns.eq("token2"), 1.0, np.nan))
        if "outcomeIndex" in c:  # mixed-generation research file: Data-API rows carry outcomeIndex instead
            oi = np.where(np.isnan(oi), pd.to_numeric(df["outcomeIndex"], errors="coerce"), oi)
    elif "outcomeIndex" in c:
        oi = pd.to_numeric(df["outcomeIndex"], errors="coerce").to_numpy(dtype=float)
    else:
        warn_legacy_reader(where, "Frame has no nonusdc_side/outcomeIndex, so token identity is unknown.")
        return df
    out = df.copy()
    out["price_raw"] = pd.to_numeric(out["price"], errors="coerce")
    out["price"] = to_yes(out["price_raw"].to_numpy(dtype=float), oi)
    flip = oi == 1
    for col in ("maker_direction", "taker_direction", "side"):
        if col in c:
            v = out[col].astype(object).to_numpy()
            out[col] = np.where(flip, flip_side(v), v)
    out["outcome_index_raw"] = oi
    out["outcome"] = "YES"
    out["yes_normalized"] = True
    if drop_unknown:
        out = out[~np.isnan(oi)]
    return out


# --------------------------------------------------------------------------------------------- diagnostics


def mixing_stats(
    df: pd.DataFrame,
    group_col: str,
    order_cols: Sequence[str] | str,
    price_col: str = "price",
    token_col: Optional[str] = None,
    jump: float = 0.50,
) -> dict:
    """Token-mixing and consecutive-jump statistics of a price series.

    * ``frac_markets_mixed``: share of groups (markets) whose prints come from both tokens (``token_col``).
    * ``frac_jumps``: share of consecutive same-group print pairs with ``|dp| > jump``.
    * ``frac_markets_with_jump``: share of groups with at least one such jump.
    """
    order_cols = [order_cols] if isinstance(order_cols, str) else list(order_cols)
    d = df[[group_col, *order_cols, price_col] + ([token_col] if token_col else [])].dropna(subset=[price_col])
    d = d.sort_values([group_col, *order_cols], kind="mergesort")
    g = d[group_col].to_numpy()
    p = d[price_col].to_numpy(dtype=float)
    same = np.r_[False, g[1:] == g[:-1]]
    dp = np.abs(np.r_[np.nan, np.diff(p)])
    jumps = same & (dp > jump)
    n_pairs = int(same.sum())
    res = {
        "n_rows": int(len(d)),
        "n_markets": int(pd.Series(g).nunique()),
        "n_pairs": n_pairs,
        "n_jumps": int(jumps.sum()),
        "frac_jumps": float(jumps.sum() / n_pairs) if n_pairs else float("nan"),
        "frac_markets_with_jump": float(pd.Series(jumps).groupby(g).any().mean()) if len(d) else float("nan"),
    }
    if token_col:
        k = d.groupby(group_col)[token_col].nunique()
        res["frac_markets_mixed"] = float((k > 1).mean()) if len(k) else float("nan")
        res["frac_rows_outcome1"] = float(pd.to_numeric(d[token_col], errors="coerce").eq(1).mean()) \
            if pd.api.types.is_numeric_dtype(d[token_col]) else float("nan")
    return res


def assert_single_token_series(
    df: pd.DataFrame,
    group_col: str,
    token_col: str,
    where: str = "",
    action: str = "raise",
) -> None:
    """Guard: a raw price column must not interleave both tokens within a market.

    Call this in readers that consume ``price`` directly. ``action``: "raise" (default) or "warn".
    """
    if token_col not in df.columns or group_col not in df.columns or df.empty:
        return
    k = df.groupby(group_col)[token_col].nunique()
    if (k > 1).any():
        msg = (f"{where}: {int((k > 1).sum())}/{len(k)} markets mix both outcome tokens in one price series; "
               "normalise with src.data_quality.polymarket_tokens.normalize_trades / yes_price_frame first")
        if action == "warn":
            warnings.warn(msg, LegacyPolymarketReaderWarning, stacklevel=2)
        else:
            raise MixedTokenSeriesError(msg)


def warn_legacy_reader(name: str, detail: str = "") -> None:
    """Mark a legacy reader that consumes raw, possibly YES/NO-mixed Polymarket prices.

    Emits :class:`LegacyPolymarketReaderWarning`; raises instead when env ``PMFIX_STRICT=1``.
    """
    msg = (f"{name} reads Polymarket prices without YES/NO token normalisation (STRATEGY_AUDIT D1); results "
           f"from it are unreliable. {detail} See docs/PMFIX_POLYMARKET_TOKEN_NORMALISATION.md")
    if os.environ.get("PMFIX_STRICT", "") == "1":
        raise MixedTokenSeriesError(msg)
    warnings.warn(msg, LegacyPolymarketReaderWarning, stacklevel=2)
