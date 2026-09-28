BEGIN;

DROP TRIGGER IF EXISTS trg_cross_provider_match_reconciliations_append_only_mutation
    ON cross_provider_match_reconciliations;

DROP TABLE IF EXISTS cross_provider_match_reconciliations;

COMMIT;
