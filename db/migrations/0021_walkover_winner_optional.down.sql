BEGIN;

ALTER TABLE match_result_observations
    DROP CONSTRAINT IF EXISTS chk_match_result_terminal_winner;

ALTER TABLE match_result_observations
    ADD CONSTRAINT chk_match_result_terminal_winner_required
    CHECK (
        (outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER') AND winner_side IS NOT NULL)
        OR (outcome_status = 'CANCELLED' AND winner_side IS NULL)
    );

COMMIT;
