"""Unit tests for MarketTaxonomy."""

import pytest
import tempfile
from pathlib import Path

import pandas as pd

from src.taxonomy.markets import (
    MarketTaxonomy,
    MarketClassification,
    CATEGORIES,
    categorize_market,
    categorize_markets,
)


class TestCategorizeMarket:
    def test_crypto_keywords(self):
        assert categorize_market("Will Bitcoin reach $100k?") == "crypto"
        assert categorize_market("Ethereum ETF approval") == "crypto"
        assert categorize_market("Solana price prediction") == "crypto"

    def test_politics_keywords(self):
        assert categorize_market("Trump wins 2024 election") == "politics"
        assert categorize_market("Senate majority vote") == "politics"
        assert categorize_market("Biden approval rating") == "politics"

    def test_sports_keywords(self):
        assert categorize_market("Super Bowl winner") == "sports"
        assert categorize_market("NBA MVP award") == "sports"
        assert categorize_market("World Cup finals") == "sports"

    def test_ai_tech_keywords(self):
        assert categorize_market("OpenAI releases GPT-5") == "ai_tech"
        assert categorize_market("Anthropic Claude update") == "ai_tech"
        assert categorize_market("NVIDIA earnings report") == "ai_tech"

    def test_other_category(self):
        assert categorize_market("Random unrelated question") == "other"
        assert categorize_market("") == "other"


class TestCategorizeMarkets:
    def test_batch_categorization(self):
        resolution_data = {
            "market1": {"question": "Bitcoin price above 50k?"},
            "market2": {"question": "Trump wins primary?"},
            "market3": {"question": "Lakers win championship?"},
        }

        categories = categorize_markets(resolution_data)

        assert categories["market1"] == "crypto"
        assert categories["market2"] == "politics"
        assert categories["market3"] == "sports"


class TestMarketClassification:
    def test_dataclass_fields(self):
        classification = MarketClassification(
            market_id="test_123",
            question="Will Bitcoin reach $100k?",
            primary_category="crypto",
            subcategory="price",
            confidence=0.9,
            method="keyword",
        )

        assert classification.market_id == "test_123"
        assert classification.primary_category == "crypto"
        assert classification.confidence == 0.9


class TestMarketTaxonomy:
    def test_init_default(self):
        taxonomy = MarketTaxonomy()

        assert len(taxonomy._categories) > 0
        assert "crypto" in taxonomy._categories
        assert "politics" in taxonomy._categories

    def test_classify_single(self):
        taxonomy = MarketTaxonomy()

        result = taxonomy.classify("Will Bitcoin hit $100k by end of 2025?")

        assert result.primary_category == "crypto"
        assert result.confidence > 0
        assert result.method == "keyword"

    def test_classify_with_market_id(self):
        taxonomy = MarketTaxonomy()

        result = taxonomy.classify(
            "Trump 2024 election odds",
            market_id="poly_12345",
        )

        assert result.market_id == "poly_12345"
        assert result.primary_category == "politics"

    def test_classify_batch(self):
        taxonomy = MarketTaxonomy()

        markets_df = pd.DataFrame({
            "id": ["m1", "m2", "m3"],
            "question": [
                "Bitcoin price prediction",
                "Trump wins election",
                "Lakers championship",
            ],
        })

        result = taxonomy.classify_batch(markets_df)

        assert len(result) == 3
        assert result["primary_category"].tolist() == ["crypto", "politics", "sports"]

    def test_list_categories(self):
        taxonomy = MarketTaxonomy()

        categories = taxonomy.list_categories()

        assert "crypto" in categories
        assert "politics" in categories
        assert len(categories) >= 5

    def test_get_keywords(self):
        taxonomy = MarketTaxonomy()

        crypto_keywords = taxonomy.get_keywords("crypto")

        assert "bitcoin" in crypto_keywords
        assert "ethereum" in crypto_keywords

    def test_get_hierarchy(self):
        taxonomy = MarketTaxonomy()

        # This tests subcategories if defined
        subcats = taxonomy.get_hierarchy("crypto")

        # May be empty if no subcategories defined
        assert isinstance(subcats, list)

    def test_add_category(self):
        taxonomy = MarketTaxonomy()

        taxonomy.add_category(
            "custom",
            keywords=["custom_keyword", "another_term"],
        )

        assert "custom" in taxonomy.list_categories()

        result = taxonomy.classify("This contains custom_keyword")
        assert result.primary_category == "custom"

    def test_empty_question(self):
        taxonomy = MarketTaxonomy()

        result = taxonomy.classify("")

        assert result.primary_category == "other"
        assert result.confidence == 0.0

    def test_multiple_matches(self):
        taxonomy = MarketTaxonomy()

        # Question with multiple category keywords
        result = taxonomy.classify("Bitcoin reaches $100k after Trump election win")

        # Should return highest scoring category
        assert result.primary_category in ["crypto", "politics"]
        assert len(result.all_matches) >= 2
