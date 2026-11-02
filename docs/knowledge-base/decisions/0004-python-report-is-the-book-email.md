# 0004. The daily book e-mail is the Python report only

- **Status:** Accepted
- **Date:** 2026-10-30
- **Sources:** AlgoGators/trade-ngin#261, #86

## Context

Three senders had existed for the book e-mail: the engine's C++ e-mail, a hand-off to AlgoLens, and AlgoLens's own report worker.

## Decision

The engine's Python report (the AlgoLens design) in `services/trade-ngin/services/rpc/algogators_rpc/services/report/` is the only sender. The C++ e-mail is deleted, the hand-off to AlgoLens is reverted (trade-ngin #261, commit `167bfac4`), and AlgoLens's report worker is removed (#86). The recipient list lives in each book's `email.json` on the production box, never in this repository.

## Consequences

Supersedes AlgoGators/trade-ngin#264 ("keep the engine's e-mail"), closed. A change to the e-mail's content is a change to the Python report.
