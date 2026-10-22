# NOWC pre-registration: earnings-call mention markets vs "always said" base rates

Last revised: 2026-10-15

Written 2026-10-14 before any price/outcome comparison with the features below.

## Universe and split
- All Kalshi earnings-call mention markets (KXEARNINGSMENTION*, KXMENTIONEARN*) settled yes/no with >= 1 trade
  (no selection on volume or outcome). Trades: fin_D local tape (closes <= 2026-02-11) + fin_D public REST pull.
- Train: events closing before 2026-05-01. Test: 2026-05-01 .. 2026-10-12. Test touched once per rule.

## Features (strictly causal)
- kb_*: Kalshi's own settled outcome for the same company + same normalised word in earlier calls (closed before this
  event opened). kb_last = said on the previous listed call.
- pr_*: SEC EDGAR earnings press releases (8-K item 2.02 / 6-K, EX-99.x) accepted >= 48h before the call ended; last
  <= 4 releases; match = exact word/phrase or plural/possessive, not inside a hyphenated/compound token (Kalshi rule).
- Base-rate score BR = (kb_said + pr_hits + 1) / (kb_n + pr_n + 2), defined when kb_n + pr_n >= 2.
  LOW: BR <= 0.30. HIGH: BR >= 0.70. No fitting.
- pr_same: word appears in the same-quarter release (accepted within 48h before the call end); usable only after
  its EDGAR acceptance time.

## Rules (fixed, no grid beyond what is listed)
- R1 maker fade (coordinator framing): post-only NO bids at causal levels (audit_flb_common Fa 1c-inside 50% /
  Fb trade-through 25% / Fc join-queue 10% behind Q0 = 25), NO own price 0.60-0.90 (YES 10-40c), resting from open to
  event close - 2h, LOW markets only. Controls: same rule on ALL markets and on HIGH markets.
  Kill: < +1.5c per filled contract over >= 300 test fills, or event-clustered CI including 0.
- R2 taker YES on HIGH words priced low: at t0 = close - 24h, first taker-YES print within 2h with YES ask <= 0.60.
- R3 taker NO on LOW words priced high: at t0, first taker-NO print within 2h with YES >= 0.40 (NO ask <= 0.60).
- R4 same-quarter release lead-lag: word in the same-quarter release; buy YES at the first taker-YES print in
  [acceptance + 60s, acceptance + 30min] with ask <= 0.90, only if before event close - 90 min.
- Fees: taker ceil(0.07 C p(1-p)); maker fee only where the series fee_type charges it (mention series: none).
  Caps 5%/market, 15%/event, held to settlement, no profit recycling.
- Report: per-contract edge, PnL/yr at $1k/$2.5k/$5k/$10k, avg deployed, event-cluster bootstrap CI, by quarter,
  top-5 event share, DSR vs own trials (R1 x 3 fill models x 3 classes + R2-R4 = 12) and vs ~8,000 repo trials.
- Also: logistic y ~ logit(price at t0) + BR + pr_last + kb_last on train, event-clustered SEs, test log-loss gain.
