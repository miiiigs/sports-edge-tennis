BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM manager_consensus_results mcr
        JOIN manager_policy_versions mpv
          ON mpv.manager_policy_version_id = mcr.manager_policy_version_id
        WHERE mpv.policy_key = 'tennis_manager_consensus'
          AND mpv.policy_version = 'v2'
    ) THEN
        RAISE EXCEPTION
            'cannot roll back strict Manager v2 after it has been referenced by Manager results';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS trg_manager_policy_versions_append_only_mutation
    ON manager_policy_versions;

DELETE FROM manager_policy_versions
WHERE policy_key = 'tennis_manager_consensus'
  AND policy_version = 'v2';

CREATE TRIGGER trg_manager_policy_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON manager_policy_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

ALTER TABLE manager_policy_versions
    DROP CONSTRAINT IF EXISTS chk_manager_policy_versions_allow_agent3_abstention;

ALTER TABLE manager_policy_versions
    ADD CONSTRAINT chk_manager_policy_versions_allow_agent3_abstention
    CHECK (allow_agent3_abstention = TRUE);

COMMIT;
