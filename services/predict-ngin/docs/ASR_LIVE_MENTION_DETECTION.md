# ASR: live speech-to-text word detection vs Kalshi mention-market repricing

Last revised: 2026-10-16

Round 6, 2026-10-16. Backlog item A3. **Paper test only: no orders.** Scripts are in `scripts/research/asr_*.py`;
outputs go to `data/vendor/asr/`. This builds on docs/MHAZ_IN_EVENT_HAZARD.md, which found:
- prices reprice 6-26 s (median) after a word is said;
- 90-250 taker-NO contracts trade in the 120 s before a YES word's jump.

## Question

Can a streaming ASR pipeline hear a contract word and act on it before the Kalshi market reprices? If so, how much
stale size is there to take after fees? The pipeline is public live stream -> Deepgram/AssemblyAI -> grammar match
-> order.

## Pre-registration (written 2026-10-16 before any lead, alignment or P&L number was computed)

So far I have looked at stream metadata and the release-anchor cross-check (below), and at nothing market-side.

### Events (fixed before any result)

Public YouTube VODs of streams that were live (`live_status = was_live`). No login and no paywall. Downloaded with
yt-dlp and converted to 16 kHz mono with the ffmpeg bundled in imageio-ffmpeg. The manifest is in `asr_common.py`.

| event | source |
|---|---|
| KXFEDMENTION 25DEC | ABC News live |
| KXFEDMENTION 26JAN, 26MAR, 26APR, 26JUN, 26JUL, 26SEP | CNBC Television live |
| KXEARNINGSMENTIONTSLA 26JUN30 (Q4-25), 26APR22 (Q1-26), 26JUL22 (Q2-26) | Tesla's own channel live |
| KXEARNINGSMENTIONNFLX 26JUN30 (Q4-25), 26APR16 (Q1-26), 26JUL02 (Q2-26) | Netflix IR live |

- That is 13 events: 7 FOMC and 6 earnings.
- No White House, executive-branch or other MNPI-family streams were used, so there is no separate compliance
  section.
- Sports were skipped: no rights-clean public replays.

### ASR (measured, not assumed)

- Each WAV is streamed at 1x real time in 100 ms chunks. Chunk i is sent at wall time t0 + 0.1 i.
- Every response message is logged with its wall-clock arrival time.
- **Deepgram** (all 13 events): nova-3, interim_results, diarize, endpointing 300 ms. Keyterm prompting uses the
  event's contract words, which are known before the event.
- **AssemblyAI** universal-streaming v3 (3 events: FOMC 26JUL, TSLA 26JUL22, NFLX 26APR16): keyterms_prompt with
  the same words.
- **Word latency**:
  - interim latency = arrival of the first message containing the word − (t0 + word end);
  - final latency = the same for the first final message containing it.

### Grammar (rules text)

The rule text reads: "exact phrase/word, or a plural or possessive form ... Grammatical/tense inflections are otherwise
not included."
- `yes_sub_title` alternatives are split on " / ".
- "(N+ times)" sets a count threshold.
- On the last token only, the plural or possessive forms s / es / 's / s' / y->ies are allowed.
- Hyphenated compounds become token sequences. An ASR split ("super charger") or join ("shutdown") of a target also
  matches.
- **Speaker**:
  - FOMC: the contract counts only the Chair. The Chair is taken to be the diarized speaker with the most words in
    the first 180 s after the first speech, which is known live because the introductory statement comes first.
    Only Chair words count.
  - Earnings: the contract counts "any company representative (including the operator)". All speakers count, and
    analyst and interviewer speech is reported as a false-positive source.

### Detection policies

- **P-int** (primary): fire on the first interim or final message whose running token list (finals plus the
  current interim) reaches the count threshold.
- **P-fin**: the same, but on final messages only.

### Market clock (tape, `data/vendor/ment5/tape.parquet`)

- p0 = the last print before the event start (first ASR speech, wall time).
- Words with p0 >= 0.90 at the start are "already priced" and excluded from the lead and P&L.
- **J_move** = the first print at or above max(p0 + 0.15, 0.50) after the start. This is the market's first visible
  reaction and the primary jump time.
- **Jh** = the sustained jump: the first print >= 0.95 with no later print < 0.90 (as in mhaz). Secondary.

### Audio-to-market alignment

- **Primary, absolute**: spoken wall time = release_timestamp (YouTube actualStartTime) + audio time.
  - Not identified exactly. The broadcaster's own delay to YouTube ingest makes this estimate late, which biases
    the lead DOWN (conservative). A VOD trimmed at the start would bias it the other way.
  - Validated by:
    (a) the Chair's "good afternoon" vs the 14:30:00 ET scheduled start;
    (b) earnings speech start vs the scheduled call time;
    (c) the market jump of the eventual-YES words that are said only once (the jump cannot come before the word,
        unless anticipated).
- **LOO** (as specified): offset_i = median over j != i of (J_move_j − spoken_j). Its residual (J_move_i − spoken_i −
  offset_i) gives the per-word timing spread.
  - LOO cannot identify the absolute market lag: it centres every event's lead at the event's median lag. It is
    reported for spread only.

### Lead and paper P&L

- Detection wall time D = spoken + ASR latency (measured, per policy) + viewer delay V + 1 s order latency.
- V = 5 s is primary: a low-latency HLS/YouTube LL feed. V = 0 (broadcaster ingest) and V = 15 s (a normal HLS
  player) are sensitivities.
- **lead** = J_move − D. It is reported for every eventual-YES word that was detected and unpriced at the start.
- **Paper fill proxy**: buy YES at the price y of every taker-NO print in (D, Jh] (or (D, D + 300 s] if no Jh).
  - Full print size, one participant.
  - Taker fee ceil(0.07 C p (1 − p)) per print-sized order.
  - Hold to settlement.
  - On the tape only prints are visible. A taker-NO print at y means a resting YES bid at y was hit, so this is a
    proxy for stale counterparties, not proof of a fillable YES ask.
  - Taker-YES prints in the same window (asks that existed and were lifted by someone else) are reported as the race
    competition.
- **False positives** (detections on words that settled NO) are paper-traded the same way, over (D, D + 300 s].
- Recorder books (`data/live_books`, from 2026-10-12) do not cover any of these events. The tape only.

### Trials and verdict rule

- Trials: 2 policies × 3 viewer delays on Deepgram (6), plus 2 policies on AssemblyAI at V = 5 s (2), so 8.
- `promising` needs all of the following:
  - primary median lead > 0;
  - paper P&L > 0 after fees with an event-bootstrap CI > 0;
  - fillable P&L >= $100 per event (so that a few events a month could plausibly matter);
  - signs holding at V = 15 s.
- `capacity_capped`: a positive lead and P&L, but under $100 per event or the CI includes 0.
- `rejected`: median lead <= 0 at V = 5 s.

## Data

| item | path | coverage |
|---|---|---|
| Audio (16 kHz WAV) + stream metadata | `data/vendor/asr/audio/` | 13 events, 650 min |
| Anchor cross-check (2nd live stream) | `data/vendor/asr/audio/*__check.*`, `anchor_check.csv` | 3 FOMC |
| ASR message logs | `data/vendor/asr/stream/<event>__{dg,aai}.jsonl` | see the minutes log |
| Minutes processed | `data/vendor/asr/minutes_log.jsonl` | |
| Mention tape | `data/vendor/ment5/tape.parquet` | to 2026-10-13 |
| Contract titles and rules | `data/vendor/fin_D/api_{earn,other}mention_markets.parquet` | |

### Anchor cross-check (pre-registration time)

- For 3 FOMC events I cross-correlated the 1 kHz amplitude envelope of the CNBC VOD against a second live VOD (AP or
  Yahoo). Every 60 s chunk locates sharply (z 14-33) with a lag that is stable to 0.05 s.
- The release_timestamp anchors disagree by 953-5,204 s. The AP and Yahoo VODs were trimmed at the start (their
  streams began with long pre-shows), so **release_timestamp is not a safe anchor in general**.
- The CNBC, Tesla and Netflix anchors are therefore checked against scheduled starts (validations a and b above)
  before they are used.

## Credit spend

| provider | audio minutes | list price | est. spend |
|---|---|---|---|
| Deepgram nova-3 streaming | 657.5 (13 events, 2 short tests, 2 harness dry runs) | $0.0077/min | about $5.06 |
| AssemblyAI universal-streaming | 159.7 (3 events, 1 test) | $0.15/h | about $0.40 |
| **total** | 817 | | **about $5.5** (cap $15) |

- Source: `data/vendor/asr/minutes_log.jsonl`.
- **Balance not read.** The coordinator asked me twice to read the Deepgram balance with DEEPGRAM_ADMIN_KEY. The
  permission classifier denied that call ("credential exploration"). I did not retry or work around it, so no
  before/after balance is recorded. If the user wants it, they can read it, or approve that call.
- The streaming key was used only for the websocket. No key was printed or logged.

## Verdict

| item | verdict | one line |
|---|---|---|
| ASR detection on public YouTube live feeds (Deepgram / AssemblyAI) vs the Kalshi mention sweep | **rejected** | On every event with an informative anchor, Kalshi repriced **before the audio existed on YouTube**: by 4.3 s (Tesla's own stream), 8.8 s (ABC, Dec FOMC) and 22-36 s (best FOMC rebroadcaster). Lead <= -5.8 s even with zero viewer delay. |
| The prize, if you were first (context, not tradable for us) | large, heavy-tailed | A taker-YES bot sweeps each said word's whole ask ladder in one microsecond-stamped burst, with a timing spread of 0.25-1.4 s (MAD) around a constant delay. Swept ladder below 95c: median $163 per word, about $1.2k per event, $116k over 12 events; one FOMC event holds 85% (1c asks). |

## Results

### 1. ASR latency (measured: arrival wall time minus the time the word's audio was sent)

| provider | events | words | interim/partial median | interim p90 | final median | final p90 |
|---|---|---|---|---|---|---|
| Deepgram nova-3 | 13 | 101k | **0.43 s** (0.39-0.46 per event) | 0.87 s | 2.6 s | 4.3 s |
| AssemblyAI v3 | 3 | 23.6k | 1.8-2.6 s | 5.8-6.5 s | 5.8-20.7 s | 22-48 s |

Deepgram is about 4x faster on first sight of a word. AssemblyAI end-of-turn finals are far too slow for this.
(`latency_summary.csv`)

### 2. Precision / recall against Kalshi settlement (`precision_recall.csv`)

| family | provider | policy | YES words | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|---|---|---|
| fed (Chair filter by diarization) | dg | P-int | 106 | 104 | 10 | 2 | 0.91 | 0.98 |
| fed | dg | P-fin | 106 | 103 | 4 | 3 | 0.96 | 0.97 |
| fed (no diarization) | aai | P-int | 12 | 12 | 6 | 0 | 0.67 | 1.00 |
| fed | aai | P-fin | 12 | 12 | 2 | 0 | 0.86 | 1.00 |
| earn (all speakers) | dg | P-int / P-fin | 67 | 66 | 6 | 1 | 0.92 | 0.99 |
| earn | aai | P-int / P-fin | 25 | 25 | 2 / 0 | 0 | 0.93 / 1.00 | 1.00 |

- **False positives** are mostly other speakers. On FOMC, "Trump", "President" and "Dissent" come from reporters' questions while the running-majority speaker heuristic is still settling, or when a reporter is mis-diarized. On Netflix, "Warner Bros" and "WWE" come from the interviewer, who is not a Netflix representative.
- One FP is real grammar: "Gigafactory" was said, but Kalshi settled NO.
- Without diarization (AssemblyAI) FOMC P-int precision falls to 0.67. Speaker attribution matters.
- **Misses**: "Good Afternoon" ×2 (the Chair said "good afternoon" before the running-majority speaker was the
  Chair), "Megapack 3", "Anchor/Anchored" (P-fin).

### 3. The market reacts in one sweep at a near-constant delay after the audio (`spread.csv`, tape inspection)

For each eventual-YES word that the detector found and that was unpriced (p0 < 0.90):
- d = J_move − (release_timestamp + audio time);
- r = d minus the LOO centre of the other words' d.

| event | stream | matched words | centre d | LOO MAD of r | share abs(r) <= 2 s |
|---|---|---|---|---|---|
| FOMC 25DEC | ABC | 3 | -8.8 s | 1.4 s | - |
| FOMC 26JAN | CNBC | 9 | +84.0 s | 0.63 s | 0.55 |
| FOMC 26MAR | CNBC | 10 | -64.1 s | 0.43 s | 0.75 |
| FOMC 26APR | CNBC | 6 | -64.4 s | 0.44 s | 0.67 |
| FOMC 26JUN | CNBC | 9 | -65.2 s | 0.25 s | 0.80 |
| FOMC 26JUL | CNBC | 9 | +144.7 s | 0.70 s | 0.78 |
| TSLA 26APR22 | Tesla | 3 | -4.4 s | 0.86 s | 0.50 |
| TSLA 26JUL22 | Tesla | 5 | -4.3 s | 0.26 s | 0.67 |
| NFLX 26APR16 | Netflix IR | 7 | +176.5 s | 0.62 s | 0.56 |
| NFLX 26JUL02 | Netflix IR | 4 | +147.9 s | 2.3 s | 0.25 |

- Words "matched" have abs(r) <= 30 s. The rest (about 35%) are detections of an occurrence the market did not
  react to, usually an earlier mention by another speaker or a later repeat.
- Inside an event, the market moves at the same delay after the word, to within about half a second. The tape shows
  why (`data/vendor/asr` investigation of 26JUL). The move is a single taker-YES order that sweeps the whole ask
  ladder at one microsecond timestamp. For example, "Restrictive" went 0.17 -> 0.99 in one burst of about 3,000
  contracts. Before the sweep there are essentially no taker prints after the word.
- After the sweep, the taker-NO prints at 0.99 are 1c NO lottery buyers, not stale flow. The MHAZ "90-250 stale NO
  contracts in the 120 s before the jump" are mostly before the word.
- This is an automated competitor with deterministic latency.
- The positive centres (+84 to +177 s) are VODs trimmed at the start. On them release_timestamp + s is too early,
  and the bound below carries no information.

### 4. Alignment and the lead bound (pre-registered absolute anchor fails; a one-sided bound works)

- **Validation failed.** The pre-registered checks (a) and (b) fail:
  - first speech is −157 to +186 s off schedule;
  - the centres differ by up to 210 s across CNBC events.

  So release_timestamp + s is not the speech time, and the absolute market lag L (speech -> sweep) is **not
  identified** from archived video (deviation 1).
- **A one-sided bound that is valid.** A frame at VOD time s cannot have been on YouTube before release_timestamp + s,
  because trimming only makes it later. So d is an **upper bound** on how far the sweep trails the moment that
  stream had the audio. Any detector fed by that stream then has
  lead <= d − ASR latency − viewer delay − 1 s.
- To tighten the FOMC bound I cross-correlated 1 kHz audio envelopes of 20 other live streams of the same pressers
  against the primary (`asr_altanchor.py`, `alt_anchor.csv`; z 8-28, chunk lags consistent within 2 s).

**Best informative bound per event** (the fastest stream measured):

| event | best valid stream | d bound | lead bound at V = 0 (ASR 0.45 s + 1 s order) | at V = 5 s |
|---|---|---|---|---|
| TSLA 26APR22 / 26JUL22 | Tesla's own channel | -4.4 / -4.3 s | **-5.8 s** | -10.8 s |
| FOMC 25DEC | ABC News | -8.8 s | -10.2 s | -15.2 s |
| FOMC 26JUN | Benzinga (CNBC: -65 s) | -34.6 s | -36 s | -41 s |
| FOMC 26JUL | Right Side Broadcasting (Benzinga -34 s) | -22.4 s | -23.9 s | -28.9 s |
| FOMC 26APR | Benzinga | -28.6 s | -30 s | -35 s |
| FOMC 26MAR | Forbes | -34.3 s | -35.8 s | -40.8 s |
| FOMC 26JAN | "Courage Pays" | -32.3 s | -33.8 s | -38.8 s |

- Unbounded (only trimmed VODs): TSLA 26JUN30 (2 matched words), all three Netflix events, FOMC 26SEP.
- **Every stream with an informative anchor was behind the market.**
  - Tesla's own YouTube stream became available at least 4.3 s after Kalshi had already been swept.
  - FOMC rebroadcasters and CNBC are 22-65 s behind.
- The sweeper uses a faster source: the Fed's own webcast, a TV or satellite feed, or a conference-call dial-in.
- The pre-registered rule says `rejected` if the median lead at V = 5 s is <= 0. It is <= -10.8 s on every bounded
  event.

### 5. Paper P&L

**Lead grid on the unidentified L** (`lead_grid_summary.csv`). This is a sensitivity on a parameter we cannot
measure, not a selectable trial. Deepgram P-int, matched words, 12 events:

| assumed L (speech -> sweep) | V = 0 | V = 5 s | V = 15 s |
|---|---|---|---|
| 1 s | lead -0.6 s, win 31% | -5.6 s, 1% | -15.6 s, 1% |
| 3 s | +1.4 s, 87% | -3.6 s, 7% | -13.6 s, 1% |
| 5 s | +3.4 s, 97% | -1.6 s, 23% | -11.6 s, 1% |
| 10 s | +8.4 s, 97% | +3.4 s, 97% | -6.6 s, 1% |

With any realistic YouTube viewer delay (V >= 5 s) the pipeline beats the sweep only if L >= about 7 s. The bounds
in §4 put the sweep before YouTube availability, so for this feed L is effectively negative.

**Pre-registered proxy: taker-NO prints in (D, Jh].** About $84k in every cell, of which 97% is one word.
- 26JAN "Renovation": after the sweep the price fell back (no sustained >= 95c), and 107k NO contracts traded at
  1-20c before it settled YES.
- That is a resolution-interpretation bet after the mention, available to anyone without speed. It is not an ASR
  lead, and it is a single word. Without it the proxy is $1.3-3.0k over 12 events in every cell, mostly post-sweep prints on 26MAR "Stagflation", and it does not depend on V.
- These are stale counterparties after the sweep, not before it.
- So the pre-registered proxy does not measure what it was meant to measure: the stale NO flow MHAZ saw is not
  there between the word and the sweep.

**If first: the ladder prize.** These are the taker-YES asks below 95c that the winning sweep lifted within 2 s:
- 70 matched words, 12 events;
- median per word: 818 contracts, $618 cost, **$163 net of fees**;
- per event: earnings $150-1,850, FOMC $780-3,300; 26JAN alone $98k (61k + 37k YES asks resting at 1c on
  "Renovation" and "Trade War");
- total $116k, or $56k without 26JAN-RENO;
- false-positive cost at the family-median ladder: −$9.9k over 12 events (dg P-int).

This is real capacity, about $1-3k per event for whoever is first. A public-YouTube detector is not first.

### 6. Statistics

- **Trials**: 8 pre-registered (2 policies × 3 V on Deepgram, plus 2 on AssemblyAI). The L grid has 6 values, so I
  log 14. Repo-wide that is about 8,050 + 14.
- **Deflated Sharpe**: not computed. There is no identified, tradable return stream: lead is negative wherever it is
  bounded.
- **Bootstrap**: event-cluster CIs are in `lead_grid_summary.csv`. They are meaningless here because the sign of the
  lead is decided by the bound, not by sampling.
- **Concentration**: in every cell where we "win", the top word is >= 52% of the prize.

### 7. Look-ahead and integrity checks

- Detection is causal. `asr_words.Detector` sees only messages already received, and the Chair heuristic uses only
  finals so far. The live harness runs the same code.
- Keyterms are the contract titles, which are known before the event.
- p0 is the last print before schedule − 120 s.
- J_move and Jh come from the tape only.
- Settlement is used only for scoring.
- Audio is paced at 1x real time, so latencies are wall-clock, not batch.
- **Deviations from the pre-registration**, all post-hoc:
  1. The absolute anchor failed validation, so it was replaced by the one-sided per-stream bound and a grid on L.
  2. The LOO centre is the densest ±2.5 s cluster, not the median, because of mismatched occurrences.
  3. The P&L excludes words with abs(r) > 30 s.
  4. The ladder prize was added.

  None of these can turn the verdict positive. The bound is one-sided against us.

## Phase 2: live paper harness (built and dry-run, not scheduled)

`scripts/research/asr_live.py` (read-only; never places orders):

```
python scripts/research/asr_live.py --event KXFEDMENTION-26OCT --url "<stream URL>" --fed --minutes 90
python scripts/research/asr_live.py --score data/vendor/asr/live/<log>.jsonl
```

- **Audio**: yt-dlp (YouTube) or any ffmpeg input (HLS/RTMP/file) -> 16 kHz PCM -> Deepgram nova-3 streaming, with
  contract keyterms and diarization.
- **Detection**: the same `Detector` as the replay.
- **Book**: Kalshi public REST `GET /markets?event_ticker=` every 1 s (top of book for all of the event's markets, 1
  req/s), plus one `GET /markets/{t}/orderbook` per detection.
- **Output**: JSONL of ASR messages, detections and book snapshots.
- `--score` buys at the first polled YES ask >= 1 s after the detection, at the displayed size, if the ask is still
  below 0.90. It reports the lead as the first poll with ask >= 0.90 minus the detection time, plus paper P&L at
  settlement.
- **Dry runs** on the 26SEP presser (`data/vendor/asr/live/dryrun*_KXFEDMENTION-26SEP.jsonl`):
  - 7 min of audio, 420 book polls, 0 errors;
  - "Shock" and "Uncertainty" were detected from the Chair;
  - the scorer ran (no fills, because the market is settled).
- **Risk**: YouTube served a "confirm you're not a bot" wall to yt-dlp for about 2 h during this run. A live run should
  have a direct HLS URL ready as a fallback; passing cookies is not allowed.

**What a live run can settle that the archive cannot.** The harness measures our detection wall time against the
moment the book is swept, at 1 s polling resolution. Given §4, a YouTube feed will lose. The only live test worth
running uses a **direct** source, the federalreserve.gov webcast or an earnings-call dial-in/webcast, with YouTube in
parallel as the control.

**Upcoming candidates** (Kalshi public API, `data/vendor/asr/upcoming_events.json`):
- KXEARNINGSMENTIONNFLX-26OCT20 (Oct 20): Netflix IR posts its interview live on YouTube.
- KXEARNINGSMENTIONINTC / SPOT / AAL-26OCT22 (Oct 22): IR webcasts, usually behind a registration form.
- **KXFEDMENTION-26OCT and KXEARNINGSMENTIONTSLA-26OCT28 (Oct 28)**. FOMC: federalreserve.gov live webcast
  (direct), plus YouTube rebroadcasts as the control. Tesla: Tesla's own YouTube live, already shown to lose by at
  least 4.3 s.
- CVNA / SFM / WING (Oct 28) and AAPL / AMZN / GOOGL / MA / RDDT / SBUX (Oct 29): IR webcasts.

## Files

- **Scripts**:
  - `asr_common.py`: manifest;
  - `asr_fetch.py`: audio;
  - `asr_anchorcheck.py`, `asr_altanchor.py`: cross-correlation anchors;
  - `asr_words.py`: grammar and Detector;
  - `asr_stream.py`: 1x streaming to Deepgram / AssemblyAI;
  - `asr_eval.py`: parse, detect, market clock;
  - `asr_lead.py`: lead grid and prize;
  - `asr_report.py`: tables;
  - `asr_live.py`: live paper harness.
- **Outputs**: `data/vendor/asr/`. The streams take about 2.3 h of wall time at 1x; every script skips work it has
  already finished.

## Note (lead, 2026-10-15): measured Deepgram spend
The balance read with the user-provided admin key was $194.63 after the runs, against $200.00 before. That is $5.37
spent, matching the minutes-based estimate.
