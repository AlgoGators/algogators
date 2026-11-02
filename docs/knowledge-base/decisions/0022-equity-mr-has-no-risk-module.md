# 0022. The equity book runs no portfolio risk module, by ruling

- **Status:** Accepted
- **Date:** 2026-09-18
- **Sources:** `config_template/portfolios/equity_mr/`; `config_template/README.md`

## Context

Risk schema 2 forces an explicit choice for a book without a risk gate instead of a silent default.

## Decision

`equity_mr`'s `risk.json` assigns a `none` module with `_reason`, `_ruled_by` and `_ruled_on`, and the equity runners refuse `use_optimization: true`.

## Consequences

Any book without a risk layer must carry the same three fields.
