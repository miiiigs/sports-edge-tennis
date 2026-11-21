-- Canonicalize in-match default/disqualification separately from retirement/walkover.
-- Existing settlement policy treats abnormal finishes as provider-neutral VOID, but
-- sporting identity keeps the declared winner for audit.

BEGIN;

ALTER TABLE match_result_observations
    DROP CONSTRAINT IF EXISTS chk_match_result_terminal_winner;

-- Future non-cancelled terminal enum values require a declared winner by default.
-- WALKOVER remains the sole legitimate winnerless terminal result.
ALTER TABLE match_result_observations
    ADD CONSTRAINT chk_match_result_terminal_winner
    CHECK (
        (outcome_status = 'CANCELLED' AND winner_side IS NULL)
        OR outcome_status = 'WALKOVER'
        OR (
            outcome_status NOT IN ('CANCELLED', 'WALKOVER')
            AND winner_side IS NOT NULL
        )
    );

COMMIT;

ALTER TYPE result_status ADD VALUE IF NOT EXISTS 'DEFAULTED';
ALTER TYPE match_status ADD VALUE IF NOT EXISTS 'DEFAULTED';
