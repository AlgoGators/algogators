"""Kalshi request signing (RSA-PSS/SHA256 or Ed25519 over timestamp_ms + METHOD + path).

Shim: the signer lives in ``predict_ngin.venues.kalshi.auth``. The key id and key path come from
the environment (KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY_PATH) or the repo .env. Key material, key
ids and signatures are NEVER logged or printed; ``repr`` is redacted.

Only the signer is shared. The gated trading client (``client.py``: three-gate ``LiveGate``,
one-shot mutation permit, post-only orders) stays here.
"""
from __future__ import annotations

from pathlib import Path

from predict_ngin.venues.kalshi.auth import KalshiSigner
from predict_ngin.venues.kalshi.auth import read_dotenv as _read_dotenv

ROOT = Path(__file__).resolve().parents[3]

__all__ = ["KalshiSigner", "ROOT", "_read_dotenv"]
