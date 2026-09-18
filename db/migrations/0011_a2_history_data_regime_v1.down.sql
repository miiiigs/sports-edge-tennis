BEGIN;

DROP TRIGGER IF EXISTS trg_a2_history_data_regimes_append_only_mutation
    ON a2_history_data_regimes;
DROP TABLE IF EXISTS a2_history_data_regimes;

COMMIT;
