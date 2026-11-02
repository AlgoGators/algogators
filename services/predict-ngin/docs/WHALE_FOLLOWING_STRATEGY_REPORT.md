# Whale-Following Strategy (Defaults)

## 1. Whale Identification

**Rolling, no look-ahead:** At trade T, whale status uses only trades before T.

**Volume whale:** Trader in top 95th percentile by USD volume in that market.
$$\text{whale} \Leftrightarrow V_{\text{trader}} \geq P_{95}(V_{\text{market}})$$

**Performance filter:** WR ≥ 50% and positive surprise on resolved trades.
$$\text{surprise} = \text{actual WR} - \text{expected WR}$$
- Expected: BUY YES @ $p$ → $p$; SELL NO @ $p$ → $1-p$
- Actual: from resolution outcomes

**Monthly rebalancing:** Whale set recomputed each month from data through end of previous month.

---

## 2. Whale Score

$$W = 0.50 \cdot S_{\text{surprise}} + 0.30 \cdot S_{\text{roi}} + 0.20 \cdot S_{\text{sharpe}}$$

- $S_{\text{surprise}} = \min(\max(\frac{\text{surprise}}{0.15} \cdot 5 + 5,\, 0),\, 10)$
- $S_{\text{roi}} = \min(\overline{\text{ROI}} \cdot 10,\, 10)$
- $S_{\text{sharpe}} = \min(\max(\text{Sharpe},\, 0) \cdot 2,\, 10)$

**Tiers:** TIER_1 (W≥8): 40% cap, \$50k max | TIER_2 (7≤W<8): 35%, \$30k | TIER_3 (6≤W<7): 15%, \$20k. Min score 6.0 to trade.

---

## 3. Position Sizing

**Win probability:**
$$p = \frac{W}{10} \cdot p_{\text{whale}} + \left(1 - \frac{W}{10}\right) \cdot p_{\text{market}}$$

**Fractional Kelly (¼):**
$$f^* = 0.25 \cdot \frac{p \cdot b - q}{b}, \quad q = 1 - p$$
- BUY YES: $b = \frac{1 - \text{price}}{\text{price}}$
- SELL NO: $b = \frac{\text{price}}{1 - \text{price}}$

**Size:** $\text{size} = \min(f^* \cdot \text{available},\, \text{caps})$

**Caps:** \$5k–\$50k, 3% of market liquidity, tier max.

---

## 4. Risk Limits

| Limit | Value |
|-------|-------|
| Max position | 5% capital |
| Max whale | 20% capital |
| Max market | 8% capital |
| Max category | 30% capital |
| Reserve | 10% |

$$\text{available} = \text{capital} - \text{deployed} - 0.10 \cdot \text{capital}$$

**Enforcement:** Category ≥30% → size ×0.5; ≥36% → skip. Whale ≥20% → size ×0.5. Market ≥8% → skip. Tier over target ×1.1 → scale down.

---

## 5. Signal & Exit

**Signal filters:** Min \$1k trade, min \$10k liquidity, score ≥6, time to resolution ≤90 days.

**Exits:** Resolution → close at 0/1. No resolution → close at CLOB. Conflicting signal → close and flip (last signal wins).

**PnL:** BUY: $(P_{\text{exit}} - P_{\text{entry}}) \cdot \frac{\text{size}}{P_{\text{entry}}}$. SELL: $(P_{\text{entry}} - P_{\text{exit}}) \cdot \frac{\text{size}}{P_{\text{entry}}}$. Net = gross × 0.97.
