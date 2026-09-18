BEGIN;

CREATE TABLE a2_history_data_regimes (
    regime_code TEXT PRIMARY KEY CHECK (btrim(regime_code) <> ''),
    source_system TEXT NOT NULL CHECK (source_system = 'API_TENNIS'),
    window_start DATE NOT NULL,
    window_end DATE NOT NULL,
    chunk_days SMALLINT NOT NULL CHECK (chunk_days BETWEEN 1 AND 31),
    activated_at TIMESTAMPTZ NOT NULL,
    activation_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (window_end >= window_start)
);

CREATE TRIGGER trg_a2_history_data_regimes_append_only_mutation
BEFORE UPDATE OR DELETE ON a2_history_data_regimes
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
