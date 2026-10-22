"""
Market categorization using keyword matching.
"""

from typing import Dict, List


CATEGORIES = {
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "solana",
        "dogecoin", "memecoin", "defi", "blockchain", "token"
    ],
    "politics": [
        "trump", "biden", "election", "democrat", "republican",
        "congress", "senate", "president", "governor", "vote", "poll",
        "primary", "nominee", "campaign"
    ],
    "sports": [
        "nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball",
        "baseball", "tennis", "ufc", "boxing", "super bowl", "world cup",
        "championship", "playoffs", "mvp"
    ],
    "ai_tech": [
        "ai", "gpt", "openai", "anthropic", "claude", "gemini", "llm",
        "artificial intelligence", "machine learning", "apple", "google",
        "microsoft", "meta", "nvidia", "chatgpt", "deepmind"
    ],
    "finance": [
        "stock", "spy", "s&p", "nasdaq", "dow", "fed", "interest rate",
        "inflation", "recession", "gdp", "earnings", "ipo", "market"
    ],
    "geopolitics": [
        "war", "ukraine", "russia", "china", "israel", "iran",
        "military", "nato", "invasion", "sanctions", "conflict"
    ],
}


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
