# NOWC: earnings-call mention markets vs "always said" base rates (EDGAR press releases + Kalshi's own history)

Last revised: 2026-10-15

Research only; no orders. Pre-registration: docs/NOWC_MENTION_PREREG.md. Already rejected and not
redone: kalshi_earnings_mention_no_maker, kalshi_mention_no_maker (artifact), kalshi_mention_wikipedia_attention.

## Verdict
**Rejected as a strategy.** Base rates are strongly informative on their own, but the market already prices them.
- **Log-loss.** Adding base-rate features to the price does not improve log-loss out of sample.
- **Maker NO fade (coordinator framing).** A causal maker fading YES-priced 10-40c words chosen by low base rate
  fails the kill rule: -2.2c per filled contract on 1,658 test fills.
- **Taker NO on low-base-rate words (R3).** Positive out of sample (+5.5c/contract), but the same rule without the
  base-rate filter earns +5.9c. The base rate is not what makes money: the T-24h YES-overpricing already documented
  in the repo is. R3 also deploys about $7 on average.

| rule (pre-registered, no grid) | test $/yr at $1k / $2.5k / $5k / $10k | avg deployed | edge/contract (event CI) | test Sharpe | DSR (12 / 8,000 trials) |
|---|---|---|---|---|---|
| R1 LOW maker NO 60-90c, Fa | -1,219 / -879 / -133 / -6,955 | $485 / $1.1k / $1.9k / $3.2k | -2.2c [-6.5, +2.0] | -1.4 | 0 / 0 |
| R1 LOW, Fb | -1,010 (at $1k) | $426 | -1.9c [-6.1, +2.2] | -1.3 | 0 / 0 |
| R1 LOW, Fc | -317 / -2,068 / -2,562 / -1,819 | $371 / $741 / $1.2k / $1.3k | -0.6c [-4.7, +3.2] | -0.5 | 0 / 0 |
| R1 ALL (control), Fa | -4,101 (at $1k) | $570 | -6.4c [-10.7, -2.1] | -4.0 | 0 |
| R1 HIGH (control), Fa | -5,571 (at $1k) | $276 | -25.3c [-33, -17] | -6.5 | 0 |
| R2 taker YES, HIGH words <= 60c | -270 (at $1k) | $1 | -19.4c [-37.6, -1.7] | -2.7 | 0 |
| R3 taker NO, LOW words YES >= 40c | +486 / +572 / +572 / +572 | $7 | +5.5c [-3.9, +15.7] | 1.9 | 0.53 / 0.04 |
| R4 YES after the same-quarter EDGAR release | +168 / +43 / -71 / -71 | $2-3 | +2.0c [-4.6, +9.9] | 0.9 | ~0 |

Train (events closing before 2026-05-01) for reference:
- **R1 LOW.** Fa +1.3c/contract [-2.6, +4.8], +$407/yr at $1k. This is already below the +1.5c kill bar.
- **R3.** +7.8c [+0.7, +16.3].
- **Controls at T-24h.** The R3 rule on all words made -0.2c in train and +5.9c in test. On HIGH words it made -5.5c
  in train and +17.8c in test. So the R3 profit in test is a period-wide NO effect, not something the base rate
  selects.
- **Fill ordering.** Across fill models and both periods, the order is LOW > ALL > HIGH: the base rate does rank which
  words are least bad to fade. But the level is negative under causal fills, as in the earlier audit.

**OOS status.** Test window is 2026-05-01..2026-10-12: 169 events, 2,359 markets with a T-24h price.

## Features and coverage
- **Kalshi own history (kb).** The same company and the same normalised word, settled in an earlier call. Covers 45%
  of markets, because Kalshi rotates word lists.
  - Train: P(yes | said last call) = 0.77; P(yes | not said) = 0.29; P(yes | no history) = 0.55.
- **EDGAR press releases (pr).**
  - Source: 8-K item 2.02 / 6-K, exhibit 99.x, accepted at least 48h before the call ended; the last 4 releases.
    Pulled for 2023-06 onward via the free EDGAR API, at <= 4 req/s with a generic-contact User-Agent.
  - Covers 92% of markets. Missing: AC, ARITZIA (no SEC filer), EA, VSCO (ticker map), and AZO, CBRS, CRCL, M, NVDA,
    RACE, TOL, TSM, URBN, ZS (exhibit file names were not matched).
  - Matching follows Kalshi's rule: the exact word, or its plural or possessive. Hyphenated and compound tokens are
    excluded, and "A / B" lists count as any variant.
  - Train: P(yes | word in the last release) = 0.75; P(yes | not) = 0.45.
  - P(yes | word in the same-quarter release) = 0.78.
- **Combined score.** BR = (kb_said + pr_hits + 1) / (kb_n + pr_n + 2).
  - Train: LOW markets (BR <= 0.3, n=1,421) resolved yes 39.1% against a T-24h price of 48.5c. HIGH markets resolved
    76.6% against 77.6c.
  - Test: LOW 38.4% vs 42.9c; HIGH 70.3% vs 72.9c.
- **Logistic, train.** Outcome ~ logit(price at T-24h) + base-rate features, event-clustered errors (n = 2,730 markets,
  217 events):
  - logit(price): coefficient 1.10 (t = 20.4).
  - BR: t = 0.56. pr_last: t = 1.71. kb_last: t = -0.57.
  - Log-loss gain is 0.003 in-sample and -0.0005 on test (0.5317 with features vs 0.5312 price-only).
  - This agrees with the brief's lead (Kim et al.): market Brier on earnings mentions is already about 0.14-0.18, and a
    plain base rate is priced in.

## Rules, risks, and the CFTC probe
- **Resolution rules** (rules_secondary, verified on the stored markets):
  - The call audio is used to resolve.
  - The exact word or phrase counts, as do its plural and possessive forms. Other grammatical or tense inflections do
    not count.
  - A hyphenated or single compound word does not count. A space-separated compound group ("fire truck") does count.
  - The market covers any company representative, including during Q&A.
  - Press-release text is a proxy only: a word in the release does not have to be spoken. "Prepared remarks only"
    rules do not exist.
- **CFTC.** Press reports from August 2026 (NPR, via crypto.news and DeFi Rate) say the CFTC is reviewing whether
  mention markets are too easy to manipulate. Kalshi pulled sports mention markets "until further notice". Earnings-
  call and political mention markets are still listed. The CFTC has not confirmed the review. A separate CFTC
  insider-trading probe concerns Trump-speech mention bets (Bloomberg Law, July 2026).
- **Executive gaming risk.** It is real (the Coinbase CEO reading out market words at the end of a call has been
  widely reported). It is a tail risk for any NO-side position, and zero observed losses would not mean zero risk.

## Blockers & how to unblock
- **Full call transcripts** (the real "always said" base rate, 20+ quarters).
  - Free option: Motley Fool transcript pages. robots.txt allows crawling, but the site terms were not confirmed, and
    the fin_D decision was not to scrape. User action: decide whether scraping Motley Fool transcripts is acceptable
    (cost $0, legal/ToS judgement).
  - Paid option: Seeking Alpha Premium, about $240/yr (above the $0 budget).
  - Expected value is low. The price already absorbs the release and Kalshi-history base rates, and the coordinator
    has queued a fuller count-model study.
- **Execution depth.** R3 capacity (one print per market) cannot be judged without book depth at T-24h. The recorder
  now logs mention books; about 4 weeks of earnings season (late Oct to Nov 2026) would measure it.
  - Free: needs only the existing recorder.
  - Re-test R3 against its all-words control if wanted. If the control matches it again, the effect belongs to the
    already-rejected mention-NO family.

## Files
scripts/research/nowc_edgar_fetch.py, nowc_mention_build.py, nowc_mention_eval.py;
data/vendor/nowc/{edgar/, mention_markets.parquet, mention_trades.parquet, mention_rules_{train,test}.csv,
mention_logit_{train,test}.json}.
