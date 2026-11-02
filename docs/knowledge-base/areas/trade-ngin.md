# trade-ngin: the engine

**LAST UPDATED:** 2026-11-02 · **Commit:** `8c534c1b` (main, the code this page was checked against)

A map of `services/trade-ngin/` for someone about to change it. Deploys, CI, the QT day, the daily
e-mail and the move into this repository are not repeated here: see [operations.md](../operations.md),
[decisions/](../decisions/README.md), [gotchas.md](../gotchas.md) and [where-things-live.md](../where-things-live.md).
Paths below are relative to `services/trade-ngin/` unless they start at the repository root.

## What it is

A C++20 engine that runs backtests and live (paper) daily runs of the futures and equity books, and
stores every position, fill and result in PostgreSQL. The same Docker image carries the Python
service `algogators_rpc` (`services/rpc/`): a gRPC server with the QT desk service, the catch-up
scheduler and the daily report (`docs/design/rpc.md`, `docs/design/desk-service.md`; protos in
`proto/algogators/`). The `tradengin` Python module wraps the backtest side (`python/`).
`docs/SYSTEM_ARCHITECTURE.md` is the long-form map; this page is the short one.

## The books

Each book is a folder under `config_template/portfolios/`; the runner reads it from `./config/portfolios/<dir>/`.

| Folder | `portfolio_id` | Trades | Backtest / live binary (source) | Runs in production |
|---|---|---|---|---|
| `conservative` | `CONSERVATIVE_PORTFOLIO` | 36 futures, one trend sleeve, 500,000 | `bt_portfolio_conservative` / `live_portfolio_conservative` (`apps/backtest/bt_portfolio_conservative.cpp`, `apps/strategies/live_portfolio_conservative.cpp`) | the old cron's book: `live_portfolio.cron` runs it at 09:30 every calendar day through `scripts/run_live_portfolio.sh`. That cron is being retired (`docs/design/qt-contract.md` D-4) and the move to new_algo_data is #185 |
| `qt_conservative` | `QT_CONSERVATIVE_PORTFOLIO` | the conservative book with desk editing on (`"qt": {"desk_editable": true}`) | `live_portfolio_conservative --portfolio-config qt_conservative` (`scripts/qt_model_run.sh`) | yes: the desk's catch-up scheduler in `engine-rpc` (`QT_CATCHUP_PORTFOLIOS`, `deploy/engine-rpc.compose.yml`) |
| `qt_conservative_model` | `QT_CONSERVATIVE_MODEL_PORTFOLIO` | the untouched model twin | same binary | no: retired (qt-contract D-1); its rows stay |
| `qt_e2e` | `QT_E2E_PORTFOLIO` | test book for `scripts/qt_e2e.sh` | same binary | no |
| `base` | `BASE_PORTFOLIO` | two trend sleeves (`TREND_FOLLOWING` 0.7, `TREND_FOLLOWING_FAST` 0.3); a placeholder test book, the only one whose sleeves net | `bt_portfolio` / `live_portfolio` (`bt_portfolio.cpp`, `live_portfolio.cpp`) | no schedule; by hand only (`docs/performance_upkeep.md` section 3) |
| `equity_mr` | `EQUITY_MR_PORTFOLIO` | US equities, mean reversion, 100,000 | `bt_equity_mr` / `live_equity_mr` (`bt_equity_mean_reversion.cpp`, `live_equity_mean_reversion.cpp`) | no schedule; by hand only |

Other binaries: `bt_equity_validation` (recomputes an equity backtest independently),
`qt_settings_check` (would a live run accept these desk settings; the desk calls it before AlgoLens
saves a `strategy_config` version) and `contract_rolls_dump` (the roll rule's held series as CSV), in
`apps/tools/`. The QT books have three books a day in the same tables (`system`, `qt_proposal`, `qt`,
column `portfolio_type`; migration 021, `docs/design/qt-contract.md` section 3).

## Strategies, sizing and risk

| Piece | What | Main files |
|---|---|---|
| Trend following | Carver's EWMAC trend (*Advanced Futures Trading Strategies*), six speed pairs, 20% risk target; `TREND_FOLLOWING_FAST` is four pairs at 25%. Long and short | `src/strategy/trend_following.cpp`, `trend_estimator.cpp`; `docs/TREND_FOLLOWING_SYSTEM.md` |
| Carry | Carver's carry blended into the trend forecast. In the code, off in every template (`c7cec212`); needs `per_contract_rolls` and one trend sleeve. Tuning is #186 | `src/strategy/carry_estimator.cpp`, `include/trade_ngin/data/carry_quote.hpp`; `config_template/README.md` |
| Mean reversion | The equity strategy: z-score entry and exit, stop loss | `src/strategy/mean_reversion.cpp`; `docs/EQUITY_STRATEGY_GUIDE.md` |
| Spread (pairs) | A two-ticker spread strategy (hedge ratio, z-score). No runner builds it; fixed in #102 | `src/strategy/spread.cpp` |
| Relative value layer (new) | Trade definitions (legs, inputs, signals) parsed and validated, and the analysis registry's descriptors; the computing comes later (#101). Example definitions in `config_template/trades/`. Plan: #126, #127 | `src/relative_value/` |
| Regime detector | A classifier no runner or strategy calls; the regime work lives on its own branch ([0003](../decisions/0003-regime-on-its-own-branch.md)) | `src/strategy/regime_detector.cpp` |
| Sizing capital | `sizing_mode: half_compounding`: sized on starting capital less the drawdown of settled net P&L from its peak, never above starting capital; same figure in backtest and live | `include/trade_ngin/portfolio/sizing_capital.hpp` |
| The futures rebalance | One pass from the held book to a whole-contract book (buffering, rounding, the overlay's trim). Every non-futures book takes `DynamicOptimizer`; a trend sleeve without an overlay is refused | `src/optimization/one_pass.cpp`, `src/portfolio/portfolio_manager.cpp`; `docs/OPTIMIZER_AND_RISK_DESIGN.md` |
| Risk modules | `risk.json` schema 2 names the modules a book runs. `carver` wraps the Carver `RiskManager` gate and the overlay (R, R_jump, R_shock to the risk target, gross and net leverage, per-name cap, daily trim); `none` needs `_reason`, `_ruled_by`, `_ruled_on` (EQUITY_MR is `none`, HD 2026-09-18); fixed cut, warn and refuse modules exist | `src/risk/` (`risk_module_config.cpp`, `carver_risk_module.cpp`, `overlay.cpp`, `basic_risk_modules.cpp`); `docs/RISK_MODULES.md` |
| Costs | Fee plus spread plus square-root impact, netted across sleeves; rules too expensive for a contract are removed (`5a8e5b48`) | `src/transaction_cost/`; `docs/COST_MODEL.md` |
| Rolls | Vendor `.v.0` series, or with `per_contract_rolls` the held contract's own bars and a CME roll rule (`e170ec40`, `e2dc5769`) | `include/trade_ngin/data/contract_rolls.hpp`, `per_contract_source.hpp`; `docs/FUTURES_ROLLS.md` |

## Data in and out

| Reads | Notes |
|---|---|
| `futures_data.ohlcv_1d` | the vendor's `.v.0` daily bars, a raw splice with no back-adjustment and no unique key (`docs/DATA_SOURCES_OF_TRUTH.md` 4.2, 4.3) |
| `futures_data.ohlcv_1d_raw` | the same prints with `instrument_id`: the only place the contract behind a bar is known |
| `futures_data.contract_ohlcv_1d` | per-contract bars; read only with `per_contract_rolls` true |
| `metadata.contract_metadata` | size, cost and margin of every contract; the first 21 columns are read **by position** (`src/data/postgres_database.cpp`, #162) |
| `equities_data.ohlcv_1d`, `.corporate_action`, `.ticker_aliases` | the equity book; never the `adj_*` columns or `sharadar_ohlcv_1d` (`DATA_SOURCES_OF_TRUTH.md` section 2) |
| `trading.strategy_config` | the desk's active settings, merged over the files on live runs only |

It writes `trading.positions`, `executions`, `live_results`, `equity_curve`, `signals`,
`live_run_metadata` and `corp_action_applied` on live runs, and `backtest.results`, `run_metadata`,
`equity_curve`, `final_positions`, `executions` and `signals` on backtests (`src/storage/`).
`docs/LIVE_RUN_CYCLE.md` says which row a run reads, writes, and when it is final.

**Migrations** are `migrations/NNN_name.sql`, each with `NNN_name_rollback.sql` and mostly a
`test_NNN_*.sh` against a throwaway database. There is no migrations table: applied state is a
hand-kept table in `migrations/README.md`. Numbers 007-011 and 030-033 are retired and never
reused; 034-039 were 028-033 on another line (#232, `241cceda`). Highest today: 041. Read the
README's deploy notes: some migrations must ship with the binary of their commit (001, 021, 027).

**Config per book** (`docs/CONFIG_GUIDE.md`): `config/defaults.json` (database, shared settings),
then `portfolios/<dir>/portfolio.json` (strategies, capital, sizing, `use_optimization`, rolls),
`risk.json` (schema 2 modules) and `email.json` (recipients; values live only on the box).
`ConfigLoader::load` (`src/core/config_loader.cpp`, `include/trade_ngin/core/config_loader.hpp`)
merges them, refuses schema-1 and removed keys, and on a live run deep-merges the desk override.
`scripts/migrate_risk_json.py` moves a schema-1 `risk.json` to schema 2.

## Build and test locally

- Dependencies: vcpkg manifest at the repository root (`vcpkg.json`, `VCPKG_ROOT` set). Presets in
  the root `CMakePresets.json`, included by `services/trade-ngin/CMakePresets.json`. From
  `services/trade-ngin/`: `cmake --preset ci-gcc13`, `cmake --build --preset ci-gcc13`,
  `ctest --preset ci-gcc13` (as `.github/workflows/_quality-cpp.yml`). `dev` is a Debug preset;
  clang, MSVC, ASan and UBSan presets exist. `requirements/install_*.sh` install the system packages
  on Ubuntu or macOS.
- `Dockerfile`: Ubuntu 24.04, builds the runners and the Python service into the production image;
  `build_docker.sh`, `docs/README.Docker.md`.
- Tests: GoogleTest, one binary `trade_ngin_tests` from `tests/<module>/` (mirrors `src/`), plus
  Python suites registered with `add_test` (`tests/CMakeLists.txt`). Run them serially ([gotchas.md](../gotchas.md)).
  The desk service's tests are in `services/rpc/tests/` (`svc-trade-ngin.rpc.yml`).
- The **live mode gate** (`scripts/live_mode_gate/gate.sh`, HD 2026-10-10) runs the same dates as
  past dates and as production runs them, using a clock shim (`hostclock_shim.c`) on an unpatched
  binary, against a scratch database, and fails unless every stored table matches. It refuses a
  database whose name lacks "scratch" and never sends e-mail.
- Python module: `python/bindings/` (pybind11), CMake option `BUILD_PYTHON_WRAPPER`; release steps
  in [0005](../decisions/0005-tradengin-releases.md). `tests/backtest/test_wrapper_parity.cpp` checks it equals the engine bit for bit.

## Design documents worth knowing (`docs/`)

| File | One line |
|---|---|
| `SYSTEM_ARCHITECTURE.md` | the parts of the system and how they connect, checked against the code |
| `LIVE_RUN_CYCLE.md` | one live run: which day it is, what it reads and writes, what a refused run leaves |
| `TREND_FOLLOWING_SYSTEM.md` | the trend strategy end to end, every setting, and where it departs from Carver |
| `OPTIMIZER_AND_RISK_DESIGN.md` | one futures rebalance from forecast to stored book |
| `RISK_MODULES.md` | the risk module interface, schema 2, the Carver overlay, what each day stores |
| `FUTURES_ROLLS.md` | how a roll is detected, held, booked and stored |
| `DATA_SOURCES_OF_TRUTH.md` | which table and column to read, and which never to read |
| `COST_MODEL.md` | what a fill costs and how sleeves are netted |
| `AVERAGE_PRICE_LIFECYCLE.md` | the three meanings of `Position::average_price` and who may write each |
| `BROKER_BASIS_RECONCILIATION.md`, `CORP_ACTIONS_DATA_BOUNDARY.md` | equity cost basis against a broker statement; corporate-action sources |
| `CONFIG_GUIDE.md`, `performance_upkeep.md` | every config key; build, schedule, manual and catch-up run rules |
| `design/qt-contract.md`, `qt-master-rulings.md` | the binding engine and AlgoLens contract (C1-C11) and the rulings behind it |
| `design/desk-service.md`, `design/rpc.md` | the desk's gRPC commands; the shared gRPC layer and how to add a service |

## Rules a contributor must follow

- **Say whether the PR moves a stored number.** Write "moves no stored number" or list what moved;
  a mover names who re-seeds the stored backtests and live history (#179). Recent large changes
  shipped behind a switch that is off when absent and moves nothing (carry, `per_contract_rolls`, 040).
- **Mode-branch guard:** any new clock or environment read in `src/` or `include/` must be listed
  in `tests/live/test_mode_branch_guard.cpp` ([gotchas.md](../gotchas.md)). A dated run must behave the same in
  production and in a test (`33804800`).
- **Schema 2 has no defaults.** Every gating value is written in the book's own `risk.json`;
  `use_optimization` is required in `portfolio.json`; retired keys are load errors; the window is
  `"lookback_unit": "dates"` (`"bars"` is refused) (`config_template/README.md`).
- **`per_contract_rolls`** is off when absent and on in every futures template, but the box's
  configs lack it on purpose until the futures release (#180; [0006](../decisions/0006-risk-config-schema-2.md)).
- **The desk override is narrow:** it may only change a key the files hold, keep its JSON type,
  carry no null and no credential-like key; backtests never read it (`config_loader.hpp`).
- **The futures runners are twins.** `live_portfolio.cpp` and `live_portfolio_conservative.cpp`
  share the cycle by copy; the gate checks BASE stayed a twin. Merging them is #152.
- **The futures runner runs every calendar day** (HD 2026-09-17, `live_portfolio.cron`): a weekday
  schedule drops a session and breaks the T-1 chain.
- **A refusal stores nothing.** A held symbol with no bar past `live.data_staleness_tolerance_days`
  refuses the run; equity runners refuse `use_optimization: true` (`gate.sh`, `equity_mr/portfolio.json`).

## Open work (engine)

| Area | Issues |
|---|---|
| Futures release and rolls | #180 release (per-contract rolls on, MGC and SIL, retuned settings), #183 roll follow-ups, #36 history holes |
| Futures strategy | #186 carry, #184 universe and liquidity, #172 margin cut at 60%, #187 new strategies, #171 junk-bar classifier, #170 stale held symbol |
| Calendars | #168 CME calendar, #169 new exchanges, #146 calendar from the database |
| Production and monitoring | #185 move to new_algo_data, #176 data wait, #160 and #158 a failed write that exits 0, #145 run events |
| QT and book identity | #157 book key when sleeves change, #59 read by portfolio id, #178 desk follow-ups |
| Equities | #138 leftovers, #139 margin, #140 Reg T and shorting, #141 multi-strategy, #142 hybrid book, #163, #147 |
| Spreads and relative value | #126 layer plan, #127 QT side |
| Regime and models | #128, #148 to #151, #166 MS-DFM, #129 to #132 ML models |
| Refactor, tests, hygiene | #152 and #155 runner refactor, #153 backtest window, #177 hardening, #182 tests and CI, #181 wrapper parity, #161 and #162 config and metadata, #190 statistics |
