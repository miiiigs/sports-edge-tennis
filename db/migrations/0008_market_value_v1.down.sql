BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM decisions WHERE manager_consensus_result_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            '0008_market_value_v1.down.sql cannot roll back persisted Market decisions.';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS trg_market_decisions_eligibility_guard ON decisions;
DROP TRIGGER IF EXISTS trg_market_policy_versions_append_only_mutation ON market_policy_versions;
DROP TRIGGER IF EXISTS trg_decision_odds_snapshots_append_only_mutation ON decision_odds_snapshots;

DROP FUNCTION IF EXISTS guard_market_decision_insert();

DROP TABLE IF EXISTS decision_odds_snapshots;

ALTER TABLE decisions
    DROP CONSTRAINT IF EXISTS uq_decisions_market_idempotency,
    DROP CONSTRAINT IF EXISTS chk_decisions_market_links_coherent,
    DROP CONSTRAINT IF EXISTS fk_decisions_market_policy_version,
    DROP CONSTRAINT IF EXISTS fk_decisions_manager_consensus_result,
    DROP COLUMN IF EXISTS snapshot_lineage_hash,
    DROP COLUMN IF EXISTS market_policy_version_id,
    DROP COLUMN IF EXISTS manager_consensus_result_id;

ALTER TABLE decisions
    ALTER COLUMN decision_policy_version_id SET NOT NULL;

DROP TABLE IF EXISTS market_policy_versions;

DROP TYPE IF EXISTS market_snapshot_role;

ALTER TABLE decisions
    ALTER COLUMN reason_code DROP DEFAULT;

ALTER TABLE decisions
    ALTER COLUMN reason_code TYPE TEXT
    USING reason_code::text;

ALTER TYPE decision_reason_code RENAME TO decision_reason_code_market_value_v1;

CREATE TYPE decision_reason_code AS ENUM (
    'NONE',
    'CONSENSUS_FAILURE',
    'PRICE_FAILURE',
    'STALE_DATA',
    'ABSTENTION_RELATED',
    'DIRECTIONAL_DISAGREEMENT',
    'PROBABILITY_SPREAD',
    'PROBABILITY_FLOOR',
    'DATA_SUFFICIENCY',
    'VETO_TRIGGERED'
);

ALTER TABLE decisions
    ALTER COLUMN reason_code TYPE decision_reason_code
    USING reason_code::decision_reason_code;

ALTER TABLE decisions
    ALTER COLUMN reason_code SET DEFAULT 'NONE';

DROP TYPE decision_reason_code_market_value_v1;

COMMIT;
