# Instruments

What a symbol is to the engine: its contract size, tick, fee, margin and trading hours. Headers are
in `include/trade_ngin/instruments/`, sources in `src/instruments/`.

## The registry

`InstrumentRegistry` (`src/instruments/instrument_registry.cpp`) is one shared registry per
process.

- `initialize` and `load_instruments` read every row of `metadata.contract_metadata` and build a
  `FuturesInstrument` per futures row: the multiplier from "Contract Size", the tick, the fee from
  "Fee Per Contract", the initial and maintenance margins from "Overnight Initial Margin" and
  "Overnight Maintenance Margin", and the trading hours. A row whose "Contract Size" is missing or
  not positive is given 1.0 with a warning in the log, so a bad metadata row sizes the contract as
  one unit instead of stopping the run: check the log after any metadata change. The table and its
  rows are described in [docs/DATA_SOURCES_OF_TRUTH.md](../../docs/DATA_SOURCES_OF_TRUTH.md).
- `get_instrument` resolves a symbol to its row; the `.v.` suffix of the vendor's continuous symbol
  is stripped first, so `ZN.v.0` finds `ZN`.
- `load_equity_instruments` registers each equity symbol of the equity book with fixed defaults:
  exchange from `data/equity_exchanges.json`, which the equity runners pass (NYSE when a symbol is
  not listed or the file cannot be read), tick 0.01, commission 0.005
  per share. The commission here is only what the instrument reports; the cost a fill is charged
  comes from the transaction cost manager ([docs/COST_MODEL.md](../../docs/COST_MODEL.md)).

## Futures

`FuturesInstrument` (`src/instruments/futures.cpp`): the notional of a position is quantity times
price times the multiplier, and the margins are the per-contract figures of the metadata row. The
live margin calculation is `MarginManager` (`src/live/margin_manager.cpp`); it refuses an initial
margin that is not positive and uses the initial margin when a row's maintenance margin is not
positive.

## Equities and the margin rule

`EquityInstrument` (`src/instruments/equity.cpp`) carries an account mode, and the margin a
position ties up follows it (`get_margin_requirement`):

| Account mode | Long position | Short position | Maintenance margin |
|---|---|---|---|
| `CASH` (the default) | 100 percent of the notional | not allowed | none: the shares are paid for in full |
| `REG_T` | 50 percent of the notional (the Federal Reserve's Regulation T initial margin) | 150 percent: the short sale's proceeds held as collateral plus 50 percent | the same as the initial margin |

The registry never sets `REG_T`, so every equity the engine runs is held in a cash account: the book cannot borrow
against its shares and cannot sell short. The default keeps every equity long only and unleveraged (the comment beside
`account_mode` in `include/trade_ngin/instruments/equity.hpp`); a cash account posts the full price, so it has no margin
call, no borrow fee and no forced sale. A short needs both
`REG_T` and `short_selling_allowed`; otherwise the mean-reversion strategy clamps a short target
to zero with a warning (`src/strategy/mean_reversion.cpp`), and the equity runners refuse to start
when the book's `max_gross_leverage` is above 1.0 and any of its equities is in a cash account. The Reg T arithmetic is
there for the planned margin and shorting work (AlgoGators/algogators#139 and #140); the equity
book itself is in [docs/EQUITY_STRATEGY_GUIDE.md](../../docs/EQUITY_STRATEGY_GUIDE.md).

## Options

`OptionInstrument` (`src/instruments/option.cpp`) holds an option's specification. No runner
creates one.
