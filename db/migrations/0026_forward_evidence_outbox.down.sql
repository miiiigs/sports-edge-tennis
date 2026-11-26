BEGIN;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM forward_originals) OR EXISTS (SELECT 1 FROM forward_experiment_freezes)
       OR EXISTS (SELECT 1 FROM forward_delivery_events) THEN
        RAISE EXCEPTION 'refusing to drop collected forward evidence';
    END IF;
END $$;
DROP TABLE forward_delivery_events;
DROP TABLE forward_originals;
DROP TABLE forward_experiment_freezes;
COMMIT;
