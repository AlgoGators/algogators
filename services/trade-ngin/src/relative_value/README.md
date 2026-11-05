# Relative Value Module

## Overview

`src/relative_value/` reads and checks **relative-value trade definitions**: one JSON object per
trade, naming its legs, the inputs it reads, how its series is built from them, the signals and
diagnostics to compute on that series, and how it would be paper traded. The module parses a
definition, checks it, and gives its canonical text. It computes no series, signal or position,
and **no runner uses it yet**: the only callers are its tests.

The two-leg
`SpreadStrategy` (`src/strategy/README.md`, "The spread strategy") is a separate piece and does not
read these definitions.

---

## File layout

```
src/relative_value/
├── trade_definition.cpp      # the parser: JSON types, unknown keys, required blocks; canonical text
├── definition_validator.cpp  # what the values mean: symbols, names, bounds, references, cycles
├── analysis_registry.cpp     # the registry of named analyses
├── builtin_analyses.cpp      # the built-in series rules, signals, diagnostics, forecast methods
├── param_schema.cpp          # a parameter schema: types, defaults, bounds, unknown keys
└── contract_lookup.cpp       # a leg's contract size from metadata.contract_metadata

include/trade_ngin/relative_value/
├── trade_definition.hpp      # TradeDefinition, Leg, InputSpec, PackageSpec, SeriesSpec, SignalSpec, TradeBlock
├── definition_validator.hpp  # ValidationContext, validate_trade_definition, validate_definition_set
├── analysis_registry.hpp     # AnalysisKind, AnalysisDescriptor, AnalysisRegistry
├── param_schema.hpp          # ParamSpec, ParamSchema, ValidationIssue
└── contract_lookup.hpp       # contract_root, make_contract_lookup

config_template/trades/       # seven example definitions, read by the tests
tests/relative_value/         # the parser and validator tests, and one fixture
```

All six sources are in `TRADE_NGIN_SOURCES` (the root `CMakeLists.txt`), so the library is
built with every app; nothing in `apps/` includes these headers.

---

## A definition

| Key | Required | What it holds |
|---|---|---|
| `id` | yes | 1 to 50 letters, digits, `_` and `-`, with no `--`; unique in a set; the file name of a template |
| `description` | no | text |
| `legs` | yes, a list | each `{"symbol", "asset_class"}`, `asset_class` `"futures"` or `"equity"`. `[]` makes the definition **analysis only**: it is computed and stored, never traded. All legs must be one asset class |
| `inputs` | yes, a list | named series the definition reads: `source` `"leg"` or `"instrument"` (a symbol), `"series"` (a `table` and `key`, such as a macro series), or `"trade"` (another definition's series by `trade_id`) |
| `series` | yes | `{"rule": ...}` plus that rule's parameters: how the series is built from the inputs |
| `signals` | no | each with `name`, `type`, `sign`, an optional `input`, an optional `forecast` block (`method` plus its parameters), and the type's parameters |
| `diagnostics` | no | names of the form `<name>_<window>` |
| `package` | with legs | `mode` `"fixed"` (signed whole contracts per leg; keys exactly the leg symbols) or `"derived"` (`scale`, `rebalance_threshold` 0.10 when absent, and `weights` when the series rule has none) |
| `trade` | with legs | `blend` (signal name to weight; non-negative, summing to 1), `risk_target`, `capital` |
| `history_start` | no | an ISO date |
| `strategy` | no | the name of a specialised strategy |

A definition holds only values its author chose, never values the code derives (no contract
multipliers, fitted betas or derived contract counts), so it can be stored as it was written
(`trade_definition.hpp`).

**The built-in analyses** (`builtin_analyses.cpp`):

| Kind | Names |
|---|---|
| Series rules | `fixed`, `ratio`, `estimated`, `neutral`, `forward` |
| Signals | `zscore`, `ewmac`, `momentum`, `percentile`, `seasonal`, `value_vs_anchor`, `info_ratio_signal`, `forward_gap`, `equity_risk_premium`, `implied_growth_gap`, `relative_pe` |
| Diagnostics | `adf`, `kpss`, `coint`, `hurst`, `vr`, `half_life`, `factors` |
| Forecast methods | `linear`, `scalar`, `fixed_scalar`, `percentile`, `none` |

Each has a parameter schema with types, defaults and bounds; the validator checks a definition's
parameters against it.

---

## Parsing and validating

Two steps, each returning a list of issues (a path such as `signals[2].window` and what is wrong);
an empty list means the definition passed.

1. **Parse** (`parse_trade_definition`, `trade_definition.cpp`): the structure. Right JSON types,
   no unknown key at any level, and the blocks that must exist (`id`, `legs`, `inputs`, `series`
   with a `rule`).
2. **Validate** (`validate_trade_definition`, `definition_validator.cpp`): the meaning. Among the
   checks:
   - every series rule, signal type and forecast method is a registered one, and its parameters
     pass its schema and its own cross-checks (a fast span below a slow one, and the like);
   - every input a parameter names is a declared input, and a definition never reads its own series;
   - a definition with legs has a `package` and a `trade` block; an analysis-only one has neither;
   - a leg symbol has a `metadata.contract_metadata` row, and that row has a contract size: a
     blank size is refused, because the engine would size the leg as 1.0;
   - each key the definition would write fits the 50-character `symbol` column of the stored
     signals.

   Every outside lookup (the contract table, whether a series exists, the first date with data,
   the other definitions) is optional in `ValidationContext`: a caller without a database
   supplies only what it wants checked. `validate_definition_set` also checks that ids are unique
   and that `trade` inputs form no cycle.

`canonical_text` gives the definition with sorted keys and no whitespace, the text that is hashed
and stored.

---

## The templates in `config_template/trades/`

| File | What it is |
|---|---|
| `crack_321.json` | the 3-2-1 crack spread: two RB and one HO against three CL |
| `gold_silver.json` | the gold/silver ratio, a notional-neutral GC/SI package |
| `copper_gold_zn.json` | the copper/gold ratio as a growth signal, traded on one leg, ZN |
| `yield_spread_10y_2y.json` | the 10-year less 2-year Treasury yield, analysis only |
| `curve_2s10s.json` | a ZT/ZN steepener that reads the yield spread as a `trade` input |
| `nasdaq_vs_sp.json` | MNQ against MES, beta-neutral |
| `fly_zt_zf_zn.json` | a ZT/ZF/ZN butterfly, long the wings |

The test `DefinitionValidator.TheFirstSetFilesAndTheFlyValidateAsASet`
(`tests/relative_value/test_definition_validator.cpp`) parses all seven and validates them as one
set; each file's name must equal its `id`. No runner reads the folder.

---

## Testing

```bash
cd build
ctest -R "TradeDefinitionParser|DefinitionValidator|AnalysisRegistry|ParamSchema|ContractLookup" --output-on-failure
```

---

## References

- [Strategy Module](../strategy/README.md): the spread strategy, which does not read these definitions
- [config_template/README.md](../../config_template/README.md): the template folders
