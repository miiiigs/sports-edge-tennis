BEGIN;

DELETE FROM manager_policy_versions
WHERE policy_key = 'tennis_manager_consensus'
  AND policy_version = 'v2';

ALTER TABLE manager_policy_versions
    DROP CONSTRAINT IF EXISTS chk_manager_policy_versions_allow_agent3_abstention;

ALTER TABLE manager_policy_versions
    ADD CONSTRAINT chk_manager_policy_versions_allow_agent3_abstention
    CHECK (allow_agent3_abstention = TRUE);

COMMIT;
