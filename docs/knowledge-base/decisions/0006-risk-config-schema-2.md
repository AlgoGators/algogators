# 0006. Risk config schema 2 names its overlay covariance

- **Status:** Accepted
- **Date:** 2026-11-01
- **Sources:** production engine logs, 2026-11-01; `services/trade-ngin/config_template/`; #180

## Context

From trade-ngin #257 the engine refuses a `carver` risk module whose `risk.json` does not name `overlay_covariance` (schema 2 has no defaults). The first deploy of that engine failed every model run and send on the production box.

## Decision

The QT books' `risk.json` on the box name `"sample"`, the template's value and the behaviour before the change. The box's `portfolio.json` files still lack some newer template keys (`per_contract_rolls` and others) on purpose: switching those on changes trading and is tracked in #180.

## Consequences

A PR that changes a config schema must say which box configs change with it, and the rollout updates them before or with the image.
