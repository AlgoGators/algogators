"""
Cross-Market Strategy

Exploits relationships between related markets.

Hypothesis:
- Related markets should move together
- Divergence between related markets creates arbitrage
- Lead-lag relationships exist between markets

Signal Types:
- Pair divergence: Related markets diverge
- Correlation breakdown: Historical correlation breaks
- Lead-lag: One market leads another

Examples:
- "Trump wins" vs "Republican wins" should be correlated
- Related event outcomes (e.g., different deadline questions)
- Conditional markets (A given B)
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional, Tuple, Set
from datetime import datetime, timedelta
from dataclasses import dataclass
from collections import defaultdict
import re


@dataclass
class CrossMarketSignal:
    """Signal from cross-market analysis."""
    timestamp: datetime
    market_id_long: str  # Buy this
    market_id_short: str  # Sell this
    direction: int  # 1 = long market_id_long, -1 = short market_id_long
    confidence: float
    signal_type: str  # "pair_divergence", "correlation_break", "lead_lag"
    spread: float  # Price difference
    historical_spread: float
    z_score: float  # Standardized divergence
    correlation: float


class MarketRelationshipDetector:
    """
    Detects relationships between markets.

    Uses text similarity and price correlation to find related markets.
    """

    def __init__(
        self,
        min_correlation: float = 0.5,
        min_text_similarity: float = 0.3,
    ):
        self.min_correlation = min_correlation
        self.min_text_similarity = min_text_similarity
        self.relationships: Dict[str, Set[str]] = defaultdict(set)

    def extract_keywords(self, text: str) -> Set[str]:
        """Extract keywords from market question."""
        # Simple keyword extraction
        text = text.lower()
        # Remove common words
        stopwords = {
            "will", "the", "be", "to", "in", "on", "at", "by", "for",
            "a", "an", "is", "of", "and", "or", "with", "this", "that",
            "before", "after", "during", "when", "what", "who", "how",
            "yes", "no", "market", "price", "question"
        }

        words = re.findall(r'\b\w+\b', text)
        keywords = {w for w in words if len(w) > 2 and w not in stopwords}

        return keywords

    def text_similarity(self, text1: str, text2: str) -> float:
        """Calculate Jaccard similarity between market questions."""
        kw1 = self.extract_keywords(text1)
        kw2 = self.extract_keywords(text2)

        if not kw1 or not kw2:
            return 0

        intersection = len(kw1 & kw2)
        union = len(kw1 | kw2)

        return intersection / union if union > 0 else 0

    def find_related_markets(
        self,
        markets_df: pd.DataFrame,
        prices_df: pd.DataFrame,
    ) -> Dict[str, List[Tuple[str, float]]]:
        """
        Find related markets based on text and price correlation.

        Args:
            markets_df: DataFrame with market_id, question
            prices_df: DataFrame with market_id, datetime, price

        Returns:
            Dict of market_id -> list of (related_id, similarity_score)
        """
        relationships = defaultdict(list)

        # Create market question lookup
        questions = dict(zip(markets_df["id"], markets_df["question"]))

        # Get price series for each market
        price_series = {}
        for market_id, group in prices_df.groupby("market_id"):
            group = group.sort_values("datetime")
            series = group.set_index("datetime")["price"].resample("1h").last().dropna()
            if len(series) > 24:  # Need enough data
                price_series[market_id] = series

        market_ids = list(price_series.keys())

        # Compare all pairs
        for i, id1 in enumerate(market_ids):
            for id2 in market_ids[i + 1:]:
                # Text similarity
                text_sim = 0
                if id1 in questions and id2 in questions:
                    text_sim = self.text_similarity(questions[id1], questions[id2])

                # Price correlation
                s1 = price_series[id1]
                s2 = price_series[id2]

                # Align series
                aligned = pd.concat([s1, s2], axis=1, join="inner")
                if len(aligned) < 24:
                    continue

                correlation = aligned.corr().iloc[0, 1]

                # Combined score
                if text_sim >= self.min_text_similarity or correlation >= self.min_correlation:
                    score = 0.4 * text_sim + 0.6 * abs(correlation)
                    relationships[id1].append((id2, score, correlation))
                    relationships[id2].append((id1, score, correlation))

        # Sort by score
        for market_id in relationships:
            relationships[market_id].sort(key=lambda x: x[1], reverse=True)

        return relationships


class CrossMarketStrategy:
    """
    Cross-Market Arbitrage Strategy.

    Trades divergences between related markets.
    """

    def __init__(
        self,
        z_score_threshold: float = 2.0,
        lookback_hours: int = 72,
        min_spread_pct: float = 0.05,
        max_spread_pct: float = 0.30,
    ):
        """
        Initialize strategy.

        Args:
            z_score_threshold: Std devs from mean spread to signal
            lookback_hours: Hours for historical spread calculation
            min_spread_pct: Minimum spread to trade
            max_spread_pct: Maximum spread (avoid broken relationships)
        """
        self.z_score_threshold = z_score_threshold
        self.lookback_hours = lookback_hours
        self.min_spread_pct = min_spread_pct
        self.max_spread_pct = max_spread_pct
        self.name = "cross_market"

        self.relationship_detector = MarketRelationshipDetector()

    def calculate_spread(
        self,
        prices1: pd.Series,
        prices2: pd.Series,
        correlation: float,
    ) -> pd.Series:
        """
        Calculate spread between two price series.

        For positive correlation: spread = p1 - p2
        For negative correlation: spread = p1 + p2 - 1
        """
        if correlation > 0:
            return prices1 - prices2
        else:
            return prices1 + prices2 - 1

    def analyze_pair(
        self,
        market_id1: str,
        market_id2: str,
        prices_df: pd.DataFrame,
        correlation: float,
        as_of: datetime,
    ) -> Optional[CrossMarketSignal]:
        """
        Analyze a market pair for divergence signals.

        Args:
            market_id1: First market
            market_id2: Second market
            prices_df: Price data
            correlation: Known correlation between markets
            as_of: Current time

        Returns:
            Signal or None
        """
        window_start = as_of - timedelta(hours=self.lookback_hours)

        # Get price series
        p1 = prices_df[prices_df["market_id"] == market_id1]
        p2 = prices_df[prices_df["market_id"] == market_id2]

        p1 = p1[p1["datetime"] <= as_of].sort_values("datetime")
        p2 = p2[p2["datetime"] <= as_of].sort_values("datetime")

        if p1.empty or p2.empty:
            return None

        # Get current prices
        current_price1 = p1.iloc[-1]["price"]
        current_price2 = p2.iloc[-1]["price"]

        # Calculate current spread
        if correlation > 0:
            current_spread = current_price1 - current_price2
        else:
            current_spread = current_price1 + current_price2 - 1

        abs_spread = abs(current_spread)

        # Check spread bounds
        if abs_spread < self.min_spread_pct:
            return None
        if abs_spread > self.max_spread_pct:
            return None

        # Get historical spread
        p1_hist = p1[p1["datetime"] >= window_start].set_index("datetime")["price"]
        p2_hist = p2[p2["datetime"] >= window_start].set_index("datetime")["price"]

        # Align and calculate spread history
        aligned = pd.concat([p1_hist, p2_hist], axis=1, join="inner")
        aligned.columns = ["p1", "p2"]

        if len(aligned) < 24:
            return None

        if correlation > 0:
            spread_history = aligned["p1"] - aligned["p2"]
        else:
            spread_history = aligned["p1"] + aligned["p2"] - 1

        # Calculate z-score
        spread_mean = spread_history.mean()
        spread_std = spread_history.std()

        if spread_std == 0:
            return None

        z_score = (current_spread - spread_mean) / spread_std

        # Check for signal
        if abs(z_score) >= self.z_score_threshold:
            # Trade direction: bet on convergence
            if z_score > 0:
                # Spread too wide, expect it to narrow
                # If p1 > p2 more than usual: sell p1, buy p2
                direction = -1
                long_market = market_id2
                short_market = market_id1
            else:
                # Spread too narrow (or negative), expect it to widen
                # If p1 < p2 more than usual: buy p1, sell p2
                direction = 1
                long_market = market_id1
                short_market = market_id2

            confidence = min(abs(z_score) / 4, 0.9)

            return CrossMarketSignal(
                timestamp=as_of,
                market_id_long=long_market,
                market_id_short=short_market,
                direction=direction,
                confidence=confidence,
                signal_type="pair_divergence",
                spread=current_spread,
                historical_spread=spread_mean,
                z_score=z_score,
                correlation=correlation,
            )

        return None

    def generate_signals(
        self,
        markets_df: pd.DataFrame,
        prices_df: pd.DataFrame,
        relationships: Optional[Dict] = None,
        timestamps: Optional[List[datetime]] = None,
    ) -> List[CrossMarketSignal]:
        """
        Generate cross-market signals.

        Args:
            markets_df: Market metadata
            prices_df: Price data
            relationships: Pre-computed relationships (or will compute)
            timestamps: Times to check (default: hourly)

        Returns:
            List of signals
        """
        # Find relationships if not provided
        if relationships is None:
            relationships = self.relationship_detector.find_related_markets(
                markets_df, prices_df
            )

        signals = []

        # Default timestamps
        if timestamps is None:
            start = prices_df["datetime"].min() + timedelta(hours=self.lookback_hours)
            end = prices_df["datetime"].max()
            timestamps = pd.date_range(start, end, freq="6H").tolist()

        # Track checked pairs to avoid duplicates
        checked_pairs = set()

        for ts in timestamps:
            for market_id1, related_list in relationships.items():
                for market_id2, score, correlation in related_list[:3]:  # Top 3 related
                    pair_key = tuple(sorted([market_id1, market_id2]))
                    if pair_key in checked_pairs:
                        continue

                    signal = self.analyze_pair(
                        market_id1,
                        market_id2,
                        prices_df,
                        correlation,
                        ts,
                    )

                    if signal:
                        signals.append(signal)
                        checked_pairs.add(pair_key)

        return signals

    def get_parameters(self) -> Dict[str, Any]:
        """Get strategy parameters."""
        return {
            "z_score_threshold": self.z_score_threshold,
            "lookback_hours": self.lookback_hours,
            "min_spread_pct": self.min_spread_pct,
            "max_spread_pct": self.max_spread_pct,
        }
