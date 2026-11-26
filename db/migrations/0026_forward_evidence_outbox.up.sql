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
COMMIT;
