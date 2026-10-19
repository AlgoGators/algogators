"""
NLP-Based Correlation Strategy

Uses NLP techniques (bag-of-words, TF-IDF, optional LLM embeddings) to compute
semantic similarity between market definitions, then trades divergences between
highly correlated markets.

Hypothesis:
- Markets with similar questions/definitions should move together
- Semantic similarity is better than simple keyword matching
- Divergences between semantically similar markets create arbitrage opportunities

Signal Types:
- Semantic divergence: Related markets diverge based on NLP similarity
- Correlation breakdown: Historical correlation breaks for semantically similar markets
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple, Set
import logging

import pandas as pd
import numpy as np

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

from .base import BaseStrategy, StrategyConfig, Signal, SignalType

# Import trade-to-price conversion utility
try:
    from ..momentum_signals_from_trades import trades_to_price_history
    TRADES_TO_PRICE_AVAILABLE = True
except ImportError:
    TRADES_TO_PRICE_AVAILABLE = False


logger = logging.getLogger(__name__)


@dataclass
class MarketSimilarity:
    """Market similarity result."""
    market_id1: str
    market_id2: str
    similarity_score: float  # 0-1
    method: str  # "bag_of_words", "tfidf", "embedding", "hybrid"
    text_similarity: float
    price_correlation: Optional[float] = None
    combined_score: Optional[float] = None


class NLPSimilarityEngine:
    """
    NLP-based similarity engine for market definitions.
    
    Supports multiple methods: bag-of-words, TF-IDF, embeddings (optional).
    """

    def __init__(
        self,
        method: str = "hybrid",  # "bag_of_words", "tfidf", "embedding", "hybrid"
        min_similarity: float = 0.3,
        use_price_correlation: bool = True,
        price_correlation_weight: float = 0.3,
    ):
        """
        Initialize similarity engine.
        
        Args:
            method: Similarity method to use
            min_similarity: Minimum similarity threshold
            use_price_correlation: Whether to combine with price correlation
            price_correlation_weight: Weight for price correlation in hybrid score
        """
        self.method = method
        self.min_similarity = min_similarity
        self.use_price_correlation = use_price_correlation
        self.price_correlation_weight = price_correlation_weight
        
        # Initialize TF-IDF if available
        self.tfidf_vectorizer = None
        if method in ["tfidf", "hybrid"] and SKLEARN_AVAILABLE:
            self.tfidf_vectorizer = TfidfVectorizer(
                max_features=5000,
                stop_words="english",
                ngram_range=(1, 2),  # Unigrams and bigrams
                min_df=2,  # Minimum document frequency
            )
        
        # Initialize sentence transformer if available
        self.sentence_model = None
        if method in ["embedding", "hybrid"] and SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                self.sentence_model = SentenceTransformer('all-MiniLM-L6-v2')
                logger.info("Loaded sentence transformer model")
            except Exception as e:
                logger.warning(f"Could not load sentence transformer: {e}")
                self.sentence_model = None
        
        # Cache for embeddings
        self._embedding_cache: Dict[str, np.ndarray] = {}
        self._tfidf_cache: Optional[np.ndarray] = None
        self._market_texts: Dict[str, str] = {}
    
    def _preprocess_text(self, text: str) -> str:
        """Preprocess market question text."""
        if not text:
            return ""
        
        # Lowercase
        text = text.lower()
        
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text
    
    def _bag_of_words_similarity(
        self,
        text1: str,
        text2: str,
    ) -> float:
        """
        Calculate bag-of-words similarity using Jaccard.
        
        Simple but effective for keyword matching.
        """
        # Extract words
        words1 = set(re.findall(r'\b\w+\b', text1.lower()))
        words2 = set(re.findall(r'\b\w+\b', text2.lower()))
        
        # Remove stopwords
        stopwords = {
            "will", "the", "be", "to", "in", "on", "at", "by", "for",
            "a", "an", "is", "of", "and", "or", "with", "this", "that",
            "before", "after", "during", "when", "what", "who", "how",
            "yes", "no", "market", "price", "question", "does", "do",
        }
        words1 = {w for w in words1 if len(w) > 2 and w not in stopwords}
        words2 = {w for w in words2 if len(w) > 2 and w not in stopwords}
        
        if not words1 or not words2:
            return 0.0
        
        # Jaccard similarity
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        return intersection / union if union > 0 else 0.0
    
    def _tfidf_similarity(
        self,
        texts: List[str],
        idx1: int,
        idx2: int,
    ) -> float:
        """
        Calculate TF-IDF cosine similarity.
        
        Better than bag-of-words for semantic similarity.
        """
        if not SKLEARN_AVAILABLE:
            return 0.0
        
        if self._tfidf_cache is None:
            # Fit and transform all texts
            try:
                tfidf_matrix = self.tfidf_vectorizer.fit_transform(texts)
                self._tfidf_cache = tfidf_matrix
            except Exception as e:
                logger.warning(f"TF-IDF failed: {e}")
                return 0.0
        
        # Get vectors for the two texts
        vec1 = self._tfidf_cache[idx1:idx1+1]
        vec2 = self._tfidf_cache[idx2:idx2+1]
        
        # Cosine similarity
        similarity = cosine_similarity(vec1, vec2)[0, 0]
        
        return max(0.0, float(similarity))
    
    def _embedding_similarity(
        self,
        text1: str,
        text2: str,
    ) -> float:
        """
        Calculate embedding-based similarity.
        
        Uses sentence transformers for semantic similarity.
        """
        if not self.sentence_model:
            return 0.0
        
        # Check cache
        if text1 in self._embedding_cache:
            emb1 = self._embedding_cache[text1]
        else:
            emb1 = self.sentence_model.encode(text1, convert_to_numpy=True)
            self._embedding_cache[text1] = emb1
        
        if text2 in self._embedding_cache:
            emb2 = self._embedding_cache[text2]
        else:
            emb2 = self.sentence_model.encode(text2, convert_to_numpy=True)
            self._embedding_cache[text2] = emb2
        
        # Cosine similarity
        similarity = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))
        
        return max(0.0, float(similarity))
    
    def compute_similarity(
        self,
        text1: str,
        text2: str,
    ) -> float:
        """
        Compute similarity between two market texts.
        
        Args:
            text1: First market question/definition
            text2: Second market question/definition
            
        Returns:
            Similarity score 0-1
        """
        text1 = self._preprocess_text(text1)
        text2 = self._preprocess_text(text2)
        
        if not text1 or not text2:
            return 0.0
        
        if self.method == "bag_of_words":
            return self._bag_of_words_similarity(text1, text2)
        elif self.method == "tfidf":
            # For TF-IDF, we need all texts - use bag-of-words as fallback
            return self._bag_of_words_similarity(text1, text2)
        elif self.method == "embedding":
            return self._embedding_similarity(text1, text2)
        elif self.method == "hybrid":
            # Combine multiple methods
            bow_sim = self._bag_of_words_similarity(text1, text2)
            
            # Try embedding if available
            emb_sim = 0.0
            if self.sentence_model:
                emb_sim = self._embedding_similarity(text1, text2)
            
            # Weighted combination
            if emb_sim > 0:
                return 0.4 * bow_sim + 0.6 * emb_sim
            else:
                return bow_sim
        else:
            return self._bag_of_words_similarity(text1, text2)
    
    def compute_batch_similarity(
        self,
        markets_df: pd.DataFrame,
        question_col: str = "question",
        market_id_col: str = "id",
        batch_size: int = 100,
        max_comparisons: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Compute pairwise similarities for all markets (memory-efficient).
        
        Args:
            markets_df: DataFrame with market data
            question_col: Column with market questions
            market_id_col: Column with market IDs
            batch_size: Process markets in batches to reduce memory
            max_comparisons: Maximum number of comparisons (None = all)
            
        Returns:
            DataFrame with market_id1, market_id2, similarity_score
        """
        # Limit markets if too many (memory protection)
        n_markets = len(markets_df)
        if max_comparisons is None:
            # Auto-limit: for n markets, comparisons = n*(n-1)/2
            # Limit to ~10M comparisons max (about 4500 markets)
            max_markets = int(np.sqrt(20_000_000))  # ~4472 markets
            if n_markets > max_markets:
                logger.warning(f"Too many markets ({n_markets}), limiting to {max_markets} for memory efficiency")
                markets_df = markets_df.head(max_markets)
                n_markets = max_markets
        
        # Extract texts
        texts = []
        market_ids = []
        self._market_texts = {}
        
        for _, row in markets_df.iterrows():
            market_id = str(row[market_id_col])
            question = str(row.get(question_col, ""))
            processed = self._preprocess_text(question)
            texts.append(processed)
            market_ids.append(market_id)
            self._market_texts[market_id] = processed
        
        # Clear embedding cache if too large
        if len(self._embedding_cache) > 10000:
            logger.info("Clearing embedding cache to free memory")
            self._embedding_cache.clear()
        
        # Compute similarities
        similarities = []
        total_comparisons = n_markets * (n_markets - 1) // 2
        
        if max_comparisons and total_comparisons > max_comparisons:
            logger.warning(f"Limiting comparisons from {total_comparisons:,} to {max_comparisons:,}")
            # Use sampling instead of all pairs
            import random
            random.seed(42)
            pairs_to_check = set()
            while len(pairs_to_check) < max_comparisons and len(pairs_to_check) < total_comparisons:
                i = random.randint(0, n_markets - 1)
                j = random.randint(0, n_markets - 1)
                if i != j:
                    pairs_to_check.add((min(i, j), max(i, j)))
            
            logger.info(f"Checking {len(pairs_to_check):,} random pairs")
            for i, j in pairs_to_check:
                sim = self.compute_similarity(texts[i], texts[j])
                if sim >= self.min_similarity:
                    similarities.append({
                        "market_id1": market_ids[i],
                        "market_id2": market_ids[j],
                        "similarity_score": sim,
                        "method": self.method,
                    })
            
            return pd.DataFrame(similarities)
        
        if self.method == "tfidf" and SKLEARN_AVAILABLE and self.tfidf_vectorizer:
            # Use TF-IDF for batch (memory-efficient with sparse matrices)
            try:
                # Fit TF-IDF
                tfidf_matrix = self.tfidf_vectorizer.fit_transform(texts)
                self._tfidf_cache = tfidf_matrix
                
                # Process in batches to avoid large similarity matrix
                logger.info(f"Computing TF-IDF similarities for {n_markets} markets in batches of {batch_size}")
                
                for i_start in range(0, n_markets, batch_size):
                    i_end = min(i_start + batch_size, n_markets)
                    
                    # Compute similarities for this batch against all others
                    batch_matrix = tfidf_matrix[i_start:i_end]
                    batch_similarities = cosine_similarity(batch_matrix, tfidf_matrix)
                    
                    for i_idx, i in enumerate(range(i_start, i_end)):
                        for j in range(i + 1, n_markets):
                            sim = float(batch_similarities[i_idx, j])
                            if sim >= self.min_similarity:
                                similarities.append({
                                    "market_id1": market_ids[i],
                                    "market_id2": market_ids[j],
                                    "similarity_score": sim,
                                    "method": "tfidf",
                                })
                    
                    # Clear batch matrix to free memory
                    del batch_matrix, batch_similarities
                    
                    if (i_start // batch_size) % 10 == 0:
                        logger.info(f"  Processed {i_end}/{n_markets} markets, found {len(similarities)} pairs")
                
                # Clear TF-IDF cache after use
                self._tfidf_cache = None
                
            except MemoryError as e:
                logger.warning(f"TF-IDF memory error: {e}, falling back to bag-of-words")
                # Fallback to bag-of-words (more memory efficient)
                for i in range(n_markets):
                    if i % 100 == 0:
                        logger.info(f"  Processing {i}/{n_markets} markets...")
                    for j in range(i + 1, n_markets):
                        sim = self._bag_of_words_similarity(texts[i], texts[j])
                        if sim >= self.min_similarity:
                            similarities.append({
                                "market_id1": market_ids[i],
                                "market_id2": market_ids[j],
                                "similarity_score": sim,
                                "method": "bag_of_words",
                            })
            except Exception as e:
                logger.warning(f"TF-IDF batch failed: {e}, falling back to pairwise")
                # Fallback to pairwise
                for i in range(n_markets):
                    if i % 100 == 0:
                        logger.info(f"  Processing {i}/{n_markets} markets...")
                    for j in range(i + 1, n_markets):
                        sim = self.compute_similarity(texts[i], texts[j])
                        if sim >= self.min_similarity:
                            similarities.append({
                                "market_id1": market_ids[i],
                                "market_id2": market_ids[j],
                                "similarity_score": sim,
                                "method": self.method,
                            })
        else:
            # Pairwise computation with progress logging
            logger.info(f"Computing pairwise similarities for {n_markets} markets...")
            for i in range(n_markets):
                if i % 100 == 0:
                    logger.info(f"  Processing {i}/{n_markets} markets, found {len(similarities)} pairs so far...")
                for j in range(i + 1, n_markets):
                    sim = self.compute_similarity(texts[i], texts[j])
                    if sim >= self.min_similarity:
                        similarities.append({
                            "market_id1": market_ids[i],
                            "market_id2": market_ids[j],
                            "similarity_score": sim,
                            "method": self.method,
                        })
        
        logger.info(f"Found {len(similarities)} similar market pairs")
        return pd.DataFrame(similarities)
    
    def add_price_correlation(
        self,
        similarity_df: pd.DataFrame,
        prices_df: Optional[pd.DataFrame] = None,
        trades_df: Optional[pd.DataFrame] = None,
        min_overlap_hours: int = 24,
    ) -> pd.DataFrame:
        """
        Add price correlation to similarity DataFrame.
        
        Args:
            similarity_df: DataFrame with market pairs and similarity scores
            prices_df: DataFrame with market_id, datetime, price (optional)
            trades_df: DataFrame with trades (optional, used if prices_df not provided)
            min_overlap_hours: Minimum hours of overlap required
            
        Returns:
            DataFrame with added price_correlation column
        """
        similarity_df = similarity_df.copy()
        similarity_df["price_correlation"] = None
        
        # Convert trades to prices if needed
        if prices_df is None or prices_df.empty:
            if trades_df is not None and not trades_df.empty and TRADES_TO_PRICE_AVAILABLE:
                prices_df = trades_to_price_history(trades_df, outcome="YES")
            else:
                # No price data available
                similarity_df["combined_score"] = similarity_df["similarity_score"]
                return similarity_df
        
        # Get price series for each market
        price_series = {}
        for market_id, group in prices_df.groupby("market_id"):
            group = group.sort_values("datetime")
            series = group.set_index("datetime")["price"].resample("1h").last().dropna()
            if len(series) >= min_overlap_hours:
                price_series[market_id] = series
        
        correlations = []
        for _, row in similarity_df.iterrows():
            market_id1 = row["market_id1"]
            market_id2 = row["market_id2"]
            
            if market_id1 in price_series and market_id2 in price_series:
                s1 = price_series[market_id1]
                s2 = price_series[market_id2]
                
                # Align series
                aligned = pd.concat([s1, s2], axis=1, join="inner")
                aligned.columns = ["p1", "p2"]
                
                if len(aligned) >= min_overlap_hours:
                    corr = aligned.corr().iloc[0, 1]
                    correlations.append(float(corr))
                else:
                    correlations.append(None)
            else:
                correlations.append(None)
        
        similarity_df["price_correlation"] = correlations
        
        # Compute combined score if requested
        if self.use_price_correlation:
            def compute_combined(row):
                text_sim = row["similarity_score"]
                price_corr = row.get("price_correlation")
                
                if price_corr is None or np.isnan(price_corr):
                    return text_sim
                
                # Weighted combination
                return (
                    (1 - self.price_correlation_weight) * text_sim +
                    self.price_correlation_weight * abs(price_corr)
                )
            
            similarity_df["combined_score"] = similarity_df.apply(compute_combined, axis=1)
        else:
            similarity_df["combined_score"] = similarity_df["similarity_score"]
        
        return similarity_df


class NLPCorrelationStrategy(BaseStrategy):
    """
    NLP-based correlation trading strategy.
    
    Uses semantic similarity to find related markets and trade divergences.
    """
    
    def __init__(
        self,
        config: Optional[StrategyConfig] = None,
        similarity_method: str = "hybrid",
        min_similarity: float = 0.4,
        z_score_threshold: float = 2.0,
        lookback_hours: int = 72,
        min_spread_pct: float = 0.05,
        max_spread_pct: float = 0.30,
        use_price_correlation: bool = True,
        price_correlation_weight: float = 0.3,
        max_pairs_per_market: int = 5,
        max_markets_for_similarity: int = 1000,
        similarity_batch_size: int = 100,
        max_similarity_comparisons: Optional[int] = None,
    ):
        """
        Initialize NLP correlation strategy.
        
        Args:
            config: Strategy configuration
            similarity_method: Method for computing similarity
            min_similarity: Minimum similarity to consider markets related
            z_score_threshold: Std devs from mean spread to signal
            lookback_hours: Hours for historical spread calculation
            min_spread_pct: Minimum spread to trade
            max_spread_pct: Maximum spread (avoid broken relationships)
            use_price_correlation: Whether to combine with price correlation
            price_correlation_weight: Weight for price correlation
            max_pairs_per_market: Maximum pairs to consider per market
            max_markets_for_similarity: Maximum markets to process for similarity (memory limit)
            similarity_batch_size: Batch size for similarity computation
            max_similarity_comparisons: Maximum comparisons (None = all, use for memory limit)
        """
        parameters = {
            "similarity_method": similarity_method,
            "min_similarity": min_similarity,
            "z_score_threshold": z_score_threshold,
            "lookback_hours": lookback_hours,
            "min_spread_pct": min_spread_pct,
            "max_spread_pct": max_spread_pct,
            "use_price_correlation": use_price_correlation,
            "price_correlation_weight": price_correlation_weight,
            "max_pairs_per_market": max_pairs_per_market,
            "max_markets_for_similarity": max_markets_for_similarity,
            "similarity_batch_size": similarity_batch_size,
            "max_similarity_comparisons": max_similarity_comparisons,
        }
        
        super().__init__(
            config or StrategyConfig(name="nlp_correlation", parameters=parameters)
        )
        
        self.similarity_engine = NLPSimilarityEngine(
            method=similarity_method,
            min_similarity=min_similarity,
            use_price_correlation=use_price_correlation,
            price_correlation_weight=price_correlation_weight,
        )
        
        # Cache for market relationships
        self._relationships: Optional[Dict[str, List[Tuple[str, float, float]]]] = None
        self._last_markets_hash: Optional[str] = None
    
    def clear_caches(self):
        """Clear all caches to free memory."""
        self._relationships = None
        self._last_markets_hash = None
        if hasattr(self.similarity_engine, '_embedding_cache'):
            self.similarity_engine._embedding_cache.clear()
        if hasattr(self.similarity_engine, '_tfidf_cache'):
            self.similarity_engine._tfidf_cache = None
        if hasattr(self.similarity_engine, '_market_texts'):
            self.similarity_engine._market_texts.clear()
        logger.info("Cleared all caches")
    
    def _hash_markets(self, markets_df: pd.DataFrame) -> str:
        """Create hash of markets for caching."""
        import hashlib
        market_ids = sorted(markets_df["id"].astype(str).tolist())
        return hashlib.md5("|".join(market_ids).encode()).hexdigest()
    
    def _build_relationships(
        self,
        markets_df: pd.DataFrame,
        prices_df: Optional[pd.DataFrame] = None,
        trades_df: Optional[pd.DataFrame] = None,
    ) -> Dict[str, List[Tuple[str, float, float]]]:
        """
        Build market relationships using NLP similarity.
        
        Returns:
            Dict of market_id -> list of (related_id, similarity, correlation)
        """
        # Check cache
        markets_hash = self._hash_markets(markets_df)
        if self._relationships is not None and self._last_markets_hash == markets_hash:
            return self._relationships
        
        logger.info("Building market relationships using NLP similarity...")
        
        # Limit markets for memory efficiency
        max_markets_for_similarity = self.config.parameters.get("max_markets_for_similarity", 1000)
        if len(markets_df) > max_markets_for_similarity:
            logger.warning(
                f"Too many markets ({len(markets_df)}), "
                f"limiting to {max_markets_for_similarity} for similarity computation"
            )
            markets_df = markets_df.head(max_markets_for_similarity)
        
        # Compute similarities (with memory limits)
        batch_size = self.config.parameters.get("similarity_batch_size", 100)
        max_comparisons = self.config.parameters.get("max_similarity_comparisons", None)
        similarity_df = self.similarity_engine.compute_batch_similarity(
            markets_df,
            batch_size=batch_size,
            max_comparisons=max_comparisons,
        )
        
        if similarity_df.empty:
            logger.warning("No similar markets found")
            self._relationships = {}
            self._last_markets_hash = markets_hash
            return self._relationships
        
        # Add price correlation if requested
        if self.similarity_engine.use_price_correlation:
            similarity_df = self.similarity_engine.add_price_correlation(
                similarity_df, prices_df=prices_df, trades_df=trades_df
            )
        
        # Build relationship dict
        relationships = defaultdict(list)
        
        # Sort by combined score or similarity
        score_col = "combined_score" if "combined_score" in similarity_df.columns else "similarity_score"
        similarity_df = similarity_df.sort_values(score_col, ascending=False)
        
        for _, row in similarity_df.iterrows():
            market_id1 = row["market_id1"]
            market_id2 = row["market_id2"]
            similarity = row["similarity_score"]
            correlation = row.get("price_correlation", None)
            if correlation is None or np.isnan(correlation):
                correlation = 0.0
            else:
                correlation = float(correlation)
            
            relationships[market_id1].append((market_id2, similarity, correlation))
            relationships[market_id2].append((market_id1, similarity, correlation))
        
        # Limit pairs per market
        max_pairs = self.config.parameters.get("max_pairs_per_market", 5)
        for market_id in relationships:
            relationships[market_id] = relationships[market_id][:max_pairs]
        
        self._relationships = dict(relationships)
        self._last_markets_hash = markets_hash
        
        logger.info(f"Built relationships for {len(self._relationships)} markets")
        return self._relationships
    
    def _calculate_spread(
        self,
        prices1: pd.Series,
        prices2: pd.Series,
        correlation: float,
    ) -> pd.Series:
        """Calculate spread between two price series."""
        if correlation > 0:
            return prices1 - prices2
        else:
            return prices1 + prices2 - 1
    
    def _analyze_pair(
        self,
        market_id1: str,
        market_id2: str,
        prices_df: pd.DataFrame,
        correlation: float,
        timestamp: datetime,
    ) -> Optional[Signal]:
        """
        Analyze a market pair for divergence signals.
        
        Returns Signal if divergence detected, None otherwise.
        """
        lookback_hours = self.config.parameters.get("lookback_hours", 72)
        z_score_threshold = self.config.parameters.get("z_score_threshold", 2.0)
        min_spread_pct = self.config.parameters.get("min_spread_pct", 0.05)
        max_spread_pct = self.config.parameters.get("max_spread_pct", 0.30)
        
        window_start = timestamp - timedelta(hours=lookback_hours)
        
        # Get price series
        p1 = prices_df[prices_df["market_id"] == market_id1]
        p2 = prices_df[prices_df["market_id"] == market_id2]
        
        p1 = p1[p1["datetime"] <= timestamp].sort_values("datetime")
        p2 = p2[p2["datetime"] <= timestamp].sort_values("datetime")
        
        if p1.empty or p2.empty:
            return None
        
        # Get current prices
        current_price1 = float(p1.iloc[-1]["price"])
        current_price2 = float(p2.iloc[-1]["price"])
        
        # Calculate current spread
        if correlation > 0:
            current_spread = current_price1 - current_price2
        else:
            current_spread = current_price1 + current_price2 - 1
        
        abs_spread = abs(current_spread)
        
        # Check spread bounds
        if abs_spread < min_spread_pct:
            return None
        if abs_spread > max_spread_pct:
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
        spread_mean = float(spread_history.mean())
        spread_std = float(spread_history.std())
        
        if spread_std == 0:
            return None
        
        z_score = (current_spread - spread_mean) / spread_std
        
        # Check for signal
        if abs(z_score) >= z_score_threshold:
            # Trade direction: bet on convergence
            if z_score > 0:
                # Spread too wide, expect it to narrow
                # If p1 > p2 more than usual: sell p1, buy p2
                signal_type = SignalType.SELL
                target_market = market_id1
                direction = -1
            else:
                # Spread too narrow, expect it to widen
                # If p1 < p2 more than usual: buy p1, sell p2
                signal_type = SignalType.BUY
                target_market = market_id1
                direction = 1
            
            confidence = min(abs(z_score) / 4.0, 0.9)
            
            return Signal(
                strategy_name=self.name,
                market_id=target_market,
                signal_type=signal_type,
                timestamp=timestamp,
                price=current_price1 if target_market == market_id1 else current_price2,
                confidence=confidence,
                reason=f"nlp_correlation_divergence",
                features={
                    "pair_market": market_id2 if target_market == market_id1 else market_id1,
                    "z_score": z_score,
                    "spread": current_spread,
                    "historical_spread": spread_mean,
                    "correlation": correlation,
                },
            )
        
        return None
    
    def generate_signals(
        self,
        market_data: Dict[str, Any],
        timestamp: datetime,
    ) -> List[Signal]:
        """
        Generate signals from NLP-based correlation analysis.
        
        Args:
            market_data: Current market state. Can include:
                - markets: List of market dicts
                - prices: Dict of market_id -> price or price dict
                - trades: Optional DataFrame with trades (used if prices not available)
            timestamp: Current time
            
        Returns:
            List of signals
        """
        signals: List[Signal] = []
        
        # Extract market data
        markets = market_data.get("markets", [])
        prices = market_data.get("prices", {})
        trades_df = market_data.get("trades")  # Optional: DataFrame with trades
        
        if not markets:
            return signals
        
        # Convert to DataFrame
        markets_df = pd.DataFrame(markets)
        if "id" not in markets_df.columns or "question" not in markets_df.columns:
            return signals
        
        # Build prices DataFrame (from prices dict or trades)
        prices_df = None
        prices_list = []
        
        # Try to build from prices dict first
        if prices:
            for market_id, price_data in prices.items():
                if isinstance(price_data, dict):
                    prices_list.append({
                        "market_id": market_id,
                        "datetime": price_data.get("datetime", timestamp),
                        "price": price_data.get("price", 0.5),
                    })
                elif isinstance(price_data, (int, float)):
                    prices_list.append({
                        "market_id": market_id,
                        "datetime": timestamp,
                        "price": float(price_data),
                    })
            
            if prices_list:
                prices_df = pd.DataFrame(prices_list)
                prices_df["datetime"] = pd.to_datetime(prices_df["datetime"])
        
        # If no prices but have trades, convert trades to prices
        if (prices_df is None or prices_df.empty) and trades_df is not None:
            if not trades_df.empty and TRADES_TO_PRICE_AVAILABLE:
                # Add outcome column if missing (infer from direction or default to YES)
                trades_for_price = trades_df.copy()
                if "outcome" not in trades_for_price.columns:
                    # Try to infer from maker_direction or taker_direction
                    if "maker_direction" in trades_for_price.columns:
                        # maker_direction might be "yes"/"no" or "buy"/"sell"
                        trades_for_price["outcome"] = trades_for_price["maker_direction"].str.upper()
                        # Normalize: if it's buy/sell, we can't determine outcome, default to YES
                        trades_for_price.loc[
                            ~trades_for_price["outcome"].isin(["YES", "NO"]), "outcome"
                        ] = "YES"
                    elif "taker_direction" in trades_for_price.columns:
                        trades_for_price["outcome"] = trades_for_price["taker_direction"].str.upper()
                        trades_for_price.loc[
                            ~trades_for_price["outcome"].isin(["YES", "NO"]), "outcome"
                        ] = "YES"
                    else:
                        # No direction info, default to YES
                        trades_for_price["outcome"] = "YES"
                
                prices_df = trades_to_price_history(trades_for_price, outcome="YES")
        
        # Build relationships (can work with just text similarity if no prices)
        relationships = self._build_relationships(
            markets_df, prices_df=prices_df, trades_df=trades_df
        )
        
        if not relationships:
            return signals
        
        # Need prices for divergence analysis
        if prices_df is None or prices_df.empty:
            logger.warning("No price data available for divergence analysis")
            return signals
        
        # Check each market pair for divergences
        checked_pairs = set()
        
        for market_id1, related_list in relationships.items():
            for market_id2, similarity, correlation in related_list:
                pair_key = tuple(sorted([market_id1, market_id2]))
                if pair_key in checked_pairs:
                    continue
                
                signal = self._analyze_pair(
                    market_id1,
                    market_id2,
                    prices_df,
                    correlation,
                    timestamp,
                )
                
                if signal:
                    signals.append(signal)
                    checked_pairs.add(pair_key)
        
        return signals


__all__ = ["NLPCorrelationStrategy", "NLPSimilarityEngine", "MarketSimilarity"]
