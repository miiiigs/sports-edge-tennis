BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM match_versions
        WHERE match_start_at IS NULL
    ) THEN
        RAISE EXCEPTION
            '0003_day5_temporal_reconciliation.down.sql requires rows with NULL match_start_at to be removed before rollback.';
    END IF;
END;
$$;

ALTER TABLE match_versions
    ALTER COLUMN match_start_at SET NOT NULL;

COMMIT;
