# Settlement-mechanics and partial-information series on Kalshi (settle, 2026-10-15)

Last revised: 2026-10-15

Research only. No orders were placed, and no keys were printed or written. Kalshi REST use was signed GETs at 1.4 req/s or
less: series and rules enumeration (677 series), market lists for 40 series, and public trades for KXRAIN. Everything
else came from local data (`data/kalshi/trades_fixed` to 2026-02-11) or from free public sources:

- tsa.gov;
- Wayback captures of gasprices.aaa.com;
- IEM (CLI and ASOS METAR);
- SPC storm reports;
- Copernicus Climate Pulse ERA5;
- NOAA NCEI.

## Verdict

**No new edge. The weather temperature lead does not generalise.** In every series tested, the market already prices
the fastest free public proxy. It also prices the already-published components of weekly and monthly aggregates. Where a
residual mispricing exists, it is the familiar 95-99c favourite residue, and its volume is tiny.

| Rank | Series (volume) | Structure tested | Result |
|---|---|---|---|
| 1 | **KXRAIN** daily multi-city rain (55.7M contracts in 3 months; the biggest weather series) | METAR measurable / trace vs CLI; **trace counts as 0** | Proxy = settlement (MEAS 452/452 YES, NONE 1317/1317 NO, trace-only 220/222 NO). The market is at 0.98-0.99 *before* the first public measurable METAR. The trace-rule trap was mispriced in the first 6 weeks after launch (train), then calibrated (test NO taker −$357 to −$664/yr). Decided-YES taker: +$40-100/yr, avg deployed < $18. |
| 2 | KXAAAGASM / W (49M contracts in the tape) | Published AAA dailies, nowcast for a Monday / month-end value | The market beats the nowcast (test Brier W 0.113 vs 0.148; M 0.060 vs 0.076). The train-best taker loses out of sample (-$1.2k/yr at $10k, CI < 0). |
| 3 | KXRAINNYC (21.9M; ended 2026-07, **trace counted YES**) | METAR state vs CLI incl. trace | Proxy = settlement (trace-or-wx days 111/111 YES). Only $624 of below-95c YES offers were ever lifted after a signal. |
| 4 | KXTSAW (5.5M incl. TSAW) | Published daily TSA, nowcast for the weekly average | Model ≈ market (test Brier 0.096 vs 0.100). Train-selected taker test +$371/yr, CI [-29, 969], avg deployed $4. |
| 5 | KXHMONTH / RANGE (3.3M) | ERA5 (public ~10 days before NOAA) vs the NOAA settlement | The market beats the ERA5 model (Brier 0.057 vs 0.110). |
| 6 | Monthly rain + snow (16 series, ~8M) | Month-to-date CLI already above the strike | 380/381 won. The market was already at 0.95-0.99. +$5/yr. |
| 7 | KXSPOTIFYW (1.8M) | Published daily #1s vs the weekly #1 | A daily-#1 sweep after 2+ days wins 100%, but it trades at 0.94-0.98. +$1/yr. |

**Headline $/yr.**

| Capital | $1k | $2.5k | $5k | $10k |
|---|---|---|---|---|
| Best honest number | ≈ $0 | ≈ $0 | ≈ $0 | ≈ $0 |

- No tested rule is positive out of sample with CI > 0, apart from tiny-deployment rules (monthly decided-YES: +$5/yr; KXRAIN decided-YES: +$40-100/yr, CI > 0, avg deployed < $18).
- Average deployed capital is below $10 everywhere except KXAAAGASM, where it is $100-470 and the rule loses.
- Sharpe and DSR are not meaningful. This workstream ran about 140 grid cells, on top of the ~8,000 repo-wide trials.
- **OOS status:** negative or null for every series.
- **Verdict: rejected.** The temperature 5-min-feed lead (`wx_5min_feed_edge_taker`) depends on a feature peculiar to it:
  the *settlement* value (the 6-hour METAR max) is systematically *lower* than the fast public feed's max. No other series
  examined has a fast public proxy that is biased against the official value. Where a fast proxy exists (METAR precip,
  ERA5, published AAA / TSA / Spotify / CLI dailies), it is unbiased and already priced.

## 1. Enumeration (`settle_enumerate.py`, `settle_fetch_markets.py`)

**Coverage.**
- 13,880 open events in 4,582 series on 2026-10-14.
- Of these, 1,726 series are active outside Sports, Crypto, Politics and Elections.
- 677 were prioritised: tape volume ≥ 50k contracts, or a data-release keyword. For each, `/series` (settlement sources)
  and one open market's `rules_primary` / `rules_secondary` were stored. Files: `data/vendor/settle/series_rules.parquet`
  and `series_rules_summary.csv`.
- Settlement sources by series count:

| Source | Series |
|---|---|
| The Weather Company | 80 |
| BLS | 55 |
| NY Fed (claims / EIA) | 39 |
| AAA | 23 |
| Spotify | 20 |
| Billboard | 16 |
| Netflix | 10 |
| NOAA | 7 |
| Google Trends | 6 |
| YouTube Charts | 8 |
| Luminate | 4 |
| Rotten Tomatoes | 3 |

**What the rules say for the candidate list**, and whether a faster public proxy exists:

| Series | Settlement source / value | Timing | Rounding / revisions | Faster public proxy? |
|---|---|---|---|---|
| KXRAIN (new 2026-07-16, ~25-34 cities/day) | TWC "CLIxxx" daily precip > 0; **"Trace (T) and missing are counted as 0"** | Closes 05:00Z (end of the Eastern climate day; Central/Mountain/Pacific close 1-3 h before their LST day ends); settles ~11:30Z | Exp. value = IEM CLI on 98.4% of markets (the rest are station/station-ID quirks) | METAR hourly/special precip groups + present weather (free, obs + ~5 min). It equals settlement (section 3) |
| KXRAINNYC (ended 2026-07-15) | NWS CLI Central Park precip > 0; **T resolves YES** | Closes 23:59 ET; settles 12:00Z | Fallback is the weather.gov time series | Same METAR proxy; equals settlement |
| KXRAIN*M, *SNOWM (monthly) | CLI / TWC monthly total > strike (station can change by month: Chicago moved MDW→ORD) | Closes at month end | — | Daily CLI each morning (published components) |
| KXTEMP*H (hourly temperature at hour H) | "Kalshi Weather Index" (multi-station Synoptic, minute resolution, weekly-calibrated offsets) | Closes at H | 2 dp °F | **Kalshi publishes the index live** (API live-data `get-weather-index`), so no proxy gap is possible |
| KXAAAGASD / W / M, MAXM / MINM | AAA national average regular on date D (4 dp since 2026) | Closes 23:59 ET on D-1; AAA updates ~03:00-04:00 ET D | Settlement = published value | GasBuddy real-time (free live, no free history); OPIS (paid) |
| KXTSAW | TSA weekly average (Mon-Sun), **first publication** | Closes Sun 23:59 ET; settles after Monday's 09:00 ET release | TSA revised 2023-25 weeks up by 0.1-0.4% later (21 strike flips vs today's table); none in 2026 | Daily TSA, published Mon-Fri by 09:00 ET (published components) |
| KXEIACRUDEW (new, ~180 contracts/day) | EIA commercial crude, first publication | Closes Wed 10:29 ET | Revisions ignored | API (American Petroleum Institute) Tuesday 16:30 ET headline, widely reported free. Volume too small to matter |
| KXJOBLESSCLAIMS / KXCONTCLAIMS | DOL advance figure | Closes Thu 08:29 ET | Revisions ignored | None (no state-level data before release) |
| KXCPI* / KXPCE* / KXUSGASCPI | BLS / BEA / FRED first print, 1 dp | Closes before 08:30 ET | — | None before the release. Rounding of YoY from index levels is deterministic after the release, but the markets close before it |
| KXHMONTH / KXHMONTHRANGE / KXGTEMP | NOAA NCEI (first release) / NASA GISS | Close at the NOAA/GISS release (~10th-16th of the next month) | NOAA revises later (Aug 2026: 1.32 settled vs 1.31 now) | ERA5 daily (Climate Pulse, 2 days behind); Copernicus bulletin ~6th-8th |
| KXTORNADO | SPC "Preliminary Report Summary" monthly count | Closes at month end | Settled count < today's revised SPC daily files (Dec 2024: 90 vs 145) | SPC filtered daily reports (real time). This is the same source, so there is no gap; a PIT history is not recoverable (files are revised) |
| KXNETFLIXRANK* | Netflix Tudum weekly US/Global top 10 (Mon-Sun views), published Tuesday | Closes Mon 23:59 ET | — | FlixPatrol daily (in-app daily top 10). Cloudflare-blocked; Wayback has ~30% of days (blocker) |
| KXSPOTIFYD / W | Spotify Charts daily / weekly #1 (USA) | Daily: closes 23:59 ET on chart date, published next afternoon | — | kworb / in-app play counts (live); published daily charts for the weekly series |
| KXTOPSONG / KXTOPALBUM / KXALBUMSALES | Billboard (Luminate) | Weekly | — | HITS Daily Double / Talk of the Charts projections (free websites, no archive API) |
| KXRT / KXRTTV | Rotten Tomatoes score at a timestamp | — | — | The source itself is live, so no gap |
| KXAPPRANKFREE (ended 2025-11) | App Store rank at a timestamp | — | — | Same source, live |
| KXRANKLISTGOOGLESEARCH (annual) | Google Year in Search | Annual | — | Google Trends (live, different metric) |
| Box office | No active series on 2026-10-14 | — | — | — |
| Mentions | Transcript of an event (already audited: `kalshi_mention_no_maker` rejected) | — | — | — |

## 2. Rank (volume × plausibility of a proxy-vs-settlement gap)

The volume tape (`series_volume_tape.csv`) covers 2025-02 to 2026-02. For new series, volume comes from the API
`volume_fp`. Ranking:

1. KXRAIN.
2. AAA gas.
3. KXRAINNYC.
4. KXSPOTIFYD / W.
5. KXNETFLIXRANK.
6. KXTSAW.
7. Monthly rain / snow.
8. KXHMONTH.
9. KXTORNADO.
10. The rest.

Netflix was dropped for data (blocker). KXSPOTIFYD needs a play-count history that is not free. A generic late-window
screen (`settle_screen.py`, `screen_late_window.csv`) found no series where 50-95c favourites in the final 1-48 h win
far above price with a CI excluding 0 across horizons. The ≥ 95c bands win ~100% everywhere (+1-2c per contract on
print prices), which is the known favourite-maker effect.

## 3. Daily rain - KXRAIN (`settle_rain_multi.py`) and KXRAINNYC (`settle_rain.py`)

**KXRAIN: proxy vs settlement, 1,991 markets.** METAR states inside each station's LST climate day:
- MEAS: any report with p01i ≥ 0.01.
- TRACE_OR_WX: a trace, or a precip present-weather code, with no measurable report.
- NONE: neither.

| METAR state | Kalshi NO | Kalshi YES |
|---|---|---|
| MEAS | 0 | 452 |
| TRACE_OR_WX | 220 | 2 |
| NONE | 1,317 | 0 |

**Does the market price the proxy?** Measured on the public-trades tape: 2.03M prints, 55.3M contracts, 1,988 markets.

**S1 (YES decided).** On 449 days the first public METAR showed measurable precip (obs + 5 min) before the close.
- Every one resolved YES.
- Last print just before that METAR was public: median 0.99 (mean 0.93 ten minutes before; 0.979 at the moment it went
  public). The market moves on radar, 1-minute and 5-minute data before the public METAR.
- Taker that buys YES at the next YES-taker print (25% share):

| Split | Latency | $/yr at $1k | $/yr at $10k | CI at $10k | Avg deployed |
|---|---|---|---|---|---|
| Train | 1 min | +$18 | — | — | — |
| Test | 1 min | +$40 | +$100 | [32, 175] | < $18 |
| Test | 15 min | — | +$9 to +$31 | — | — |

- 0 losing fills. Capacity is the binding constraint: only 4-14 fills per 6 weeks.

**S2 (the trace-counts-as-0 trap).**
- In hindsight, trace or precip-wx-only days (221) resolved YES 0.9% of the time, while the last print one hour after
  the day's *last* precip report averaged 18c (median 8c).
- That last report is not knowable in real time, so a causal version was built (`settle_rain_trace.py`). At each hourly
  METAR, the station-day must have had precip wx or a trace, no measurable precip, and no precip report for ≥ 60 or
  ≥ 120 min. P(YES) is the train rate by hours-to-close.

| Split | Hours to close | Market last print | Realised YES rate |
|---|---|---|---|
| Train (Jul 16 – Aug 31) | 12-24 h | 0.46 | 0.28 |
| Train (Jul 16 – Aug 31) | 4-8 h | 0.22 | 0.13 |
| Test (Sep 1 – Oct 13) | 12-24 h | 0.31 | 0.33 |
| Test (Sep 1 – Oct 13) | 4-8 h | 0.10 | 0.10 |

- In train, the market overpriced YES. In test it is calibrated.
- The NO taker made +$134 to +$447/yr in train, but **−$357 to −$664/yr in test** (6 cells, every one negative;
  avg deployed $4-12).
- So the market learned the new trace rule within about 6 weeks of the series launch (2026-07-16). The mispricing was a
  launch transient, not a durable edge. The general lesson: watch the first weeks after any settlement-rule change.
  The micro scan found young series in general are not mispriced, but this was a rule change.

**S0 (no precip report all day).** 1,315 days, 0 YES. The last print 3 h before close averaged 2.7c.

**KXRAINNYC (tape 2025-01 → 2026-02, trace = YES).**
- METAR state vs CLI: TRACE_OR_WX → T on 61 of 64 days (3 measurable). MEAS → >0 on 243 of 245.
- All 111 trace/precip-wx signal days resolved YES.
- After a signal, the market's median last print was 0.98-0.99. YES offers below 95c lifted after a signal totalled
  4,302 contracts / $624 of gross edge in 13 months.
- Taker: +$3 to +$14/yr, avg deployed < $3.

## 4. AAA gas - partial information (`settle_aaa_wayback.py`, `settle_aaa.py`)

**Data.**
- AAA daily history 2024-05 → 2026-10 was rebuilt from 912 Wayback captures. Each capture gives current / yesterday /
  week-ago / month-ago values, combined with Kalshi settlement values.
- Update timing: 0 of 10 captures before 03:00 ET show the new date, and every capture from 04:00 ET does. Events are
  therefore timed at 04:30 ET.
- Settlement = the published value.

**Model.** AAA(T) = AAA(D) + h·b·(3-day mean change) + e, with b = momentum and sd(e | h) fit on publications before
2025-07. Test sd(h): 1.8c at h=1, 9.4c at h=7, 17c at h=14.

**Results.**

| Series | Test Brier (model vs market) | Market reaction at the AAA update |
|---|---|---|
| KXAAAGASW | 0.148 vs 0.113 | Slope 0.06 within 2 h |
| KXAAAGASM | 0.076 vs 0.060 | Slope 0.02 within 2 h |

- The market moves *before* the AAA update. It carries real-time station-price information that the published dailies
  lag.
- KXAAAGASW: every train cell is negative.
- KXAAAGASM: the train-best cell (L=5 min, θ=10c) made +$564/yr at $10k in train (CI [53, 1095]). In test (the 2026 spike
  window) it made **−$1,171/yr, CI [−2,260, −200]**.

## 5. TSA - partial information (`settle_tsa.py`)

**Publication model.** Day d is assumed public at 09:15 ET on the next business day (TSA: "Monday through Friday by
9 a.m."). The weekend is published Monday.

**Nowcast.** Published days, plus pax(d−364) × the 14-day YoY ratio for unpublished days. The error sd on the weekly
average is 2.0% with 3 days unknown and 4.5% with 7 unknown (train). Strikes are 50k apart, about 2%.

**Results.**
- Brier, model vs market: pre-release train 0.125 vs 0.087; test 0.096 vs 0.100.
- The market reprices only 9-12% of the model's move within 4 h of a release. Since it is no worse than the model, the
  release is mostly anticipated.
- Train-selected taker: test **+$371/yr at $1k, CI [−29, 969]**, avg deployed $4, 40 fills in 7 months. Capacity is
  capped by about 250k contracts/quarter of total volume.

**Settlement quirks.**
- First-print vs revised TSA numbers differ by 0.1-0.4% (2023-25), which flipped 21 strike outcomes.
- KXTSAW-25APR20 settled on 2.189M while today's TSA table gives 2.467M (an Easter-week data glitch).

## 6. KXHMONTH / RANGE vs ERA5 (`settle_hmonth.py`)

**Proxy quality.**
- ERA5's monthly mean is public about 3 days after month end, roughly 7-10 days before NOAA.
- Mapping: NOAA = 0.614 + 0.899·ERA5 anomaly, resid sd 0.045 °C.
- "Record month" agreement since 2015: 129 of 141 months.

**Results.** The market beats the proxy model at every horizon. At ERA5 completion, Brier is 0.057 (market) vs 0.110
(model) over 26 events. In 2025 the market already priced 'no record' at 1-3c. Taker: −$14 to +$36/yr.

## 7. Monthly cumulative 'decided' (`settle_monthly.py`) and Spotify weekly (`settle_spotify.py`)

- **Monthly rain and snow (16 series).**
  - Of 381 strikes passed by the published month-to-date CLI total (signal at CLI issue + 10 min), 380 won. The one
    loss is a station mapping change.
  - The market's last price at the signal averaged 0.95-0.99.
  - Only 1,879 YES contracts were lifted afterwards in two years: +$5/yr.
- **KXSPOTIFYW.**
  - The song that swept every published daily #1 (from KXSPOTIFYD settlements) won the week 100% of the time once 2 or
    more days were known.
  - It was priced at 0.94-0.98 at the daily publication.
  - Taker: 3 fills, +$1/yr.

## 8. Not tested (compact)

| Series | Reason |
|---|---|
| Netflix (FlixPatrol daily) | Blocked: Cloudflare; Wayback has ~30% of days |
| Billboard / album sales | No free archived projections |
| KXTORNADO | No PIT counts: SPC daily files are revised after the fact, and the settled preliminary count is lower |
| KXTEMP*H | Kalshi publishes the settlement index live |
| Claims / CPI / EIA | No public proxy before the close, or negligible volume |
| RT / app ranks | The settlement source is itself live |

## Blockers & how to unblock

| Need | What it would test | Cost / action |
|---|---|---|
| GasBuddy daily national-average history (the fast AAA proxy) | Whether KXAAAGASD/W asks lag GasBuddy moves | Not free as history. A free forward log from the GasBuddy site (scrape, ToS risk), or OPIS (paid, ~$ thousands/yr). Not recommended: the market already beats the published-AAA nowcast. |
| FlixPatrol daily Netflix top 10 | KXNETFLIXRANK* partial information (7.7M contracts) | FlixPatrol API is paid (≈ €30-200/mo). The free site is Cloudflare-protected. A free option: a forward daily log of Netflix's own in-app "Top 10 in the U.S. Today" via a logged-in browser, done by the user (ToS grey). |
| KXRAIN live books | Depth at the first-measurable-METAR moment | Free: add `KXRAIN` to `config/recorder.yaml` (the recorder currently skips it). Low priority given the price is already 0.98 before the METAR. |
| Synoptic Data API | KXTEMP*H station-level replication | Free tier needs a token. Not needed: Kalshi publishes the index itself. |
| HITS Daily Double / Luminate projections | Billboard album / song partial information | Luminate is paid (enterprise). HDD is free to read but has no archive. A forward log would take months. |

## Files

- **Scripts:** `scripts/research/settle_*.py`
  - Shared: common, enumerate, fetch_markets, fetch_trades.
  - Data fetches: tsa_fetch, cli_fetch, asos_fetch, spc_fetch, aaa_wayback.
  - Analyses: tsa, aaa, rain, rain_multi, hmonth, monthly, spotify, screen.
- **Data:** `data/vendor/settle/`
  - Rules: `series_rules*.`
  - Markets and trades: `markets/`, `trades/KXRAIN.parquet`
  - Sources: `tsa_daily`, `aaa_snapshots`, `aaa_daily`, `cli/`, `asos/`, `era5/`, `spc_torn_daily`
  - Panels and grids: `*_panel`, `*_grid.csv`
- **Ledger:** `scripts/research/ledger_entries/settle.yaml`
