Last revised: 2026-10-15

I trade a small account (under $10k) on Kalshi, a US CFTC-regulated prediction market exchange. I also use Polymarket international data for research, but I can't trade there. I'm looking for strategy ideas that a small, systematic, mostly automated trader can test on historical data. I pay $0 for data, so free public sources only (free API tiers are fine). To be worth running, a strategy must keep at least about $1,000 of capital in the market on average.

**What we have already tested.** About 18,000 backtest variants, with causal queue-aware fills, fees, out-of-sample splits and deflated Sharpe. Please don't propose these again unless you have a specific new angle:
- **Forecast-model edges.** These are already priced in: weather (GFS/ECMWF/NBM), economic nowcasts (Cleveland Fed, GDPNow, jobless claims), Fed futures vs Kalshi Fed markets, options-implied fair value for index, commodity and crypto brackets, gas prices vs RBOB, CPI mechanics, measles counts, TSA, Spotify, Rotten Tomatoes review arithmetic, Netflix/FlixPatrol.
- **Mention markets.** All rejected: word base rates, earnings-call and FOMC transcript count models, Trump speech models, Wikipedia and GDELT news attention, Yes-bias taking, in-event hazard decay, logical nesting between related markets, Kalshi vs Polymarket lead-lag.
- **Market making.** A blind two-sided book loses in every category (informed flow fills you). Toxicity-conditioned and state-conditioned maker books fail too.
- **Cross-venue arbitrage.** Kalshi vs Polymarket, PredictIt, ForecastEx, Gemini and sportsbooks: rejected or too small.
- **Sports.** Taking or making against the de-vigged sharp line, and in-play longshot fades: rejected.
- **Microstructure.** Round-number anchoring, time-of-day effects, following large prints, newly listed series, post-move fade or follow: rejected.

**What works, but only at small size.** Each of these deploys well under $1k on average:
- Resting bids on 91–99¢ favourites in economic, Fed and gas markets.
- Rotten Tomatoes and Netflix favourites in the last 24 hours before close.
- Weather and rain markets already decided by station observations.
- Taking NO during live earnings calls.
- Kalshi Liquidity Incentive Program rewards. The best lead: bidding about Target/4 lots one tick behind the best bid on state gas markets makes our price the scoring reference. It is fragile to a single competitor.

**What I'm looking for:**
1. **Structural or mechanical edges**, where profit comes from rules, incentives, settlement mechanics, scheduled flows or market design rather than out-forecasting the crowd. Examples: incentive and rebate programs, settlement-source quirks and revision rules, new market launches and rule changes, roll and expiry mechanics, fee asymmetries, forced or uninformed flow.
2. **Speed edges a retail trader can actually win.** Seconds to minutes, not milliseconds, ideally from free public feeds. (We are now testing live speech-to-text for mention markets.)
3. **New Kalshi market families** (launched in 2026 or upcoming) where the crowd is still learning the settlement rules.
4. **Ways to raise capacity.** Edges that deploy more capital per opportunity, or many uncorrelated small edges that can share one account.
5. Anything from academic papers (2024–2026), practitioner write-ups or exchange filings showing a persistent prediction-market inefficiency that our list doesn't cover.

**For each idea, give:**
- **Name and one-sentence mechanism:** why the mispricing exists, and why it persists against faster or bigger traders.
- **Market:** the exact Kalshi series or market family (tickers if you know them) and the settlement source.
- **Data:** free data needed, with its URL or API, how much history exists, and whether timestamps are point-in-time.
- **Test:** a testable rule (entry, exit, sizing), plus the main look-ahead and overfitting traps for this specific idea.
- **Capacity:** expected edge per contract and capacity in dollars of average capital deployed, with your reasoning.
- **Competition:** how quickly a faster competitor would erase it, and an observable early-warning sign of that.
- **Evidence:** sources (papers, filings, articles) with links.
- **Confidence:** high, medium or low.

Rank the ideas by expected dollars per year at a $5k–10k account after fees and adverse selection, not by novelty. Fewer, well-specified ideas are better than many vague ones. Exclude anything that relies on non-public information, controllable outcomes, or market manipulation.
