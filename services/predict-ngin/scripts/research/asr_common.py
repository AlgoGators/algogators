"""ASR live-mention-detection study: shared constants and the event manifest.

Every source is a public YouTube VOD of a stream that was live (live_status == was_live), so
release_timestamp (YouTube actualStartTime) anchors VOD t=0 to wall-clock time.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "vendor" / "asr"
AUDIO = OUT / "audio"
TAPE = ROOT / "data" / "vendor" / "ment5" / "tape.parquet"
YTDLP = ROOT / "venv" / "Scripts" / "yt-dlp.exe"

# event_ticker -> (youtube id, family, note). family 'fed' = FOMC press conference (Chair is the speaker);
# 'earn' = company earnings call / interview. No White House / executive-branch streams are used.
EVENTS = {
    "KXFEDMENTION-25DEC": ("-eBiSSmpRyM", "fed", "ABC News live"),
    "KXFEDMENTION-26JAN": ("78Cxs99mWwc", "fed", "CNBC Television live"),
    "KXFEDMENTION-26MAR": ("ZZqMoV-LZfE", "fed", "CNBC Television live"),
    "KXFEDMENTION-26APR": ("zKBz1WGsn6g", "fed", "CNBC Television live"),
    "KXFEDMENTION-26JUN": ("cbT4f4Bpjs0", "fed", "CNBC Television live"),
    "KXFEDMENTION-26JUL": ("qBPN5OjxKDM", "fed", "CNBC Television live"),
    "KXFEDMENTION-26SEP": ("Nkmrr7P5gW4", "fed", "CNBC Television live"),
    "KXEARNINGSMENTIONTSLA-26JUN30": ("oK0UZEE9GPo", "earn", "Tesla Q4 2025 webcast (Tesla channel live)"),
    "KXEARNINGSMENTIONTSLA-26APR22": ("qO7T5zgRvXM", "earn", "Tesla Q1 2026 webcast"),
    "KXEARNINGSMENTIONTSLA-26JUL22": ("9H5y9Uag8AA", "earn", "Tesla Q2 2026 webcast"),
    "KXEARNINGSMENTIONNFLX-26JUN30": ("GYqP8zJLYKk", "earn", "Netflix Q4 2025 interview (Netflix IR live)"),
    "KXEARNINGSMENTIONNFLX-26APR16": ("LXej1Cf4aP8", "earn", "Netflix Q1 2026 interview"),
    "KXEARNINGSMENTIONNFLX-26JUL02": ("qqGCRlldcXY", "earn", "Netflix Q2 2026 interview"),
}
# second independent live stream of the same event, used only to check the release_timestamp anchor
ANCHOR_CHECK = {
    "KXFEDMENTION-26JUL": "kLzUcirVvHE",   # Associated Press
    "KXFEDMENTION-26MAR": "rXef0jYuaYU",   # Yahoo Finance
    "KXFEDMENTION-26JUN": "2kI62fC3DXM",   # Associated Press
}

SR = 16000  # audio sample rate fed to ASR (linear16 mono)


def load_env() -> dict:
    """Minimal .env reader (values never printed or logged)."""
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def meta(ev: str) -> dict:
    return json.loads((AUDIO / f"{ev}.json").read_text())
