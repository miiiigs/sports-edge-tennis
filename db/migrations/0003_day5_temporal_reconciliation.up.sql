BEGIN;

ALTER TABLE match_versions
    ALTER COLUMN match_start_at DROP NOT NULL;

COMMIT;
