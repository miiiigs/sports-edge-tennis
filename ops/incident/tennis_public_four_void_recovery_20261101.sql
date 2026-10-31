-- 2026-11-01: Four source-verified late VOID outcomes. Never backdate
-- source observation, never force a winner, never rerun once committed.
-- Protected fresh production PostgreSQL custom dump, separate backup volume:
-- /backup/tennis-pre-public-5-cases-20261101.dump
-- sha256: 942b6228fba843edc7f0bdba76c59225cccf4229f1fb6d35f232ed06f367b168
-- 117860 Jaume Munar / Gonzalo Bueno, Seville, 2026-09-09 WALKOVER
-- https://www.tennis.com/tournaments/lx-copa-sevilla/matches/j-munar-vs-g-bueno-2026-09-09
-- https://www.365scores.com/tennis/match/seville-6909/gonzalo-bueno-jaume-munar-24651-69493-6909
-- 131636 Gerard Campana Lee / Pedro Vives Marcos Palermo qualifying 2026-10-05 WALKOVER
-- https://www.tennis.com/tournaments/atp-challenger-palermo-italy?date=2026-10-05
-- https://www.sofascore.com/tennis/match/gerard-campana-lee-pedro-vives-marcos/mEKbsUacd
-- 131307 Rigele Te / James Duckworth Beijing qualifying 2026-09-28 CANCELLED.
-- Preserved API-Tennis fixture is tagged COMPLETED but *has no winner*;
-- this is late independent cancellation evidence, not an inferred finish.
-- https://www.tennis.com/tournaments/china-open-atp/matches/r-te-vs-j-duckworth-2026-09-28
-- https://playsift.com/scores/tennis?date=2026-09-28
-- 131446 Alex Molcan / Arthur Rinderknech Beijing R1 2026-10-01 CANCELLED.
-- API-Tennis fixture tagged COMPLETED without winner. Different match
-- Molcan–Machac actually played; do not confuse results.
-- https://www.tennis.com/tournaments/china-open-atp/matches/a-molcan-vs-a-rinderknech-2026-10-01
-- https://www.sofascore.com/tennis/match/alex-molcan-arthur-rinderknech/gxAsFcib
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '90s';
SELECT pg_advisory_xact_lock(20261101, 11);
CREATE TEMP TABLE _public_voids ON COMMIT DROP AS
WITH expected(match_id,source,event_id,p1,p2,day,fixture_status,result_status) AS (
 VALUES
 (117860,'API_TENNIS','12161828','J. Munar','G. Bueno',DATE '2026-09-09','SCHEDULED','WALKOVER'),
 (131636,'API_TENNIS','12168173','G. Campana Lee','P. Vives Marcos',DATE '2026-10-05','SCHEDULED','WALKOVER'),
 (131307,'API_TENNIS','12166372','R. Te','J. Duckworth',DATE '2026-09-28','COMPLETED','CANCELLED'),
 (131446,'API_TENNIS','12166954','A. Molcan','A. Rinderknech',DATE '2026-10-01','COMPLETED','CANCELLED')
), latest AS (
 SELECT e.*,mv.match_version_id,mv.match_start_at,mv.match_status,
        mv.player_1_id,mv.player_2_id
 FROM expected e JOIN LATERAL (
   SELECT * FROM match_versions v WHERE v.match_id=e.match_id
   ORDER BY as_of DESC,source_observed_at DESC,match_version_id DESC LIMIT 1
 ) mv ON TRUE
)
SELECT v.match_id,v.result_status FROM latest v
JOIN players p1 ON p1.player_id=v.player_1_id
JOIN players p2 ON p2.player_id=v.player_2_id
WHERE p1.canonical_name=v.p1 AND p2.canonical_name=v.p2
AND (v.match_start_at AT TIME ZONE 'UTC')::date=v.day
AND v.match_status::text=v.fixture_status
AND EXISTS (
 SELECT 1 FROM match_source_ids x WHERE x.match_id=v.match_id
 AND x.source_system=v.source AND x.source_match_id=v.event_id
)
AND EXISTS (
 SELECT 1 FROM match_version_sources x WHERE x.match_version_id=v.match_version_id
 AND x.source_system=v.source AND x.source_match_id=v.event_id
)
AND (SELECT COUNT(DISTINCT x.match_id) FROM match_source_ids x
 WHERE x.source_system=v.source AND x.source_match_id=v.event_id)=1;
DO $guard$
BEGIN
IF (SELECT COUNT(*) FROM _public_voids)<>4 THEN
 RAISE EXCEPTION 'PUBLIC_VOID_IDENTITY_OR_FIXTURE_CONFLICT'; END IF;
IF EXISTS (SELECT 1 FROM match_result_observations m
 JOIN _public_voids v USING(match_id)) THEN
 RAISE EXCEPTION 'PUBLIC_VOID_RESULT_ALREADY_OBSERVED'; END IF;
END $guard$;
WITH audit AS (
INSERT INTO ingest_runs
 (source_system,source_dataset,started_at,finished_at,status,source_version,source_file_name,rows_seen,rows_inserted,rows_rejected)
VALUES ('PUBLIC_SCORE_CROSSCHECK','manual_verified_void_results',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'COMPLETED',
        'independent_public_two_source_v2','ops/incident/tennis_public_four_void_recovery_20261101.sql',4,4,0)
RETURNING ingest_run_id
), inserted AS (
INSERT INTO match_result_observations
 (match_id,outcome_status,winner_side,retired_side,source_observed_at,as_of,ingest_run_id)
SELECT v.match_id,v.result_status::result_status,NULL,NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,a.ingest_run_id
FROM _public_voids v CROSS JOIN audit a ORDER BY v.match_id
RETURNING match_id,outcome_status,winner_side
)
SELECT 'PUBLIC_VOID_INSERTED' AS marker,match_id,outcome_status,winner_side
FROM inserted ORDER BY match_id;
DO $verify$
BEGIN
IF (SELECT COUNT(*) FROM match_result_observations m JOIN _public_voids v USING(match_id))<>4
OR (SELECT COUNT(*) FROM match_result_observations m JOIN _public_voids v USING(match_id)
 WHERE m.outcome_status::text=v.result_status AND m.winner_side IS NULL AND m.retired_side IS NULL)<>4
THEN RAISE EXCEPTION 'PUBLIC_VOID_POSTINSERT_VALIDATION_FAILED'; END IF;
END $verify$;
COMMIT;
SELECT 'FOUR_VERIFIED_VOID_RESULTS_COMMITTED' AS status;
