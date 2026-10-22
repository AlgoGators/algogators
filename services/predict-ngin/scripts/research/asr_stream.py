"""Stream a WAV file at 1x real time into Deepgram (nova-3) or AssemblyAI (universal streaming v3) and log every
message with its wall-clock arrival time. Never logs keys.

Usage: asr_stream.py <event_ticker> <dg|aai> [--seconds N] [--start S] [--tag T]
Output: data/vendor/asr/stream/<event>__<provider><tag>.jsonl  (first line = header with t0_wall)
Each audio chunk i (100 ms) is sent at wall t0 + i*0.1, so audio time a is "spoken" at wall t0 + a (minus start).
Resumable at event level: skips if the output already ends with a 'done' trailer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.parse
import wave

import pandas as pd
import websockets

from asr_common import AUDIO, OUT, ROOT, SR, load_env
from asr_words import keyterms

CHUNK_S = 0.1


def event_titles(ev: str) -> list[str]:
    f = "api_earnmention_markets" if "EARNINGS" in ev else "api_othermention_markets"
    m = pd.read_parquet(ROOT / "data/vendor/fin_D" / f"{f}.parquet", columns=["event_ticker", "yes_sub_title"])
    return m[m.event_ticker == ev].yes_sub_title.tolist()


def url_for(provider: str, kts: list[str]) -> str:
    if provider == "dg":
        q = [("model", "nova-3"), ("encoding", "linear16"), ("sample_rate", SR), ("channels", 1),
             ("interim_results", "true"), ("punctuate", "true"), ("diarize", "true"), ("endpointing", "300"),
             ("language", "en")]
        q += [("keyterm", k) for k in kts[:60]]
        return "wss://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(q)
    q = [("sample_rate", SR), ("encoding", "pcm_s16le"), ("format_turns", "false"),
         ("keyterms_prompt", json.dumps(kts[:60]))]
    return "wss://streaming.assemblyai.com/v3/ws?" + urllib.parse.urlencode(q)


def compact(provider, msg):
    if provider == "dg":
        if msg.get("type") != "Results":
            return {"type": msg.get("type")}
        alt = msg["channel"]["alternatives"][0]
        return {"type": "R", "fin": msg.get("is_final"), "sf": msg.get("speech_final"), "st": msg.get("start"),
                "du": msg.get("duration"),
                "w": [[x["word"], x["start"], x["end"], round(x.get("confidence", 0), 3), x.get("speaker")]
                      for x in alt.get("words", [])]}
    if msg.get("type") != "Turn":
        return {"type": msg.get("type"), **({"err": str(msg.get("error"))} if "error" in msg else {})}
    return {"type": "T", "to": msg.get("turn_order"), "eot": msg.get("end_of_turn"),
            "w": [[x["text"], x["start"] / 1000, x["end"] / 1000, round(x.get("confidence", 0), 3),
                   x.get("word_is_final")] for x in msg.get("words", [])]}


async def run(ev, provider, seconds, start, tag):
    outd = OUT / "stream"
    outd.mkdir(parents=True, exist_ok=True)
    out = outd / f"{ev}__{provider}{tag}.jsonl"
    if out.exists() and '"done"' in out.read_text()[-300:]:
        print("skip", out.name)
        return
    env = load_env()
    key = env["DEEPGRAM_API_KEY"] if provider == "dg" else env["ASSEMBLYAI_API_KEY"]
    hdr = {"Authorization": f"Token {key}" if provider == "dg" else key}
    kts = keyterms(event_titles(ev))
    with wave.open(str(AUDIO / f"{ev}.wav")) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        w.setpos(int(start * SR))
        n_total = w.getnframes() - int(start * SR)
        if seconds:
            n_total = min(n_total, int(seconds * SR))
        per = int(CHUNK_S * SR)
        with open(out, "w") as f:
            async with websockets.connect(url_for(provider, kts), additional_headers=hdr, max_size=2**24,
                                          ping_interval=20) as ws:
                state = {"sent_s": 0.0}
                t0 = time.time()
                f.write(json.dumps({"type": "hdr", "event": ev, "provider": provider, "t0_wall": t0, "start_s": start,
                                    "keyterms": kts}) + "\n")

                async def sender():
                    i = 0
                    sent = 0
                    while sent < n_total:
                        k = min(per, n_total - sent)
                        await ws.send(w.readframes(k))
                        sent += k
                        state["sent_s"] = sent / SR
                        i += 1
                        await asyncio.sleep(max(0.0, t0 + i * CHUNK_S - time.time()))
                    await asyncio.sleep(1.0)
                    await ws.send(json.dumps({"type": "CloseStream"} if provider == "dg" else {"type": "Terminate"}))

                async def receiver():
                    async for raw in ws:
                        now = time.time()
                        rec = compact(provider, json.loads(raw))
                        rec["rw"] = round(now, 3)
                        rec["sent"] = round(state["sent_s"], 2)
                        f.write(json.dumps(rec) + "\n")
                        f.flush()

                st = asyncio.create_task(sender())
                try:
                    await receiver()
                except websockets.ConnectionClosed as e:
                    print("closed", e.code)
                # Finish sending before the WAV and transcript handles close.
                await asyncio.gather(st)
            f.write(json.dumps({"type": "done", "audio_s": n_total / SR, "wall_s": time.time() - t0}) + "\n")
    with open(OUT / "minutes_log.jsonl", "a") as g:
        g.write(json.dumps({"event": ev, "provider": provider, "tag": tag, "audio_min": round(n_total / SR / 60, 2),
                            "ts": time.time()}) + "\n")
    print("done", ev, provider, round(n_total / SR / 60, 1), "min")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("event")
    ap.add_argument("provider", choices=["dg", "aai"])
    ap.add_argument("--seconds", type=float, default=0)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    asyncio.run(run(a.event, a.provider, a.seconds, a.start, a.tag))
