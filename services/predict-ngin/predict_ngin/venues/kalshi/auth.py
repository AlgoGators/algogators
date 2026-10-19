"""Kalshi request signing: RSA-PSS/SHA256 or Ed25519 over ``timestamp_ms + METHOD + path``.

The path is signed without its query string. The key id and key path come from the arguments,
the environment (``KALSHI_API_KEY_ID``, ``KALSHI_PRIVATE_KEY_PATH``) or the repo ``.env``.
Key material, key ids and signatures are never logged or printed; ``repr`` is redacted.
``cryptography`` is imported lazily, so importing this module never needs it.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from predict_ngin.shared.paths import repo_root
from predict_ngin.venues.kalshi.endpoints import WS_SIGN_PATH

__all__ = [
    "read_dotenv",
    "credentials",
    "load_private_key",
    "sign_message",
    "signed_headers",
    "KalshiSigner",
]

KEY_ID_VAR = "KALSHI_API_KEY_ID"
KEY_PATH_VAR = "KALSHI_PRIVATE_KEY_PATH"


def read_dotenv(path: Path) -> Dict[str, str]:
    """``KEY=value`` lines of a .env file (quotes stripped, comments skipped); {} if missing."""
    env: Dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def credentials(env_file: Optional[Path] = None) -> Optional[Tuple[str, str]]:
    """(key id, key path) from the environment, else from ``env_file`` (default: repo .env)."""
    env = read_dotenv(env_file or repo_root() / ".env")
    kid = os.environ.get(KEY_ID_VAR) or env.get(KEY_ID_VAR)
    path = os.environ.get(KEY_PATH_VAR) or env.get(KEY_PATH_VAR)
    if not kid or not path:
        return None
    return kid, path


def load_private_key(path):
    """Load a PEM private key (RSA or Ed25519, unencrypted)."""
    from cryptography.hazmat.primitives import serialization

    with open(os.path.expanduser(str(path)), "rb") as fh:
        return serialization.load_pem_private_key(fh.read(), password=None)


def sign_message(key, message: bytes) -> bytes:
    """RSA-PSS (MGF1-SHA256, salt = digest length) / SHA256, or Ed25519."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

    if isinstance(key, rsa.RSAPrivateKey):
        return key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
    if isinstance(key, ed25519.Ed25519PrivateKey):
        return key.sign(message)
    raise ValueError("unsupported key type")


def signed_headers(key, key_id: str, method: str, path: str, ts_ms=None) -> Dict[str, str]:
    """The three ``KALSHI-ACCESS-*`` headers for one request. ``path`` may carry a query string;
    it is not signed. ``ts_ms`` (epoch milliseconds) defaults to now."""
    ts = str(int(time.time() * 1000)) if ts_ms is None else str(int(ts_ms))
    msg = (ts + method.upper() + path.split("?", 1)[0]).encode()
    sig = sign_message(key, msg)
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "KALSHI-ACCESS-TIMESTAMP": ts,
    }


class KalshiSigner:
    """Holds one key and signs requests: ``headers(method, path)``.

    ``key_id`` / ``key_path`` default to the environment, then the repo ``.env``. A relative key
    path is taken relative to the repo root. ``key`` passes an already-loaded key instead.
    """

    def __init__(
        self,
        key_id: Optional[str] = None,
        key_path: Optional[str] = None,
        *,
        key=None,
        env_file: Optional[Path] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        if key_id is None or (key is None and key_path is None):
            found = credentials(env_file)
            if found is not None:
                key_id = key_id or found[0]
                key_path = key_path or found[1]
        if not key_id or (key is None and not key_path):
            raise RuntimeError(
                "Kalshi credentials not configured (KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PATH)"
            )
        if key is None:
            p = Path(os.path.expanduser(str(key_path)))
            if not p.is_absolute() and not p.exists():
                p = repo_root() / p
            key = load_private_key(p)
        self._kid = key_id
        self._key = key
        self._clock = clock

    def __repr__(self) -> str:  # never leak key material
        return "KalshiSigner(<redacted>)"

    def headers(self, method: str, path: str) -> Dict[str, str]:
        now = self._clock() if self._clock is not None else time.time()
        return signed_headers(self._key, self._kid, method, path, int(now * 1000))

    def ws_headers(self) -> Dict[str, str]:
        """Headers for the WebSocket handshake (``GET /trade-api/ws/v2``)."""
        return self.headers("GET", WS_SIGN_PATH)
