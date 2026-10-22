# User-supplied brief #3 (2026-10-14): small-capital strategy families by category. LEADS ONLY.

Last revised: 2026-10-14

Condensed from a literature/market scan the user pasted. Figures are secondary and unverified unless re-measured;
labels: [D] documented, [P] plausible, [S] speculative (the brief author's own judgement).

## Documented biases cited
- Becker (72.1M Kalshi trades): takers -1.12%, makers +1.12% on average; 5c contracts win 4.18%; at 1c YES EV -41%, NO +23%.
  Maker NO vs maker YES differ by only 0.47 pp, so makers earn by supplying biased flow, not by forecasting.
- Maker-taker gap by category (pp): Finance 0.17, Politics 1.02, Sports 2.23, Weather 2.57, Crypto 2.69,
  Entertainment 4.79, Media 7.28, World Events 7.32. Fish in retail/narrative books.
- Deleep, Lee, Bai, Suresh, Dhawan (UC Berkeley, SSRN 6322678, 2026): mention markets show a pervasive "Yes Bias"
  once lifecycle timing is controlled; whales lose EV to small traders. Effect sizes not obtained (need the PDF).
- Kalshi calibration study (2,243,741 resolved markets): weaker calibration in Mentions.
- Unverified GitHub tape study (~3,800 Kalshi markets settled Sep-Oct 2026): maker profit when takers bought YES
  +3.3c (3-7c), +6.3c (8-15c), +8.3c (16-25c); positive in Politics, Mentions, Sci/Tech, Entertainment, Elections;
  negative in Weather, Crypto and above 25c.
- Counter-evidence: replication on 8,329 markets at open+24h with real quotes found no miscalibrated cell; blind rules
  lose spread + fee. Polymarket's apparent Yes bias sat on empty books (median spread 94c).
- Kim et al. (arXiv 2602.21229): 856 Kalshi earnings-mention markets, market Brier 0.1402; GPT-5.1 anchored to price
  only 0.1392. Level forecasts at 7 days are near-efficient.
- Moshrefi (arXiv 2607.14430, 23M moneyline trades): cross-game parlays overpriced vs product of legs, growing with
  leg count; calibration step-like in the last 10 minutes.
- Structural arb: scanners found 0 of 327 displayed violations surviving fees; 8 LP candidates, none after rounding.

## Liquidity context (Oct 2026, notional)
- Kalshi Sep 2026 $60.8B; week Sep 28-Oct 4 $18.37B, Exotics 64.34%; Mentions $9.80M/week; Economics $19.34M;
  Sci/Tech $6.93M. Mentions ~$32-35M/30 days (Dune/CNBC figure of $3.3M for July conflicts; plan from per-event books).
- Mid-cap earnings-mention events pre-call (Oct 14): United $30,421; Sprouts $12,605; Intel $8,980; Carvana $8,147;
  Intuitive Surgical $7,570; GM $6,325; Truist $4,725; AAL $4,532; Moody's $3,729; Spotify $3,650; AmEx $3,143;
  Wingstop $2,647; Ally $2,569; P&G $1,901; Starbucks $1,800; Lockheed $1,692; Mastercard $1,245; Alaska Air $1,049.
  13-17 words each. ~24 company events Oct 16-29 (10-15/week in season).
- Mega-cap: Amazon $49,059/22 words, Google $19,326/19, Tesla $14,957/25, Netflix $13,842/15 (tool-covered:
  Odds Assist tracker, MentionMetrix $199/mo).
- Culture (Kalshi Oct 14): RT ladders $237K-$996K each (Digger settled $3.1M); Netflix Top US Movie $27,913;
  #1 movie views $17,123; #2 US $3,294; #2 Global $615; Taylor Swift daily YouTube views $68,952 (31 strikes);
  Broadway attendance $296,279.
- Weather Oct 14: LA high $239K, Miami $107K, OKC $76K, Austin $57K, Chicago $52K, Seattle $15K; "Where will it rain
  today" $1.14M across 31+ cities; 143 weather series incl. international cities.
- Fees: Polymarket intl taker rates 0.04 mentions/politics/finance/tech, 0.05 culture/weather/economics, 0.07 crypto,
  0 geopolitics; maker 25% rebate in mentions/culture/weather. ForecastEx ~$0.01 per pair.

## Regulatory
- CFTC staff advisory Letter 26-27 (Sep 22, 2026): contracts on what a person says/attends/interacts with "may be
  presumed readily susceptible to manipulation" (not a ban). Kalshi removed sports mentions (Aug 2026); margin filing
  excludes sports, culture and mention markets. Enforcement: teleprompter operator $107,539.02 disgorgement + $65,000;
  Santos $17,569.98 + $17,500. WSJ (Sep 22, 2026) on ~1M near-identical ETH-perp trades (incentive scrutiny).

## Ranked ideas (brief's ranking)
1. **A1 Mid-cap earnings mention Yes-fade, maker, with a count model.** Negative-binomial word counts with partial
   pooling across company/sector/quarter, separate prepared-remarks vs Q&A rates, call length, sector news; convert to
   P(count>=1) under exact matching rules. Rest NO bids only where fair value is >= 4-5c below YES price; skip big
   disagreements. ~12 events/week x 5-7 words x $15-25. Target the $2K-15K pre-call band (Truist, Ally, Moody's,
   Wingstop, Sprouts, Carvana, Intuitive Surgical, Alaska Air). Claimed 2-5c per filled contract [P]. Kill rule:
   < +1.5c per filled contract over >= 300 fills. Cancel at call start.
   REPO NOTE: the earlier mention NO-maker +$17.6k was an artifact (print+1c fills); any retest must use causal fills.
2. **B1 Cross-family culture maker book**: RT beta-binomial on review count/critic mix; Netflix FlixPatrol -> weekly
   views; YouTube daily view curves. 8-12 ladders/week x $80-120 [P].
3. **C1 Sports prop-leg quoting + combo-leg LIP (CLIFF up to 25% of combo fees)**: many small two-sided quotes on
   low-attention legs priced from sportsbook consensus; 50-100 legs x $10-20 [P]. (lipaudit estimate: ~0.2-0.5c per
   prop contract; untestable without prop books in the recorder.)
4. **D1 LIP overlay** on A1/B1 books where the model is confident [P].
5. **A2 Count-threshold ladders** (Fed pressers, Trump): fit NB dispersion on FOMC transcripts since 2018; trade rungs
   whose implied survival probabilities break the fitted shape; "say X" vs "say X N+ times" [S].
6. A3 In-event hazard quoting on live speech-to-text (public webcast) [S, high engineering cost].
7. E1 Hawkes count model for Truth Social posts / executive orders (Kalshi) [S].
8. F1 Rain-city binaries in the last 3 hours with radar/METAR nowcasts, mainly for LIP [S].
9. Avoid: macro/finance takers, day-ahead weather taking, structural arb.

## Validation plan from the brief
- A1: all settled KXEARNINGSMENTION markets + trades with taker side; causal maker fills (fill only if a later taker
  YES print at or above p; queue last); walk-forward fits; P&L per contract by price band, company size, time to call;
  series maker fees; cluster SEs by event.
- A2: NB dispersion from FOMC transcripts 2018+; compare implied rungs to ladder snapshots; Brier vs market + maker P&L.
- B1: join settled RT/Netflix/YouTube ladders with timestamped review counts, FlixPatrol ranks, view counts; test
  books < $50K separately.
- C1: paper-quote props 4 weeks; CLIFF payout from per-market score breakdowns; 1m/10m markouts.
- D1: LIP payouts vs inventory P&L per market-day.
- Re-run monthly (maker-taker sign has flipped once as professionals arrived).
