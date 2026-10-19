# Brief #6: Kalshi perps funding carry (user-supplied research, 2026-10-15)

Last revised: 2026-10-15

Supplied by the user. Its figures are leads to verify against raw data, not facts. The source list is at the end.

## Bottom line (from the brief)
- The one perp idea that clears the $1k-deployed bar is **spot-hedged funding carry**: short the Kalshi crypto perps where
  funding has been persistently positive (BTC, SOL, XRP) and hold spot on a US exchange.
  - About $500–1,000/yr gross on a $10k account, or about $300–600/yr above T-bills.
- Two early-life carries are richer per dollar now but likely decay within months:
  - platinum perp vs PPLT;
  - US500 perp vs SPY.
- There are no perp liquidity rewards for retail.
- At Kalshi, "scalar" is a settlement outcome, not a product. The brief found no product called a "settlement floor".

## 1. Contract specs
- **Funding**
  - Every 8 h for crypto (12 AM, 8 AM and 4 PM ET); daily at 10 AM ET for precious metals.
  - The rate is the TWAP of 480 one-minute candle premiums (perp vs index), clamped to ±2% per interval.
  - **If |rate| < 0.01% the rate is set to zero.** This deadband is 10.95%/yr on an 8 h schedule and 3.65%/yr on a daily one.
  - The interest component is 0. Funding pauses if the CF Benchmarks feed is down.
  - Any part of an interval when the underlying market is closed does not count.
- **Index**
  - BTC uses the CF BRTI (1 s updates); contract size 0.0001 BTC; trades 24/7; variation settlement at 12 PM and 4 PM ET.
  - Gold and silver use Pyth prices.
  - US500 uses the MerQube US Large Cap price-return index.
  - The perps WS ticker carries `reference_price`, `settlement_mark_price`, `liquidation_mark_price`, and `funding_rate`
    with `next_funding_time_ms`. The mark methodology is not published.
- **Max leverage**

  | US500 | Gold | Silver | Platinum | Palladium | Aluminum | Copper | BTC | ETH | LINK | XRP | SOL | HYPE |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|
  | 13.7x | 15.2x | 8.2x | 6.6x | 4.5x | 8.0x | 7.8x | 5.8x | 4.4x | 3.4x | 2.7x | 2.6x | 2.1x |

  - Allowed leverage falls as size grows.
  - Liquidation happens at maintenance margin, which is about opening margin ÷ 1.3.
  - BTC distance to liquidation, long / short: 2x 42.6% / 31.7%, 3x ~23% / 17%, 5x ~8% / 5%.
  - The margin cash account earns 3.25% APR (needs a $250 average balance). Margin requirements can rise with volatility.
- **Fees**
  - Charged on notional at open and at close.
  - Entry tier (as of 2026-09-25): 0.120% taker / 0.020% maker. Prediction-market volume counts toward the tier.
    One source says 0.05% maker; check with `GET /margin/fee_tiers`.
  - The Temporary Perpetual Fee Rebate (to 2026-12-31) covers Self-Clearing Members only, not retail.
- **Access**
  - Requires a margin account application with a questionnaire and a tutorial. Retail trades through Kalshi Prime (FCM).
  - The perps REST and WS API is "rolling out member by member"; check with `/margin/enabled`.
  - On margin there are no batch orders, no queue positions and no historical-data endpoints.
  - **The perps margin account is segregated from the predictions balance**, so it competes for capital with the
    resting-bid book.

## 2. Funding data and carry math
- **Free point-in-time funding history:**
  `GET https://api.elections.kalshi.com/trade-api/v2/margin/funding_rates/historical`
  - Public, no auth. Optional `ticker`, `start_ts`, `end_ts` (Unix s).
  - Returns `market_ticker`, `funding_time`, `funding_rate` and `mark_price` per settled event.
  - Crypto settles at 04, 12 and 20 Z; metals at 14 Z.
  - Related endpoints: own funding history, funding rate estimate, premium index.
  - Third-party mirror: predictionmarketspicks (CC BY 4.0 JSON/CSV, refreshed every 5 min).
- **History through Oct 15, as given in the brief**
  - BTC (402 prints since Jun 3): longs paid an average 8.5%/yr.
    - Longs paid in 49% of windows, shorts in 0%, and 51% of windows paid nothing.
    - Last 30 days: +0.0097% per window. Nonzero prints run 0.010–0.028% per 8 h.
  - 30-day annualised funding on the Oct 15 board (share of zero windows in brackets where given):

    | BTC | ETH | SOL | XRP | Gold | Silver | Platinum | Palladium | Copper |
    |---|---|---|---|---|---|---|---|---|
    | +10.7% | −0.2% (86% zero) | +19.6% (83% zero) | +20.6% (58% zero) | +3.9% (37% zero) | +4.0% | +18.6% (17% zero) | +4.7% | −12.8% |

    - US500 last print: +0.0196% (22% zero). 28 perps live, $52.8M OI.
  - Brief's spot checks:
    - Platinum: 0.026–0.083%/day in late September (~16%/yr).
    - Gold: 0–0.046%/day.
    - SOL: ~0.013% per 8 h over Sep 25–Oct 4 (~14%/yr).
  - Kalshi Research: signed difference to spot over Jun–Sep was $16.39 for BTC and $0.11 for ETH, vs $49 / $1.33 on
    Binance. Kalshi BTC funding was 0.03–0.06%/day.
  - Offshore BTC funding was ~11% on Oct 6 (OKX 6.4% to Kraken 15.4%; Kalshi 13.7%). The edge is access, not a
    cross-venue gap.
- **Carry arithmetic (BTC, $10k account)**
  - Position: short $5k KXBTCPERP at 1.5x ($3.3k margin, liquidation ~+60% away), long $5k spot, $1.7k reserve.
  - Funding income: $425–535. Interest on margin cash: $163.
  - Costs: perp maker fees ~$2; spot round trip 0.5–1.2% ($25–60).
  - Gross ~$550–650/yr, or ~$200–300 above T-bills (3.81%).
  - Adding SOL and XRP sleeves: gross $800–1,100 and net $400–700, with more liquidation risk. Keep leverage ≤ 1.5x.

## 3. Liquidity rewards
- LIP does not apply to perps.
- Perps use the Designated LP program:
  - 6 DLPs each on BTC, ETH, SOL, XRP, DOGE, HYPE, LINK, LTC, BCH, SUI and SHIB.
  - 4 DLPs each on gold, silver and platinum.
- The DLP program, the SCM fee rebate and LIP all end around 2026-12-31 / 2027-01-01. Expect funding and spreads to shift
  in January 2027.

## 4. Other launches
- **US500 perp** (launched Oct 6)
  - Settles to MerQube price-return, $1 per point. Trades Sun 6 PM to Fri 5 PM ET.
  - Funding is set daily at 4 PM on index days; overnight moves are excluded.
  - Fair funding (r−q)/365 ≈ 0.007%/day, which is below the deadband. Early prints run ~0.02%/day, about 3x fair.
- USTECH100 and WTIFUTURE perps are live (WTI funding is negative: roll-down, not an edge). A separate WTI perp is in its
  CFTC 45-day window.
- **15-minute series**
  - Crypto 15M settles on 60 s CF averages. Commodities settle on Pyth 1-min closes; FX on the Pyth rate.
  - KXINX15M and KXNDQ15M show a placeholder source ("For example, Google Finance").
  - KX2/5/10/30YRRATE15M name the US Treasury as source. None of these were trading.
  - Free settlement-family feed: perps `reference_price` (Pyth for metals, BRTI for BTC).

## 5. Ranked ideas
1. **Spot-hedged funding carry: short KXBTCPERP / KXSOLPERP / KXXRPPERP, long spot** (Coinbase Advanced or Kraken Pro).
   - Expected: $800–1,100/yr gross on $8–10k.
   - Entry: trailing 30-day funding > 6%/yr **and** at least 3 of the last 9 prints nonzero.
   - Sizing: at most 50% of the account per asset; leverage ≤ 1.5x short.
   - Rebalance: when the margin ratio reaches 1.3× maintenance.
   - Exit: trailing 30-day funding < 4%/yr for 14 days, or zero-window share > 75%.
   - Traps:
     - Funding regimes last months, so 4 months of history overfits.
     - Annualising only the nonzero prints overstates the yield.
     - The spot fee dominates costs.
   - Erasure signals: zero-window share rising; OI growing faster than volume; the Dec-31 DLP subsidy end.
   - Confidence: high on mechanics, medium on persistence.
2. **Platinum early-life carry: short KXPLATINUMPERP vs long PPLT.**
   - Expected: $500–800/yr for 1–4 months.
   - Entry while trailing 10-day funding > 8%/yr; leverage ≤ 1.5x.
   - Exit on two consecutive zero prints, or if OI triples.
   - Only ~11 data points so far. PPLT tracks spot, not the Kalshi index. Confidence: low-medium.
3. **US500 financing: short US500PERP vs long SPY.**
   - Expected: $250–350/yr on $6.7k if early prints persist.
   - Entry: 5 consecutive prints ≥ 0.015%/day; leverage ≤ 3x. Exit after 3 consecutive zeros.
   - Only 7 prints so far. Confidence: low-medium.
4. **Watch item: Treasury-yield and index 15M rules.**
   - If KX10YRRATE15M lists with a once-daily Treasury source and "at least" ties resolve YES, every window that does
     not span the daily publication is a certain YES.
   - Pull `rules_primary` the day it lists.

Not worth building, per the brief: funding-print timing, perp market making against subsidised DLPs, gold and silver
carry (~fair), ETH carry (~0), WTI negative funding.

## Sources (from the brief)
Kalshi: How Funding Works; BTC perp contract specs; Perpetuals Learn; Applying for Perpetuals Access; Perps Fees
Explained; Perps API overview; Get Historical Funding Rates; Kalshi Research "American Perpetuals"; LIP; Liquidity
Provider Program (DLP table); CFTC filings on the Perpetual Fee Rebate (2026-06-24, 2026-09-02); PredictionMarketsPicks
perps board, bitcoin perps page and "How Kalshi 15-minute markets settle"; Funkleville (US500 settles to MerQube);
TradingView/Finance Magnates and PrimeXBT (US500 launch); DeFiRate (perp funding across venues); BloomingBit (WTI perp
filing); Kalshi Combos (scalar settlement); Kalshi API changelog; Traders Union (3-month T-bill yield).
