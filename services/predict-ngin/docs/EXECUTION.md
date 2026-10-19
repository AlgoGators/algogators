# Execution (`predict_ngin.execution`)

Last revised: 2026-10-17

Order placement for Kalshi and Polymarket US. **Live trading is off by default.** Nothing in the repo places
a real order unless the person running it arms three separate gates.

## Modes

| Mode | Keys | Sends orders | Fills |
|---|---|---|---|
| `dry_run` (default) | no | no | none; the journal records what would have been sent |
| `paper` | no | no | simulated against public venue data, queue-aware on Kalshi (`paper.py`) |
| `live` | yes | yes | real |

```python
from predict_ngin.execution import open_broker, OrderIntent

b = open_broker("kalshi", paper=True)       # or live=True (needs the gates below)
ack = b.place(OrderIntent(venue="kalshi", market="KXINXU-...", outcome="yes", price=0.42, quantity=1))
b.cancel(ack.order_id)
```

Every order passes the same risk limits in every mode, so a strategy behaves the same in paper and live.

### Paper mode details

- **Fills:** an order joining the best price waits behind the displayed size; public trades at its price
  eat that queue first. A trade through its price, or the other side crossing it, fills the rest. Stepping
  inside the spread starts first in line. Cancellations ahead never advance the queue, so results are
  cautious.
- **Requests (Kalshi):** batched. `prefetch(markets)` loads every top of book with one
  `GET /markets?tickers=...` (50 per request), and each `poll()` reads the global public trade feed once.
  Trades count once each (by id), and only if they come after the order was placed. Requests are paced to
  `max_rps`, and a 429 is retried with backoff.
- **Signed reads:** `paper_opts={"signer": KalshiSigner()}` signs the GETs. They then count against the
  account's read limit, not the IP's, which other jobs on the same connection can exhaust. They are still
  reads only.
- **Limits and journal:** a paper or dry-run broker can take its own `limits=RiskLimits(...)` and
  `journal_dir=`, for example a market maker that re-quotes all day. A live broker refuses both and always
  uses `config/live.local.yaml` and the live journal.

## The three live gates

1. Environment variable `PREDICT_NGIN_LIVE=1`.
2. `config/live.local.yaml` (gitignored; copy `config/live.example.yaml`) with `live.enabled: true` and the
   venue listed in `live.venues`.
3. The caller passes `live=True` (CLI: `--live`).

If any gate is missing, a live request raises `MutationBlocked`. It never falls back silently to dry run.
At the transport level, every non-GET request needs a permit that only the gated path issues. Calling a
venue client's send method directly cannot mutate anything.

## Kill switch

`predict-ngin trade kill on` (creates `data/live/STOP`), or `kill: true` in the config. While the switch is
on, new orders are refused everywhere, but cancels still work. `predict-ngin trade cancel-all --venue kalshi
--live` cancels every resting order on the account.

## Risk limits

The defaults are deliberately tiny:
- $5 per order, $25 per day, $10 per market;
- 5 open orders, 100 contracts;
- prices from 1c to 99c;
- post-only only, no sells.

Override them under `limits:` in the local config. Unknown keys are an error.

## Credentials

- **Kalshi:** `KALSHI_API_KEY_ID` and the private key path (`KALSHI_PRIVATE_KEY_PATH`), the same as the read
  client in `predict_ngin.venues.kalshi`.
- **Polymarket US:** `POLYMARKET_US_KEY_ID` and `POLYMARKET_US_SECRET_KEY` (Ed25519). Create them at
  polymarket.us/developer; KYC is required.

Keep these in `.env`. Never commit them. The journal redacts anything that looks like a key or signature.

## Journal

Every intent, limit rejection, request, response and cancel is appended, with fsync, to
`data/live/journal/<UTC date>.jsonl` (each line carries venue and mode).

## CLI

```
predict-ngin trade status [--venue kalshi]        # gates, kill switch, limits, balance (read-only)
predict-ngin trade latency-test --venue kalshi --live [--market T] [--trials 3]
predict-ngin trade cancel-all --venue kalshi --live
predict-ngin trade kill on|off
```

The latency test places a post-only BUY of 1 YES at 1c in a market whose best YES bid is at least 10c, so it
cannot fill in practice. It times create, visibility and cancel, then cancels the order. The worst case per
trial is 1c.

## Venue notes

- **Kalshi:** Create Order V2 (`POST /portfolio/events/orders`) quotes the YES leg. A NO buy at p is sent as
  an ask on YES at 1-p.
- **Polymarket US:** `POST /v1/orders` with `participateDontInitiate` for post-only. Cancel is
  `POST /v1/order/{id}/cancel`. The rate limit is 25 requests/s per IP.
