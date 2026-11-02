# 0045. Carry shipped switched off

- **Status:** Accepted
- **Date:** 2026-10-30
- **Sources:** commit `c7cec212`; `config_template/README.md`; #180, #186

## Context

Nothing should move until carry is re-measured and tuned.

## Decision

Carver's carry is in the trend strategy, but no template has the `carry` block; it requires `per_contract_rolls` and a single trend sleeve, and the database cannot override it.

## Consequences

With the block absent the strategy is the trend strategy to the last stored digit. Turning carry on is #186's work and moves stored numbers.
