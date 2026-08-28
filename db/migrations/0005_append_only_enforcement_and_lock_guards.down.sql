BEGIN;

DROP TRIGGER IF EXISTS trg_ingest_runs_delete_blocker ON ingest_runs;
DROP TRIGGER IF EXISTS trg_ingest_runs_lifecycle_guard ON ingest_runs;
DROP TRIGGER IF EXISTS trg_data_health_checks_append_only_mutation ON data_health_checks;
DROP TRIGGER IF EXISTS trg_matches_append_only_mutation ON matches;
DROP TRIGGER IF EXISTS trg_tournament_source_ids_append_only_mutation ON tournament_source_ids;
DROP TRIGGER IF EXISTS trg_identity_resolution_candidates_append_only_mutation ON identity_resolution_candidates;
DROP TRIGGER IF EXISTS trg_identity_resolution_events_append_only_mutation ON identity_resolution_events;
DROP TRIGGER IF EXISTS trg_player_source_ids_append_only_mutation ON player_source_ids;
DROP TRIGGER IF EXISTS trg_match_stat_observations_append_only_mutation ON match_stat_observations;
DROP TRIGGER IF EXISTS trg_match_result_observations_append_only_mutation ON match_result_observations;
DROP TRIGGER IF EXISTS trg_match_version_sources_append_only_mutation ON match_version_sources;
DROP TRIGGER IF EXISTS trg_match_source_ids_append_only_mutation ON match_source_ids;
DROP TRIGGER IF EXISTS trg_match_versions_append_only_mutation ON match_versions;
DROP TRIGGER IF EXISTS trg_odds_snapshots_append_only_mutation ON odds_snapshots;
DROP TRIGGER IF EXISTS trg_rating_snapshots_append_only_mutation ON rating_snapshots;
DROP TRIGGER IF EXISTS trg_decision_agents_append_only_mutation ON decision_agents;
DROP TRIGGER IF EXISTS trg_decisions_delete_blocker ON decisions;
DROP TRIGGER IF EXISTS trg_decisions_publication_guard ON decisions;
DROP TRIGGER IF EXISTS trg_agent_config_versions_append_only_mutation ON agent_config_versions;
DROP TRIGGER IF EXISTS trg_decision_policy_versions_append_only_mutation ON decision_policy_versions;
DROP TRIGGER IF EXISTS trg_predictions_append_only_mutation ON predictions;
DROP TRIGGER IF EXISTS trg_predictions_parent_run_lock_guard ON predictions;
DROP TRIGGER IF EXISTS trg_prediction_runs_delete_blocker ON prediction_runs;
DROP TRIGGER IF EXISTS trg_prediction_runs_lifecycle_guard ON prediction_runs;

DROP FUNCTION IF EXISTS guard_prediction_parent_run_insert();
DROP FUNCTION IF EXISTS validate_decision_publication_update();
DROP FUNCTION IF EXISTS validate_ingest_run_lifecycle();
DROP FUNCTION IF EXISTS validate_prediction_run_lifecycle();
DROP FUNCTION IF EXISTS raise_append_only_mutation();

ALTER TABLE predictions
    DROP CONSTRAINT fk_predictions_prediction_run_finalization;

ALTER TABLE predictions
    ADD CONSTRAINT fk_predictions_prediction_run_finalization
        FOREIGN KEY (prediction_run_id, prediction_locked_at)
        REFERENCES prediction_runs(prediction_run_id, prediction_finalized_at);

COMMIT;
