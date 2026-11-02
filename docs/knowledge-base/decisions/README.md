# Decisions

One file per decision, numbered in order. Read the ones for your area before proposing a change
to something that looks odd: it is often a decision.

| # | Decision | Status | Date |
|---|---|---|---|
| [0001](0001-merge-and-history-rules.md) | Merge and history rules | Accepted | 2026-10-29 |
| [0002](0002-qt-day-and-missed-sends.md) | The QT day, and why a missed day is not sent late | Accepted | 2026-10-09 |
| [0003](0003-regime-on-its-own-branch.md) | Regime detection stays on its own branch | Accepted | 2026-11-02 |
| [0004](0004-python-report-is-the-book-email.md) | The daily book e-mail is the Python report only | Accepted | 2026-10-30 |
| [0005](0005-tradengin-releases.md) | Releasing the tradengin Python package | Accepted | 2026-11-02 |
| [0006](0006-risk-config-schema-2.md) | Risk config schema 2 names its overlay covariance | Accepted | 2026-11-01 |
| [0007](0007-prod-deploys-mains-image.md) | Prod deploys main's tested image; the C++ build is cached | Accepted | 2026-11-02 |
| [0008](0008-trade-ngin-in-the-mono-repo.md) | trade-ngin lives in the mono repo; the old repository is archived | Accepted | 2026-11-02 |
| [0009](0009-tracker-on-pages.md) | The issue tracker page is published from its own repository | Accepted | 2026-11-02 |
| [0010](0010-branch-cleanup.md) | Delete merged and closed PR branches; keep the rest | Accepted | 2026-11-02 |
| [0011](0011-workspace-libs-ship-inside-consumers.md) | Workspace libraries ship inside their consumers, never on PyPI | Accepted | 2026-08-15 |
| [0012](0012-algosystem-keeps-its-own-db-config.md) | algosystem keeps its own database config | Accepted | 2026-08-15 |
| [0013](0013-pin-libpqxx-7.md) | Pin libpqxx to the 7.x API | Accepted | 2026-08-15 |
| [0014](0014-one-uv-workspace.md) | One uv workspace, one lockfile, members listed by hand | Accepted | 2026-08-17 |
| [0015](0015-path-filtered-ci-without-skip-twins.md) | Path-filtered CI with no skip twins, guarded by a checker | Accepted | 2026-08-18 |
| [0016](0016-no-required-status-checks-on-main.md) | The main ruleset gates reviews and CodeQL, not status checks | Accepted | 2026-08-19 |
| [0017](0017-platform-db-unpublished-with-access-gate.md) | platform-db is an unpublished platform member with an access-gate seam | Accepted | 2026-08-21 |
| [0018](0018-people-schema-no-scores.md) | The people schema stores no scores; GitHub is the source of members | Accepted | 2026-08-22 |
| [0019](0019-codeql-buildless-and-diff-scoped.md) | CodeQL is buildless and diff-scoped, with a weekly full scan | Accepted | 2026-08-24 |
| [0020](0020-website-numbers-have-one-source.md) | The website's numbers and dates have one source each | Accepted | 2026-08-30 |
| [0021](0021-futures-runner-every-calendar-day.md) | The futures runner runs every calendar day | Accepted | 2026-09-17 |
| [0022](0022-equity-mr-has-no-risk-module.md) | The equity book runs no portfolio risk module, by ruling | Accepted | 2026-09-18 |
| [0023](0023-risk-schema-2-no-defaults.md) | Risk config schema 2: no defaults, schema 1 refused | Accepted | 2026-10-08 |
| [0024](0024-half-compounding-sizing-capital.md) | Futures books are sized on half-compounding capital | Accepted | 2026-10-08 |
| [0025](0025-prod-branch-deploys-tags-release.md) | Production deploys from a shared prod branch; tags are releases | Accepted | 2026-10-08 |
| [0026](0026-data-ngin-deploys-a-ci-built-image.md) | data-ngin deploys a CI-built image by digest | Accepted | 2026-10-08 |
| [0027](0027-failed-task-fails-the-dag.md) | A failed pipeline task fails its DAG run, and failures file issues | Accepted | 2026-10-08 |
| [0028](0028-airflow-outside-the-workspace.md) | Airflow is not a workspace dependency | Accepted | 2026-10-08 |
| [0029](0029-lock-changes-run-affected-members-only.md) | A lockfile change runs only the members whose dependencies changed | Accepted | 2026-10-08 |
| [0030](0030-algolens-calls-the-engine-over-grpc.md) | AlgoLens calls the engine over gRPC; Postgres stays the record | Accepted | 2026-10-09 |
| [0031](0031-protos-vendored-with-version-header.md) | The engine's protos are vendored with a version header | Accepted | 2026-10-09 |
| [0032](0032-algolens-yields-memory-to-the-engine.md) | AlgoLens yields memory to the engine on the shared box | Accepted | 2026-10-09 |
| [0033](0033-desk-override-narrow-and-live-only.md) | The desk's config override is narrow and live-only | Accepted | 2026-10-09 |
| [0034](0034-live-mode-gate-uses-a-clock-shim.md) | The live mode gate tests production with a clock shim | Accepted | 2026-10-10 |
| [0035](0035-per-user-screens-not-roles.md) | AlgoLens grants screens per user instead of new roles | Accepted | 2026-10-11 |
| [0036](0036-futures-pull-at-0430-for-the-previous-day.md) | Futures bars are pulled at 04:30 New York time for the previous UTC day | Accepted | 2026-10-12 |
| [0037](0037-re-pull-the-last-five-days.md) | Each pull re-fetches the last five days and upserts | Accepted | 2026-10-12 |
| [0038](0038-data-ready-owned-by-the-consumer.md) | ops.data_ready belongs to the engine; data-ngin only writes rows | Accepted | 2026-10-12 |
| [0039](0039-published-at-is-the-approval.md) | Only published_at says a desk day is approved | Accepted | 2026-10-13 |
| [0040](0040-fund-totals-count-ungrouped-books.md) | Fund totals count only ungrouped books | Accepted | 2026-10-14 |
| [0041](0041-predict-ngin-publishes-only-rejected-strategies.md) | predict-ngin publishes only rejected strategies | Accepted | 2026-10-17 |
| [0042](0042-strategy-registry-has-no-fallback.md) | The strategy registry has no built-in fallback | Accepted | 2026-10-19 |
| [0043](0043-migrations-without-an-applied-table.md) | Migrations keep no applied-state table; numbers are never reused | Accepted | 2026-10-21 |
| [0044](0044-expirations-from-vendor-definitions.md) | Contract expirations come from vendor definitions, never a calendar guess | Accepted | 2026-10-25 |
| [0045](0045-carry-shipped-switched-off.md) | Carry shipped switched off | Accepted | 2026-10-30 |
| [0046](0046-code-graph-committed-and-refreshed-weekly.md) | The code graph is committed and refreshed weekly | Accepted | 2026-11-02 |

## Adding one

Copy [template.md](template.md) to the next number, fill it in, and add a row above in the same
PR as the change it records. A decision is never rewritten: a new one supersedes it, the old one's
status becomes `Superseded by NNNN`, and both link to each other.
