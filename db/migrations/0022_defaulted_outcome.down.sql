-- PostgreSQL enum value removal requires type recreation and dependent-column rewrites.
-- This migration is deliberately fail-closed rather than pretending to roll back
-- DEFAULTED into RETIRED/WALKOVER and corrupting sporting semantics.
DO $$
BEGIN
    RAISE EXCEPTION
        'Migration 0022 is forward-only: DEFAULTED is a distinct canonical sporting outcome. '
        'Use an explicitly reviewed enum-rebuild migration if a rollback is ever required.'
        USING ERRCODE = 'P0001';
END;
$$;
