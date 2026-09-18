BEGIN;

CREATE TYPE publication_control_action AS ENUM ('HALT', 'ENABLE');
CREATE TYPE publication_control_source AS ENUM (
    'OPERATOR',
    'CLV_STOP',
    'DRAWDOWN_STOP',
    'SYSTEM'
);

CREATE TABLE publication_control_events (
    publication_control_event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action publication_control_action NOT NULL,
    source publication_control_source NOT NULL,
    reason TEXT NOT NULL,
    actor TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_publication_control_reason_nonempty
        CHECK (btrim(reason) <> ''),
    CONSTRAINT chk_publication_control_operator_actor
        CHECK (
            source <> 'OPERATOR'
            OR (actor IS NOT NULL AND btrim(actor) <> '')
        ),
    CONSTRAINT chk_publication_control_enable_operator_only
        CHECK (action <> 'ENABLE' OR source = 'OPERATOR')
);

CREATE INDEX idx_publication_control_events_latest
    ON publication_control_events (created_at DESC, publication_control_event_id DESC);

CREATE TRIGGER trg_publication_control_events_append_only_mutation
BEFORE UPDATE OR DELETE ON publication_control_events
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
