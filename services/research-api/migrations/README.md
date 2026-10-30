# AlgoLens migrations

Apply in the order below, after the trade-ngin migrations they depend on (trade-ngin
`migrations/README.md`). There is no migrations table (master document ruling 26): applied state
is tracked by hand in this file. When you apply a migration to a database, fill in the date in
its column in the same PR or straight after. A blank cell means "not recorded", not "not applied".

| # | File | What it does | Rollback | new_algo_data |
|---|---|---|---|---|
| 001 | `001_create_strategy_registry.sql` | `trading.strategy_registry`: the portfolios AlgoLens shows, by API id, with display metadata and starting equity; seeds the trend book | none (drop the table) | |
| 002 | `002_strategy_lifecycle.sql` | Lifecycle (`live`, `incubating`, `retired`) on the registry and `trading.strategy_lifecycle_log` | none | |
| 003 | `003_screen_access.sql` | `auth.user_screen_access` and `auth.screen_access_log` (member, investor and qt screens, admin-assigned) and `investor.reports` | `003_screen_access_rollback.sql` (destructive: back up first) | |
| 004 | `004_desk_settings_locks.sql` | `trading.desk_settings_locks` (the settings the QT desk sees greyed out, D-14; seeded with Dom's 2026-10-14 list) and its append-only log | `004_desk_settings_locks_rollback.sql` (drops the log) | |
| 005 | `005_investor_portfolio_links.sql` | `investor.portfolio_links`: the portfolio and book (QT or model) each investor follows (D-25, decision 13), with an append-only log | `005_investor_portfolio_links_rollback.sql` (every investor back to the QT book) | |
| 006 | `006_report_queue.sql` | `reports.report_queue`: the queue of the retired AlgoLens report worker (the book e-mail is the engine's); nothing reads it, the rollback drops it | `006_report_queue_rollback.sql` (destructive: unsent reports are lost) | |
| 007 | `007_book_sent_grant.sql` | Lets `svc_algolens` set `trading.live_run_metadata.sent_at` (the retired report worker). Take it back on every database it was applied to | `007_book_sent_grant_rollback.sql` (revokes the grant; the engine alone writes `sent_at`) | |
| 008 | `008_one_registry_entry_per_book.sql` | One registry entry per book (issue #59): retires BASE_PORTFOLIO's two entries `inc_tf_base` and `inc_tf_fast` (BASE's history mixes leftover rows of other strategy ids, and it has not run since 2026-02; kept as history and logged), a unique index on `portfolio_id` for entries not retired, `strategy_type` documented as display only. Refuses to run while another book has two entries | `008_one_registry_entry_per_book_rollback.sql` (drops the index, returns both BASE entries to their logged state) | |

The QT tables AlgoLens reads and writes (`trading.position_overrides`, `trading.strategy_config`,
the `portfolio_group` and `desk_editable` registry columns) are trade-ngin's migrations 021 to 029.
