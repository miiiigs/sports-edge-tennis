BEGIN;

-- Evidence-preserving guard: a schema rollback must never destroy the
-- append-only history. Export/archive it first, then truncate or drop it
-- deliberately. Empty tables still roll back for clean-room tests.
DO $$
BEGIN
    IF to_regclass('public.oddspapi_event_identity_history') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM oddspapi_event_identity_history) THEN
            RAISE EXCEPTION
                'refusing to drop oddspapi_event_identity_history: evidence rows present; export or archive first';
        END IF;
    END IF;
END
$$;

DROP TABLE IF EXISTS oddspapi_event_identity_history;

COMMIT;
