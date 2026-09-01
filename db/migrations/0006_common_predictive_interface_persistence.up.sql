BEGIN;

DROP TRIGGER IF EXISTS trg_predictions_decision_agent_lineage_integrity ON predictions;
DROP TRIGGER IF EXISTS trg_decision_agents_lineage_integrity ON decision_agents;
DROP TRIGGER IF EXISTS trg_decision_agents_append_only_mutation ON decision_agents;
DROP TRIGGER IF EXISTS trg_predictions_append_only_mutation ON predictions;
DROP TRIGGER IF EXISTS trg_predictions_parent_run_lock_guard ON predictions;
DROP TRIGGER IF EXISTS trg_prediction_runs_lifecycle_guard ON prediction_runs;

DROP FUNCTION IF EXISTS validate_decision_agent_lineage_integrity();
DROP FUNCTION IF EXISTS guard_prediction_parent_run_insert();
DROP FUNCTION IF EXISTS validate_prediction_run_lifecycle();

ALTER TABLE prediction_runs
    ADD COLUMN prediction_contract_version TEXT;

UPDATE prediction_runs
SET prediction_contract_version = 'legacy_prediction_ledger_v1'
WHERE prediction_contract_version IS NULL;

ALTER TABLE prediction_runs
    ALTER COLUMN prediction_contract_version SET NOT NULL,
    ADD CONSTRAINT chk_prediction_runs_contract_version
        CHECK (prediction_contract_version IN ('legacy_prediction_ledger_v1', 'predictive_agent_result_v1'));

ALTER TABLE predictions
    ALTER COLUMN win_probability TYPE NUMERIC(51, 50),
    ADD COLUMN probability_representation TEXT;

UPDATE predictions
SET probability_representation = 'legacy_persisted_win_probability_v1'
WHERE prediction_status = 'PREDICTED'
  AND probability_representation IS NULL;

ALTER TABLE predictions
    ADD CONSTRAINT chk_predictions_probability_representation
        CHECK (
            (prediction_status = 'PREDICTED' AND probability_representation IS NOT NULL AND btrim(probability_representation) <> '')
            OR (prediction_status = 'ABSTAINED' AND probability_representation IS NULL)
        ),
    ADD CONSTRAINT chk_predictions_live_probability_representation
        CHECK (
            probability_representation IS NULL
            OR probability_representation IN (
                'agent1_raw_probability_v1',
                'agent2_raw_probability_v1',
                'agent3_raw_probability_v1',
                'legacy_persisted_win_probability_v1'
            )
        );

ALTER TABLE predictions
    ALTER COLUMN probability_representation SET DEFAULT NULL;

ALTER TABLE decision_agents
    ALTER COLUMN proposed_probability TYPE NUMERIC(51, 50),
    ADD COLUMN probability_representation TEXT;

UPDATE decision_agents da
SET probability_representation = p.probability_representation
FROM predictions p
WHERE da.participation_status = 'ACTIVE'
  AND da.prediction_id = p.prediction_id
  AND da.prediction_run_id = p.prediction_run_id
  AND da.match_id = p.match_id
  AND da.agent_code = p.agent_code
  AND da.probability_representation IS NULL;

ALTER TABLE decision_agents
    ADD CONSTRAINT chk_decision_agents_probability_representation
        CHECK (
            (participation_status = 'ACTIVE' AND probability_representation IS NOT NULL AND btrim(probability_representation) <> '')
            OR (participation_status IN ('ABSTAINED', 'INACTIVE') AND probability_representation IS NULL)
        ),
    ADD CONSTRAINT chk_decision_agents_live_probability_representation
        CHECK (
            probability_representation IS NULL
            OR probability_representation IN (
                'agent1_raw_probability_v1',
                'agent2_raw_probability_v1',
                'agent3_raw_probability_v1',
                'legacy_persisted_win_probability_v1'
            )
        );

ALTER TABLE decision_agents
    DROP CONSTRAINT chk_decision_agents_participation_nullability,
    ADD CONSTRAINT chk_decision_agents_participation_nullability
        CHECK (
            (
                participation_status = 'ACTIVE'
                AND prediction_id IS NOT NULL
                AND proposed_selection_side IS NOT NULL
                AND proposed_probability IS NOT NULL
                AND probability_representation IS NOT NULL
            )
            OR (
                participation_status = 'ABSTAINED'
                AND prediction_id IS NOT NULL
                AND proposed_selection_side IS NULL
                AND proposed_probability IS NULL
                AND probability_representation IS NULL
            )
            OR (
                participation_status = 'INACTIVE'
                AND prediction_id IS NULL
                AND proposed_selection_side IS NULL
                AND proposed_probability IS NULL
                AND probability_representation IS NULL
            )
        );

CREATE TABLE prediction_run_targets (
    prediction_run_id BIGINT NOT NULL REFERENCES prediction_runs(prediction_run_id),
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (prediction_run_id, match_id)
);

CREATE INDEX idx_prediction_run_targets_match
    ON prediction_run_targets (match_id);

CREATE INDEX idx_predictions_run_representation
    ON predictions (prediction_run_id, probability_representation);

CREATE INDEX idx_decision_agents_run_representation
    ON decision_agents (prediction_run_id, probability_representation);

CREATE OR REPLACE FUNCTION validate_prediction_run_v1_completeness(target_prediction_run_id BIGINT)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM predictions p
        LEFT JOIN prediction_run_targets t
          ON t.prediction_run_id = p.prediction_run_id
         AND t.match_id = p.match_id
        WHERE p.prediction_run_id = target_prediction_run_id
          AND t.prediction_run_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'predictive_agent_result_v1 runs cannot finalize with predictions outside prediction_run_targets.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM prediction_run_targets t
        CROSS JOIN (
            VALUES
                ('AGENT_1_SURFACE_ELO'::agent_code),
                ('AGENT_2_HIERARCHICAL_MARKOV'::agent_code),
                ('AGENT_3_INTRANSITIVITY'::agent_code)
        ) expected(agent_code)
        WHERE t.prediction_run_id = target_prediction_run_id
          AND NOT EXISTS (
              SELECT 1
              FROM predictions p
              WHERE p.prediction_run_id = t.prediction_run_id
                AND p.match_id = t.match_id
                AND p.agent_code = expected.agent_code
          )
    ) THEN
        RAISE EXCEPTION
            'predictive_agent_result_v1 runs require exactly the canonical three-agent prediction set for every target.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM predictions p
        WHERE p.prediction_run_id = target_prediction_run_id
          AND p.agent_code NOT IN (
              'AGENT_1_SURFACE_ELO',
              'AGENT_2_HIERARCHICAL_MARKOV',
              'AGENT_3_INTRANSITIVITY'
          )
    ) THEN
        RAISE EXCEPTION
            'predictive_agent_result_v1 runs cannot finalize with non-canonical predictive agents.';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION validate_prediction_run_lifecycle()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['run_status', 'prediction_finalized_at', 'ended_at'])
        IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['run_status', 'prediction_finalized_at', 'ended_at']) THEN
        RAISE EXCEPTION
            'prediction_runs identity fields are immutable under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    IF OLD.run_status <> 'RUNNING' THEN
        RAISE EXCEPTION
            'prediction_runs terminal rows are immutable under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    IF NEW.prediction_contract_version = 'predictive_agent_result_v1'
       AND OLD.prediction_finalized_at IS NULL
       AND NEW.prediction_finalized_at IS NOT NULL THEN
        PERFORM validate_prediction_run_v1_completeness(NEW.prediction_run_id);
    END IF;

    IF NEW.run_status = 'RUNNING' THEN
        IF OLD.prediction_finalized_at IS NULL
           AND NEW.prediction_finalized_at IS NOT NULL
           AND OLD.ended_at IS NULL
           AND NEW.ended_at IS NULL THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'prediction_runs only allows RUNNING -> RUNNING finalization updates that set prediction_finalized_at once while ended_at stays NULL.';
    END IF;

    IF NEW.run_status = 'COMPLETED' THEN
        IF OLD.ended_at IS NULL
           AND NEW.ended_at IS NOT NULL
           AND (
               (OLD.prediction_finalized_at IS NOT NULL AND NEW.prediction_finalized_at = OLD.prediction_finalized_at)
               OR (OLD.prediction_finalized_at IS NULL AND NEW.prediction_finalized_at IS NOT NULL)
           ) THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'prediction_runs RUNNING -> COMPLETED requires ended_at NULL -> timestamp and prediction_finalized_at either unchanged after prior finalization or set in the same terminal update.';
    END IF;

    IF NEW.run_status IN ('FAILED', 'CANCELLED') THEN
        IF OLD.ended_at IS NULL
           AND NEW.ended_at IS NOT NULL
           AND (
               (OLD.prediction_finalized_at IS NULL)
               OR (NEW.prediction_finalized_at = OLD.prediction_finalized_at)
           ) THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'prediction_runs RUNNING -> FAILED/CANCELLED requires ended_at NULL -> timestamp and does not allow prediction_finalized_at to be cleared or rewritten.';
    END IF;

    RAISE EXCEPTION
        'prediction_runs allows only RUNNING -> RUNNING finalization and RUNNING -> COMPLETED/FAILED/CANCELLED terminal transitions under 0005_append_only_enforcement_and_lock_guards.';
END;
$$;

CREATE OR REPLACE FUNCTION validate_prediction_run_target_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    parent_run_status prediction_runs.run_status%TYPE;
    parent_prediction_finalized_at prediction_runs.prediction_finalized_at%TYPE;
    parent_contract_version prediction_runs.prediction_contract_version%TYPE;
BEGIN
    SELECT run_status, prediction_finalized_at, prediction_contract_version
    INTO parent_run_status, parent_prediction_finalized_at, parent_contract_version
    FROM prediction_runs
    WHERE prediction_run_id = NEW.prediction_run_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'prediction_run_targets insert requires an existing prediction_runs parent row.';
    END IF;

    IF parent_contract_version <> 'predictive_agent_result_v1' THEN
        RAISE EXCEPTION
            'prediction_run_targets are valid only for predictive_agent_result_v1 runs.';
    END IF;

    IF parent_run_status <> 'RUNNING' OR parent_prediction_finalized_at IS NOT NULL THEN
        RAISE EXCEPTION
            'prediction_run_targets insert requires parent prediction_runs row to be RUNNING and unfinalized.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION expected_probability_representation(
    target_agent_code agent_code,
    target_contract_version TEXT,
    target_prediction_status prediction_status
)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
BEGIN
    IF target_prediction_status = 'ABSTAINED' THEN
        RETURN NULL;
    END IF;

    IF target_contract_version = 'legacy_prediction_ledger_v1' THEN
        RETURN 'legacy_persisted_win_probability_v1';
    END IF;

    IF target_contract_version = 'predictive_agent_result_v1' THEN
        CASE target_agent_code
            WHEN 'AGENT_1_SURFACE_ELO' THEN RETURN 'agent1_raw_probability_v1';
            WHEN 'AGENT_2_HIERARCHICAL_MARKOV' THEN RETURN 'agent2_raw_probability_v1';
            WHEN 'AGENT_3_INTRANSITIVITY' THEN RETURN 'agent3_raw_probability_v1';
        END CASE;
    END IF;

    RAISE EXCEPTION 'unsupported prediction contract version %', target_contract_version;
END;
$$;

CREATE OR REPLACE FUNCTION guard_prediction_parent_run_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    parent_run_status prediction_runs.run_status%TYPE;
    parent_prediction_finalized_at prediction_runs.prediction_finalized_at%TYPE;
    parent_contract_version prediction_runs.prediction_contract_version%TYPE;
    expected_representation TEXT;
BEGIN
    SELECT run_status, prediction_finalized_at, prediction_contract_version
    INTO parent_run_status, parent_prediction_finalized_at, parent_contract_version
    FROM prediction_runs
    WHERE prediction_run_id = NEW.prediction_run_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'predictions insert requires an existing prediction_runs parent row (prediction_run_id=%).',
            NEW.prediction_run_id;
    END IF;

    IF parent_run_status <> 'RUNNING' OR parent_prediction_finalized_at IS NOT NULL THEN
        RAISE EXCEPTION
            'predictions insert requires parent prediction_runs row to be RUNNING and unfinalized under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    expected_representation := expected_probability_representation(
        NEW.agent_code,
        parent_contract_version,
        NEW.prediction_status
    );

    IF parent_contract_version = 'legacy_prediction_ledger_v1'
       AND NEW.prediction_status = 'PREDICTED'
       AND NEW.probability_representation IS NULL THEN
        NEW.probability_representation := expected_representation;
    END IF;

    IF NEW.probability_representation IS DISTINCT FROM expected_representation THEN
        RAISE EXCEPTION
            'predictions probability_representation does not match agent, status, and prediction contract.';
    END IF;

    IF parent_contract_version = 'predictive_agent_result_v1' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM prediction_run_targets t
            WHERE t.prediction_run_id = NEW.prediction_run_id
              AND t.match_id = NEW.match_id
        ) THEN
            RAISE EXCEPTION
                'predictive_agent_result_v1 predictions must reference a prediction_run_targets row.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION populate_decision_agent_probability_representation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    linked_probability_representation TEXT;
BEGIN
    IF NEW.participation_status = 'ACTIVE'
       AND NEW.probability_representation IS NULL
       AND NEW.prediction_id IS NOT NULL THEN
        SELECT p.probability_representation
        INTO linked_probability_representation
        FROM predictions p
        WHERE p.prediction_id = NEW.prediction_id
          AND p.prediction_run_id = NEW.prediction_run_id
          AND p.match_id = NEW.match_id
          AND p.agent_code = NEW.agent_code;

        NEW.probability_representation := linked_probability_representation;
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_decision_agent_lineage_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    linked_prediction_status prediction_status;
    linked_selection_side SMALLINT;
    linked_win_probability NUMERIC(51, 50);
    linked_probability_representation TEXT;
BEGIN
    IF TG_TABLE_NAME = 'decision_agents' THEN
        IF NEW.participation_status = 'ACTIVE' THEN
            SELECT p.prediction_status, p.selection_side, p.win_probability, p.probability_representation
            INTO linked_prediction_status, linked_selection_side, linked_win_probability, linked_probability_representation
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

            IF NEW.probability_representation IS DISTINCT FROM linked_probability_representation THEN
                RAISE EXCEPTION
                    'decision_agents ACTIVE probability_representation must equal predictions.probability_representation.';
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

            IF NEW.proposed_selection_side IS NOT NULL
               OR NEW.proposed_probability IS NOT NULL
               OR NEW.probability_representation IS NOT NULL THEN
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
                  OR da.probability_representation IS DISTINCT FROM NEW.probability_representation
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

CREATE TRIGGER trg_prediction_runs_lifecycle_guard
BEFORE UPDATE ON prediction_runs
FOR EACH ROW
EXECUTE FUNCTION validate_prediction_run_lifecycle();

CREATE TRIGGER trg_prediction_run_targets_parent_run_lock_guard
BEFORE INSERT ON prediction_run_targets
FOR EACH ROW
EXECUTE FUNCTION validate_prediction_run_target_insert();

CREATE TRIGGER trg_prediction_run_targets_append_only_mutation
BEFORE UPDATE OR DELETE ON prediction_run_targets
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_predictions_parent_run_lock_guard
BEFORE INSERT ON predictions
FOR EACH ROW
EXECUTE FUNCTION guard_prediction_parent_run_insert();

CREATE TRIGGER trg_predictions_append_only_mutation
BEFORE UPDATE OR DELETE ON predictions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_decision_agents_probability_representation_default
BEFORE INSERT ON decision_agents
FOR EACH ROW
EXECUTE FUNCTION populate_decision_agent_probability_representation();

CREATE CONSTRAINT TRIGGER trg_decision_agents_lineage_integrity
AFTER INSERT OR UPDATE ON decision_agents
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION validate_decision_agent_lineage_integrity();

CREATE CONSTRAINT TRIGGER trg_predictions_decision_agent_lineage_integrity
AFTER UPDATE OF prediction_status, selection_side, win_probability, probability_representation ON predictions
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION validate_decision_agent_lineage_integrity();

CREATE TRIGGER trg_decision_agents_append_only_mutation
BEFORE UPDATE OR DELETE ON decision_agents
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
