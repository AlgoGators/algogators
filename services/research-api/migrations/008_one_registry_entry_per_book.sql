-- 008: one registry entry per book (issue #59).
--
-- AlgoLens finds a book by its portfolio id, never by the engine's strategy id. The
-- registry's key to the trading tables is portfolio_id; strategy_type stays as a description
-- shown on the page and is not matched on.
--
-- BASE_PORTFOLIO holds two entries: inc_tf_fast (the two-sleeve book) and inc_tf_base (its
-- rows from before the second sleeve was added in November 2025). Read by portfolio id both
-- would show the same book, so inc_tf_base is retired, kept as history with its incubation
-- start and mock capital, and the change is logged. A partial unique index then keeps one
-- entry per book that is not retired.
--
-- Safe to run more than once. Fails, changing nothing, if another portfolio still has two
-- entries that are not retired: retire one of them first.

BEGIN;

INSERT INTO trading.strategy_lifecycle_log
    (strategy_id, before_state, after_state, reason, user_id)
SELECT id, lifecycle, 'retired',
       'issue #59: one registry entry per book; BASE_PORTFOLIO is read through inc_tf_fast',
       'migration 008'
FROM trading.strategy_registry
WHERE id = 'inc_tf_base' AND lifecycle <> 'retired';

UPDATE trading.strategy_registry
SET lifecycle = 'retired', updated_at = now()
WHERE id = 'inc_tf_base' AND lifecycle <> 'retired';

DO $$
DECLARE
    doubled TEXT;
BEGIN
    SELECT string_agg(portfolio_id || ' (' || ids || ')', ', ')
    INTO doubled
    FROM (
        SELECT portfolio_id, string_agg(id, ', ' ORDER BY id) AS ids
        FROM trading.strategy_registry
        WHERE lifecycle <> 'retired'
        GROUP BY portfolio_id
        HAVING count(*) > 1
    ) AS d;
    IF doubled IS NOT NULL THEN
        RAISE EXCEPTION 'more than one registry entry per book: %; retire all but one', doubled;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS strategy_registry_one_entry_per_book
    ON trading.strategy_registry (portfolio_id)
    WHERE lifecycle <> 'retired';

COMMENT ON COLUMN trading.strategy_registry.portfolio_id IS
    'The book: AlgoLens reads every trading table by this id (issue #59).';
COMMENT ON COLUMN trading.strategy_registry.strategy_type IS
    'Display only (issue #59): the engine''s strategy id as a description; never matched on.';

COMMIT;
