# MRULE: do traders misprice how Kalshi will rule on borderline mentions?

Last revised: 2026-10-16

Round 6, 2026-10-16. Research only: no orders, no keys. Scripts: `scripts/research/mrule_*.py`; outputs:
`data/vendor/mrule/`. Brief: `docs/research_briefs/mention_markets_framework.md`; seed example from
`docs/ASR_LIVE_MENTION_DETECTION.md` (26JAN "Renovation").

## Question

A mention market jumps to >= 95c (somebody heard the word), then trades back down before settlement (somebody doubts
it counts). Does the market misprice the eventual ruling after such a "reprice-then-fallback"? If so, buying the side
the contract rules imply after the fallback should pay.

## Pre-registration (written 2026-10-16 before any episode count, outcome or P&L was computed)

What was looked at before writing this section: the 26JAN-RENO print path and its rules text (the seed example given
in the task), and that the official FOMC transcript for 2026-01-28 does not contain "renovation" while our Deepgram
stream heard "technological renovation, you know, revolution" at s = 2640 (chair). Nothing else.

### Universe and groups
- Tape: `data/vendor/ment5/tape.parquet` (settled binary mention markets, 2025-01-29..2026-10-13), all series.
- Groups use `mhaz_common.family()`:
  - **headline**: `earn`, `fed`, `sports`, `speech`;
  - **separate, labelled, never pooled into the verdict**: `x_exec` (Trump / VP / press secretary / cabinet: MNPI /
    outcome-control risk, CFTC Aug 2026 action), `x_prerec` (pre-recorded content, pre-release risk), `x_nonlive`
    (weekly/monthly "say" windows).

### Episode definition
- **Jump Jc** (mhaz detector, unchanged): the first print with YES price >= 0.95 whose market's last print at least
  300 s earlier was < 0.80.
- **Fallback F**: the first print strictly after Jc with YES price <= 0.60. One episode per market (the first Jc).
- **Deep fallback**: same with <= 0.30 (descriptive only).
- Catalogue per episode: settlement (y01), Jc, F, market last print, settle_ts, time Jc->F, F->close, F->settle,
  min price after F, last price before close, contracts and $ traded after F by taker side.

### Split
Train = event end (`ev_end`) < 2026-05-01 UTC; test = on/after (same split as mhaz/ment5).

### Price-only rules (P-YES, P-NO)
- Decision time D = F + L, L = 5 s (60 s and 300 s as sensitivities).
- Fill state at a print t > D: "in fallback" = the market's last print before t - L had YES price <= 0.60.
- **Taker fills**: P-YES fills on later taker-YES prints at that print's YES price; P-NO on later taker-NO prints at
  the NO price 1 - y. Share of each print: Ta 50%, Tb 25%, Tc 10% (taker analogue of Fa/Fb/Fc). At most one fill per
  market per second. Taker fee ceil(0.07 C p (1-p)) per order.
- **Maker fills**: `audit_flb_common.causal_opps` on the chosen side, Fa/Fb/Fc (Q0 71 earnings, 20 otherwise),
  eligible while in fallback after D; maker fee only on maker-fee series (point-in-time).
- **Selection**: on train only, choose the side (YES or NO) with the higher taker Tb net cents per contract. That side
  is applied to test only if its train event-cluster CI lower bound is > 0. Otherwise the price-only rule is
  `rejected` without looking further. Trials logged: 2 sides x 3 latencies x 3 shares + 3 maker = 21.

### Rules-text predictor (R-rule)
- For episodes with a transcript, count the market's word in the transcript under the contract grammar from
  `rules_secondary`: exact word/phrase, or plural / possessive forms (s, es, 's, s'), case-insensitive, on word
  boundaries; tense inflections excluded. Predict YES if count >= 1 (respecting a "N times" threshold where the
  title says so), else NO. Trade the predicted side with the same fills.
- Sources: FOMC press-conference transcripts (`data/vendor/ment5/fomc`), Motley Fool earnings transcripts
  (`data/vendor/a1fool`), Trump transcripts (`data/vendor/trumpx`, separate group), our ASR streams
  (`data/vendor/asr/stream`, timed).
- **Causality caveat, stated up front**: official transcripts are published after the market closes, and they are
  edited. They stand in for "a live listener who heard everything", and they also leak words said after D. So the
  transcript R-rule is an *upper bound*, not a causal backtest. The causal version uses only our ASR word times
  < D, and it exists only for the ASR-covered events.
- No parameters are fitted, so train and test are both reported; the verdict weighs test.

### Verdict rules
- Fewer than ~20 headline episodes with a tradable post-fallback window: at most `unproven`, with a forward design.
- `promising` needs: test taker Tb net > 0 with event-cluster CI > 0, avg deployed >= $1k at $10k, DSR > 0.5.

### Cause classification (descriptive, post hoc, not used for trading)
grammar variant (plural, possessive, compound, inflection); homophone / ASR mis-hearing; wrong speaker; quoted clip
or video; event boundary (said before/after the counted window); settlement clarification; self-corrected slip
(spoken but edited out of the official transcript); fat-finger / thin print (no word evidence at all).

<!-- results below were written after the pre-registration -->
