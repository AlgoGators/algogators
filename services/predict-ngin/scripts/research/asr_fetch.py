"""Download public live-stream VOD audio (yt-dlp) and convert to 16 kHz mono WAV (ffmpeg). Resumable.

Writes data/vendor/asr/audio/<event>.wav and <event>.json (release_timestamp, duration, title, channel).
"""
from __future__ import annotations

import json
import subprocess
import sys

import imageio_ffmpeg

from asr_common import ANCHOR_CHECK, AUDIO, EVENTS, SR, YTDLP

FF = imageio_ffmpeg.get_ffmpeg_exe()


def fetch(name: str, vid: str):
    wav = AUDIO / f"{name}.wav"
    js = AUDIO / f"{name}.json"
    if wav.exists() and js.exists():
        return
    url = f"https://www.youtube.com/watch?v={vid}"
    info = json.loads(subprocess.run([str(YTDLP), "-J", "--skip-download", url], capture_output=True, text=True,
                                     check=True).stdout)
    raw = AUDIO / f"{name}.raw"
    subprocess.run([str(YTDLP), "-f", "bestaudio", "-o", str(raw), "--force-overwrites", "-q", url], check=True)
    subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(raw), "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le",
                    str(wav)], check=True)
    raw.unlink()
    m = {k: info.get(k) for k in ("id", "title", "channel", "live_status", "release_timestamp", "timestamp",
                                  "duration", "upload_date")}
    js.write_text(json.dumps(m, indent=1))
    print(name, m["duration"], m["release_timestamp"], flush=True)


if __name__ == "__main__":
    AUDIO.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1:]
    for ev, (vid, fam, note) in EVENTS.items():
        if not only or ev in only:
            fetch(ev, vid)
    for ev, vid in ANCHOR_CHECK.items():
        if not only or ev in only:
            fetch(ev + "__check", vid)
