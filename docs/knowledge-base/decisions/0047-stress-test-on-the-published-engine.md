# 0047. The stress test runs on the published engine, unchanged

- **Status:** Accepted
- **Date:** 2026-11-02
- **Sources:** `apps/stress-test/` (README, `src/stress_test/replay.py`)

## Context

The black-swan stress test (`apps/stress-test`) replays historical events against the book held on
a day and against proposed edits. Its dynamic tier wants the trade-ngin strategy to trade through
each event starting from the stored book. The published `tradengin` module cannot do that: every
backtest starts flat, and nothing in its config, bindings or hooks sets a starting book. Its `Bar`
carries no contract id and its `FuturesSpec` no per-contract fee either. A copy or patch of the
engine inside the tool would break on every engine update.

## Decision

The tool uses the published module unchanged, through its documented surface only (a book run from
a config directory, a `MarketDataSource`, the CSV export), and reads at run time what the installed
module offers. For each event:

- the strategy production recorded for the day trades from flat over the last sessions before it
  and arrives with its own book;
- the tested book is that book plus its difference from it; a symbol's difference lasts until the
  strategy itself trades the symbol (`--difference held` keeps it through the event);
- an engine that reads no contract ids gets the history with each roll's gap taken out (the roll
  bar's return set to 0), which is how production books a roll.

`stress-test replay-check` reports how far the strategy's own book is from the stored one.

## Consequences

- No engine change is needed, and the tool keeps working across engine releases. When a module
  version exposes contract ids or per-contract fees, the tool hands them over without a change.
- Measured on the conservative QT book on 2026-11-02, against a replay started from the stored book
  itself: the worst point per event was off by 0.4 points of the account on average (median 0.01)
  over 39 events at two severities, against 1.7 for holding the book unchanged. The largest miss,
  about 7.5 points, was the longest event (the 364-session euro-area crisis).
- An exact replay of the stored book would need the engine to accept a starting book (the book, the
  account and the sizing peak, seeded into the strategy, its PortfolioManager slot and the filled
  ledger on the first traded cycle). That is an engine change to propose on its own, not part of
  this tool.
