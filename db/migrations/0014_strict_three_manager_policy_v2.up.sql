BEGIN;

ALTER TABLE manager_policy_versions
    DROP CONSTRAINT IF EXISTS chk_manager_policy_versions_allow_agent3_abstention;

ALTER TABLE manager_policy_versions
    ADD CONSTRAINT chk_manager_policy_versions_allow_agent3_abstention
    CHECK (
        (policy_version = 'v1' AND allow_agent3_abstention = TRUE)
        OR (policy_version <> 'v1' AND allow_agent3_abstention IN (TRUE, FALSE))
    );

INSERT INTO manager_policy_versions (
    policy_key,
    policy_version,
    consensus_mode,
    allow_agent3_abstention,
    three_agent_max_probability_spread,
    agent3_abstention_max_probability_spread,
    min_active_agent_probability
)
VALUES (
    'tennis_manager_consensus',
    'v2',
    'UNANIMOUS_ACTIVE_AGENTS',
    FALSE,
    0.080000,
    0.000000,
    0.500000
)
ON CONFLICT (policy_key, policy_version) DO NOTHING;

COMMIT;
