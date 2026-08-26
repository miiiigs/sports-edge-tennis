BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM predictions
        UNION ALL
        SELECT 1 FROM decisions
        UNION ALL
        SELECT 1 FROM decision_agents
    ) THEN
        RAISE EXCEPTION
            '0004_prediction_runs_and_policy_versions.up.sql requires predictions, decisions, and decision_agents to be empty because exact T-ED1 lineage cannot be reconstructed safely.';
    END IF;
END;
$$;

CREATE TABLE prediction_runs (
    prediction_run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_status TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    prediction_finalized_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    experiment_id TEXT,
    parent_prediction_run_id BIGINT REFERENCES prediction_runs(prediction_run_id),
    parent_relation_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_prediction_runs_status
        CHECK (run_status IN ('RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')),
    CONSTRAINT chk_prediction_runs_prediction_finalized_after_started
        CHECK (prediction_finalized_at IS NULL OR prediction_finalized_at >= started_at),
    CONSTRAINT chk_prediction_runs_prediction_finalized_after_as_of
        CHECK (prediction_finalized_at IS NULL OR prediction_finalized_at >= as_of),
    CONSTRAINT chk_prediction_runs_ended_after_started
        CHECK (ended_at IS NULL OR ended_at >= started_at),
    CONSTRAINT chk_prediction_runs_ended_after_prediction_finalized
        CHECK (prediction_finalized_at IS NULL OR ended_at IS NULL OR ended_at >= prediction_finalized_at),
    CONSTRAINT chk_prediction_runs_running_has_no_ended_at
        CHECK (run_status <> 'RUNNING' OR ended_at IS NULL),
    CONSTRAINT chk_prediction_runs_terminal_has_ended_at
        CHECK (run_status = 'RUNNING' OR ended_at IS NOT NULL),
    CONSTRAINT chk_prediction_runs_parent_pairing
        CHECK (
            (parent_prediction_run_id IS NULL AND parent_relation_type IS NULL)
            OR (parent_prediction_run_id IS NOT NULL AND parent_relation_type IS NOT NULL)
        ),
    CONSTRAINT chk_prediction_runs_parent_relation_type
        CHECK (parent_relation_type IS NULL OR parent_relation_type IN ('RETRY', 'RECALCULATION')),
    CONSTRAINT chk_prediction_runs_not_self_parent
        CHECK (parent_prediction_run_id IS NULL OR parent_prediction_run_id <> prediction_run_id),
    CONSTRAINT uq_prediction_runs_id_as_of
        UNIQUE (prediction_run_id, as_of),
    CONSTRAINT uq_prediction_runs_id_prediction_finalized_at
        UNIQUE (prediction_run_id, prediction_finalized_at)
);

CREATE INDEX idx_prediction_runs_status_started_at
    ON prediction_runs (run_status, started_at DESC);

CREATE INDEX idx_prediction_runs_parent_prediction_run_id
    ON prediction_runs (parent_prediction_run_id);

CREATE TABLE decision_policy_versions (
    decision_policy_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_key TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    consensus_mode TEXT NOT NULL,
    allow_agent3_abstention BOOLEAN NOT NULL,
    max_probability_spread NUMERIC(8, 6) NOT NULL,
    min_active_agent_probability NUMERIC(8, 6) NOT NULL,
    requires_data_sufficiency BOOLEAN NOT NULL,
    min_recent_matches_with_serve_stats INTEGER NOT NULL,
    odds_feed_max_staleness_minutes INTEGER NOT NULL,
    match_data_max_staleness_hours INTEGER NOT NULL,
    ratings_max_staleness_hours INTEGER NOT NULL,
    fixture_feed_max_staleness_hours INTEGER NOT NULL,
    atp_main_all_agents_min_edge_pct NUMERIC(8, 6) NOT NULL,
    atp_main_agent3_abstain_min_edge_pct NUMERIC(8, 6) NOT NULL,
    challenger_min_edge_pct NUMERIC(8, 6) NOT NULL,
    default_devig_method TEXT NOT NULL,
    sharp_reference_policy_code TEXT NOT NULL,
    veto_policy_code TEXT NOT NULL,
    publication_requires_min_acceptable_odds BOOLEAN NOT NULL,
    publish_retractions_required BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_decision_policy_versions_identity
        UNIQUE (policy_key, policy_version),
    CONSTRAINT chk_decision_policy_versions_consensus_mode
        CHECK (consensus_mode = 'UNANIMOUS_ACTIVE_AGENTS'),
    CONSTRAINT chk_decision_policy_versions_allow_agent3_abstention
        CHECK (allow_agent3_abstention = TRUE),
    CONSTRAINT chk_decision_policy_versions_max_probability_spread
        CHECK (max_probability_spread >= 0 AND max_probability_spread <= 1),
    CONSTRAINT chk_decision_policy_versions_min_active_agent_probability
        CHECK (min_active_agent_probability >= 0 AND min_active_agent_probability <= 1),
    CONSTRAINT chk_decision_policy_versions_min_recent_matches
        CHECK (min_recent_matches_with_serve_stats >= 0),
    CONSTRAINT chk_decision_policy_versions_odds_feed_staleness
        CHECK (odds_feed_max_staleness_minutes >= 0),
    CONSTRAINT chk_decision_policy_versions_match_data_staleness
        CHECK (match_data_max_staleness_hours >= 0),
    CONSTRAINT chk_decision_policy_versions_ratings_staleness
        CHECK (ratings_max_staleness_hours >= 0),
    CONSTRAINT chk_decision_policy_versions_fixture_feed_staleness
        CHECK (fixture_feed_max_staleness_hours >= 0),
    CONSTRAINT chk_decision_policy_versions_atp_main_all_agents_min_edge
        CHECK (atp_main_all_agents_min_edge_pct >= 0),
    CONSTRAINT chk_decision_policy_versions_atp_main_agent3_abstain_min_edge
        CHECK (atp_main_agent3_abstain_min_edge_pct >= 0),
    CONSTRAINT chk_decision_policy_versions_challenger_min_edge
        CHECK (challenger_min_edge_pct >= 0),
    CONSTRAINT chk_decision_policy_versions_default_devig_method
        CHECK (default_devig_method IN ('POWER', 'SHIN'))
);

CREATE TABLE agent_config_versions (
    agent_config_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_code agent_code NOT NULL,
    config_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    calibration_version TEXT,
    parameter_schema_version TEXT NOT NULL,
    config_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_agent_config_versions_identity
        UNIQUE (agent_code, config_version),
    CONSTRAINT uq_agent_config_versions_lineage_identity
        UNIQUE (agent_config_version_id, agent_code, model_version, feature_version),
    CONSTRAINT chk_agent_config_versions_config_version
        CHECK (btrim(config_version) <> ''),
    CONSTRAINT chk_agent_config_versions_model_version
        CHECK (btrim(model_version) <> ''),
    CONSTRAINT chk_agent_config_versions_feature_version
        CHECK (btrim(feature_version) <> ''),
    CONSTRAINT chk_agent_config_versions_parameter_schema_version
        CHECK (btrim(parameter_schema_version) <> ''),
    CONSTRAINT chk_agent_config_versions_config_payload_object
        CHECK (jsonb_typeof(config_payload) = 'object')
);

CREATE INDEX idx_agent_config_versions_agent_created_at
    ON agent_config_versions (agent_code, created_at DESC);

DROP INDEX IF EXISTS uq_predictions_identity;
DROP INDEX IF EXISTS uq_decisions_identity;

ALTER TABLE predictions
    ADD COLUMN prediction_run_id BIGINT NOT NULL,
    ADD COLUMN agent_config_version_id BIGINT NOT NULL,
    ADD CONSTRAINT fk_predictions_prediction_run
        FOREIGN KEY (prediction_run_id)
        REFERENCES prediction_runs(prediction_run_id),
    ADD CONSTRAINT fk_predictions_prediction_run_as_of
        FOREIGN KEY (prediction_run_id, as_of)
        REFERENCES prediction_runs(prediction_run_id, as_of),
    ADD CONSTRAINT fk_predictions_prediction_run_finalization
        FOREIGN KEY (prediction_run_id, prediction_locked_at)
        REFERENCES prediction_runs(prediction_run_id, prediction_finalized_at),
    ADD CONSTRAINT fk_predictions_agent_config_lineage
        FOREIGN KEY (agent_config_version_id, agent_code, model_version, feature_version)
        REFERENCES agent_config_versions(
            agent_config_version_id,
            agent_code,
            model_version,
            feature_version
        ),
    ADD CONSTRAINT uq_predictions_run_match_agent
        UNIQUE (prediction_run_id, match_id, agent_code),
    ADD CONSTRAINT uq_predictions_lineage_identity
        UNIQUE (prediction_id, prediction_run_id, match_id, agent_code);

ALTER TABLE decisions
    ADD COLUMN prediction_run_id BIGINT NOT NULL,
    ADD COLUMN decision_policy_version_id BIGINT NOT NULL,
    ADD CONSTRAINT fk_decisions_prediction_run
        FOREIGN KEY (prediction_run_id)
        REFERENCES prediction_runs(prediction_run_id),
    ADD CONSTRAINT fk_decisions_policy_version
        FOREIGN KEY (decision_policy_version_id)
        REFERENCES decision_policy_versions(decision_policy_version_id),
    ADD CONSTRAINT uq_decisions_lineage_identity
        UNIQUE (decision_id, prediction_run_id, match_id);

ALTER TABLE decision_agents
    ADD COLUMN prediction_run_id BIGINT NOT NULL,
    ADD COLUMN match_id BIGINT NOT NULL,
    ADD COLUMN prediction_id BIGINT,
    ADD CONSTRAINT fk_decision_agents_decision_lineage
        FOREIGN KEY (decision_id, prediction_run_id, match_id)
        REFERENCES decisions(decision_id, prediction_run_id, match_id),
    ADD CONSTRAINT fk_decision_agents_prediction_lineage
        FOREIGN KEY (prediction_id, prediction_run_id, match_id, agent_code)
        REFERENCES predictions(prediction_id, prediction_run_id, match_id, agent_code),
    ADD CONSTRAINT chk_decision_agents_participation_nullability
        CHECK (
            (participation_status = 'ACTIVE' AND prediction_id IS NOT NULL AND proposed_selection_side IS NOT NULL AND proposed_probability IS NOT NULL)
            OR (participation_status = 'ABSTAINED' AND prediction_id IS NOT NULL AND proposed_selection_side IS NULL AND proposed_probability IS NULL)
            OR (participation_status = 'INACTIVE' AND prediction_id IS NULL AND proposed_selection_side IS NULL AND proposed_probability IS NULL)
        );

CREATE OR REPLACE FUNCTION validate_decision_agent_lineage_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    linked_prediction_status prediction_status;
    linked_selection_side SMALLINT;
    linked_win_probability NUMERIC(8, 6);
BEGIN
    IF TG_TABLE_NAME = 'decision_agents' THEN
        IF NEW.participation_status = 'ACTIVE' THEN
            SELECT p.prediction_status, p.selection_side, p.win_probability
            INTO linked_prediction_status, linked_selection_side, linked_win_probability
            FROM predictions p
            WHERE p.prediction_id = NEW.prediction_id
              AND p.prediction_run_id = NEW.prediction_run_id
              AND p.match_id = NEW.match_id
              AND p.agent_code = NEW.agent_code;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'decision_agents ACTIVE row must reference an existing aligned prediction row.';
            END IF;

            IF linked_prediction_status <> 'PREDICTED' THEN
                RAISE EXCEPTION
                    'decision_agents ACTIVE rows must reference predictions with prediction_status = PREDICTED.';
            END IF;

            IF NEW.proposed_selection_side IS DISTINCT FROM linked_selection_side THEN
                RAISE EXCEPTION
                    'decision_agents ACTIVE proposed_selection_side must equal predictions.selection_side.';
            END IF;

            IF NEW.proposed_probability IS DISTINCT FROM linked_win_probability THEN
                RAISE EXCEPTION
                    'decision_agents ACTIVE proposed_probability must equal predictions.win_probability.';
            END IF;
        ELSIF NEW.participation_status = 'ABSTAINED' THEN
            SELECT p.prediction_status
            INTO linked_prediction_status
            FROM predictions p
            WHERE p.prediction_id = NEW.prediction_id
              AND p.prediction_run_id = NEW.prediction_run_id
              AND p.match_id = NEW.match_id
              AND p.agent_code = NEW.agent_code;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'decision_agents ABSTAINED row must reference an existing aligned prediction row.';
            END IF;

            IF linked_prediction_status <> 'ABSTAINED' THEN
                RAISE EXCEPTION
                    'decision_agents ABSTAINED rows must reference predictions with prediction_status = ABSTAINED.';
            END IF;
        ELSIF NEW.participation_status = 'INACTIVE' THEN
            IF NEW.prediction_id IS NOT NULL THEN
                RAISE EXCEPTION
                    'decision_agents INACTIVE rows must not reference a prediction.';
            END IF;

            IF NEW.proposed_selection_side IS NOT NULL OR NEW.proposed_probability IS NOT NULL THEN
                RAISE EXCEPTION
                    'decision_agents INACTIVE rows must not store proposal audit values.';
            END IF;
        END IF;

        RETURN NEW;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM decision_agents da
        WHERE da.prediction_id = NEW.prediction_id
          AND (
              (da.participation_status = 'ACTIVE' AND (
                  NEW.prediction_status <> 'PREDICTED'
                  OR da.proposed_selection_side IS DISTINCT FROM NEW.selection_side
                  OR da.proposed_probability IS DISTINCT FROM NEW.win_probability
              ))
              OR (da.participation_status = 'ABSTAINED' AND NEW.prediction_status <> 'ABSTAINED')
          )
    ) THEN
        RAISE EXCEPTION
            'predictions update would violate decision_agents lineage integrity.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_decision_agents_lineage_integrity
AFTER INSERT OR UPDATE ON decision_agents
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION validate_decision_agent_lineage_integrity();

CREATE CONSTRAINT TRIGGER trg_predictions_decision_agent_lineage_integrity
AFTER UPDATE OF prediction_status, selection_side, win_probability ON predictions
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION validate_decision_agent_lineage_integrity();

COMMIT;
