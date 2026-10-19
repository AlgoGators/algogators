# Position Sizing, Execution & Strategy Generation: Literature Review

**Date:** February 2026  
**Focus:** Prediction Markets (Polymarket) Trading Research

## Executive Summary

This document synthesizes current research on position sizing, execution quality, and strategy generation for prediction markets, with specific focus on Polymarket trading strategies.

---

## 1. Position Sizing Strategies

### 1.1 Kelly Criterion in Prediction Markets

**Key Finding:** Market prices in prediction markets do not normally match actual probabilities of events occurring. This gap between prices and probabilities is critical for proper position sizing.

**Research Insights:**
- Kelly Criterion maximizes capital growth rate through optimal position sizing
- Uses logarithmic utility and information theory
- **Critical distinction:** Market prices ≠ true probabilities in prediction markets
- Modified payout structures may be needed to reduce price-probability gaps
- Kullback-Leibler divergence measures impact of bias misjudgment

**Practical Application:**
- Use fractional Kelly (25-50% of full Kelly) to reduce volatility
- Adjust for the gap between market prices and subjective probability estimates
- Consider volatility-adjusted Kelly sizing for high-volatility markets

**Reference:** "Application of the Kelly Criterion to Prediction Markets" (arXiv:2412.14144)

### 1.2 Volatility-Adjusted Sizing

**Key Finding:** Optimal position size depends on specific asset price characteristics and market conditions, not standard frameworks.

**Research Insights:**
- Relative position sizing significantly impacts trading results
- Volatility characteristics vary by market type and conditions
- Standard sizing frameworks may not apply directly to prediction markets
- Need to account for bounded price ranges (0-1) in prediction markets

**Practical Application:**
- Scale positions inversely with volatility
- Target portfolio-level volatility (e.g., 15% annualized)
- Adjust for market-specific volatility patterns

**Reference:** "Managing Position Size Depending on Asset Price Characteristics" (SSRN:2913172)

### 1.3 Risk Parity Approaches

**Key Finding:** Risk parity allocates assets to equalize risk contribution rather than equal dollar weights.

**Research Insights:**
- Traditional portfolios (60/40) derive 90% risk from equities
- Risk parity equalizes risk contribution across positions
- Active risk budgeting improves upon standard risk parity
- "Outcome risk parity" extends to include return predictability

**Practical Application:**
- Allocate based on equal risk contribution, not equal capital
- Adjust for mean-reversion vs momentum characteristics
- Consider portfolio heat (total risk exposure) when sizing

**Reference:** "From Risk Parity to Outcome Risk Parity" (SSRN:4444069)

---

## 2. Execution Quality & Slippage

### 2.1 Market Impact Models

**Key Finding:** Market impact follows a 3/5 power law (not square-root) with coefficients depending on volatility, daily volume, and turnover.

**Research Insights:**
- Temporary impact: power law with 3/5 exponent
- Coefficients depend on:
  - Volatility
  - Daily volume
  - Turnover
- Long-term impact: correlated orders create cumulative costs
- Expected Future Flow Shortfall (EFFS) measures cumulative impact

**Practical Application:**
- Use power-law models (not just square-root) for impact estimation
- Account for volatility and volume in impact calculations
- Consider cumulative impact from correlated trades

**Reference:** "Quantifying Long-Term Market Impact" (SSRN:3874261)

### 2.2 Slippage Decomposition

**Key Finding:** Slippage = Market Impact + Market Risk

**Research Insights:**
- Market impact: price movement caused by the order itself
- Market risk: price movement from exogenous factors
- Understanding decomposition is essential for optimization
- RL frameworks can discover optimal execution strategies

**Practical Application:**
- Measure both components separately
- Optimize execution timing to minimize market risk
- Use execution algorithms that balance impact vs risk

**Reference:** Reinforcement Learning for Optimal Execution (arXiv)

### 2.3 Polymarket-Specific Execution Costs

**Key Finding:** Transaction fees are ~2% on profitable outcomes, significantly impacting profitability.

**Research Insights:**
- Account for ~2% transaction fees when calculating position sizes
- Fees significantly impact whether opportunities remain profitable
- Execution windows are often seconds-long
- Fragmented liquidity creates execution challenges

**Practical Application:**
- Factor 2% fees into all profitability calculations
- Optimize for quick execution (seconds, not minutes)
- Monitor liquidity fragmentation
- Use limit orders when possible to reduce slippage

**Reference:** Polymarket Trading Strategies (DataWallet, PolyTrackHQ)

---

## 3. Strategy Generation & Optimization

### 3.1 Genetic Programming for Strategy Generation

**Key Finding:** Genetic programming can automatically generate human-readable trading strategies.

**Research Insights:**
- Modular input/output systems decoupled from GP kernel
- NSGA-II multi-objective selection improves performance and interpretability
- Strategies can be optimized for both returns and risk
- Regularization prevents overfitting

**Practical Application:**
- Use GP to explore strategy space
- Optimize for multiple objectives (Sharpe, returns, drawdown)
- Regularize to prevent overfitting
- Generate interpretable strategies

**Reference:** "Generating and Optimizing Human-Readable Quantitative Program Trading Strategies" (ScienceDirect)

### 3.2 Meta Reinforcement Learning

**Key Finding:** Meta RL with cognitive game theory achieved 46.5-53.7% annualized returns with Sharpe ratios of 2.08-2.45.

**Research Insights:**
- Combines meta reinforcement learning with cognitive game theory
- Strong risk-adjusted returns (Sharpe 2.08-2.45)
- Managed drawdowns (-10.2% to -12.1%)
- Works across global markets

**Practical Application:**
- Consider RL-based strategy optimization
- Incorporate game-theoretic elements
- Focus on risk-adjusted metrics, not just returns
- Test across multiple market regimes

**Reference:** "Adaptive Quantitative Trading Strategy Optimization Framework" (Springer)

### 3.3 Semantic Market Clustering

**Key Finding:** Agentic AI using LLMs can identify relationships between related contracts, achieving 20% average returns with 60-70% relationship prediction accuracy.

**Research Insights:**
- LLMs can cluster markets semantically
- Discover correlated/anti-correlated outcome pairs
- Trading strategies based on relationships show strong returns
- Week-long horizons work well for relationship-based strategies

**Practical Application:**
- Use NLP/semantic similarity to find related markets
- Trade divergences between related markets
- Monitor relationship strength over time
- Combine with price correlation analysis

**Reference:** "Semantic Trading: Agentic AI for Clustering and Relationship Discovery" (arXiv:2512.02436)

### 3.4 Backtest Overfitting Concerns

**Key Finding:** Backtest overfitting is a major concern - calibrating through historical simulation often leads to underperformance.

**Research Insights:**
- Multiple backtest configurations increase overfitting risk
- Optimal trading rules without multiple configurations may help
- Identifying non-overfit solutions remains an open problem
- Need robust validation frameworks

**Practical Application:**
- Use walk-forward validation
- Test on out-of-sample data
- Limit parameter optimization iterations
- Focus on robust strategies, not perfect fits

**Reference:** "Quantitative Finance > Portfolio Management" (arXiv:1408.1159)

---

## 4. Polymarket-Specific Strategies

### 4.1 Arbitrage Opportunities

**Key Finding:** Arbitrage traders extracted over $40M between April 2024-2025, with average margins of 2-3%.

**Strategy Types:**

1. **Binary Market Arbitrage**
   - YES + NO prices should sum to $1.00
   - When they don't, buy both sides for guaranteed profit
   - Example: YES=$0.48, NO=$0.49 → Buy both for $0.97, profit $0.03 (3.09%)

2. **Multi-Outcome Arbitrage**
   - All outcome prices should sum to $1.00
   - Buy all outcomes when sum < $1.00
   - Lock in risk-free profits

3. **Cross-Platform Arbitrage**
   - Exploit price differences between Polymarket and Kalshi
   - Can yield 12.7% returns on capital

4. **Endgame Arbitrage**
   - Buy near-certain outcomes (95-99%) close to resolution
   - High annualized returns, lower per-trade margins

**Performance Data:**
- Profitable users: 0.51% of participants
- One trader: $10K → $100K over 6 months (10,000+ markets)
- Average margins: 2-3%
- Opportunity windows: seconds-long

### 4.2 Volatility Capture Strategy

**Key Finding:** Automated bots can capture volatility spikes for profitable trades.

**Mechanism:**
- Monitor markets for 2+ minute windows
- Trigger when either side drops 15%+ in ~3 seconds
- Hedge by buying opposite side when target cost condition met
- Exploit rapid price movements

**Practical Application:**
- Real-time monitoring required
- Fast execution critical (seconds)
- Need automated systems
- Risk management essential

### 4.3 Market Inefficiency Sources

**Key Finding:** Opportunities stem from fragmented liquidity, emotional trading, and whale accumulation.

**Sources:**
1. Fragmented liquidity across markets
2. Emotional trading during news events
3. Whale position accumulation
4. Information asymmetry
5. Cross-market pricing gaps

**Practical Application:**
- Monitor liquidity fragmentation
- Trade during high-volatility news events
- Track whale positions
- Exploit information delays

---

## 5. Recommendations for Research Script

Based on this literature review, the research script should:

### Position Sizing Enhancements:
1. **Implement fractional Kelly** (25%, 50% variants) ✓ Already done
2. **Add probability-price gap adjustment** - NEW
3. **Implement 3/5 power law impact model** - NEW
4. **Add risk parity sizing** ✓ Already done
5. **Account for 2% Polymarket fees** - ENHANCE

### Execution Analysis Enhancements:
1. **Decompose slippage** into impact vs risk - NEW
2. **Use power-law impact models** (not just square-root) - NEW
3. **Measure execution timing** effects - NEW
4. **Track cumulative impact** from correlated trades - NEW

### Strategy Generation Enhancements:
1. **Add semantic similarity** to market relationship analysis - NEW
2. **Implement walk-forward validation** to prevent overfitting - NEW
3. **Test arbitrage strategies** (binary, multi-outcome) - NEW
4. **Add volatility capture** strategy variants - NEW

### Validation Enhancements:
1. **Out-of-sample testing** - NEW
2. **Multiple time periods** - NEW
3. **Robustness checks** - NEW
4. **Overfitting detection** metrics - NEW

---

## 6. Key Takeaways

1. **Position Sizing:** Kelly Criterion works but must account for price-probability gaps. Volatility and risk parity approaches are valuable.

2. **Execution:** Use power-law impact models (3/5), decompose slippage, account for 2% fees, optimize timing.

3. **Strategy Generation:** Genetic programming, RL, and semantic clustering all show promise. Avoid overfitting through robust validation.

4. **Polymarket Specific:** Arbitrage opportunities are key ($40M+ extracted). Focus on quick execution, exploit inefficiencies, account for fees.

5. **Risk Management:** Always factor in transaction costs, use fractional Kelly, implement proper validation to prevent overfitting.

---

## References

1. "Application of the Kelly Criterion to Prediction Markets" - arXiv:2412.14144
2. "Managing Position Size Depending on Asset Price Characteristics" - SSRN:2913172
3. "From Risk Parity to Outcome Risk Parity" - SSRN:4444069
4. "Quantifying Long-Term Market Impact" - SSRN:3874261
5. "Generating and Optimizing Human-Readable Quantitative Program Trading Strategies" - ScienceDirect
6. "Adaptive Quantitative Trading Strategy Optimization Framework" - Springer
7. "Semantic Trading: Agentic AI for Clustering and Relationship Discovery" - arXiv:2512.02436
8. Polymarket Trading Strategies - DataWallet, PolyTrackHQ
9. "Understanding Market Impact in Crypto Trading" - Talos
