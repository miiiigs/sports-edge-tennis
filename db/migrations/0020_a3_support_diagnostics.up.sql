BEGIN;

-- Append-only, prospective observability for Agent 3 (intransitivity) support.
--
-- A3 abstains on almost every live fixture, and the reason could only be
-- reconstructed offline. This table records, for every A3 evaluation, exactly
-- what the matchup graph saw (direct support, raw vs eligible shared opponents,
-- q count) plus the gate that decided the outcome. It is operational telemetry
-- and is deliberately separate from official prediction truth.
--
-- Observations are append-only and deduplicated on
-- (match_id, evaluated_at, player_1_model_id, player_2_model_id).
CREATE TABLE a3_support_diagnostics (
    a3_support_diagnostic_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id BIGINT NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    player_1_model_id BIGINT,
    player_2_model_id BIGINT,
    identity_status TEXT NOT NULL CHECK (btrim(identity_status) <> ''),
    direct_raw_support INTEGER,
    direct_effective_support NUMERIC(18, 6),
    raw_shared_opponents INTEGER,
    eligible_shared_opponents INTEGER,
    q_count NUMERIC(18, 6),
    support_path TEXT,
    gate_failed TEXT,
    final_status TEXT NOT NULL CHECK (final_status IN ('PREDICTED', 'ABSTAINED')),
    graph_degree_player1 INTEGER,
    graph_degree_player2 INTEGER,
    history_count_player1 INTEGER,
    history_count_player2 INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_a3_support_diagnostics_observation
        UNIQUE (match_id, evaluated_at, player_1_model_id, player_2_model_id)
);

CREATE INDEX idx_a3_support_diagnostics_match
    ON a3_support_diagnostics (match_id, evaluated_at DESC);

CREATE INDEX idx_a3_support_diagnostics_status
    ON a3_support_diagnostics (final_status, evaluated_at DESC);

CREATE TRIGGER trg_a3_support_diagnostics_append_only_mutation
BEFORE UPDATE OR DELETE ON a3_support_diagnostics
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
