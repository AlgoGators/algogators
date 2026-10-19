# NOWC: Kalshi initial jobless claims (KXJOBLESSCLAIMS) vs a free DOL-data nowcast

Last revised: 2026-10-15

Research only; no orders. Pre-registration: docs/NOWC_CLAIMS_PREREG.md (written before any price comparison).

## Verdict
**Rejected. No edge.** The free-data nowcast is no better than a random walk. It beats stale 2025 Kalshi prices
in train, but loses to the market on the 2026-04..10 holdout (Brier 0.101 vs 0.080). Every taker variant loses out of
sample. The causal maker loses in all three fill models. Average deployment is $1-$410, far below the $1k bar.

| rule (chosen on train) | test $/yr at $1k / $2.5k / $5k / $10k | avg deployed | test CI ($1k) | test Sharpe |
|---|---|---|---|---|
| Taker T_wed12_m3 (train best: +$464/yr, Sharpe 2.7, avg dep $3) | -459 / -869 / -993 / -993 | $1-3 | [-893, -128] | -2.7 |
| Maker Fa (1c inside, 50%) | -291 / -421 / -3,729 / -12,296 | $84-409 | [-1,735, +1,166] | -0.5 |
| Maker Fb (trade-through, 25%) | -628 / -3,061 / -6,661 / -10,601 | $61-173 | [-2,305, +996] | -1.0 |
| Maker Fc (join queue 10%) | -1,094 / -3,355 / -4,886 / -5,152 | $50-82 | [-2,682, +547] | -1.8 |

DSR: test Sharpe is negative, so DSR = 0 against both the 6 own taker variants and the ~8,000 repo trials. The
best train maker (Fa, Sharpe 1.8) has DSR 0.19 even in-sample against 8,000 trials.

## Series facts (verified from the API)
- KXJOBLESSCLAIMS: weekly ladder of 9-11 "at least K" strikes (5k spacing). It settles on the DOL advance SA
  headline (rules cite dol.gov/ui/data.pdf). Markets close 08:25 ET on release day, before the 08:30 print.
  There are 59 settled events, 2025-06-12..2026-10-08, with no events Oct-Dec 2025. The older JOBLESS (2021-22) and
  KXJOBLESS series are inactive.
- Size: median event notional is about $12k (about $16k since April 2026); recent events run 6-37k contracts. Recorder
  snapshot of the 2026-10-15 event: the 195k strike is quoted 46/47c, 190k 62/69c, 200k 20/27c, with roughly
  100-1,000 contracts near the touch. The brief's ~$6.5k/event is the right order of magnitude.
- Fee: quadratic; taker ceil(0.07 C p(1-p)), no maker fee.

## What the free data can and cannot give before Thursday 08:30
- **Seasonal factors are published ahead.** The DOL weekly-claims XML (oui.doleta.gov/unemploy/wkclaims/report.asp,
  POST level=us) lists SFs through 2027-04. So SA = NSA / SF is mechanical once NSA is known.
- **NSA is not known before the release.** National and state advance figures come out in the same 08:30 release.
  The state detail available beforehand is the previous week's, and the market already prices it. I found no free,
  systematic pre-release state source; some states post their own figures irregularly, but not as a consistent feed.
- **Forecasting NSA from seasonal ratios is worse than a random walk.** The model was NSA_{W-1} times the median
  past-year W/W-1 ratio, divided by SF_W. On 574 training weeks (2012-19, 2022-24) its log RMSE was 9.0%, against
  4.2% for the SA random walk. The fitted blend gives that model weight 0 (0.6 RW + 0.4 four-week mean, RMSE 3.98%).
  On 55 real-time 2025-26 weeks, the median absolute error was 7.4k for the blend and 7.0k for the RW.
- **Real-time inputs.** Real-time advance values (2024-26) were parsed from 132 archived DOL press-release PDFs
  (oui.doleta.gov/unemploy/archive.asp); 6 PDFs failed and fell back to current vintage.
- **Google Trends was not tested.** pytrends is not installed, and installing it is outside my write namespace.
  Weekly Trends values are also re-normalised on every pull, so a point-in-time series would need daily archived
  pulls going forward. This is listed as a gap, not as a blocker.

## Calibration (last print before the decision time)
| | train Brier mkt / model | test Brier mkt / model | test log-loss mkt / model |
|---|---|---|---|
| Wed 12:00 ET | 0.136 / 0.129 (231 mkts, 31 events) | 0.080 / 0.101 (265, 28) | 0.283 / 0.334 |
| Thu 08:00 ET | 0.135 / 0.126 | 0.080 / 0.101 | 0.275 / 0.342 |

The train advantage came from thin 2025 books. By 2026 the market is sharper than any public-data random walk, and it
presumably also uses consensus and other information. Disclosure: the calibration script printed the test Brier at
the same time as the train numbers. It is a pre-registered diagnostic and was not used to select a rule.

## Gaps
- Taker fills use the first opposite-side taker print within 60 minutes after the decision time, i.e. a real ask.
  Depth beyond that print is unknown.
- Maker fills are print-based causal models (audit_flb_common Fa/Fb/Fc, Q0 = 50); there are no historical books.
- Only 28 test events.

## Blockers & how to unblock
- None that money would fix. Consensus forecasts (Bloomberg/Reuters polls) are paid, but the market already beats a
  free nowcast; a consensus feed would at best tell us what the market already knows.
- Re-test only if a free, pre-release, state-level NSA source appears (e.g. a set of large states publishing daily
  initial claims) that covers at least 40% of national NSA, with history.

## Files
scripts/research/nowc_kalshi_fetch.py, nowc_dol_parse.py, nowc_claims_model.py, nowc_claims_eval.py;
data/vendor/nowc/{jobless_markets.parquet, jobless_trades/, jobless_tape_trades.parquet, dol/, claims_*.csv|json|parquet}.
