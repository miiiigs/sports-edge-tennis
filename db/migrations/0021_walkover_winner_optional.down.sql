BEGIN;

-- A null-winner WALKOVER is legitimate after 0021 and must never be deleted,
-- rewritten with an invented winner, or silently invalidated by schema rollback.
-- Fail BEFORE dropping the new constraint. PostgreSQL rolls back the transaction,
-- leaving both the records and the forward schema intact.
DO $
BEGIN
    IF EXISTS (
        SELECT 1
        FROM match_result_observations
        WHERE outcome_status = 'WALKOVER'
          AND winner_side IS NULL
    ) THEN
        RAISE EXCEPTION
            'Cannot reverse migration 0021: null-winner WALKOVER results exist. '
            'Keep the forward schema; verify application rollback compatibility '
            'or prepare a separately approved data-preserving recovery plan.'
            USING ERRCODE = 'P0001';
    END IF;
END;
$;

ALTER TABLE match_result_observations
    DROP CONSTRAINT IF EXISTS chk_match_result_terminal_winner;

ALTER TABLE match_result_observations
    ADD CONSTRAINT chk_match_result_terminal_winner_required
    CHECK (
        (outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER') AND winner_side IS NOT NULL)
        OR (outcome_status = 'CANCELLED' AND winner_side IS NULL)
    );

COMMIT;
