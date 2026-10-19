# Order-book recorder (read-only)

Last revised: 2026-10-18

`scripts/live/record_books.py` records top-N order-book levels and trade prints from
**Kalshi**, **Polymarket (global CLOB)** and **Polymarket US** into hourly Parquet files, plus
Chainlink/Binance reference crypto prices from **Polymarket RTDS**. Besides the fixed
cross-venue pairs it follows rolling *series* (15-minute crypto, hourly index, daily
commodities, econ releases, weather, mention markets) through discovery; see
[Discovery](#discovery-rolling-series-subscriptions).
We need this data to measure real cross-venue arbitrage gaps and maker (market-making)
opportunity. Historical trade data can't answer either question, because both depend
on resting depth and spread.

The recorder **never places, cancels or signs orders**. It calls only public market-data
endpoints; the only signature it makes is the Kalshi WS login header.
It costs $0/month and needs **no API keys** to run.

## Quick start

```bash
# after `uv sync` (see README.md); same commands on Windows, macOS and Linux
uv run python scripts/live/record_books.py --dry-run --duration 60   # connect, print, write nothing
uv run python scripts/live/record_books.py                          # record until Ctrl+C
uv run python scripts/live/record_books.py --duration 3600 --venues kalshi polymarket
uv run python scripts/live/record_books.py discover --topics fed cpi sports   # candidate pairs
```

Ctrl+C (or SIGTERM on Linux) stops the run cleanly. The recorder flushes its buffer and
compacts finished hours before it exits. Creating the file `data/live_books/STOP` does the
same for a detached run (checked every 5 s, then deleted).

### Running it in the background (Windows)

`scripts/live/start_recorder.ps1` is a machine-local helper and is not in the repository. Without it, run
the recorder under any process manager (Task Scheduler, `nohup`, systemd) and stop it with the `STOP`
file above.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\live\start_recorder.ps1           # start (detached, hidden)
powershell -ExecutionPolicy Bypass -File scripts\live\start_recorder.ps1 -Status   # PID + last log lines
powershell -ExecutionPolicy Bypass -File scripts\live\start_recorder.ps1 -Stop     # graceful stop (STOP file; kill after 60 s)
```

The script starts Python through WMI (`Win32_Process.Create`), so the recorder is not a
child of the terminal and survives closing it. It passes `--env-file .env` (Kalshi WS
keys) and `--log-file data/live_books/recorder.log`; the PID is in
`data/live_books/recorder.pid`. Emergency stop:
`Stop-Process -Id (Get-Content data\live_books\recorder.pid)` (loses at most
`flush_interval_s` of buffered rows; already-flushed part files are safe).

It does **not** restart after a reboot or crash. Optional: start it at logon with Task Scheduler:

```powershell
$a = New-ScheduledTaskAction -Execute powershell.exe `
     -Argument '-NoProfile -ExecutionPolicy Bypass -File "<repo>\scripts\live\start_recorder.ps1"'
$t = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask -TaskName "predict-ngin recorder" -Action $a -Trigger $t
# remove: Unregister-ScheduledTask -TaskName "predict-ngin recorder" -Confirm:$false
```

The script is idempotent (it exits if a recorder is already running), so you can also add
a second trigger every 15 minutes as a crude crash watchdog.

- **Linux VPS:** use a `systemd` unit with `Restart=always`.
- **Memory:** the recorder holds only current books plus at most `flush_interval_s` of
  rows. That is under 100 MB with static pairs only, and about 300 MB with all discovery
  groups (2,000+ books).

## Endpoints (verified 2026-10-12)

| Venue | Books | Trades | Auth |
|---|---|---|---|
| Kalshi | REST `GET api.elections.kalshi.com/trade-api/v2/markets/{ticker}/orderbook?depth=N` (bid-only YES/NO ladders) | REST `GET /markets/trades?ticker=&min_ts=` polled every 15 s | none for REST |
| Kalshi WS | `wss://external-api-ws.kalshi.com/trade-api/ws/v2`, channels `orderbook_delta` (snapshot + deltas, `seq`) and `trade` | same feed | **API key required, even for public channels** |
| Polymarket | WS `wss://ws-subscriptions-clob.polymarket.com/ws/market` with `custom_feature_enabled: true` (`book`, `price_change`, `best_bid_ask`, `last_trade_price`), plus REST `POST clob.polymarket.com/books` for resync | `last_trade_price` events | none |
| Polymarket US | REST `GET gateway.polymarket.us/v1/markets/{slug}/book` | only the last trade (`stats.lastTrade*`) per poll | none (the WS `wss://api.polymarket.us/v1/ws/markets` needs a KYC'd key; still TODO) |
| Polymarket RTDS (verified 2026-10-13) | `wss://ws-live-data.polymarket.com`, `{"action":"subscribe","subscriptions":[{"topic":"crypto_prices_chainlink","type":"*","filters":""},{"topic":"crypto_prices","type":"update","filters":""}]}`, text `PING` every 5 s | reference prices, not trades | none (the newer documented `ws-live-v2` endpoint needs CLOB API credentials) |
| Discovery (REST) | Kalshi `GET /markets?series_ticker=&status=open\|unopened`, `GET /series?category=`, `GET /events?status=open`; Gamma `GET /events?tag_slug=&end_date_min=&end_date_max=` | | none |

Kalshi WS facts measured 2026-10-13 with a read-only probe:

- One `orderbook_delta` subscription accepted **6,865 markets** without error code 26
  ("per-subscription market limit"). Two connections on the same key at once are fine.
- `update_subscription` (`add_markets` / `delete_markets`, one `sid` per command) works live.
  Its `ok` reply **carries the subscription's `seq`**, so the sequence tracker consumes it.
  Otherwise every add or delete would look like a gap.
- `max_close_ts` combined with `status=open` returns 0 markets, so close-time filtering is
  done client-side.
- 15-minute markets are listed ahead with `status=unopened`. Subscribing to them early means
  a new window is recorded from its first second.

Rate limits:

- **Kalshi Basic tier:** 200 read tokens/s, about 20 req/s. The default cap is 8 req/s
  shared by book and trade polling. At a 2 s poll interval that covers about 12 markets.
  For more markets, raise `rest_poll_interval_s` or enable WS.
- **Polymarket US:** 25 req/s per IP. The default cap is 8 req/s.
- **Polymarket WS:** has no practical limit at this scale.

Polymarket CLOB v2 (2026-04-28) did not change the public `book`/`books` endpoints or
the market WS URL.

## Optional keys (all free)

| Key | What it unlocks | How |
|---|---|---|
| `KALSHI_API_KEY_ID` + `KALSHI_PRIVATE_KEY_PATH` | Kalshi WebSocket: every book change with sequence numbers, trades in real time instead of a 15 s poll | Kalshi account → Settings → API Keys → create key. Save the `.pem`, set both env vars (in `.env`; `cryptography` is already a dependency). `mode: auto` switches to WS by itself. |
| Polymarket US API key | Polymarket US WS (full trade tape, every book change) | KYC in the Polymarket US app, then polymarket.us/developer. Not wired up yet (see the TODO in `src/recorder/venues/polymarket_us.py`). |

Without keys you still get Kalshi and Polymarket US at ~2 s REST resolution and Polymarket at 1 s WS resolution.

## Config: `config/recorder.yaml`

- `pairs`: equivalent markets across venues. Each pair has a `kalshi.ticker`, a
  `polymarket.yes_token` (optional `no_token`) and/or a `polymarket_us.slug`, plus
  `status: verified | needs-review` (default needs-review) and a `note` listing residual
  rule differences. The shipped list (2026-10-12) is 10 pairs / 30 instruments: Fed Oct
  hold + hike 25, Dodgers World Series, five LCS games (Oct 12-15), MNF and TNF. Games
  expire once played, so refresh the list every few days.
- **Polymarket second token:** the NO / second-team token's book is the exact complement of
  the first (every `price_change` carries both), so the shipped config records only
  `yes_token`, which halves Polymarket storage.
- **Polymarket US sports books** are priced in the first ("long") side, which is the first
  team in the slug (`aec-mlb-lad-mil-...` is priced in Dodgers). Set `outcome:` to that team.
- **Polymarket NFL slugs use the UTC date** (`nfl-buf-la-2026-10-13` for a Monday-night
  game on Oct 12 ET); Kalshi and Polymarket US use the ET date.
- `markets`: single-venue markets, for the maker-opportunity study.
- **Quote Polymarket token ids.** They are 78-digit integers. The parser keeps unquoted
  ones exact, but quoting is clearer.
- **Sports token ids are team outcomes, not yes/no.** Polymarket sports markets have
  outcomes like `["Los Angeles Dodgers", "Milwaukee Brewers"]`. `yes_token` is simply
  the first one.
- **`discover` output is unreviewed.** It writes candidate pairs to
  `data/live_books/candidate_pairs_*.yaml`. Matching is fuzzy: title tokens plus close
  date. Read both rulebooks before promoting a pair, because rules really do differ:
  - headline CPI vs core CPI, MoM vs YoY
  - "exactly 25 bps" vs "> 25 bps"
  - regulation time vs including overtime
  - which game of a series

## Discovery (rolling series subscriptions)

The `discovery:` section of `config/recorder.yaml` lists **groups**. Each group is
re-queried every `refresh_s`, and its market set is *replaced*:

- New markets are subscribed live. Kalshi uses `update_subscription add_markets`;
  Polymarket uses `{"operation":"subscribe"}` on the open socket.
- Markets that no longer qualify are unsubscribed.
- Every market is dropped locally `close_grace_s` after its `close_time`, even if the REST
  refresh is late or rate-limited.

| Key | Meaning |
|---|---|
| `discovery.close_grace_s` | keep recording this long after close (120) |
| `discovery.series_scan_s` | category groups: how often to re-scan which series have an open event (21,600 = 6 h; costs about 70 requests) |
| `discovery.kalshi.max_requests_per_s` | discovery REST budget (1.0). Other research processes share this IP; repeated 429s back off up to 120 s and keep the previous set |
| `discovery.polymarket.max_requests_per_s` | Gamma budget (2.0) |
| group `name`, `series: [...]` | Kalshi series tickers |
| group `category` | Kalshi category: every series in it that has an open event (used for `Mentions`) |
| group `tags`, `slug_regex` | Polymarket: Gamma tag slugs and an event-slug filter |
| `refresh_s` | re-query interval |
| `max_hours_to_close` | only markets closing within N hours |
| `nearest_strikes: K` | per event, the K strikes nearest the price implied by the event's own quotes (strike with YES mid nearest 0.5; for range brackets, the highest-priced bracket). Hysteresis: a subscribed strike stays while it ranks within ceil(1.5 K) |
| `include_unopened` | Kalshi: also take markets listed but not yet open (next 15-minute window) |
| `depth`, `heartbeat_s` | per-group overrides of `defaults` |
| `bbo` | Polymarket: also store `best_bid_ask` events (default off for groups) |
| `tokens: first\|both` | Polymarket: first outcome token only (default) or both |
| `series_regex` | Kalshi category groups: keep only category series whose ticker matches (`re.search`) |
| `event_regex` | Kalshi: keep only markets whose `event_ticker` matches, e.g. `-(26\|2026)$` |
| `sample_interval_s` | per-group book sampling interval (on change); default `defaults.ws_sample_interval_s` |
| `sample_windows` | list of `{start, end, interval_s[, heartbeat_s]}` (ISO-8601, offset allowed, naive = UTC): inside `[start, end)` the group samples at `interval_s` (fastest window wins) and, if given, heartbeats at `heartbeat_s`. The sampler loop ticks at `defaults.ws_sample_interval_s` (1 s), so windows faster than that have no effect |
| `track_settlement` | Kalshi: log this group's market status/result changes (see [Elections](#2026-us-midterm-elections-added-2026-10-14)) |
| `incentives: true` | Kalshi: take markets from `GET /incentive_programs` (Liquidity Incentive Program) instead of `series`/`category`; see [k-lip](#kalshi-liquidity-incentive-markets-k-lip-added-2026-10-14) |
| `max_markets` | incentives groups: keep the top N markets by reward $/day |
| `incentive_lookahead_s` | incentives groups: also take programmes starting within this many seconds (`status=upcoming`) |
| `min_reward_per_day` | incentives groups: drop markets whose programmes pay less than this (USD/day) |
| `venues.kalshi.max_markets_per_conn` | discovered markets per Kalshi WS connection (1000). Static pairs always get connection 0 to themselves, so discovery churn never touches them, and a sequence-gap reconnect only rebuilds one shard |

**Shipped groups (2026-10-13).** About 1,950 Kalshi markets on 3 connections and 21 Polymarket
tokens.

| Group | What | Filter |
|---|---|---|
| `k-crypto-15m` | KXBTC15M, KXETH15M, KXSOL15M | ≤0.6 h, incl. unopened |
| `k-crypto-hourly` | KXBTCD, KXETHD, KXSOLD (current + next hour) | ≤1.2 h, unopened, 10 nearest strikes |
| `k-crypto-daily` | same series, 5 pm-ET daily / Friday weekly | ≤30 h, 12 nearest |
| `k-index-hourly` | KXINXU, KXNASDAQ100U | ≤4 h, 10 nearest |
| `k-index-daily` | KXINX, KXNASDAQ100 (ranges) | ≤30 h, 12 nearest |
| `k-commodities` | KXWTI, KXGOLDD, KXBRENTD | ≤30 h, 12 nearest |
| `k-econ` | KXCPI, KXCPICORE, KXCPIYOY, KXCPICOREYOY, KXFEDDECISION, KXFED, KXRATECUT, KXRATECUTCOUNT, KXPAYROLLS, KXU3, KXJOBLESSCLAIMS, KXGDP, KXPCECORE, KXPCEHEAD, KXAAAGASD, KXAAAGASW | ≤2,000 h, heartbeat 120 s |
| `k-weather-high` | the 18 KXHIGH* cities that ForecastEx also lists (station-matched in FIN_E) | ≤48 h, depth 5 |
| `k-mentions` | category `Mentions` (76 active series / 1,347 markets on 2026-10-13: earnings calls, Trump/Fed/press-secretary, debates) | all, depth 5, heartbeat 300 s |
| `pm-crypto-{5m,15m,1h,4h}` | Polymarket BTC/ETH/SOL up/down, "Up" token | ≤0.15 / 0.5 / 1.2 / 4.2 h, depth 5 |

Not found or not open on 2026-10-13:

- `KXAAAGASD` (daily AAA gas) exists but had no open event. It is in `k-econ` and is
  picked up automatically when one opens.
- There is no `KXPCE`; headline PCE is `KXPCEHEAD`.
- No sports-broadcast mention series was open. The `Mentions` category scan picks them up
  within `series_scan_s` once listed.
- No ETH/SOL 15M gap: `KXETH15M` and `KXSOL15M` exist. `KXXRP15M` also exists but is not
  recorded.

Election groups `k-elex-races` and `k-elex-control` (added 2026-10-14) are described in
[2026 US midterm elections](#2026-us-midterm-elections-added-2026-10-14).

**Polymarket 5-minute books burst hard.** Up to ~1,100 msgs/s for 9 tokens. Polymarket then
occasionally closes the socket with `1013 slow consumer`. This happens even with a bare
consumer, and our processing costs 18 µs/msg (profiled on captured frames), so it is
server-side. Each Polymarket group therefore has its own connection. Reconnects take about
1 s and set `gap`. Client-side `max_queue` is 16,384 frames.

### Hot reload of discovery groups (2026-10-15)

The running recorder checks `config/recorder.yaml`'s mtime every 30 s (`--no-reload` turns
this off). When the file changes, it loads it and applies discovery-group changes without a
restart (`src/recorder/reload.py`):

- **Added group:** a new discovery loop starts. A Polymarket group also gets its own WS feed.
  Its markets are recorded from its first refresh.
- **Removed or `enabled: false` group:** its loop stops and its markets roll out through
  `Universe.set_group`. A market still held by another group or by the static config stays.
- **Changed group:** its loop restarts with the new settings, and the next refresh replaces
  its market set. New depth, heartbeat or sampling settings reach markets subscribed after the
  change. Markets already subscribed keep their old settings until they roll over.

A file that fails to load is rejected whole. The running groups stay, the log says
`config reload REJECTED`, and the next change to the file is tried again. Changes to
`defaults`, `venues`, static `pairs`/`markets`, `rtds`, `settlement` or the discovery rate
limits still need a restart. They are logged as `need a recorder restart and were NOT
applied`.

## 2026 US midterm elections (added 2026-10-14)

This section implements the recorder part of the pre-registered plan in
`docs/ELEX_ELECTIONS.md` §7. It is read-only, like the rest of the recorder.

**What is recorded**

| Group | Markets | Books | Trades |
|---|---|---|---|
| `k-elex-races` | every Kalshi `Elections` series matching `^(CONTROL[HS]\|SENATE[A-Z]{2}S?\|GOVPARTY[A-Z]{2}\|HOUSE[A-Z]{2}\d+\|KXHOUSERACE)$` with a 2026 event (`event_regex: -(26\|2026)$`). That is chamber control, Senate, governor, the HOUSE<st><n> district series and KXHOUSERACE: 982 markets on 2026-10-14 | WS depth 5, on change at most every 5 s, heartbeat 300 s | WS `trade`, every print |
| `k-elex-control` | seat counts and combos for the 2026 Congress: KXBALANCEPOWERCOMBO, KXDSENATESEATS, RSENATESEATS, KXDHOUSESEATS, KXRHOUSESEATS, KXDSENATESEATSH, KXHOUSEPOPVOTEMARGIN, KXBLUEWAVECOMBO, KXBLUETSUNAMICOMBO and KXCLOSESTSENATE, `-27…` events only. 91 markets | same | same |

- Primaries are excluded. They are separate series, and the regexes do not match them.
- Discovery re-runs every 30 min, so newly listed races are added automatically. The
  `Elections` category scan (which series have an open event) runs every `series_scan_s` (6 h).
- Rows have `pair = null`; join `markets-HH` on `market_id` to get `group`, `series`,
  `event` and `title`.

**Election-night window.** Both groups set
`sample_windows: [{start: 2026-11-03T17:00-05:00, end: 2026-11-04T12:00-05:00, interval_s: 1, heartbeat_s: 30}]`,
which is 2026-11-03 22:00Z to 2026-11-04 17:00Z. Inside the window, books are written on
every change at up to 1 s, and quiet books every 30 s. The window covers both the
requested 18:00 ET start and the pre-registered 22:00Z start, through 12:00 ET Nov 4.
Outside it the 5 s / 300 s settings apply. No restart is needed: the switch happens on
the clock.

**Close and settlement log.** `settlement:` polls `GET /markets?tickers=…` for every market
of a `track_settlement` group: 100 tickers per request at ≤1 req/s, about 11 requests per
sweep. It sweeps every 5 min from 2026-11-03 18:00 ET to 2026-11-11 00:00 ET
(`fast_windows`), hourly otherwise, and also within 30 s of a market first being
discovered. Output goes to `data/live_books/kalshi/{date}/status-{HH}.parquet`, with columns
`venue, market_id, group, event, status, result, prev_status, prev_result, close_ns,
settlement_ns, expiration_value, settlement_value, first_seen, ts_recv_ns`.

- Each market gets one `first_seen` baseline row, then one row per change of status,
  result, close time, `settlement_ts` or expiration value.
- `close_ns` is the venue's `close_time`. Kalshi moves it forward on an early close.
  `settlement_ns` is the venue's `settlement_ts`. `ts_recv_ns` of the first `closed` row is
  when we observed the close, so it is accurate to within one sweep.
- Closed markets leave the book universe, but they stay tracked until Kalshi reports
  `finalized`. The tracked set persists in `data/live_books/kalshi/settlement_state.json`,
  so a restart does not lose them. Deleting that file only causes new baseline rows.

**PredictIt.** This is already covered by the fin_E collector
(`scripts/research/fin_E_live_collector.py`).
- It GETs `https://www.predictit.org/api/marketdata/all/` once per Kalshi cycle, which is
  ≥60 s (median gap 60 s on 2026-10-14). It stores the raw JSON, gzip JSONL, in
  `data/vendor/fin_E/live/predictit/{YYYYMMDD_HH}.jsonl.gz` as lines of
  `{"t": recv_unix_s, "k": "all", "d": payload}`.
- That costs about 15-45 MB/day.
- A non-200 response (e.g. 429) is skipped, and the next try is a full cycle (≥60 s) later.
  It is never retried immediately.
- The recorder does not poll PredictIt a second time.
- The watchdog task `predict-ngin-collectors` restarts the collector within 15 min unless
  `data/vendor/fin_E/live/STOP` exists, so keep that file absent through Nov 4.
- ForecastEx election markets are in the same collector.

**Load and limits.**
- The election groups add about 1,070 markets to the ~2,200 already recorded, for about
  3,300 Kalshi markets on 5 WS connections. That is 4 discovery shards at ≤1,000 each,
  plus connection 0 for static pairs.
- One subscription has accepted 6,865 markets, and several connections on one key work
  (probe 2026-10-13). Kalshi documents no per-key connection cap.
- Discovery adds about 180 REST requests per 30 min for the series scan. That fits within
  the shared 1 req/s discovery budget.

**Disk estimate.** This was measured in a 10-minute trial on 2026-10-14 (11:29-11:39 UTC), with the election groups alone.

- **Normal regime (5 s / 300 s).** Rows per market per minute: races 0.98 and control 2.5. Those control rows were sampled at 1 s, because the trial forced the window on. That is about 1,100 rows/min for about 1,070 markets, or roughly 1.6 M book rows a day. At ~40 B/row compacted (63 B in small part files) that is **about 65 MB/day**, plus trades at about 9/min (negligible) and status rows (a few KB). From now to Nov 10 that comes to about 1.8 GB.
- **Election night (1 s / 30 s, 19 h).** The hard ceiling is every market changing every second: 1,073 x 68,400 s, or 73 M rows, which is **about 2.9 GB**. A realistic case is 10-30% of books changing each second, which gives **0.3-0.9 GB**. Add trades: even 1 M prints is only about 30 MB.
- **Free space.** C: had 170 GB free on 2026-10-14, so even the ceiling fits easily. The existing ~2,200-market load is unchanged at roughly 250 MB/day (Kalshi books + trades on 10-13).

**How to stop it.**
- To stop recording elections but keep everything else: set `enabled: false` on
  `k-elex-races` and `k-elex-control` and `settlement.enabled: false`, then restart
  (`start_recorder.ps1 -Stop`, then start).
- To stop only the 1 s window: delete the `sample_windows` entries and restart.
- To stop the whole recorder: `start_recorder.ps1 -Stop`, or create `data/live_books/STOP`.
  The watchdog will not restart it while STOP exists.
- After 2026-11-10, disable the two groups. Leave the settlement log running until every
  market is `finalized` (`settlement_tracked` in the 60 s `discovery:` status line drops to 0).

## Kalshi Liquidity Incentive markets (`k-lip`, added 2026-10-14)

**Purpose.** Books for the `lipfwd` forward test of LIP reference-level farming
(`docs/LIPFWD_PREREG.md`; evaluator `scripts/research/lipfwd_eval.py`). Before this
group the recorder covered only ~420 of ~5,500 incentivised markets ($18k of $340k/day).

**How it selects markets** (`incentives: true`, `src/recorder/series_discovery.py`):
1. Every `refresh_s` (1,200 s) it reads `GET /incentive_programs?type=liquidity` with
   `status=active` and `status=upcoming`. It keeps upcoming programmes that start within
   `incentive_lookahead_s` (1,500 s), so the next 15-minute window is subscribed before it opens.
   That is 2 requests.
2. It ranks markets by reward $/day: `period_reward` (centi-cents) / 10⁴, spread over the period
   (minimum 15 min) and summed over the market's programmes.
   - A market already subscribed ranks as if its reward were 25% higher. This stops churn
     around the cap.
3. It reads close time and status for the head of the ranking from `GET /markets?tickers=`
   (100 per request). Rows are cached per ticker, so steady state only asks for new tickers.
4. It applies the usual filters (open or unopened, not past close) and keeps the top
   `max_markets` (1,500).

**Shared markets.** A market also held by another group keeps that group's settings, except for
depth. Depth becomes the maximum any group asks for (`Universe.current`). For example, a
`k-mentions` market that is also in `k-lip` is stored at depth 15, not 5. Static pairs are never
changed.

**Depth 15.** A probe of 600 random LIP markets on 2026-10-14
(`scripts/research/lipfwd_probe.py`) measured how many levels a side needs to hold the
1,000-contract target. Of the sides that reach the target at all (94%):

| Depth | Share of sides covered |
|---|---|
| 1 level (median) | — |
| 5 levels (90th percentile) | 90.9% |
| 10 levels | 97.9% |
| 15 levels | 99.6% |
| 20 levels | 100% |

Depth 15 was chosen. Rows hold 11 levels on average (both sides).

**Settlement.** `track_settlement: true`, so every k-lip market is in the hourly
`status` log until it is `finalized`.

**The `lip` table** (`data/live_books/kalshi/{date}/lip-{HH}.parquet`) holds one row per
programme per refresh, for every active programme plus upcoming ones within the lookahead. That
is about 5,500 rows and ~190 KB per refresh, or ~14 MB/day. Columns:

| Column | Meaning |
|---|---|
| `group`, `program_id`, `market_id` | |
| `status` | `active` or `upcoming` at refresh time |
| `start_ns`, `end_ns` | the programme period |
| `period_reward_usd`, `reward_usd_day` | |
| `target_size`, `discount_factor_bps` | |
| `description` | e.g. `series_lip` |
| `paid_out` | |
| `selected` | the market was in the recorded set after this refresh |
| `rank` | rank by $/day |
| `ts_recv_ns` | refresh time |

A point-in-time universe for minute t is the latest refresh with `ts_recv_ns ≤ t`, where
`selected` is true.

**Load (2026-10-14 14:24 UTC, after the restart).**
- **Markets.** 5,024 Kalshi markets on 7 WS connections: connection 0 for static pairs plus
  6 discovery shards of ≤ 1,000 each. Before the restart it was ~3,400 on 5.
- **Overlap.** Only ~124 of the 1,500 k-lip markets (8%) were already recorded by other groups.
- **Coverage.** k-lip holds $349k/day of the $418k/day of active plus imminent pools.
- **REST.** Steady state is ~2 incentive requests plus a few `/markets` batches per 20 min, plus
  ~15 settlement requests per hour. All of it fits the shared 1 req/s discovery budget.

**Disk.**

| Measurement | Value |
|---|---|
| Trial, k-lip alone (scratch output, 14:00-14:11 UTC) | 6,800 book rows/min for 1,500 markets (4.5 rows/market/min), 50 B/row (part files and compacted alike) → **0.49 GB/day** |
| Of which already-recorded overlap | 12% of rows |
| Production, Kalshi books per 5-min flush | 2.4-2.6 MB before → 4.3 MB after |
| Production, increase | **+22 MB/h, about +0.45-0.55 GB/day** at US-morning activity |
| Trades and `lip` rows | +~25 MB/day |
| Whole recorder before | ~0.6-0.9 GB/day |
| Whole recorder after | **~1.1-1.4 GB/day**; C: had 173 GB free, i.e. over 100 days |

If a day goes over budget, trim in this order:
1. `heartbeat_s: 300`;
2. `sample_interval_s: 2`;
3. `max_markets: 1000`, which still holds 68% of pool $;
4. `depth: 10`, which covers the target on 97.9% of sides.

**Restart.** The restart on 2026-10-14 at 14:11:56-14:12:00 UTC kept the process down for
~4 s.
- Discovered groups return as each first refresh completes:
  - fast groups and `k-lip` within 3 min (k-lip at 14:14:52);
  - `k-mentions` (93-series category scan) at 14:21;
  - `k-elex-races` at 14:23.
- Any restart has this re-discovery delay. Plan restarts outside 12:25-12:40 and 18:00-18:10 UTC.

## Output

```
data/live_books/{venue}/{YYYY-MM-DD}/books-{HH}.parquet           # compacted hour (UTC)
data/live_books/{venue}/{YYYY-MM-DD}/books-{HH}.partNNNN.parquet  # current-hour flushes
data/live_books/{venue}/{YYYY-MM-DD}/trades-{HH}.parquet
data/live_books/{venue}/{YYYY-MM-DD}/bbo-{HH}.parquet             # venue-published BBO (Polymarket)
data/live_books/polymarket_rtds/{YYYY-MM-DD}/prices-{HH}.parquet  # Chainlink / Binance reference prices
data/live_books/{venue}/{YYYY-MM-DD}/markets-{HH}.parquet         # discovered markets rolling in/out
data/live_books/kalshi/{YYYY-MM-DD}/status-{HH}.parquet            # status/result changes of track_settlement groups (elections, k-lip)
data/live_books/kalshi/{YYYY-MM-DD}/lip-{HH}.parquet               # Liquidity Incentive Program snapshots (k-lip, one row per programme per refresh)
```

Read a whole day with `pd.read_parquet("data/live_books/polymarket/2026-10-12")` or
`pl.scan_parquet(".../*.parquet")`. Part files and compacted files never overlap.

**books**:

| Column | Meaning |
|---|---|
| `venue`, `market_id`, `outcome`, `pair` | Identify the instrument and the config pair it belongs to |
| `source` | `ws` / `rest` |
| `ts_exchange_ns` | Venue timestamp. Null for Kalshi REST, which has none. |
| `ts_recv_ns` | Local UTC ns |
| `seq` | Kalshi WS sequence number |
| `n_updates` | Book changes since the previous row |
| `gap` | A sequence gap, BBO mismatch, reconnect or poll stall happened since the previous row |
| `best_bid`, `best_ask` | Top of book |
| `bid_px[]`, `bid_sz[]`, `ask_px[]`, `ask_sz[]` | Top-N levels, best first. Every size is > 0; `best_bid == bid_px[0]` (null when the side is empty) |
| `last_update_ns` | Local receive time (UTC ns) of the venue's last message for this market: WS delta/snapshot, REST snapshot, or a Kalshi silence-audit REST confirmation. Added 2026-10-14 11:00 UTC; null before |
| `age_s` | `(ts_recv_ns - last_update_ns) / 1e9`. Heartbeat rows of a silent book show it growing; use it, not row presence, to judge staleness |

**Price convention:** every book is stored in the terms of its own instrument.

- **Kalshi** is stored in YES terms. Kalshi only has bids, so a NO bid at p becomes a
  YES ask at 1−p.
- **Polymarket** stores one book per outcome token.
- **Polymarket US** stores one book per slug (the "long" side).

**trades**: `venue, market_id, outcome, pair, trade_id, ts_exchange_ns, ts_recv_ns,
price, size, taker_side, source`.

**prices** (`polymarket_rtds`): `venue, topic, symbol, ts_exchange_ns` (price observation
time), `ts_msg_ns` (socket timestamp), `ts_recv_ns, value`. Topics:

- `crypto_prices_chainlink`: `btc/usd`, `eth/usd`, `sol/usd`. These are the settlement
  source of Polymarket's up/down markets, about 1/s each.
- `crypto_prices`: Binance `btcusdt`, `ethusdt`, `solusdt`.

**markets** (one row per discovered market each time it rolls in or out): `venue,
market_id, group, series, event, title, strike_type, floor_strike, cap_strike, close_ns,
outcome, ts_recv_ns, action (add|remove)`.

- Kalshi `title` includes the yes-subtitle, i.e. the mention word or the strike.
- For Polymarket, `event` is the market slug, which is the only way to map a token id back
  to its window.
- Discovered markets have `pair = null` in books/trades. Join to this table on
  `market_id` to get the group.

**bbo** (Polymarket only; every `best_bid_ask` WS event, unsampled): `venue, market_id,
outcome, pair, ts_exchange_ns, ts_recv_ns, best_bid, best_ask, source`. This is the
venue's own top of book, independent of our depth reconstruction. Use it when you only
need the touch, or to audit the `books` table.

## Sampling and integrity

- **When rows are written:** WS books are kept in memory from snapshot plus deltas, and
  a row is written at most every `ws_sample_interval_s` (1 s), only when the book
  changed. Unchanged books are still written every `heartbeat_s` (60 s). `n_updates`
  shows how much activity the 1 s sampling hid.
- **Kalshi WS:** each subscription has a `seq` number. A gap invalidates the affected
  books, and the recorder reconnects to get fresh snapshots. A delta that would make a
  level negative also triggers a resync.
- **Polymarket WS:** the feed has no sequence numbers. The recorder compares its
  reconstructed best bid/ask with the `best_bid`/`best_ask` echoed on `price_change`.
  On a mismatch it invalidates the book and takes a REST snapshot. It also takes a full
  REST snapshot every `resync_interval_s` (600 s). A REST snapshot older than the WS
  state already applied is ignored (`stale_rest_skipped`).
  - **Split updates (bug fixed 2026-10-12).** The venue often sends one matching-engine
    update as several `price_change` messages with the same `timestamp` (e.g. "BUY 0.65
    size 15" then "SELL 0.65 size 0"). The echoed best bid/ask on *every* message of the
    group already reflects the end of the group. Checking after each message flagged the
    intermediate, often crossed, state: about 12 false mismatches and REST resyncs per
    minute during a live MLB game. The check now runs when the group closes (the next
    message for that asset with a different timestamp, or 0.5 s later), and books with an
    open group are not sampled. After the fix there were 0 mismatches in 1,618 checks
    (120 s) and in 3,531 checks (180 s, 10 tokens). Replay fixture:
    `tests/unit/fixtures/recorder_polymarket_split_update.jsonl`.
  - The live counters `bbo_checks` / `bbo_mismatches` appear in the 60 s status log line.
  - **Empty ask side (fixed 2026-10-13).** On an almost-resolved up/down market the venue
    echoes `best_ask = 1` when there are no asks. 1.0 is not an order price, so the check
    treats it as "no ask". Before the fix, this caused a false mismatch and REST resync
    every 1-2 s.
  - **Dead tokens.** A token that the REST `/books` call no longer returns (resolved market)
    is retried only every 30 min. Before this, finished games caused about 80 futile
    resyncs a minute (`dead_tokens` counter).
- **Kalshi WS silence audit (2026-10-14).** Kalshi sends deltas only when a book
  changes, so a quiet book and a dead subscription look the same on the wire. Every
  `audit_interval_s` (60 s) the recorder takes up to `audit_max_per_round` (10) books with
  no WS message for `silence_audit_s` (120 s), least recently audited first, and compares
  their top 3 levels with REST `orderbook?depth=3` (1 req/s). A match refreshes
  `last_update_ns`. A mismatch is re-read 3 s later. If it still differs and no WS message
  arrived meanwhile, the book is invalidated (no more rows) and its shard reconnects for
  fresh snapshots, at most once per shard per 10 min. Counters in the 60 s status line:
  `audit_ok`, `audit_stale`, `audit_reconnects`, `audit_skipped` (book changed or market
  gone during the check), `audit_errors`. Set `silence_audit_s: 0` to disable.
- **REST:** a poll interval that stretches past 3× its target sets `gap`.
- **Reconnects:** exponential backoff with jitter, capped at 60 s.

## Data-quality notes for existing files (audit 2026-10-14)

The micro-anomaly scan (`docs/MICRO_ANOMALY_SCAN.md` §1.5) reported two
artifacts. This is what they turned out to be.

**1. "best_bid stays set after its level empties (size 0)": a reader artifact, not in the files.**
A scan of every `books` file up to 2026-10-14 11:00 UTC (Kalshi 6.44M rows, Polymarket
0.79M, Polymarket US 26k) found **0 levels with size ≤ 0** and **0 rows where
`best_bid`/`best_ask` differ from `bid_px[0]`/`ask_px[0]`**. All three paths already drop
empty levels: Kalshi deltas to 0 (float residue included), Polymarket `price_change`
size 0, and REST snapshots. What does happen is that a side empties: `best_bid` becomes
null and `bid_px` becomes `[]`. Kalshi had 480k such rows on the bid side and 228k on the ask
side, mostly weather. A reader that forward-fills `best_bid` on a 1 s grid (as
`scripts/research/micro_h6_ladders.py` does with `x.ffill()`) carries the old price into
those rows while the touch size it derived is 0. Rules for readers:

- **Never forward-fill `best_bid`/`best_ask` across a row where they are null.** A null
  means the side is empty, not that the value is unknown. Forward-fill whole rows (as-of
  join on `ts_recv_ns`), not single columns.
- Kalshi sizes are fractional. 53k bid and 65k ask touches are below 1 contract (e.g.
  0.01). They are real resting orders, but you can't hit them for a whole contract. To
  require executable size, filter `bid_sz[0] >= 1` / `ask_sz[0] >= 1`, or walk the
  ladder to your size.

**2. Silent feeds: quiet books, not dropped subscriptions (so far).** Heartbeat rows (every
`heartbeat_s`) re-store an unchanged book, so files before 2026-10-14 11:00 UTC can't
separate "unchanged" from "stale" by row presence. Kalshi WS index-hourly rows had age
(`ts_recv_ns − ts_exchange_ns`) above 60 s in 10,923 of 531k rows (542 markets; p99 63 s,
max 1,326 s). They are mostly off-the-money strikes during the US session. In 2,581
silences longer than 120 s on 10-13 there were **0 trades** on the silent market. A trade
consumes resting size and would have produced a delta, so these were quiet books rather
than lost deltas. From 2026-10-14 the silence audit checks this live (`audit_stale`
should stay 0).

How to filter each file vintage:

| Files | Staleness | Zero/dust touch |
|---|---|---|
| before 2026-10-14 11:00 UTC (no `age_s`) | Kalshi WS: `age = (ts_recv_ns − ts_exchange_ns)/1e9`. `ts_exchange_ns` is the last delta's venue time, or the snapshot send time after a (re)connect. Polymarket WS: same formula (venue `timestamp`). Kalshi/Polymarket US REST: each row is a fresh poll, so age is about 0. Cap at e.g. `age <= 10` for books whose market maker re-quotes every 1-2 s | `best_* is not null` and `*_sz[0] >= 1` |
| from 2026-10-14 11:00 UTC | `age_s <= cap` (`last_update_ns` is local clock, so it is free of venue clock skew) | same |

Mixed day folders: 2026-10-14 contains files with and without the two new columns.
`pd.read_parquet(dir)` takes its schema from the first file, so it can silently drop them.
Read with an explicit schema (`pq.read_table(dir, schema=src.recorder.writer.BOOK_SCHEMA)`)
or file by file. Compaction fills the missing columns with nulls (`writer.conform`).

Regression tests: `tests/unit/test_recorder_integrity.py`.

## Storage with discovery (measured 2026-10-13, 11:00-12:00 UTC)

This hour is the first full one with every group live: 2,037 Kalshi markets on 4
connections, 21 Polymarket crypto tokens, 10 static pairs and RTDS. It is pre-US-open, with
crypto active and index/econ quiet.

| File | MB/h | Rows/h | B/row |
|---|---|---|---|
| kalshi books | 10.55 | 282,124 | 37 |
| kalshi trades | 3.76 | 139,652 | 27 |
| polymarket books (crypto + pairs) | 1.60 | 38,703 | 41 |
| polymarket trades | 0.68 | 12,209 | 56 |
| polymarket_rtds prices | 0.52 | 21,418 | 24 |
| everything else (markets, bbo, polymarket_us) | 0.09 | | |
| **total** | **17.2** | | ≈ **0.41 GB/day** at this rate |

Kalshi books by group (MB/h): mentions 2.2, weather 1.8, crypto-daily 1.7, crypto-hourly 1.4,
commodities 1.2, index-hourly 1.1, econ 0.8, crypto-15m 0.3, index-daily 0.1, static pairs 0.1.

US-session hours and release spikes (CPI 12:30 UTC) will be busier. Expect roughly
**0.5-1.0 GB/day**, under the 1.5 GB/day budget. If a day goes over:

1. Drop `k-mentions` `depth` to 3.
2. Raise `heartbeat_s` on `k-weather-high`.
3. Lower `nearest_strikes`.
4. Raise `defaults.ws_sample_interval_s` to 2.

Check with `du -sh data/live_books/*/$(date -u +%F)`.

Integrity in the same hour:

- **Kalshi:** 0 crossed or locked books in 282k rows, 0 `gap` rows, no WS errors or
  resyncs. Rollover sent 1,802 `add_markets` and 302 `delete_markets` with no false gaps.
- **Polymarket static pairs:** stored books agree 100% (as-of join) with the venue's own
  `best_bid_ask`.
- **Polymarket crypto groups:** 61 BBO mismatches in 5.2M checks. Each one triggers a REST
  resync.
- **Polymarket 5m socket:** reconnected 26 times in the hour (`1013 slow consumer`, see above).

**Process:** about 18% of one core and about 300 MB of memory (higher than the static-only
<100 MB because of ~2,000 books plus up to 5 min of buffered rows).

## Storage estimate, static pairs only (zstd Parquet, depth 10; 2026-10-12)

Measured 2026-10-12 with the shipped 30-instrument config during two live LCS games
(180 s, real Parquet output):

| Table | Rows / 180 s | Bytes/row (small part files) |
|---|---|---|
| kalshi books / trades | 453 / 1,670 | 66 / 33 |
| polymarket books / bbo / trades | 252 / 109 / 19 | 74 / 52 / ~290 |
| polymarket_us books / trades | 57 / 25 | ~200 |

That is 131 KB per 3 minutes, or **about 63 MB/day if games were live all day**. With a
realistic mix (games live a few hours, Fed and futures quiet otherwise) expect **about
20-40 MB/day**. Hard ceiling: if all 30 books changed every second for 24 h (1 s sampling)
and Kalshi printed trades at the measured peak of ~9/s all day, the total is
30 x 86,400 x ~70 B + ~26 MB, or **about 210 MB/day**. The config therefore stays under the
300 MB/day budget even in the worst case. Hourly compaction removes part-file overhead.

**Retention / disk (C: had ~31 GB free on 2026-10-12).** At ~40 MB/day a month is about
1.2 GB. Nothing is deleted automatically. Housekeeping:

- Check usage: `du -sh data/live_books/*` (Git Bash).
- Keep the last 30 days locally and move older date folders to other storage. List them with
  `find data/live_books -mindepth 2 -maxdepth 2 -type d -name '20*' -mtime +30`, review
  the list, then move it.
- To save more: lower `depth` (10 -> 5 roughly halves book-row size), raise
  `ws_sample_interval_s`, or drop finished-game pairs from the config promptly.
- If free space on C: drops under ~10 GB, stop the recorder (`start_recorder.ps1 -Stop`).

## How this feeds the analysis

1. **Cross-venue arb gaps.** Join pair legs on `pair` with an as-of join on `ts_recv_ns`
   (tolerance ≤ 2 s; filter `gap == False`).
   - For a YES/YES pair, the arb exists if `ask_A(YES) + ask_B(NO) < 1 − fees`.
   - On Kalshi, the NO ask is `1 − best_bid` in YES terms. On Polymarket, it is the
     no-token ask.
   - Use `ask_sz`/`bid_sz` cumulated over levels to get executable size at each edge.
     Then measure how long each gap persists. A gap that lasts less than one poll
     interval is not capturable without WS.
2. **Maker opportunity.**
   - Spread: `best_ask − best_bid` over time.
   - Depth at the touch, and queue size ahead of a new quote.
   - Trade arrival rate and size at or through the touch (the `trades` table).
   - Adverse selection: the mid move after trades.
3. **Feed latency.** `ts_recv_ns − ts_exchange_ns` per venue tells you which venue
   leads. Kalshi REST trades lag about 7 s median at a 15 s poll. Use WS keys for
   latency-sensitive work.

## Code map

- `src/recorder/universe.py`: the live market set (static + discovered), rollover, local
  expiry, sticky shard assignment
- `src/recorder/series_discovery.py`: Kalshi series/category and Polymarket tag discovery
  (pure selection functions plus async refresh loops)
- `src/recorder/venues/polymarket_rtds.py`: RTDS reference prices
- `src/recorder/settlement.py`: Kalshi close/settlement status log (`settlement:`)
- Tests: `tests/unit/test_recorder_elections.py` covers sampling windows, series/event regexes and the settlement log
- Tests: `tests/unit/test_recorder_discovery.py` covers discovery, rollover, shards, the
  dynamic Kalshi WS sequence handling and RTDS parsing

- `src/recorder/book.py`: order-book state
- `src/recorder/sequence.py`: gap detection
- `src/recorder/sampler.py`: when to write rows
- `src/recorder/writer.py`: Parquet sinks and compaction
- `src/recorder/config.py`: YAML parsing
- `src/recorder/venues/{kalshi,polymarket,polymarket_us}.py`: pure parsers plus async feeds
- `src/recorder/runner.py`: orchestration (plus STOP-file shutdown)
- `scripts/live/start_recorder.ps1`: detached start / status / stop on Windows
- `src/recorder/discovery.py`: pair discovery
- Tests: `tests/unit/test_recorder_book.py`, `tests/unit/test_recorder_io.py`, `tests/unit/test_recorder_integrity.py` (fixtures
  recorded from live payloads, no network)

## Auto-restart watchdog (2026-10-14)

The scheduled task `predict-ngin-collectors` runs `scripts/live/watchdog.ps1` at logon and every 15 minutes. It starts the recorder, the fin_E ForecastEx and Gemini collectors, and the wxlive ASOS observation collector only when they are not already running, so it never creates duplicates. It never starts anything whose STOP file exists (`data/live_books/STOP`, `data/vendor/fin_E/live/STOP` or `data/vendor/wxlive/STOP`). Restarts are logged to `data/live_books/watchdog.log`.

The task runs only while the user is logged on. To also cover a reboot that happens before anyone logs on, re-run the installer from an elevated PowerShell, which switches the task to S4U plus an at-startup trigger.

- Install or update: `powershell -ExecutionPolicy Bypass -File scripts\live\install_watchdog.ps1`
- Remove: `powershell -ExecutionPolicy Bypass -File scripts\live\install_watchdog.ps1 -Remove`

To stop a collector for good, create its STOP file or remove the task. If you only kill the process, the watchdog restarts it within 15 minutes.

## wxlive ASOS observation collector (2026-10-14)

`scripts/research/wxlive_obs_collector.py` is a side collector for the forward test of `wx_5min_feed_edge_taker`
(pre-registration: `docs/WXLIVE_PREREG.md`). It is read-only, free and uses no keys. It records **when** each
weather observation for the 18 `k-weather-high` stations becomes visible from free sources. The evaluator joins it
with this recorder's Kalshi weather books.

**Sources, all in one thread.** Requests are spaced ≥ 1.05 s apart (≤ 1 req/s in total), with a 30 s cycle and a
descriptive User-Agent.

| Source | Request | What it carries |
|---|---|---|
| `madis` | 1 national request per cycle to the MADIS public CGI, providers `ASOS-HFM` and `ASOS`, last 25 min | 5-minute obs (whole °C) plus METARs (0.1 °C) |
| `nws` | `api.weather.gov/stations/{ICAO}/observations/latest` × 18 per cycle | latest ob only: 5-minute obs (whole °C, empty `rawMessage`) or METAR; CDN `s-maxage=300` |
| `iem` | IEM `asos.py`, all 18 stations, every 10 min | MADIS HF and METAR archive; low-rate cross-check |

**Backoff.** On an error, 429 or 5xx, the source (per station for `nws`) backs off 30 s → 600 s and honours
`Retry-After`.

**Output** (`data/vendor/wxlive/`):
- `obs/{madis,nws,iem}/YYYYMMDD.jsonl.gz` has one line per **new** observation:
  `recv, prev_poll, src, stn, obs_ts, obs_epoch, temp_c, qc, kind (hf|metar), id, boot`.
  - The observation arrived in `(prev_poll, recv]`.
  - `boot = true` means it was first seen on the first poll after a start, so its arrival time is unknown.
- `obs/polls/YYYYMMDD.jsonl.gz` has one line per request (`t, src, stn, ok, http, dur, n_new, err`). The coverage rule
  uses it.
- `collector.log` holds start/stop/backoff lines; `collector.pid` holds the running PID.
- Volume: about 0.8 GB/day downloaded (the national MADIS reply is ~270 KB per 30 s; the CGI ignores station lists and boxes) and only a few MB/day stored.

**Control.**
- Start or keep alive: the watchdog does it, or run `uv run python scripts/research/wxlive_obs_collector.py`
  (it refuses to start a second copy).
- Stop for good: create `data/vendor/wxlive/STOP`.
- Test: `... wxlive_obs_collector.py --once`.

**Latency found on 2026-10-14** (`recv − obs time`):

| Source | 5-minute obs latency |
|---|---|
| MADIS public | p10 6.9 / median 7.5 / p90 16.9 min (40 obs, first 15 min of running) |
| `api.weather.gov` | median 18 min (p10 13, p90 18.6; 21 obs); responses carry `s-maxage=300` |
| IEM | ≈ 25 min for 5-minute obs (one-off probe); 12 min for one METAR |

None of these is the ~3-minute feed the backtest assumed. Running numbers are in `data/vendor/wxlive/eval/latency.parquet`
and the evaluator log.

**Evaluator:** `uv run python scripts/research/wxlive_paper_eval.py`. Run it daily; it is idempotent.
- It reads books file by file with `BOOK_SCHEMA` + `conform`, and caches compacted hours for weather markets in
  `data/vendor/wxlive/books_cache/`.
- It settles via the public Kalshi `/markets?event_ticker=` endpoint (no key, ≤ 1.5 req/s).
- It writes `data/vendor/wxlive/eval/{signals,latency,settle}.parquet`, `summary.json` and
  `data/vendor/wxlive/logs/eval_YYYYMMDD.txt`.
- `--diagnostic` also evaluates uncovered station-days and boot-triggered crossings. It is a pipeline check only and is
  never used for promotion.

