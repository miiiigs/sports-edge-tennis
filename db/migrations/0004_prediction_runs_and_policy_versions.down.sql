BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM predictions
        UNION ALL
        SELECT 1 FROM decisions
        UNION ALL
        SELECT 1 FROM decision_agents
        UNION ALL
        SELECT 1 FROM prediction_runs
        UNION ALL
        SELECT 1 FROM decision_policy_versions
        UNION ALL
        SELECT 1 FROM agent_config_versions
    ) THEN
        RAISE EXCEPTION
            '0004_prediction_runs_and_policy_versions.down.sql requires T-ED1 lineage tables and columns to be empty before rollback because rollback would destroy historical run, policy, config, or decision lineage.';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS trg_predictions_decision_agent_lineage_integrity ON predictions;
DROP TRIGGER IF EXISTS trg_decision_agents_lineage_integrity ON decision_agents;
DROP FUNCTION IF EXISTS validate_decision_agent_lineage_integrity();

ALTER TABLE decision_agents
    DROP CONSTRAINT IF EXISTS fk_decision_agents_prediction_lineage,
    DROP CONSTRAINT IF EXISTS fk_decision_agents_decision_lineage,
    DROP CONSTRAINT IF EXISTS chk_decision_agents_participation_nullability,
    DROP COLUMN IF EXISTS prediction_id,
    DROP COLUMN IF EXISTS match_id,
    DROP COLUMN IF EXISTS prediction_run_id;

ALTER TABLE decisions
    DROP CONSTRAINT IF EXISTS uq_decisions_lineage_identity,
    DROP CONSTRAINT IF EXISTS fk_decisions_policy_version,
    DROP CONSTRAINT IF EXISTS fk_decisions_prediction_run,
    DROP COLUMN IF EXISTS decision_policy_version_id,
    DROP COLUMN IF EXISTS prediction_run_id;

ALTER TABLE predictions
    DROP CONSTRAINT IF EXISTS uq_predictions_lineage_identity,
    DROP CONSTRAINT IF EXISTS uq_predictions_run_match_agent,
    DROP CONSTRAINT IF EXISTS fk_predictions_agent_config_lineage,
    DROP CONSTRAINT IF EXISTS fk_predictions_prediction_run_finalization,
    DROP CONSTRAINT IF EXISTS fk_predictions_prediction_run_as_of,
    DROP CONSTRAINT IF EXISTS fk_predictions_prediction_run,
    DROP COLUMN IF EXISTS agent_config_version_id,
    DROP COLUMN IF EXISTS prediction_run_id;

DROP INDEX IF EXISTS idx_agent_config_versions_agent_created_at;
DROP TABLE IF EXISTS agent_config_versions;

DROP TABLE IF EXISTS decision_policy_versions;

DROP INDEX IF EXISTS idx_prediction_runs_parent_prediction_run_id;
DROP INDEX IF EXISTS idx_prediction_runs_status_started_at;
DROP TABLE IF EXISTS prediction_runs;

CREATE UNIQUE INDEX uq_predictions_identity
    ON predictions (
        match_id,
        agent_code,
        model_version,
        feature_version,
        COALESCE(experiment_id, ''),
        COALESCE(walk_forward_fold, -1),
        COALESCE(variant_id, ''),
        as_of
    );

CREATE UNIQUE INDEX uq_decisions_identity
    ON decisions (
        match_id,
        signal_type,
        model_version,
        feature_version,
        COALESCE(experiment_id, ''),
        COALESCE(walk_forward_fold, -1),
        COALESCE(variant_id, ''),
        as_of
    );

COMMIT;
