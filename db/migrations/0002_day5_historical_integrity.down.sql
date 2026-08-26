BEGIN;

ALTER TABLE tournaments
    ALTER COLUMN competition_tier TYPE TEXT
    USING competition_tier::text;

ALTER TABLE match_versions
    ALTER COLUMN competition_tier TYPE TEXT
    USING competition_tier::text;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM tournaments
        WHERE competition_tier = 'ATP_TOUR'
    ) OR EXISTS (
        SELECT 1
        FROM match_versions
        WHERE competition_tier = 'ATP_TOUR'
    ) THEN
        RAISE EXCEPTION
            '0002_day5_historical_integrity.down.sql requires ATP_TOUR rows to be removed before rollback.';
    END IF;
END;
$$;

ALTER TYPE competition_tier RENAME TO competition_tier_day5_historical_integrity;

CREATE TYPE competition_tier AS ENUM (
    'GRAND_SLAM',
    'MASTERS_1000',
    'ATP_500',
    'ATP_250',
    'CHALLENGER'
);

ALTER TABLE tournaments
    ALTER COLUMN competition_tier TYPE competition_tier
    USING competition_tier::competition_tier;

ALTER TABLE match_versions
    ALTER COLUMN competition_tier TYPE competition_tier
    USING competition_tier::competition_tier;

DROP TYPE competition_tier_day5_historical_integrity;

COMMIT;
