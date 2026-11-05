# How trade-ngin works, and why

This folder and the documents it points to describe trade-ngin as one set: how the futures and equity books work, why
each is built the way it is, what was tried and why it does not fit, the failure modes to watch for, and what is open.
The repository's own documents explain the mechanics; the files in this folder give the reasons and point to those
documents rather than repeat them. Where a document and the code disagree, the code is what runs; please fix the
document.

The code is `services/trade-ngin/` in AlgoGators/algogators, imported from AlgoGators/trade-ngin main on 2026-11-01.
Issues live in AlgoGators/algogators; older pull requests stay on AlgoGators/trade-ngin. Dates are UTC.

## The book in brief

The futures book follows Carver's trend-following method (*Advanced Futures Trading Strategies*) on 36 futures at a
capital of 500,000, with whole contracts chosen each day by the dynamic optimisation and a risk overlay with limits of
1.75 / 4.75 / 3.00 / 7.25 / 5.50. It prices each position on the contract it actually holds and rolls contract by
contract; gold, silver and the four equity indices trade as their micros; carry is built and switched off. The template that holds it is
`config_template/` (CONSERVATIVE_PORTFOLIO). [THE_BOOK](01_the_book/THE_BOOK.md) gives each setting and its reason;
[CARVER_DEPARTURES](02_decisions/CARVER_DEPARTURES.md) lists every place it differs from Carver and why.

The equity book is a separate, cash-only strategy; [THE_EQUITY_BOOK](01_the_book/THE_EQUITY_BOOK.md) describes it.

## Where to start

| If you want to | Read |
|---|---|
| Understand the futures book | [THE_BOOK](01_the_book/THE_BOOK.md), then [TREND_FOLLOWING_SYSTEM](../TREND_FOLLOWING_SYSTEM.md) and [OPTIMIZER_AND_RISK_DESIGN](../OPTIMIZER_AND_RISK_DESIGN.md) |
| Understand the equity book | [THE_EQUITY_BOOK](01_the_book/THE_EQUITY_BOOK.md), then [EQUITY_STRATEGY_GUIDE](../EQUITY_STRATEGY_GUIDE.md) |
| Know why a setting is what it is | [CARVER_DEPARTURES](02_decisions/CARVER_DEPARTURES.md), [DECISIONS_BY_AREA](02_decisions/DECISIONS_BY_AREA.md), [LEVERS](03_tuning_and_results/LEVERS.md) |
| Run something | [RUNBOOKS](05_operations/RUNBOOKS.md), [LIVE_RUN_CYCLE](../LIVE_RUN_CYCLE.md), [the release scripts](../../scripts/futures_release/README.md) |
| Change the code safely | [HOW_WE_VERIFY](04_lessons/HOW_WE_VERIFY.md), [WARNINGS](04_lessons/WARNINGS.md) |
| Pick up new work | [FUTURE_WORK_ADVICE](06_open/FUTURE_WORK_ADVICE.md) |

## The repository's documents (how it works)

| Document | What it explains |
|---|---|
| [SYSTEM_ARCHITECTURE](../SYSTEM_ARCHITECTURE.md) | The parts of the system, the database and how they connect |
| [TREND_FOLLOWING_SYSTEM](../TREND_FOLLOWING_SYSTEM.md) | The futures strategy: forecasts, sizing, the speed limit, carry |
| [OPTIMIZER_AND_RISK_DESIGN](../OPTIMIZER_AND_RISK_DESIGN.md) | The daily optimisation pass and the risk overlay, with worked days |
| [RISK_MODULES](../RISK_MODULES.md) | The risk modules and the overlay's readings and limits |
| [COST_MODEL](../COST_MODEL.md) | Fees, spread and impact per fill |
| [FUTURES_ROLLS](../FUTURES_ROLLS.md) | Per-contract rolls: the roll rule, the roll bar and its split |
| [FRONT_MONTH_EXAMPLES](../FRONT_MONTH_EXAMPLES.md) | Worked roll and deadline examples |
| [LIVE_RUN_CYCLE](../LIVE_RUN_CYCLE.md) | One dated live run, from the previous day's book to the e-mail |
| [DATA_SOURCES_OF_TRUTH](../DATA_SOURCES_OF_TRUTH.md) | Which table is authoritative for what |
| [CONFIG_GUIDE](../CONFIG_GUIDE.md) | Every config key, by file and block |
| [EQUITY_STRATEGY_GUIDE](../EQUITY_STRATEGY_GUIDE.md), [AVERAGE_PRICE_LIFECYCLE](../AVERAGE_PRICE_LIFECYCLE.md), [BROKER_BASIS_RECONCILIATION](../BROKER_BASIS_RECONCILIATION.md), [CORP_ACTIONS_DATA_BOUNDARY](../CORP_ACTIONS_DATA_BOUNDARY.md) | The equity strategy, cost basis, broker reconciliation and corporate actions |
| [performance_upkeep](../performance_upkeep.md) | Statistics, storage and migrations upkeep |
| [the `docs/design/` set](../design/) | The QT desk, its contract and rulings, and the rpc service |
| The `src/*/README.md` files, [migrations](../../migrations/README.md), [config_template](../../config_template/README.md), [the live mode gate](../../scripts/live_mode_gate/README.md) | Each module and tool, next to its code |

## This folder (why it is built this way)

| File | What it holds |
|---|---|
| [01_the_book/THE_BOOK](01_the_book/THE_BOOK.md) | How the futures book is set, setting by setting, with each reason; why these 36 markets and the gold and silver micros |
| [01_the_book/ROLLS_AND_DATA](01_the_book/ROLLS_AND_DATA.md) | Why per-contract rolls, how the rule handles each case, the data holes and their fill, the micros |
| [01_the_book/THE_EQUITY_BOOK](01_the_book/THE_EQUITY_BOOK.md) | How the equity book runs as a system: the account, prices and corporate actions, dates, the daily cycle, what is planned |
| [02_decisions/CARVER_DEPARTURES](02_decisions/CARVER_DEPARTURES.md) | Each difference from Carver's method, grouped, with its economics and whether it keeps his intent |
| [02_decisions/DECISIONS_BY_AREA](02_decisions/DECISIONS_BY_AREA.md) | The design decisions in force that shape the book, by area, each with its reason and data |
| [03_tuning_and_results/TUNING_STORY](03_tuning_and_results/TUNING_STORY.md) | What each tuning round established about how the book behaves |
| [03_tuning_and_results/LEVERS](03_tuning_and_results/LEVERS.md) | Each lever: what it does, why it is or is not in the book, and the data |
| [03_tuning_and_results/RESULTS](03_tuning_and_results/RESULTS.md) | The book's results by window, capital against the account, the comparison with SG Trend, and the limits of the evidence |
| [03_tuning_and_results/CARRY](03_tuning_and_results/CARRY.md) | Carry: the design, what is known about why it earned little, and what is not yet measured |
| [04_lessons/WARNINGS](04_lessons/WARNINGS.md) | Failure modes in the data, sizing, rolls, statistics, runs, configuration, equities, repository and production, each with the rule that prevents it |
| [04_lessons/HOW_WE_VERIFY](04_lessons/HOW_WE_VERIFY.md) | How a change is proved: change classes, identity and A/B checks, worked days, tests |
| [05_operations/RUNBOOKS](05_operations/RUNBOOKS.md) | The release seed, dated runs, restarts, migrations and the gate, each step with its reason; the team's standing rules |
| [06_open/FUTURE_WORK_ADVICE](06_open/FUTURE_WORK_ADVICE.md) | What to do next and what not to repeat, with the open design questions and their issues |
