BEGIN;

DROP INDEX IF EXISTS idx_predictions_agent_status_abstain_reason;

ALTER TABLE predictions
    DROP CONSTRAINT IF EXISTS chk_predictions_abstain_reason_code,
    DROP CONSTRAINT IF EXISTS chk_predictions_abstain_reason_status,
    DROP COLUMN IF EXISTS abstain_reason_code;

COMMIT;
