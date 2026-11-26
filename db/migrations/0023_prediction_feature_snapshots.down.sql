BEGIN;

-- Evidence-preserving guard: a schema rollback must never destroy captured
-- research evidence. Export/archive the table first, then truncate or drop it
-- deliberately. Empty tables still roll back for clean-room tests.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM prediction_feature_snapshots) THEN
        RAISE EXCEPTION
            'refusing to drop prediction_feature_snapshots: evidence rows present; export or archive first';
    END IF;
END
$$;

DROP TABLE IF EXISTS prediction_feature_snapshots;

DROP INDEX IF EXISTS uq_match_versions_id_orientation;

COMMIT;
