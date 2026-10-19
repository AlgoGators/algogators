"""
Market Scanner for Polymarket.

Fetches active markets from the Polymarket Gamma API and scores them
by trading opportunity based on volume, expiry, price movement, and liquidity.

Usage:
    python -m src.tools.market_scanner --top 20
    python -m src.tools.market_scanner --top 10 --json
    python -m src.tools.market_scanner --min-volume 50000 --min-liquidity 10000
    python -m src.tools.market_scanner --mode high-volume --top 15
    python -m src.tools.market_scanner --mode expiring --expiring-within 5
    python -m src.tools.market_scanner --mode opportunities --mode expiring
"""

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests


# API endpoint
POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"

# Default thresholds
DEFAULT_MIN_VOLUME = 10000.0      # Minimum 24h volume in USD
DEFAULT_MIN_LIQUIDITY = 5000.0    # Minimum liquidity in USD
DEFAULT_MIN_SCORE = 0.0           # Minimum opportunity score


@dataclass
class MarketOpportunity:
    """Represents a scored market opportunity."""

    # Market info
    market_id: str
    question: str
    slug: str
    url: str

    # Current state
    yes_price: float
    no_price: float
    volume_24h: float
    total_volume: float
    liquidity: float

    # Timing
    end_date: Optional[str]
    days_to_expiry: Optional[float]

    # Scores (0-1 scale)
    volume_score: float
    expiry_score: float
    movement_score: float
    liquidity_score: float
    opportunity_score: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


class MarketScanner:
    """Scans Polymarket for trading opportunities."""

    def __init__(
        self,
        min_volume: float = DEFAULT_MIN_VOLUME,
        min_liquidity: float = DEFAULT_MIN_LIQUIDITY,
        min_score: float = DEFAULT_MIN_SCORE,
    ):
        """
        Initialize the scanner.

        Args:
            min_volume: Minimum 24h volume threshold
            min_liquidity: Minimum liquidity threshold
            min_score: Minimum opportunity score threshold
        """
        self.min_volume = min_volume
        self.min_liquidity = min_liquidity
        self.min_score = min_score

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PredictNgin/1.0"
        })

    def fetch_active_markets(
        self,
        limit: int = 500,
        batch_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Fetch active (non-closed) markets from Polymarket Gamma API.

        Args:
            limit: Maximum number of markets to fetch
            batch_size: Number of markets per API request

        Returns:
            List of market dictionaries
        """
        markets = []
        offset = 0

        while len(markets) < limit:
            try:
                response = self.session.get(
                    f"{POLYMARKET_GAMMA_API}/markets",
                    params={
                        "limit": min(batch_size, limit - len(markets)),
                        "offset": offset,
                        "closed": "false",
                        "active": "true",
                    },
                    timeout=30,
                )
                response.raise_for_status()
                batch = response.json()

                if not batch:
                    break

                markets.extend(batch)
                offset += len(batch)

                if len(batch) < batch_size:
                    break

            except requests.RequestException as e:
                print(f"Error fetching markets: {e}", file=sys.stderr)
                break

        return markets

    def _parse_float(self, value: Any, default: float = 0.0) -> float:
        """Safely parse a float value."""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def _parse_prices(self, market: Dict[str, Any]) -> tuple[float, float]:
        """Parse YES/NO prices from market data."""
        prices_str = market.get("outcomePrices", "")

        if isinstance(prices_str, str) and prices_str:
            try:
                prices = json.loads(prices_str)
                if isinstance(prices, list) and len(prices) >= 2:
                    return self._parse_float(prices[0], 0.5), self._parse_float(prices[1], 0.5)
            except json.JSONDecodeError:
                pass

        # Fallback: try bestBid/bestAsk or default
        best_bid = self._parse_float(market.get("bestBid"), 0.5)
        best_ask = self._parse_float(market.get("bestAsk"), 0.5)

        if best_bid > 0 and best_ask > 0:
            return (best_bid + best_ask) / 2, 1 - (best_bid + best_ask) / 2

        return 0.5, 0.5

    def _calculate_days_to_expiry(self, end_date_str: Optional[str]) -> Optional[float]:
        """Calculate days until market expiry."""
        if not end_date_str:
            return None

        try:
            # Handle ISO format with or without timezone
            if end_date_str.endswith("Z"):
                end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            elif "+" in end_date_str or end_date_str.count("-") >= 3:
                end_date = datetime.fromisoformat(end_date_str)
            else:
                end_date = datetime.fromisoformat(end_date_str).replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            delta = end_date - now
            return max(0, delta.total_seconds() / 86400)
        except (ValueError, TypeError):
            return None

    def _score_volume(self, volume_24h: float) -> float:
        """
        Score based on 24h volume.

        Higher volume = higher score.
        Scale: $10k = 0.2, $100k = 0.5, $1M = 0.8, $10M = 1.0
        """
        if volume_24h <= 0:
            return 0.0

        import math
        # Log scale from $10k to $10M
        log_vol = math.log10(max(volume_24h, 1))
        # 4 (10k) -> 0.2, 5 (100k) -> 0.4, 6 (1M) -> 0.7, 7 (10M) -> 1.0
        score = (log_vol - 4) / 3
        return max(0.0, min(1.0, score))

    def _score_expiry(self, days_to_expiry: Optional[float]) -> float:
        """
        Score based on days to expiry.

        Prefer markets expiring in 1-7 days.
        Peak score at 3-5 days, declining for very short or long expiries.
        """
        if days_to_expiry is None:
            return 0.3  # Unknown expiry gets neutral score

        if days_to_expiry < 0.5:
            # Less than 12 hours - too risky
            return 0.1
        elif days_to_expiry <= 1:
            # 12-24 hours - moderate score
            return 0.6
        elif days_to_expiry <= 7:
            # Sweet spot: 1-7 days
            # Peak at 3-4 days
            if days_to_expiry <= 4:
                return 0.8 + (days_to_expiry - 1) * 0.067  # 0.8 to 1.0
            else:
                return 1.0 - (days_to_expiry - 4) * 0.067  # 1.0 to 0.8
        elif days_to_expiry <= 30:
            # 1-4 weeks - decent
            return 0.5 - (days_to_expiry - 7) * 0.013  # 0.5 to 0.2
        else:
            # More than a month - low priority
            return 0.1

    def _score_movement(self, yes_price: float) -> float:
        """
        Score based on price position (proxy for movement potential).

        Markets near 50% have highest movement potential.
        Extreme prices (near 0 or 1) have limited upside.
        """
        # Distance from 50%
        distance_from_mid = abs(yes_price - 0.5)

        # Score inversely proportional to distance from 50%
        # At 50%: score = 1.0
        # At 0% or 100%: score = 0.0
        return 1.0 - (distance_from_mid * 2)

    def _score_liquidity(self, liquidity: float) -> float:
        """
        Score based on available liquidity.

        Higher liquidity = easier to enter/exit positions.
        Scale: $5k = 0.2, $50k = 0.5, $500k = 0.8, $5M = 1.0
        """
        if liquidity <= 0:
            return 0.0

        import math
        log_liq = math.log10(max(liquidity, 1))
        # 3.7 (5k) -> 0.2, 4.7 (50k) -> 0.5, 5.7 (500k) -> 0.8, 6.7 (5M) -> 1.0
        score = (log_liq - 3.7) / 3
        return max(0.0, min(1.0, score))

    def _calculate_opportunity_score(
        self,
        volume_score: float,
        expiry_score: float,
        movement_score: float,
        liquidity_score: float,
    ) -> float:
        """
        Combine individual scores into overall opportunity score.

        Weights:
        - Volume: 30% (indicates market interest)
        - Expiry: 25% (timing is important)
        - Movement: 25% (profit potential)
        - Liquidity: 20% (ability to execute)
        """
        return (
            volume_score * 0.30 +
            expiry_score * 0.25 +
            movement_score * 0.25 +
            liquidity_score * 0.20
        )

    def score_market(self, market: Dict[str, Any]) -> Optional[MarketOpportunity]:
        """
        Score a single market for trading opportunity.

        Args:
            market: Market data from API

        Returns:
            MarketOpportunity if market passes thresholds, None otherwise
        """
        opp = self.build_opportunity(market)
        if opp is None:
            return None

        # Apply minimum score threshold
        if opp.opportunity_score < self.min_score:
            return None

        return opp

    def build_opportunity(self, market: Dict[str, Any]) -> Optional[MarketOpportunity]:
        """
        Build a MarketOpportunity from market data without applying score threshold.

        Args:
            market: Market data from API

        Returns:
            MarketOpportunity if market passes volume/liquidity thresholds, None otherwise
        """
        # Extract basic info
        market_id = market.get("id", "")
        question = market.get("question", "")[:100]
        slug = market.get("slug", "")

        # Parse numeric values
        volume_24h = self._parse_float(market.get("volume24hr"))
        total_volume = self._parse_float(market.get("volume"))
        liquidity = self._parse_float(market.get("liquidity"))

        # Apply minimum thresholds early
        if volume_24h < self.min_volume:
            return None
        if liquidity < self.min_liquidity:
            return None

        # Parse prices
        yes_price, no_price = self._parse_prices(market)

        # Calculate days to expiry
        end_date = market.get("endDate")
        days_to_expiry = self._calculate_days_to_expiry(end_date)

        # Calculate individual scores
        volume_score = self._score_volume(volume_24h)
        expiry_score = self._score_expiry(days_to_expiry)
        movement_score = self._score_movement(yes_price)
        liquidity_score = self._score_liquidity(liquidity)

        # Calculate overall score
        opportunity_score = self._calculate_opportunity_score(
            volume_score, expiry_score, movement_score, liquidity_score
        )

        # Build URL
        url = f"https://polymarket.com/event/{slug}" if slug else ""

        return MarketOpportunity(
            market_id=market_id,
            question=question,
            slug=slug,
            url=url,
            yes_price=yes_price,
            no_price=no_price,
            volume_24h=volume_24h,
            total_volume=total_volume,
            liquidity=liquidity,
            end_date=end_date,
            days_to_expiry=days_to_expiry,
            volume_score=round(volume_score, 3),
            expiry_score=round(expiry_score, 3),
            movement_score=round(movement_score, 3),
            liquidity_score=round(liquidity_score, 3),
            opportunity_score=round(opportunity_score, 3),
        )

    def build_opportunities(self, markets: List[Dict[str, Any]]) -> List[MarketOpportunity]:
        """Build opportunities from raw market data (volume/liquidity filtered)."""
        opportunities: List[MarketOpportunity] = []
        for market in markets:
            opp = self.build_opportunity(market)
            if opp is not None:
                opportunities.append(opp)
        return opportunities

    def scan(
        self,
        top: int = 20,
        fetch_limit: int = 500,
    ) -> List[MarketOpportunity]:
        """
        Scan markets and return top opportunities.

        Args:
            top: Number of top opportunities to return
            fetch_limit: Maximum markets to fetch from API

        Returns:
            List of MarketOpportunity sorted by opportunity_score
        """
        # Fetch markets
        markets = self.fetch_active_markets(limit=fetch_limit)

        return self.scan_from_markets(markets, top=top)

    def scan_from_markets(
        self,
        markets: List[Dict[str, Any]],
        top: int = 20,
    ) -> List[MarketOpportunity]:
        """Scan a pre-fetched market list for top opportunities."""
        opportunities = [
            opp for opp in self.build_opportunities(markets)
            if opp.opportunity_score >= self.min_score
        ]
        opportunities.sort(key=lambda x: x.opportunity_score, reverse=True)
        return opportunities[:top]

    def scan_high_volume(
        self,
        top: int = 20,
        fetch_limit: int = 500,
    ) -> List[MarketOpportunity]:
        """Return highest 24h volume markets."""
        markets = self.fetch_active_markets(limit=fetch_limit)
        return self.scan_high_volume_from_markets(markets, top=top)

    def scan_high_volume_from_markets(
        self,
        markets: List[Dict[str, Any]],
        top: int = 20,
    ) -> List[MarketOpportunity]:
        """Return highest 24h volume markets from pre-fetched data."""
        opportunities = self.build_opportunities(markets)
        opportunities.sort(
            key=lambda x: (x.volume_24h, x.liquidity, x.opportunity_score),
            reverse=True,
        )
        return opportunities[:top]

    def scan_expiring(
        self,
        top: int = 20,
        fetch_limit: int = 500,
        within_days: float = 7.0,
        include_no_expiry: bool = False,
    ) -> List[MarketOpportunity]:
        """Return markets expiring within a given number of days."""
        markets = self.fetch_active_markets(limit=fetch_limit)
        return self.scan_expiring_from_markets(
            markets,
            top=top,
            within_days=within_days,
            include_no_expiry=include_no_expiry,
        )

    def scan_expiring_from_markets(
        self,
        markets: List[Dict[str, Any]],
        top: int = 20,
        within_days: float = 7.0,
        include_no_expiry: bool = False,
    ) -> List[MarketOpportunity]:
        """Return markets expiring within N days from pre-fetched data."""
        opportunities = []
        for opp in self.build_opportunities(markets):
            if opp.days_to_expiry is None:
                if include_no_expiry:
                    opportunities.append(opp)
                continue
            if opp.days_to_expiry <= within_days:
                opportunities.append(opp)

        opportunities.sort(
            key=lambda x: (
                x.days_to_expiry if x.days_to_expiry is not None else float("inf"),
                -x.volume_24h,
            )
        )
        return opportunities[:top]



def format_table(opportunities: List[MarketOpportunity]) -> str:
    """Format opportunities as a nice ASCII table."""
    if not opportunities:
        return "No opportunities found matching criteria."

    # Column definitions
    columns = [
        ("Rank", 4),
        ("Question", 45),
        ("YES", 6),
        ("Vol 24h", 10),
        ("Liquidity", 10),
        ("Expiry", 10),
        ("Score", 6),
    ]

    # Build header
    header = " | ".join(name.ljust(width) for name, width in columns)
    separator = "-+-".join("-" * width for _, width in columns)

    lines = [header, separator]

    for i, opp in enumerate(opportunities, 1):
        # Format values
        rank = str(i)
        question = opp.question[:45] if len(opp.question) > 45 else opp.question
        yes_price = f"{opp.yes_price:.1%}"
        vol_24h = _format_currency(opp.volume_24h)
        liquidity = _format_currency(opp.liquidity)

        if opp.days_to_expiry is not None:
            if opp.days_to_expiry < 1:
                expiry = f"{opp.days_to_expiry * 24:.0f}h"
            else:
                expiry = f"{opp.days_to_expiry:.1f}d"
        else:
            expiry = "N/A"

        score = f"{opp.opportunity_score:.2f}"

        # Build row
        row_values = [rank, question, yes_price, vol_24h, liquidity, expiry, score]
        row = " | ".join(
            val.ljust(width) for val, (_, width) in zip(row_values, columns)
        )
        lines.append(row)

    return "\n".join(lines)


def _format_currency(value: float) -> str:
    """Format currency value with K/M suffixes."""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.0f}K"
    else:
        return f"${value:.0f}"


def scan_markets(
    top: int = 20,
    min_volume: float = DEFAULT_MIN_VOLUME,
    min_liquidity: float = DEFAULT_MIN_LIQUIDITY,
    min_score: float = DEFAULT_MIN_SCORE,
    as_json: bool = False,
) -> List[MarketOpportunity]:
    """
    Convenience function to scan markets.

    Args:
        top: Number of top opportunities to return
        min_volume: Minimum 24h volume threshold
        min_liquidity: Minimum liquidity threshold
        min_score: Minimum opportunity score threshold
        as_json: If True, print JSON output

    Returns:
        List of MarketOpportunity
    """
    scanner = MarketScanner(
        min_volume=min_volume,
        min_liquidity=min_liquidity,
        min_score=min_score,
    )

    opportunities = scanner.scan(top=top)

    if as_json:
        print(json.dumps([opp.to_dict() for opp in opportunities], indent=2))
    else:
        print(format_table(opportunities))

    return opportunities


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Scan Polymarket for trading opportunities"
    )
    parser.add_argument(
        "--top", "-n",
        type=int,
        default=20,
        help="Number of top opportunities to show (default: 20)"
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        default=DEFAULT_MIN_VOLUME,
        help=f"Minimum 24h volume in USD (default: {DEFAULT_MIN_VOLUME})"
    )
    parser.add_argument(
        "--min-liquidity",
        type=float,
        default=DEFAULT_MIN_LIQUIDITY,
        help=f"Minimum liquidity in USD (default: {DEFAULT_MIN_LIQUIDITY})"
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        help=f"Minimum opportunity score (default: {DEFAULT_MIN_SCORE})"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of table"
    )
    parser.add_argument(
        "--mode", "-m",
        action="append",
        choices=["opportunities", "high-volume", "expiring"],
        help="Report mode(s). Can be used multiple times (default: opportunities)"
    )
    parser.add_argument(
        "--expiring-within",
        type=float,
        default=7.0,
        help="Expiring mode: include markets expiring within N days (default: 7)"
    )
    parser.add_argument(
        "--include-no-expiry",
        action="store_true",
        help="Expiring mode: include markets with missing expiry dates"
    )
    parser.add_argument(
        "--fetch-limit",
        type=int,
        default=500,
        help="Max markets to fetch from API (default: 500)"
    )

    args = parser.parse_args()

    print("Scanning Polymarket markets...\n")

    modes = args.mode or ["opportunities"]
    scanner = MarketScanner(
        min_volume=args.min_volume,
        min_liquidity=args.min_liquidity,
        min_score=args.min_score,
    )

    markets = scanner.fetch_active_markets(limit=args.fetch_limit)
    results: Dict[str, List[MarketOpportunity]] = {}

    if "opportunities" in modes:
        results["opportunities"] = scanner.scan_from_markets(
            markets,
            top=args.top,
        )
    if "high-volume" in modes:
        results["high_volume"] = scanner.scan_high_volume_from_markets(
            markets,
            top=args.top,
        )
    if "expiring" in modes:
        results["expiring"] = scanner.scan_expiring_from_markets(
            markets,
            top=args.top,
            within_days=args.expiring_within,
            include_no_expiry=args.include_no_expiry,
        )

    if args.json:
        if len(results) == 1:
            only_list = next(iter(results.values()))
            print(json.dumps([opp.to_dict() for opp in only_list], indent=2))
        else:
            payload = {
                key: [opp.to_dict() for opp in opps]
                for key, opps in results.items()
            }
            print(json.dumps(payload, indent=2))
        return

    display_titles = {
        "opportunities": "Top Opportunities",
        "high_volume": "High-Volume Markets (24h)",
        "expiring": f"Expiring Within {args.expiring_within:g} Days",
    }
    mode_key_map = {
        "opportunities": "opportunities",
        "high-volume": "high_volume",
        "expiring": "expiring",
    }

    if len(results) == 1:
        only_list = next(iter(results.values()))
        print(format_table(only_list))
        return

    for mode in modes:
        key = mode_key_map[mode]
        title = display_titles.get(key, key)
        print(f"{title}\n{format_table(results[key])}\n")


if __name__ == "__main__":
    main()
