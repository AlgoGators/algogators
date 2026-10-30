-- Rollback of 008_one_registry_entry_per_book.sql: drops the one-entry-per-book index and
-- returns inc_tf_base to the state 008 retired it from (its incubation start and mock capital
-- were kept). Only a retirement 008 itself logged is undone.

BEGIN;

DROP INDEX IF EXISTS trading.strategy_registry_one_entry_per_book;

WITH retired AS (
    SELECT DISTINCT ON (strategy_id) strategy_id, before_state
    FROM trading.strategy_lifecycle_log
    WHERE strategy_id = 'inc_tf_base' AND user_id = 'migration 008'
    ORDER BY strategy_id, created_at DESC
)
INSERT INTO trading.strategy_lifecycle_log
    (strategy_id, before_state, after_state, reason, user_id)
SELECT r.strategy_id, 'retired', r.before_state,
       'rollback of migration 008', 'migration 008 rollback'
FROM retired r
JOIN trading.strategy_registry s ON s.id = r.strategy_id AND s.lifecycle = 'retired';

UPDATE trading.strategy_registry s
SET lifecycle = r.before_state, updated_at = now()
FROM (
    SELECT DISTINCT ON (strategy_id) strategy_id, before_state
    FROM trading.strategy_lifecycle_log
    WHERE strategy_id = 'inc_tf_base' AND user_id = 'migration 008'
    ORDER BY strategy_id, created_at DESC
) AS r
WHERE s.id = r.strategy_id AND s.lifecycle = 'retired';

COMMENT ON COLUMN trading.strategy_registry.portfolio_id IS NULL;
COMMENT ON COLUMN trading.strategy_registry.strategy_type IS NULL;

COMMIT;
