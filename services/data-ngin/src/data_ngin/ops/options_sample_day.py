"""Run the IV pass on one day directly from the raw OPRA.PILLAR files.

This is the O5 check from #32, and it doesn't need anything loaded into the DB. For
2026-09-15 (Tier 1) the issue says: every quoted OTM contract at 7-365 days solved
(7,900 of 7,900 on SPXW), SPX 30-day ATM vol of 13.8%, and a QQQ forward of 704.52
vs a 704.54 close. This prints the same numbers so we can compare.

    python -m data_ngin.ops.options_sample_day --date 2026-09-15 \\
        --definitions <definition .dbn.zst files> --quotes <cbbo-1m .dbn.zst files> \\
        [--dtb3 <percent>] [--out eod.csv]

Pass in the files for that date (pulled out of the vendor job tars). If you leave
out --dtb3 it grabs the T-bill rate from FRED (no key needed). Doesn't write to the DB.
"""

import argparse
import logging
import sys
from datetime import date, time

from data_ngin.application.implied_vol_pass import (
    EXCHANGE_TZ,
    atm_vol_by_underlying,
    compute_eod,
)

logger = logging.getLogger(__name__)

FRED_DTB3_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"
# Each closing-quote file has two snapshots. Use the later one and fall back to the
# earlier (#32 section 4). On early close days they're 3 hours earlier.
REGULAR_SNAPSHOTS = (time(16, 0), time(15, 59))
EARLY_CLOSE_SNAPSHOTS = (time(13, 0), time(12, 59))


def load_definitions(paths: list[str]):
    """Latest definition for each instrument_id, options only (class C or P)."""
    import databento as db
    import pandas as pd

    frames = [db.DBNStore.from_file(path).to_df() for path in paths]
    defs = pd.concat(frames).sort_index(kind="mergesort")
    defs = defs[defs["instrument_class"].isin(["C", "P"])]
    defs = defs.reset_index().drop_duplicates("instrument_id", keep="last")
    return pd.DataFrame(
        {
            "contract_id": defs["instrument_id"],
            # OSI symbol: root is the first 6 chars, padded with spaces.
            "root": defs["raw_symbol"].str[:6].str.strip(),
            "underlying": defs["underlying"],
            "expiration": pd.to_datetime(defs["expiration"], utc=True),
            "strike": defs["strike_price"].astype(float),
            "call_put": defs["instrument_class"],
        }
    ).reset_index(drop=True)


def load_closing_quotes(paths: list[str], trade_date: date):
    """One bid/ask per instrument_id: the closing snapshot, or the minute before if missing."""
    import databento as db
    import pandas as pd

    frames = [db.DBNStore.from_file(path).to_df() for path in paths]
    quotes = pd.concat(frames)
    stamp = pd.to_datetime(quotes.index, utc=True).floor("min")
    local = stamp.tz_convert(EXCHANGE_TZ)
    quotes = quotes.assign(quote_ts=stamp, local_date=local.date, local_time=local.time)
    quotes = quotes[quotes["local_date"] == trade_date]
    snapshots = (
        REGULAR_SNAPSHOTS
        if (quotes["local_time"] == REGULAR_SNAPSHOTS[0]).any()
        else EARLY_CLOSE_SNAPSHOTS
    )
    quotes = quotes[quotes["local_time"].isin(snapshots)]
    quotes = quotes.assign(preference=(quotes["local_time"] != snapshots[0]).astype(int))
    quotes = quotes.sort_values(["instrument_id", "preference"], kind="mergesort")
    quotes = quotes.drop_duplicates("instrument_id", keep="first")
    return pd.DataFrame(
        {
            "contract_id": quotes["instrument_id"],
            "bid": quotes["bid_px_00"].astype(float),
            "ask": quotes["ask_px_00"].astype(float),
            "quote_ts": quotes["quote_ts"],
        }
    ).reset_index(drop=True)


def dtb3_for(trade_date: date) -> float:
    """DTB3 for trade_date from FRED. On holidays, uses the last value before it."""
    import pandas as pd

    series = pd.read_csv(FRED_DTB3_URL, na_values=".")
    series.columns = ["date", "rate"]
    series["date"] = pd.to_datetime(series["date"]).dt.date
    known = series[series["date"] <= trade_date].dropna()
    if known.empty:
        raise ValueError(f"FRED DTB3 has no value on or before {trade_date}")
    return float(known["rate"].iloc[-1])


def summarise(quotes, eod) -> None:
    """Print the numbers from #32 section 1 so we can compare."""
    from data_ngin.application.implied_vol_pass import QuoteStatus

    not_attempted = {s.value for s in QuoteStatus}
    merged = quotes.merge(eod, on="contract_id")
    attempted = merged[~merged["iv_status"].isin(not_attempted)]
    counts = (
        attempted.assign(solved=attempted["iv_status"] == "ok")
        .groupby("root")["solved"]
        .agg(quoted_otm_7_365="size", solved="sum")
    )
    print("\nQuoted out-of-the-money contracts at 7-365 days, and how many solved:")
    print(counts.to_string())
    print("\nStatus counts:")
    print(merged["iv_status"].value_counts().to_string())
    print("\n30-day at-the-money volatility:")
    print(atm_vol_by_underlying(quotes, eod).to_string(index=False))
    nearest = (
        merged.dropna(subset=["forward"])
        .sort_values(["underlying", "years_to_expiry"], kind="mergesort")
        .drop_duplicates("underlying")
    )
    print("\nForward of the nearest expiry per underlying:")
    print(nearest[["underlying", "root", "expiration", "forward"]].to_string(index=False))


def main(argv: list[str] | None = None) -> int:
    from data_ngin.domain.options_pricing import discount_yield_to_continuous

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--definitions", required=True, nargs="+")
    parser.add_argument("--quotes", required=True, nargs="+")
    parser.add_argument("--dtb3", type=float, help="3-month T-bill discount yield, percent")
    parser.add_argument("--out", help="write the computed eod rows to this CSV")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    definitions = load_definitions(args.definitions)
    closing = load_closing_quotes(args.quotes, args.date)
    quotes = closing.merge(definitions, on="contract_id", how="left")
    unmatched = quotes["root"].isna().sum()
    if unmatched:
        logger.warning("%d quotes have no definition and are dropped", unmatched)
        quotes = quotes.dropna(subset=["root"])

    dtb3 = args.dtb3 if args.dtb3 is not None else dtb3_for(args.date)
    rate = float(discount_yield_to_continuous(dtb3))
    logger.info("DTB3 %.2f%% -> continuous rate %.6f", dtb3, rate)

    eod = compute_eod(quotes, rate, args.date)
    summarise(quotes, eod)
    if args.out:
        eod.merge(quotes, on="contract_id").to_csv(args.out, index=False)
        print(f"\nWrote {len(eod)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
