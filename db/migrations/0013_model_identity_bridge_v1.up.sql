BEGIN;

CREATE TABLE model_identity_bridge_events (
    model_identity_bridge_event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    live_player_id BIGINT NOT NULL REFERENCES players(player_id),
    action TEXT NOT NULL CHECK (action IN ('BRIDGE', 'DISABLE')),
    historical_player_id BIGINT REFERENCES players(player_id),
    evidence_source_system TEXT,
    evidence_match_id BIGINT REFERENCES matches(match_id),
    evidence_provider_event_id TEXT,
    evidence_provider_player_id TEXT,
    evidence_provider_name TEXT,
    evidence_status TEXT,
    evidence_as_of TIMESTAMPTZ NOT NULL,
    activated_at TIMESTAMPTZ NOT NULL,
    actor TEXT NOT NULL CHECK (btrim(actor) <> ''),
    reason TEXT NOT NULL CHECK (btrim(reason) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (activated_at >= evidence_as_of),
    CHECK (
        (
            action = 'BRIDGE'
            AND historical_player_id IS NOT NULL
            AND live_player_id <> historical_player_id
            AND evidence_source_system IS NOT NULL
            AND btrim(evidence_source_system) <> ''
            AND evidence_match_id IS NOT NULL
            AND evidence_provider_event_id IS NOT NULL
            AND btrim(evidence_provider_event_id) <> ''
            AND evidence_provider_player_id IS NOT NULL
            AND btrim(evidence_provider_player_id) <> ''
            AND evidence_provider_name IS NOT NULL
            AND btrim(evidence_provider_name) <> ''
            AND evidence_status = 'UNIQUE_PIT_CANDIDATE'
        )
        OR
        (
            action = 'DISABLE'
            AND historical_player_id IS NULL
        )
    )
);

CREATE INDEX idx_model_identity_bridge_events_latest
    ON model_identity_bridge_events (
        live_player_id,
        activated_at DESC,
        model_identity_bridge_event_id DESC
    );

CREATE TRIGGER trg_model_identity_bridge_events_append_only_mutation
BEFORE UPDATE OR DELETE ON model_identity_bridge_events
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
