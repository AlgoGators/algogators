# Kalshi weather settlement sources after the TWC switch, and the hourly temperature markets (twc, 2026-10-15)

Last revised: 2026-10-15

Research only. No orders were placed and no keys were printed.

**Data used**
- **Kalshi REST** (read-only GETs at ≤ 0.5 req/s, then ≤ 0.3 req/s), about 3,800 calls:
  - `/series`, `/events`, `/markets` (rules_primary / rules_secondary, expiration_value);
  - batch `/markets/candlesticks`;
  - the public `/live_data/weather/{city}` Kalshi Weather Index.
- **Free archives:** IEM (CLI, METAR, 1-minute ASOS).
- **One 70-minute sample** of aviationweather.gov METAR latency.
- **Read-only inputs:** the wx study files and the live recorder.

The wx / wxlive namespaces and the running wxlive forward test were not touched.

Pre-registration for the hourly tests: `docs/TWC_HOURLY_PREREG.md`. It was written before any hourly price
data was loaded.

## Verdict

**There is no tradeable TWC-specific edge.** Headline $/yr is **$0** at $1k / $2.5k / $5k / $10k:

| Capital | $/yr | Avg deployed | Sharpe / DSR | OOS |
|---|---|---|---|---|
| $1k / $2.5k / $5k / $10k | $0 (nothing passes) | $0 | n/a: no positive OOS cell. Best test cell −1.8¢/contract; all 30 pre-registered hourly cells are negative out of sample | Pre-registered split (train ≤ 09-30, test 10-01 → 10-14): fails |

**1. Daily KXHIGH* / KXLOW* settle on TWC, but TWC = NWS CLI.**
- **Agreement.** Settled values match the CLI final on **2,837 of 2,838 city-days** (2026-08-14 → 10-13, 48 series).
- **Switch date.** Every pre-existing series switched on **event date 2026-08-14**: the last NWS-rules event was 08-13.
- **The one disagreement is a gross TWC error, not QC.** KXHIGHMIA-26AUG29 settled at 90 °F; the CLI and every METAR
  said 85 °F.
- **For wxlive** the old CLI-era backtests stay representative, and the wxlive P&L stays valid. That P&L uses real
  settlement, and the 5-minute-feed-overstates-the-max mechanism is unchanged.
- **New tail risk:** roughly 1 gross TWC error per ~2,800 city-days.

**2. Hourly temperature markets no longer settle on TWC.** The four listed series now settle on the **Kalshi Weather
Index**: KXTEMPMIAH, KXTEMPNYCHS, KXTEMPCHIHS and KXTEMPLAXHS.
- **The index.** It is an offset-adjusted, equal-weight mean of the 1-minute HF-ASOS readings delivered by Synoptic:

  | City | Stations |
  |---|---|
  | NYC | 8 (LGA, EWR, TEB, CDW, HPN, FRG, SMQ, ISP; Central Park is *not* in it) |
  | Miami | 5 |
  | Chicago metro | 5 (MDW, ORD, PWK, DPA, ARR) |
  | Coastal LA | 5 |

- **Publication.** Kalshi publishes it **free, with no key**, on `/live_data/weather/{city}`. Per-station readings
  appear about 145 s after each minute; the canonical point appears at t + 300 s.
- **History of the settlement source:**

  | Period | Source |
  |---|---|
  | Apr 2026 pilots | AccuWeather |
  | 2026-08-07 → 09-10 | TWC "temperature at S" for the station coordinates (KXTEMP{AUS,CHI,DC,LAX,NYC}H) |
  | Since 2026-08-15 (MIAH), 2026-09-10 (others) | Kalshi Weather Index |

**3. Hourly strategies all fail.**
- **H1 (trade after the reading) is impossible.**
  - The rulebook sets the last trading time at the reading minute S.
  - **Zero** volume in 34,517 post-S candles over 1,061 hours.
- **H2 (intra-hour index-convergence taker, 30 pre-registered cells) loses out of sample in every cell**
  (−1.8 to −6.5 ¢/contract).
  - The market's Brier (0.101–0.104) beats the index nowcast at every horizon, **even when the nowcast uses the
    station readings 2.6 minutes before the canonical point**.
- **TWC-era retro test.** In the TWC hourly era, settlement equalled the last routine METAR (99.5%).
  - The book went one-sided the moment that METAR was observed.
  - At the measured ~4-minute free METAR latency, only **2 of 30,730** markets still offered the implied side.
- **H3 (QC spike fade) has no events.**
  - The index's spatial QC rejected nothing in 244k minutes.
  - TWC daily kept 27 of 28 single-report METAR spikes.

**4. The Quantcha claim (mid-price hourly takers win 53.1% at 50.6¢) is not reproduced.**
- Our only side-labelled hourly tape is the recorder: KXTEMPMIAH, 5 events on 2026-10-14.
- Mid-price (21–79¢) takers there won 42.9% at 49.9¢ (−9¢/contract after fees, 2,443 prints).
- N is far too small to reject the claim. See Blockers.

## 1. Settlement-source map (`scripts/research/twc_source_map.py`)

**What the script does.**
- Re-runnable daily; it is not scheduled.
- ≤ 0.5 req/s, about 425 calls.
- Writes `data/vendor/twc/source_map_YYYYMMDD.parquet` (+ .csv, plus raw JSON in `data/vendor/twc/raw/`).
- Per series it records:
  - title and frequency;
  - the `/series` settlement_sources agency;
  - whether the series is listed (open markets);
  - the agency parsed from `rules_primary`, falling back to `rules_secondary`;
  - the station (CLIxxx or ICAO), the measured quantity, the stated time, and close/expiration times.
- Flags:
  - `title_station_mismatch`;
  - `sources_rules_mismatch`;
  - `stale_metadata` (series last_updated > 60 days while listed);
  - `source_changed` (against fin_E Aug-2026 rules, the wx 10-14 snapshot and earlier twc maps).
- `--reparse` recomputes the flags from a saved map with no API calls.

**Snapshot of 2026-10-14** (`source_map_20261014.parquet`). Of 424 Climate-and-Weather series, 138 are listed.

| Group | Listed | Rules source | Notes |
|---|---|---|---|
| Daily high/low KXHIGH*/KXLOWT* | 48 (24 stations × high/low) | TWC (weather.com/kalshi) for every one, keyed to "(CLIxxx)" | Climate day = local standard time (per the wx study) |
| Hourly temperature | 4: MIAH, NYCHS, CHIHS, LAXHS | "Synoptic Data … Kalshi Weather Index Methodology" | Close = stated minute S; settlement median 81 min after S |
| Monthly rain KXRAIN*M | 19 | Primary says "at CLIxxx" with no agency; 17 name **TWC** daily precip in rules_secondary | The brief's "still NWS CLI" did not hold for these on 10-14 |
| Hurricanes | 7 | NHC | — |

**Flags.**
- **Title vs station (confirmed lead):**

  | Series | Title says | Rules say |
  |---|---|---|
  | **KXHIGHTSDF** | "HIGHEST Temperature SATX" | Louisville (CLISDF) |
  | **KXLOWTSDF** | "Lowest Temperature SDX" | Louisville (CLISDF) |

  San Antonio has its own correct series, KXHIGHTSATX / KXLOWTSATX (CLISAT). Anyone trading the SDF series by title
  is trading Louisville.
- **Sources vs rules:** KXTEMPCHIHS lists only "kalshi.com" as its settlement source; the rules say Synoptic. This is
  harmless.
- **Stale metadata:** none.
- **Source changed:** the 36 series in the fin_E August snapshot went NWS CLI → TWC. No station changed.

**Exact switch date.** From the rules_primary of every settled market 2026-08-07 → 10-13 (`daily_markets.parquet`):
- All 42 series that existed before the switch have NWS rules through event date **2026-08-13** and TWC from
  **2026-08-14**.
- Six were launched after and were TWC from launch: SAN 08-19, EWR/TTN 08-26, SDF 08-28.

## 2. Daily settlement: TWC vs CLI vs METAR vs ASOS (`scripts/research/twc_settle_compare.py`)

**Sample and inputs.**
- 2,840 TWC-era events, 2026-08-14 → 10-13.
- Kalshi `expiration_value` and the winning bracket.
- NWS CLI final (IEM).
- METAR max/min including the 6-hour groups, METAR hourly T-group max, and ASOS 5-minute and 1-minute max/min (IEM
  1-minute archive; only 378 complete station-days).

| Comparison (TWC era) | Highs: exact / same bracket | Lows: exact / same bracket |
|---|---|---|
| Kalshi = NWS CLI final | **0.9993 / 0.9993** | **1.0000 / 1.0000** |
| = round(METAR incl. 6 h groups) | 0.989 / 0.992 | 0.856 / 0.925 |
| = round(METAR hourly T-groups only) | 0.349 / 0.632 | — |
| = ASOS 5-min sample max/min (n = 378) | 0.709 / 0.860 | 0.926 / 0.966 |
| = ASOS 1-min max/min (n = 378) | 0.474 / 0.770 | 0.847 / 0.923 |

Pre-switch NWS-rules events in the same pull (280) matched the CLI 100%.

**By city.**
- CLI agreement is 100% at every station except MIA high, where the single error sits.
- METAR agreement is lowest for lows: DEN 0.61, HOU 0.66, MDW 0.75, MSP 0.75, OKC 0.77, MSY 0.75.
- Full table in `data/vendor/twc/logs/settle_compare.txt`.

**Are the disagreements predictable before the close?**
- **Highs:**
  - Kalshi ≠ METAR on only 1.1% of days.
  - When the 6-hour max group exceeds the hourly T-group max, Kalshi ≠ METAR on just 0.3% of days.
  - TWC = CLI = the 6-hour group, all available before the close.
- **Lows:** Kalshi settles 1 °F below round(METAR min) on 11.6% of days. This is predictable from the 5-minute feed:

  | 5-minute min vs METAR min | Disagreement rate |
  |---|---|
  | 1 °F below | 65% |
  | 2 °F or more below | 100% |
  | Equal | 1.2% |

  The CLI uses the true sensor minimum, which the hourly METARs miss.
- **The TWC error** (MIA 2026-08-29: 90 vs 85) was not predictable from any observation. The "below 88" market
  (34k contracts) and the 88–89 bracket paid out wrong.

**What this means**
- **wxlive (`wx_5min_feed_edge_taker`).**
  - Its edge is that the free 5-minute feed overstates the settlement max by 1 °F on about 25% of days, and the
    market overreacts.
  - Settlement is TWC = CLI, so the mechanism and the backtest's CLI-based truth are unchanged. The forward-test P&L
    uses real Kalshi settlement, so it is valid.
  - The only new item is a tail: about 1 gross TWC error per 2,840 city-days. On a sell-YES position this can turn a
    sure winner into a loss. Size so one such day is survivable.
- **Old CLI-era backtests (`wx_*`) stay representative.** The source switch changed nothing measurable, which confirms
  the wx study's 2,121 / 2,122 result on 13 more days and 12 more series.
- **Daily TWC spikes (H3, descriptive).**
  - On 28 TWC-era days a single METAR report ≥ 3 °F above both neighbours set the daily max.
  - TWC/CLI kept the spike on 27. The exception is NYC 2026-08-27: METAR 80 °F spike, settled 77.
  - TWC adds no QC beyond the CLI, so there is no "market overreacts to a spike TWC will reject" trade.

## 3. Hourly temperature markets

**Rules** (CHIMETROWINDEX rulebook, CFTC filing 2026-09-09; pdf copy in `data/vendor/twc/docs/`; same in the market
rules).
- "At" contracts settle on the last **canonical** Kalshi Weather Index point with event time ≤ S, at most 60 minutes
  old.
- Strikes are "above X.99 °F", at 2-decimal precision with no rounding.
- **Last trading time = S.**
- Canonical point for minute t:
  - It is computed and published at t + 300 s and never revised.
  - Inputs are the 1-minute HF-ASOS readings (Synoptic network 258) with receipt ≤ t + 300 s.
  - The calculation is z_i = x_i − b_i + B_c, then an equal-weight mean, with quorum ≥ 80% of the weight.
  - QC is a rate gate plus a spatial gate, max(4 °C, 6×MAD); "extreme" is > max(8 °C, 10×MAD).
- Offsets are re-estimated weekly, and the calibration history is public at `/live_data/weather/{city}/calibrations`.

**Verified facts (`twc_hourly.py`).**
- **Settlement = the index.**
  - Kalshi `expiration_value` equals the canonical index point at S on **100% of 3,470 hourly events** that have
    index history (MIA from 08-31, NYC/CHI/LA from 09-10).
  - The result is consistent with the expiration value and strike on 78,928 of 78,928 markets.
- **Station readings come early.** The free endpoint returns each station's pending reading (temp_f, received_at) a
  median **145 s** after the minute (p90 156–165 s), versus 300 s for the canonical point.
  - Our recomputation of the index from those readings and the public offsets matches the canonical value to
    ≤ 0.05 °F on 100.0% of minutes.
- **Station inputs are whole °C** (e.g. 84.2 °F = 29 °C). The index therefore moves in steps of about 0.36 °F
  (Miami) and frequently jumps across a strike in the last minutes.
- **Timing.** Markets open 60 minutes before S, and trading clusters in the last 45 minutes. Settlement comes a median
  81 minutes after S, not "25–35 minutes".
- **Volume.** About **640k contracts/day** across the four series (MIAH 375k/day), quadratic fee type.

### H1. Post-reading latency taker — rejected

Markets close exactly at S (rule plus data).
- Candles after S: 34,517 one-minute candles over 1,061 KWI-era hours, **0 volume**.
- Recorder KXTEMPMIAH (2026-10-14): the last prints land 2–6 s before S in 5 of 6 events.

### H2. Intra-hour index-convergence taker — rejected

**Setup (pre-registered).**
- **Decision times:** S − k, for k ∈ {1, 2, 3, 5, 10} minutes.
- **Information**, two variants:
  - C = canonical point, available at t + 300 s;
  - P = pending station readings, available when ≥ 80% of members have been received, about t + 145 s.
- **Fair value:** p(I(S) > K) from the train-only empirical distribution of index changes over the gap g.
- **Execution:** taker at the 1-minute candle's YES ask / NO ask (1 − bid), 10 lots, Kalshi taker fee.
  θ ∈ {2, 4, 8} ¢.
- **Split:** train ≤ 2026-09-30 (32 days), test 2026-10-01 → 10-14 (14 days).
- **Size:** 4 cities, 177,198 decisions.

**Brier score** (two-sided quotes, mid):

| k | Market (test) | Model C (test) | Model P (test) | Model P (train) | Market (train) |
|---|---|---|---|---|---|
| 1 | **0.102** | 0.131 | 0.113 | 0.082 | 0.083 |
| 3 | **0.101** | 0.138 | 0.119 | 0.088 | 0.083 |
| 10 | **0.101** | 0.161 | 0.151 | 0.107 | 0.087 |

**P&L, ¢/contract, test window** (event-day cluster bootstrap 95% CI); full grid in `data/vendor/twc/h2_results.csv`:

| θ | Variant | k = 1 | k = 2 | k = 3 | k = 5 | k = 10 |
|---|---|---|---|---|---|---|
| 2¢ | C | −6.1 [−8.8, −3.4] | −3.8 | −4.3 | −4.2 | −4.4 |
| 2¢ | P | −2.3 [−5.0, +0.1] | −3.9 | −3.6 | −3.3 | −4.0 |
| 4¢ | P | −3.5 [−6.9, −0.3] | −3.7 | −3.2 | −3.2 | −4.3 |
| 8¢ | P | −3.2 [−6.8, +0.7] | −4.1 | −1.8 [−5.1, +1.3] | −4.2 | −4.9 |

**Why it fails.**
- **Train looked positive only for P at k = 1** (+1.8 to +4.8 ¢, CI barely > 0 at θ = 4–8¢). It flipped to −2.3 to
  −3.5 ¢ in test, which is the overfitting signature.
- **Information content.** Blending the P nowcast 50/50 with the mid improves the test Brier only marginally
  (0.1013 vs 0.1023 at k = 1). The free pending readings carry a sliver of information the market lacks, but:
  - it is far smaller than the spread, which averages 10¢ at k = 1 and 6¢ at k = 10;
  - plus the taker fee.
- **Who is faster.** Other participants evidently see the station data faster: direct Synoptic or HF-ASOS
  subscribers, or Kalshi-endpoint pollers at < 30 s.
- **Gaps in the test.** Candles carry no depth, and the dI model pools all hours (no diurnal drift term). A better
  nowcast might narrow the Brier gap but cannot cover 6–10¢ of spread.

### TWC-era hourly retro check (`twc_hourly_twcera.py`, `twc_hourly_twcera_px.py`)

These series ran 2026-08-07 → 09-10, with 4,008 events. TWC's "temperature at S for KAUS/KORD/KDCA/KLAX/KNYC" was an
integer, and it **equalled the last METAR at or before S on 99.5% of events**:

| Compared with | Exact match |
|---|---|
| Last METAR at or before S (obs :51–:56, 4–9 min before S) | **99.5%** |
| 1-minute ASOS at S | 59% |
| METAR before S − 10 min | 27% |

So the outcome was physically known 4–9 minutes before the close. The market already priced it:
- Two-sided quotes covered 43% of markets at S − 8, 2% at S − 5 and < 0.1% at S − 3. The book went one-sided right
  as the METAR was observed, while volume continued at extreme prices.
- **Free METAR latency, measured.**

  | Source | Latency from obs time | Sample |
  |---|---|---|
  | aviationweather.gov | **median 3.9 min** (p10 3.5, p90 5.0) | 11 METARs on 2026-10-14 |
  | IEM | 13 min | wxlive logs |
  | MADIS | 19 min | wxlive logs |

- **Causal replay at 4-minute latency:** only **2 of 30,730** markets offered the METAR-implied side below $1.
- **Hypothetical 2-minute feed:** 1,287 fills at +8.8 ¢ (candle closes, no depth). Somebody with sub-2-minute METAR or
  1-minute ASOS access took that.
- **This era is over.** The series were replaced by the index markets.

### H3. QC spike fade — rejected (no events)

- **Index QC:** across 244,440 index-minutes in 4 cities, the station codes show **0 spatial or extreme rejections**
  (4 range, 10 provider-QC, the rest ok/missing/late).
- **Member spreads:** a member deviating ≥ 4 °C from the offset-adjusted peer median happens on 1–12% of minutes.
  These are real spatial differences (sea breeze, urban sites) and they enter the index, so the "QC will reject the
  spike" premise never triggers.
- **Daily:** see section 2 (27 of 28 spikes kept).

### Quantcha mid-price-taker claim

**Our test.**
- **Data:** the recorder's side-labelled prints, KXTEMPMIAH, 5 events on 2026-10-14.
- **Side normalisation:** the recorder `price` is the YES price (outcome is always "yes"). A taker's paid price is that
  price for taker_side = yes and 1 − price for taker_side = no.
- **Result for 21–79¢ takers:**
  - 2,443 prints, 51,846 contracts;
  - win rate 42.9% at an average 49.9¢;
  - −9.1 ¢/contract after fees.

**Verdict.** With 5 events this is noise. It neither confirms nor rejects +1.7% on 603,661 trades.

**What a proper check needs.** Per-ticker `/markets/trades` for about 3,500 events × 10 strikes, which is more than
10 h at 0.3 req/s. Alternatively, add the 3 *HS series to the recorder.

Even if true, +1.7% gross-of-selection on mid-price takers is not a strategy we can isolate ex ante. The H2 test is the
causal version of "take mid-priced hourly contracts with an information edge", and it lost.

## Blockers & how to unblock

| Blocker | Effect | Unblock |
|---|---|---|
| **Hourly-market depth.** The recorder covers only KXTEMPMIAH (k-lip group, from 10-14); NYCHS/CHIHS/LAXHS books are not recorded. | No causal maker test, and no real queue depth at the decision time. | Free. The user adds `KXTEMPNYCHS, KXTEMPCHIHS, KXTEMPLAXHS` to the recorder config and runs it ≥ 14 days. Only then is a maker test that quotes off the pending index worth running. |
| **No side-labelled trade tape for hourly markets before 10-13.** | The Quantcha check is not testable at scale. | Free but slow: per-ticker `/markets/trades` (≈ 35k calls ≈ 32 h at 0.3 req/s). Not recommended given H2. |
| **Faster station data than the free index endpoint** (~145 s). | Probably what the winning hourly takers use. | Synoptic Data API (free "open access" tier needs a **user-registered token**; HF-ASOS 1-min network 258 availability on the free tier is unverified). Paid Synoptic tiers are quoted on request. Only the user can register. Expected value is low: even the 145-s data does not beat the spread. |
| **aviationweather.gov latency from one 70-min sample.** | Matters for wxlive's daily METAR leg, not for this study. | Free: run `scripts/research/twc_awc_latency.py --minutes 1440` for a day. |
| **Synoptic free-tier HF-ASOS and NWS-free HF data are not used for the daily wxlive edge.** | — | wxlive owners may want to test the Kalshi index endpoint's per-station 1-minute readings. For KMDW (Chicago metro), KLGA/KEWR (NYC index) and KMIA (Miami) these are a ~2.5-minute free 1-minute feed. That is faster than MADIS 5-minute (7–17 min) for the daily stations that happen to be index members. **Lead only; not tested here.** |

## Files

**Scripts** (`scripts/research/`):

| Script | Purpose |
|---|---|
| `twc_source_map.py` | Daily source map; `--reparse` mode |
| `twc_fetch.py` | daily, hourly and batch (KWI / TWC era) market and candle pulls |
| `twc_index_fetch.py` | Kalshi Weather Index history with per-station receipts |
| `twc_settle_compare.py` | Section 2 |
| `twc_hourly.py` | Settlement = index check, H1, H2 (`--h2`) |
| `twc_hourly_twcera.py`, `twc_hourly_twcera_px.py` | TWC-era retro check |
| `twc_awc_latency.py` | aviationweather.gov METAR latency |

**Tests:** `tests/unit/test_twc_parsing.py` (3 pass).

**Data** (`data/vendor/twc/`):
- `source_map_20261014.parquet/.csv`
- `daily_markets.parquet`, `hourly_markets.parquet`
- `index/<city>/`, `bcandles/`, `bcandles_twc/`
- `settle_compare.parquet`, `h2_decisions.parquet`, `h2_results.csv`
- `hourly_twcera_compare.parquet`, `awc_latency.jsonl`
- `logs/`

**Ledger:** `scripts/research/ledger_entries/twc.yaml` (5 entries, all `rejected`).
