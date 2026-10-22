# QUIVER: does the Quiver Quantitative "Trader" API add value for prediction-market research? (2026-10-14)

Last revised: 2026-10-14

Bounded screen, research only. No orders were placed. The raw Quiver data stays in `data/vendor/quiver/` (gitignored; Quiver's ToS forbids redistribution). The API key was read from `.env` and never printed.

Scripts:
- `scripts/research/quiver_probe.py`: inventory probe of every endpoint, at 1 req/s.
- `scripts/research/quiver_pm_whales_fetch.py`: full download of the Polymarket large-trade feed.
- `scripts/research/quiver_pm_whales_test.py`: calibration and encompassing test for H4.

## Headline

**Verdict: for prediction-market research, the Quiver "Trader" subscription adds nothing we do not already have or cannot get free.** No strategy was built. $/yr is $0 at $1k, $2.5k, $5k and $10k, and nothing is deployed.

| hypothesis | Quiver data needed | gate (≥ 12 months PIT, ≥ 30 events) | result |
|---|---|---|---|
| H1 attention → Kalshi mention / entertainment | Wikipedia, WSB, Twitter | **fail**: Wikipedia returns 404 (retired); WSB history stops 2025-02-21 and covers stock tickers only; Twitter history is empty | Untestable with Quiver. The free Wikimedia API is the only source, so Quiver adds nothing |
| H2 lobbying / contracts / congress trades / bills → Kalshi policy markets | legislation, lobbying, congress trading | **fail**: legislation is a 2025-03..2025-12 snapshot; ~1 Kalshi event per bill, under 30 mappable events; every input is free at source | Untestable, null |
| H3 alt-data → Kalshi KPI ladders | app ratings, flights, jobs, web traffic | **fail**: app ratings cover the last 7 days only; flights frozen 2025-07; jobs and web traffic return 404 | Untestable. Forward logging is the only route |
| **H4 Polymarket large trades beat their own price** | `polymarkettrades` (2025-10 → now, 152,680 rows) | pass | **Rejected.** Taking the whale's side *at the whale's own price, before fees*: train event-mean −2.2%/$ (t = −3.2), $-weighted −0.9% [CI −2.7%, +1.0%]; test −2.5% (t = −3.6), $-weighted +0.6% [−1.8%, +3.1%]. Same null for non-sports, trades ≥ $50k, BUY and SELL. H4b (tradeable) not run, per the stop rule |
| H5 "events beta" election odds | eventsbeta / politicalbeta | n/a | Not a prediction-market history: monthly 2020-election odds, stale since 2025-07 |

The only prediction-market item Quiver has is a feed of Polymarket trades of about $10k or more.
- It is published in hourly batches, with a **31-minute median lag** (55 min at p90) after the trade.
- Polymarket's own free `data-api` serves the same filter (`filterType=CASH&filterAmount=10000`) in real time.
- The repo already holds the full fill tape (Telonex, Dune).
- Its "sports excluded by default" filter is leaky: about 79% of rows are sports or esports matches.

The rest of the API repackages free government data:
- STOCK-Act trades, Senate LDA lobbying, FEC, USAspending, congress.gov, EDGAR, FINRA, USPTO.

Quiver's distinctive alt-data is retired, frozen or snapshot-only on this plan:
- Wikipedia, Twitter, jobs, web traffic, app-rating history, flights.

## 1. Inventory

Method:
- Endpoint list from Quiver's own OpenAPI document (`https://api.quiverquant.com/docs/schema.json`, 54 paths).
- Legacy paths taken from the official `quiverquant` Python client (v0.2.6).
- Every path was hit once with this subscription's key (`quiver_probe.py`, output in `data/vendor/quiver/inventory_probe.csv`).
- Auth: the OpenAPI security scheme is HTTP `bearer`, so the header is `Authorization: Bearer <token>`. The legacy `Token <token>` header also works.
- Rate limits: the docs state none, and no `X-RateLimit-*` or `Retry-After` headers came back on any of 67 responses. We stayed at 1 req/s and saw no 429s.

"History" is what this key actually returned on 2026-10-14. One example ticker was used for the per-ticker endpoints (TSLA or LMT).

### Prediction-market data (the item that matters most)

| endpoint | what it is | history | update | access |
|---|---|---|---|---|
| `/beta/live/polymarkettrades` | Polymarket trades with an estimated value of at least about $10k ("large/unusual"). Sports markets are excluded by default. Fields: wallet, side, outcome, price, size, title/slug/condition_id, trade_time, `uploaded`, and wallet stats (first activity, position rank, markets traded) | **2025-10-02 → now** (`date=` and `page=` work). About 100–500 trades a day; 152,680 rows in total with the default filter (see §3) | Hourly batch. `uploaded` lags `trade_time` by 31 min median, 55 min p90 | 200 OK |
| `/beta/historical/eventsbeta/{t}`, `/beta/today/eventsbeta`, legacy `politicalbeta` | Per-stock "event beta" to the **2020** presidential election, with "odds" (TrumpVictory/BidenVictory). | ~17 irregular monthly rows, 2020-06 → 2025-07. The odds column is stale; it reads 0.97 Trump today | Dead | 200 OK |

There is **no Kalshi data, no PredictIt data, no Polymarket price or book history and no Polymarket resolution data**. Everything in `polymarkettrades` is a filtered copy of public on-chain fills, which the repo already holds in full:
- Telonex/Dune fills: `data/vendor/telonex`, `data/vendor/dune`.
- Polymarket's free `data-api.polymarket.com/trades?filterType=CASH&filterAmount=10000` returns the same "large trade" cut in real time, without the hourly delay.

Quiver's only additions are its wallet-stat columns and the de-sporting filter. Both can be derived from the free data.

### Everything else this key can reach

| endpoint (family) | content | history returned | update | access |
|---|---|---|---|---|
| congress trading: `bulk/congresstrading`, `live/congresstrading`, `historical/{congress,house,senate}trading/{t}`, `live/{house,senate}trading`, `congressholdings`, `congress/politicians`, `bulk/congress/politicians` | STOCK-Act periodic transaction reports | bulk: 116k trades, **2012-02 → 2026-10-08** | daily (filings arrive 30–45 days after the trade) | 200 |
| `live/congress_stock_holdings` | estimated holdings | needs `bioguide_id` or `ticker` | daily | 200 with params |
| `bulk/trumpstocktrades` | Trump OGE filings | 2026-08 (200 per page) | per filing | 200 |
| lobbying: `historical/lobbying/{t}`, `live/lobbying`, `live/revolvingdoor` | Senate LDA filings mapped to tickers | LMT 1999 → 2026-10; live 2025-04 → now | daily | 200 |
| `bulk/corporatedonors`, `historical/corporatedonors/{t}` | FEC PAC donations by company | paginated, 2025-12 → now on page 1 | daily | 200 |
| gov contracts: `historical/govcontractsall/{t}`, `live/govcontractsall`, `(historical/live)/govcontracts` | USAspending awards; quarterly totals | LMT 2022-04 → 2026-10 | daily | 200 |
| `live/legislation`, `live/bill_summaries` | congress.gov bill actions, enriched with lobbying and member-trade counts per bill | ~9.9k bill-action rows (current congress) | daily | 200 |
| `live/insiders` | SEC Form 4 | 2022-06 → now (20k rows per page) | daily | 200 |
| `live/sec13f`, `live/sec13fchanges`, `funds` | 13F holdings and changes | latest filings | daily | 200 |
| `historical/offexchange/{t}`, `live/offexchange` | FINRA off-exchange short volume / DPI | 2021-01 → now | daily | 200 |
| `historical/dailyprices/{t}` | EOD OHLCV | 2014 → now | daily | 200 |
| patents: `historical/allpatents/{t}`, `live/allpatents`, `patentdrift`, `patentmomentum` | USPTO grants and derived scores | 2010 → now (momentum stops 2022) | weekly | 200 |
| `live/appratings` | App Store rating and review **count** per app (1,817 apps, 795 tickers) | **rolling last 7 days only, no history endpoint** (`historical/appratings` → 404) | daily | 200 |
| `live/flights` (legacy `historical/flights/{t}`) | corporate-jet flights | **frozen at 2025-07-23**; historical returns empty | dead | 200 |
| `historical/wallstreetbets/{t}`, `live/wallstreetbets` | WSB ticker mentions and sentiment, **stock tickers only** | TSLA 2018-08 → **2025-02-21** (history frozen); live is a snapshot | live only | 200 |
| `historical/twitter/{t}`, `live/twitter` | follower counts | empty / last row 2023-08 | dead | 200 |
| `historical/wikipedia/{t}`, `live/wikipedia` | Wikipedia pageviews | **404: retired** | — | — |
| `tickerdata` | snapshot percentiles: wsbMentionsWeekly, wiki_change, twitter_change (top 100 tickers) | snapshot only | daily | 200 |
| `historical/spacs/{t}` | r/SPACs mentions | 2020-07 → 2023-04 (dead) | dead | 200 |
| `live/cnbc`, `personalities` | CNBC on-air stock picks | recent | daily | 200 |
| `live/quivernews`, `live/rss` | news headlines | recent / stale (2022) | — | 200 |
| `historical/executivecompensation/{t}`, `live/topshareholders/{t}` | DEF 14A / ownership | annual | — | 200 |
| `strategies/holdings`, `coins`, `companies`, `tickerdata` | Quiver portfolio strategies, universes | 2009 → now | — | 200 |
| `live/earningsdistortionscores/{t}`, `live/wsbcomments`, `live/cryptocomments` | New Constructs scores, raw comments | — | — | **403: needs a higher plan** |
| `historical/jobs`, `historical/webtraffic`, `historical/appratings`, `historical/sec13f`, `wikipedia` | (old Quiver datasets) | — | — | **404: no longer exist** |
| `auth/premium` | subscription check | returns `invalid permissions` (internal) | — | — |

Almost everything that works is a repackaging of **free government sources**: the STOCK Act, the Senate LDA, the FEC, USAspending, congress.gov, SEC EDGAR, FINRA and USPTO.

The "alternative data" that once made Quiver distinctive (Wikipedia, Twitter, jobs, web traffic, app-rating history, flights) is retired, frozen or snapshot-only on this plan.

## 2. Pre-registered hypotheses (written before any test was run)

I wrote these after the inventory probe (which shows only which endpoints exist and how deep their history goes) and before computing any outcome statistic.

Gate rule for every hypothesis: a test runs only if Quiver supplies a point-in-time (PIT) series with at least 12 months of history that overlaps settled Kalshi or Polymarket markets, and at least 30 independent events can be mapped to it. If it does not, the hypothesis is recorded as **untestable with Quiver**, which is a null for the question "does Quiver add value".

**H1. Attention: person or topic attention predicts Kalshi mention and entertainment outcomes beyond price.**
- Rationale: retail mention-market traders anchor on salience. A spike in public attention to a speaker or topic raises the true probability that the word is said, faster than thin mention books reprice.
- Data needed: daily attention per person or topic (Wikipedia pageviews, WSB or Twitter mentions).
- Split: train on markets closing before 2025-12-01; test on markets closing 2025-12-01..2026-02-11 (tape), as in FIN-D.
- Test: logistic regression of the outcome on logit(price at T-24h), plus z-scored 7-day attention change. The free Wikimedia pageviews API is the baseline. Quiver counts only if its series adds information beyond Wikimedia (encompassing test, likelihood-ratio p < 0.01 on test).

**H2. Politics: lobbying, government contracts, congressional trading or bill status predict Kalshi policy markets** (bills becoming law, shutdowns, tariffs, confirmations).
- Rationale: lobbying intensity and member trading may carry insiders' views of legislative odds; bill-status changes (committee report, cloture filed) are public but may be slow to price.
- Split: train on events closing before 2025-12-01, test after.
- Test: for each bill or policy event, compare the bill-status or lobbying feature at T-7d with the Kalshi price at T-7d (outcome ~ logit(price) + feature).

**H3. Company KPI markets: Quiver alt-data predicts Kalshi KPI ladders** (deliveries, app downloads, headcount, etc.).
- Rationale: app ratings counts, flights and web or job signals are leading indicators of reported KPIs. FIN-D found no stable bias in the ladders, but did not use alt-data.
- Split: release quarters before 2026-04-01 train, after test.
- Test: does the alt-data surprise (QoQ change vs the trailing trend) predict which bracket settles, beyond the T-1d price? Encompassing test, with N counted at the event level.

**H4. Polymarket large ("whale") trades flagged by Quiver predict outcomes beyond the trade price.**
- Rationale: traders who bet $10k or more might be informed. The repo already rejected generic whale-following on Polymarket (`whale_follow_category`, timing ≈ random). This tests Quiver's specific "large/unusual" filter.
- Data: Quiver `/beta/live/polymarkettrades`, non-sports, all history. Outcomes from Polymarket's free gamma API (resolved markets only).
- Split: trades before 2026-04-01 are train; 2026-04-01 onward are test.
- **H4a (calibration):** the per-$ return of taking the whale's side at the whale's own price, `(win - p_side)/p_side`. Event-clustered mean and t-stat. Pass requires t > 2 on train and the same sign with t > 2 on test.
- **H4b (tradeable):** only if H4a passes. Enter at the Polymarket price one minute after Quiver's `uploaded` timestamp (the first time we could see the trade), with fees and impact. Also map to the matching Kalshi market where one exists.
- Stop rule: if H4a fails on train, no strategy is built (H4b can only be worse, since it trades later and at a worse price).
- Known bias: restricting to markets resolved by 2026-10-14 over-weights short-dated markets. This is reported, not corrected.

**H5. Quiver "events beta" historical election odds as prediction-market history.**
- Check only: is it a usable price history for any prediction market? No outcome test.

## 3. Results

### Gate checks for H1–H3 and H5 (no outcome statistics computed)

**H1 (attention).**
- `historical/wikipedia/{t}` and `live/wikipedia` return 404.
- `historical/twitter/TSLA` returns `[]`, and `live/twitter` last updated 2023-08-11.
- `historical/wallstreetbets/TSLA` runs 2018-08-01 → **2025-02-21** and is keyed by stock ticker.
- Kalshi mention markets are keyed by speaker and word (Trump, NFL broadcasts, Fed chair). No Quiver series maps to them.
- `tickerdata` gives only a current percentile snapshot (`wiki_change`, `twitter_change`) for 100 stock tickers.
- Result: **untestable with Quiver.** The free Wikimedia pageviews API is the obvious baseline, and Quiver no longer offers anything to compare with it.

**H2 (politics).**
- `live/legislation` holds 9,896 bill-action rows, all from the 119th Congress, with action dates 2025-03-03..2025-12-18. There is no date parameter, and the source is congress.gov, which has a free API.
- Each row is enriched with lobbying and member-trade counts for that bill. That enrichment is the one Quiver-specific piece.
- Kalshi (settled, to 2026-10-14): 147 bill or shutdown events, 53 confirmation events and 65 tariff events, but spread over 107 / 43 / 54 series.
- Bill-specific markets are about one event per bill: SAVE Act, Epstein bill, reconciliation, border bill. Shutdown, tariff and confirmation outcomes turn on executive or leadership decisions that lobbying spend and member stock trades do not measure.
- Result: under 30 independent, mappable events, so the gate fails.

**H3 (KPI).**
- `live/appratings` holds 1,817 apps across 795 tickers, but only the last 7 daily scrapes (2026-10-07..13). `historical/appratings` returns 404.
- `live/flights` is frozen at 2025-07-23, and `historical/flights/TSLA` returns `[]`.
- `historical/jobs` and `historical/webtraffic` return 404.
- Result: no point-in-time history exists for any Kalshi KPI ladder. FIN-D had already found no stable KPI-ladder bias (N = 33 train events).

**H5 (events beta).**
- `historical/eventsbeta/TSLA` has 17 rows (2020-06..2025-07), each giving a stock's beta to "PresidentialElection2020" odds.
- Today's odds read 0.97 Trump. It is a dead feature, not a prediction-market price history.

### H4: Polymarket large trades (Quiver `polymarkettrades`)

Data:
- 152,680 trades (Quiver's default "non-sports" filter), 2025-10-02 → 2026-10-14. That is ~420 a day, with estimated amounts from $10k up (median ~$15k).
- Outcomes from the free gamma API (`closed=true`): 147,026 trades on 30,184 markets resolved to exactly 0 or 1. That is 96% of trades, so resolution selection bias is small.
- Side normalisation was hand-checked on 20 random fills (`data/vendor/quiver/h4_handcheck20.csv`). A SELL of outcome *o* at *p* is treated as long the complement at 1−*p*; all 20 rows were correct.
- Returns are per $ staked, taking the whale's side **at the whale's own execution price, before fees**. This is an upper bound on any follower, who could see the trade only about 31 minutes later.
- Clusters are Polymarket `event_slug`. Split: train before 2026-04-01, test from 2026-04-01.

| split | subset | trades | events | event-mean ret/$ | t | $-weighted ret/$ [95% cluster-bootstrap CI] |
|---|---|---|---|---|---|---|
| train | all | 71,682 | 9,824 | −2.2% | −3.21 | −0.9% [−2.7%, +1.0%] |
| train | non-sports titles | 16,407 | 1,974 | −4.0% | −2.36 | −2.1% [−4.5%, +0.2%] |
| train | ≥ $50k | 8,397 | 2,984 | +0.6% | 0.37 | −0.8% [−5.0%, +2.9%] |
| train | SELL side | 6,911 | 2,031 | −9.8% | −3.13 | −1.4% [−6.6%, +4.0%] |
| test | all | 75,344 | 9,327 | −2.5% | −3.55 | +0.6% [−1.8%, +3.1%] |
| test | non-sports titles | 11,467 | 781 | −5.7% | −2.67 | −1.3% [−8.0%, +3.2%] |
| test | ≥ $50k | 8,926 | 2,220 | +0.3% | 0.19 | +2.1% [−2.8%, +7.0%] |
| test | SELL side | 7,056 | 1,914 | −14.0% | −3.94 | −4.4% [−9.8%, −0.4%] |

The price-bucket breakdown is in `data/vendor/quiver/h4_results.csv`. No bucket is positive with t > 2 in both train and test. The train 0.2–0.5 $-weighted +6.8% flips to −1.4% on test.

**Result:** H4a fails the pre-registered gate (t > 2 positive on train). Large Polymarket trades do not beat their own price; equal-weighted, they lose slightly. This matches the repo's earlier rejection of whale-following (`whale_follow_category`, docs/WHALE_RETEST.md).

Under the stop rule, H4b (following at the price after `uploaded`, plus fees and impact, and mapping to Kalshi) was not run. Its results could only be worse. The DSR is not applicable because no strategy was built. The trials added to the repo-wide count are 5 hypotheses, plus about 8 H4 subsets.

## 4. Verdict

**Quiver adds no value for this repo's prediction-market research.**

- **Prediction-market data:** only `polymarkettrades`. It is free elsewhere in real time (Polymarket `data-api`), lags 31 minutes, and has no predictive content (H4).
- **Attention and alt-data endpoints:** these are the ones that could have mattered for Kalshi mention, entertainment and KPI markets. They are retired (Wikipedia, jobs, web traffic: 404), frozen (WSB 2025-02, flights 2025-07, Twitter 2023) or snapshot-only (app ratings, 7 days).
- **Political endpoints:** convenient joins of free government data (congress.gov, LDA, STOCK Act, FEC, USAspending). Kalshi's policy markets are too few and idiosyncratic for any of them to be tested.

If the subscription is kept for other reasons (equity research), the only marginal prediction-market use is a convenience feed of large Polymarket trades for discretionary monitoring. It should not be renewed on prediction-market grounds.

## Blockers & how to unblock

| blocker | effect | how to unblock | cost |
|---|---|---|---|
| Quiver attention history (Wikipedia, Twitter, WSB after 2025-02) retired or frozen | H1 untestable with Quiver | Use the **free** Wikimedia pageviews REST API (`wikimedia.org/api/rest_v1/metrics/pageviews/per-article/...`, daily, 2015 →) for speakers and topics; run H1 as a separate free-data study | $0 |
| No app-ratings, jobs or web-traffic history | H3 untestable | Start a daily forward log of `/beta/live/appratings` (already paid for) or free App Store / Google Play scrapes for tickers with Kalshi KPI series (HOOD, app-download KPIs); revisit after ≥ 4 release cycles (~1 year) | $0 (user action: schedule a daily job) |
| `earningsdistortionscores`, `wsbcomments`, `cryptocomments` return 403 | Not needed for prediction markets | A higher Quiver tier (Institutional; price not published, quote-based) | Not recommended |
| Kalshi policy markets are ~1 event per bill | H2 cannot reach N ≥ 30 on any data | None. Revisit if Kalshi lists recurring bill-passage ladders | — |
| Polymarket whale feed lags 31 minutes | Any follow strategy enters late | Use Polymarket's free real-time `data-api/trades?filterType=CASH&filterAmount=10000`, though H4 shows no edge even at the whale's own price | $0 |

## Files

- `data/vendor/quiver/inventory_probe.csv`: status, rows and date range for every endpoint.
- `data/vendor/quiver/raw/*.json`: one sample response per endpoint. Local only.
- `data/vendor/quiver/pm_whales.parquet`: 152,680 large Polymarket trades.
- `data/vendor/quiver/pm_whale_outcomes.parquet`: resolutions from the gamma API.
- `data/vendor/quiver/h4_results.csv`, `h4_handcheck20.csv`.
