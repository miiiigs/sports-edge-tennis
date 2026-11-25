BEGIN;

DROP TABLE IF EXISTS prediction_feature_snapshots;

DROP INDEX IF EXISTS uq_match_versions_id_orientation;

COMMIT;
