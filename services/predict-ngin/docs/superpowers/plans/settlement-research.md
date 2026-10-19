# Settlement research implementation plan

Last revised: 2026-10-15

**Goal:** Implement the supplied settlement-interpretation research as isolated, paper-only tooling.
**Spec:** User's supplied five-idea research and backtest plan, 2026-10-15.
**Architecture:** A SQLite collector stores complete market observations, contract changes and subsequent quotes. A separate pure decision function evaluates reviewed evidence without inferring rules from settlement results.
**Tech stack:** Python standard library; httpx for public GET requests; pytest.

## Constraints and decisions

- Other agents own `mrule_*`, `market_feature_watch.py`, live deployments and the shared results database. Do not edit them or switch branches.
- New files use `settlement_rules_*` names. No orders, account credentials, deployment, scheduler changes or claimed returns.
- Retain complete raw JSON and actual observation times. Rule hashes include title, subtitle, custom strike, rule text and settlement-source metadata when present.
- A first observation is a baseline, not a clarification. A missing field is distinct from an explicit empty field.
- Record +0, 5, 30, 120 and 1440 minute observations at their actual observation times; flag delay rather than backdating. Missing/one-sided quotes have no midpoint.
- Mention entry uses reviewed evidence tied to market ticker and exact contract digest, an independently established event end, source availability and explicit clause. No automatic NO from missing transcript words.
- Default paper risk: 1% per strike and 3% per event (below research ceilings); fees consume budget. Ask must be 75–97c; no invented fill depth. Floor signals are alerts only.

## Review focus

Future evidence; rule changes after review; missing/invalid prices; stale or closed markets; duplicate/out-of-order observations and restart persistence.

## Tasks

- [x] Write failure tests for contract diffs, delayed followups, restart, missing prices, evidence timing, budget/fee limits and floor alerts.
- [x] Implement `scripts/research/settlement_rules_watch.py`: scoped public collector and transactional replay engine with SQLite.
- [x] Implement `scripts/research/settlement_rules_candidates.py`: pure paper decision function, JSON CLI, no execution adapter.
- [x] Run focused tests, public read-only smoke collection and a synthetic candidate CLI demonstration, clearly labelled.
- [x] Request independent review, address material findings, refresh Graphify and document usage, evidence limits and remaining inputs.

Verification: 27 focused tests independently confirmed; public enriched-source smoke captured 91 markets without errors; timestamp inventory and synthetic candidate CLI completed; Graphify refreshed after final source edits. Full pytest attempted and stopped at 25 collection errors outside these changes, documented in the research note. Reviewer found no remaining important issues within paper-only scope.

Ruling: Added a read-only timestamp inventory after the user asked to search the repo for data. This is coverage analysis, not a return trial. It found publisher timestamps before market close for 118 matched earnings events, but not verified historical content versions or event ends.
Ruling: Executed inline in the shared workspace using uniquely named new files because other agents are working in this branch. No branch switch, shared results write, staging, commit or deployment.

No strategy-return experiment is run by this plan; unit fixtures and collector smoke tests do not add strategy trials. Forward eligibility remains unproven pending point-in-time evidence and executable books. Scalar rounding is deferred to the feature watcher owner's work: it is a pricing correction, not an independently supported strategy.
