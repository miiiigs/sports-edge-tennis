-- Append-only history for OddsPapi event identities.
--
-- oddspapi_event_identity_cache is a single mutable row per match: upserts
-- overwrite last_verified_at and invalidations DELETE the row. That destroys
-- point-in-time evidence, so a historical "warm at cutoff" identity cannot be
-- reconstructed (observed: two archived warm fixtures no longer reproducible).
--
-- This table records every upsert and invalidation with the values in force at
-- that moment. It is write-only for the runtime and read-only for research.

BEGIN;

CREATE TABLE oddspapi_event_identity_history (
    oddspapi_event_identity_history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_match_id BIGINT NOT NULL REFERENCES matches(match_id),
    sportradar_event_id TEXT,
    oddspapi_event_id TEXT NOT NULL CHECK (btrim(oddspapi_event_id) <> ''),
    betradar_id TEXT,
    oddspapi_tournament_id INTEGER NOT NULL,
    player_orientation TEXT NOT NULL,
    resolution_status TEXT NOT NULL,
    scheduled_start TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ NOT NULL,
    last_verified_at TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('UPSERTED', 'INVALIDATED')),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_oddspapi_identity_history_match
    ON oddspapi_event_identity_history (canonical_match_id, recorded_at DESC);

CREATE TRIGGER trg_oddspapi_identity_history_append_only_mutation
BEFORE UPDATE OR DELETE ON oddspapi_event_identity_history
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
