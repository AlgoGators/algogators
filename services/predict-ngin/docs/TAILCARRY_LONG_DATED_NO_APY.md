# TAILCARRY: long-dated tail-NO carry with Kalshi APY (user brief #6)

Last revised: 2026-10-16

Round 6, 2026-10-16. Research only: no orders and no keys. Scripts: `scripts/research/tailcarry_*.py`; tests:
`tests/unit/test_tailcarry.py`; outputs: `data/vendor/tailcarry/`.

## Question

The brief is third-party, so every claim in it is verified here. It says:

- Rest NO bids at ask - 1c in high-gap categories (Becker maker-taker gap: Entertainment 4.79pp, Media 7.28pp,
  World Events 7.32pp; vs Finance 0.17pp, politics 1.02pp).
- Enter when YES has traded at or below 8c for at least 7 days and more than 30 days remain.
- Hold to settlement, or exit if YES rises above 20c.
- Size at 2% of equity per event and 25% per category.
- The position is "paid twice": by the longshot bias (plus a long-maturity settlement-wedge discount, Gebele &
  Matthes arXiv 2605.31431), and by Kalshi's APY on the marked value of open positions.

Prior work tested 91-99c favourites mostly days from close (`kalshi_flb_maker_econ_97`, `fin_C_fvflb_97`,
`mkgap_favourite_residue_maker`), and Entertainment/World NO at 10-50c (`micro_category_no_maker`, rejected). New
here: a horizon of more than 30 days, the 7-day tail state, the high-gap categories, and APY.

## Pre-registration (written 2026-10-16, before any fill, PnL or outcome was computed)

**Universe.**
- Kalshi series whose `/series` category is Entertainment, World or Social. This is group **H**, the high-gap
  group the brief names. Becker's "Media" has no Kalshi category; its nearest is Mentions, which is excluded
  (studied in round 6, and it carries MNPI risk).
- Politics and Elections form group **L**. This is the low-gap comparison and does not drive the verdict.
- Every ticker in the tape (`trades_fixed` + `trades_fixed_ext` via `kalshi_tape.tape_sql`) with at least one
  YES print at or below 8c is included, whatever its final state: yes, no, scalar, voided, or still open.
- Outcomes come from `/historical/markets` and `/markets` as of 2026-10-16.
- The NO payoff is 1 for `no`, 0 for `yes`, and 1 - `settlement_value` otherwise. This covers last-fair-price
  settlements and voids.

**Rule** (`tailcarry_common.py`, unit-tested). State is taken from prints in earlier seconds only.
- **Tail state:** the current run of YES prints at or below 8c began at least 7 days ago, and the market's first
  print is at least 7 days old.
- **Horizon:** more than 30 days remain to `latest_expiration_time`.
  - `latest_expiration_time` is fixed at listing.
  - The actual `close_time` is not used. Kalshi moves it earlier when a market is decided early, mostly when the
    YES event happens, so filtering on it would drop exactly the losing entries.
  - Using actual close as the horizon is reported once, only as a look-ahead diagnostic.
- **Entry:** a resting NO bid at the causal Fa / Fb / Fc level from `src/data/fills.py`. Fa (1c inside the last
  hit, joining on a 1c spread) is the tape analogue of "ask - 1c".
- **Fc queue:** Q0 is the median NO-bid depth in tail books (best YES bid at or below 8c) from the live recorder
  (`tailcarry_books.py`): Entertainment 2,775, Politics 2,134, Elections 49, World/Social 1,000 (default).
- **Fees:** maker fee on maker-fee series from 2025-04-01. 154 Entertainment markets are on
  `quadratic_with_maker_fees`; no fee change touching these categories falls inside the tape window.
- **Exit:** the first YES print at or above 21c in a later second than the fill triggers it. We sell NO as a taker
  at the first print at least 60 s later, at 100 - yes - 1c, paying the taker fee. Otherwise we hold to settlement.

**Split.** By fill time.
- Train: 2021-07-01 to 2025-07-01.
- Test: 2025-07-01 to 2026-03-02 (the tape end).
- Positions are held to settlement whenever that falls. Outcomes are known to 2026-10-16.
- The rule is fixed by the brief, so train selects nothing. It is a sanity check only.
- Test results are reported once.

**Statistics.**
- Edge per contract is computed on engine fills with each fill capped at 100 contracts. It is net of the maker
  fee, and of the exit price and taker fee when an exit occurs.
- The CI is an event-cluster bootstrap: one event is one cluster, so a multi-outcome event counts once.
- **Portfolio:**
  - caps $1k / $5k / $10k, replenish mode;
  - 2% of the cap per event and 25% per Kalshi category, both on gross cost;
  - cash use is the collateral locked after Kalshi netting (MECNET, mutually exclusive events:
    locked = cost - (sum q - max q)), with no netting as a comparison.
- **Reported:**
  - $/yr = PnL / years of the entry window;
  - average deployed = locked capital-years / entry-window years;
  - annualised return on deployed = PnL / locked capital-years, with and without APY.
- **APY** accrues on the position's cost (a proxy for its last-trade mark) under an assumed rate path:
  - 0 before 2024-10-10;
  - 4.05% from 2024-10-10;
  - 3.75% from 2025-09-17;
  - 3.50% from 2025-12-10.

  Kalshi pays the same APY on idle cash, so APY is not incremental to the strategy.
- Also reported: event-cluster CIs on $/yr, the daily Sharpe (PnL on settlement days), the DSR at N = 8,500 repo
  trials plus this study's 24 cells, top-5 event share, and the per-event effect of multi-outcome clustering.

**Verdict (H group, test, $10k cap):**
- `promising` needs all of these:
  - Fb and Fc edge per contract > 0;
  - the Fb event-cluster CI lower bound > 0;
  - average deployed (netted) of at least $1k;
  - DSR of at least 0.9;
  - top-5 events below 50% of PnL.
- `capacity_capped` if it is positive with a CI above 0 but fails only on capacity.
- `unproven` if it is positive but the CI includes 0.
- `rejected` otherwise.

## Data

| Item | Path | Coverage |
|---|---|---|
| Tape slice | `data/vendor/tailcarry/tape.parquet` (from `kalshi_tape.tape_sql`) | 14.3M prints, 41.2k tickers in the 5 categories, 2021-07-07 to 2026-03-01 (the ext tape currently ends there) |
| Candidates | `cand.parquet` | 29,388 tickers with a YES print <= 8c (Entertainment 19,150, Politics 6,193, Elections 3,780, World 214, Social 51) |
| Market metadata | `markets.parquet` (`/historical/markets`, `/markets`, fetched 2026-10-16) | 0 missing. 27,533 finalized (no 25,813, yes 1,698, scalar 22), 1,855 still active (marked at the current last price). No voided markets in the set. |
| Events | `events.parquet` (`/events/{t}`) | 6,945 events. 4,483 are mutually exclusive with `MECNET` netting, 268 are `DIRECNET`. |
| Fees | `fee_changes.json` (`/series/fee_changes?show_historical=true`) | No maker-fee change touches these series in the tape window. The 2026-08-20 change is after the tape. |
| Tail queue Q0 | `books_q0.csv` (recorder, 2026-10-12+) | Entertainment 2,775 (73 markets), Politics 2,134, Elections 49, World/Social 1,000 (default) |

The Gebele & Matthes paper (arXiv 2605.31431) exists. It recovers an annualised settlement wedge from persistent
near-certain contracts, which supports the "maturity discount" premise in principle. The paper is not re-tested
here. The Kalshi APY page (help.kalshi.com, read 2026-10-16) gives 3.50%. Interest accrues daily on the **net
portfolio value: available cash plus positions at last traded prices**. Positions therefore earn exactly what idle
cash earns.

## Results

Notation: H = Entertainment/World/Social, L = Politics/Elections. P1 is the pre-registered horizon. Edge per
contract is net of fees and exits. CIs are event-cluster bootstrap. Files: `edge_{train,test}.csv`,
`port_{train,test}.csv`, `diag_*.csv`.

### Edge per contract (P1)

| split | group | Fa c [CI] | Fb c [CI] | Fc c [CI] | fills (Fb) | events (Fb) | exit share |
|---|---|---|---|---|---|---|---|
| train | H | -1.14 [-2.52, -0.08] | -1.43 [-2.99, -0.31] | -1.11 [-2.95, -0.01] | 4,127 | 305 | 17-20% |
| train | L | -0.85 [-2.42, 0.72] | -0.61 [-1.71, 0.40] | +0.11 [-1.75, 1.45] | 6,229 | 413 | 19-28% |
| **test** | **H** | **-2.14 [-4.78, -0.51]** | **-2.98 [-6.00, -0.95]** | **-0.80 [-3.19, 0.66]** | 13,262 | 516 | 20-26% |
| test | L | -0.18 [-1.21, 0.82] | -0.39 [-1.61, 0.67] | +0.90 [0.12, 1.72] | 8,678 | 724 | 5-19% |

- **By category (test, Fb):**
  - Entertainment -3.01c [-5.90, -0.99];
  - World +2.6c (11 events);
  - Social +3.9c (3 events);
  - Politics -0.01c;
  - Elections -0.85c.

  World and Social are too thin to matter.
- **Average entry price.** The average NO entry is 96-97c. Losses at settlement are rare: 0.4-1.4% of held H fills
  settle YES.
- **Where the losses come from.** They are the exits:
  - 20-26% of H fills see a YES print at or above 21c;
  - those are sold at a median NO of 87-91c;
  - **87-93% of the exited markets then settle NO**.

  The stop sells transient spikes at the worst price.

### Portfolio (test, P1, netting on; 2%/event and 25%/category on gross cost)

| model | group | cap | $/yr [CI] | c/contract | avg deployed (netted / gross) | peak | ret on deployed: no APY / with APY | Sharpe | DSR |
|---|---|---|---|---|---|---|---|---|---|
| Fa | H | 1k | -22 [-101, 42] | -0.84 | 395 / 416 | 372 | -5.5% / -1.6% | -0.57 | 0 |
| Fb | H | 1k | -15 [-71, 34] | -0.52 | 285 / 317 | 307 | -5.3% / -1.2% | -0.48 | 0 |
| Fc | H | 1k | -7 [-55, 32] | -0.28 | 275 / 314 | 296 | -2.7% / +1.6% | -0.21 | 0 |
| Fa | H | 5k | -56 [-461, 209] | -0.41 | 1,720 / 1,963 | 1,729 | -3.2% / +1.0% | -0.29 | 0 |
| Fb | H | 5k | -102 [-354, 114] | -0.80 | 1,320 / 1,628 | 1,411 | -7.7% / -3.1% | -0.62 | 0 |
| Fc | H | 5k | -62 [-259, 99] | -0.54 | 1,221 / 1,508 | 1,335 | -5.1% / -0.5% | -0.50 | 0 |
| Fa | H | 10k | -380 [-1,169, 175] | -1.43 | 3,135 / 3,821 | 3,333 | -12.1% / -7.6% | -0.94 | 0 |
| **Fb** | **H** | **10k** | **-207 [-624, 147]** | -0.92 | **2,455 / 3,052** | 2,730 | **-8.4% / -3.8%** | -0.70 | 0 |
| Fc | H | 10k | -13 [-284, 213] | -0.06 | 2,340 / 2,884 | 2,653 | -0.6% / +4.0% | -0.08 | 0 |
| Fb | L | 10k | -499 [-1,033, -1] | -2.19 | 6,990 / 8,270 | 4,480 | -7.1% / -2.8% | -1.38 | 0 |
| Fc | L | 10k | +47 [-206, 284] | +0.21 | 6,250 / 8,188 | 4,107 | +0.8% / +5.5% | +0.19 | 0 |

- **Train.** Train is negative in every H cell: Fb at $10k is -$222/yr, CI [-407, -59].
- **Average deployed.** This is the steady-state value: locked capital-years divided by the entry-window years. It
  can exceed the peak inside the short test window because positions run past the window end.
- **DSR.** The DSR is 0 in every cell, computed at N = 8,536 against the dispersion of this study's cells.

### APY

- APY adds +3.5 to +4.6pp to the annualised return on deployed capital: $107-142/yr at $10k in H, $299-328/yr in L.
- Kalshi accrues the same rate on idle cash. Against a cash benchmark, **APY is a wash**. It is not a second source
  of return, and the "paid twice" premise fails.
- Positions do not earn more than cash, so there is also no reason to expect a settlement-wedge discount on Kalshi
  from foregone interest. A wedge would have to come from collateral lock-up or attention instead.

### Multi-outcome event clustering

- **Event structure.** 70-80% of H fills are in mutually exclusive (MECNET) events, at 3-4 tickers per event in
  test. Treating the event as one bet matters:
  - the 2% cap binds per event;
  - the worst single event loses $64-184 at $10k (near a full 2% bet in Fa);
  - event-cluster and ticker-cluster CIs are similar in width (Fb H test: event [-6.00, -0.95], ticker
    [-6.22, -0.98]), so there is little extra dependence beyond the event.
- **Collateral netting.** Netting cuts average locked capital by 15-20% (Fb H $10k: 2,455 vs 3,052 gross). It
  raises the return on deployed capital but does not change PnL.

### Look-ahead and robustness checks

- **Horizon proxy.** P2 (more than 30 days to the ACTUAL close) is look-ahead. It makes H worse, not better: Fb
  test -4.07c vs -2.98c. The `latest_expiration_time` proxy therefore does not flatter the result.
- **Hold to settlement, no exit (post hoc, not pre-registered; `diag_*.csv`).**
  - H: train -0.0 to +0.7c, test -0.7 to 0.0c (CIs about [-4, +2]), so the tail NO edge in high-gap categories is
    about zero.
  - L: train +2.4 to +2.8c (CI > 0), test +1.2 to +1.3c (CI > 0 under all three models). But holds last 177-282
    days, 30-65% of test fills are in markets still open (marked at last price), and the sized book at $10k makes
    -$451 (Fa), -$13 (Fb) and +$218 (Fc) per year with CIs spanning 0.
  - +1.2c over a ~0.6-year hold is about 2%/yr, below the cash APY.
- **Long horizon.** Edge for held H fills that settled within 30 days of entry vs later (look-ahead split): test
  later-settling fills are worse (-1.4 to -2.2c) than earlier ones (+0.2 to +1.0c). The long horizon does not help
  in H.
- **Causality.** All state comes from earlier seconds (unit tests in `tests/unit/test_tailcarry.py`, 8 passing).
  Exits need a later-second trigger and a print at least 60 s later.

## Limitations

- Fills come from the tape (no book). Q0 comes from 4 days of recorder tail books.
- Rules text, `latest_expiration_time` and `mutually_exclusive` are as of 2026-10-16. They are the current
  versions, not point-in-time.
- APY change dates between the published points (4.05% at 2024-10-10, 3.50% in 2026) are assumed.
- The tape ends 2026-03-01, so exits after that date cannot be observed and those positions are held. 1,855
  markets are still open and marked at the current last price.
- No voided markets fell in the candidate set. The 22 scalar settlements are handled via `settlement_value`.
- Maker fees apply on 154 Entertainment markets from 2025-04-01 (repo convention). The 2026-08-20 change post-dates
  the tape.

## Trials

24 pre-registered cells: 2 groups × 3 models × (3 caps + no-netting), plus the P2 and category diagnostics. 12
post-hoc hold cells bring the total to N = 8,500 + 36 = 8,536.

## Verdict

**`rejected`** for the brief's rule in the high-gap categories:
- edge per contract is negative under Fa, Fb and Fc in both splits;
- the Fb CI excludes 0 on the negative side;
- the sized book loses at $1k, $5k and $10k, with DSR 0.

Capacity is not the problem: it deploys $2.3-3.1k at $10k. The stop rule destroys value, because it sells spikes
that mostly settle NO. Without it the edge is about 0. APY is not incremental, because idle cash earns it too. The
post-hoc Politics/Elections hold-to-settlement observation is positive per contract but not after sizing. It is
logged as rejected, with a re-test after the Nov-2026 settlements.
