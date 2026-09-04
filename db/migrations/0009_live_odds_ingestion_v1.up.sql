BEGIN;

CREATE TYPE odds_observation_time_basis AS ENUM ('PROVIDER_TIME', 'RECEIVE_TIME');
CREATE TYPE odds_normalization_class AS ENUM ('SHARP', 'OFFERED', 'RAW_ONLY');
CREATE TYPE odds_origin AS ENUM ('HISTORICAL', 'SYNTHETIC', 'LIVE_PROVIDER');
CREATE TYPE raw_odds_observation_status AS ENUM ('RECEIVED');

CREATE TABLE raw_odds_observations (
    raw_odds_observation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingest_run_id BIGINT NOT NULL REFERENCES ingest_runs(ingest_run_id),
    provider_code TEXT NOT NULL CHECK (btrim(provider_code) <> ''),
    provider_event_id TEXT NOT NULL CHECK (btrim(provider_event_id) <> ''),
    provider_observation_key TEXT NOT NULL CHECK (btrim(provider_observation_key) <> ''),
    provider_request_id TEXT,
    provider_response_id TEXT,
    provider_observed_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL,
    observation_time TIMESTAMPTZ NOT NULL,
    observation_time_basis odds_observation_time_basis NOT NULL,
    payload_hash TEXT NOT NULL CHECK (btrim(payload_hash) <> ''),
    payload_json JSONB NOT NULL,
    adapter_version TEXT NOT NULL CHECK (btrim(adapter_version) <> ''),
    normalizer_version TEXT NOT NULL CHECK (btrim(normalizer_version) <> ''),
    raw_status raw_odds_observation_status NOT NULL DEFAULT 'RECEIVED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_raw_odds_observations_provider_key
        UNIQUE (provider_code, provider_observation_key),
    CONSTRAINT chk_raw_odds_observations_provider_time
        CHECK (
            (
                observation_time_basis = 'PROVIDER_TIME'
                AND provider_observed_at IS NOT NULL
                AND observation_time = provider_observed_at
                AND received_at >= provider_observed_at
            )
            OR (
                observation_time_basis = 'RECEIVE_TIME'
                AND provider_observed_at IS NULL
                AND observation_time = received_at
            )
        )
);

CREATE TABLE provider_bookmaker_mappings (
    provider_bookmaker_mapping_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_code TEXT NOT NULL CHECK (btrim(provider_code) <> ''),
    provider_bookmaker_key TEXT NOT NULL CHECK (btrim(provider_bookmaker_key) <> ''),
    mapping_version INTEGER NOT NULL CHECK (mapping_version > 0),
    bookmaker_code TEXT NOT NULL CHECK (btrim(bookmaker_code) <> ''),
    normalization_class odds_normalization_class NOT NULL,
    effective_from TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_provider_bookmaker_mappings_version
        UNIQUE (provider_code, provider_bookmaker_key, mapping_version),
    CONSTRAINT uq_provider_bookmaker_mappings_effective_from
        UNIQUE (provider_code, provider_bookmaker_key, effective_from)
);

CREATE OR REPLACE FUNCTION guard_provider_bookmaker_mapping_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    max_effective_from TIMESTAMPTZ;
    max_mapping_version INTEGER;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(NEW.provider_code || chr(31) || NEW.provider_bookmaker_key, 0)
    );

    SELECT max(effective_from), max(mapping_version)
    INTO max_effective_from, max_mapping_version
    FROM provider_bookmaker_mappings
    WHERE provider_code = NEW.provider_code
      AND provider_bookmaker_key = NEW.provider_bookmaker_key;

    IF max_effective_from IS NOT NULL AND NEW.effective_from <= max_effective_from THEN
        RAISE EXCEPTION
            'provider_bookmaker_mappings effective_from must move forward for provider_code=%, provider_bookmaker_key=%.',
            NEW.provider_code,
            NEW.provider_bookmaker_key;
    END IF;

    IF max_mapping_version IS NOT NULL AND NEW.mapping_version <= max_mapping_version THEN
        RAISE EXCEPTION
            'provider_bookmaker_mappings mapping_version must move forward for provider_code=%, provider_bookmaker_key=%.',
            NEW.provider_code,
            NEW.provider_bookmaker_key;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_provider_bookmaker_mappings_insert_guard
BEFORE INSERT ON provider_bookmaker_mappings
FOR EACH ROW
EXECUTE FUNCTION guard_provider_bookmaker_mapping_insert();

ALTER TABLE odds_snapshots
    ADD COLUMN raw_odds_observation_id BIGINT,
    ADD COLUMN provider_bookmaker_mapping_id BIGINT,
    ADD COLUMN odds_origin odds_origin,
    ADD CONSTRAINT fk_odds_snapshots_raw_odds_observation
        FOREIGN KEY (raw_odds_observation_id)
        REFERENCES raw_odds_observations(raw_odds_observation_id),
    ADD CONSTRAINT fk_odds_snapshots_provider_bookmaker_mapping
        FOREIGN KEY (provider_bookmaker_mapping_id)
        REFERENCES provider_bookmaker_mappings(provider_bookmaker_mapping_id),
    ADD CONSTRAINT chk_odds_snapshots_live_provider_lineage
        CHECK (
            odds_origin IS DISTINCT FROM 'LIVE_PROVIDER'
            OR (
                raw_odds_observation_id IS NOT NULL
                AND provider_bookmaker_mapping_id IS NOT NULL
                AND source_system IN ('SHARP', 'OFFERED')
            )
        );

CREATE INDEX idx_raw_odds_observations_provider_event
    ON raw_odds_observations (provider_code, provider_event_id, observation_time);

CREATE INDEX idx_provider_bookmaker_mappings_selector
    ON provider_bookmaker_mappings (provider_code, provider_bookmaker_key, effective_from DESC);

CREATE INDEX idx_odds_snapshots_raw_odds_observation
    ON odds_snapshots (raw_odds_observation_id)
    WHERE raw_odds_observation_id IS NOT NULL;

CREATE INDEX idx_odds_snapshots_provider_bookmaker_mapping
    ON odds_snapshots (provider_bookmaker_mapping_id)
    WHERE provider_bookmaker_mapping_id IS NOT NULL;

CREATE TRIGGER trg_raw_odds_observations_append_only_mutation
BEFORE UPDATE OR DELETE ON raw_odds_observations
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_provider_bookmaker_mappings_append_only_mutation
BEFORE UPDATE OR DELETE ON provider_bookmaker_mappings
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
