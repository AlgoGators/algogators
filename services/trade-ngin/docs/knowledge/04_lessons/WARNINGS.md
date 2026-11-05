# Warnings

This file lists the ways the code, the data and production can go wrong without anything saying so: a table that lacks
days it should hold, a run that exits 0 and stored nothing, a key that loads and does nothing, a test that passes with
nothing run. Each warning says how it shows, its cause, what it does to the book or the data, the rule that prevents it,
and where it is in the tracked code, docs, PRs or issues. They are grouped by area (data; sizing and the optimiser;
rolls; statistics and stored results; runs and dates; configuration; equities; repository; production), and within a
group the costliest consequence comes first. Each group ends with a table of smaller warnings. A date is given only
where it tells you which stored data a warning affects.

How a change is proved is in [HOW_WE_VERIFY.md](HOW_WE_VERIFY.md); the steps for each job are in
[RUNBOOKS.md](../05_operations/RUNBOOKS.md). Paths are in AlgoGators/algogators main, with the engine under
`services/trade-ngin/`. Commits on the old repository are cited as
`AlgoGators/trade-ngin@<id>`; its pull requests stay there, and its open issues moved to `AlgoGators/algogators` with new
numbers. The numbers below only let other files point at a warning.

## 1. Data

**1.1 A symbol-day missing from the continuous futures tables changes every symbol's book.**
- Shows as: a result moves after a data change that touched one symbol, or one run gives two results on two copies of
  the database (one 2-year run read -10.4282% on one copy and -1.56821% on another).
- Cause: the optimiser builds one covariance on the dates every symbol shares, so one symbol's missing date removes that
  date for all of them. Production's continuous tables lacked 476 symbol-days that its per-contract table held: SI, ZC
  and ZS from 2025-03-24 to 2025-04-04, Sundays, thin days and market-wide days. Until 2026-10-12 the daily pull started
  at the whole table's latest date plus one, so a symbol-day missed once was never fetched again. The vendor had every
  bar checked; the holes were in ingestion.
- Effect: the gold and silver micros' year from 2025-10-05 read +6.20% on data with holes and -9.25% on the real
  contracts, on the same settings; aligning the calendars alone gave +7.22%. Every measured number on the unfilled data
  rests on the holes.
- Rule: every hole is filled before a measured run, and a run names the data it ran on by a fingerprint (row counts and
  md5 per table) taken before the run. Two runs are compared only on the same data and one build. Covariance returns
  are paired by date, never by count: the same number of bars is not the same dates, and the optimiser paired the last
  returns by count until AlgoGators/trade-ngin@4c349ad5 (2026-09-24). A gap between two runs that cannot be explained
  is not accepted.
- Where: `services/trade-ngin/scripts/futures_release/sql/data_fingerprint.sql`; the production fill in
  `services/trade-ngin/scripts/futures_release/README.md` (AlgoGators/algogators#124, AlgoGators/algogators#180); a
  proposed change to the daily pull that would fill `.v.0` holes after each pull, the pull request
  AlgoGators/algogators#87; the fill rule in
  [ROLLS_AND_DATA.md](../01_the_book/ROLLS_AND_DATA.md) and `services/trade-ngin/docs/FUTURES_ROLLS.md`.

**1.2 Contract sizes in the wrong unit make a contract 100 times too large.**
- Shows as: a book far below its leverage target (a live run held about 6 positions, about $305K notional on $500K,
  0.61x), with instruments that never reach one contract.
- Cause: the price table and the metadata used different units: cents per bushel against bushels, dollars per
  hundredweight against pounds, points against face value. For ZC, ZW, ZS, KE, ZL, GF, LE, HE, ZN and UB,
  `contract_size x price` came to 100 times the real notional: ZC computed at $2,140,000 a contract instead of
  $21,400, so its raw position was 0.009 and rounded to 0 for ever.
- Effect: only 12 of 36 instruments could reach one contract; 20 of 36 could after the fix, adding about $1,064,000 of
  positions (+2.13x on $500K). Those instruments could not trade until the metadata migration of 2026-05-02.
- Rule: `Contract Size` means dollars per 1.0 of quoted price, and notional is contracts x price x that value. Notional
  per contract is checked against the real contract whenever metadata is added or changed.
- Where: `services/trade-ngin/docs/DATA_SOURCES_OF_TRUTH.md`.

**1.3 A source that returns no rows looks exactly like a quiet day.**
- Shows as: nothing. An applier that asks for a 14-day window and gets 0 rows behaves as it would on a fortnight with no
  events.
- Cause: feeds stop without an alarm. `equities_data.corporate_action` holds no price-restating or termination event
  after 2025-08-29. The futures ingest stopped on 2026-08-06 (33 symbols' last bar), with the equity re-adjustment job,
  and about 127 of 700 daily-bar equity symbols stopped at the same time.
- Effect: equity splits, quantities and the dividend ledger were dead on the live path, and backtests that use corporate
  actions are right only for windows ending by 2025-08-29. Equity replay windows must end by 2026-08-06. The futures
  book's consequence is warning 5.1.
- Rule: no rows from a source that should have rows is checked against the source's newest date. Price-restating events
  come from the per-bar columns, written on the ex-date bar and never restated; the vendor's adjusted columns are not
  read. The deal-terms feed is frozen and is treated as a known data block.
- Where: `services/trade-ngin/docs/CORP_ACTIONS_DATA_BOUNDARY.md`; `services/trade-ngin/docs/DATA_SOURCES_OF_TRUTH.md`.

**1.4 Duplicate bars enter every estimate as zero returns.**
- Shows as: a bar count too high on a copy of the database, or a change meant to move no number failing its proof.
- Cause: `futures_data.ohlcv_1d` once had no key although its writer declared one, and held 970 extra rows on 306
  symbol-dates in two windows (2025-10-06 to 2025-11-12, and 2026-02-03 to 2026-02-05). Production's tables now carry
  primary keys on (time, symbol); older copies, and the comment in the loader, still describe the keyless table.
- Effect: each duplicate entered the trend sleeve as a zero return inside its volatility, its moving averages, the
  optimiser covariance, the risk gate's window and the cost model. Removing them moved the book (one chain day went from
  6 executions to 4).
- Rule: the bar loader returns one bar per symbol per date (highest volume, then lowest close, open, high, low) and logs
  what it dropped. On any copy, count with `COUNT(DISTINCT ...)`.
- Where: `kFuturesBarKeepOrder`, `services/trade-ngin/include/trade_ngin/data/market_data_utils.hpp`;
  AlgoGators/trade-ngin#98.

**1.5 A stand-in series carries its source's history, not the traded contract's.**
- Shows as: a result on an instrument that does not survive the switch to that instrument's own data.
- Cause: MES, MNQ and MYM carried the E-mini's bars from 2010 although they listed on 2019-05-06. Gold and silver micros
  built from GC and SI bars carried those contracts' calendars and holes, and each leg of a listing switch was priced on
  the micro's thin volume (an SI exit at $55.38 against about $17).
- Effect: the equity micros earned about +51k to +54k gross that could not have been traded, costed on E-mini volume,
  and booked false roll legs on 2026-02-24 when the source changed mid-quarter. On the 16-year window the book read +22.15% on
  the stand-ins and +47.19% on the real contracts.
- Rule: a contract trades only from its listing date, with the full-size root before it, and each leg of a listing
  switch is priced on its own contract.
- Where: `services/trade-ngin/src/data/listing_dates.cpp`; [ROLLS_AND_DATA.md](../01_the_book/ROLLS_AND_DATA.md).

| Shows as | Cause and effect | Rule | Where |
|---|---|---|---|
| A query for `MBT` returns nothing | Symbols in `futures_data.ohlcv_1d` carry `.v.0` | Query `MBT.v.0` | `services/trade-ngin/docs/DATA_SOURCES_OF_TRUTH.md` |
| A bar exists and is not used | The date grid is the vendor's: with per-contract rolls on, `ohlcv_1d` and `ohlcv_1d_raw` still supply the dates. Metadata trading hours do not say how the vendor dates a bar | Read the date grid from the tables, not from trading hours | `services/trade-ngin/include/trade_ngin/data/per_contract_source.hpp` |
| A weekend count is wrong | Weekend bars differ by table and period. In the per-contract table on 2026-10-23: Saturday bars on MBT only (39, from 2026-05-30), Sunday bars on 33 roots. In `futures_data.ohlcv_1d` for the weekends of 2026-09-26 to 10-18: Saturday on MBT only, Sunday on 26 of 36 | State the table and date with any weekend count. A simulated chain across a weekend carries weekend bars as production has them | `services/trade-ngin/docs/LIVE_RUN_CYCLE.md`; [ROLLS_AND_DATA.md](../01_the_book/ROLLS_AND_DATA.md) |
| A compare keyed by stamp lists extra fills | Before the first Saturday bar, a backtest stamps a Friday bar's trades on Sunday and a live chain on Saturday | Compare by bar | [ROLLS_AND_DATA.md](../01_the_book/ROLLS_AND_DATA.md) |
| An equity replay fails | The equity bar of 2026-08-07 is absent | Keep equity replay windows on or before 2026-08-06 | `services/trade-ngin/docs/DATA_SOURCES_OF_TRUTH.md` |

## 2. Sizing and the optimiser

**2.1 A guard written for positive totals dropped every short.**
- Shows as: no negative position anywhere in stored rows while the signals have negative values.
- Cause: AlgoGators/trade-ngin@ed2b6fd5 (2025-12-25) replaced the rounding step with a share distribution guarded by
  `total_original > 1e-8`, so a negative contribution got no share and every short was stored as 0.
- Effect: every build with that line runs long-only, which no setting selects, and production's stored rows from
  2025-12-25 hold no shorts: 0 shorts in 1,695 chain rows, against 37.1% negative signal values (2,406 of 6,480). The
  line is gone from main. The trend forecast is signed, and a negative forecast is meant to size a short; with the
  guard every negative forecast sized to zero. Because the line was later removed inside a roll commit, that commit
  appeared to cost -109k to -281k by window when almost all of it was the shorts switching on.
- Rule: shorts are on. A test of a sizing step holds a short, and a change that moves the book is measured on its own
  commit, never bundled with another.
- Where: `services/trade-ngin/docs/OPTIMIZER_AND_RISK_DESIGN.md`; [CARVER_DEPARTURES.md](../02_decisions/CARVER_DEPARTURES.md).

**2.2 A risk scale applied in a loop compounds.**
- Shows as: a book cut deeper than its risk limits ask; a cut that the next step puts back.
- Cause: the old five-lap loop ran the gate on the book the previous lap had already scaled, so the scale applied again
  on each lap. Its history was capped at 252 rows across 36 symbols, which is 2 to 7 distinct dates, so the correlation
  term pinned on 90.5% of binding laps. A cut in whole contracts handed to the next lap was re-optimised with the buffer
  anchored on the held book, so a cut inside the no-trade band was put back, and the gate, having recorded the cut,
  reported none.
- Effect: on a 608-day backtest the gate bound on 57% of days, a requested scale of 0.832 became a realised 0.691, 24.3%
  of gross contracts went and 337 positions were zeroed. 14 of 342 cut days on the 2-year delivered above the request
  (2 fully undone), and 158 of 1,233 on the 5-year (25 fully undone).
- Rule: one pass. The overlay reads the target, applies its scalar once, and nothing feeds back. A history window is
  counted in dates, not rows. An unmet limit is stored and marked over limit by hold, never left unnoticed.
- Where: `services/trade-ngin/docs/OPTIMIZER_AND_RISK_DESIGN.md`; `services/trade-ngin/src/optimization/one_pass.cpp`.

**2.3 On a multi-strategy book the allocation can be applied twice.**
- Shows as: a book that realises the sum of the squared allocations (58% at 70/30, 50% at 50/50, about 14% volatility on
  a 25% target), while Sharpe does not move, so it reads as strategies that are not aggressive. A run log shows capital
  350,000 and 150,000 on one 500,000 book.
- Cause: each strategy sized for its own slice of capital, and the portfolio layer multiplied the quantities by the
  allocation again. At 70/30 a ZN target of 1.22 contracts shipped 0.69.
- Effect: every multi-strategy run before the fix ran at the wrong exposure, and no single-strategy study could show it.
- Rule: book positions are the sum over strategies. `get_portfolio_positions()` still applies the allocation again and
  is kept for legacy paths; the equity runner still calls it, which is harmless only at allocation 1.0, so it is never
  used on a multi-strategy book. A change to how strategies combine is tested with two strategies in one book.
- Where: AlgoGators/trade-ngin@35d64c74; `services/trade-ngin/src/portfolio/README.md`.

**2.4 At 500,000 a good return can be one contract.**
- Shows as: a headline return that rests on one position.
- Cause: a full-size GC or SI contract is far larger than the book's target in it. The no-trade buffer's width is set by
  the largest one-contract risk among the names held or wanted, so a held lumpy contract sets its own band and stays (SI:
  tracking error 0.300 against a band of 0.306 that SI itself set).
- Effect: the 2-year returns of 62% to 66% were one GC contract held at 10 to 30 times its target (+212k). A run that
  read +98.88% was one SI contract held at a median 15 times its target, up to 118% of capital, for +$391k, 79% of the
  run. The live year to 2026-10-14 read +42.3%, of which one SI contract made +181,138 of +211,714; without silver the
  year was +6.1%.
- Rule: a return is attributed by contract before it is read as the system's, and a whole-contract book is judged first
  on how closely it tracks the unrounded book. Gold and silver trade as micros. The shipped buffer still lets a held
  contract set its width.
- Where: [LEVERS.md](../03_tuning_and_results/LEVERS.md); [THE_BOOK.md](../01_the_book/THE_BOOK.md).

**2.5 Weights computed over rows the book does not trade starve the rest.**
- Shows as: a book sized under its risk target with no single cause in the sizing code.
- Cause: the weights counted every row of the contract metadata, so four equity index contracts that were never traded
  (ES, NQ, YM, RTY) each took a 1.79% share. Separately, a symbol filter that erased from the list while looping over it
  dropped MES from all four futures runners (35 symbols traded of 36).
- Effect: the traded names' weights summed to about 84%, so the book was sized about 16% under its risk target.
- Rule: weights are computed over the contract-metadata rows whose symbol has bars in the continuous table, not over
  the configured universe, so a symbol with bars that the book does not trade still takes a share. A symbol is added to
  or removed from the bar table and the universe together, and the count of symbols a run loaded is checked against the
  universe.
- Where: `get_weights` in `services/trade-ngin/src/strategy/trend_following.cpp`; AlgoGators/trade-ngin@0c623ad8.

**2.6 A fixed small buffer, or a search that starts from zero, churns a whole-contract book.**
- Shows as: fills many times higher than the book's target and pairs of trades that reverse within two days.
- Cause: a no-trade buffer of 1% tracking error is smaller than one contract of many names on a 500,000 book: one ZN
  contract is 1.1 buffers, one UB 3.0, one GC 12.9. A search that starts from zero each day lets correlated names (ZN,
  ZT, ZF, UB) trade places from one day to the next.
- Effect: a reference model run outside the engine on real bars made 2,107 fills and 588 two-day reversal pairs a year
  on the 2-year window at fee-only costs; with a buffer scaled to a contract's size and a search from the held book it
  made 562 and 16.
- Rule: the buffer scales to a contract's size and the search starts from the held book. Any change to either is run on
  real bars before it is built.
- Where: `services/trade-ngin/docs/OPTIMIZER_AND_RISK_DESIGN.md`, sections 7.3 and 7.4.

**2.7 A factor rounded to eight decimals stores fractional contracts.**
- Shows as: execution quantities of 0.99999999 or 1.00000003, with commissions of 1.49999999, in `backtest.executions`
  on a whole-contract book.
- Cause: a level advanced by a factor rounded to eight decimals, so a request that should equal 1.0 missed it in the
  eighth decimal and the book was nudged by 1e-8 contracts.
- Effect: 20 non-integer quantities in stored rows, which a check of which tables moved does not see.
- Rule: stored quantities on a whole-contract book are whole, and a comparison checks the form of values as well as
  which tables moved.
- Where: `backtest.executions`; `services/trade-ngin/docs/OPTIMIZER_AND_RISK_DESIGN.md` (stored positions are whole contracts).

| Shows as | Cause and effect | Rule | Where |
|---|---|---|---|
| Costs too high on a book of two sleeves | From 2026-09-25 to 2026-10-09 costs were charged per sleeve, gross of netting, and the stored netting adjustment was read by nothing: 21,617 dollars (about 4.3 points) over 16 years on the two-sleeve test book | Every total uses the cost after netting, which is what the account pays | AlgoGators/trade-ngin#178 |

## 3. Rolls

**3.1 `.v.0` is a raw splice: a roll's gap is not a market move.**
- Shows as: a return that jumps on a roll; a short side that loses in contango; a P&L derived from close changes that
  disagrees with the engine.
- Cause: `.v.0` is the vendor's volume-ranked front contract at each contract's own raw price, so on a roll the close
  jumps by the gap between two contracts. In contango a short is charged at each roll step. The header comment of
  `market_data_utils.hpp` still says futures prices are already back-adjusted; no code adjusts them.
- Effect: on books built on the splice, roll gaps entered P&L, the signals and the volatility estimate as market moves.
  The first estimate was about -170k of the shorts' -211k over 16 years; a later design audit put the artefact at -67k
  to -123k, the rest being genuine short-side performance. Pricing the unrounded book on raw close changes charged every
  roll step as P&L: it read about -5k over 16 years where the exact method gave +258k, and the difference reconciled to
  roll steps (about -266k) year by year.
- Rule: the series is built from the contracts the book holds (`per_contract_rolls` true, as on every futures
  template): a roll bar's return is the old contract's own move, and only the gap between contracts stays out of P&L.
  An attribution prices each day on the held contract's own move (close x r/(1+r)) and checks roll days by hand. Never
  take a log or percentage of an adjusted level: CL, HO and RB are at or below zero in 2020. A code comment is a claim
  to check against the data.
- Where: `services/trade-ngin/docs/FUTURES_ROLLS.md`; `services/trade-ngin/docs/DATA_SOURCES_OF_TRUTH.md` section 4.2;
  `services/trade-ngin/include/trade_ngin/data/market_data_utils.hpp`; [ROLLS_AND_DATA.md](../01_the_book/ROLLS_AND_DATA.md).

**3.2 Rolling when the vendor rolls holds many contracts into their last trading day.**
- Shows as: a held contract at or past its last trading day or first notice.
- Cause: the vendor's series rolls too late for a live book on 17 of 36 symbols: with no business day left before the
  last trading day on 6A, 6B, 6E, 6J, 6M, 6N and 6S; past the last trading day on MBT (65 of 65 rolls) and 6L (122 of
  164); on or past first notice on most rolls of GC, SI, HG, PL, ZN, ZF, ZT and UB. A live run trades about two business
  days after the vendor's date, later still.
- Effect: a book that follows the vendor would hold those 17 symbols into the last trading day or delivery notice on
  most of their rolls.
- Rule: the book rolls by its own rule: the first of the vendor's forward switch once the next bar keeps it, a volume
  crossover in the last five CME trading days before the deadline, or the last actionable bar. A roll rule is measured
  on the full history against the last trading day and first notice before it is adopted.
- Where: `services/trade-ngin/include/trade_ngin/data/contract_roll_rule.hpp`; `services/trade-ngin/docs/FUTURES_ROLLS.md`.

**3.3 The vendor reuses instrument ids, so an expiration joined on the id alone is another contract's.**
- Shows as: a contract that expires before its own last bar, or, invisibly, later than it really does.
- Cause: ids are reused across years and products, and in a join on the id alone the last record wins.
- Effect: 825 of 4,982 contracts got another instrument's date: 359 expired before their own last bar and 466 were later
  than the real date, which no check against the last bar can see. Production was corrected on 2026-10-25 (140,855
  rows); a copy of the database taken before that carries the wrong dates.
- Rule: definitions are matched on instrument id, raw symbol and dates. A contract's identity is (root, raw symbol,
  expiration), and an expiration outside the delivery window is refused. A check is tested in both directions: one that
  catches early dates says nothing about late ones.
- Where: `ContractKey`, `services/trade-ngin/include/trade_ngin/data/contract_bars.hpp`; AlgoGators/algogators#78;
  [ROLLS_AND_DATA.md](../01_the_book/ROLLS_AND_DATA.md).

**3.4 Leaving the whole change bar out of P&L also drops the held contract's real move that day.**
- Shows as: reference returns that differ from returns that book the roll bar.
- Cause: the rule of no P&L on a change bar removes the jump between contracts and also the held contract's own move on
  that bar.
- Effect: measured from stored runs on the vendor path, the 2-year would read about 49.5% against a stored 61.68%, and
  the 5-year 12.8% against 22.24%; one day dominated (GC on 2026-01-30, long one contract, about -56,000). Any figure
  from a run without per-contract rolls carries this gap.
- Rule: with per-contract rolls on, the held contract's move on the roll bar is booked and only the gap between contracts
  stays out of P&L. A known caveat travels with the number.
- Where: `services/trade-ngin/docs/FUTURES_ROLLS.md`; [ROLLS_AND_DATA.md](../01_the_book/ROLLS_AND_DATA.md).

| Shows as | Cause and effect | Rule | Where |
|---|---|---|---|
| A re-run charges a roll again | A re-run read its own stale legs and charged again a roll that late bars had completed; a symbol that rolled twice also failed | Re-run logic works from stored rows and is tested on a symbol that rolls twice | AlgoGators/trade-ngin#238 |

## 4. Statistics and stored results

**4.1 A whole-contract book's result moves by tens of points with its start date.**
- Shows as: two windows that differ only by start date and give very different returns; a change inside warm-up that
  moves a long window's result.
- Cause: at 500,000 a whole-contract book turns on a few lumpy positions (warning 2.4), so when it enters them decides
  much of the result.
- Effect: a one-week later start on identical prices moved the 2-year from 61.68% to 57.01%; five starts two weeks apart
  span 21 to 27 points on the 2-year and 31 to 63 on the 10-year; a 10-year of 129% and one of 60% differ only by start
  date. Feeding the backtest's window classifier the prefix live already has changed 2, 1 and 14 bars, all inside
  warm-up, and moved the 5-year from +9.20% to -6.96%. Carver's standard error of the Sharpe ratio (AFTS, strategy
  three) is about 0.26 for a 16-year Sharpe near 0.30, a separate quantity from the spread across start dates, and no
  difference between the sets of the second tuning round was significant (the largest paired t-statistic was 1.14), so
  choices rest on mechanism, not on one number.
- Rule: a result is read as a distribution over start dates, with path noise from at least three perturbations. Noise
  bands come from start-date probes, never from other starting equities. A change to dates, warm-up or identity is
  assumed to move stored numbers until a run shows it does not.
- Where: [RESULTS.md](../03_tuning_and_results/RESULTS.md); AlgoGators/trade-ngin#158.

**4.2 Stray rows can move the annualisation anchor.**
- Shows as: annualised figures that are far too small for a young book.
- Cause: live statistics annualise over the trading days since an anchor. A rule that takes the earlier of the metadata
  row and the earliest stored result lets any stray row move it: `BASE_PORTFOLIO` held two backdated `live_results` rows
  (dates 2025-01-29 and 2025-02-06), and that rule annualised a 164-day stream over 450 days. No runner writes
  `trading.strategy_trading_days_metadata`.
- Effect: every annualised figure of the book is wrong.
- Rule: the metadata row is authoritative. `assess_trading_days_anchor` returns it whenever one exists, and a runner only
  warns when it disagrees with the book's first stored day. A new live book gets its row by hand before its first run,
  checked with `trading.get_trading_days`. A wrong row is corrected in the data, not by the runner.
- Where: `services/trade-ngin/include/trade_ngin/live/trading_days_anchor.hpp`;
  `services/trade-ngin/docs/LIVE_RUN_CYCLE.md` section 7.

**4.3 A comparison that cannot fail passes.**
- Shows as: identity verdicts that hold whatever the inputs.
- Cause: a compare that reads a subset of the columns, keys a table on a column that does not exist, or orders by less
  than a total key passes books that differ and fails books that are the same. Specific traps in the stored tables:
  `run_metadata.date_inserted` is the insert's wall clock, so a hash of it never reproduces; an unordered `SELECT *` on
  `metadata.contract_metadata` and a stored-book read without `ORDER BY` change the last digit of a few cells between
  two runs of one binary; `backtest.signals` has no writer and compares equal in every run; the positions and BASE
  export CSVs have no fixed row order; `compare_csv_dirs.py` exits 0 on two folders with no CSV; a database connection
  in a zone other than UTC renders the stored instants in that zone.
- Effect: identity claims that rest on partial evidence.
- Rule: compare every cell of every table, keyed and ordered by every column; refuse a table whose key is not in the
  header; treat insert clocks and surrogate keys as provenance; compare unordered exports as multisets; refuse a folder
  without its completion marker or with no stored table; set the database time zone to UTC. A compare carries a control
  that must fail.
- Where: `PROVENANCE` in `services/trade-ngin/scripts/live_mode_gate/compare_csv_dirs.py`;
  `services/trade-ngin/docs/SYSTEM_ARCHITECTURE.md`; [HOW_WE_VERIFY.md](HOW_WE_VERIFY.md).

**4.4 Regime results on the regime branch are in-sample.**
- Shows as: regime confidence, breaks and stress checks that look like expected live behaviour.
- Cause: on `regime_detection_updated` (on `AlgoGators/trade-ngin`) the models are fit once on 2020 to 2025, so every
  fixture result is in-sample. The structural model's training used a backward fill and a centred smoother, both of
  which read later data; removing them moved macro confidence from 0.064 to 0.077 and removed 27 of 105 breaks. The
  Markov-switching factor model picks one regime on 100.0% of the sample at confidence 0.016, while its test checks one
  synthetic bar. The one stress check of four that passed was a single Sunday stub bar (2022-10-09): about one daily
  return in six is a partial day.
- Effect: no regime number on that branch is expected live behaviour.
- Rule: a regime result counts only out of sample, a check passed by a stub bar is not a pass, and a change that shifts
  the baseline lands in one controlled migration measured against the baselines on the branch.
- Where: AlgoGators/algogators#128.

**4.5 A fix to one route of a stored column leaves the others.**
- Shows as: a corrected column that still violates on some days; a repair that zeroes a correct row.
- Cause: the realized P&L column is written by more than one route. Two fixes that each fixed one route still left 20 of
  90, then 32 of 90 days violating. A settlement repair zeroed a correct results row when a positions rewrite failed
  (2,235.125 lost in the reproduction).
- Effect: stored rows wrong in a new way after each fix.
- Rule: every route that writes a column is fixed in one change, and a repair is tested on its own failure path with a
  test that fails without the fix.
- Where: `services/trade-ngin/src/live/live_pnl_manager.cpp`.

| Shows as | Cause and effect | Rule | Where |
|---|---|---|---|
| Statistics columns are NULL on older rows | The statistics columns came with migrations 036 (live) and 037 (backtest) with no backfill, the e-mail columns with 041 the same way | Read a NULL on an old row as not computed, never as zero | Headers of `services/trade-ngin/migrations/036_live_results_statistics.sql`, `037_backtest_statistics.sql`, `041_book_email_columns.sql` |
| A ratio is NULL on a new row | A ratio with no denominator (a Sharpe with no volatility, a Calmar with no drawdown) has no value; it used to be stored as 0 or 999 | Treat NULL as no value | `services/trade-ngin/include/trade_ngin/core/no_value.hpp` |
| A NULL reads as 0 downstream | Some readers keep the old rule that a NULL cell is 0.0 | Check the reader before trusting a zero | `read_double_or_zero_on_null`, `services/trade-ngin/src/live/live_data_loader.cpp` |
| Live and backtest statistics differ by a factor of 100 | Live columns are in percent, backtest columns in fractions | Check each column's unit before comparing | `services/trade-ngin/docs/TREND_FOLLOWING_SYSTEM.md`, the statistics tables |
| A Sharpe near zero for a good year | Annualising by `252/days` read about 0.002 instead of about 0.88 for a 7.85% return | Annualise by the grid's row frequency (311.0574 a year for futures, 252 for equities), and record the factor the figures actually use | `services/trade-ngin/src/backtest/README.md` |
| A second backtest to the same end date is refused at start | The run id is the strategies and the end date, so a second run to the same end date carries the same id. Without the refusal its `backtest.final_positions` rows would replace the first run's date by date and its `backtest.results` insert would fail on the key, leaving the first run's results beside the second run's positions | Export and delete the stored run from the five backtest tables first; reset `backtest.*` for each side of a comparison and count the rows after the reset | `services/trade-ngin/include/trade_ngin/backtest/stored_run_refusal.hpp` |
| A 2-year window traded 1.2 years | A lookback counts warm-up and ends where the run is told to end; a 16-year window trades about 15.2 years because the first 256 rows are warm-up | Label a window by its first traded day and check the traded span against its name | AlgoGators/algogators#153 |
| Live and backtest disagree on most rows | Pairing row D with row D offsets them (138 of 184 differ) | Pair by signal date, and seed the live chain from the backtest's own book | [HOW_WE_VERIFY.md](HOW_WE_VERIFY.md) |

## 5. Runs and dates

**5.1 A held symbol's stale feed refuses only on today's date.**
- Shows as: a book that carries flat for weeks while the run exits 0.
- Cause: the feed-hole refusal refuses only when the run date is the host's date. On a past date, and on a future date
  such as a Saturday run building Monday, it warns and holds the symbol at its last price. Before the refusal existed,
  the freshness refusals fired only on dateless runs, which production never makes.
- Effect: when the futures feed stopped (warning 1.3), production warned, carried the book, stored and exited 0.
  Inferred from stored marks against the bars now present, 138 of 370 run dates with holdings booked a daily P&L within
  5 dollars of zero; from 2026-08-07 to 2026-10-04 the book sat flat at about 475,000 for 59 run dates with 9 positions,
  with no catch-up when the feed resumed, roughly -10k unbooked.
- Rule: the two run modes (the host's date, a past date) are distinct, and a change is tested in the mode production
  runs; a run with no date prints its usage and exits 2. An un-held stale symbol is one warning; a held one stops the
  book for what it holds.
- Where: `rule_held_feed_holes`, `services/trade-ngin/include/trade_ngin/live/session_book_gate.hpp`;
  `services/trade-ngin/docs/LIVE_RUN_CYCLE.md`.

**5.2 A run can exit 0 after a write failed.**
- Shows as: a clean exit and missing or partial rows.
- Cause: some write failures are logged and the run carries on, and each looks clean because the insert path computes
  its identities self-consistently. The equity runner once wrote metric keys that `trading.live_results` does not have,
  so its results never persisted; a 62-character strategy id failed the store; a backtest that could not finish writing
  truncated its stored rows and still printed its success line. A fallback mark filled MYM at a 4-day-old 49,707 in a
  multi-day feed gap.
- Effect: unstored days with clean exits, while every internal check still reconciles.
- Rule: exit 0 is never evidence: the log is searched for `ERROR`, the row counts are checked, and every table the run
  writes is reconciled. Missing metadata is an error, with no fallback tables or prices.
- Where: exit codes in `services/trade-ngin/docs/LIVE_RUN_CYCLE.md`; AlgoGators/algogators#158 (a failed futures
  positions write); AlgoGators/algogators#160.

**5.3 Every date after one refusal is refused, and a middle date cannot be re-run alone.**
- Shows as: a chain of refusals after one bad date; stored numbers that revert, or rows from two runs side by side.
- Cause: the missed-run guard needs the previous date's stored run, and a refused date stores nothing, not even a
  `live_run_metadata` row. Each day's rows are built from the previous day's stored rows, so re-running one middle date
  rebuilds that day and leaves every later row on a book that no longer exists. Re-running single dates of an equity
  replay in place left a close executed while its row kept the pre-trade 87.418 shares, a Saturday row re-opening 57.412
  shares with no execution, and `total_realized` going back from -648.97 to -150.59.
- Effect: a stored series that no clean replay can reproduce.
- Rule: replay from the last stored date plus one, in order, one run per date, checking each exit code. Never re-run a
  middle date alone: restart from a reset point and run through to the end. Never insert placeholder rows.
- Where: `services/trade-ngin/docs/LIVE_RUN_CYCLE.md` section 11; [RUNBOOKS.md](../05_operations/RUNBOOKS.md).

**5.4 Parsing the run date at UTC midnight moves the book.**
- Shows as: a date change that looks clean on a UTC host, or under a time zone ahead of UTC.
- Cause: futures bars are keyed at 00:00 UTC and the backtest and live window ask for `time BETWEEN` two instants. The
  image sets `TZ=America/New_York`, while the servers and the database run UTC; the futures runner reads a date as
  local midnight and formats it back through UTC, so futures rows stamp 05:00Z all year and equities 00:00Z. Moving the
  futures run's instant from 05:00Z to 00:00Z, five hours back, puts the bar on the window's start edge inside the
  window: one extra bar per symbol. On the equity runner, UTC midnight of a
  Monday is Sunday evening in New York, and the shared consumers that read local time resolve the previous trading day
  to the Saturday before. Under Asia/Tokyo, ahead of UTC, the same code looks correct.
- Effect: on the futures runner a UTC parse moved 225 of 6,480 stored signal values on a ten-day chain (by up to 1.42 on
  a forecast that runs to about 20). On the equity runner it marked the book a day early (unrealized P&L off by 112, 153
  and 193 on AAPL, GOOGL and META on the first day), gave order ids the previous day's date, and stopped a replay after
  8 of 25 days. The equity runner parses its date as UTC midnight, so its held-symbol check never refuses on a host
  behind UTC.
- Rule: the run date stays at the host's local midnight, the image keeps `TZ` (the cron line and the wrapper's `date` are
  New York time), and date-frame tests pin both frames under `TZ=America/New_York`. A change to how a run date is parsed
  is run on the real host before it lands. Sums across books join on the date, not the timestamp.
- Where: `services/trade-ngin/Dockerfile`, runtime stage (since AlgoGators/trade-ngin@f9340384); the `RunDateFrame`
  tests; `PreviousTradingDayFrameTest` in `services/trade-ngin/tests/core/test_holiday_checker.cpp`;
  `services/trade-ngin/docs/LIVE_RUN_CYCLE.md` section 1.1 (section 1.2 for the equity runner's held-symbol check);
  AlgoGators/algogators#163.

**5.5 A backtest's end date is the wall clock.**
- Shows as: two runs of the same binary on the same settings three hours apart differ (about 6 executions, about $39 of
  cost and 4 trades), and backtest figures differ between hosts in different zones.
- Cause: backtest binaries take no date argument; their end date is `now()` unless the test-only key
  `backtest.frozen_end_date` is set.
- Effect: any difference between two backtests below that band is noise.
- Rule: set `backtest.frozen_end_date` and check that the run logs `FROZEN BACKTEST WINDOW`; compare figures only between
  hosts in the same zone.
- Where: AlgoGators/algogators#153 (explicit dates with the warm-up loaded before the window).

**5.6 A changed book identity starts a second book.**
- Shows as: a new 500,000 book that starts with exit 0 where the old book should have continued.
- Cause: a run finds its stored book by its identity, and a changed id finds none and starts fresh.
- Effect: the old book's chain stops silently and a second one starts.
- Rule: a book's identity is never changed under a running book; a run that finds no stored book where one is expected
  is treated as a failure.
- Where: AlgoGators/algogators#157.

| Shows as | Cause and effect | Rule | Where |
|---|---|---|---|
| A live run with no date prints its usage and exits 2 | A dateless run runs, stores and sends nothing. Before AlgoGators/trade-ngin#205 a bare invocation turned the e-mail on, and any older build still does | Always pass the date | `kDatelessRunExitCode`, `services/trade-ngin/include/trade_ngin/live/run_args.hpp` |
| `--date --send-email 2026-10-21` sends nothing | `--date` takes the next argument whatever it is, and the flag text is then skipped | Give the date first: `<date> --send-email`, as cron does | Argument parsing in `services/trade-ngin/apps/strategies/live_portfolio_conservative.cpp` |
| Two dates on one command line | The futures runners exit 1; the equity runner keeps the last date it parses and gives no warning | Pass one date, and on the equity runner check the logged date | `two_run_dates_message`, `services/trade-ngin/include/trade_ngin/live/run_args.hpp`; `services/trade-ngin/apps/strategies/live_equity_mean_reversion.cpp` |
| A flat day looks like no run | A flat book writes no position rows, so zero rows means either | Read that day's log and `live_results` | The `trading.positions` writer in the live runners |
| Rows of two runs collide | Two runners on one database race on row keys | One runner per database at a time | `services/trade-ngin/docs/LIVE_RUN_CYCLE.md` |
| Ad hoc SQL shows every date a day early | The engine pins UTC on connect; `psql` does not, and a database brought up in America/New_York gives false date joins | Set `PGTZ=UTC` for any connection that reads dates, and run every test database in UTC | `session_time_zone_statement`, `services/trade-ngin/include/trade_ngin/data/postgres_database.hpp`; `services/trade-ngin/scripts/futures_release/lib.zsh` |

## 6. Configuration

**6.1 A key can load and do nothing.**
- Shows as: a setting changed and nothing moves.
- Cause: a rename removed `max_gross_leverage` from the risk config and left the config files carrying it, so 4.0 and
  2.0 loaded without error and did nothing; a config migration of 2026-02-06 silently changed the conservative
  backtest's reserve from 0.15 to 0.10. Keys that load and are not read today: `reserve_capital_pct` and
  `reserve_capital` (a warning says so); `covariance_history_prices` and `covariance_stale_dates`, which shape only the
  generic optimiser step, while the futures search takes 756 closes and a 5-date stale rule from constants;
  `sizing_mode` and `starting_capital`, required and checked, while the sizing reads `initial_capital`;
  `instrument_id_relabels` with `per_contract_rolls` on (a log line says it is not read); `to_emails_production`.
- Effect: a book set by a value nobody chose, or tuning of a value the code never reads.
- Rule: a key the code stops reading is refused at load, as the retired one-pass keys are on a futures book (table
  below), never left to load silently. Not every key above follows this: `reserve_capital_pct` and `reserve_capital`
  only warn and load, and the others load with no word. The code path of a key is read before it is tuned.
- Where: `services/trade-ngin/src/core/config_loader.cpp`, the retired-key check and the reserve-key warning; `services/trade-ngin/src/optimization/one_pass.cpp`;
  `relabels_read` in `services/trade-ngin/src/data/listing_dates.cpp`; `services/trade-ngin/src/portfolio/README.md`.

**6.2 A limit stated below the target it bounds binds every day.**
- Shows as: a book permanently in `risk_exceeded`, and liquidations that look like correct risk control.
- Cause: `var_limit` was 0.10 under a 0.20 risk target.
- Effect: a book set by a cap, not by the strategy (the liquidation of 2025-11-18 was this). The limit was raised to 0.25
  on 2026-03-01 and the family of keys later retired.
- Rule: a limit is stated in the same units as the target it bounds, and above it. The risk number is checked before the
  market is blamed.
- Where: AlgoGators/trade-ngin@38c646d9; `services/trade-ngin/docs/RISK_MODULES.md`.

| Shows as | Cause and effect | Rule | Where |
|---|---|---|---|
| A futures config is refused at load, naming a key | A retired one-pass key (`tau`, `asymmetric_risk_buffer`, `buffer_size_factor` under `optimization`; `carver_buffer_floor`, `carver_buffer_position_factor`; a sleeve's `weight`, `max_symbol_concentration`, `use_position_buffering`) is refused on a futures book | Remove the key | `services/trade-ngin/src/core/config_loader.cpp` |
| Every runner dies at config load with `type_error.302` | `port` is a `std::string`, read from JSON as a string | Keep `port` a quoted JSON string; `num_connections` stays a number | `services/trade-ngin/include/trade_ngin/core/config_loader.hpp` |
| A config is refused for a missing risk key | A carver risk module needs `overlay_covariance` | Start from a current template | `services/trade-ngin/src/risk/risk_module_config.cpp` |

## 7. Equities

**7.1 A split applied before the price series reaches its ex-date fills at the wrong unit.**
- Shows as: a realized P&L far larger than the book.
- Cause: on BKNG's 25-for-1 ex-date (2026-04-06) the position was restated into post-split units while the price
  series, which ends at the last completed trading day, stayed in pre-split units.
- Effect: the run sold 248.799399 post-split shares at the pre-split 4,194.31, about $1,001,800 of realized P&L on a
  $100,000 book (the figure in the code comment), and the day's stored BKNG row carried realized P&L of $1,001,584, at
  exit 0, with every invariant of the run passing.
- Rule: a corporate action is applied only once the price series covers its ex-date; with no dedup row the next run
  reconsiders it.
- Where: `services/trade-ngin/apps/strategies/live_equity_mean_reversion.cpp`;
  `services/trade-ngin/tests/live/corp_actions/test_frame_horizon.cpp`; `services/trade-ngin/docs/CORP_ACTIONS_DATA_BOUNDARY.md`.

**7.2 An empty write can leave yesterday's book standing.**
- Shows as: the same sale repeated on every trading day.
- Cause: TMUS was fully sold on 2026-07-07, the day's write was empty, the save returned early, and a placeholder row
  from the corporate-action step survived as that day's book.
- Effect: the next run loaded it and sold 17.615626 TMUS again on every trading day from 2026-07-08 to 07-27: 17 phantom
  executions, with mark P&L booked on stock the book did not hold.
- Rule: today's position rows are cleared unconditionally, so an empty write cannot leave yesterday's book standing.
- Where: `services/trade-ngin/apps/strategies/live_equity_mean_reversion.cpp`.

**7.3 A book reset without its corporate-action dedup table skips a split.**
- Shows as: a loss on a split date after a reset and re-run.
- Cause: the table that records which corporate actions were applied (`trading.corp_action_applied`) was not reset with
  the book, so BKNG's split was skipped as already applied, against a previous-day row still in pre-split units (1.200601
  shares at 4,194.31). No guard fires: the basis-against-mark check inspects only actions that were applied.
- Effect: the post-split mark of 176.19 booked -4,824 of unrealized P&L. Deleting dedup rows automatically is not the
  fix: it is right only when the book was also reset, and would turn a plain re-run into a double application.
- Rule: the dedup table is reset together with positions, `live_results`, the equity curve and executions from the
  replay start date, or not at all. Each dedup row stores the run date that wrote it, and a run refuses to start behind
  rows written by a later pass.
- Where: `services/trade-ngin/src/live/corporate_actions_audit_log.cpp`;
  `services/trade-ngin/migrations/005_corp_action_applied_run_date.sql`.

**7.4 A change to shared code for equities can break the futures book.**
- Shows as: a futures replay that halts, or futures P&L counted twice, after an equity change.
- Cause: on a futures row `average_price` is the previous settlement close, so mark-to-market against it repeats the
  day's settlement. Removing the unconditional zero for futures `daily_unrealized_pnl` in
  `LivePnLManager::finalize_previous_day` counted a futures row's settlement move in both realized and unrealized P&L.
  Removing a date predicate from the shared `delete_stale_executions`, and a log-retention change that matched file
  names by bare prefix (so the retention of `live_trend` logs deleted the conservative runner's logs), did the same
  kind of damage.
- Effect: wrong futures P&L and lost logs from changes that never ran the futures book.
- Rule: a change to shared code is followed by a futures run before futures are called unchanged. Futures rows are
  settled: the day's settlement move is realized and unrealized stays 0. Log retention owns exact file names.
- Where: `services/trade-ngin/src/live/live_pnl_manager.cpp`; `services/trade-ngin/docs/LIVE_RUN_CYCLE.md`, the settled futures rows.

**7.5 A seed row under the wrong key never loads.**
- Shows as: a seeded position that seems to be traded away; a log line `Loaded 0 previous day positions`.
- Cause: seed positions written with `strategy_name` `MEAN_REVERSION` while the runner reads `EQUITY_MEAN_REVERSION`.
  The executions such runs print are the warm-up feed, whose `Generated execution` lines look like the day's trades.
- Effect: a run that starts flat while it appears to hold the seed.
- Rule: a seed row is keyed exactly as `load_positions_by_date` reads it (its strategy name, and a `last_update` of
  00:00:00 UTC), and a run's log is checked for the loaded count before its trades are read.
- Where: `load_positions_by_date`, `services/trade-ngin/include/trade_ngin/data/postgres_database.hpp`;
  [THE_EQUITY_BOOK.md](../01_the_book/THE_EQUITY_BOOK.md).

**7.6 No code caps the equity book's gross exposure.**
- Shows as: nothing. A book run as a cash account holds stock worth more than its capital, and the run exits 0.
- Cause: the strategy sizes each name on its own, at capital x `position_size` x a volatility scalar capped at 2.0
  (`calculate_position_size`), so one name can take up to 0.2 of capital at the template's `position_size` of 0.1.
  The only cap is the per-symbol share count in `position_limits` (`execution.position_limit_live`, 500 shares in the
  template defaults, and `position_limit_backtest`, 1,000, in a backtest), which bounds shares, not value. The book's risk module is type `none`, no strategy leverage check
  runs on the equity path (the strategy's `on_data` does not call it), and the start-up guard on `max_gross_leverage` reads the config value, not the book.
- Effect: a full set of the template's ten symbols can size up to 2.0 times capital, and each added symbol raises that
  bound by up to 0.2. Cash only is the account's stance, because the engine does not model a margin account and shorting is
  not built; on the stored record the book's exposure stayed under 1.0,
  which is a measurement, not a bound.
- Rule: whoever adds symbols or raises `position_size` checks symbols x `position_size` x 2.0 against 1.0, and reads
  the book's gross exposure on stored rows after the change.
- Where: `calculate_position_size`, `services/trade-ngin/src/strategy/mean_reversion.cpp`;
  `services/trade-ngin/config_template/portfolios/equity_mr/portfolio.json` and `risk.json`;
  `services/trade-ngin/config_template/defaults.json`; [THE_EQUITY_BOOK.md](../01_the_book/THE_EQUITY_BOOK.md).

| Shows as | Cause and effect | Rule | Where |
|---|---|---|---|
| A deal reads as a strange exchange ratio | The vendor's deal `value` is a deal value, not an exchange ratio; tests that hand-set deal terms stay green while the field is misread | Test on inputs the real feed gives | `services/trade-ngin/docs/CORP_ACTIONS_DATA_BOUNDARY.md` |
| Allocation applied twice | The equity runner calls `get_portfolio_positions()`, harmless only at allocation 1.0 (warning 2.3) | Never use it on a multi-strategy book | `services/trade-ngin/src/portfolio/README.md` |

## 8. Repository

**8.1 A test suite can report green with nothing run.**
- Shows as: a green suite, or `0 tests ran`, read as a pass.
- Cause: `ctest` exits clean on an empty match. `ctest -R` and `ctest -E` both match the registered `Suite.Test` name
  and never a file name, so a `ctest -E "_db|atomicity"` guard meant to leave out database test files excluded 0 of
  1,232 tests. Tests are listed in
  `TEST_SOURCES`, and `gtest_discover_tests` sees only what was configured, so a new file without a reconfigure never
  runs. All tests build into one executable, `trade_ngin_tests`, so one that does not compile stops the suite. Database
  tests skip without their environment and report green; about 30 tests that read a source file skip when the file is
  not found. A count from a `ctest -R` filter is a subset of the suite, and a pass count says nothing about which
  tests ran.
- Effect: real mismatches stay hidden behind green, and a guard believed to protect the database protects nothing.
- Rule: reconfigure after adding a test file and check that `ctest -N` counts it. Set `TRADE_NGIN_TEST_DSN` and
  `TRADE_NGIN_REQUIRE_DB=1` so a skip fails, run from the build tree through `ctest`, and run serially, because the
  database tests share one database. Before a merge, run the full suite and compare failing test names, not counts.
- Where: `gtest_discover_tests` in `services/trade-ngin/tests/CMakeLists.txt`;
  `services/trade-ngin/tests/backtest/test_backtest_stored_run_refusal.cpp`.

**8.2 A test on mocks passes a write path that cannot write.**
- Shows as: green tests and green CI on a change that fails against the real database.
- Cause: a fake answers what the test expects; only the real schema refuses a missing column. A write path in
  AlgoGators/AlgoLens#80 omitted three NOT NULL columns of `trading.positions` while 35 tests passed. The C++ book and
  desk database tests, the automated proof that a desk write never touches system rows, skip in CI when no test DSN is
  set.
- Effect: blockers that ship behind green checks.
- Rule: every change that touches the database has at least one test against a Postgres built from the shipped
  migrations, and a database test that skips is not counted as a pass. Lock lists and configs are tested against the
  shipped config files.
- Where: AlgoGators/AlgoLens#80; a database test that skips without `TRADE_NGIN_TEST_DSN` and fails under
  `TRADE_NGIN_REQUIRE_DB=1`, for example `services/trade-ngin/tests/storage/test_live_results_key_roundtrip_db.cpp`.

**8.3 CI checks less than its colour suggests.**
- Shows as: CI green while coverage, lint and the database suite are unknown; a local build that passes and CI that
  fails.
- Cause: the CI of `AlgoGators/algogators` has no coverage gate, trade-ngin lint, Sonar, Valgrind or branch protection,
  and runs no C++ database test: its only database is a throwaway postgres:16 for four migrations. CI builds and runs
  with gcc on Linux; the build passes `-Wno-error`, so gcc warnings never fail CI, and only hard errors and failing
  tests do. A dangling reference that ran clean under a local clang build failed a test on CI's gcc build. The schema
  ownership job fails on a quoted `futures_data.` literal in `src/data` or `src/storage`.
- Effect: a green mark that does not cover coverage or the database.
- Rule: read `gh pr checks` on every PR, check coverage (above 80% on the code a PR pushes) and the database suite by hand at
  every head, and build table names with `build_table_name`.
- Where: `.github/workflows/svc-trade-ngin.quality.yml`, which builds and runs the C++ tests through
  `.github/workflows/_quality-cpp.yml` (gcc 13 on Ubuntu); `.github/workflows/svc-trade-ngin.rpc.yml` (the throwaway
  postgres:16 and the schema ownership job); `services/trade-ngin/CMakeLists.txt` (`-Wno-error`); AlgoGators/algogators#120.

**8.4 Old commit ids and bare issue numbers point at the wrong thing.**
- Shows as: a commit id that is not on main; an issue link that points at another item or redirects.
- Cause: history was rewritten more than once, and the import into `AlgoGators/algogators` gave every commit a new id
  (`a22f43d0`, the merge of AlgoGators/trade-ngin#186 on its old line, is not an id on main). The open issues of
  `AlgoGators/trade-ngin` moved to `AlgoGators/algogators` with new numbers (for example trade-ngin #187 is algogators
  #163, #269 is #179, #270 is #180, #275 is #185); closed issues stayed, and pull requests cannot move. When issues are
  transferred, GitHub silently rewrites every bare `#N` that refers to them, with no edit history: 336 references in 57
  texts went wrong in one transfer.
- Effect: citations that resolve to nothing or to the wrong item.
- Rule: cite a tracked file and its section, or a PR, or a commit you have found in the repository you name, with the
  repository named. Write full `AlgoGators/<repo>#N` references, never a bare `#N`, and snapshot and diff the referring
  texts around any transfer. The archive tags and branches (`archive/keep-do-not-delete/*`, `main-rolls`, `main-nonQT`,
  `regime_detection_updated`) are on `AlgoGators/trade-ngin`; a commit only on an archive branch is invisible to a
  reader of main. Never delete `AlgoGators/trade-ngin`: its PRs live only there.
- Where: AlgoGators/algogators#90; AlgoGators/algogators#98.

**8.5 A merge with no conflict can still revert code.**
- Shows as: a clean merge that deletes or restores code nobody touched.
- Cause: git reports conflicting lines, not changed behaviour. AlgoGators/trade-ngin#41 would have merged with no
  conflict and reverted main's statistics library (five model structs deleted and 14 test files unregistered), because
  main had not touched those files since the merge base.
- Effect: silent loss or return of code.
- Rule: a merge is diffed against both parents, and the full suite runs on the merged tree before merging.
- Where: AlgoGators/trade-ngin#41.

| Shows as | Cause and effect | Rule | Where |
|---|---|---|---|
| A secret is in history | A value pasted into a commit stays in history after it is removed | Nothing secret goes in a file, a commit, an issue, a PR, a log or a report. Configs are never printed; a value is cited by its key. A secret that reached a commit is rotated | `AGENTS.md` at the repository root (the repository is public: no secrets); `services/trade-ngin/README.md`, Configuration (`config/` is ignored by git so credentials are never committed) |
| A merge brings hundreds of commits | A merge commit carries every commit beneath it, and merges nest | Squash by default | `AGENTS.md` at the repository root; AlgoGators/algogators#179 |
| On macOS, a baseline build shows a clean zero | On macOS `libtrade_ngin.dylib` resolves through `@loader_path`, so a copied `build/bin/Debug/` loads the new library (the setting is macOS only) | A baseline is a separate checkout and build at a named commit | `services/trade-ngin/CMakeLists.txt`, the RPATH block |
| A fix in an app cannot be tested | Logic in `services/trade-ngin/apps/` is not compiled into `trade_ngin_tests` | Move the logic into a header first | `services/trade-ngin/include/trade_ngin/live/corp_action_window.hpp` |
| A migration test fails in a path with a space | `test_027_qt_daily_cutoff.sh` uses an unquoted `\i $HERE/...` | Run it from a path without spaces | `services/trade-ngin/migrations/test_027_qt_daily_cutoff.sh` |
| A closing keyword did not close the issue | It works only when the PR merges into the default branch | Close by hand when merging into any other branch | By design of GitHub |
| An old PR's commits cannot be removed | GitHub keeps `refs/pull/N/head` for every PR | Treat them as permanent | By design of GitHub |
| A tracked doc disagrees with the code | Docs drift from code that keeps moving | Trust the code, and correct the doc in the same PR as the code | `services/trade-ngin/docs/` |

## 9. Production

**9.1 Live trading can stop and nothing notice.**
- Shows as: nothing. Live trading stopped on 2026-05-05 and nothing noticed for 81 days: cron called a binary that does
  not exist.
- Cause: no check read the outcome of the daily run. The watchdog (`check_live_trading.py`) reads only the books in
  `LIVE_PORTFOLIOS`, which defaults to `QT_CONSERVATIVE_PORTFOLIO`, and its workflow does not set it.
- Effect: 81 days with no live book stored; a book missing from the list is never checked, so a refused date on it
  raises no alarm.
- Rule: the watchdog's `LIVE_PORTFOLIOS` names
  every book that has a daily run. A book listed with no daily run reads as stale every day, so a book is listed once
  its daily run exists. The workflow does not set it yet; [FUTURE_WORK_ADVICE.md](../06_open/FUTURE_WORK_ADVICE.md) has the step.
- Where: `services/trade-ngin/scripts/check_live_trading.py`; `.github/workflows/svc-trade-ngin.watchdog.yml`;
  [RUNBOOKS.md](../05_operations/RUNBOOKS.md) section 3.

**9.2 A futures schedule of Monday to Friday corrupts the book.**
- Shows as: costs several times too high, a book rebuilt from flat every Monday, and a stored book that disagrees with
  the broker, all at exit 0 with the watchdog satisfied.
- Cause: the futures chain needs every calendar day: the Saturday run books Friday and the Sunday run carries the book
  over, and a skipped day is lost, not deferred. AlgoGators/trade-ngin@65f61482 (2026-07-25), a fix for a cron that
  called a missing binary, also restricted the schedule to Monday to Friday, until AlgoGators/trade-ngin@3198b5a7
  (2026-09-23).
- Effect: replayed against the seven-day chain, costs of $348.76 against $57.52 (6.1 times), every Friday left
  unfinalised for good, and a database that ended believing 8 contracts in 5 symbols against the broker's 64 in 15.
  Under the run-gap refusal, 1 of 21 dates ran and then the book refused for good.
- Rule: the cron runs every calendar day, the wrapper has no weekday guard, and the watchdog counts calendar days. A
  schedule change never rides inside an unrelated fix.
- Where: `services/trade-ngin/live_portfolio.cron`; `services/trade-ngin/docs/LIVE_RUN_CYCLE.md`.

**9.3 A script that changes production can fail where its own checks cannot see.**
- Shows as: exit 0 and lost rows, or a re-run that reports nothing to do.
- Cause: `psql` goes on after a failed statement unless `ON_ERROR_STOP` is set, and a shell pipeline reports its last
  command's status unless `pipefail` is set, so a failed step is followed by the next one. A generated script whose
  data lines are missing still creates its staging tables, deletes, inserts nothing and exits 0. A check run after
  COMMIT reads the state the script just wrote, so a re-run reports nothing to do. A compare of counts passes a changed
  row set. When no temporary table of that name exists, an unqualified `drop table` of a work table drops the
  permanent table of the same name.
- Effect: rows lost or changed on production with a clean exit, found only by a later row count.
- Rule: every check of a production script runs inside its transaction, before COMMIT: a full row-set compare, row
  counts, and a ledger diff against a pinned file. Work tables live in `pg_temp`. A second run refuses. Scripts stop on
  the first error (`ON_ERROR_STOP`, `pipefail`). No DELETE runs on any table before a dump of that row set exists and its
  location is written down. A verification run that writes goes only to a scratch copy of the database, never to
  production: the release scripts accept `TARGET=scratch` only for a database whose name contains `scratch`, and `scratch_guard` stops a
  scratch command whose `PGHOST` and `PGPORT` are the server in `config/defaults.json`. A script is rehearsed on a copy
  before production, and production is changed only by hand.
- Where: `services/trade-ngin/scripts/futures_release/lib.zsh` (the targets, `pipefail`);
  `services/trade-ngin/scripts/futures_release/scratch_guard.zsh`; `services/trade-ngin/scripts/futures_release/README.md`.

**9.4 Tests and scripts can find the production database.**
- Shows as: probe rows or test books in production tables.
- Cause: database tests run from a nested checkout walked up the directories to the real `config/defaults.json`, behind
  a guard that excluded nothing (warning 8.1). Shared scripts that change into the main checkout use whatever database
  its config names; `services/trade-ngin/scripts/qt_e2e.sh` writes `QT_E2E_PORTFOLIO` rows to the database its config
  names. A migration test whose guard knew only the production name would have dropped the `trading` schema of a shared
  test database.
- Effect: writes to production from test work.
- Rule: database tests connect only through `TRADE_NGIN_TEST_DSN`. Scripts are rooted where they run and refuse a
  production target; guards refuse a whole family of database names and any database holding a book table.
- Where: AlgoGators/trade-ngin#86.

**9.5 The order of the futures release matters, and one wrong step does not refuse.**
- Shows as: a release step that refuses, or worse, nothing refuses.
- Cause: the live runner takes its universe from the stored `.v.0` symbols. Part B before the hole fill refuses. Part B
  with the QT runs still going does not refuse, because no script can see a server's config, and no config is valid on
  both sides of part B.
- Effect: gold and silver can enter the book twice.
- Rule: follow the order in the release README exactly, stop the QT runs before part B, and run the read-only check
  after every step.
- Where: `services/trade-ngin/scripts/futures_release/README.md`, the order; AlgoGators/algogators#180.

**9.6 No table records which migrations production has.**
- Shows as: a migration's state on production is unknown, or a number on production differs from main.
- Cause: migrations are applied by hand and nothing records them in the database. Migrations 034 to 039 were applied to
  production as 028 to 033, before the two lines of main joined, and their stored comments still name the old numbers.
  Every migration but 024 has a `_rollback.sql` beside it, so a glob also applies the rollbacks; 024 is irreversible.
  A binary deployed before its migration fails a dynamic-column insert on the missing column and can still exit 0.
  Migration 041 is not in the release order: nothing fails, because the writer checks for the columns, but rows written
  without it keep `mark_price` and the settle columns NULL and lose the e-mail's notes.
- Effect: re-applied or skipped migrations, and runs that store nothing after a deploy.
- Rule: record each applied date in `services/trade-ngin/migrations/README.md` in the same PR or straight after; a
  blank cell means not recorded, not not applied. Read the target branch's `migrations/` before picking a number, and
  never reuse a retired number (007 to 011 and 030 to 033). Apply one file at a time, by name, in number order. Never
  apply 034 to 039 again on production. Apply a migration before the binary that names its columns, and 041 before the
  seed if the notes are wanted.
- Where: `services/trade-ngin/migrations/README.md`; `services/trade-ngin/docs/performance_upkeep.md` section 9.

**9.7 A test of the e-mail never runs the send path, and a copied config mails real people.**
- Shows as: an e-mail that prints wrong figures in production although every test passed; real recipients mailed from
  a test.
- Cause: the e-mail path runs only with `--send-email`, and test runs never pass it, so the one path production uses is
  the one path no test runs: from AlgoGators/trade-ngin#133 the e-mail's yesterday table printed 0.00 on every row
  until the flagged path was first run. Configs carry real recipient lists. The To line is the portfolio's `to_emails`;
  the Cc line is `QT_MAIL_CC` plus every approver in `QT_APPROVERS`, whatever the config says.
- Effect: a shipped regression in the daily e-mail, or mail to real people from a test.
- Rule: test runs pass a date and never `--send-email`. The sending path is tested on its own with the report's
  `--to <address>` (that address only, no Cc). Before any run that could send, overwrite `to_emails` in the copied
  config and clear `QT_MAIL_CC` and `QT_APPROVERS`. The book e-mail is the Python report, which reads only stored
  figures; `TRADE_NGIN_EMAIL_BODY_DIR` is read by nothing since AlgoGators/trade-ngin#261.
- Where: `book_recipients`, `services/trade-ngin/services/rpc/algogators_rpc/services/report/__main__.py`;
  `services/trade-ngin/config_template/README.md`; `services/trade-ngin/scripts/run_live_portfolio.sh`.

| Shows as | Cause and effect | Rule | Where |
|---|---|---|---|
| Row locks held by a client that is gone | A connection whose client stops mid-transaction stays idle inside the transaction and keeps its row locks, blocking later writes to those rows | Give every connection a time limit, as the desk service's connections do (`statement_timeout` of 30 seconds); a batch run by hand on production sets its own; the server's `idle_in_transaction_session_timeout` ends an idle transaction | `STATEMENT_TIMEOUT_MS`, `services/trade-ngin/services/rpc/algogators_rpc/services/desk/config.py`; a PostgreSQL server setting |
| Every daily run logs skipping and exits 0 | The wrapper's lock is a directory removed on exit; a killed wrapper leaves it behind. A binary started by hand takes no lock | Remove the stale lock directory after proving no run holds it | `services/trade-ngin/scripts/run_live_portfolio.sh` |
| A dump or `COPY ... TO` of a bar table yields nothing | `futures_data.ohlcv_1d` and `contract_ohlcv_1d` are TimescaleDB hypertables on production: the parent holds no rows of its own and reads 16 kB | Use `COPY (SELECT ...)` and `hypertable_size`, and check the table kind first; the release README lists what was not tested on these tables | `services/trade-ngin/scripts/futures_release/README.md` |
