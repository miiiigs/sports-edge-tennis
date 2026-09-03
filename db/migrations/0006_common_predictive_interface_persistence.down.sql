BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM prediction_runs
        WHERE prediction_contract_version = 'predictive_agent_result_v1'
    ) THEN
        RAISE EXCEPTION
            '0006_common_predictive_interface_persistence.down.sql cannot roll back predictive_agent_result_v1 runs.';
    END IF;

    IF EXISTS (SELECT 1 FROM prediction_run_targets) THEN
        RAISE EXCEPTION
            '0006_common_predictive_interface_persistence.down.sql cannot roll back persisted prediction_run_targets.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM predictions
        WHERE win_probability IS NOT NULL
          AND win_probability <> round(win_probability, 6)
    ) THEN
        RAISE EXCEPTION
            '0006_common_predictive_interface_persistence.down.sql cannot narrow high-precision prediction probabilities.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM decision_agents
        WHERE proposed_probability IS NOT NULL
          AND proposed_probability <> round(proposed_probability, 6)
    ) THEN
        RAISE EXCEPTION
            '0006_common_predictive_interface_persistence.down.sql cannot narrow high-precision decision-agent probabilities.';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS trg_predictions_decision_agent_lineage_integrity ON predictions;
DROP TRIGGER IF EXISTS trg_decision_agents_lineage_integrity ON decision_agents;
DROP TRIGGER IF EXISTS trg_decision_agents_probability_representation_default ON decision_agents;
DROP TRIGGER IF EXISTS trg_predictions_parent_run_lock_guard ON predictions;
DROP TRIGGER IF EXISTS trg_prediction_run_targets_append_only_mutation ON prediction_run_targets;
DROP TRIGGER IF EXISTS trg_prediction_run_targets_parent_run_lock_guard ON prediction_run_targets;
DROP TRIGGER IF EXISTS trg_prediction_runs_lifecycle_guard ON prediction_runs;

DROP FUNCTION IF EXISTS validate_decision_agent_lineage_integrity();
DROP FUNCTION IF EXISTS populate_decision_agent_probability_representation();
DROP FUNCTION IF EXISTS guard_prediction_parent_run_insert();
DROP FUNCTION IF EXISTS expected_probability_representation(agent_code, TEXT, prediction_status);
DROP FUNCTION IF EXISTS validate_prediction_run_target_insert();
DROP FUNCTION IF EXISTS validate_prediction_run_v1_completeness(BIGINT);
DROP FUNCTION IF EXISTS validate_prediction_run_lifecycle();

DROP INDEX IF EXISTS idx_decision_agents_run_representation;
DROP INDEX IF EXISTS idx_predictions_run_representation;
DROP INDEX IF EXISTS idx_prediction_run_targets_match;

DROP TABLE IF EXISTS prediction_run_targets;

ALTER TABLE decision_agents
    DROP CONSTRAINT IF EXISTS chk_decision_agents_participation_nullability,
    DROP CONSTRAINT IF EXISTS chk_decision_agents_live_probability_representation,
    DROP CONSTRAINT IF EXISTS chk_decision_agents_probability_representation,
    DROP COLUMN IF EXISTS probability_representation,
    ALTER COLUMN proposed_probability TYPE NUMERIC(8, 6),
    ADD CONSTRAINT chk_decision_agents_participation_nullability
        CHECK (
            (participation_status = 'ACTIVE' AND proposed_selection_side IS NOT NULL AND proposed_probability IS NOT NULL)
            OR (participation_status = 'ABSTAINED' AND proposed_selection_side IS NULL AND proposed_probability IS NULL)
            OR (participation_status = 'INACTIVE')
        );

ALTER TABLE predictions
    DROP CONSTRAINT IF EXISTS chk_predictions_live_probability_representation,
    DROP CONSTRAINT IF EXISTS chk_predictions_probability_representation,
    DROP COLUMN IF EXISTS probability_representation,
    ALTER COLUMN win_probability TYPE NUMERIC(8, 6);

ALTER TABLE prediction_runs
    DROP CONSTRAINT IF EXISTS chk_prediction_runs_contract_version,
    DROP COLUMN IF EXISTS prediction_contract_version;

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

CREATE OR REPLACE FUNCTION guard_prediction_parent_run_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    parent_run_status prediction_runs.run_status%TYPE;
    parent_prediction_finalized_at prediction_runs.prediction_finalized_at%TYPE;
BEGIN
    SELECT run_status, prediction_finalized_at
    INTO parent_run_status, parent_prediction_finalized_at
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

CREATE TRIGGER trg_prediction_runs_lifecycle_guard
BEFORE UPDATE ON prediction_runs
FOR EACH ROW
EXECUTE FUNCTION validate_prediction_run_lifecycle();

CREATE TRIGGER trg_predictions_parent_run_lock_guard
BEFORE INSERT ON predictions
FOR EACH ROW
EXECUTE FUNCTION guard_prediction_parent_run_insert();

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
