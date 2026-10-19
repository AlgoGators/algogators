"""ASR live paper harness for Kalshi mention markets (A3). READ-ONLY: never places, cancels or signs orders.

  stream URL (YouTube live / HLS / any ffmpeg input)  --or--  --wav <file> (dry run, paced at 1x)
        -> 16 kHz mono PCM -> Deepgram nova-3 streaming (interim results, diarization, contract keyterms)
        -> asr_words.Detector (same grammar/speaker code as the offline replay)
        -> JSONL log: ASR messages, detections, and the Kalshi public book polled ~1 s (read-only REST)

Usage:
  python asr_live.py --event KXFEDMENTION-26OCT --url "https://www.youtube.com/watch?v=..." [--fed] [--minutes 90]
  python asr_live.py --event KXFEDMENTION-26SEP --wav data/vendor/asr/audio/KXFEDMENTION-26SEP.wav --minutes 5
  python asr_live.py --score data/vendor/asr/live/<file>.jsonl      # paper evaluation of a finished log

Kalshi calls (public, no auth, <= ~1.3 req/s): GET /markets?event_ticker=  every --poll s (top of book for every
market of the event), plus GET /markets/{ticker}/orderbook once on each detection.
The viewer delay of the chosen stream is NOT removed: what this logs is exactly what a live trader would see.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
import sys
import time
import urllib.parse
import wave
from pathlib import Path

import requests
import websockets

from asr_common import OUT, SR, YTDLP, load_env
from asr_words import Detector, keyterms, norm_tok

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"User-Agent": "Mozilla/5.0 (research; read-only)"}
CHUNK = int(0.1 * SR) * 2  # 100 ms of s16le


def fetch_markets(event):
    r = requests.get(f"{KALSHI}/markets", params={"event_ticker": event, "limit": 200}, headers=UA, timeout=10)
    r.raise_for_status()
    return r.json().get("markets", [])


def top(m):
    def f(k):
        v = m.get(k)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    return {"t": m["ticker"], "yb": f("yes_bid_dollars"), "ya": f("yes_ask_dollars"),
            "yas": f("yes_ask_size_fp"), "ybs": f("yes_bid_size_fp"), "lp": f("last_price_dollars"),
            "vol": f("volume_fp"), "st": m.get("status")}


def audio_proc(url):
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    if "youtube.com" in url or "youtu.be" in url:
        src = subprocess.run([str(YTDLP), "-g", "-f", "bestaudio/best", url], capture_output=True, text=True,
                             check=True).stdout.strip().splitlines()[0]
    else:
        src = url
    return subprocess.Popen([ff, "-loglevel", "error", "-re" if not src.startswith("http") else "-nostdin", "-i",
                             src, "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"], stdout=subprocess.PIPE)


async def live(args):
    env = load_env()
    mk = fetch_markets(args.event)
    titles = {m["ticker"]: m.get("yes_sub_title") or m.get("subtitle") or "" for m in mk}
    kts = keyterms(titles.values())
    det = Detector(titles, speaker_filter=args.fed)
    outd = OUT / "live"
    outd.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else outd / f"{args.event}__{time.strftime('%Y%m%dT%H%M%S')}.jsonl"
    f = open(out, "a")

    def log(rec):
        f.write(json.dumps(rec) + "\n")
        f.flush()

    q = [("model", "nova-3"), ("encoding", "linear16"), ("sample_rate", SR), ("channels", 1),
         ("interim_results", "true"), ("punctuate", "true"), ("diarize", "true"), ("endpointing", "300")]
    q += [("keyterm", k) for k in kts[:60]]
    url = "wss://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(q)
    hdr = {"Authorization": f"Token {env['DEEPGRAM_API_KEY']}"}
    deadline = time.time() + args.minutes * 60
    log({"type": "hdr", "event": args.event, "source": args.url or args.wav, "fed": args.fed, "t_start": time.time(),
         "titles": titles, "keyterms": kts, "dry_run": bool(args.wav)})

    async with websockets.connect(url, additional_headers=hdr, max_size=2**24, ping_interval=20) as ws:
        t0 = time.time()
        state = {"sent": 0.0, "stop": False}
        log({"type": "t0", "t0_wall": t0})

        async def sender():
            if args.wav:
                w = wave.open(args.wav)
                w.setpos(int(args.wav_start * SR))
                i = 0
                while time.time() < deadline:
                    b = w.readframes(CHUNK // 2)
                    if not b:
                        break
                    await ws.send(b)
                    i += 1
                    state["sent"] = i * 0.1
                    await asyncio.sleep(max(0.0, t0 + i * 0.1 - time.time()))
            else:
                p = audio_proc(args.url)
                loop = asyncio.get_running_loop()
                while time.time() < deadline:
                    b = await loop.run_in_executor(None, p.stdout.read, CHUNK)
                    if not b:
                        break
                    await ws.send(b)
                    state["sent"] += len(b) / 2 / SR
                p.kill()
            state["stop"] = True
            await asyncio.sleep(1.5)
            await ws.send(json.dumps({"type": "CloseStream"}))

        async def receiver():
            async for raw in ws:
                rw = time.time()
                m = json.loads(raw)
                if m.get("type") != "Results":
                    continue
                alt = m["channel"]["alternatives"][0]
                words = [(norm_tok(x["word"]), x["start"], x["end"], x.get("speaker")) for x in alt.get("words", [])]
                fin = bool(m.get("is_final"))
                log({"type": "asr", "rw": round(rw, 3), "sent": round(state["sent"], 2), "fin": fin,
                     "w": [list(x) for x in words]})
                for d in det.feed(rw, fin, words):
                    d["type"] = "det"
                    d["asr_lat"] = round(rw - (t0 + d["e"]), 3) if args.wav else None
                    d["title"] = titles.get(d["ticker"])
                    log(d)
                    print(f"DETECT {d['policy']} {d['ticker']} '{d['title']}' at audio {d['s']:.1f}s", flush=True)
                    if d["policy"] == "int":
                        asyncio.get_running_loop().run_in_executor(None, snap_book, d["ticker"], log)

        async def poller():
            while not state["stop"]:
                ts = time.time()
                try:
                    ms = await asyncio.get_running_loop().run_in_executor(None, fetch_markets, args.event)
                    log({"type": "book", "rw": round(ts, 3), "rw_done": round(time.time(), 3),
                         "m": [top(m) for m in ms]})
                except Exception as e:  # noqa: BLE001
                    log({"type": "book_err", "rw": ts, "err": str(e)[:200]})
                await asyncio.sleep(max(0.0, args.poll - (time.time() - ts)))

        await asyncio.gather(sender(), receiver(), poller())
    log({"type": "done", "t_end": time.time(), "audio_s": state["sent"]})
    with open(OUT / "minutes_log.jsonl", "a") as g:
        g.write(json.dumps({"event": args.event, "provider": "dg", "tag": "live" if args.url else "dryrun",
                            "audio_min": round(state["sent"] / 60, 2), "ts": time.time()}) + "\n")
    print("log:", out)


def snap_book(ticker, log):
    try:
        r = requests.get(f"{KALSHI}/markets/{ticker}/orderbook", headers=UA, timeout=10)
        log({"type": "ob", "ticker": ticker, "rw": round(time.time(), 3), "ob": r.json()})
    except Exception as e:  # noqa: BLE001
        log({"type": "ob_err", "ticker": ticker, "err": str(e)[:200]})


def taker_fee(p, c):
    return math.ceil(0.07 * c * p * (1 - p) * 100 - 1e-9) / 100


def score(path, order_lat=1.0, max_ctr=1000):
    """Paper evaluation: for each 'int' detection at wall D, buy YES at the first polled yes_ask at or after
    D + order_lat, size = displayed ask size (capped), only if the book has not already repriced (ask < 0.90).
    Lead = first poll time the ask is >= 0.90 minus D. Settlement fetched from Kalshi (public)."""
    rows = [json.loads(x) for x in Path(path).read_text().splitlines()]
    hdr = rows[0]
    books = [r for r in rows if r["type"] == "book"]
    dets = [r for r in rows if r["type"] == "det" and r["policy"] == "int"]
    res = {m["ticker"]: m.get("result") for m in fetch_markets(hdr["event"])}
    out = []
    for d in dets:
        D = d["rw"] + order_lat
        snaps = [(b["rw_done"], next((m for m in b["m"] if m["t"] == d["ticker"]), None)) for b in books]
        snaps = [(t, m) for t, m in snaps if m]
        pre = [m for t, m in snaps if t <= d["rw"]]
        after = [(t, m) for t, m in snaps if t >= D]
        fill = after[0] if after else None
        rep = next((t for t, m in snaps if t >= d["rw"] - 120 and (m["ya"] or 0) >= 0.90), None)
        r = {"ticker": d["ticker"], "title": d["title"], "D": D, "result": res.get(d["ticker"]),
             "ask_before": pre[-1]["ya"] if pre else None, "lead_s": (rep - D) if rep else None}
        if fill and fill[1]["ya"] is not None and fill[1]["ya"] < 0.90 and r["result"] in ("yes", "no"):
            a = fill[1]["ya"]
            c = min(fill[1]["yas"] or 0, max_ctr)
            r.update(fill_ask=a, ctr=c, pnl=c * ((r["result"] == "yes") - a) - taker_fee(a, c) if c else 0.0)
        out.append(r)
    for r in out:
        print(json.dumps(r))
    tot = sum(r.get("pnl", 0) or 0 for r in out)
    print(json.dumps({"detections": len(out), "paper_pnl": round(tot, 2),
                      "note": "book polled ~1 s; top-of-book size only; dry runs on settled events have no live book"}))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--event")
    ap.add_argument("--url")
    ap.add_argument("--wav")
    ap.add_argument("--fed", action="store_true", help="count only the running-majority speaker (FOMC Chair)")
    ap.add_argument("--minutes", type=float, default=120)
    ap.add_argument("--poll", type=float, default=1.0)
    ap.add_argument("--wav-start", type=float, default=0.0, help="dry run: seconds into the WAV to start")
    ap.add_argument("--out")
    ap.add_argument("--score")
    a = ap.parse_args()
    if a.score:
        score(a.score)
        sys.exit(0)
    if not a.event or not (a.url or a.wav):
        ap.error("--event and one of --url/--wav are required")
    asyncio.run(live(a))
