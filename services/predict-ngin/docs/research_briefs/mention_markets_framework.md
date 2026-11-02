# Brief #4 (user-supplied, 2026-10-15): quantitative framework for mention markets

Last revised: 2026-10-15

Condensed from the user's pasted report. Its formulas are kept where they define a testable rule.

## Core model
Pipeline: textual point process → conditional terminal mention probability q_t → binary fair value → thin-LOB execution.
Edge = q_t − executable price − all-in cost − κ_q·σ_q (κ_q ≈ 1.5–2.5).

Keep two NLP layers apart:
- **Resolution-exact.** A deterministic contract grammar, versioned by date: speakers, window, token patterns, plurals/possessives/compounds, event boundaries, source hierarchy.
- **Predictive-semantic.** Entity linking, fuzzy/phonetic matches. These are predictors only, never rewrites of the settlement count.

## State variables
- Kernel mention intensity λ̂_t, velocity d log λ̂/dt and acceleration.
- Unique-author and platform breadth, original-content share, repost ratio, author HHI.
- Negative-binomial baseline with an exposure offset log V_t, time-of-day/week (harmonic), event-relative time; the signal is the NB-adjusted residual z.
- Kleinberg burst states; per-source normalisation before aggregation.

## Strategy families
1. **Momentum / threshold hazard.**
   - Poisson fair value q_t = 1 − F_Pois(K − N_t − 1; Λ_{t,T}); a marked Hawkes process for self-excitation.
   - Entry needs all of: Δq > 0, velocity > min, breadth > min, edge > cost + κσ.
   - Optional order-flow-imbalance confirmation.
   - Sizing: uncertainty-scaled, capped at 10–25% of depth within a slippage band, at most fractional Kelly f = (q − p)/(1 − p).
   - Stops are model-invalidation stops, not price stops. Risk is budgeted per event.
2. **Mean reversion / overreaction.**
   - Attention A_t is high but its transmission to the target source ρ_t ≈ 0 (low breadth, high HHI, repost-heavy).
   - Logit residual r = logit(p) − logit(q). Require a fitted OU process with κ > 0 and a half-life well inside the horizon.
   - Starting thresholds, not to be optimised: entry |z| > 2, partial exit < 1, exit < 0.5.
3. **Stat arb / cross-market.**
   - Logical constraints first:
     - nested count thresholds P(N ≥ K+1) ≤ P(N ≥ K);
     - Fréchet bounds max(0, P(A) + P(B) − 1) ≤ P(A ∩ B) ≤ min(P(A), P(B)).
   - Then state-space regressions of mention logits on related markets (primary event, equity, sibling mentions), with an OU residual and a minimum-variance hedge.
   - Correlation is not arbitrage.
   - Exclude contracts with outcome-control or pre-release (MNPI) risk. The CFTC brought an Aug 2026 enforcement action over presidential mention markets.

## Market categorisation
| Archetype | Best fit | Main failure |
|---|---|---|
| Live speech | Momentum, threshold, cross-strike | Feed latency, adverse selection |
| Earnings call | Cross-market, hazard | Hedge basis, transcript latency |
| TV/podcast/sports broadcast | Point-process momentum | ASR errors, thin books |
| Weekly/monthly public figure | Hawkes, mean reversion | — |
| Viral X/Reddit terms | — | Bots |
| Asset-linked topics | Hedged residual | — |
| Meme/novelty | Usually reject | — |

Scores:
- Velocity/volatility S_V uses robust (MAD) z-scores and within-family percentiles.
- Text→price lead L(τ) must exceed end-to-end latency.
- Information asymmetry S_A uses source lead, breadth and concentration.
- Adverse selection AS_h = E[m_{t+h} − p_fill | our passive fill].
- Event clustering C_E = 1 − normalised entropy of mention timing.
- Executability gate: gross edge / all-in cost ≥ 2, and depth ≥ 4× target size.
- HS score with hard gates on data, execution and compliance. High-Signal needs HS ≥ 70 and the same sign in ≥ 75% of walk-forward folds.
- Want ~50+ independent events before trusting flexible parameters. Effective sample size is counted in events, not posts.

## Backtest mandates
- Knowledge time t_avail = max(local receive, parse, entity-link complete). Execution happens at t_d + latency against the book at that time.
- No forward-fill or interpolation; no common bars before causality is checked.
- Aggressive fills walk the real depth (VWAP) with partial fills. Passive fills need the queue ahead to be consumed; run optimistic, base and conservative queue regimes and judge on base or conservative.
- Fees are date- and contract-specific.
- Capacity curve Π(Q).
- NLP leakage to guard against: backfill, edits, deletions (survivorship), final-transcript vs live caption, alias/dictionary hindsight, rule hindsight.
- Universe: include illiquid and cancelled contracts. Choose terms only from pre-period information.
- CV by event / family / time block with an embargo. Leave-regime-out (e.g. train on speeches, test on debates).
- Report net Sharpe, deflated Sharpe, PBO, max drawdown, capacity, turnover, Brier and log loss. The benchmark is the market's own contemporaneous price.

## How this maps onto prior repo results (as of 2026-10-15)
- **Rejected:**
  - Wikipedia attention beyond price (RELEASE_ATTENTION_MENTIONS);
  - base rates (NOWC);
  - the blind mention NO maker (AUDIT_MENTION_NO_MAKER);
  - the FOMC NB count model (MENT5);
  - the Yes-bias taker (MENT5: the bias is real but shrinking, from −9c to −3c).
- **mkgap:** passive mention fills are informed. A blind maker loses −2.2 to −2.9c per contract under Fb.
- **Count ladders are sparse on Kalshi:** only 26 of 248 count rungs have a sibling.
