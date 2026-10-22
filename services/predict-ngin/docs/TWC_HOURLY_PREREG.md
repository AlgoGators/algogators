# TWC / Kalshi Weather Index hourly temperature markets: pre-registration (twc, written 2026-10-14 before any price data was loaded)

Last revised: 2026-10-15

Research only. This file fixes the rules **before** any Kalshi price or trade data for the hourly temperature
series was loaded. The only things known when it was written:

- The series metadata (frequency `hourly`; settlement_sources `weather.com/kalshi` for the older `KXTEMP*H` series,
  `synopticdata.com` / `kalshi.com` for the `*HS` series).
- The CHIMETROWINDEX rulebook (CFTC filing of 2026-09-09):
  - The underlying is the **Kalshi Weather Index**: an equal-weight, offset-adjusted mean of 5 HF-ASOS one-minute
    stations delivered by Synoptic.
  - One canonical point is published per event minute at t + 300 s and is never revised.
  - "at" contracts settle on the last canonical point with t ≤ S.
  - Last trading time is the end of the time period, i.e. S.
- The public index endpoint (`/live_data/weather/{city}`, no auth) returns:
  - canonical points;
  - per-station "pending" readings (temp_f, received_at_ms), about 2.5 min after the event minute;
  - weekly calibration offsets.
- One day of recorder trades for KXTEMPMIAH: trades end before S; each event trades about 45 min.

## Universe (no selection on outcome or volume)

The universe is every settled market of every hourly temperature series returned by `/series` (category Climate and
Weather, frequency hourly, "Temp" in the title), at any volume.

Markets are split into two eras by rules text:

| Era | Settlement source |
|---|---|
| TWC | "according to The Weather Company" |
| KWI | "Synoptic Data ... Kalshi Weather Index" |

H2 and H3 are tested only on KWI-era markets whose city has an index history.

## H1. Post-reading latency taker

- **Hypothesis.** Trading stays open after the stated reading time S. If so, the reading is knowable before trading
  closes.
- **Test.** In the trade tape (`/markets/trades`) and the recorder, count prints with ts > S, and quotes with
  ts > S + 5 s.
- **Rule.**
  - Viable only if prints after S exist on ≥ 5% of events.
  - Then: buy the side implied by the canonical index point for S at any ask ≤ 97¢, once the point is public
    (S + 300 s).

## H2. Intra-hour index-convergence taker

**Decision times.** τ = S − k min, for k ∈ {1, 2, 3, 5, 10}.

**Information at τ.** Two variants:

| Variant | What is known at τ |
|---|---|
| (C) canonical | The last canonical point with t ≤ τ − 300 s (published by spec at t + 300 s). |
| (P) pending | The latest minute t whose pending station readings were received ≤ τ (received_at_ms ≤ τ), with ≥ 4 members. The index is recomputed as mean(x_i − b_i) + B_c, using the calibration in effect at t. On train it is checked against canonical v (must match to ≤ 0.05 °F on ≥ 95% of minutes, else P is dropped). |

**Fair value.**
- p = P(I(S) > K | I_last, g), where g = (S − t_last) in minutes.
- It comes from the empirical distribution of I(t + g) − I(t), per city and g, estimated **on train dates only**.
- All hours are pooled, with a ±0.005 °F tie guard.

**Prices.**
- Use the Kalshi 1-minute candle whose period ends at τ.
- YES ask = `yes_ask.close` and YES bid = `yes_bid.close`. The NO ask is 1 − YES bid.
- Skip the decision if the needed side is missing.

**Trade rule.**
- Buy YES if p − ask − fee ≥ θ.
- Buy NO if (1 − p) − (1 − bid) − fee ≥ θ.
- θ ∈ {2, 4, 8} ¢.
- Fee is the taker fee ceil(0.07 · C · P · (1 − P)) per fill, at C = 10.
- Each (k, θ, variant) cell is evaluated separately, with at most one trade per market per cell.

**Size.** 10 contracts per trade. Candles carry no depth, so for KXTEMPMIAH the recorder top-of-book size (levels ≥ 1)
at τ is reported as the depth check.

**Split.**
- Train: KWI-era event dates ≤ 2026-09-30.
- Test: 2026-10-01 → last settled date.

**Grid and pass bar.**
- The grid is 5 k × 3 θ × 2 variants = 30 cells.
- Pass requires all of:
  - test ¢/contract ≥ +2;
  - event-day cluster bootstrap CI > 0;
  - in ≥ 2 adjacent k for the same θ and variant;
  - DSR vs 30 local and about 8,000 repo-wide trials reported.

## H3. Spike / QC fade (TWC- and KWI-specific)

**KWI hourly.**
- **Event:** a member station's pending one-minute reading deviates from the offset-adjusted median of its peers by
  ≥ 2 °C.
- **Effect:** the spatial QC will reject it, so the published index moves by less than the raw-mean change.
- **Test:** whether the YES mid of the nearest-strike market moves in the spike's direction in the 1–3 minutes after
  the spike receipt, and reverts by S.
- **Taker:** fade at the candle ask/bid at receipt + 1 min, with the same fee/θ rules as H2 (θ = 4¢).
- **Report:** N, ¢/contract, CI. Expect a tiny N.

**Daily (TWC) markets.**
- **Event:** a METAR/5-minute single-report spike, i.e. the reading exceeds both neighbouring reports by ≥ 3 °F and
  sets the running max.
- **Test:** whether TWC's final max (Kalshi expiration_value) includes it (it follows CLI), and whether the market
  priced it.
- **Decision:** descriptive only, because the TWC era has just 59 days of candles.
