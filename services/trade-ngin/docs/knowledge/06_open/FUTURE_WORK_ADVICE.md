# Future work: what to do and what not to do

The futures book on AlgoGators/algogators main, under `services/trade-ngin/`, is the chosen setup at net leverage 5.50
(AlgoGators/trade-ngin#282). The advice in short: run the seed once at the futures release from `services/trade-ngin/scripts/futures_release/README.md`
(merged with AlgoGators/algogators#124), keep carry on main and switched off, and spend new measurement only on questions
that have a stated mechanism and a test written before the result.

Four findings sit under almost every item below:

- **A Sharpe difference is read against two measured quantities.** Carver's standard error of the Sharpe ratio (AFTS,
  strategy three) is about 0.26 on the 16-year window and 0.45 on the 5-year at a Sharpe near 0.30; it assumes
  independent returns, and trend returns are fat-tailed and autocorrelated. Across start dates the chosen
  setup's Sharpe runs from 0.206 to 0.453 on the 5-year window; the 16-year window is one run. So a choice rests on its mechanism, and its numbers are read on
  grids of start dates.
- **The 2025-26 loss traced to no code defect.** Every fix that closed the 2025-26 gap failed when the history was split
  into 2011-2020 and 2021-2026, so no more tuning on those two years.
- **At 500,000 the book holds few contracts per market.** The whole-contract decision on a few coarse positions moves
  results by more than most levers do, so a lever counts only when it also holds on the unrounded book (the positions
  before rounding to whole contracts).
- **The book is 500,000.** A larger account is not a fix, and instruments are not retired to reach a churn figure:
  diversification across many markets is where a trend book earns its return, and Carver's dynamic optimisation exists so
  that a small account can keep them all.

Results and lever tables are in [RESULTS.md](../03_tuning_and_results/RESULTS.md),
[LEVERS.md](../03_tuning_and_results/LEVERS.md) and [CARRY.md](../03_tuning_and_results/CARRY.md); why the book trades
these 36 markets is in [THE_BOOK.md](../01_the_book/THE_BOOK.md).

How to read an item: the advice in bold; **Why**, what it does to the book and the economics behind it; **Tried**, what
was measured and what it showed, the numbers as support; **Issue**, where the work is held. Figures come from the date and
engine of their measurement, and most predate the final data: they say why the advice stands, not what the book earns.
Where a cause is suspected but not measured, the item says unmeasured. "Offline" means a reimplementation on the engine's
series, not an engine run. The reference model is a model of the trading loop run outside the engine, used to test churn
and risk settings.

## Data

### Do

- **Tune and gate on the filled data only, and never compare a run on unfilled data with one on filled data.** Why: the
  optimiser works on the dates all symbols share, so a hole in one symbol's continuous table removes that date from all
  36, and two runs on different fills differ in their data, not in the change under test. Tried: the swing between the
  real micros and their stand-ins (full-size bars under micro names) looked like a build problem; it was holes the
  stand-ins carried and the real micros did not. Filling added 476 rows to each continuous table on the base data, 484
  with the micros. Issue: `AlgoGators/algogators#36`; steps 5 and 8 of the release README.
- **Align calendars and run one build on both sides before blaming prices or costs.** Why: a calendar or build difference
  changes which dates the optimiser shares, which moves every position and reads like a price or cost effect. Tried: the
  real micro set against the stand-ins read -4.49 points on the mean with 12 of 17 windows lower; aligned, it read +0.92
  with 8 of 17 lower. Issue: none; whether the optimiser's covariance should use only the shared dates is related to
  `AlgoGators/algogators#177` item A4.
- **Keep the daily fill's limits: never the newest day, never from a partial contract load, no Saturdays, and a NULL vendor
  id on volume-picked rows.** Why: each limit stops the fill from giving the book a bar the vendor did not print. The
  newest day can carry a one-day vendor stray into a roll switch; a partial load lets the volume pick choose from an
  incomplete set; a Saturday row adds a day that is not a trading day for the book's roots; and the NULL id keeps a
  volume-picked row from being read as the vendor's own. Tried: a review of the fill found the stray and the partial-load
  cases. Issue: `AlgoGators/algogators#87`.
- **Take a free vendor count on a few of the probable vendor holes.** Why: a filled bar the market never printed is a price
  the book could not have traded, and a count settles it at no cost. Tried: on 33 filled rows both real neighbours name the
  same contract and that contract has no bar that day (31 are 6L days of 1 to 300 lots, plus 2 MGC days); the fill filled
  them anyway. Issue: none.

### Do not

- **Do not overwrite or back-adjust `futures_data.ohlcv_1d` or `ohlcv_1d_raw` in place.** Why: the raw close is the real
  traded price, and notional, whole-contract rounding and costs all need it; an adjusted price changes every contract count
  and cost. Tried: the per-contract work kept to this: its data went to new tables, and the release's fills only insert into
  the continuous tables. Issue: none.
- **Do not delete from production without a dump whose location is written down.** Why: a delete cannot be taken back
  without a copy taken before it, and a copy whose location is not written down cannot be restored from. Tried: the
  release's live reset archives first, prints the archive's path, and restores from it
  (`services/trade-ngin/scripts/futures_release/10_live_reset.sh`). Issue: step 14 of the release README.
- **Do not act on a stand-in result.** Why: a stand-in carries the full-size contract's bars and holes, not the micro's, so
  it measures a different book. Tried: two selective speed rules scored mean Sharpe 0.45 and 0.56 at full size and 0.16 and
  0.31 on the gold and silver micros, against 0.30 for the plain book on the micros (7-run screen). Issue: none.

## Rolls

### Do

- **Work through the per-contract roll list before adding to the roll design.** Why: the list's items are known errors in
  roll accounting that already ships, such as a late correction applied at the previous day's quantity and a rule-derived
  last trading day that is wrong on 54 contracts, which can roll a position late; design built on top would bury them.
  Tried: the reviews of the October roll work left nine items; items 3 and 4 may move a stored number. Issue:
  `AlgoGators/algogators#183`.
- **Judge a roll setting on its price effect and on several start dates.** Why: a roll setting changes the day a position
  moves between contracts, and on a coarse book that moves a whole contract's timing, so one window's result moves with that
  timing, not with the setting's cost. Tried: a fix that changed no booked move took the per-contract switch-on from -14.08% to 26.19% on
  the 5y and from 74.54% to 27.92% on the 10y. Issue: none.
- **Count roll days on CME Group's calendar per product group, not the NYSE's.** Why: a roll deadline is counted in the
  trading days of the market where the contract trades, and NYSE holidays differ from CME product-group closures, so a
  deadline counted on the wrong calendar lands a day off. Tried: the October roll work built one calendar per exchange with
  8 product groups and 297 closures (`services/trade-ngin/include/trade_ngin/core/futures_holidays.json`). Moving the runners
  and the e-mail to it is on an issue, and new exchanges need their own. Issue: `AlgoGators/algogators#168`,
  `AlgoGators/algogators#169`.
- **Write re-run logic on what the stored rows say, not on the order they were written.** Why: a re-run of an older date
  writes after later dates were written, so the write order no longer follows the book's history. Tried: a `created_at`
  candidate would have marked a later date's fresh late roll stale after a re-run of an older date, and the run would have
  dropped that roll. Issue: none; the rule is in the code.

### Do not

- **Do not revive roll back-refusal.** Why: refusing a roll back to an earlier contract keeps the book in a contract the
  market has left whenever the forward switch was a vendor peek, and the saving is small. Tried, 2026-10-05, on the vendor
  series: 16y back rolls fell from 150 to 2 but it saved only about 57 a year, and the real front's return was refused (ZM
  2025 frozen at -2 for 77 bars, -8,760 against -1,350). Not adopted. Issue: none.
- **Do not remove `instrument_id_relabels` from a book that has stored a chain with it.** Why: the list maps vendor ids the
  book has already stored; replayed without it, the same contract reads as a new one and the book pays for a roll that never
  happened. Tried: a book written with the list and replayed without it books a false late roll between 2026-02-23 and
  2026-03-17. Issue: none; the list is in the conservative template.
- **Do not reopen when to follow the vendor as a roll change on its own.** Why: following the vendor only near the deadline
  touches few rolls, and their leg costs are small next to the book's costs, so on its own it does not move the book's
  economics; the case for it is cleanliness, not money. Tried: priced at 38 close rolls over 16 years, 4 of them legged.
  Booking a roll as a spread is separate. Issue: `AlgoGators/algogators#183` item 10 (the spread); the near-deadline
  question is in the table at the end of this file.

## The optimiser and sizing

### Do

- **Treat the chosen optimiser values as fixed code.** `buffer_step_rule`, `b_sigma_rule`, `wrong_sign_rule`,
  `sample_cov_window` and `fast_off_high_vol` are not on main (nor is `edge_risk`): the fraction step, the granularity
  buffer, the sign close and the 756-bar window are behaviour, not keys. Why: with no key, no config can drift them, and
  every book runs the loop that was tested. Tried: each alternative was measured and not kept
  ([LEVERS.md](../03_tuning_and_results/LEVERS.md)). To measure one again, start from
  `archive/keep-do-not-delete/s25-one-pass`, `archive/keep-do-not-delete/step-edge` or `archive/keep-do-not-delete/levers`
  on `AlgoGators/trade-ngin` (they are not on `AlgoGators/algogators`), and ship any new switch absent from the templates and
  proven identical when off, so it cannot move a stored book that did not choose it. Issue: none.
- **Judge a sizing fix in this order: target book, tracking error, the gap's spread across starts, and only then window
  means.** Why: a sizing fix aims to bring the held whole-contract book closer to the unrounded target; a window mean mixes
  that with the effect of its start date. Tried: the rounding gap tracks at about 8% a year, a standard deviation of roughly 89k over five
  years and 57k over two; the differences between the last candidates (74k and 27k) sat inside one standard deviation.
  Issue: none.

### Do not

- **Do not chase a churn target by retiring instruments.** Why: trend following is paid by a few large, persistent moves,
  and which market will make the next one cannot be known in advance, so each market left out is a market where such a move
  can no longer be caught. Carver's own answer for a small account is dynamic optimisation over all its instruments
  (strategy twenty-five), not fewer instruments. Tried: Carver's instrument selection at 500,000 picked 7 instruments and
  lost 50.7% over ten years; energy, the lumpy group a trim would remove first, is 198k of the chosen setup's 270k 16y target
  book. Adopted 2026-10-26: keep and trade every contract, with no drop option. The chosen setup runs 152.8 round trips a
  year on the 16y and 215.5 on the 2y, against a bar of about 150. Issue: `AlgoGators/algogators#184` (a study of adding
  markets, not of removing them).
- **Do not expect a fixed 1% buffer to work at 500,000.** Why: one contract of most futures here is wider than Carver's 1
  percent band, so the book's band is set to the smallest trade it can make, and the book trades only when a whole contract
  closes a gap worth that trade. Account size alone does not explain the 1% band's churn: Carver's own 500,000 test at 1%
  turned over about as much as his 50 million book (AFTS, table 124); his test searched 100 instruments, not 36, and costed each
  trade at the current cost per contract scaled to the volatility of its period, with no impact term. Which difference drives the churn here is unmeasured. Tried: the 1% band tracks the unrounded
  book more closely (4.5% against 8.3% tracking error on the 16y) but churns 337 to 454 round trips a year at 1.2% to 1.5%
  costs, and the costs take back the tracking gain. Issue: none.
- **Do not cure churn with per-symbol whole-contract thresholds.** Why: any hysteresis wide enough to stop the churn also
  stops the thin positions, and those are the book's diversification. Tried, 2026-10-04: the staircase went (one-contract
  trims 97 and 105 to 0), but sectors under 1.5 contracts never opened, energy and metals fell to about 0, and every variant
  lost over 5y. Issue: none.
- **Do not extend micros past gold and silver to cut coarse positions.** Why: micros are used where the full-size contract
  cannot be held at 500,000: gold and silver want about 0.05 contracts at an average forecast (one full-size GC is 12.9 of
  Carver's buffers), so only their micros hold them near target. Elsewhere a
  micro makes each position finer without moving the band, which the lumpiest contracts set, so the book holds several
  contracts where it held none or one and every one-lot adjustment becomes a trade, and each micro's fee falls on a smaller
  notional. Tried: micro FX, metals and crude ran 278 to 376 round trips a year and a mean Sharpe of 0.17 against 0.31.
  The copper micro, listed only since 2022-05-02, measured 0.13 against 0.30. Micro crude was also tried
  ([LEVERS.md](../03_tuning_and_results/LEVERS.md), lever 10). Issue: none.
- **Do not lower the risk target to reach a drawdown figure.** Why: a lower target shrinks every position, and with whole
  contracts more targets round to zero, so at 500,000 lowering the target trades diversification for lower risk: the book
  holds fewer names. Tried, 2026-10-06, 1,132 runs of the reference model at 0.20, 0.15, 0.125 and 0.10: names held 14.0
  down to 8.2, Sharpe 0.44, 0.26, 0.26, 0.27; no value met both the return and the drawdown bar. Issue: none.
- **Do not reopen the sizing forms on performance.** Why: a sizing form decides how risk responds to past profit and loss.
  Half compounding is Carver's form: it cuts sizing by the full dollar loss. The proportional form cuts it by the
  drawdown's share of the peak account, so it keeps more risk on in a drawdown from a high peak. Half compounding stays as
  Carver wrote it (adopted 2026-10-09). Tried, October: the proportional form was deeper in drawdown in 13 of 18 windows,
  and neither it nor a capped account had a gain with a path-free reading. Issue: none.
- **Do not change the `b_sigma` floor, restore the wrong-sign buffer or build a cost-aware edge.** Why: the floor rarely
  binds, so changing it moves little; a position held against its own forecast is a bet the system expects to lose, and
  the wrong-sign buffer kept such positions for long stretches; and a cost-aware edge would rank steps by risk per dollar of
  cost, which leans the search toward the large energy and index contracts and concentrates the book (a proxy shows the
  direction; the size is unmeasured). Tried: the floor binds on 0.6% to 2.8% of days; under the wrong-sign buffer positions
  sat against their forecast for a median of 4 rebalance cycles (longest 217) and the book tracked its target at 20.9%
  tracking error; in the chosen setup, positions held against the forecast earned +10.1k (5y) and +1.9k (2y). Issue: none.
- **Do not retest the covariance lookback without a stated mechanism.** Why: the lookback trades responsiveness against
  estimation noise, and without a mechanism a pass would be fitted. Tried: 160, 252 and 504 each passed on 0 of 3 bases.
  Issue: none; the shared-dates question is separate (related to
  `AlgoGators/algogators#177` item A4).
- **Do not propose a larger starting equity as a fix.** Why: a larger account holds more contracts per market, so rounding
  stops dominating, but the account the book trades is 500,000, and a fix that needs another account does not fix this
  book. Tried: the capital study of 2026-10-08, from 500,000 to 10m: risk is the same, round trips go from 143 to 2,369 a
  year, costs from 0.61% to 1.99%, and the 16y return falls from +93.5% to +24.3%. Issue: none; see
  [LEVERS.md](../03_tuning_and_results/LEVERS.md).

## Overlay

### Do

- **Set limits by the p99 method on the candidate's own 16y, and judge each candidate at its own limits.** Why: Carver's
  method sets each limit from the book's own risk distribution, so a candidate judged at another book's limits is held
  where it would not be, or left free where it would be held. Tried: the selective rule that passed every clause at the
  parent's limits failed at its own, because looser limits rarely bind and the rounding gap took the loss (1y -59.9k
  against -37.8k). State each set's limits beside every paired difference. Issue: none.
- **Redo the limits when the book changes.** Why: a new book has a new risk distribution, so old limits bind in the wrong
  places. Tried: the carry book's 99th percentiles moved the risk limit to 1.71, the jump-risk limit to 3.30, the
  shock-risk limit to 3.32 and net leverage to 5.69. Part C of the universe study retunes the limits for this reason. Issue:
  `AlgoGators/algogators#184`.
- **When a limit rounds near a midpoint, measure both sides.** Why: rounding to the grid can put a limit on either side of
  its percentile, and the method, not the nearer number, decides. Tried: the chosen setup's net 99th percentile is 5.5926
  against a midpoint of 5.625; at 5.75 the mean of five on a proxy was 0.276 against 0.274,
  and the book stayed at 5.50, where the method rounds it. Issue: none.
- **Change overlay settings book by book.** Why: limits come from each book's own risk. On main the conservative template
  has `overlay_covariance` `carver` and limits 1.75 / 4.75 / 3.00 / 7.25 / 5.50; the QT books keep R_max 2.25 and sample
  covariance. Tried: the tuning ran on the conservative book, and no QT measurement at these limits is recorded. Issue:
  none.
- **Build the daily margin check as its own decision.** Why: margin bounds what the book can hold through a stress, and
  with nothing reading it a margin call would be the first signal; a check that acts on margin changes sizing, so it needs
  its own test. Tried: on main no sizing or risk code reads margin; the mean margin share on the 16y is 20.77%. Issue:
  `AlgoGators/algogators#172`.

### Do not

- **Do not tune BASE's limits.** Why: BASE proves the multi-strategy plumbing and is a regression book, not a traded one,
  so its limits carry no economics (recorded 2026-10-01). It must keep running and must not break. Its template carries
  2.25 / 4.5 / 4.0 / 8.0 / 6.0, and no tuning rested on it. Issue: none.

## Costs

### Do

- **Re-measure the speed list when fees, the cost model, the universe or a listing date changes, otherwise once a year.**
  Why: the list drops a speed on a contract when its cost takes too much of its expected return (Carver's speed limit), so
  it moves whenever costs move. Tried: twelve contracts lost eighteen rules; `--check` reports differences and rewrites
  nothing. MGC and SIL run all six speeds on a list measured on full-size GC and SI; their own costs against the speed
  limit are unmeasured. Issue: none; the tool is `services/trade-ngin/scripts/trading_rule_costs.py`, and the micros'
  re-measure is in the table at the end of this file.
- **Check the MGC and SIL margins and the SIL exchange fee against the broker statement.** Why: the optimiser's search reads
  the fee, so a wrong fee makes a different whole-contract book from the first traded day, and margin decides what the
  account can hold. Issue: `AlgoGators/algogators#180` item 5.

### Do not

- **Do not change a fee to change trading.** Why: the fee is an input to the trade decision, so a wrong fee makes the
  optimiser misjudge which trades pay. Tried: at a flat 1.50 the micros look more than twice as expensive as they are
  (0.61), so the book trades them less and sits further from target. If less trading is wanted, use the buffer or the cost
  weight. Issue: none.
- **Do not read a fee schedule's difference as edge.** Why: a fee change flips marginal whole-contract decisions, and on a
  coarse book one flipped contract can dominate a window. Tried: the whole 16y gap between flat and IBKR fees was one gold
  contract held in one run and never in the other (+218,150). Decide a schedule on whether it is correct. Issue: none.
- **Do not look for the 2y's loss in costs.** Why: costs are small against the market's swing. Tried: costs are 0.05 of
  Sharpe on every window, and at twice the costs the 16y reads 0.255 against 0.300. Issue: none.

## Signals

### Do

- **Read [LEVERS.md](../03_tuning_and_results/LEVERS.md) before proposing a lever.** Why: each re-test on the same history
  is another draw, which raises the chance a lever passes by chance alone. Tried: equity fast-speed cuts and the
  high-volatility fast-off are both measured there. Issue: none.
- **Take the counted-row question through the engine before relying on its offline figure.** Why: volatility estimated on
  counted rows treats a Sunday bar as a day, which changes sizing on the roots that trade on Sundays; the offline figure
  carries no costs. Tried: business-day spans moved the 16y Sharpe from 0.21 to 0.27 offline and did not move 2025; the
  engine effect is unmeasured. Issue: the table at the end of this file.
- **Route new rules through the issues.** Why: each of Carver's trend variants adds trading to a book whose churn is
  already near its bar of about 150 round trips a year, and on Carver's own tables the gain is small: strategies 12, 17, 18
  and 19 each gain 0.02 of Sharpe or less on his tables 59, 74 and 78, and 17 and 18 gain 0.05 and 0.04 on his
  trend-and-carry table (AFTS, table 86), a fifth or less of Carver's standard error of the Sharpe ratio on the 16-year
  window (0.26). So each needs a reason
  in its mechanism. Tried: none built. Issue: `AlgoGators/algogators#187` for new strategies.

### Do not

- **Do not tune further on 2025-26.** Why: the 2025-26 loss traced to no code defect, and a fix chosen on two years trades
  the long record for the short one: the fast speeds that lost in 2025 earned 196k of the unrounded 16y target book's 270k.
  Tried, offline: 16/64 and
  slower gave 2025 +3.9% against -14.3%, but the 16y fell from +64.7% to +45.0%, and the speed order reverses between the
  halves (Sharpe 0.39 against 0.18 in 2011-2020, 0.06 against 0.28 in 2021-2026). Two changes did pass the split and neither
  closes the gap: the vol blend off (noise, t 0.35, worse in 2025 and 2026) and excluding FX (below). The one-at-a-time
  speed sweep was dropped on 2026-10-29 as fitting. Issue: none.
- **Do not fit speed weights by class or by regime.** Why: fitted weights take more parameters than sixteen years support,
  so the fit finds noise. Tried, offline: class-fitted weights added +1.9 and +2.8 points, and -0.4 and +0.2 when refitted;
  the volatility-regime fast-off lost in 2011-2020, and its idea came from the same window, so the test was not blind.
  Issue: none.
- **Do not exclude FX on its record.** Why: instruments are chosen on cost, liquidity and diversification, not on past
  return; removing an asset class because it lost is fitting, and FX is one of the seven sectors that give the book its
  diversification. Tried, offline: excluding FX raised the return in both halves (16y +117% against +65%) and left 2025 at
  -9.1%. Adopted 2026-10-26: FX stays. Issue: none.

## Carry

### Do

- **Keep carry on main, switched off.** Why: carry is built and shipped off because it has not yet been tuned on the data
  the book trades. It needs two real contract prices on the same date, which only the per-contract bars give, and on some
  roots the second contract is missing on a large share of dates (UB has it on 55.4 percent of dates and 6M on 66.0
  percent; 6L's longest gap is 78 dates). When carry was measured (2026-10-26 and 2026-10-27) the continuous series still
  had holes (AlgoGators/algogators#36) and the gold and silver micros were stand-ins, and the final retune ran with carry
  off (AlgoGators/algogators#186). What is known from those measurements: Carver's case for carry is a second return
  beside trend, and on these instruments since 2011 carry alone earned about nothing, so the blend adds a forecast that earned nothing here and kept trend's
  Sharpe at lower volatility with no second return. The blend tracks its unrounded book a little more closely than trend
  alone, roughly halves the round trips, and leaves a similar dollar shortfall from its unrounded book; why the traded
  blend's after-cost Sharpe falls is unmeasured, and the number of positions that round to zero was never counted. On the
  gold and silver micro book of the 2026-10-27 grid (12 runs per cell) the blend ran 76 to 110 round trips a year against
  152 to 216, at a median tracking error of 9.5, 8.7 and 9.6 percent against 9.8, 9.3 and 11.3; on the one stored 16-year
  run it kept 9k of its 200k unrounded book against trend's 65k of 260k, a shortfall of about 191k against 195k. It is
  built so it can be measured on the final data (AlgoGators/trade-ngin#281), off by default, and the templates ship without the block
  (`services/trade-ngin/config_template/README.md`). Tried: unrounded over 16y, carry alone had a Sharpe of 0.02 against
  trend's 0.26, and the 60/40 blend 0.25; by Carver's tables 46 and 47 carry here promised a Sharpe of 0.29 and delivered
  0.04 (15%), where his own figures realise 115%. Details are in [CARRY.md](../03_tuning_and_results/CARRY.md). Issue:
  `AlgoGators/algogators#186`.
- **Give it its own test before any switch-on.** Why: carry changes the book's mix of risk, so it moves every overlay
  limit; it needs its own 99th-percentile limits, the standard window set and a pass rule written before the result.
  Unmeasured: carry on the filled data and with the real micros. Issue: `AlgoGators/algogators#186` (route A, Carver's way;
  route B, side by side).

### Do not

- **Do not switch carry on without that test.** Why: the switch-on moves every limit and the trading of every market at
  once. Tried: on the sizing grid it halved churn but the mean after-cost Sharpe fell in two of three pairs (0.34 to 0.04,
  0.19 to -0.15). Issue: `AlgoGators/algogators#186`.
- **Do not read carry on the 16y alone or on the unrounded book alone.** Why: the held whole-contract book is what trades,
  and a single window's figure is read beside the spread across start dates. Tried: the 16y was the one window where the blend's Sharpe was the higher (0.42 against 0.38), and the blend's
  unrounded book kept +200k while its held book kept +9k. Issue: `AlgoGators/algogators#186`.
- **Do not fit carry weights by a search.** Why: a search on sixteen years fits noise, while Carver's table 51 sets the
  weights from costs and correlations without fitting. Use the rows table 51 gives and a rule written first. Issue:
  `AlgoGators/algogators#186`.

## Shorts

### Do

- **Keep shorts on.** Why: trend following is long and short by design (Carver's trend strategies are long and short from
  strategy six on), so the book follows falling markets as well as rising ones; a long-only trend book is a different
  strategy with a standing long market bet in it. Long-only came from a defect in the optimiser that dropped short
  positions, never from a decision. The one exception, the equity slow rule, is under "Do not" below. Issue: none.
- **Measure long against short again on per-contract data before saying anything about it.** Why: the earlier figure was
  measured on spliced data, where roll jumps were booked as profit and loss, which distorts the short side. Tried: with the
  roll spread removed, shorts were about break-even before costs and lost after them (-40.9k, plus or minus 21.5k, over
  16y), in equity index and metals. Issue: none.

### Do not

- **Do not turn equity-index shorts off.** Why: with shorts off the equity-index sleeve becomes a standing long market bet,
  not a trend strategy. The book already holds one asymmetry there, the equity slow rule: an equity-index short opens
  only when both slow speeds (32/128 and 64/256) are negative. It was adopted on 2026-10-05 on this book's record: 113 of
  190 equity-index shorts had been opened on the fast speeds alone, and equity shorts as a whole lost 121,403 over 16
  years, which fits Carver's finding that equities are the weakest trend class (a median instrument Sharpe of 0.03 for
  his multiple-momentum strategy, AFTS table 37). It is a declared departure from his advice against asymmetric rules by
  asset class. Tried: with the slow rule off the mean Sharpe is 0.17 against 0.30. The same idea for all instruments is
  not in the book: the record behind it is for equities only. Issue: none.
- **Do not judge shorts on the reference model's profit and loss.** Why: that model's long/short book ran under long-only
  limits, so its profit and loss measures the limits, not the shorts. Tried: it was cut on 599 days, and under its own
  limits its profit and loss moves 100k, so only the cost was concluded (fills 298 to 538). Issue: none.

## Equities

### Do

- **Work from the issues in order.** Why: the later issues build on the earlier ones; the hybrid book, for one, builds on
  items of the multi-strategy issue. The work is in five issues: leftovers
  `AlgoGators/algogators#138`, margin `AlgoGators/algogators#139`, Reg T and shorting `AlgoGators/algogators#140`,
  multi-strategy `AlgoGators/algogators#141`, hybrid `AlgoGators/algogators#142`.
- **Answer the fill-routing analysis (item A7) before the backtest hands each fill only to the strategy that generated it
  (item W4b).** Why: the BASE backtest already runs two strategies through that loop, so changing the routing may move the
  futures two-strategy book. Issue: `AlgoGators/algogators#141`.
- **Copy the per-sleeve seeding rule, not the old futures code.** Why: a second sleeve must be anchored on its own stored
  book, or it sizes from the first sleeve's. Tried: the older futures runners seeded only the first strategy's stored rows.
  The rule on main is `services/trade-ngin/include/trade_ngin/live/sleeve_seeding.hpp`. Issue: `AlgoGators/algogators#141`.
- **Put each hybrid question to its evidence first.** Why: the hybrid book's 22 design questions each change its shape.
  Issue: `AlgoGators/algogators#142`.

### Do not

- **Do not start shorting on the strength of the plan.** Why: a short book pays borrow every day, and until borrow is stored
  and accrued the statistics and the live book would leave that cost out. `AlgoGators/algogators#140` is a plan; building
  it is its own decision. Inside it, borrow rows that can be stored come before any short-enabled run, and live end-of-day
  borrow accrual before the live refusal is retired. Issue: `AlgoGators/algogators#140`.
- **Do not reconcile book profit and loss against the broker dollar for dollar.** Why: the book adjusts its cost basis for
  dividends and the broker does not, so the two differ by an exact, expected gap unless the position was bought at the
  cum-dividend close. Reconcile quantity, basis and dividend cash exactly, and report the gap by its formula
  (`services/trade-ngin/docs/BROKER_BASIS_RECONCILIATION.md`, identity 3). Issue: none.
- **Do not give `average_price` one meaning across the books.** Why: on an equity row it is the true weighted cost basis,
  and equity profit and loss and margin read it that way, so never write a mark there; on a futures row it is the previous
  day's close, reset every day, so never write a cost basis there (`services/trade-ngin/docs/LIVE_RUN_CYCLE.md` section 4.2,
  `services/trade-ngin/docs/AVERAGE_PRICE_LIFECYCLE.md`). Issue: none.

## QT

### Do

- **Stop the QT runs before part B (the MGC and SIL `.v.0` rows), and restart them only after their configs and the merged
  build are in.** Why: no config is valid on both sides of part B: one with the MGC and SIL pairs refuses while those rows
  are not stored, and one without them refuses once they are. This is a warning in the scripts, not a check: nothing
  refuses if the QT runs are still going. Issue: `AlgoGators/algogators#180`; the release README, "The QT runs".
- **Close the 2026-10-16 decisions the code does not meet.** Why: the decisions define what the desk can rely on, so a gap
  is a surprise at the desk. Tried, read from the code on main and not run: decision 11 (settings fixed for the day) is not
  built, and decisions 15, 17, 20, 21 and 22 are built in part; on decision 17 the code does the reverse of its first part,
  keeping the desk's saved books after a model re-run. The decisions are in
  `services/trade-ngin/docs/design/qt-master-rulings.md` (AlgoGators/algogators#105). Issue: none; decisions 11,
  17, 20 and 22 are also items of `AlgoGators/algogators#178`.
- **Test every write path against the real DDL.** Why: a fake accepts writes the real schema rejects. Tried: 35 green tests
  and green CI coexisted with a write path that could not write, and every blocking item died against a Postgres fixture and
  survived every fake. Issue: none; the rule stands for new write paths.
- **Finish the desk's items, including the save a model re-run can wipe.** Why: a wiped save that still reports done
  leaves the desk believing in a book it did not get. Tried: a same-day model re-run wipes a pending save, and the save
  still finishes as done. Issue: `AlgoGators/algogators#178`.
- **Name `portfolio_type` in every statement on the four book tables.** Why: the model's rows and the desk's share those
  tables, so a statement without it reads or overwrites the other book's rows. Tried: a review of the merge found about 50
  lines without it; the settled stamp carries it (`services/trade-ngin/include/trade_ngin/live/settled_stamp.hpp`), and the
  review proposed a CI grep check for the rest. Issue: none.

### Do not

- **Do not merge with the database tests or the coverage bar failing.** Why: a red test or uncovered code on main is a
  defect the next change builds on. The CI port (AlgoGators/algogators#120) did not carry over the coverage gate,
  trade-ngin lint, Sonar or Valgrind, and CI runs no C++ database test (its only database is a throwaway postgres:16 for
  four migrations, `.github/workflows/svc-trade-ngin.rpc.yml`), so coverage and the database suite are checked by hand.
  Issue: `AlgoGators/algogators#182` for the coverage gate.

## Regime and ML

### Do

- **Start regime work from the plan, and in its order.** The four analyses first, then steps 0 to 6. Why: the whole
  timeline is in-sample, fit once on the full 2020-2025 window, so every regime flip needs evidence before it changes the
  book. Issue: `AlgoGators/algogators#128`, `AlgoGators/algogators#148`, then `AlgoGators/algogators#149`,
  `AlgoGators/algogators#150`, `AlgoGators/algogators#151`.
- **Build the walk-forward test first, then measure the four findings of step 2 before changing them.** Why: a change
  measured in-sample cannot be told from a fit. Issue: `AlgoGators/algogators#148` (step 2).
- **Land each baseline move in one controlled migration, with a declared diff.** Why: a stored baseline that moves in
  pieces cannot be told apart from a defect. Issue: `AlgoGators/algogators#148`, `AlgoGators/algogators#149`.
- **Do the ML foundations first, and hold tree models to the elastic net.** Why: there are about 190 independent 20-day
  label periods, which supports a handful of effective parameters, so deep trees would fit noise.
  `AlgoGators/algogators#130` blocks `AlgoGators/algogators#131` and `AlgoGators/algogators#132`. Issue:
  `AlgoGators/algogators#129`, `AlgoGators/algogators#130`, `AlgoGators/algogators#131`, `AlgoGators/algogators#132`.
- **Run the engine's validation before quoting uncertainty.** The causality check comes first, and nothing else counts until
  it passes. Why: a look-ahead leak would inflate every statistic after it. Tried: the engine reports Sharpe with no
  interval, no out-of-sample view and no count of configurations tried. Issue: `AlgoGators/algogators#133`.

### Do not

- **Do not quote in-sample regime numbers as results.** Why: they were fit on the window they describe. Issue:
  `AlgoGators/algogators#128`.
- **Do not let a missing or stale belief tighten or refuse.** The engine runs as today, on default weights, and warns. A
  neutral belief must return today's book exactly. Why: the regime layer must never become a new way for the book to stop
  or shrink on bad data. Issue: `AlgoGators/algogators#151`.

## Statistics

### Do

- **Judge every change on start-date grids.** Why: the start date alone moves a window's result by more than most
  changes do. Tried: a one-week later start on identical prices moved a 2y from 61.68% to 57.01%; at 500,000 the 5y
  return has a standard deviation of 17.5 points across start months; the chosen setup's five 2y starts run from a Sharpe
  of -0.173 to 0.542. Across start dates the Sharpe runs from 0.206 to 0.453 on the 5y and 0.145 to 0.394 on the 10y; the 16y is one run;
  Carver's standard error of the Sharpe ratio is about 0.45 on the 5y and 0.26 on the 16y. No difference between the final
  four sets was significant (the largest paired t-statistic was 1.14). Even five starts two weeks apart are close to one
  draw. Issue: `AlgoGators/algogators#133`.
- **Run a statistics change so that nothing else moves.** Why: a statistics change must not move the book, and history
  stays comparable only if a stored past statistic is never rewritten. The named identical list covers every column of
  positions, executions, signals and the equity curve. Issue: `AlgoGators/algogators#190`.
- **Let displays read stored statistics.** Why: one computation means the e-mail and AlgoLens never disagree. AlgoLens reads
  the stored columns and computes none, and NULLs ship only once it reads them as optional. Issue:
  `AlgoGators/algogators#190`.
- **Close the two statistics questions:** the weekday volatility statistic, which is printed and stored nowhere,
  and the Sunday rows of the sizing-capital statistics, which divide Sunday's profit and loss by the last capital before
  it. Why: each statistic is either built as meant or retired, so a reader never trusts one that is not there. Issue: the
  table at the end of this file.

### Do not

- **Do not apply the trading-day grid to stored rows.** Why: it is a statistics construct; built into storage as a rule that drops
  Saturday rows, it would move sizing and the run-gap refusal. Issue: none.
- **Do not use other account sizes as a noise band.** Why: 2m, 5m and 10m are one book on the same window (mean differences
  of 3.0 and 1.7 points), while 500,000 and 1m are lumpier books, not small copies. Issue: none.

## Operations

### Do

- **Run the seed once, at the futures release, after the merge, from `services/trade-ngin/scripts/futures_release/README.md`.**
  Why: the seed rebuilds the stored backtests and live history on the merged engine, so every stored number comes from the
  code that runs daily. The seed targets CONSERVATIVE and is run by one operator. The README records what was not tested,
  among them any live run on this build, so those steps are checked at the seed. Issue:
  `AlgoGators/algogators#180`; the steps' reasons and checks are in [RUNBOOKS.md](../05_operations/RUNBOOKS.md).
- **Point the watchdog at CONSERVATIVE after the seed.** Why: `services/trade-ngin/scripts/check_live_trading.py` defaults
  to `QT_CONSERVATIVE_PORTFOLIO` and `.github/workflows/svc-trade-ngin.watchdog.yml` does not set `LIVE_PORTFOLIOS`, so until
  that variable names CONSERVATIVE as well as the QT book, a refused CONSERVATIVE date is not reported and the book can stop
  unseen. Tried: read in the code, not run (unmeasured). Issue: none; the step is in
  [RUNBOOKS.md](../05_operations/RUNBOOKS.md).
- **Re-seed when numbers move.** Why: the stored history must be what the merged engine produces, or the live book and its
  record disagree and every statistic mixes two engines. A PR either says it moves no stored number or lists what moved;
  when it moves them, the stored backtests (2y, 5y, 10y, 16y) and the live history are re-run on the merged engine and the
  seed's backtest and live-history folders rebuilt. New markets are the exception to prefer: add them from a
  stated date, as listing dates do, since a market added from a date changes no day before it, with a full re-seed only if
  needed and the reason written down. Issue: `AlgoGators/algogators#179`, `AlgoGators/algogators#184`.
- **Keep the stored-book refusal rule in view.** Why: a run that loads a different strategy id or sleeve than the stored
  book's would size and settle a book it does not own. Main has the one-rule form (AlgoGators/trade-ngin@3e354dcb, with
  AlgoGators/trade-ngin#155's message): a run refuses when the latest stored non-zero positions belong to a strategy id or
  sleeve it does not load (`services/trade-ngin/include/trade_ngin/live/stored_book_ownership.hpp`). A five-rule version was
  built and checked, then set aside on 2026-10-07, because book identity belongs to AlgoGators/algogators#157. Tried: of the
  56 modelled states, 11 pass on main and are refused by the five-rule version, for example a run sizing on 500,000 as a
  first run beside another id's results history. Issue:
  `AlgoGators/algogators#157`.
- **After a refusal, run the missed dates oldest first, then forward to the last stored date.** One date each, explicit, no
  `--send-email`, strictly in sequence. Why: each date settles the one before, so a single-date re-run leaves later days on
  the old book. Issue: none; `services/trade-ngin/docs/LIVE_RUN_CYCLE.md` holds the rule.
- **Keep the production form dated.** Production runs `<date> --send-email` (`services/trade-ngin/scripts/run_live_portfolio.sh`);
  a run with no date exits 2 (`services/trade-ngin/include/trade_ngin/live/run_args.hpp`). Test runs pass a date and omit
  `--send-email`. Why: the date fixes which row a run settles, so a dated run is repeatable. Issue: none.
- **Close the live failure modes on their issues.** Why: each is a way a day can be stored wrong or the book can stop
  without notice. A failed positions write `AlgoGators/algogators#158`, a run that exits 0 on a failed write
  `AlgoGators/algogators#160`, a held stale symbol `AlgoGators/algogators#170`, the classifier's first day
  `AlgoGators/algogators#171`.
- **Build e-mail changes on the Python report.** The book e-mail on main is the Python report only, the decided state
  (`services/trade-ngin/services/rpc/algogators_rpc/services/report`, called through
  `services/trade-ngin/include/trade_ngin/live/python_mail.hpp`); AlgoGators/trade-ngin#261 removed the C++ e-mail. Why: one send path means one place where the day's report is built from the
  stored day. Issue: none.

### Do not

- **Do not schedule futures Monday to Friday.** Why: the runner refuses a date whose previous day was not run, so a skipped
  weekend stops the book, and a book that does not settle every day drifts from the broker. Tried: under the run-gap
  refusal 1 of 21 dates ran, then refusal for good; on the code before that, costs were 6.1 times the seven-day run's and
  the database held 8 contracts against the broker's 64. The schedule is every calendar day
  (`services/trade-ngin/docs/LIVE_RUN_CYCLE.md`). Issue: none.
- **Do not patch out the run-gap refusal, insert placeholder rows, hand-edit rows or parallelise a replay.** Why: each makes
  a stored day no run built, so every later day settles on a book that never existed. Issue: none;
  `services/trade-ngin/docs/LIVE_RUN_CYCLE.md` holds the rule.
- **Do not drop `TZ=America/New_York` from the image on its own.** Why: the cron expression and the wrapper's date are
  written in Eastern time, so dropping it alone shifts the run date. Issue: `AlgoGators/algogators#185`.

## Git and issues

### Do

- **Follow the three standing merge rules.** Squash by default, no AI attribution, re-seed when numbers move. A merge
  commit is allowed when needed, and the PR says why. Why: one commit per PR keeps main's history one change per step, so a
  moved number can be traced to the PR that moved it. Issue: `AlgoGators/algogators#179`.
- **Take a migration's number at landing, and name it by file.** Why: two branches that pick one number collide, and a
  number applied to production cannot be reused. Tried: AlgoGators/algogators#153's text names migration 027, and that
  number is taken on main by `services/trade-ngin/migrations/027_qt_daily_cutoff.sql`; 041 is the highest file on main.
  Issue: `AlgoGators/algogators#153`, `AlgoGators/algogators#178`.
- **Read the refactor issues correctly.** The refactor is in four issues: `AlgoGators/algogators#152` (the futures runners
  onto one shared daily cycle), `AlgoGators/algogators#153` (the backtest window), `AlgoGators/algogators#154` (the
  comparison tooling) and `AlgoGators/algogators#155` (the equity runner onto the shared cycle). The per-step issues
  AlgoGators/trade-ngin#139 to AlgoGators/trade-ngin#143 and AlgoGators/trade-ngin#145 to AlgoGators/trade-ngin#147 were
  folded into these as their stages. AlgoGators/algogators#154 (the comparison tooling) comes first and needs the scrubbed
  scripts and read access; AlgoGators/algogators#155 waits until it is accepted. Why: a refactor must change no stored value,
  so when identity fails the rule is to stop and revert, not to widen the comparison, which would hide the change. Issue:
  `AlgoGators/algogators#152`, `AlgoGators/algogators#154`, `AlgoGators/algogators#155`.
- **Run the full serial suite on every commit of a merge branch.** Why: a subset misses what it does not run, and a clean
  textual merge can still break behaviour. Issue: `AlgoGators/algogators#182`.
- **Keep one owner where two issues share a block.** Why: two owners of one block either collide or each assume the other
  fixed it. `AlgoGators/algogators#158` and `AlgoGators/algogators#160` are the case on record. Issue: both.
- **Cite the archive branches for measured work, and do not delete them.** Why: they hold what the release left out, and the
  measurements in [LEVERS.md](../03_tuning_and_results/LEVERS.md) can be re-run only from them.
  `archive/keep-do-not-delete/s25-one-pass`, `step-edge`, `levers` and `futures-integration` are on `AlgoGators/trade-ngin`,
  not on `AlgoGators/algogators`. Issue: none.

### Do not

- **Do not send speed work through AlgoGators/algogators#152 without a speed baseline.** Why: its comparison
  (AlgoGators/algogators#154) proves stored values, not timings. Issue: `AlgoGators/algogators#152`,
  `AlgoGators/algogauge#10`.
- **Do not pre-assign migration numbers across open branches.** Why: the branch that lands second has to renumber files
  already applied elsewhere. Tried: 028 to 033 had to be renumbered to 034 to 039 (AlgoGators/trade-ngin#232). Issue: none.

## Docs

### Do

- **Update a tracked doc in the PR that changes the code it describes.** Why: a doc written before its code merges has to be
  written again. Issue: none.
- **Read a dated doc as a record of its date.** `services/trade-ngin/docs/DATA_DUPLICATION_FIX_TECHNICAL_SPEC.md` (dated
  2026-03-01) and `services/trade-ngin/docs/code_coverage/coverage_analysis.md` (a February 2026 snapshot, as its third
  line says) describe the code when they were written; the code and the reference docs hold the current state. Why: a
  dated doc read as current misleads. Issue: `AlgoGators/algogators#188` holds the reference-doc items.
- **Clear stale source comments in one comment-only commit.** Why: a comment that describes a removed state misleads the
  next reader of the code, and a commit that touches only comments moves no number. Issue: none.

### Do not

- **Do not quote performance figures from removed tracked-doc rows as current.** Why: they predate later decisions. Use
  [RESULTS.md](../03_tuning_and_results/RESULTS.md).

## Open design questions and their issues

These are the design questions in the futures book that have no decision yet. None of them moves a stored number until
it is decided. Where an open issue already covers a question, the Issue column names it, and the part it covers where it
covers only part; the issue owns that part and its text is the reference. A question with a dash in the Issue column has no
issue.

| Question | Issue | Why it matters |
|---|---|---|
| Should volatility be measured on business days (Sunday rows folded into Monday), as Carver does, instead of on the bars the feed prints? | - | Sunday counts as its own day because the Sunday evening session is a real exchange session that prints its own bar, and the runner trades on the bars the feed prints (decided on 2026-09-03, kept on 2026-10-28). Folding Sunday into Monday moves every volatility, forecast and limit, so it waits for a full engine retune; business-day spans moved the 16-year Sharpe from 0.21 to 0.27 on a rebuild outside the engine with no costs |
| Should the optimiser's covariance stop depending on the dates every symbol shares, or should the book decide per instrument? | `AlgoGators/algogators#177` item A4 (it asks for the channel by which a stale symbol moves the rest of the book) | One symbol's calendar moves every other row: the gold and silver micros' year read -9.25 percent against +7.22 percent on an aligned calendar |
| Should the instrument weights add Carver's group layer (asset class, group, instrument) instead of one layer by sector? | - | It moves risk between clusters: precious metals and developed FX would get about half their weight, emerging FX and meats about double |
| Should any of Carver's trend variants 12, 17, 18 or 19 be built? | - | On Carver's trend-only tables 74 and 78 strategy seventeen gains 0.01 of Sharpe and eighteen alone is lower; on his carry-and-trend tables twelve gains 0.02 at most (table 59) and seventeen and eighteen gain 0.05 and 0.04 (table 86). Each adds turnover to a book near its churn bar |
| Should sizing take an FX rate per instrument before any contract not priced in US dollars? | - | A non-USD contract would be sized off by its exchange rate, silently; removing the unused field closes the route |
| Should a trend window that starts on an unconfirmed vendor switch bar read that bar's roll field? | - | Consistency between the engine and a full rebuild; the effect is at most 1.4e-6 on one forecast and moves no stored figure |
| Should a sleeve's own risk refusal be reported as a refusal rather than as a portfolio risk module failure? | - | A deliberate hold reads as a broken run (exit 3, "risk module failed"); no shipped book has a sleeve-scope module |
| Should a roll leg be booked at a close at or below zero instead of stopping the run? | `AlgoGators/algogators#177` item A3 (the upstream rejection of a bar batch with a non-positive close) | The design allows such prices but the code stops the run; once the upstream rejection is narrowed to one symbol, the roll stop becomes reachable on a held symbol |
| Should listing-date switch fills count as their own class, like roll legs, instead of as strategy fills? | - | They add round trips to the churn measure the book is judged on |
| Should the weekday volatility be printed beside the stored statistic, or retired? | - | The stored volatility and the 20 percent target use different day conventions |
| Should the morning data rule have its own staleness setting, alert once per partial row, and have N and X replayed on more history? | `AlgoGators/algogators#176` part B item 5 (the replay of N and X) | The rule decides whether a bad data morning costs the desk a trading day; one partial Friday row can alert up to three times |
| Should Sunday P&L stay in the sizing-capital statistics, divided by Saturday's capital? | - | It touches the three statistics taken on the capital the book was sized on |
| Should the backtest log a price-source change only for the sleeves that hold the symbol? | `AlgoGators/algogators#183` item 6 (the live line) | Cosmetic: one line per sleeve, at quantity 0 included; no stored number |
| Should the roll rule follow the vendor's switch only near the deadline? | - | 38 of the rule's 39 close rolls fall in the 16-year window; the book held the symbol on 4, whose legs cost 144.92 dollars. The vendor's switch marks where volume moved |
| Should the optimiser block new positions in a contract about to roll? | - | An entry inside the roll window pays the roll's two legs almost at once |
| Should the speed list be re-measured on the gold and silver micros? | - | The list was measured on full-size GC and SI; the micros' costs may put their fast speeds above Carver's 0.15 limit |
| Should the one pass read its stale rule, window and search threshold from the config keys that claim to set them? | `AlgoGators/algogators#161` section 2 and `AlgoGators/algogators#137` | The keys say they govern the futures optimiser, but a changed value would have no effect on it |
| Should the attenuation be measured on the engine (off, and Carver's ratio form), or kept in the engine's form on Carver's reason (AFTS, strategy thirteen)? | - | It moves every forecast; the one measurement is outside the engine, on the unrounded book (16-year Sharpe 0.13 without it against 0.20 with it) |
| How does a desk save carry `overlay_covariance`, so that a saved strategy config does not refuse the futures book? | - | A saved row without the key would hold the book every day until corrected |
| Should `per_contract_switch_bar` and the off branch of `per_contract_rolls` be removed, or kept with a reason? | - | Each keeps a second code path, and a wrong value on a server changes prices silently |
| Should the morning data rule judge only the symbols the book trades, instead of every series printed in the last 60 days? | - | A series no longer pulled can hold a day that would otherwise run, and can push the real missing symbol out of the alert |
| First notice days come from the exchange rule and are not checked against data; the last trading day comes from the stored expiration | `AlgoGators/algogators#183` item 4 | Deadlines are safe on the stored expiration, but every mismatch with the rule logs a warning |

Four small points are held in this table alone, with no issue:

| Point | Why it matters |
|---|---|
| On a listing switch bar, the predecessor's position row names the copied bar's contract | Quantity and P&L are right, and no live chain can hold a predecessor at a switch |
| The closing listing-switch leg is recorded at the copied close (21.60 against the old contract's 21.625) | Declared; the P&L stops at the old contract's own close |
| Before June 2026 the backtest stamps a Friday bar's trades on Sunday and a live chain on Saturday | Same rows otherwise; a comparison keyed on the stamp date lists them as differences |
| The strategy's drawdown line measures cumulative P&L against its allocated capital, not peak to trough, and only warns | It is a log line, not a gate: sizing and the risk overlay own the response to a loss |
