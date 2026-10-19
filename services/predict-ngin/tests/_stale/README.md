# Stale tests (kept, not collected)

Tests whose subject no longer exists in the codebase. They are kept for
reference instead of being deleted, and are excluded from collection via
`norecursedirs` in `pytest.ini`.

| File | Why it is stale |
|---|---|
| `test_experiment_tracker.py` | Tests `src.experiments.tracker`, which was removed in the "complete refactor" (commit aff5b21). Moved here from `tests/unit/` on 2026-10-15. |

To revive one: restore or rewrite its subject module, move the test back under
`tests/`, and make it pass.
