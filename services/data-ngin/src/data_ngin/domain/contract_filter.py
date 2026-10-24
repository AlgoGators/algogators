"""
Domain logic for filtering and parsing outright futures contracts.
Ensures calendar spreads, options, and multi-leg strategies are excluded
per AlgoGators/algogators#37.
"""

import re
from typing import Optional, Tuple

MONTH_CODES = set("FGHJKMNQUVXZ")

# Matches standard futures outright symbol: e.g. ESZ24, CLV5, 6JU0, ZBU2024
OUTRIGHT_SYMBOL_REGEX = re.compile(r"^([A-Z0-9]+?)([FGHJKMNQUVXZ])(\d{1,4})$")


def is_outright_contract(symbol: str, instrument_class: Optional[str] = "F") -> bool:
    """
    Determine if a given contract symbol and instrument class represents an outright future.

    Args:
        symbol: The raw contract symbol (e.g. 'ESZ4', 'CLZ5-CLF6').
        instrument_class: The Databento instrument class ('F' for Future, 'S' for Spread, etc.).

    Returns:
        True if the contract is an outright future, False if it is a spread or other derivative.
    """
    if not symbol:
        return False

    # Check instrument class: only outright futures ('F') are allowed
    if instrument_class is not None and instrument_class != "F":
        return False

    # Disqualify multi-leg symbols containing spread separators
    if "-" in symbol or ":" in symbol or "_" in symbol or " " in symbol:
        return False

    # Symbol must match outright contract pattern (root + month code + year)
    match = OUTRIGHT_SYMBOL_REGEX.match(symbol)
    if not match:
        return False

    return True


def parse_contract_symbol(symbol: str) -> Tuple[str, str, str]:
    """
    Parse an outright futures symbol into root, month code, and year.

    Args:
        symbol: E.g. 'ESZ24', 'CLV5', '6JU0'.

    Returns:
        Tuple of (root, month_code, year).

    Raises:
        ValueError: If symbol does not match an outright contract pattern.
    """
    match = OUTRIGHT_SYMBOL_REGEX.match(symbol)
    if not match:
        raise ValueError(f"Invalid outright futures symbol: '{symbol}'")
    return match.group(1), match.group(2), match.group(3)
