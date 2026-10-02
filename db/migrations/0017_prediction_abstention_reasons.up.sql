BEGIN;

-- Append-only abstention observability: preserve the agent-level reason that
-- already exists in the predictive-result contract but was previously dropped at
-- persistence. Nullable so historical rows remain valid without any backfill.
ALTER TABLE predictions
    ADD COLUMN abstain_reason_code TEXT;

-- Coherence: a predicted row can never carry an abstention reason. An abstained
-- row may carry a NULL reason only when it predates 0017 (historical rows). New
-- abstained rows are required to persist the reason by the application layer and
-- the canonical-vocabulary constraint below.
ALTER TABLE predictions
    ADD CONSTRAINT chk_predictions_abstain_reason_status
        CHECK (
            (prediction_status = 'PREDICTED' AND abstain_reason_code IS NULL)
            OR (prediction_status = 'ABSTAINED')
        ),
    ADD CONSTRAINT chk_predictions_abstain_reason_code
        CHECK (
            abstain_reason_code IS NULL
            OR abstain_reason_code IN (
                'CONFLICTING_DIRECT_NETWORK',
                'INSUFFICIENT_HISTORY',
                'INSUFFICIENT_MATCHUP_SUPPORT',
                'INSUFFICIENT_POINT_HISTORY',
                'INVALID_STAT_ROW',
                'ONE_SIDED_HISTORY',
                'PIT_STATE_UNRESOLVED',
                'UNKNOWN_SURFACE',
                'UNSUPPORTED_OR_UNKNOWN',
                'UNSUPPORTED_SCORING',
                'WEAK_SIGNAL'
            )
        );

CREATE INDEX idx_predictions_agent_status_abstain_reason
    ON predictions (agent_code, prediction_status, abstain_reason_code);

COMMIT;
