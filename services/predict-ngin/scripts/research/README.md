# Research Scripts

Scripts for long-running research tasks that can run overnight or in the background.
All scripts feature checkpointing, low resource usage, and resumability.

## 1. Market Similarity Computation

**Script:** `compute_market_similarities.py`

Computes pairwise NLP similarities between all markets based on their questions/text.
Finds markets that are semantically similar.

**Use cases:**
- Market relationship analysis
- Finding semantically related markets
- NLP correlation strategy research
- Market clustering and grouping

**Usage:**
```bash
python scripts/research/compute_market_similarities.py
python scripts/research/compute_market_similarities.py --max-markets 500 --method tfidf
python scripts/research/compute_market_similarities.py --resume
```

**Output:** `data/research/similarities/market_similarities.parquet`

---

## 2. Price Correlation Computation

**Script:** `compute_price_correlations.py`

Computes pairwise price correlations between all markets based on actual price movements.
Finds markets that move together in price.

**Use cases:**
- Finding markets with correlated price movements
- Cross-market arbitrage opportunities
- Market relationship analysis (complements NLP similarity)
- Risk management (diversification)

**Usage:**
```bash
python scripts/research/compute_price_correlations.py
python scripts/research/compute_price_correlations.py --max-markets 500
python scripts/research/compute_price_correlations.py --resume --min-correlation 0.2
```

**Output:** `data/research/correlations/price_correlations.parquet`

---

## 3. Market Feature Extraction

**Script:** `extract_market_features.py`

Extracts comprehensive features for all markets from price and trade data.
Creates a feature matrix useful for ML models and analysis.

**Features extracted:**
- Price statistics (volatility, returns, trends)
- Volume patterns (daily volume, volume trends)
- Liquidity metrics
- Time-based patterns
- Market lifecycle (days to resolution)

**Use cases:**
- Building ML models for market prediction
- Market classification and clustering
- Feature engineering for strategies
- Market analysis and research

**Usage:**
```bash
python scripts/research/extract_market_features.py
python scripts/research/extract_market_features.py --max-markets 500
python scripts/research/extract_market_features.py --resume
```

**Output:** `data/research/features/market_features.parquet`

---

## General Tips

### Resource Usage
All scripts are designed for low-resource overnight processing:
- **Memory**: 100-500 MB (depending on batch size)
- **CPU**: Single-threaded, I/O bound
- **Disk**: Checkpoints saved incrementally

### Checkpointing
All scripts support checkpointing:
- Progress saved automatically
- Resume with `--resume` flag
- Checkpoint files in `data/research/checkpoints/`
- Delete checkpoint files to start fresh

### Performance Estimates

**Similarity/Correlation (N markets):**
- 1,000 markets ≈ 500K comparisons (few hours)
- 5,000 markets ≈ 12.5M comparisons (overnight)
- 10,000 markets ≈ 50M comparisons (multiple nights)

**Feature Extraction:**
- ~100-500 markets/hour (depends on data availability)
- Scales linearly with number of markets

### Best Practices

1. **Start small**: Test with `--max-markets 500` first
2. **Monitor progress**: Check checkpoint files periodically
3. **Resume capability**: All scripts can resume if interrupted
4. **Combine outputs**: Use similarity + correlation + features together for comprehensive analysis
5. **Run sequentially**: Run one script at a time to avoid resource contention

---

## 4. Position Sizing & Execution Research

**Script:** `position_sizing_execution_research.py`

Comprehensive research on position sizing methods, execution quality, and strategy optimization.
Runs for a few hours testing multiple configurations.

**Research Areas:**
- Position sizing methods (Fixed, Kelly, Volatility-adjusted, Liquidity-based, Risk Parity, Composite)
- Execution analysis (slippage, fill rates, market impact)
- Strategy optimization (parameter tuning, ensemble methods)

**Usage:**
```bash
# Full research run (few hours)
python scripts/research/position_sizing_execution_research.py

# Test with limited markets
python scripts/research/position_sizing_execution_research.py --max-markets 500

# Resume from checkpoint
python scripts/research/position_sizing_execution_research.py --resume

# Limit number of tests
python scripts/research/position_sizing_execution_research.py --n-tests 50
```

**Output:** `data/research/position_sizing/position_sizing_results.parquet`

**Performance:**
- **Time**: 2-4 hours depending on number of tests
- **Memory**: ~500 MB - 1 GB
- **CPU**: Moderate (backtesting computations)
