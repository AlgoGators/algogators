# Polymarket (intl) Intra-Venue Arbitrage in 2026: Binary Complement, NegRisk, Combinatorial (2026-10-12)

Last revised: 2026-10-14

**Bottom line.**
- **Binary complement (YES+NO < $1) is structurally dead on the book.** A binary market has one order book; the
  NO book is the exact mirror of the YES book (ask_NO = 1 - bid_YES; checked on 912 random YES/NO book pairs: |ask_NO - (1 - bid_YES)| = 0 to 6 decimals, minimum ask_YES + ask_NO = 1.001, 0 crossed books). So ask_YES + ask_NO = 1 + spread,
  always >= $1. The matching engine itself crosses a YES buyer with a NO buyer by minting. What the IMDEA paper
  counted as "single-condition arbitrage" is fills at different moments, not an executable book state.
- **NegRisk arbitrage is alive but tiny, and it is not decaying.** On-chain, wallets that bought every outcome
  (or a set of NOs) of one NegRisk event inside a short window locked in about **$130-180k per quarter in 2026**
  as takers. Counting passive (maker-filled) baskets it is **$650-770k per quarter**. Per $1M of NegRisk taker
  volume, that is **$30-85 (taker)**, flat since 2024. About 250-350 wallets a month profit as takers; the
  top 10 take 65-75% (2026).
- **For a $1k-$10k account, capacity is depth-limited, not capital-limited.** Every opportunity is worth cents to
  tens of dollars, so the result barely changes from $1k to $10k.
  - Live, across all ~9,350 open NegRisk groups, the *standing* stock of net-of-fee arbs was 3-7 groups per sweep (anthropic/openai IPO ladders, NK missile tests, House-incumbent primaries, Fed end-2026 rate), worth **$0.01-$2.47 in total (median $0.36)** of depth-walked profit.
  - The *instant* (sell/convert) side, extrapolated from 7 days of tick books on the ~400 liquid groups, is worth
    about **$130-145/week at 50 ms, $85-110/week at 250 ms, and < $0 at >= 1 s**. That assumes we beat every
    existing bot at that latency, which the on-chain competition says we would not.
  - The *buy-all-YES* side's positive value sits almost entirely in long-dated events (median 86 days to
    resolution). Those return about **2-3%/yr on locked capital, below T-bills**. Short-dated buy-side flow nets
    about $0 or less after latency.
- **Verdict: `rejected` for a $1k-$10k account** (the ledger entry is at the end). It does not clear $1k/yr at any
  latency a home setup can reach. Fees (2026: taker rate x p(1-p) per leg, makers 0) are the main reason. Gross
  sum-of-asks < 1 or sum-of-bids > 1 is open 2.7% and 6.0% of the time on liquid groups. Net of fees it is open
  0.27% and 0.02% of the time, and mostly on books too thin for 5 shares.

Data used: **0.71 GB** under `data/vendor/negrisk/` (Probalytics 7-day books 0.45 GB, live REST sweeps 0.13 GB, Tardis quotes, Predexon
snapshots, live captures). Dune: about 100 credits.

## 1. Mechanics (docs.polymarket.com, verified 2026-10-12)

### Fees (2026)
`fee = C x feeRate x p x (1 - p)` per *taker* fill, in USDC. C is shares and p is the fill price. Makers pay 0, and
geopolitics markets are fee-free. Each market's gamma record carries `feesEnabled` and
`feeSchedule {rate, exponent (=1 everywhere), takerOnly, rebateRate}`.

| category | rate | fee per share at p = 0.5 |
|---|---|---|
| Crypto | 0.07 | 1.75c |
| Sports / Economics / Culture / Weather / Other | 0.05 | 1.25c |
| Finance / Politics / Mentions / Tech | 0.04 | 1.00c |
| Geopolitics | 0 | 0 |

Rates seen in the live universe (127k NegRisk markets): 0.05 on 92k markets, 0.04 on 30k, 0.03 on 4.3k, 0.07 on
0.4k and 0 on 0.5k.

**Every leg pays its own fee.** For an n-leg basket the total fee is `sum_i rate x p_i(1 - p_i)`. That is roughly
`rate x (1 - sum p_i^2)` for the buy-all-YES basket: about 4c on a 5-way race at rate 0.05. That is far larger than
the typical 0.3-1c overround. The fee is symmetric in p, so buying NO at 1-b costs the same as selling YES at b.
Split, merge and convert pay no protocol fee.

**Gas.** Position operations (split, merge, convert, redeem) go through the Relayer for Polymarket proxy and
deposit wallets, which is gasless for the user. A self-custodied EOA pays Polygon gas of about $0.001-0.02 per
operation. That is negligible except against the many sub-$0.10 arbs seen here.

There is also a taker-rebate programme (tiered, by volume) and a maker-rebate programme funded by fees. We
ignored both, which is conservative for takers. A $1k-$10k account would sit in the lowest tier.

### The three arbitrage types
1. **Binary complement.** Buy YES and NO for less than $1 and merge them into $1, or mint $1 into YES+NO and sell
   both for more than $1.
   - There is one book per binary market and the API's NO book is its mirror.
   - In 912 random YES/NO book pairs, |ask_NO - (1 - bid_YES)| was 0 to 6 decimals and the minimum
     ask_YES + ask_NO was 1.001. No book was crossed.
   - **Not an executable opportunity.** Fills can still print at prices summing below 1 at different moments,
     which is what trade-based studies count.
2. **NegRisk multi-outcome** (exactly one of n outcomes resolves YES; the CTF NegRiskAdapter, or the V2
   Router/NegRiskModule):
   - **Buy side:** `sum_i ask_YES_i + fees < 1`. Buy one YES of every outcome; one of them pays $1 at resolution.
     - **There is no instant exit.** A cross-market YES set cannot be merged, so capital is locked until
       resolution.
     - It is valid only if *every* unresolved outcome is tradable. Augmented events (with unnamed placeholder
       outcomes and an "Other" whose meaning changes) fail this; 26% of live groups are augmented.
   - **Sell side:** `sum_{i in S} bid_YES_i - fees > 1` for any subset S with |S| = k >= 2.
     - **Execution.** Buy NO_i for every i in S at ask_NO_i = 1 - bid_YES_i, which is the same book as hitting the
       YES bids. Then **convert** the k NOs, atomically, into **k - 1 USDC plus 1 YES of every outcome outside S**,
       which is worth >= 0.
     - **Profit** = `sum_S bid_i - 1 - fees`.
     - **Capital** = `sum_S (1 - bid_i)`, about k - 1 per set. It comes back at conversion, so it recycles within
       seconds.
     - **Equivalent route.** Split $1 into YES+NO in each of the k markets, sell the k YES, and convert the k NOs.
     - **Augmented events.** The sell side works for them too, because placeholders just become free YES.
     - The docs state the convert rule as "a No share in any market can be converted into 1 Yes share in every
       other market". With k NOs that nets k - 1 collateral, since k - 1 of the k converted NOs' YES legs pair up
       into complete sets.
3. **Combinatorial / cross-market logical** (literature only).
   - IMDEA found 13 dependent pairs, all on the 2024 US election, with about $95k extracted. Pair 2 took $60k and
     pair 4 $18k.
   - A 2026 NBA study (arXiv 2605.00864, abstract only) found 290 combinatorial episodes with a median 101 bps
     return. 77% were capped at about 15 shares, i.e. retail size.
   - Polymarket V2 now offers *combinatorial positions*: conjunction YES/NO tokens quoted by RFQ, with a
     `CombinatorialModule.convertToYesBasket` on chain. Mispricing between a combo quote and its legs is a new
     surface, but RFQ makers quote it. Not tested here.

**Venue frictions.**
- Minimum order size is 5 shares, applied in every depth walk.
- Ticks are 0.01 or 0.001, depending on the market.
- In-play sports carry a marketable-order delay (gamma `secondsDelay = 1`).
- There is no atomic multi-leg order. The legs are separate taker orders, hence leg risk.

## 2. Literature: IMDEA "Unravelling the Probabilistic Forest" (arXiv 2508.03474, AFT 2025)

**Method.**
- On-chain fills (OrderFilled, PositionSplit, PositionsMerge) for markets resolved from 1 Apr 2024 to 1 Apr 2025.
- Opportunities: per-block VWAP with prices carried forward for up to about 2.5 h, and a 2c (or 5c) threshold.
- Realized profit: per wallet, from its own fills grouped within 950 blocks (about 1 h). Bids under $2 are
  dropped, and an ε = $1 heuristic is applied.
- Fees: zero, since Polymarket had no fees at the time.

**Realized extraction: $39.59M in total.**

| type | realized |
|---|---|
| Single-condition: buy < $1 | $5.90M |
| Single-condition: sell > $1 | $4.68M |
| NegRisk: buy YES | $11.09M |
| NegRisk: buy NO | **$17.31M** |
| NegRisk: sell YES | $0.61M |
| NegRisk: sell NO | $0.004M |
| Combinatorial | $0.095M |

**Concentration.** The top wallet took $2.01M over 4,049 transactions; the top 10 took about $8.2M (21%).

**Timing.**
- No monthly table is published, only charts. NegRisk theoretical profit is dominated by sports in every month
  except the election period.
- Realized NegRisk profit is dominated by politics: Nov 2024, plus Aug 2024 after Biden withdrew.
- About 1% of the estimated election opportunities were taken.
- The median single-condition deviation is about 60c. This is clearly a VWAP artifact.

**Why these are upper bounds.** VWAP prices ignore depth and simultaneity. The 1-h grouping turns directional NO
buying into "arbitrage". There were no fees. Our stricter on-chain replication (§4) finds about 1.4% of their NegRisk
figure over the same months ($0.40M all roles, $0.14M taker).

**Follow-up (arXiv 2605.00864, Apr 2026, NBA, 75M book snapshots).** Only 7 executable single-market episodes, with
a median life of 3.6 s.

## 3. Live scan, 2026-10-13 02:35-04:25 UTC (public REST and WebSocket)

**Universe.** `scripts/research/negrisk_universe.py`:
- 22,248 open events, of which 9,363 are NegRisk.
- 127,237 NegRisk markets, of which 96,150 are accepting orders.
- 157,671 open binary markets.

Groups are keyed by `negRiskMarketID`.

**REST sweeps.** `negrisk_live_scan.py`:
- Every 60 s: about 96k YES books via `POST /books`, 500 tokens per call and 6 in flight, about 15 s per sweep.
- Every 10th sweep also checks the binary mirror.

**WebSocket watch.** `negrisk_ws_watch.py`:
- Every YES token of every group: 108,933 tokens over 55 connections of 2,000 tokens each.
- Books are seeded and refreshed from REST every 10 min.
- About 7k messages/s.
- Edges are recomputed on every change, and every state change near or above zero is logged with its receive
  timestamp.

**REST sweeps:** 110 sweeps over 1.82 h, 9,352 NegRisk groups per sweep (complete = every unresolved outcome tradable: 70.6%).

|      |   buy_net>0 groups |   sell_net>0 groups |   buy gross (sum_ask<1) groups |   sell gross (sum_bid>1) groups |   buy $ (depth, no cap) |   sell $ (depth, no cap) |   buy $ @1k |   buy $ @10k |
|:-----|-------------------:|--------------------:|-------------------------------:|--------------------------------:|------------------------:|-------------------------:|------------:|-------------:|
| mean |               4.38 |                0.93 |                          16.05 |                           40.05 |                    0.69 |                        0 |        0.69 |         0.69 |
| 50%  |               4.5  |                1    |                          16    |                           40    |                    0.36 |                        0 |        0.36 |         0.36 |
| min  |               3    |                0    |                          12    |                           35    |                    0.01 |                        0 |        0.01 |         0.01 |
| max  |               7    |                1    |                          21    |                           46    |                    2.47 |                        0 |        2.47 |         2.47 |

Groups with a net-of-fee arb in any sweep (median over the sweeps where present):

| event_slug                                                                       |   sweeps |   n | buy_edge   | buy_profit   |   sell_edge |   sell_profit | end                  |   fee |
|:---------------------------------------------------------------------------------|---------:|----:|:-----------|:-------------|------------:|--------------:|:---------------------|------:|
| anthropic-ipo-closing-market-cap-979                                             |      110 |   7 | 0.0160     | 0.0000       |     -0.1121 |             0 | 2028-01-01T04:59:00Z |  0.04 |
| bitcoin-vs-gold-vs-sp-500-in-2026                                                |      110 |   3 | 0.0015     | 0.0074       |     -0.1079 |             0 | 2027-01-01T04:59:00Z |  0.04 |
| openai-ipo-closing-market-cap                                                    |      110 |   7 | 0.0007     | 0.0034       |     -0.0368 |             0 | 2027-01-01T04:59:00Z |  0.04 |
| nhl-player-to-record-the-most-hat-tricks-2026-27                                 |      102 |  87 |            |              |      0.0272 |             0 | 2027-04-30T03:59:00Z |  0.05 |
| how-many-democratic-house-incumbents-will-not-win-their-primary                  |       61 |   2 | 0.0042     | 0.2139       |     -0.0696 |             0 | nan                  |  0.04 |
| number-of-north-korea-missile-tests-in-october-2026                              |       34 |   3 | 0.0100     | 1.0000       |     -0.08   |             0 | 2026-11-01T03:59:00Z |  0    |
| how-many-republican-house-incumbents-will-not-win-their-primary                  |       34 |   2 | 0.0018     | 0.0865       |     -0.0737 |             0 | nan                  |  0.04 |
| what-will-the-fed-rate-be-at-the-end-of-2026                                     |       22 |  15 | 0.0367     | 0.7076       |     -0.2301 |             0 | 2027-01-01T04:59:00Z |  0.05 |
| how-many-triple-crowns-will-anna-leigh-waters-win-in-the-2026-27-ppa-tour-season |        1 |   6 | 0.0540     | 1.0791       |     -0.432  |             0 | 2027-05-16T23:59:00Z |  0.05 |

Complete groups: median (sum of best asks - 1) = 1.5400; share with < 1: 0.23%; median (sum of best bids - 1) = -0.3900.

Binary mirror check: 912 YES/NO book pairs; max |ask_NO - (1 - bid_YES)| = 0.000000; min ask_YES + ask_NO = 1.0010; crossed books 0.

**WebSocket watch (1.83 h):**

- standing (open at start), buy: 5 episodes / 5 opportunities in 5 groups; duration s p10/p50/p90 = 1166.52/6587.13/6587.13; profit at first sight (depth-walked) $2.48; groups: anthropic-ipo-closing-market-cap-979, bitcoin-vs-gold-vs-sp-500-in-2026, how-many-democratic-house-incumbents-will-not-win-their-primary, number-of-north-korea-missile-tests-in-october-2026, openai-ipo-closing-market-cap
- standing (open at start), sell: 1 episodes / 1 opportunities in 1 groups; duration s p10/p50/p90 = 934.47/934.47/934.47; profit at first sight (depth-walked) $0.00; groups: nhl-player-to-record-the-most-hat-tricks-2026-27
- flow (opened during capture), buy: 101 episodes / 23 opportunities in 13 groups; duration s p10/p50/p90 = 2.92/6.78/81.59; profit at first sight (depth-walked) $5.79; groups: highest-temperature-in-hong-kong-on-october-13-2026, highest-temperature-in-seoul-on-october-13-2026, highest-temperature-in-shanghai-on-october-13-2026, highest-temperature-in-singapore-on-october-13-2026, how-many-democratic-house-incumbents-will-not-win-their-primary, how-many-dissent-at-the-december-fed-meeting-20260730000304473, how-many-republican-house-incumbents-will-not-win-their-primary, how-many-ships-transit-bab-el-mandeb-strait-week-of-october-12-20261006t134953677z
- flow (opened during capture), sell: 13 episodes / 10 opportunities in 7 groups; duration s p10/p50/p90 = 0.00/0.03/805.52; profit at first sight (depth-walked) $1.20; groups: 1-paid-app-in-the-us-apple-app-store-on-october-16, cma-entertainer-of-the-year-2026, highest-temperature-in-qingdao-on-october-13-2026, highest-temperature-in-shanghai-on-october-13-2026, highest-temperature-in-taipei-on-october-13-2026, nhl-player-to-record-the-most-hat-tricks-2026-27, pro-football-nfc-west-champion

Latency race on flow episodes, WS feed ($/wk scaled from the capture; fail = -1c/set):

| side   | L      |   opportunities |   captured |   $/wk @1k |   $/wk @2.5k |   $/wk @5k |   $/wk @10k |
|:-------|:-------|----------------:|-----------:|-----------:|-------------:|-----------:|------------:|
| buy    | 0ms    |              23 |         23 |     573.14 |       573.14 |     573.14 |      573.14 |
| buy    | 50ms   |              23 |         18 |     477.2  |       477.2  |     477.2  |      477.2  |
| buy    | 250ms  |              23 |         18 |     477.2  |       477.2  |     477.2  |      477.2  |
| buy    | 1000ms |              23 |         17 |     220.46 |       220.46 |     220.46 |      220.46 |
| buy    | 5000ms |              23 |         16 |     143.51 |       143.51 |     143.51 |      143.51 |
| sell   | 0ms    |              10 |         10 |     110.61 |       110.61 |     110.61 |      110.61 |
| sell   | 50ms   |              10 |          4 |     -43.38 |       -43.38 |     -43.38 |      -43.38 |
| sell   | 250ms  |              10 |          4 |     -43.38 |       -43.38 |     -43.38 |      -43.38 |
| sell   | 1000ms |              10 |          3 |     -61.19 |       -61.19 |     -61.19 |      -61.19 |
| sell   | 5000ms |              10 |          2 |     -61.19 |       -61.19 |     -61.19 |      -61.19 |

Flow episodes split by time to resolution (WS, $/wk at a $1k cap, fail = 1c/set; scaled x92 from 1.83 night-time hours, so very noisy):

| flow | opportunities | 0 ms | 50 ms | 250 ms | 1 s | 5 s |
|---|---|---|---|---|---|---|
| buy, resolves <= 2 d (temperature) | 7 | +254 | +204 | +204 | -52 | -52 |
| buy, > 2 d (re-opening standing stock, locked weeks-months) | 16 | +319 | +273 | +273 | +273 | +196 |
| sell / convert, <= 2 d | 6 | +72 | -72 | -72 | -72 | -72 |
| sell / convert, > 2 d | 4 | +39 | +28 | +28 | +10 | +10 |

In absolute terms the whole 1.83 h capture held **$5.79 (buy) and $1.20 (sell) of depth-walked flow profit at first sight across all 9,352 groups**, plus a standing stock of $2.48.

**Reading.**
- **The standing stock.** At any moment a handful of groups show a net-of-fee buy-all-YES arb:
  - long-dated, thin events such as the Anthropic and OpenAI IPO market-cap ladders, North Korea missile tests in
    October, and House incumbents losing primaries;
  - their total depth-walked value is **a few dollars**;
  - they lock capital for weeks to over a year.
- **The sell side.** A sell-side arb almost never survives fees on any book with more than 5 shares at the top.
- **Typical prices.** The median complete group's sum of best asks is far above 1, because long tails sit at
  0.001-0.01 ticks with wide spreads. Only 0.26% of complete groups have a sum below 1 even before fees.

## 4. History

### 4a. Probalytics tick books, 2026-10-06 to 10-13 (7 days, every book change)

**Sample.** `negrisk_probalytics.py` covers 62 groups with 2-12 legs, all legs open: the 40 top by 24h volume plus
22-29 random ones with 24h volume >= $1k. That is 576 legs, 25.2M book changes and 0.45 GB of top-3 levels.
Population weighting covers the 396 comparable groups.

62 NegRisk groups (33 top-volume, 29 random), 6,632 group-hours, 25,151,112 book changes. Excluded 1 group(s) whose legs resolved inside the window; 463 episodes form 245 opportunities (gaps < 60 s = same resting liquidity).

Share of time an arb is open (mean over groups): gross buy 2.67%, net buy 0.273%; gross sell 5.96%, net sell 0.018%.

- buy: 230 episodes in 21 groups; duration ms p10/p25/p50/p75/p90 = 28/163/2,548/10,343/220,363; median edge 0.0036; depth-walked profit sum $264.53 (top group who-wins-the-senate-pending-hormuz-agreement $163.36)
- sell: 233 episodes in 32 groups; duration ms p10/p25/p50/p75/p90 = 27/63/327/2,107/8,632; median edge 0.0037; depth-walked profit sum $180.03 (top group bra-bra-cru-2026-10-12 $123.34)

Latency race on the 7-day sample (sample totals per week, no scaling):

| side   | L      |   opportunities |   captured |   $/wk @1k |   $/wk @2.5k |   $/wk @5k |   $/wk @10k |
|:-------|:-------|----------------:|-----------:|-----------:|-------------:|-----------:|------------:|
| buy    | 0ms    |             126 |        109 |     158.44 |       164.07 |     170.77 |      184.17 |
| buy    | 50ms   |             126 |         97 |     125.83 |       131.45 |     138.15 |      151.55 |
| buy    | 250ms  |             126 |         85 |     112.8  |       118.42 |     125.12 |      138.52 |
| buy    | 1000ms |             126 |         79 |     106.53 |       112.16 |     118.86 |      132.26 |
| buy    | 5000ms |             126 |         67 |      37.38 |        43.01 |      49.71 |       63.11 |
| sell   | 0ms    |             119 |        104 |      53.03 |        47.47 |      39.39 |       39.42 |
| sell   | 50ms   |             119 |         77 |      33.96 |        28.31 |      20.23 |       20.26 |
| sell   | 250ms  |             119 |         60 |      25.92 |        16.01 |       4.57 |        4.42 |
| sell   | 1000ms |             119 |         37 |     -14.2  |       -25.04 |     -36.47 |      -36.62 |
| sell   | 5000ms |             119 |         17 |     -38.13 |       -48.97 |     -60.4  |      -60.55 |

Same, failed attempts free (upper bound):

| side   | L      |   opportunities |   captured |   $/wk @1k |   $/wk @2.5k |   $/wk @5k |   $/wk @10k |
|:-------|:-------|----------------:|-----------:|-----------:|-------------:|-----------:|------------:|
| buy    | 0ms    |             126 |        109 |     164.9  |       170.53 |     177.23 |      190.63 |
| buy    | 50ms   |             126 |         97 |     135.01 |       140.64 |     147.34 |      160.74 |
| buy    | 250ms  |             126 |         85 |     129.73 |       135.35 |     142.05 |      155.45 |
| buy    | 1000ms |             126 |         79 |     125.56 |       131.19 |     137.89 |      151.29 |
| buy    | 5000ms |             126 |         67 |      59    |        64.63 |      71.33 |       84.73 |
| sell   | 0ms    |             119 |        104 |      67.56 |        69.5  |      70.07 |       70.1  |
| sell   | 50ms   |             119 |         77 |      55.17 |        57.02 |      57.6  |       57.63 |
| sell   | 250ms  |             119 |         60 |      52.15 |        52.61 |      52.61 |       52.61 |
| sell   | 1000ms |             119 |         37 |      20.9  |        20.9  |      20.9  |       20.9  |
| sell   | 5000ms |             119 |         17 |       5.85 |         5.85 |       5.85 |        5.85 |

Population of comparable groups (2-12 legs, none resolved, 24h vol >= $1k): 396; random-stratum weight 12.3.

Same, extrapolated to the population of comparable groups (random stratum weighted):

| side   | L      |   opportunities |   captured |   $/wk @1k |   $/wk @2.5k |   $/wk @5k |   $/wk @10k |
|:-------|:-------|----------------:|-----------:|-----------:|-------------:|-----------:|------------:|
| buy    | 0ms    |             126 |        109 |     486.32 |       491.95 |     498.65 |      512.05 |
| buy    | 50ms   |             126 |         97 |     451.48 |       457.11 |     463.81 |      477.21 |
| buy    | 250ms  |             126 |         85 |     403.94 |       409.57 |     416.27 |      429.67 |
| buy    | 1000ms |             126 |         79 |     357.85 |       363.48 |     370.18 |      383.58 |
| buy    | 5000ms |             126 |         67 |     267.43 |       273.06 |     279.76 |      293.16 |
| sell   | 0ms    |             119 |        104 |     197.97 |       192.41 |     184.33 |      184.36 |
| sell   | 50ms   |             119 |         77 |     143.55 |       137.9  |     129.82 |      129.85 |
| sell   | 250ms  |             119 |         60 |     106.53 |        96.61 |      85.18 |       85.03 |
| sell   | 1000ms |             119 |         37 |     -17.05 |       -27.88 |     -39.32 |      -39.47 |
| sell   | 5000ms |             119 |         17 |     -54.09 |       -64.92 |     -76.36 |      -76.51 |

**By time to resolution** (sample, $1k cap, fail = 1c/set; population-weighted in brackets):

| side | resolves in | opportunities | 0 ms | 50 ms | 250 ms | 1 s | 5 s | median days locked |
|---|---|---|---|---|---|---|---|---|
| buy all YES | <= 2 days | 43 | +$17 (-2) | -$6 (-25) | -$8 (-28) | -$9 (-28) | -$12 (-31) | 0 |
| buy all YES | > 2 days | 83 | +$141 (489) | +$131 (477) | +$121 (432) | +$115 (386) | +$49 (299) | **86** |
| sell / convert | <= 2 days | 58 | +$26 (33) | +$14 (11) | +$13 (-3) | -$16 (-37) | -$20 (-41) | 0 (instant) |
| sell / convert | > 2 days | 61 | +$27 (160) | +$20 (128) | +$13 (99) | +$2 (9) | -$18 (-25) | 0 (instant) |

**Buy side.** All of the buy side's positive value is long-dated, standing mispricing:
- The largest item is "who wins the Senate, pending a Hormuz agreement": $163 on $18.6k over 23 days, about 14%/yr.
- Next is West Nile cases in NYC: $29 on $2.1k over 173 days, about 3%/yr. Its random-stratum weight multiplies
  it to about $360/wk in the extrapolation, which is why the population buy number is not credible as weekly flow.
- In total: $141 per week on $23k of capital locked for a median 86 days. That is **about 2.6%/yr on capital**,
  before resolution and dispute risk.

**Sell side.** It is positive only at <= 250 ms. In-play soccer contributes much of it (bra-bra-cru: $123), and
those episodes are unreachable through the 1 s sports delay.

### 4b. Tardis free days (1 Jun, Jul, Aug, Sep and Oct 2026; quotes stream, us timestamps)

`negrisk_tardis.py` took the top NegRisk groups by that day's traded USD with 2-12 legs: 45 groups and 172 legs.
Mostly they are soccer 3-way games and Fed decisions.

|                        |   episodes | med_ms   | p90_ms    |   med_edge |   profit_1k |   sport_share |
|:-----------------------|-----------:|:---------|:----------|-----------:|------------:|--------------:|
| ('2026-06-01', 'buy')  |        104 | 134.50   | 3,685.20  |       0    |       21.08 |          0.66 |
| ('2026-06-01', 'sell') |        154 | 490.50   | 10,819.40 |       0    |       64.71 |          0.56 |
| ('2026-07-01', 'buy')  |        391 | 131.00   | 1,631.00  |       0    |      409.89 |          1    |
| ('2026-07-01', 'sell') |        347 | 71.00    | 948.40    |       0    |      421.26 |          1    |
| ('2026-08-01', 'buy')  |         76 | 528.50   | 5,078.50  |       0    |       22.54 |          0.88 |
| ('2026-08-01', 'sell') |         76 | 546.50   | 4,210.00  |       0.01 |       40.1  |          0.71 |
| ('2026-09-01', 'buy')  |         39 | 277.00   | 1,931.60  |       0.01 |       10.25 |          0.82 |
| ('2026-09-01', 'sell') |         66 | 1,001.00 | 8,817.50  |       0    |       64.01 |          0.7  |
| ('2026-10-01', 'buy')  |         73 | 404.00   | 1,687.20  |       0.01 |       23.05 |          0    |
| ('2026-10-01', 'sell') |        145 | 77.00    | 1,286.20  |       0    |       56.55 |          0    |

Latency race on top-of-book size capped at 1,000 sets (5 days, sports +1 s venue delay):

| L      |   captured |   of |   profit_fail1c |   per_day_fail1c |   per_day_fail0 |
|:-------|-----------:|-----:|----------------:|-----------------:|----------------:|
| 0ms    |        212 |  302 |          108.94 |            21.79 |           44.38 |
| 50ms   |        191 |  302 |           70.75 |            14.15 |           38.74 |
| 250ms  |        165 |  302 |           28.21 |             5.64 |           35.84 |
| 1000ms |        131 |  302 |          -41.64 |            -8.33 |           25.82 |
| 5000ms |         46 |  302 |         -264.89 |           -52.98 |            4.79 |

**Reading.**
- The 1 Jul spike is the World Cup group stage, all in-play soccer, and therefore behind the 1 s delay.
- At a $1k cap and 1c per failed attempt, the five days net about **+$22/day at 0 ms, +$14 at 50 ms, +$6 at
  250 ms, and negative at >= 1 s**.
- There is no month-on-month decay across June to October. The level is just very small.

### 4c. Predexon book snapshots, Jan-Oct 2026 (monthly sample)

`negrisk_predexon_history.py` samples 12 NegRisk groups per month that ended in that month: the top 6 by volume
plus 6 random, at end-7d and end-2d.
- The data is full-depth snapshots, but they are sparse and irregular: a median gap of minutes to hours per token,
  denser for big events. So this measures *standing* mispricing.
- The leg merge is as-of, and a point is kept only if every leg is <= 5 min old.
- Request rate was <= 0.3 req/s (shared key).

| month   |   groups |   points |   median sum_ask-1 |   p10 sum_ask-1 |   gross buy<0 share |   net buy>0 share |   net sell>0 share |   groups w/ net arb |   median |dev| |
|:--------|---------:|---------:|-------------------:|----------------:|--------------------:|------------------:|-------------------:|--------------------:|---------------:|
| 2026-01 |       11 |    10377 |              0.003 |           0.001 |              0.0052 |            0.0052 |             0.0109 |                   2 |          0.003 |
| 2026-02 |       12 |     7069 |              0.04  |          -0.006 |              0.3576 |            0.3576 |             0.0755 |                   4 |          0.04  |
| 2026-03 |       12 |     8558 |              0.003 |           0.001 |              0.0027 |            0.0026 |             0.0117 |                   5 |          0.003 |
| 2026-04 |       12 |     2593 |              0.02  |           0.002 |              0.0004 |            0      |             0.0077 |                   3 |          0.02  |
| 2026-05 |       12 |     1170 |              0.03  |           0.01  |              0      |            0      |             0      |                   0 |          0.03  |
| 2026-06 |       12 |     6114 |              0.01  |           0     |              0      |            0      |             0.0028 |                   1 |          0.01  |
| 2026-07 |       12 |    32109 |              0.019 |           0.01  |              0.0035 |            0.0001 |             0.0055 |                   1 |          0.019 |
| 2026-08 |       11 |      178 |              0.02  |           0.01  |              0      |            0      |             0      |                   0 |          0.02  |
| 2026-09 |       12 |     1943 |              0.013 |           0.011 |              0.0077 |            0      |             0      |                   0 |          0.013 |
| 2026-10 |       12 |      167 |              0.02  |           0.007 |              0      |            0      |             0      |                   0 |          0.02  |

**Reading.**
- The median overround of sampled liquid groups is 0.3-4c in every month.
- Net-of-fee arbs appear in 0-1% of snapshots, apart from one long-dated 2025-tariff-revenue group in Feb, which
  was a standing 0.2c buy arb worth about $0.09.
- There is no visible trend within 2026. The sample is small (12 groups/month) and heterogeneous, so it can show
  level, not decay.

### 4d. On-chain: who captures it, 2024-01 to 2026-10 (Dune, aggregated in SQL)

`negrisk_dune.py`:
- **Rows.** `polymarket_polygon.market_trades` has one row per (wallet, fill): `maker` is the wallet and
  `maker_side` its side. On `is_taker_side` rows the wallet is the taker.
- **YES basket.** One wallet BUYS YES in *every* traded outcome (n = distinct conditions ever traded in the
  event) inside one 30-block (about 60 s) or 300-block (about 10 min) bucket.
  - Profit = min shares x (1 - sum of leg VWAPs - fee per share).
- **NO basket.** One wallet buys NO in k >= 2 outcomes in one bucket.
  - Profit = min shares x ((k - 1) - sum VWAP - fees). Holding or converting k NOs pays at least k - 1.
- **What we report.** Positive-PnL baskets only; negative ones are mostly directional. "Taker" = every leg taken;
  "all" includes passively filled legs.

Realized positive-PnL baskets per quarter, with NegRisk taker volume (`volume.csv`). 2026Q4 covers 12 days.

| quarter | NegRisk taker vol $M | YES baskets 10 min, taker | YES 10 min, all | NO baskets 60 s, taker | NO 60 s, all | taker total per $1M vol | all per $1M vol | top-10 share (YES 10 min taker) |
|---|---|---|---|---|---|---|---|---|
| 2024Q1 | 38 | $426 | $451 | $893 | $2,358 | $35 | $74 | 100% |
| 2024Q2 | 72 | $417 | $515 | $875 | $14,113 | $18 | $204 | 100% |
| 2024Q3 | 570 | $1,412 | $1,557 | $17,332 | $51,741 | $33 | $94 | 99% |
| 2024Q4 | 3,284 | $15,442 | $25,361 | $79,770 | $223,415 | $29 | $76 | 93% |
| 2025Q1 | 921 | $5,625 | $8,152 | $15,827 | $70,905 | $23 | $86 | 98% |
| 2025Q2 | 813 | $5,139 | $7,834 | $48,169 | $238,908 | $66 | $304 | 96% |
| 2025Q3 | 666 | $6,285 | $16,025 | $35,059 | $147,886 | $62 | $246 | 97% |
| 2025Q4 | 2,107 | $25,419 | $83,289 | $402,471 | $646,790 | $203 | $347 | 86% |
| 2026Q1 | 3,670 | $54,321 | $213,168 | $73,308 | $449,234 | $35 | $181 | 71% |
| 2026Q2 | 3,749 | $97,636 | $427,697 | $75,629 | $339,540 | $46 | $205 | 73% |
| 2026Q3 | 2,123 | $109,472 | $379,604 | $71,460 | $270,768 | $85 | $306 | 74% |
| 2026Q4 (12 d) | 177 | $9,521 | $30,938 | $4,485 | $23,430 | $79 | $307 | 74% |

YES baskets with a 60 s window are 23-77% of the 10 min figures. That is the sensitivity to legs landing in different buckets. In 2026 the profitable wallets per month were about 300-350 (YES baskets, taker, 10 min) and about 250 (NO baskets, taker).

**Conversions** went from 1.4k per month (Jan 2024; $3.5M of NOs; 55 wallets; top-10 share 82%) to 84.6k (Nov 2024; $122M) and to 265-448k per month in 2026 ($60-134M). Through Apr 2026 there were 1,250-5,260 distinct converters a month with a top-10 share of about 50%. From May 2026 the collateral adapter is the on-chain stakeholder, so wallets are hidden. Full monthly tables are in `data/vendor/negrisk/report.md`.

**Reading.**
- **No decay.** Realized intra-NegRisk arbitrage has grown with volume. Per $1M of NegRisk taker volume it is
  about $30-85 (taker) and $75-350 (all), roughly flat 2024 to 2026.
  - The 2025Q4 spike is one wallet with $198k of NO baskets in Dec 2025.
- **Who captures it.**
  - Hundreds of wallets per month earn something: about 300-350 YES-basket and 250 NO-basket profitable taker
    wallets a month in 2026.
  - The top 10 take 71-74% (YES, taker) and about 65% (NO, taker) each month. That is down from 93-100% in
    2024, so there are more participants, not fewer.
  - In 2026 most YES-basket profit is filled *passively*: about 75% ($270-330k of $380-430k per quarter) is
    "all" minus "taker". So the dominant way to capture the buy side is resting bids on every leg, not taking. It is the same
    business as market making, which the ledger rejects on adverse selection, and its baskets only lock in when
    every leg fills.
- **Conversions.** These are the exit leg of the sell side. They grew from about 1.4k/month (early 2024) to
  300-450k/month in 2026.
  - From 2026-05 the stakeholder is the collateral adapter (`0xada2...eaab`), which hides wallets, so the
    concentration column is meaningless from then on.
- **Versus IMDEA.** Our strict replication gives about $0.40M (all roles; $0.14M taker) for Apr 2024-Mar 2025, against IMDEA's
  $28.4M of NegRisk for the same months. The gap is method: 1 h windows, ε = $1, and directional NO buys counted
  as arbitrage.

## 5. Execution realism

- **Latency.**
  - Opportunity lifetimes (Probalytics, merged into opportunities with < 60 s gaps):
    - sell episodes, p10/p25/p50/p75/p90 = 27/63/327/2,107/8,632 ms;
    - buy episodes: 28/163/2,548/10,343/220,363 ms, where the long tail is the standing long-dated stock.
  - Tardis medians are 70-1,000 ms.
  - From a US home connection to Polymarket's matching engine (reportedly AWS eu-west-2, London; not measured here), a realistic round trip is
    150-300 ms plus signing. Competitors co-located in London are at 1-5 ms.
- **The latency table assumes we win every race we are fast enough for.** On-chain about 250-350 profitable taker wallets
  a month do this, so that is optimistic.
- **Leg risk.** Legs are separate orders, so a basket can half-fill. We charge 1c per set on the intended size for
  any opportunity that has closed by t + L: one leg left unfilled and unwound across a spread.
  - With failures free, the 250 ms sell side rises from about $26 to $52 per week (sample), and the 1 s case
    from -$14 to +$21. Even this upper bound is under $3k/yr.
- **Impact.** Fills are depth-walked on the real ladders (top 3 levels for Probalytics, full depth for the live
  feed), with a 5-share minimum on every leg. `ImpactModel` is not needed because the books are real. Its
  calibrated walk cost says any unwind beyond the visible depth is worse, which is what the 1c failure charge
  stands in for.
- **Capital lockup.**
  - Sell/convert: instant. Capital is back at the convert transaction, seconds via the relayer.
  - Buy-all-YES: locked to resolution, with no merge across markets. A $1k account in long-dated buy baskets
    earns about 2.6%/yr, and the available depth at any moment is only a few hundred dollars anyway.
  - Polymarket's "holding rewards" on some long-dated markets were not modelled. They could add a few %/yr on
    eligible legs.
- **Resolution risk.** The buy side assumes exactly one outcome resolves YES. UMA disputes, augmented "Other"
  re-definitions and voided events break it. The sell side has no such risk once converted.

## 6. Capacity ($1k / $2.5k / $5k / $10k)

$/week, best case (we win every race at our latency; fail = 1c/set). Annualized = x 52. Sources: Probalytics
population-weighted (396 liquid groups) for type 2; live WS for the >12-leg and long-tail groups.

| type | latency | $1k | $2.5k | $5k | $10k | note |
|---|---|---|---|---|---|---|
| 1. Binary YES+NO | any | 0 | 0 | 0 | 0 | impossible on a mirrored book |
| 2a. NegRisk sell / convert (instant) | 50 ms | ~$144/wk (~$7.5k/yr) | ~$138 | ~$130 | ~$130 | London co-location; competes with ~300 active taker wallets/month |
| | 250 ms | ~$107/wk (~$5.5k/yr) | ~$97 | ~$85 | ~$85 | sample-only (unweighted) $26/wk |
| | 1 s | < 0 | < 0 | < 0 | < 0 | |
| | 5 s | < 0 | < 0 | < 0 | < 0 | |
| 2b. NegRisk buy all YES, resolves <= 2 d | 50 ms - 5 s | <= $0 | <= $0 | <= $0 | <= $0 | |
| 2c. NegRisk buy all YES, long-dated | any | ~2.6%/yr of deployed capital: ~$0.5/wk at $1k | ~$1.2 | ~$2.5 | ~$5 | depth: a few hundred $ at any time; locked a median 86 d |
| 3. Combinatorial | n/a | not measured | | | | 2026 NBA study: median ~15 shares per episode |

**How to read the 2a row.**
- **It is an upper bound.**
  - It extrapolates 29 random groups by x12.3.
  - It assumes we always win when fast enough.
  - Most of it comes from groups where the observed lifetime (the time until the incumbent took it) exceeds our
    latency.
  - The unweighted 7-day sample gives $20-34/wk at 50 ms and $5-26/wk at 250 ms.
- **The honest central estimate for a home setup (>= 250 ms) is about $0-$50/week.** At 1 s and above it is
  negative.
- Account size does not matter above $1k because the depth per opportunity is small.

## 7. Verdict

Intra-venue arbitrage on Polymarket intl is **alive in 2026 as a small, steady, crowded business**:
- tens to hundreds of thousands of dollars per quarter across the whole venue;
- split among hundreds of wallets, with the top 10 taking half;
- about $30-85 per $1M of NegRisk volume to takers, with no decay since 2024 (no growth either).

For a $1k-$10k account it is **not worth building**:
- the binary type cannot exist on the unified book;
- the instant NegRisk sell side pays only at <= 250 ms, about $0.1k/week at best, and that is best case
  against co-located incumbents;
- the buy side's surviving value is long-dated standing mispricing yielding below the risk-free rate after
  lockup.

2026 taker fees are what make this so. A typical liquid 3-5 leg group needs a 2-4c gross deviation just to break
even, and gross deviations that large live a median of under a second.

## 8. Reproduce

```
venv/Scripts/python.exe scripts/research/negrisk_universe.py
venv/Scripts/python.exe scripts/research/negrisk_live_scan.py --minutes 110 --interval 60
venv/Scripts/python.exe scripts/research/negrisk_ws_watch.py --minutes 110
venv/Scripts/python.exe scripts/research/negrisk_probalytics.py --fetch --analyse      # needs PROBALYTICS_CLICKHOUSE_*
venv/Scripts/python.exe -I scripts/research/negrisk_tardis.py --fetch --analyse
venv/Scripts/python.exe scripts/research/negrisk_predexon_history.py --months 2026-01..2026-10   # PREDEXON_API_KEY, 0.3 req/s
DUNE_API_KEY=... venv/Scripts/python.exe scripts/research/negrisk_dune.py conversions|baskets|nobaskets|volume [--bucket=30|300]
venv/Scripts/python.exe scripts/research/negrisk_report.py > data/vendor/negrisk/report.md
venv/Scripts/python.exe -m pytest tests/unit/test_negrisk_common.py
```

## 9. Ledger entry (proposed for `polymarket_yes_no_intramarket_arb`)

**Status `rejected`** for a $1k-$10k account at retail latency.

**Verdict.**
- Binary YES+NO is impossible on Polymarket's mirrored single book (912/912 pairs exact mirrors).
- NegRisk arbitrage is alive and not decaying: realized on-chain profit is about $30-85 per $1M of NegRisk taker
  volume, 2024-2026, or $130-180k/quarter to takers market-wide. It is crowded: about 300 profitable taker
  wallets a month, with the top 10 taking about 70%.
- Net of 2026 taker fees:
  - The instant sell/convert side pays only at <= 250 ms: an upper bound of about $85-145/wk population-weighted,
    $5-34/wk on the unweighted 7-day sample. It is negative at >= 1 s.
  - The buy-all-YES side's positive value is long-dated standing mispricing at about 2.6%/yr on locked capital.
  - The live standing stock across all 9,352 groups was worth $0.01-2.47.

**Key numbers.** `onchain_taker_per_1M_vol` = 30-85, `sell_wk_50ms_pop` = 144, `sell_wk_250ms_pop` = 107,
`sell_wk_1s` < 0, `buy_longdated_apr` = 0.026, `live_stock_usd_median` = 0.36, `net_open_frac_sell` = 0.0002,
`gross_open_frac_sell` = 0.06.

**Gaps.**
- 7-day tick sample of 62 groups with 2-12 legs; 1.8 h night-time live capture.
- Win-every-race assumption; no measured order-ack latency.
- Passive basket bidding (where about 75% of 2026 buy-side profit is made) is not simulated.
- Holding rewards and taker rebates not modelled.
- Combinatorial / RFQ combos untested.
- On-chain detection is bucketed (60 s / 10 min) and hides wallets behind the collateral adapter from May 2026.

**Next tests (only if co-location is on the table).**
- Measure London round-trip order-ack latency with tiny orders.
- Replay the sell/convert race on 30+ days of tick books.
- Simulate passive all-leg bidding with queue position and partial-fill inventory risk.

**Retest if.**
- Polymarket cuts taker fees on NegRisk legs (rate < 0.02), or adds an atomic multi-leg order.
- Or a measured end-to-end latency of <= 100 ms is available and a 30-day replay shows > $20k/yr after leg risk.
