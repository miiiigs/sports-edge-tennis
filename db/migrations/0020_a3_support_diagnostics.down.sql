BEGIN;

DROP TRIGGER IF EXISTS trg_a3_support_diagnostics_append_only_mutation
    ON a3_support_diagnostics;

DROP TABLE IF EXISTS a3_support_diagnostics;

COMMIT;
