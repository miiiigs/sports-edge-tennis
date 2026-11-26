-- Opt-in research evidence. No automatic publishing or production activation.
BEGIN;
CREATE TABLE forward_experiment_freezes (
    experiment_version TEXT PRIMARY KEY CHECK (btrim(experiment_version) <> ''),
    freeze_bytes BYTEA NOT NULL CHECK (octet_length(freeze_bytes) > 0),
    freeze_sha256 TEXT NOT NULL CHECK (freeze_sha256 = encode(sha256(freeze_bytes), 'hex'))
);
CREATE TABLE forward_originals (
    experiment_version TEXT NOT NULL REFERENCES forward_experiment_freezes(experiment_version),
    match_id BIGINT NOT NULL,
    prediction_run_id BIGINT NOT NULL,
    payload BYTEA NOT NULL CHECK (octet_length(payload) BETWEEN 1 AND 1048576),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 = encode(sha256(payload), 'hex')),
    issued_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (experiment_version, match_id),
    FOREIGN KEY (prediction_run_id, match_id)
        REFERENCES prediction_run_targets(prediction_run_id, match_id)
);
CREATE TABLE forward_delivery_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_version TEXT NOT NULL,
    match_id BIGINT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('ARCHIVE', 'TELEGRAM')),
    state TEXT NOT NULL CHECK (state IN ('PENDING','DELIVERED','CONFIRMED_FAILURE','DELIVERY_UNCERTAIN','RECONCILED')),
    evidence TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (experiment_version, match_id) REFERENCES forward_originals(experiment_version, match_id),
    CHECK (state NOT IN ('DELIVERED','RECONCILED','CONFIRMED_FAILURE') OR
           (evidence IS NOT NULL AND btrim(evidence) <> ''))
);
CREATE INDEX idx_forward_delivery_lookup ON forward_delivery_events
    (experiment_version, match_id, channel, event_id DESC);
CREATE TRIGGER trg_forward_freezes_append_only BEFORE UPDATE OR DELETE ON forward_experiment_freezes
    FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();
CREATE TRIGGER trg_forward_originals_append_only BEFORE UPDATE OR DELETE ON forward_originals
    FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();
CREATE TRIGGER trg_forward_delivery_append_only BEFORE UPDATE OR DELETE ON forward_delivery_events
    FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();

-- Settlement evidence is separate from original forecasting and publication.
CREATE TABLE forward_settlement_events (
    settlement_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_version TEXT NOT NULL,
    match_id BIGINT NOT NULL,
    pathway TEXT NOT NULL CHECK (pathway IN ('SPORTING','BOOKMAKER')),
    market_key TEXT NOT NULL CHECK (btrim(market_key) <> ''),
    evidence_key TEXT NOT NULL CHECK (btrim(evidence_key) <> ''),
    supersedes_id BIGINT REFERENCES forward_settlement_events(settlement_id),
    status TEXT NOT NULL CHECK (status IN ('WON','LOST','VOID','CANCELLED','UNRESOLVED','NO_SELECTION')),
    outcome_kind TEXT NOT NULL CHECK (outcome_kind IN
        ('COMPLETED','RETIRED','WALKOVER','DEFAULTED','CANCELLED','UNRESOLVED')),
    evidence BYTEA NOT NULL CHECK (octet_length(evidence) BETWEEN 1 AND 1048576),
    evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 = encode(sha256(evidence), 'hex')),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (experiment_version, match_id) REFERENCES forward_originals(experiment_version, match_id),
    UNIQUE (experiment_version, match_id, pathway, market_key, evidence_key),
    UNIQUE (supersedes_id)
);
CREATE INDEX idx_forward_settlement_lookup ON forward_settlement_events
    (experiment_version, match_id, pathway, market_key, settlement_id DESC);
CREATE TRIGGER trg_forward_settlement_append_only BEFORE UPDATE OR DELETE ON forward_settlement_events
    FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();

CREATE TABLE forward_fixture_expectations (
    experiment_version TEXT NOT NULL REFERENCES forward_experiment_freezes(experiment_version),
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    expected_at TIMESTAMPTZ NOT NULL,
    source_evidence BYTEA NOT NULL CHECK (octet_length(source_evidence) > 0),
    source_sha256 TEXT NOT NULL CHECK (source_sha256 = encode(sha256(source_evidence), 'hex')),
    PRIMARY KEY (experiment_version, match_id)
);
CREATE TRIGGER trg_forward_expectations_append_only BEFORE UPDATE OR DELETE ON forward_fixture_expectations
    FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
