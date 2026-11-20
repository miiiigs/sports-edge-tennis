BEGIN;

-- A provider may correctly report WALKOVER without publishing a winner.
-- Settlement policy maps WALKOVER to VOID and does not need winner_side.
-- Relax only WALKOVER; COMPLETED and RETIRED still require a winner.
DO $$
DECLARE
    constraint_row RECORD;
BEGIN
    FOR constraint_row IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'match_result_observations'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%WALKOVER%'
          AND pg_get_constraintdef(oid) ILIKE '%winner_side IS NOT NULL%'
    LOOP
        EXECUTE format(
            'ALTER TABLE match_result_observations DROP CONSTRAINT %I',
            constraint_row.conname
        );
    END LOOP;
END;
$$;

ALTER TABLE match_result_observations
    ADD CONSTRAINT chk_match_result_terminal_winner
    CHECK (
        (outcome_status IN ('COMPLETED', 'RETIRED') AND winner_side IS NOT NULL)
        OR outcome_status = 'WALKOVER'
        OR (outcome_status = 'CANCELLED' AND winner_side IS NULL)
    );

COMMIT;
