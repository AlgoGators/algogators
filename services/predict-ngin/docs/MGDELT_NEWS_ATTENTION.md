# GDELT news attention vs Kalshi speaker-mention markets (mgdelt), 2026-10-16

Last revised: 2026-10-16

**Verdict: `rejected` (null on train; stopped per the pre-registration, so there was no test-period
fetch and no strategy).**
- The speaker-and-term news-intensity residual z and the breadth B were measured from the raw GDELT
  GKG files on 3,007 train markets / 179 events. Neither predicts the outcome beyond the price.
  - z_w: +0.043 per SD, t = 0.80, CV log-loss gain −0.00002 [−0.00101, +0.00103].
  - B: +0.135 per SD, t = 1.61, CV gain +0.00080 [−0.00366, +0.00501].
- **Caveat that matters.** The GDELT DOC API (full-text search) was throttled for this IP, so the
  test used the GKG *proxy*: titles, named entities and quotes, not full text.
  - On 36 markets where both sources exist, the proxy misses common-noun terms ("Restrictive",
    "Stock Market", "America First").
  - So the full-text version is only weakly covered; see "Re-test only if".

Research only; no orders. Prefix `mgdelt`. Brief: docs/research_briefs/mention_markets_framework.md
(attention / transmission, branches 1 momentum and 2 overreaction).

## Question

Does **news co-mention** of a speaker and a word (GDELT DOC 2.0, free, 15-minute crawl cadence)
predict whether the speaker says the word, **beyond the Kalshi price** at a fixed decision time?
If it does, is there a tradable momentum branch (broad, transmitting attention) or a fade branch
(narrow / concentrated attention the market over-reacts to)?

### Why this is not a retest of the rejected Wikipedia idea

`kalshi_mention_wikipedia_attention` (docs/RELEASE_ATTENTION_MENTIONS.md) is rejected
with `retest_if` "an hourly or same-day attention source becomes cheap to pull, and a pre-registered
intraday cutoff test is wanted". GDELT is a same-day source (15-minute updates, free), so the
condition is met, and the signal is a different object:

- Wikipedia measured **generic interest** in the word's article (e.g. views of "Tariff"), or in the
  speaker's article, separately. Neither says whether the *speaker* is being asked about or engaged
  with the word.
- GDELT measures **speaker-and-term co-occurrence in the news**: articles that mention both
  "Powell" and "tariff". This is the brief's transmission channel: what reporters put to the speaker,
  and what the speaker's news cycle is about, is what tends to come up in a press conference or
  rally. It also gives **breadth** (number of distinct outlets) and **source concentration**
  (domain HHI), which pageviews cannot.
- Exposure is controlled: the count is modelled against GDELT's total monitored volume for the
  same bin, with day-of-week terms, so a quiet Sunday or a big news day is not mistaken for attention.

## Pre-registration (written 2026-10-15 before any GDELT data was joined to outcomes)

Only the market universe (tickers, events, close times, prices) and two GDELT probe queries
(`"Powell" "tariff"` timeline and article list, to check the API format and historical coverage)
were looked at beforehand.

**Hypotheses.**
- H1 (z): an unusually high speaker-and-term news intensity in the 3 days before the decision,
  relative to its own NB baseline, predicts YES beyond the price.
- H2 (breadth): broader coverage (more distinct outlets) of the speaker-and-term pair in those
  3 days predicts YES beyond the price.
- H0: conditional on the price, neither adds anything.

**Universe (no hindsight selection).**
- The 63 single-speaker series of the Wikipedia study (`data/vendor/release/wiki/speaker_map.csv`,
  fixed there before any outcome), with a GDELT query string per speaker
  (`SPEAKER_Q` in scripts/research/mgdelt_common.py).
- Markets and prints from `data/vendor/fin_D/mention_sim_input.parquet` (tape plus API trades,
  2025-01-29 to 2026-10-13). `ev_end` = last close in the event (fin_D convention; mild timing
  look-ahead, no outcome look-ahead).
- **Decision time.** D = UTC day of (ev_end − 24h); td = D 00:15 UTC. So every decision is at least
  24h before the event ends, and GDELT daily bins through D−1 are complete and indexed
  (15-minute lag) at td.
- A market enters iff it is still open at td (close_ts > td), has at least one print in
  [td − 72h, td] (so it has a price and has been listed for a multi-day window), and settled yes/no.
  There is no volume filter, so illiquid markets are in.
- Price p = YES price of the last print at or before td, clipped to [0.01, 0.99].
- Term = the market's Word (custom_strike or yes_sub_title), split on " / " into OR-alternatives,
  with any "N+ times" suffix removed. The term comes from the market's listing, so it is known
  before td.
- **Compliance flag.** Executive-Office speakers (Trump, Vance, Leavitt / press secretary, Melania
  Trump) are flagged non-compliant following the Aug 2026 CFTC action over presidential mention
  markets. Every speaker-says contract is controllable by the speaker; that is not usable by us and
  we use only public news. The gate below is run on the full universe, because it asks whether
  news transmits to speech. Any trading rule (Test 2) trades **only compliant series**, and the
  compliant-only gate is reported as a secondary result.
- **Split.** Train: ev_end < 2026-02-01 UTC (3,405 markets, 201 events; compliant 1,456 / 84).
  Test: everything later (4,037 / 195). Test GDELT data is not even fetched unless the gate passes.

**Features (strictly point-in-time; GDELT bins dated before D 00:00 UTC only).**
- GDELT DOC 2.0 `timelinevolraw`, query `<speaker> <term>` (both must appear in the article),
  daily bins: y_d = matching articles, n_d = all articles GDELT monitored that day (exposure).
  Bins are by GDELT's crawl ("seen") time.
- NB2 baseline on days D−59..D−4 (56 days): log μ_d = log n_d + β0 + β_dow(d), ridge penalty 1.0 on
  the six day-of-week terms, overdispersion α by maximum likelihood (α ≥ 1e-4), plus a 0.5
  pseudo-count so all-zero baselines are defined.
- Attention window D−3..D−1: Y = Σ y_d, M = Σ μ_d, V = Σ(μ_d + α μ_d²).
  **z = (Y − M) / sqrt(V)** (overdispersion-adjusted residual).
- **Breadth** from `artlist` over [D−3 00:00, D 00:00) UTC, the 250 most recent matching articles:
  B = log1p(number of distinct domains). Secondary: domain HHI (needs ≥ 5 articles), and the
  3-day level L = log((Y + 0.5)/Σ n_d).
- Queries GDELT rejects (too short / too common words) give missing features; those markets are
  dropped from that variable's test (outcome-blind).
- The article-list fetch runs in a fixed random order (seed 7). If it cannot finish, H2 is tested on
  the random subset fetched; that is outcome-blind.

**Gate (Test 1, train only).**
- Logistic regression y ~ 1 + X with **offset logit(p)**, event-clustered sandwich SE, run separately
  for X = z (H1) and X = B (H2), X standardised.
- A variable passes only if **both** hold:
  1. |t| ≥ 2.24 (Bonferroni for two primary variables);
  2. the event-grouped 5-fold CV log-loss gain over y ~ 1 + offset(logit p) has a 95%
     event-bootstrap CI above 0.
- Descriptive only: the same with logit(p) as a free regressor; calibration (YES rate − price) by
  z tercile; the compliant-only subset; HHI and L.
- **Stop rule.** If neither passes: stop, write the null, no test-period fetch, no strategy.

**Test 2 (only if the gate passes; frozen now).**
- ρ_s (transmission) = per-series coefficient on z in the train model (shrunk to the pooled
  coefficient with weight n_s/(n_s+200)).
- Momentum: buy YES when z > 2, B in the top train tercile and ρ_s > 0.
- Fade: buy NO when z > 2 and (B in the bottom train tercile or HHI in the top train tercile).
- Taker at the last print + 1c slippage, Kalshi taker fee ceil(0.07·C·p·(1−p)), $25 per market,
  compliant series only, held to settlement. Run once on the test period. No maker simulation
  (mention maker fills are informed: docs/MKGAP_MAKER_TAKER_GAP.md).

### Amendment 1 (2026-10-15 ~02:55 UTC, before any feature was computed or joined to outcomes)

- GDELT's DOC API answered HTTP 429 ("one request every 5 seconds") to most requests from this IP,
  even at 15-40 s spacing (about one success per 40-80 s; probably other users on the same IP).
  The 1,548 train (speaker, term) timelines would take more than a day.
- So the timelines are fetched in a fixed random order (seed 7, outcome-blind), and **the gate runs
  on every pair fetched by 2026-10-15 08:00 UTC**. That is a random subsample of the pre-registered
  universe; nothing else changes.
- H2 (breadth) needs one article-list request per market. It is fetched only after the timelines
  and only if time remains; otherwise H2 is reported as untested, not as null.
- GDELT rejects quoted phrases shorter than about 5 characters ("Gaza", "NHS"). Such terms are sent
  unquoted. If GDELT still rejects them, they are missing (outcome-blind).

### Amendment 2 (2026-10-15 ~03:15 UTC, before any count was joined to outcomes)

- After Amendment 1 the DOC API returned about one response per 10 minutes.
  That would cover about 30 pairs by morning, so the primary data source moves to the **raw GDELT 2.1
  GKG files**. These are static files on data.gdeltproject.org with no API and no rate limit, and the
  brief allows them (scripts/research/mgdelt_gkg.py).
- **Time subsample.** One file per hour (the hh:00 15-minute batch), a fixed 1-in-4 time subsample.
  Counts and exposure come from the same files.
- **Speaker hit.** A fixed regex per speaker on GKG Persons + AllNames.
- **Term hit.** A whole-word regex (optional plural or possessive) on page title + AllNames +
  Persons + Organisations + Locations + Quotations + Themes.
  - GKG has no article full text, so this is a *proxy* for "the term appears in the article". It is
    narrower than DOC full-text search: titles, named entities, quotes and GDELT's theme codes.
  - Fix before any outcome join, ~03:20 UTC: Themes were removed from the term blob. On one day
    (2026-01-22), GDELT taxonomy prefixes (TAX_, WB_, EPU_ ...) made "Tax" match 47 of 48 Mamdani
    articles. The run was restarted; the first 10 days are kept in `gkg_v0_themes/` and not used.
- y_d = number of articles that hit both speaker and term; n_d = all GKG articles in that day's
  sampled files (exposure).
- The NB baseline, z, and windows are unchanged.
- **Breadth.** B = log1p(distinct `SourceCommonName` domains) among the D−3..D−1 co-mention
  articles; HHI is computed over the same articles (needs ≥ 5). Because the raw files give every
  domain, H2 no longer needs per-market API calls, so H2 is tested on the same markets as H1.
- **Days fetched.** The union of [D−59, D−1] over train decision days (402 days, 2024-12-21 to
  2026-01-26), newest first. If the download has not finished by 2026-10-15 08:30 UTC, the gate runs
  on markets whose full 59-day window is present. That cut is by date, so it is outcome-blind.
- The DOC-API fetch keeps running in the background. Its pairs are used only as a descriptive
  cross-check of the GKG proxy (correlation of the two z's).

### Amendment 3 (2026-10-15 ~05:40 UTC, after feature distributions were seen, before any outcome join)

- The GKG z has a very heavy right tail: median −0.31, 75th percentile 0.30, max 615, from terms
  whose news count jumps from a near-zero baseline. A standardised raw z would be driven by a
  handful of points.
- The primary H1 variable is therefore **z_w = z winsorised at its train 1st and 99th
  percentiles**. Raw z is reported as secondary. Nothing else changes.

## Data

| what | path | coverage |
|---|---|---|
| Market panel (universe, td, price, term) | `data/vendor/mgdelt/panel.parquet` (mgdelt_build.py) | 7,442 markets: train 3,405 / 201 events, test 4,037 / 195 (test untouched) |
| GDELT 2.1 GKG, hourly :00 batch, speaker x term hits + domains | `data/vendor/mgdelt/gkg/<day>.parquet/.json` (mgdelt_gkg.py, 81 MB) | 402 days 2024-12-21..2026-01-26. 18 days (2025-06-14..07-01) have no GKG files on GDELT's server (HTTP 404, a GDELT outage); 384 days usable |
| Train features | `data/vendor/mgdelt/features_train.parquet` (mgdelt_gkg_features.py) | 3,007 / 3,405 markets with a complete 59-day window (the rest overlap the outage), 179 events; compliant 1,293 / 76 |
| DOC API cache (cross-check only) | `data/vendor/mgdelt/raw/` (mgdelt_fetch.py) | 16 timelines plus 2 probes before the throttle made it impractical |
| Gate output | `data/vendor/mgdelt/gate_train.json`, `gate_train.log` | |

Each GKG day has about 15-35k articles in the sampled files. On a typical weekday, about 4,000 sampled
articles mention Trump, about 90 mention Powell, and about 50 mention Mamdani. 9.5% of train markets
have zero baseline co-mentions (the 0.5 pseudo-count keeps z defined).

## Results (train only)

Logistic y ~ 1 + X with offset logit(p), event-clustered SE. Pass = |t| ≥ 2.24 AND the CV log-loss
gain CI is above 0.

| universe | variable | N mkts / events | coef per SD | t | CV log-loss gain / mkt [95% CI] | t with logit(p) as regressor | pass |
|---|---|---|---|---|---|---|---|
| full | **z_w (H1)** | 3,007 / 179 | +0.043 | +0.80 | −0.00002 [−0.00101, +0.00103] | +0.71 | **no** |
| full | **B (H2)** | 3,007 / 179 | +0.135 | +1.61 | +0.00080 [−0.00366, +0.00501] | +1.42 | **no** |
| full | z raw | 3,007 / 179 | +0.022 | +0.62 | −0.00220 [−0.00673, +0.00021] | +0.66 | secondary |
| full | HHI (≥ 5 art.) | 1,318 / 156 | −0.139 | −1.82 | +0.00047 [−0.00326, +0.00421] | −1.76 | secondary |
| full | L (3-day share) | 3,007 / 179 | +0.133 | +1.61 | +0.00075 [−0.00317, +0.00411] | +1.41 | secondary |
| compliant | z_w | 1,293 / 76 | +0.013 | +0.23 | −0.00059 [−0.00105, −0.00011] | +0.00 | secondary |
| compliant | B | 1,293 / 76 | +0.086 | +1.13 | −0.00032 [−0.00244, +0.00169] | +0.78 | secondary |
| compliant | HHI | 316 / 61 | −0.352 | −2.11 | +0.00326 [−0.00870, +0.01381] | −2.00 | secondary |

**Calibration (YES rate − price) by tercile, full universe.**

| | low | mid | high |
|---|---|---|---|
| z_w | −7.4pp | −10.0pp | −6.3pp |
| B | −9.7pp | −8.7pp | −5.3pp |
| HHI | −4.5pp | −5.6pp | −8.4pp |

By raw z bucket, the gap is −5.7pp for z ≤ −1, −8.9pp for (−1, 0], −4.8pp for (0, 1], −6.1pp for (1, 2],
−11.9pp for (2, 5] (n = 197) and −3.8pp for z > 5 (n = 148). It is not monotone, so there is no
momentum and no clean overreaction pattern.

**Reading.**
- News attention, measured as a surprise (z) or as a level (L), carries no information beyond the
  price.
- Breadth and concentration point the way the brief's branches would predict: broad coverage → YES
  less overpriced; concentrated coverage → YES more overpriced. But t = 1.6 / −1.8 and the CV gains
  include 0, so this is the kind of pattern the gate exists to stop.
- The large uniform YES overpricing 24h out (−7.9pp overall) is the known Yes bias. MENT5 covered it
  and its taker was rejected (`kalshi_mention_yes_bias_lifecycle_taker`); it is not a new lead.

**Descriptive, not pre-registered.**
- Restricted to markets whose term is well measured (≥ 1 baseline co-mention per day, 1,739 markets /
  160 events): z_w t = 0.98, B t = 1.85. That is still below the bar.
- With < 1 per day: z_w t = −0.14, B t = 0.39. The null is not just measurement noise in rare terms.

**GKG proxy vs DOC full text (36 markets, 16 pairs fetched from the DOC API before the throttle).**
- Spearman between the two z's: 0.12. Between the 3-day counts: 0.43.
- The GKG proxy finds 0 co-mentions for common-noun terms where full text finds many: Powell +
  "Restrictive" 37 vs 0; Leavitt + "Stock Market" 38 vs 0; Vance + "America First" 91 vs 0. It works
  for named entities (Trump + "El Salvador" 648 vs 295).
- So the test is informative for entity-like terms (people, places, organisations: most Trump,
  Mamdani and Sanders words). It is weak for policy jargon (Fed words, slogans).

## Test 2

Not run, per the stop rule. There was no test-period fetch, no ρ estimate and no momentum or fade
rule. $/yr at $1k and $10k is $0 (nothing to deploy), capital deployed is $0, and Sharpe, DSR and the
bootstrap P&L CI are n/a.

## Trials

- 10 gate regressions: 2 primary and 3 secondary variables × 2 universes.
- 4 descriptive measurement-quality splits.
- 1 DOC-vs-GKG cross-check.
- Primary pre-registered trials: 2. Repo-wide count of about 8,000 + 15.

## Look-ahead checks

- Decision td = D 00:15 UTC. Every GKG input comes from files stamped ≤ D−1 23:00 UTC; the last one is
  published by about 23:15. That is ≥ 1h before td, and td is ≥ 24h before the event ends.
- The price is the last print ≤ td.
- Terms come from the market listing (custom_strike / yes_sub_title). The speaker set and the regexes
  were fixed before any count was computed.
- The GKG file time is GDELT's crawl batch, not the article's own date, so late-crawled old articles
  count at crawl time. This is conservative: it is when the information was available.
- `ev_end` (and so D) uses the last close in the event, which is mild timing look-ahead (fin_D
  convention). There is no outcome look-ahead.
- The DOC-API timeline day stamps were first parsed with an `astype(int64)//1e9` that pandas 3 maps
  wrongly (ms). That was fixed with `src.timeconv.to_epoch_seconds` before the cross-check. The GKG
  features were not affected: they use `Timestamp.timestamp()`.
- Amendments 1-3 were all made before any outcome join. Amendment 3 (winsorise z) came after the
  feature distributions were seen; raw z is reported and is also null.

## Compliance

- Executive-Office speakers (Trump, Vance, Leavitt, Melania Trump) are flagged and would not be
  traded (Aug 2026 CFTC action). Every speaker-says market is controllable by the speaker.
- Only public news was used, at the time GDELT crawled it.
- No transcript sites were used. GDELT is approved in the round-6 addendum.

## Re-test only if

- The DOC 2.0 full-text timelines can be pulled for the train pairs, so common-noun terms are
  measured too. That means the throttle lifts, or a slow fetch of about 1-2 days at ≤ 1 request per
  minute is accepted. `mgdelt_fetch.py` resumes from cache.
- Re-run the same frozen gate on z_w and B from DOC counts; the code path is `mgdelt_features.py`.
- Alternatively, a source with per-article full text plus domains (GDELT Web NGrams) becomes
  practical.

## Files

- `scripts/research/mgdelt_common.py`: DOC API client, cache, speaker queries.
- `mgdelt_build.py`: panel.
- `mgdelt_fetch.py`: DOC API.
- `mgdelt_gkg.py`: raw GKG pass.
- `mgdelt_features.py`: DOC features and the NB fit.
- `mgdelt_gkg_features.py`: GKG features.
- `mgdelt_gate.py`: gate.
- Reproduce:
  - `venv/Scripts/python.exe scripts/research/mgdelt_build.py`
  - `... mgdelt_gkg.py data/vendor/mgdelt/gkg_days_train.csv 2` (about 2.2h, 2 workers)
  - `... mgdelt_gkg_features.py train`
  - `... mgdelt_gate.py train`
