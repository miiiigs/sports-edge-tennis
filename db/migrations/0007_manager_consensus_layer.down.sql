BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM manager_consensus_results) THEN
        RAISE EXCEPTION
            '0007_manager_consensus_layer.down.sql cannot roll back persisted Manager results.';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS trg_manager_consensus_results_eligibility_guard ON manager_consensus_results;
DROP TRIGGER IF EXISTS trg_manager_consensus_results_append_only_mutation ON manager_consensus_results;
DROP TRIGGER IF EXISTS trg_manager_policy_versions_append_only_mutation ON manager_policy_versions;

DROP FUNCTION IF EXISTS guard_manager_consensus_result_insert();

DROP TABLE IF EXISTS manager_consensus_results;
DROP TABLE IF EXISTS manager_policy_versions;

DROP TYPE IF EXISTS manager_reason_code;
DROP TYPE IF EXISTS manager_consensus_status;

COMMIT;
