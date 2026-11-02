# NOWC pre-registration: Kalshi initial jobless claims (KXJOBLESSCLAIMS) vs a public-data nowcast

Last revised: 2026-10-15

Written 2026-10-14 before any Kalshi price for this series was compared with the model.

## Data
- Kalshi: KXJOBLESSCLAIMS markets (ladder of "at least K" SA initial claims, first print, close 08:25 ET on release day),
  59 settled events 2025-06-12 .. 2026-10-08 (gap Oct-Dec 2025). Trades: local tape to 2026-01, public REST after.
- DOL: archived weekly press-release PDFs 2024-2026 (real-time advance SA/NSA, revised prior week) and the national
  XML (current-vintage history 2010-2026, seasonal factors published in advance through 2027-04).

## Model (frozen on 2012-2019 + 2022-2024 current-vintage history, before any Kalshi comparison)
- Candidates: M0 random walk on last advance SA; M1 NSA seasonal-ratio (NSA_{W-1} x median past-year NSA ratio / SF_W);
  M2 4-week mean of SA. Fitted blend (log scale, weights on a 0.05 grid): 0.60 M0 + 0.40 M2, M1 weight 0
  (M1 RMSE 9.0% vs 4.2% for M0: the "NSA forecast / known seasonal factor" route is worse than the SA random walk).
- Distribution: P(SA_adv >= K) from the empirical training residuals (574 weeks; sd 3.98%, bias -0.15%) applied to
  the real-time blend, with SA rounded to the nearest 1,000.
- Lags are real-time: advance SA of W-1, revised values of W-2..W-4 as printed in the PDFs (current vintage only where
  a PDF failed to parse).
- Google Trends and pre-release state data: not used (pytrends not installed and outside my write namespace; no free
  systematic pre-release state source found). Recorded as a gap.

## Split
- Train (rule selection): events closing before 2026-04-01. Test: 2026-04-01 .. 2026-10-08. Test touched once.

## Taker rule (grid, 6 variants)
- Decision time t in {Wed 12:00 ET (T-20.5h), Thu 08:00 ET (T-25min)}; margin m in {3c, 6c, 10c}.
- Executable price: the first taker print on the side we buy within 60 min after t (a real ask); fee
  ceil(0.07 C p(1-p)) per fill. Buy YES if p_model - ask_yes > fee/contract + m; buy NO symmetric. Max one entry per
  market per event; capital caps 5%/market, 15%/event, held to settlement.
- Metrics: Brier/log-loss of market (last print before t) vs model vs logistic combo; PnL/yr at $1k/$2.5k/$5k/$10k;
  event-cluster bootstrap CI; DSR vs 6 own variants and vs ~8,000 repo trials.

## Maker rule (causal fills, audit_flb_common Fa/Fb/Fc)
- Rest a post-only bid on side S at the causal level q (Fa 1c inside, Fb join + trade-through 25%, Fc join queue
  10% behind Q0 = 50 contracts) only while q <= p_model_S - m, m = 5c fixed, from market open to close.
- Pass bar for either rule: test PnL CI excludes 0 AND avg deployed >= $1k at some capital level. Otherwise reject.
