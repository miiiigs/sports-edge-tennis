BEGIN;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM forward_originals) OR EXISTS (SELECT 1 FROM forward_experiment_freezes)
       OR EXISTS (SELECT 1 FROM forward_delivery_events)
       OR EXISTS (SELECT 1 FROM forward_settlement_events)
       OR EXISTS (SELECT 1 FROM forward_fixture_expectations) THEN
        RAISE EXCEPTION 'refusing to drop collected forward evidence';
    END IF;
END $$;
DROP TABLE forward_fixture_expectations;
DROP TABLE forward_settlement_events;
DROP TABLE forward_delivery_events;
DROP TABLE forward_originals;
DROP TABLE forward_experiment_freezes;
COMMIT;
