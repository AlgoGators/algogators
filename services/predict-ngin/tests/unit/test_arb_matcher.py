"""Tests for cross-platform market matching hard checks and the rule-based matcher."""

import pandas as pd
import pytest

from src.trading.arbitrage.market_matcher import MarketMatcher
from src.trading.arbitrage.rule_matcher import (
    RuleMatcher,
    extract_contract_terms,
    validate_pair,
)


class TestExtractTerms:
    def test_strike_and_date(self):
        t = extract_contract_terms("Will Bitcoin be above $100,000 on January 5, 2026?")
        assert t.strike == pytest.approx(100000)
        assert t.date == pd.Timestamp("2026-01-05")
        assert t.comparator == "above"

    def test_k_suffix(self):
        assert extract_contract_terms("BTC above 105k on Jan 5 2026").strike == pytest.approx(105000)

    def test_polarity_negation(self):
        assert extract_contract_terms("Will the Fed NOT cut rates in March 2025?").negated
        assert not extract_contract_terms("Will the Fed cut rates in March 2025?").negated


class TestValidatePair:
    def test_strike_mismatch_rejected(self):
        v = validate_pair(
            "Will Bitcoin be above $100,000 on January 5, 2026?",
            "Bitcoin price above $105,000 on Jan 5, 2026?",
        )
        assert not v.ok and "strike" in v.reasons[0]

    def test_date_mismatch_rejected(self):
        v = validate_pair("Will BTC be above $100,000 on January 5, 2026?", "Bitcoin above $100,000 on Jan 12, 2026?")
        assert not v.ok and any("date" in r for r in v.reasons)

    def test_comparator_mismatch_rejected(self):
        v = validate_pair("Will BTC be above $100,000 on January 5, 2026?", "Bitcoin below $100,000 on Jan 5, 2026?")
        assert not v.ok

    def test_polarity_detected(self):
        v = validate_pair("Will the Fed cut rates after the March 2025 meeting?", "Will the Fed not cut rates after the March 2025 meeting?")
        assert not v.ok and any("polarity" in r for r in v.reasons)

    def test_btc_noon_vs_5pm_flagged(self):
        v = validate_pair(
            "Will the price of Bitcoin be above $100,000 on January 5?",
            "Bitcoin price above $100,000 on Jan 5, 2026 at 5pm EDT?",
            pm_meta={"description": "resolves according to the Binance BTC/USDT 1 minute candle for 12:00 in the ET timezone (noon)"},
        )
        assert "resolution_time_mismatch" in v.flags
        assert not v.ok

    def test_same_contract_ok(self):
        v = validate_pair("Will BTC be above $100,000 on January 5, 2026?", "Bitcoin above $100k on Jan 5, 2026?")
        assert v.ok, v.reasons


class TestMarketMatcherHardChecks:
    def test_tfidf_false_match_rejected(self):
        pm = pd.DataFrame({"id": ["1"], "question": ["Will Bitcoin be above $100,000 on January 5, 2026?"]})
        k = pd.DataFrame({"ticker": ["KXBTCD-26JAN05-T105000"], "title": ["Will Bitcoin be above $105,000 on January 5, 2026?"]})
        assert MarketMatcher(min_similarity=0.25).match(pm, k) == []

    def test_year_only_date_no_longer_boosts(self):
        pm = pd.DataFrame({"id": ["1"], "question": ["Will the Fed cut rates in March 2025?"]})
        k = pd.DataFrame({"ticker": ["K"], "title": ["Will the Fed cut rates in June 2025?"]})
        assert MarketMatcher(min_similarity=0.25).match(pm, k) == []

    def test_true_match_survives(self):
        pm = pd.DataFrame({"id": ["1"], "question": ["Will Bitcoin be above $100,000 on January 5, 2026?"]})
        k = pd.DataFrame({"ticker": ["K"], "title": ["Will Bitcoin be above $100,000 on January 5, 2026?"]})
        pairs = MarketMatcher(min_similarity=0.25).match(pm, k)
        assert len(pairs) == 1 and pairs[0].metadata.get("validation") == "ok"


class TestRuleMatcher:
    def test_fed_and_sports(self):
        pm = pd.DataFrame(
            {
                "id": ["10", "11", "12"],
                "question": [
                    "Fed decreases interest rates by 25 bps after March 2025 meeting?",
                    "No change in Fed interest rates after March 2025 meeting?",
                    "Lakers vs. Celtics",
                ],
                "slug": ["fed-a", "fed-b", "nba-lal-bos-2025-03-01"],
                "end_date": ["2025-03-19T12:00:00Z", "2025-03-19T12:00:00Z", "2025-03-02T03:00:00Z"],
            }
        )
        k = pd.DataFrame(
            {
                "ticker": [
                    "KXFEDDECISION-25MAR-C25",
                    "KXFEDDECISION-25MAR-H0",
                    "KXNBAGAME-25MAR01LALBOS-LAL",
                    "KXNBAGAME-25MAR01LALBOS-BOS",
                ]
            }
        )
        out = RuleMatcher().match(pm, k)
        got = dict(zip(out.pm_id, out.k_ticker))
        assert got["10"] == "KXFEDDECISION-25MAR-C25"
        assert got["11"] == "KXFEDDECISION-25MAR-H0"
        assert got["12"] == "KXNBAGAME-25MAR01LALBOS-LAL"  # PM YES = first-named team
        assert out.set_index("pm_id").loc["12", "pm_yes_is_k_yes"]
