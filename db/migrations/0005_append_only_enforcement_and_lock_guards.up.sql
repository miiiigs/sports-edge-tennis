BEGIN;

ALTER TABLE predictions
    DROP CONSTRAINT fk_predictions_prediction_run_finalization;

ALTER TABLE predictions
    ADD CONSTRAINT fk_predictions_prediction_run_finalization
        FOREIGN KEY (prediction_run_id, prediction_locked_at)
        REFERENCES prediction_runs(prediction_run_id, prediction_finalized_at)
        DEFERRABLE INITIALLY IMMEDIATE;

CREATE OR REPLACE FUNCTION raise_append_only_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION USING
        MESSAGE = format(
            '%I is append-only under 0005_append_only_enforcement_and_lock_guards; %s is not allowed.',
            TG_TABLE_NAME,
            TG_OP
        );
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

CREATE OR REPLACE FUNCTION validate_ingest_run_lifecycle()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY[
            'finished_at',
            'status',
            'rows_seen',
            'rows_inserted',
            'rows_updated',
            'rows_rejected',
            'rows_unmatched',
            'error_summary'
        ])
        IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY[
            'finished_at',
            'status',
            'rows_seen',
            'rows_inserted',
            'rows_updated',
            'rows_rejected',
            'rows_unmatched',
            'error_summary'
        ]) THEN
        RAISE EXCEPTION
            'ingest_runs source identity fields are immutable under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    IF OLD.status <> 'RUNNING' THEN
        RAISE EXCEPTION
            'ingest_runs terminal rows are immutable under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    IF NEW.status NOT IN ('COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED') THEN
        RAISE EXCEPTION
            'ingest_runs only allows one terminal RUNNING -> COMPLETED/PARTIAL/FAILED/CANCELLED update under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    IF OLD.finished_at IS NOT NULL OR NEW.finished_at IS NULL THEN
        RAISE EXCEPTION
            'ingest_runs terminalization requires finished_at NULL -> timestamp in the same update.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_decision_publication_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - 'published_at') IS DISTINCT FROM (to_jsonb(OLD) - 'published_at') THEN
        RAISE EXCEPTION
            'decisions rows are immutable under 0005_append_only_enforcement_and_lock_guards except for one-way published_at publication.';
    END IF;

    IF OLD.published_at IS NOT NULL THEN
        RAISE EXCEPTION
            'decisions.published_at may only transition from NULL to a timestamp once.';
    END IF;

    IF NEW.published_at IS NULL THEN
        RAISE EXCEPTION
            'decisions UPDATE is allowed only for published_at NULL -> timestamp.';
    END IF;

    RETURN NEW;
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

CREATE TRIGGER trg_prediction_runs_lifecycle_guard
BEFORE UPDATE ON prediction_runs
FOR EACH ROW
EXECUTE FUNCTION validate_prediction_run_lifecycle();

CREATE TRIGGER trg_prediction_runs_delete_blocker
BEFORE DELETE ON prediction_runs
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

CREATE TRIGGER trg_decision_policy_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON decision_policy_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_agent_config_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON agent_config_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_decisions_publication_guard
BEFORE UPDATE ON decisions
FOR EACH ROW
EXECUTE FUNCTION validate_decision_publication_update();

CREATE TRIGGER trg_decisions_delete_blocker
BEFORE DELETE ON decisions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_decision_agents_append_only_mutation
BEFORE UPDATE OR DELETE ON decision_agents
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_rating_snapshots_append_only_mutation
BEFORE UPDATE OR DELETE ON rating_snapshots
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_odds_snapshots_append_only_mutation
BEFORE UPDATE OR DELETE ON odds_snapshots
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_match_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON match_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_match_source_ids_append_only_mutation
BEFORE UPDATE OR DELETE ON match_source_ids
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_match_version_sources_append_only_mutation
BEFORE UPDATE OR DELETE ON match_version_sources
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_match_result_observations_append_only_mutation
BEFORE UPDATE OR DELETE ON match_result_observations
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_match_stat_observations_append_only_mutation
BEFORE UPDATE OR DELETE ON match_stat_observations
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_player_source_ids_append_only_mutation
BEFORE UPDATE OR DELETE ON player_source_ids
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_identity_resolution_events_append_only_mutation
BEFORE UPDATE OR DELETE ON identity_resolution_events
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_identity_resolution_candidates_append_only_mutation
BEFORE UPDATE OR DELETE ON identity_resolution_candidates
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_tournament_source_ids_append_only_mutation
BEFORE UPDATE OR DELETE ON tournament_source_ids
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_matches_append_only_mutation
BEFORE UPDATE OR DELETE ON matches
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_data_health_checks_append_only_mutation
BEFORE UPDATE OR DELETE ON data_health_checks
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_ingest_runs_lifecycle_guard
BEFORE UPDATE ON ingest_runs
FOR EACH ROW
EXECUTE FUNCTION validate_ingest_run_lifecycle();

CREATE TRIGGER trg_ingest_runs_delete_blocker
BEFORE DELETE ON ingest_runs
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
