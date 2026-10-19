# Early public information research implementation plan

Last revised: 2026-10-15

**Goal:** Turn the supplied mortgage and Billboard hypotheses into reproducible, causal research tools without enabling live trading.

**Architecture:** Separate namespace `earlyinfo` in research scripts, tests and canonical `data/research/earlyinfo`. Store raw timestamped source responses; take explicit release calendars and availability timestamps as inputs. Generate research fair values and diagnostics, never assumed fills or annualized profits.

**Tech stack:** Existing Python, pandas, numpy, scipy, requests, pytest. No dependency changes.

**Spec:** User's October 2026 early-information brief in this session.

## Constraints and rulings

- Other agents own LIP, live collectors and shared results DB. Work in place on the existing feature branch with uniquely named files; do not stage, commit, deploy, change shared DBs or edit other agents' files.
- No data provider is connected. Public data first; distinguish current-vintage history from contemporaneous evidence.
- Truth Social already tested in E1CNT; preserve its existing verdict and retest conditions.
- Explicit timezone-aware window/release metadata is mandatory. Do not guess holiday windows from the ticker.
- Mortgage feature is change in weekly MND means, rather than mean daily changes, because it targets a change in weekly aggregates.
- Published PMMS rounds to 0.01 percentage point; strict above thresholds treat equality as NO.
- Current fee metadata cannot establish historical fees. Repricing diagnostics do not establish maker fillability or PnL.

## Task 1: Causal model primitives

- [x] Write and run failing tests for availability filtering, incomplete windows, strict rounded thresholds, annual maximum ties, grouped Billboard probabilities and future-label exclusion.
- [x] Implement `scripts/research/earlyinfo_models.py`: explicit PIT selection; mortgage weekly features, expanding regression and empirical forecast errors; residual-bootstrap annual-high simulation; conditional choice Billboard model with past-only features/labels.
- [x] Verify tests against deterministic examples and future-data mutation canaries.

## Task 2: Read-only evidence collection and repricing

- [x] Test pagination failures, missing candle quotes and predecessor selection at bar boundaries.
- [x] Implement `scripts/research/earlyinfo_collect.py`: public series census, live/historical market metadata, source archives and optional one-market candlesticks, with timestamped append-only runs and bounded requests. Snapshot order books for actual current spreads/depth. No credentials or order endpoints.
- [x] Implement `scripts/research/earlyinfo_run.py`: explicit input CSVs, mortgage forecasts or Billboard grouped model, repricing diagnostics. Fail on missing evidence, no automatic historical availability inference.
- [x] Run a public evidence collection and CLI smoke checks.

## Task 3: Evidence and handoff

- [x] Independent audit with backtest-audit checklist; no performance claim without an actual executable-fill test and trial logging.
- [x] Run focused tests plus repository suite; report pre-existing failures and exact limits.
- [x] Write `docs/EARLYINFO_RESEARCH.md` with findings, input schemas, run commands, preregistered promotion gates and exact provider gaps. Add a small guide entry and daily memory note.
- [x] Refresh ignored graph with two workers after source edits. Report final files and data gaps.

## Ruling: current-vintage diagnostic (before computing results)

The public MND chart embed contains 11,960 observations and publisher timestamps. Add
`earlyinfo_explore.py` to evaluate the single frozen expanding-OLS mortgage predictor
against previous-PMMS, with 52 training releases and 26 causal calibration errors.
Use post-2022-11-17 history, only Thursday three-release spans, explicit missing-day
checks against a federal-business-day calendar. Report RMSE/MAE by year, not profits.
No parameter selection, grid search or trade simulation. Publisher timestamps and
current values are unverified historical vintages; this diagnostic cannot qualify a
live strategy. Log the experiment before and after running it.

## Execution evidence

- All research implementation tasks completed in the unique earlyinfo namespace.23 focused tests pass; compilation and git diff checks pass.
- Full repository suite was run;26 existing collection errors are recorded in the report and ignored data log. This does not mean the full suite passes.
- Independent audit fixed and rechecked all major findings; review applies to research tooling, not profitability. Forward snapshot smoke:19/19 books.
- Diagnostic96 forecasts2.485bp RMSE; frozen quote study8 events0 fresh decision quotes. No PnL claimed.
- Graph refreshed with two workers:14064 nodes,23746 edges; generated outputs remain ignored.
- Added isolated earlyinfo ledger-entry YAML for later ingestion; shared results DB untouched. Five append-only experiment rows added to research/trials.csv.
- User has no source data; no provider purchase required yet. Exact provider capabilities and forward alternative are in the research report.
