"""settle: extract local Kalshi tape (full tape via predict_ngin.tapes.kalshi) for candidate series prefixes."""
import sys
from pathlib import Path
import pandas as pd, pyarrow.compute as pc
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from predict_ngin.shared.duck import connect  # noqa: E402
from predict_ngin.tapes.kalshi import tape_sql  # noqa: E402
PREF = ("KXTSAW", "KXTSAD", "TSAW", "KXRAINNYC", "KXRAIN", "KXAAAGAS", "AAAGAS", "KXSPOTIFY", "KXNETFLIX", "KXHMONTH",
        "KXTORNADO", "KXNYCSNOWM", "KXDCSNOWM", "KXCHISNOWM", "KXPHILSNOWM", "KXBOSSNOWM", "KXDETSNOWM", "KXDENSNOWMB",
        "KXSLCSNOWM", "KXLAXSNOWM", "KXDALSNOWM", "KXMIASNOWM", "KXJACWSNOWM", "KXSNOW", "KXJOBLESSCLAIMS", "KXTOPSONG", "KXTOPALBUM",
        "KXALBUMSALES", "KXAPPRANK", "KXBILLBOARD", "KXEGGS", "KXHURC", "KXEARTHQUAKE")
out = []
con = connect()
# full tape via predict_ngin.tapes.kalshi (trades_fixed alone ends 2026-01-27), one UTC month at a time
for p in pd.period_range("2021-06", pd.Timestamp.now(tz="UTC").strftime("%Y-%m"), freq="M"):
    m0, m1 = (int(x.tz_localize("UTC").timestamp()) for x in (p.start_time, (p + 1).start_time))
    t = con.sql(tape_sql(["ticker", "event_ticker", "yes_price", "count", "taker_side", "timestamp_unix"],
                         m0, m1)).to_arrow_table()
    tk = t.column("ticker")
    m = None
    for pref in PREF:
        x = pc.starts_with(tk, pref)
        m = x if m is None else pc.or_(m, x)
    t = t.filter(m)
    out.append(t.to_pandas())
    print(f"trades_{p}", t.num_rows, flush=True)
d = pd.concat(out, ignore_index=True)
d["series"] = d.event_ticker.str.split("-").str[0]
d.to_parquet(ROOT / "data/vendor/settle/tape.parquet")
print(d.groupby("series")["count"].agg(["size", "sum"]).sort_values("sum", ascending=False).head(60).to_string())
