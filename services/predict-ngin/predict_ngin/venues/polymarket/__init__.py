"""Polymarket (international): URLs and a read-only Gamma / CLOB / Data API client.
"""

from predict_ngin.venues.polymarket.client import PolymarketReadClient
from predict_ngin.venues.polymarket.endpoints import CLOB_URL, DATA_API_URL, GAMMA_URL

__all__ = ["GAMMA_URL", "CLOB_URL", "DATA_API_URL", "PolymarketReadClient"]
