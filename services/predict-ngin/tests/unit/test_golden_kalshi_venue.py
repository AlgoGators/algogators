"""Golden equivalence tests for step 5.2 (Kalshi signing + read client -> predict_ngin.venues.kalshi).

Keys are generated in the test (never the real key). Time is frozen, so Ed25519 headers must be
byte-identical to the frozen pre-change signers in ``_legacy_kalshi_auth.py``. RSA-PSS signatures
are salted (random), so for RSA the non-signature headers must match and both signatures must
verify against the public key over the same message. The read loops are replayed against a
scripted fake session and must make the same requests, sleeps, results and errors.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "research"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _legacy_kalshi_auth as LG  # noqa: E402

from predict_ngin.shared.ratelimit import MinInterval, TokenBucket  # noqa: E402
from predict_ngin.venues.kalshi import (  # noqa: E402
    BASE_URL,
    WS_SIGN_PATH,
    KalshiHTTPError,
    KalshiReadClient,
    KalshiRetriesExhausted,
    KalshiSigner,
    ReadOnlyViolation,
    signed_headers,
)

T_FIXED = 1792022400.123456
KID = "test-key-id-0000"
PATHS = ["/trade-api/v2/portfolio/balance", "/trade-api/v2/markets/KXFED-26DEC-T4.25/orderbook",
         "/trade-api/v2/markets?limit=1&status=open", WS_SIGN_PATH]


def _pem(key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
    d = tmp_path_factory.mktemp("kalshi_keys")
    out = {}
    for name, k in (("ed25519", ed25519.Ed25519PrivateKey.generate()),
                    ("rsa", rsa.generate_private_key(public_exponent=65537, key_size=2048))):
        p = d / f"{name}.pem"
        p.write_bytes(_pem(k))
        out[name] = (k, p)
    return out


@pytest.fixture
def frozen(monkeypatch):
    sleeps = []
    clock = {"t": T_FIXED}

    def fake_time():
        return clock["t"]

    def fake_sleep(s):
        sleeps.append(round(s, 9))
        clock["t"] += s

    monkeypatch.setattr(time, "time", fake_time)
    monkeypatch.setattr(time, "sleep", fake_sleep)
    return sleeps, clock


@pytest.fixture
def env_key(monkeypatch, keys):
    k, p = keys["ed25519"]
    monkeypatch.setenv("KALSHI_API_KEY_ID", KID)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(p))
    return k, p


def _verify_rsa(key, headers, method, path):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    msg = (headers["KALSHI-ACCESS-TIMESTAMP"] + method + path.split("?")[0]).encode()
    key.public_key().verify(base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]), msg,
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                            hashes.SHA256())


# ------------------------------------------------------------------------------------ signers
def test_ed25519_headers_identical_to_every_legacy_signer(frozen, keys, env_key, monkeypatch):
    from src.live.lip_pilot.auth import KalshiSigner as LipSigner
    from src.recorder.venues.kalshi import ws_auth_headers
    FB = pytest.importorskip("fin_B_common")  # private module: skipped in a public clone
    KA = pytest.importorskip("kalshi_flb_api")  # private module: skipped in a public clone
    LP = pytest.importorskip("latency_probe")  # private module: skipped in a public clone
    k, p = keys["ed25519"]
    new = KalshiSigner(KID, str(p))
    lip_new, lip_old = LipSigner(KID, str(p)), LG.LipPilotSigner(KID, k)
    for path in PATHS:
        for method in ("GET", "get", "POST", "DELETE"):
            assert lip_new.headers(method, path) == lip_old.headers(method, path)
            assert new.headers(method, path) == lip_old.headers(method, path)
            assert signed_headers(k, KID, method, path, int(T_FIXED * 1000)) == lip_old.headers(method, path)
    # recorder WebSocket handshake
    assert ws_auth_headers(KID, str(p)) == LG.recorder_ws_auth_headers(KID, str(p))
    assert new.ws_headers() == LG.recorder_ws_auth_headers(KID, str(p))
    # kalshi_flb_api.KalshiReadOnly (credentials from .env -> patched to the test key)
    monkeypatch.setattr(KA, "_load_env", lambda: ({"KALSHI_API_KEY_ID": KID, "KALSHI_PRIVATE_KEY_PATH": str(p)}, ROOT))
    ro_new = KA.KalshiReadOnly()
    ro_old = LG.FlbKalshiReadOnly(KID, k, None)
    for path in PATHS[:2]:
        assert ro_new._headers("GET", path) == ro_old._headers("GET", path)
        assert ro_new.signer.headers("GET", path) == ro_old._headers("GET", path)
    # fin_B_common.KalshiAuth (credentials from the environment)
    fb_new, fb_old = FB.KalshiAuth(), LG.FinBKalshiAuth(KID, k)
    for path in PATHS:
        url = "https://api.elections.kalshi.com" + path
        assert fb_new.headers(url) == fb_old.headers(url)
    # latency_probe.kalshi_headers (key cached in a module global)
    monkeypatch.delattr(LP, "_KEY", raising=False)
    for path in PATHS:
        assert LP.kalshi_headers(path) == LG.latency_kalshi_headers(KID, k, path)


def test_rsa_pss_headers_match_and_verify(frozen, keys):
    from src.live.lip_pilot.auth import KalshiSigner as LipSigner
    from src.recorder.venues.kalshi import ws_auth_headers

    k, p = keys["rsa"]
    new, old = LipSigner(KID, str(p)), LG.LipPilotSigner(KID, k)
    for path in PATHS:
        for method in ("GET", "DELETE"):
            a, b = new.headers(method, path), old.headers(method, path)
            assert {h: v for h, v in a.items() if h != "KALSHI-ACCESS-SIGNATURE"} == \
                   {h: v for h, v in b.items() if h != "KALSHI-ACCESS-SIGNATURE"}
            _verify_rsa(k, a, method, path)
            _verify_rsa(k, b, method, path)
    a, b = ws_auth_headers(KID, str(p)), LG.recorder_ws_auth_headers(KID, str(p))
    assert a.keys() == b.keys() and a["KALSHI-ACCESS-TIMESTAMP"] == b["KALSHI-ACCESS-TIMESTAMP"]
    _verify_rsa(k, a, "GET", WS_SIGN_PATH)


def test_signer_query_string_not_signed_and_repr_redacted(keys):
    k, p = keys["ed25519"]
    s = KalshiSigner(KID, str(p))
    a = signed_headers(k, KID, "GET", "/trade-api/v2/markets?limit=1", 1)
    b = signed_headers(k, KID, "GET", "/trade-api/v2/markets", 1)
    assert a == b
    assert KID not in repr(s) and "redacted" in repr(s)


def test_signer_credentials(monkeypatch, tmp_path, keys):
    _, p = keys["ed25519"]
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        KalshiSigner(env_file=tmp_path / "missing.env")
    env = tmp_path / "x.env"
    env.write_text(f'KALSHI_API_KEY_ID="{KID}"\nKALSHI_PRIVATE_KEY_PATH={p}\n# comment=1\n')
    assert KalshiSigner(env_file=env).headers("GET", "/x")["KALSHI-ACCESS-KEY"] == KID


# ------------------------------------------------------------------------------------ read client
class FakeSession:
    """Scripted session: each item is a status code, (status, body, headers) or an exception."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, json.dumps(params, sort_keys=True), dict(headers or {}), timeout))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body, hdr = item if isinstance(item, tuple) else (item, {"ok": item}, {})
        r = requests.Response()
        r.status_code, r._content, r.url = status, json.dumps(body).encode(), url
        r.headers.update(hdr)
        r.reason = "x"
        return r

    def post(self, *a, **k):  # pragma: no cover - must never be called
        raise AssertionError("POST through a read-only client")


SCRIPTS = {
    "ok": [200],
    "retry_then_ok": [requests.ConnectionError("down"), 429, 502, (429, {}, {"Retry-After": "7"}), 200],
    "exhausted": [500] * 8,
    "not_found": [404],
    "forbidden": [503, 403],
}


def _run(fn):
    try:
        return ("ok", fn())
    except Exception as e:  # noqa: BLE001
        return (type(e).__name__, str(e))


@pytest.mark.parametrize("script", list(SCRIPTS))
def test_kalshi_read_only_shim_replays_legacy(frozen, keys, monkeypatch, script):
    KA = pytest.importorskip("kalshi_flb_api")  # private module: skipped in a public clone
    sleeps, clock = frozen
    k, p = keys["ed25519"]
    monkeypatch.setattr(KA, "_load_env", lambda: ({"KALSHI_API_KEY_ID": KID, "KALSHI_PRIVATE_KEY_PATH": str(p)}, ROOT))
    results = []
    for build in ("old", "new"):
        sleeps.clear()
        clock["t"] = T_FIXED
        sess = FakeSession(SCRIPTS[script])
        if build == "old":
            c = LG.FlbKalshiReadOnly(KID, k, sess, min_interval=0.25)
        else:
            c = KA.KalshiReadOnly(min_interval=0.25)
            c.session = c.s = sess
        out = [_run(lambda: c.get("/markets", {"limit": 5, "status": "open"})),
               _run(lambda: c.get("/markets/KX-1", None, retries=3))] if script == "ok" else \
              [_run(lambda: c.get("/markets", {"limit": 5}))]
        results.append((out, list(sleeps), sess.calls))
    assert results[0] == results[1]


@pytest.mark.parametrize("script", list(SCRIPTS))
def test_fin_b_kalshi_api_replays_legacy(frozen, keys, env_key, script):
    FB = pytest.importorskip("fin_B_common")  # private module: skipped in a public clone
    sleeps, clock = frozen
    k, _ = keys["ed25519"]
    results = []
    for build in ("old", "new"):
        for auth in (True, False):
            sleeps.clear()
            clock["t"] = T_FIXED
            sess = FakeSession(SCRIPTS[script] * 2)
            if build == "old":
                c = LG.FinBKalshiAPI(LG.FinBKalshiAuth(KID, k) if auth else None, sess, 0.34)
            else:
                c = FB.KalshiAPI(0.34, auth=auth)
                c.s = c._client.session = sess
            url = BASE_URL + "/markets/KX-1/orderbook"
            out = _run(lambda: c.get(url, {"depth": 3}, retries=8))
            results.append((build, auth, out, list(sleeps), sess.calls))
    half = len(results) // 2
    for a, b in zip(results[:half], results[half:]):
        assert a[1:] == b[1:]


def test_read_client_is_get_only():
    c = KalshiReadClient(session=FakeSession([200]), limiter=MinInterval(0))
    for m in ("POST", "PUT", "DELETE", "PATCH", "post"):
        with pytest.raises(ReadOnlyViolation):
            c.request(m, "/portfolio/orders")
    assert not any(hasattr(c, m) for m in ("post", "put", "delete", "patch"))
    assert c.request("GET", "/exchange/status") == {"ok": 200}
    assert c.session.calls[0][0] == BASE_URL + "/exchange/status"


def test_read_client_retries_backoff_auth_and_errors(keys):
    k, p = keys["ed25519"]
    slept = []
    signer = KalshiSigner(KID, str(p), clock=lambda: 1.0)
    sess = FakeSession([requests.Timeout("t"), (429, {}, {"Retry-After": "90"}), 500, 200, 404, 400, 503, 503])
    c = KalshiReadClient(signer=signer, session=sess, limiter=MinInterval(0), sleep=slept.append,
                         none_on=frozenset({404}), retries=4)
    assert c.get("/markets?x=1", {"limit": 1}, auth=True) == {"ok": 200}
    assert slept == [1.0, 90.0, 4.0]                       # 2**0, Retry-After, 2**2
    assert c.n_requests == 4 and c.n_retries == 3 and c.n_429 == 1
    assert sess.calls[-1][2] == signed_headers(k, KID, "GET", "/trade-api/v2/markets", 1000)
    assert c.get("/missing") is None
    assert "KALSHI-ACCESS-KEY" not in sess.calls[-1][2]    # unauthenticated by default
    with pytest.raises(requests.HTTPError):
        c.get("/bad")
    with pytest.raises(KalshiRetriesExhausted, match="failed after 2 retries"):
        c.get("/busy", retries=2)
    with pytest.raises(RuntimeError, match="without a signer"):
        KalshiReadClient(session=FakeSession([])).get("/portfolio/balance", auth=True)


def test_read_client_http_error_fallback_and_pagination():
    class NoRaise:
        status_code, headers, text = 418, {}, "teapot"

        def raise_for_status(self):
            return None

    class S(FakeSession):
        def get(self, *a, **k):
            return NoRaise()

    with pytest.raises(KalshiHTTPError):
        KalshiReadClient(session=S([]), limiter=MinInterval(0)).get("/x")
    pages = [(200, {"markets": [1, 2], "cursor": "c1"}, {}), (200, {"markets": [3], "cursor": ""}, {})]
    c = KalshiReadClient(session=FakeSession(pages), limiter=MinInterval(0))
    assert list(c.paginate("/markets", "markets", {"limit": 2})) == [1, 2, 3]
    assert json.loads(c.session.calls[1][1]) == {"cursor": "c1", "limit": 2}


def test_lip_pilot_token_bucket_is_the_shared_one():
    from src.live.lip_pilot.ratelimit import TokenBucket as LipBucket
    assert LipBucket is TokenBucket
    t = {"now": 0.0}
    waits = []
    b = TokenBucket(5.0, 5.0, clock=lambda: t["now"], sleep=lambda s: (waits.append(s), t.__setitem__("now", t["now"] + s)))
    for _ in range(7):
        b.acquire()
    assert waits == pytest.approx([0.2, 0.2])


def test_base_url_defined_once():
    CD = pytest.importorskip("capfwd_data")  # private module: skipped in a public clone
    FB = pytest.importorskip("fin_B_common")  # private module: skipped in a public clone
    KA = pytest.importorskip("kalshi_flb_api")  # private module: skipped in a public clone
    from src.live.lip_pilot.config import ApiCfg
    from src.recorder.venues import kalshi as RK
    sys.path.insert(0, str(ROOT / "scripts" / "live"))
    import market_feature_watch as MFW
    PCP = pytest.importorskip("perps_carry_paper")  # private module: skipped in a public clone
    assert BASE_URL == "https://api.elections.kalshi.com/trade-api/v2"
    assert CD.API == FB.KALSHI == RK.API == MFW.BASE == PCP.KALSHI == KA.HOST + KA.BASE == BASE_URL
    assert ApiCfg().base_url == BASE_URL
    assert RK.WS_URL == "wss://external-api-ws.kalshi.com/trade-api/ws/v2" and RK.WS_SIGN_PATH == WS_SIGN_PATH
