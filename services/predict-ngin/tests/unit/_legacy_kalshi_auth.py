"""Frozen copies of the Kalshi signers and read loops replaced in step 5.2 (2026-10-15).

Verbatim from the files named in each section, as of commit e2a25f2, except that credentials
are passed in (no .env / environment lookup). Do not edit; the golden tests compare against these.
"""
from __future__ import annotations

import base64
import time
from typing import Dict
from urllib.parse import urlparse

import requests


# ---------------------------------------------------------------- src/live/lip_pilot/auth.py
class LipPilotSigner:
    def __init__(self, kid, key):
        self._kid, self._key = kid, key

    def headers(self, method: str, path: str) -> Dict[str, str]:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
        ts = str(int(time.time() * 1000))
        msg = (ts + method.upper() + path.split("?", 1)[0]).encode()
        if isinstance(self._key, rsa.RSAPrivateKey):
            sig = self._key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                                  salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        elif isinstance(self._key, ed25519.Ed25519PrivateKey):
            sig = self._key.sign(msg)
        else:
            raise ValueError("unsupported key type")
        return {"KALSHI-ACCESS-KEY": self._kid, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}


# ---------------------------------------------------------------- src/recorder/venues/kalshi.py
WS_SIGN_PATH = "/trade-api/ws/v2"


def recorder_ws_auth_headers(key_id: str, key_path: str) -> Dict[str, str]:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

    with open(key_path, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    ts = str(int(time.time() * 1000))
    msg = (ts + "GET" + WS_SIGN_PATH).encode()
    if isinstance(key, rsa.RSAPrivateKey):
        sig = key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
    elif isinstance(key, ed25519.Ed25519PrivateKey):
        sig = key.sign(msg)
    else:
        raise ValueError("unsupported Kalshi key type")
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "KALSHI-ACCESS-TIMESTAMP": ts,
    }


# ---------------------------------------------------------------- scripts/research/kalshi_flb_api.py
FLB_HOST = "https://api.elections.kalshi.com"
FLB_BASE = "/trade-api/v2"


class FlbKalshiReadOnly:
    def __init__(self, key_id, key, session, min_interval: float = 0.11):
        self.key_id, self.key = key_id, key
        self.s = session
        self.min_interval = min_interval
        self._last = 0.0

    def _headers(self, method: str, path: str):
        ts = str(int(time.time() * 1000))
        sig = self.key.sign((ts + method + path).encode())
        return {"KALSHI-ACCESS-KEY": self.key_id,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
                "KALSHI-ACCESS-TIMESTAMP": ts}

    def get(self, endpoint: str, params=None, retries: int = 8):
        path = FLB_BASE + endpoint
        for i in range(retries):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.s.get(FLB_HOST + path, params=params, headers=self._headers("GET", path), timeout=60)
            except requests.RequestException:
                time.sleep(2 ** i); continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(60, 2 ** i)); continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"GET {endpoint} failed after {retries} retries")


# ---------------------------------------------------------------- scripts/research/fin_B_common.py
class FinBKalshiAuth:
    def __init__(self, kid, key):
        self.kid, self.key = kid, key

    def headers(self, url: str) -> dict:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
        ts = str(int(time.time() * 1000))
        msg = (ts + "GET" + urlparse(url).path).encode()
        if isinstance(self.key, rsa.RSAPrivateKey):
            sig = self.key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                                 salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        elif isinstance(self.key, ed25519.Ed25519PrivateKey):
            sig = self.key.sign(msg)
        else:
            raise ValueError("unsupported key type")
        return {"KALSHI-ACCESS-KEY": self.kid, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}


class FinBKalshiAPI:
    def __init__(self, auth, session, min_gap: float = 0.34):
        self.auth, self.s, self.min_gap, self.last = auth, session, min_gap, 0.0

    def get(self, url: str, params=None, retries: int = 10, timeout: int = 30):
        for i in range(retries):
            dt = time.time() - self.last
            if dt < self.min_gap:
                time.sleep(self.min_gap - dt)
            self.last = time.time()
            try:
                h = self.auth.headers(url) if self.auth else {}
                r = self.s.get(url, params=params, timeout=timeout, headers=h)
            except requests.RequestException:
                time.sleep(2 ** i)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(2 ** i, 30))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"failed {url} {params}")


# ---------------------------------------------------------------- scripts/research/latency_probe.py
def latency_kalshi_headers(kid, key, path: str) -> Dict[str, str]:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
    ts = str(int(time.time() * 1000))
    msg = (ts + "GET" + path.split("?")[0]).encode()
    if isinstance(key, rsa.RSAPrivateKey):
        sig = key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                        salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    elif isinstance(key, ed25519.Ed25519PrivateKey):
        sig = key.sign(msg)
    else:
        raise ValueError("unsupported key type")
    return {"KALSHI-ACCESS-KEY": kid, "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts}
