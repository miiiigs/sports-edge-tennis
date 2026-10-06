BEGIN;

DROP TRIGGER IF EXISTS trg_tournament_identity_corrections_append_only_mutation
    ON tournament_identity_corrections;

DROP TABLE IF EXISTS tournament_identity_corrections;

COMMIT;
