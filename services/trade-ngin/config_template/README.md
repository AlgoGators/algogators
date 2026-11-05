# Config Template

Copy this directory to `config/` and replace the placeholders with your actual values.

## Setup

```bash
cp -r config_template config
```

## Placeholders to Replace

### defaults.json
- `YOUR_DB_HOST` - PostgreSQL server host
- `YOUR_DB_USERNAME` - Database username
- `YOUR_DB_PASSWORD` - Database password
- `YOUR_DB_NAME` - Database name

### The `email.json` of `portfolios/base`, `portfolios/conservative` and `portfolios/equity_mr`
- `YOUR_SMTP_USERNAME` - SMTP auth username (e.g. Gmail address)
- `YOUR_SMTP_APP_PASSWORD` - SMTP app password (use app-specific password for Gmail)
- `YOUR_FROM_EMAIL` - Sender email address
- `YOUR_EMAIL_FOR_NOTIFICATIONS` - Recipient(s) of every report (the `to_emails` array, the only recipient list the runners read)

The three QT directories ship no `email.json`. The loader requires one for every book, so a
deployment adds its own to each.

## Structure

```
config/
├── defaults.json              # Database, execution, optimization (shared)
├── portfolios/
│   ├── base/                  # BASE_PORTFOLIO (bt_portfolio, live_portfolio)
│   │   ├── portfolio.json     # Strategies, capital, allocations
│   │   ├── risk.json          # Risk limits
│   │   └── email.json         # Email notifications
│   ├── conservative/          # CONSERVATIVE_PORTFOLIO (bt_portfolio_conservative, live_portfolio_conservative)
│   │   ├── portfolio.json
│   │   ├── risk.json
│   │   └── email.json
│   ├── equity_mr/             # EQUITY_MR_PORTFOLIO (bt_equity_mean_reversion, live_equity_mean_reversion)
│   │   ├── portfolio.json
│   │   ├── risk.json
│   │   └── email.json
│   ├── qt_conservative/       # QT_CONSERVATIVE_PORTFOLIO, the QT desk book (portfolio.json, risk.json)
│   ├── qt_conservative_model/ # QT_CONSERVATIVE_MODEL_PORTFOLIO, the model twin, retired (not scheduled)
│   └── qt_e2e/                # QT_E2E_PORTFOLIO, the test book of scripts/qt_e2e.sh
├── examples/
│   └── risk_modules/          # one worked risk.json per module type; no runner loads them
└── trades/                    # relative-value trade definitions; read by tests only
```

The live futures runners run a QT book with `--portfolio-config <dir>`. Each QT template's
`_description` calls it a copy of CONSERVATIVE, but all three carry BASE's overlay limits and
the sample covariance; `docs/CONFIG_GUIDE.md`, section "QT Portfolios", lists what each one holds.

`trades/` holds seven relative-value trade definitions (legs, inputs, series rule, signals,
package, trade block). The relative-value module parses and checks them, and its tests read this
folder; no runner reads it. `src/relative_value/README.md` describes the format.

## Risk schema 2

`risk.json` names the risk MODULES a book runs instead of carrying a flat set of gating values,
and there are no defaults left anywhere: every gating value is written literally in the book's
own `risk.json`, `use_optimization` lives at the top level of `portfolio.json`, and
`use_risk_management` and `risk_defaults` are gone. A book that runs no risk layer says so with
a `none` module carrying `_reason`, `_ruled_by` and `_ruled_on`.

**The loader REFUSES a schema-1 `risk.json`**, so a deployed config directory written before
schema 2 has to be migrated once, before its first run on a build with schema 2:

```
python3 scripts/migrate_risk_json.py config            # dry run -- READ THE DIFF
python3 scripts/migrate_risk_json.py config --in-place # write it
```

The `.schema1.bak` files the second command leaves behind are the rollback for going back
to an older build. The script refuses, rather than guessing, when a book's `risk.json` has
no `max_drawdown`/`max_leverage` (schema 1 silently fell back to 0.4 / 4.0) or when
`use_risk_management` resolves to false anywhere: that second case needs a `none` module
with its recorded decision, and the script will not invent one.

**Three hand steps after the script.**

1. *`lookback_unit`.* An older migrated `risk.json` says `"lookback_unit": "bars"`. The Carver
   window keeps `lookback_period` distinct DATES (252 distinct dates, of which the sparse-date
   filter keeps about 187-200 on the futures books) and the loader refuses `"bars"`.
   Re-running the script upgrades such a file to `"dates"` and nothing else, keeping a
   `.bars.bak` rollback copy.
2. *A book assigned `none` (EQUITY_MR in the templates).* The script writes a `carver` module for every
   book, because that is the only gate schema 1 had, and it will not invent the decision a
   `none` module records. When the tracked `config_template/portfolios/<book>/risk.json` assigns
   the book `none`, the script prints a `NOTE:` naming the book and the template's `_ruled_by`
   and `_ruled_on`. For EQUITY_MR the operator then replaces the deployed file's `modules` list
   by hand with the template's:

   ```
   "modules": [ { "id": "no_portfolio_risk", "type": "none",
                  "_reason": "<copied verbatim from the template>",
                  "_ruled_by": "<copied from the template>",
                  "_ruled_on": "<copied from the template>" } ]
   ```

   leaving `risk_reporting`, `max_drawdown` and `max_leverage` as they are (the reporter still
   measures the book). Until that is done the deployed book keeps gating with the Carver
   module while the repository says it runs none. Each run of a `none` book logs one
   `RISK_NONE ... ruled_by=... ruled_on=...` line, which is how to check the step took.
3. *A futures book.* The script does not write the overlay's keys: add `R_max`, `R_jump_max`,
   `R_shock_max`, `per_name_cap`, `trim_max` and `overlay_covariance` to the `carver` module and
   remove the retired keys ([docs/RISK_MODULES.md](../docs/RISK_MODULES.md), section 4.5; the
   script's header lists the portfolio.json and defaults.json keys as well). Until then the
   loader refuses the file.

`examples/risk_modules/` holds one worked example per module type. They carry placeholder
numbers and no runner loads them. Their `carver` modules are the older form, without the
overlay's three `R_` limits or `overlay_covariance`, so a futures runner refuses them: start a
futures book from `config_template/portfolios/conservative/risk.json`.

## `reserve_capital_pct` deleted

`reserve_capital_pct` is gone from every `portfolio.json` template. Nothing sized on it: the
last reader was removed in February 2026, and the figure was only echoed into one log line and
into the run-metadata JSON. A deployed `portfolio.json` that still carries the key keeps
loading; the key is ignored and each run logs one WARN naming it. Delete the line to silence
it.

Cut-over in stored rows: `trading.live_run_metadata.portfolio_config` and
`backtest.run_metadata.portfolio_config` / `.hyperparameters` written before the deletion carry
`reserve_capital`; rows written after it do not. History is left as it was written.

## `carry`: carry rules in the trend strategy (AlgoGators/trade-ngin#185), off by default

The templates ship without the block: carry is off and the strategy is the trend strategy to the
last stored digit (every stored cell, the run-metadata settings snapshot included, as before the
block existed). To switch carry on, add to `portfolio.json` at the top level:

```json
"carry": {"weight": 0.4, "spans": [5, 20, 60, 120], "scalar": 30}
```

`weight` is the carry style's share of the first sleeve's forecast weights (Carver, Advanced
Futures Trading Strategies, strategy eleven, table 51: 0.4, trend 0.6), from 0 to 1, 0 being off;
`spans` the EWMA smoothing spans in business days, fastest first (strategy ten); `scalar` the
forecast scalar. `spans` and `scalar` may be left out (Carver's values). On, an instrument's
forecast is `FDM x ((1 - weight) x the average of its trend speeds + weight x the average of its
carry spans)`, capped at 20, the FDM table 52's for its number of rules (7 to 10); raw carry is
read from the held contract's and its roll-cycle neighbour's closes of the same date
(`data/carry_quote.hpp`), so carry needs `per_contract_rolls` true and a book of one
`TrendFollowingStrategy` sleeve, and is refused otherwise. The sleeve also refuses carry when a
span would trade faster than the trend rules a contract keeps: every span must be one of table 40's
(5, 20, 60, 120), and the fastest span's turnover must not be above that of the fastest EMA pair
each contract keeps after its `trading_rule_removals` (table 35), the pair shown to pass the 0.15
speed limit on it (`TrendFollowingStrategy::validate_config`, `src/strategy/trend_following.cpp`).
An instrument with no carry quote yet in its window trades its trend forecast alone; a date with no
second-contract price keeps the last known raw carry. Each signal bar logs one `CARRY` line with the
trend part and the carry part. The block cannot be overridden from the database.
