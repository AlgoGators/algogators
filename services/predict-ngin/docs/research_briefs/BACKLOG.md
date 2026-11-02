# Research backlog

Last revised: 2026-10-17

Queue of ideas still to test, fed by the user-supplied briefs in this folder. Before starting anything, check
docs/RESEARCH_LEDGER.md: rejected ideas are not retested unless their `retest_if` condition is met. When an idea is
tested, add its ledger entry and tick it off here.

**Docs: topic names, not dates.** Name docs by topic, never by date (`docs/MMQ_QUEUE_AWARE_MARKET_MAKING.md`, not
`docs/MMQ_QUEUE_AWARE_MARKET_MAKING_2026-10-17.md`). Keep a `Last revised: YYYY-MM-DD` line under the title and
update it whenever the content changes. Update the existing topic doc in place instead of creating a new dated
copy; put dated facts inside the doc (a dated results table, or a History section for superseded versions).
A new brief gets a topic file name (e.g. `mention_markets_framework.md`); its date goes in `Last revised:` and in the
brief's own text.

## Briefs
- `structural_edges_opus_research.md`: brief #5 (Claude Research): LIP at scale, combo RFQ, TSA, box office, election call window, 15-min metals, APY/netting.
- `mention_markets_framework.md`: brief #4, mention-market framework (hazard, Hawkes, reversion, logical/cross-market arb, backtest mandates).
- `settlement_sources_incentives.md`: settlement sources, revision rules, Kalshi incentives, documented
  settlement-source cases.
- `niche_edges_1k_book.md`: measles, temperature lock-ins, state gas, RT, Musk tweets, Netflix, mentions,
  TSA, claims, Polymarket city temperatures.
- `small_capital_families.md`: maker-taker gaps by category, mention Yes Bias, A1/A2/A3, B1, C1, D1, E1, F1.

## In progress (round 4, launched 2026-10-14)
- [x] db: Fed/SOFR futures (Databento) filter for the Fed favourite maker; long-horizon Fed vs futures. Done: S1 capacity_capped (+$474-588/yr at $10k OOS, <$1k deployed); others rejected; $4.22 spent.
- [x] twc: settlement-source map; TWC vs CLI vs ASOS; hourly temperature index markets. Done: rejected (TWC = CLI on 2,837/2,838 days; hourly index market beats nowcasts).
- [x] snap: snapshot-source markets (RT review arithmetic, Netflix Top 10, viewership, box office). Done: RT late favourite maker capacity_capped; review arithmetic rejected; Netflix FlixPatrol unproven.
- [x] uma: Polymarket UMA disputes (knowledge only). Done: buy-proposed-outcome unproven (fragile, DSR ~0 repo-wide); no Kalshi analogue.
- [x] pmfix: Polymarket YES/NO token normalisation across all readers. Done: helper + data/polymarket_clean + patched readers; 680 tests pass.
- [x] measles: KXMEASLES vs CDC counter and state departments. Done: no edge; forward tests F1 (late-season nowcast) and F2 (PA dashboard timing) frozen; collector wired into watchdog (user-approved).
- [x] gas: state-level AAA gas vs RBOB lag. Done: rejected (market beats model 3-4x on RMSE); yearly max/min tail-sell lead unproven, paper rule resolves 2027-01-01.
- [x] nowc: jobless claims; earnings-mention base rates from SEC press releases. Done: both rejected; base rates already in price.
- [x] polyx: Musk tweet counts and secondary-city temperatures (knowledge only). Done: tweets no edge; NOAA-era dead-bucket lock-in capacity_capped (Polymarket intl only).
- [x] lipaudit: LIP terms audit done; "fill the gap" (Gd) 14-day forward test from 2026-10-14 21:00 UTC.
  Pending: user asks Kalshi support whether 1c Target-size bids are eligible.

## Round 5 (from brief #3; launched 2026-10-15)
Agents: mkgap (maker-taker gap), ment5 (Yes Bias + A1 + A2), cult (B1), e1cnt (E1), rain (F1), parlay5 (parlay + C1 recorder assessment).
Held back: D1 (needs A1/B1 results), C1 build (needs a recorder group, user approval), A3 (last).
- [x] (a1fool: rejected) A1 mid-cap earnings-mention Yes-fade maker with a negative-binomial count model (causal fills, kill rule
  < +1.5c per filled contract over >= 300 fills). Builds on nowc's results.
- [x] (mkgap: blind rejected, favourite residue capacity_capped) Maker-taker gap by category on our own tape (replicate Becker and the GitHub tape study, causal fills): where
  is the maker premium, and does it survive queue position? This decides which families to pursue.
- [x] (rejected) Mention Yes Bias: replicate Deleep et al. on Kalshi mention tape with lifecycle-timing control.
- [x] (cult: capacity_capped) B1 cross-family culture maker book (RT beta-binomial, Netflix, YouTube view ladders, Broadway attendance).
- [x] (FOMC NB rejected; count rungs unproven) A2 count-threshold ladders (FOMC transcripts 2018+, Trump): NB shape vs rung prices.
- [x] (e1cnt: taker rejected, NB maker unproven) E1 Truth Social post / executive-order count markets (Hawkes/NB).
- [~] (recorder group k-sports-props live; combo LIP unproven) C1 sports prop-leg quoting + combo-leg LIP: needs prop books in the recorder first (add a recorder group).
- [ ] D1 LIP overlay on A1/B1 books.
- [x] (rain: decided-YES taker capacity_capped, others rejected/unproven) F1 rain-city binaries in the last 3 hours (radar/METAR nowcast, LIP).
- [x] (parlay5: rejected; RFQ quoter unproven) Parlay overpricing vs product of legs (Moshrefi); relates to the existing kalshi_parlay_rfq_quoter entry.
- [ ] A3 in-event hazard quoting on live speech (high engineering cost; last).

## Round 6: mention markets (overnight run, launched 2026-10-15 ~22:30 ET)
- [x] mhaz (rejected; live earnings taker-NO capacity_capped): in-event hazard decay of still-open words (q(τ) from first-mention timing).
- [x] mxarb (nested arb capacity_capped; cross-venue and siblings rejected): logical subset constraints within Kalshi; Kalshi vs Polymarket mention gaps and lead-lag.
- [x] mrev (rejected): passive-flow toxicity map by state; logit-shock mean reversion.
- [x] mgdelt (null on train; full-text DOC retest optional): GDELT news attention and transmission (quick kill if no gain beyond price).
- [x] a1fool (A1 with Motley Fool transcripts): rejected. [x] trumpx (Trump speech models): null, MNPI-risk family.
- [x] kq: Kalshi open questions answered (docs/KQ_KALSHI_OPEN_QUESTIONS.md).
- [x] lipscan (book rejected; gas reference posture unproven, forward prereg) + lipgas audit (survives with corrections).
- [ ] lipx: reference posture across all LIP series (running).
- [x] cult B1: capacity_capped. [x] engopt merged (25x faster multi-strategy runs). [~] tape refresh running (~2 days).

## Round 7 (brief #5, launched 2026-10-15)
- [x] feeaudit (book unchanged; netting +$250/yr at $10k): point-in-time maker fees + per-order rounding + APY/netting in capbook.
- [x] elex26 (capacity_capped; Nov 3 paper only): midterm called-not-settled window; Nov 3 prereg and call logger.
- [x] metals15 (rejected; Pyth now needs a key): 15-min metals/index settlement-rule mismatch.
- [x] asr (rejected: a bot reprices before public streams). [x] tailcarry (rejected). [x] lpland (state gas has no designated LP). [x] capfwd runner scheduled (120 days from 2026-10-16). [x] lipbot pilot code (dry-run, awaiting review).
- [ ] combo retest on Apr-May 2026 (after tape_refresh reaches May).

## Round 8 (2026-10-15)
- [x] postevent: post-transcript mention convergence: rule rejected, idea unproven (docs/POSTEVENT_CONVERGENCE.md).
- [x] Forward collectors deployed on atlas for UFLOW / EarlyInfo / settlement rules (pn-aaasrc, pn-earlyinfo*, pn-srwatch, recorder k-uflow-open).

- [x] perps: Kalshi perp funding carry, tested on the public funding history (5,896 prints, all 29 perps) plus
  Coinbase spot, Yahoo ETFs and Hyperliquid/OKX funding. Binance is geo-blocked.
  - Idea 1, spot-hedged carry: **unproven (marginal)**. +$30 to +$53/yr above T-bills on $10k at 0.5% spot cost (audited, no leak; DSR 0.17);
    negative at 1.2%; H1 negative. Break-even funding is 4.35%/yr before fees.
  - Ideas 2 (platinum/PPLT) and 3 (US500/SPY): unproven, with 23 and 10 prints.
  - Idea 4 (15M yield/index rules): rejected. The series are Pyth-sourced, "at least" is continuous, and no markets
    are listed.
  - Forward collector `perps_funding_collect.py` is written but **not scheduled** (needs approval).
  - [ ] **Re-run on/after 2027-01-15** (user-approved 2026-10-15): DLP programme and SCM fee rebate end 2026-12-31, funding likely shifts.
  - Authenticated GETs with the existing key (2026-10-15): `/margin/enabled` was false, then true after the user applied the same day (balance $0, no positions);
    fee tiers confirmed: crypto 0.12% taker / 0.02% maker; metals + US500/USTECH100 0.0072% taker / 0.0012% maker.
  - Docs: `docs/PERPS_FUNDING_CARRY.md`. Ledger: `scripts/research/ledger_entries/perps.yaml`.
  - [ ] PAPER forward test of the spot-hedged carry (user-approved 2026-10-15): prereg frozen in `docs/PERPS_CARRY_FORWARD_PREREG.md`, runner `scripts/live/perps_carry_paper.py` (one GET-only pass per run, every 15 min), collector `perps_funding_collect.py` made unattended-safe (every 15 min). Both await a `config/collectors.yaml` entry; evaluate 2027-01-15.

## Cross-cutting caveat (2026-10-16, from TAPE_REFRESH)
- The HuggingFace-sourced `data/kalshi/trades_fixed` tape is complete only through 2026-01-28. 2026-01-29..02-11 holds
  only ~1.7k snapshot tickers (a survivor subset). Studies whose test windows run into Feb 2026 (mkgap, FLB audits,
  sharp, fin_*) include up to two biased weeks. Re-run them on `tape_sql()` (src/trading/arbitrage/kalshi_tape.py)
  once `scripts/research/tape_refresh.py` has caught up (about 2 days from 2026-10-15), and rebuild derived tapes
  (fin_D, audit_flb, micro/base, ment5).

## Infrastructure (after the strategy batch)
- [ ] Optimise the backtesting engine for running many strategies at once:
  - share loaded data across strategies (one load, many consumers; memory-mapped Arrow/Parquet, no per-strategy
    copies);
  - faster DB reads (columnar store / DuckDB over Parquet, predicate and column pushdown, partitioning by
    series/date, pre-built indexes for trades_fixed and live_books);
  - parallel execution limits (process pool sized to RAM, not CPU count);
  - profile first: measure where time and RAM go on a representative multi-strategy run before changing anything.
