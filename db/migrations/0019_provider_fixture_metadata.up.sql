BEGIN;

-- Compact, append-only provenance for provider fixture observations.
--
-- Normalized persistence (match_versions / tournament_editions / tournaments)
-- intentionally discards most provider wording (raw event type, raw round,
-- raw qualification, location). That loss made the 2026 Shanghai Masters
-- qualifying identity impossible to re-audit from stored data. This table keeps
-- the minimum raw provider fields needed to explain a normalized value, without
-- storing whole provider payloads.
--
-- Observations are append-only and deduplicated on
-- (source_system, source_match_id, payload_hash): identical provider observations
-- collapse to the first sighting while any change in raw wording appends a new
-- immutable row.
CREATE TABLE provider_fixture_metadata (
    provider_fixture_metadata_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    source_match_id TEXT NOT NULL CHECK (btrim(source_match_id) <> ''),
    source_tournament_id TEXT,
    source_tournament_season INTEGER,
    raw_tournament_name TEXT,
    raw_event_type_key TEXT,
    raw_event_type_type TEXT,
    raw_tournament_round TEXT,
    raw_event_qualification TEXT,
    raw_surface TEXT,
    raw_country TEXT,
    raw_location TEXT,
    raw_stage TEXT,
    payload_hash TEXT NOT NULL CHECK (btrim(payload_hash) <> ''),
    observed_at TIMESTAMPTZ NOT NULL,
    ingest_run_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_provider_fixture_metadata_observation
        UNIQUE (source_system, source_match_id, payload_hash)
);

CREATE INDEX idx_provider_fixture_metadata_match
    ON provider_fixture_metadata (source_system, source_match_id);

CREATE INDEX idx_provider_fixture_metadata_tournament
    ON provider_fixture_metadata (source_system, source_tournament_id);

CREATE TRIGGER trg_provider_fixture_metadata_append_only_mutation
BEFORE UPDATE OR DELETE ON provider_fixture_metadata
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
