# Claude Research prompts: niche areas (2026-10-15)

Last revised: 2026-10-15

Each prompt is self-contained. Paste one per Claude Research session, then paste the result back into Claude Code.

---

## Shared context (already included in every prompt below)

> I run a small (<$10k), systematic, mostly automated account on Kalshi, a US CFTC-regulated exchange.
> Polymarket international is research-only for me; I can't trade there. I pay $0 for data, so I need free public
> sources. A strategy matters only if it keeps at least about $1k in the market on average, or adds capacity to a
> combined book of small uncorrelated edges.
>
> **Already tested (about 18,000 backtest variants, causal queue-aware fills, fees, deflated Sharpe). These mostly
> fail:**
> - Forecast-model edges: weather, economics, Fed futures, options-implied brackets, gas, CPI, measles, TSA,
>   Spotify, Netflix, Rotten Tomatoes review counts.
> - Mention-market models: base rates, earnings/FOMC/Trump transcripts, Wikipedia/GDELT attention, Yes-bias taking,
>   live speech-to-text (an automated bot reprices before public streams).
> - Blind or state-conditioned market making. Passive fills are informed in every category.
> - Cross-venue arbitrage.
> - Sports vs sharp lines.
> - Microstructure anomalies.
> - Long-dated tail-NO carry.
> - 15-minute metals settlement.
>
> **What works but is small:** 91-99c favourite resting bids (econ/Fed/gas), Rotten Tomatoes and Netflix
> favourites, station-decided weather, live earnings-call NO. Together they make a combined book of about $4k/yr at
> $10k (a backtest that has been audited; forward test running).
>
> **Best lead:** the Kalshi Liquidity Incentive Program (LIP). Bidding Target/4 lots one tick behind the best bid
> on state gas markets (no designated market maker there) makes our price the scoring reference. The program ends
> 2027-01-01.
>
> **For every idea, give:**
> - the mechanism, and why it persists against faster or larger traders;
> - the exact Kalshi series, tickers and settlement source;
> - the free data needed (URL/API, history, whether it is point-in-time);
> - a testable rule (entry, exit, sizing) and its look-ahead and overfitting traps;
> - edge per contract and dollars of capital deployed on average;
> - how a faster competitor would erase it, and an early warning sign;
> - sources with links, and confidence (high/medium/low).
>
> Rank by expected $/yr at a $5-10k account after fees and adverse selection. Fewer well-specified ideas beat many
> vague ones. Exclude anything that relies on non-public information, controllable outcomes or manipulation.

---

## Prompt 1: settlement rulings, rule clarifications and contract-language edges

[Shared context above]

Research where Kalshi settlement depends on interpretation rather than a clean number, and whether traders
systematically misprice how Kalshi will rule. Our own data has one example. On the January 2026 FOMC press
conference, the KXFEDMENTION word "Renovation" was repriced to 99c when said, then fell back. About 107k NO
contracts traded at 1-20c before it settled YES, because the rules text already decided the case.

Find and analyse:
- **Mention-market grammar disputes.** Plurals, possessives, compounds, homophones, quoted clips, non-designated
  speakers. Cover Kalshi's published rulings and clarifications, and any public record of disputed mention
  settlements.
- **Mid-life rule clarifications.** Kalshi's market "clarification" notices and rule amendments: how often, which
  families, and how prices react, and how fast.
- **Settlement-source edge cases.** Revisions, late publications, source outages, "last fair price" clauses,
  voided markets, accelerated determinations.
- **New contract features.** Settlement floors (settlement_bounds_type / settlement_floor_dollars, Oct 2026) and
  payout-curve scalar contracts (Payout Shape Methodology v1.0; MLBGAMESSCALAR filed 2026-09-28), with their
  rounding rules.

Deliver a catalogue of dispute types with historical examples (dates, tickers, how they resolved, prices before
and after), and a systematic rule a bot could apply ("when X happens and the rules say Y, buy Z") with a
backtest plan using Kalshi's public historical trades and markets API.

---

## Prompt 2: Kalshi incentive, rebate and promotion programs

[Shared context above]

Map every current and upcoming Kalshi incentive program a regular retail member can earn from, and how to earn
from each without abusing it. Include:
- **The LIP.** Terms from the 2026-07-15 CFTC amendment and the Help Center. Which pools are largest relative to the
  competing depth? Which series have no designated market maker (the Liquidity Provider Program list)? How likely
  is renewal after 2027-01-01? Weigh CFTC Letter 26-23 and the Volume Incentive Program ending 2026-10-13.
- **The Sports Prop Combo Market Component Legs LIP** (up to 25% of combo fees shared with makers in prop legs):
  exact terms, how share is computed, eligible series, historical payouts if public.
- **Other programs:** referral credits, deposit or trading promotions, fee holidays, maker-fee exemptions,
  APY/interest terms, collateral-return netting.
- **Regulatory filings:** CFTC filings that announce new programs. Search cftc.gov self-certifications from Kalshi
  for 2026.

For each program, give the expected $/day per $1k of capital for a small participant, how competition dilutes it,
the abuse and revocation boundaries in the terms, and how I could verify actual payouts (no payout API exists).

---

## Prompt 3: sports combos, props and RFQ structure

[Shared context above]

Research the economics of Kalshi sports combos (multivariate events, RFQ) and player props for a small, slower
participant. Context: our test on Nov 2025-Jan 2026 found no combo premium over the product of the legs for 2-10
legs (median 0.97). Moshrefi (arXiv 2607.14430) found inflation at 6+ legs in Apr-May 2026. Fast quoters answer in
under 200 ms (Oddpool census, May 2026).

Find:
- which RFQs the professional quoters decline or quote wide (leg count, sports mix, time of day, off-hours tennis
  or esports);
- whether requester behaviour (bots probing at about 13.5 RFQs/s) makes quoting unprofitable;
- the fee treatment of quoters (maker-equivalent fees, the under-5-second multiplier);
- free sources for real-time leg fair values;
- how prop-leg books behave around kickoff and in-play (we now record NFL/NBA prop and line books);
- whether quoting the "decline" segment at a slower latency (about 1 s) could work, with a paper-test design using
  the public RFQ feed.

---

## Prompt 4: data published before the settlement source (partially decided markets)

[Shared context above]

Find Kalshi series where the settlement value is partly or wholly determined by public data that appears before
the official settlement source publishes, or before trading closes. We already tested and rejected:
- TSA weekly;
- AAA gas weekly/monthly;
- NOAA monthly temperature vs ERA5;
- monthly rain/snow totals;
- Spotify weekly;
- first vs final Nielsen numbers;
- jobless claims;
- CPI mechanics;
- measles counts;
- daily temperature (now settled on The Weather Company).

Search the full current Kalshi series list for other aggregates: weekly and monthly counts, averages and indices
built from daily public data (energy, transport, government statistics, entertainment charts, app rankings, sports
season stats, company KPIs). For each candidate give:
- the settlement source;
- what is public earlier, and how much earlier;
- the fraction of the value already known at decision time;
- typical liquidity;
- whether the market already reprices on the early data (if possible, cite evidence such as price moves at
  release times).

---

## Prompt 5: Kalshi perpetual futures and new product lines

[Shared context above]

Kalshi now lists perpetual futures (for example KXBTCPERP, KXETHPERP, KXSOLPERP, KXGOLDPERP, KXSILVERPERP and many
crypto perps), each with 3-6 designated liquidity providers, plus new 15-minute FX and commodity series.

Research:
- **Perp contract specs:** funding-rate formula and schedule, index and mark price sources, margin and leverage
  limits, fees, position limits, and whether retail US members can trade them.
- **Funding and basis carry:** whether funding rates on Kalshi perps diverge from offshore and CME equivalents, so
  that a spot-hedged funding carry (for example, long spot BTC via a US exchange and short a Kalshi perp when
  funding is positive) is available to a US retail account after fees. What capital and margin does a $5-10k
  account need, and what is the historical funding distribution? Free data sources for Kalshi funding history
  are needed.
- **Liquidity rewards on perps,** if any.
- **Other new Kalshi products launched in 2026** (index and Treasury 15-minute series, scalar contracts, settlement
  floors): early-life mispricings while the crowd learns the rules.

Note that Polymarket-only products are research-only for me.

---

## Prompt 6: uninformed, forced and calendar flow

[Shared context above]

Our results show passive fills are informed almost everywhere, so I want situations where the flow on the other
side is predictably uninformed or forced. Research:
- **Retail-heavy windows:** NFL Sundays, prime-time games, viral events, app push notifications or promotions.
  Is there evidence the flow is less informed then, for example from Becker's maker-taker data by time, or from
  Kalshi/Polymarket studies?
- **Mechanical flow:** auto-settlement and rollover patterns, weekly or monthly series that relist, index or
  ladder rebalancing, users closing positions before expiry to free collateral, margin-driven liquidations on
  perps.
- **Tax and calendar effects:** year-end, month-end, payday.
- **Promotion-driven flow:** deposit bonuses or referral campaigns that push new users to trade particular markets.

For each, say how to detect the uninformed window in public trade data (trade size, timing, taker side), the
expected adverse-selection reduction for resting orders, and a testable rule. Cite academic and practitioner
evidence (2024-2026).
