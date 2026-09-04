BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM odds_snapshots WHERE odds_origin = 'LIVE_PROVIDER'
    ) THEN
        RAISE EXCEPTION
            '0009_live_odds_ingestion_v1.down.sql cannot roll back persisted LIVE_PROVIDER odds snapshots.';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS trg_raw_odds_observations_append_only_mutation ON raw_odds_observations;
DROP TRIGGER IF EXISTS trg_provider_bookmaker_mappings_append_only_mutation ON provider_bookmaker_mappings;
DROP TRIGGER IF EXISTS trg_provider_bookmaker_mappings_insert_guard ON provider_bookmaker_mappings;

ALTER TABLE odds_snapshots
    DROP CONSTRAINT IF EXISTS chk_odds_snapshots_live_provider_lineage,
    DROP CONSTRAINT IF EXISTS fk_odds_snapshots_provider_bookmaker_mapping,
    DROP CONSTRAINT IF EXISTS fk_odds_snapshots_raw_odds_observation,
    DROP COLUMN IF EXISTS odds_origin,
    DROP COLUMN IF EXISTS provider_bookmaker_mapping_id,
    DROP COLUMN IF EXISTS raw_odds_observation_id;

DROP FUNCTION IF EXISTS guard_provider_bookmaker_mapping_insert();

DROP TABLE IF EXISTS provider_bookmaker_mappings;
DROP TABLE IF EXISTS raw_odds_observations;

DROP TYPE IF EXISTS raw_odds_observation_status;
DROP TYPE IF EXISTS odds_origin;
DROP TYPE IF EXISTS odds_normalization_class;
DROP TYPE IF EXISTS odds_observation_time_basis;

COMMIT;
