-- AlgoLens sends the daily book email, so it records the send for the engine (sent_at), the
-- column trade-ngin migration 026 leaves to the sender. Column-level: nothing else on
-- live_run_metadata becomes writable. Idempotent; a no-op without the role or the column.
BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_algolens')
       AND EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'trading' AND table_name = 'live_run_metadata'
                      AND column_name = 'sent_at') THEN
        GRANT UPDATE (sent_at) ON trading.live_run_metadata TO svc_algolens;
    END IF;
END $$;

COMMIT;
