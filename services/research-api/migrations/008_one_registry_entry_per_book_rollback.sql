-- Rollback of 008_one_registry_entry_per_book.sql: drops the one-entry-per-book index and
-- returns inc_tf_base to the state 008 retired it from (its incubation start and mock capital
-- were kept). Only 008's own retirement is undone, and only while it is the entry's
-- newest transition.

BEGIN;

DROP INDEX IF EXISTS trading.strategy_registry_one_entry_per_book;

-- Only when 008's retirement is still the entry's newest transition: a later retirement
-- through the app is the app's, and stays.
CREATE TEMP TABLE m008_undo ON COMMIT DROP AS
SELECT l.strategy_id, l.before_state
FROM (
    SELECT DISTINCT ON (strategy_id) strategy_id, before_state, user_id
    FROM trading.strategy_lifecycle_log
    WHERE strategy_id = 'inc_tf_base'
    ORDER BY strategy_id, created_at DESC, id DESC
) AS l
JOIN trading.strategy_registry s ON s.id = l.strategy_id AND s.lifecycle = 'retired'
WHERE l.user_id = 'migration 008';

INSERT INTO trading.strategy_lifecycle_log
    (strategy_id, before_state, after_state, reason, user_id)
SELECT strategy_id, 'retired', before_state, 'rollback of migration 008', 'migration 008 rollback'
FROM m008_undo;

UPDATE trading.strategy_registry s
SET lifecycle = u.before_state, updated_at = now()
FROM m008_undo u
WHERE s.id = u.strategy_id;

COMMENT ON COLUMN trading.strategy_registry.portfolio_id IS NULL;
COMMENT ON COLUMN trading.strategy_registry.strategy_type IS NULL;

COMMIT;
