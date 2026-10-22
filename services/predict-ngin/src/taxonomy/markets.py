"""
Market taxonomy for hierarchical categorization.

Provides keyword and hybrid-based classification of prediction markets.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import logging
import re

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

import pandas as pd


# Default categories (used if no taxonomy file provided)
CATEGORIES = {
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "solana",
        "dogecoin", "memecoin", "defi", "blockchain", "token", "xrp",
        "cardano", "polkadot", "avalanche", "polygon", "arbitrum",
    ],
    "politics": [
        "trump", "biden", "election", "democrat", "republican",
        "congress", "senate", "president", "governor", "vote", "poll",
        "primary", "nominee", "campaign", "ballot", "legislation",
    ],
    "sports": [
        "nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball",
        "baseball", "tennis", "ufc", "boxing", "super bowl", "world cup",
        "championship", "playoffs", "mvp", "premier league", "uefa",
    ],
    "ai_tech": [
        "ai", "gpt", "openai", "anthropic", "claude", "gemini", "llm",
        "artificial intelligence", "machine learning", "apple", "google",
        "microsoft", "meta", "nvidia", "chatgpt", "deepmind", "agi",
    ],
    "finance": [
        "stock", "spy", "s&p", "nasdaq", "dow", "fed", "interest rate",
        "inflation", "recession", "gdp", "earnings", "ipo", "market",
        "treasury", "bonds", "yield", "forex", "currency",
    ],
    "geopolitics": [
        "war", "ukraine", "russia", "china", "israel", "iran",
        "military", "nato", "invasion", "sanctions", "conflict",
        "north korea", "taiwan", "missile",
    ],
    "entertainment": [
        "oscars", "emmy", "grammy", "netflix", "movie", "film",
        "tv show", "album", "billboard", "box office", "celebrity",
    ],
    "science": [
        "nasa", "spacex", "mars", "moon", "climate", "vaccine",
        "fda", "drug", "trial", "study", "research", "discovery",
    ],
}


@dataclass
class MarketClassification:
    """
    Classification result for a market.

    Attributes:
        market_id: Market identifier
        question: Market question text
        primary_category: Main category
        subcategory: Optional subcategory
        confidence: Classification confidence (0-1)
        method: Classification method used
        all_matches: All matched categories with scores
    """

    market_id: str
    question: str
    primary_category: str
    subcategory: Optional[str] = None
    confidence: float = 1.0
    method: str = "keyword"
    all_matches: Dict[str, float] = field(default_factory=dict)


class MarketTaxonomy:
    """
    Hierarchical market classification.

    Supports keyword-based and hybrid (keyword + rules) classification.

    Example:
        taxonomy = MarketTaxonomy()
        result = taxonomy.classify("Will Bitcoin reach $100k by 2025?")
        print(result.primary_category)  # "crypto"

        # Batch classification
        markets_df["category"] = taxonomy.classify_batch(markets_df)["primary_category"]
    """

    def __init__(
        self,
        taxonomy_path: Optional[str] = None,
        default_category: str = "other",
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize taxonomy.

        Args:
            taxonomy_path: Path to taxonomy YAML file
            default_category: Category for unclassified markets
            logger: Logger instance
        """
        self.default_category = default_category
        self.logger = logger or logging.getLogger("taxonomy.markets")

        self._categories: Dict[str, Dict[str, Any]] = {}
        self._keyword_patterns: Dict[str, List[re.Pattern]] = {}

        if taxonomy_path:
            self._load_taxonomy(taxonomy_path)
        else:
            self._load_default_taxonomy()

        self._compile_patterns()

    def _load_taxonomy(self, path: str) -> None:
        """Load taxonomy from YAML file."""
        if not YAML_AVAILABLE:
            self.logger.warning("PyYAML not available, using default taxonomy")
            self._load_default_taxonomy()
            return

        filepath = Path(path)
        if not filepath.exists():
            self.logger.warning(f"Taxonomy file not found: {path}, using default")
            self._load_default_taxonomy()
            return

        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        self._categories = data.get("categories", {})
        self.logger.info(f"Loaded taxonomy with {len(self._categories)} categories")

    def _load_default_taxonomy(self) -> None:
        """Load built-in default taxonomy."""
        for category, keywords in CATEGORIES.items():
            self._categories[category] = {
                "keywords": keywords,
                "subcategories": {},
            }

    def _compile_patterns(self) -> None:
        """Compile regex patterns for keywords."""
        for category, data in self._categories.items():
            keywords = data.get("keywords", [])
            patterns = []

            for kw in keywords:
                # Create word-boundary pattern for multi-word keywords
                escaped = re.escape(kw.lower())
                pattern = re.compile(r'\b' + escaped + r'\b', re.IGNORECASE)
                patterns.append(pattern)

            self._keyword_patterns[category] = patterns

            # Compile subcategory patterns
            for subcat, subdata in data.get("subcategories", {}).items():
                sub_keywords = subdata.get("keywords", [])
                sub_patterns = []
                for kw in sub_keywords:
                    escaped = re.escape(kw.lower())
                    pattern = re.compile(r'\b' + escaped + r'\b', re.IGNORECASE)
                    sub_patterns.append(pattern)

                self._keyword_patterns[f"{category}.{subcat}"] = sub_patterns

    def classify(
        self,
        question: str,
        market_id: str = "",
        method: str = "keyword",
    ) -> MarketClassification:
        """
        Classify a single market question.

        Args:
            question: Market question text
            market_id: Optional market identifier
            method: Classification method (keyword, hybrid)

        Returns:
            MarketClassification with category and confidence
        """
        if not question:
            return MarketClassification(
                market_id=market_id,
                question=question,
                primary_category=self.default_category,
                confidence=0.0,
                method=method,
            )

        question_lower = question.lower()

        # Count matches per category
        category_scores: Dict[str, float] = {}

        for category, patterns in self._keyword_patterns.items():
            if "." in category:
                continue  # Skip subcategories in first pass

            score = 0.0
            for pattern in patterns:
                if pattern.search(question_lower):
                    score += 1.0

            if score > 0:
                category_scores[category] = score

        if not category_scores:
            return MarketClassification(
                market_id=market_id,
                question=question,
                primary_category=self.default_category,
                confidence=0.0,
                method=method,
            )

        # Get primary category (highest score)
        primary = max(category_scores, key=lambda k: category_scores[k])
        max_score = category_scores[primary]

        # Normalize confidence based on number of matches
        total_keywords = len(self._keyword_patterns.get(primary, []))
        confidence = min(max_score / max(total_keywords * 0.3, 1), 1.0)

        # Check subcategories
        subcategory = None
        subcat_data = self._categories.get(primary, {}).get("subcategories", {})

        if subcat_data:
            subcat_scores: Dict[str, float] = {}

            for subcat in subcat_data:
                patterns = self._keyword_patterns.get(f"{primary}.{subcat}", [])
                score = 0.0
                for pattern in patterns:
                    if pattern.search(question_lower):
                        score += 1.0
                if score > 0:
                    subcat_scores[subcat] = score

            if subcat_scores:
                subcategory = max(subcat_scores, key=lambda k: subcat_scores[k])

        return MarketClassification(
            market_id=market_id,
            question=question,
            primary_category=primary,
            subcategory=subcategory,
            confidence=confidence,
            method=method,
            all_matches=category_scores,
        )

    def classify_batch(
        self,
        markets: pd.DataFrame,
        question_col: str = "question",
        market_id_col: str = "id",
        method: str = "keyword",
    ) -> pd.DataFrame:
        """
        Classify a batch of markets.

        Args:
            markets: DataFrame with market data
            question_col: Column containing questions
            market_id_col: Column containing market IDs
            method: Classification method

        Returns:
            DataFrame with classification columns added
        """
        results = []

        for _, row in markets.iterrows():
            question = str(row.get(question_col, ""))
            market_id = str(row.get(market_id_col, ""))

            classification = self.classify(question, market_id, method)

            results.append({
                market_id_col: market_id,
                "primary_category": classification.primary_category,
                "subcategory": classification.subcategory,
                "confidence": classification.confidence,
                "method": classification.method,
            })

        return pd.DataFrame(results)

    def get_hierarchy(self, category: str) -> List[str]:
        """
        Get category hierarchy.

        Args:
            category: Category name

        Returns:
            List of subcategory names, empty if category not found
        """
        cat_data = self._categories.get(category, {})
        subcats = cat_data.get("subcategories", {})
        return list(subcats.keys())

    def list_categories(self) -> List[str]:
        """
        List all top-level categories.

        Returns:
            List of category names
        """
        return list(self._categories.keys())

    def get_keywords(self, category: str) -> List[str]:
        """
        Get keywords for a category.

        Args:
            category: Category name

        Returns:
            List of keywords
        """
        return self._categories.get(category, {}).get("keywords", [])

    def add_category(
        self,
        name: str,
        keywords: List[str],
        subcategories: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        """
        Add a new category.

        Args:
            name: Category name
            keywords: List of keywords
            subcategories: Optional dict of subcategory name -> keywords
        """
        self._categories[name] = {
            "keywords": keywords,
            "subcategories": {},
        }

        if subcategories:
            for subname, subkeywords in subcategories.items():
                self._categories[name]["subcategories"][subname] = {
                    "keywords": subkeywords,
                }

        self._compile_patterns()

    def save_taxonomy(self, path: str) -> None:
        """
        Save taxonomy to YAML file.

        Args:
            path: Output file path
        """
        if not YAML_AVAILABLE:
            raise ImportError("PyYAML required to save taxonomy")

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump({"categories": self._categories}, f, default_flow_style=False)


# Convenience functions for backward compatibility
def categorize_market(question: str) -> str:
    """
    Categorize a market based on keywords in the question.

    Args:
        question: Market question text

    Returns:
        Category name or "other"
    """
    q = question.lower()

    for category, keywords in CATEGORIES.items():
        if any(kw in q for kw in keywords):
            return category

    return "other"


def categorize_markets(
    resolution_data: Dict,
    question_key: str = "question"
) -> Dict[str, str]:
    """
    Categorize all markets in resolution data.

    Args:
        resolution_data: Dictionary of market data
        question_key: Key containing the question text

    Returns:
        Dictionary mapping market ID to category
    """
    categories = {}

    for mid, data in resolution_data.items():
        question = data.get(question_key, "")
        categories[mid] = categorize_market(question)

    return categories


__all__ = [
    "MarketClassification",
    "MarketTaxonomy",
    "CATEGORIES",
    "categorize_market",
    "categorize_markets",
]
