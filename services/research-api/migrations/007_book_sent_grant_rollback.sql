DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_algolens')
       AND EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'trading' AND table_name = 'live_run_metadata'
                      AND column_name = 'sent_at') THEN
        REVOKE UPDATE (sent_at) ON trading.live_run_metadata FROM svc_algolens;
    END IF;
END $$;
