-- Forensic production result recovery 2026-10-30 UTC, already backed up.
-- Distinct historical source matches and two independent public scoreboards were checked.
-- This is a LATE-OBSERVED result event, never backdated to match day.
-- 131036 M. Alcala Gurri v C. Taberner (Plovdiv 4, 26 Sep, SF), 6-4 6-3:
-- https://www.tennis.com/tournaments/atp-challenger-plovdiv-4-bulgaria/matches/m-alcala-gurri-vs-c-taberner-2026-09-26
-- https://www.sofascore.com/tennis/tournament/challenger/plovdiv-4-bulgaria/38043
-- 131527 B. Tomic v M. Bellucci (Jingshan, 02 Oct), WALKOVER:
-- https://www.mykhel.com/tennis/jingshan-tennis-open-2026-mens-singles-scores-c13167/
-- https://tennisdatabaseapi.com/match/mt_6h6jdb44
-- 131536 H. Rocha v J. Kym (Porto, 03 Oct, SF), WALKOVER:
-- https://www.tennis.com/tournaments/clube-tenis-porto-challenger/matches/h-rocha-vs-j-kym-2026-10-03
-- https://www.sofascore.com/tennis/match/henrique-rocha-jerome-kym/wGycsvLLc
-- 131637 M. Purcell v B. Bayldon (Wuning 3 Qualification, 05 Oct, Final), WALKOVER:
-- https://www.sofascore.com/tennis/match/blake-bayldon-max-purcell/iTZsTKUb
-- https://www.tennisabstract.com/cgi-bin/player-classic.cgi?f=A2026qq&p=126845%2FMax-Purcell
-- Guard rejects stale identities, any existing result, mismatched canonical sources,
-- ambiguity, and changed fixture state. Manual public validation is NOT a provider
-- API ingestion and must remain distinguishable from the official API-Tennis feed.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '90s';
SELECT pg_advisory_xact_lock(20261030, 23);
CREATE TEMP TABLE _verified_public_results ON COMMIT DROP AS
WITH expected(match_id, source_system, event_key, first_name, second_name, tournament_day, existing_status, result_status, winner_side, scoreline, sets_1, sets_2) AS (
 VALUES
 (131036,'SPORTRADAR','sr:sport_event:74753168','M. Alcala Gurri','C. Taberner',DATE '2026-09-26','COMPLETED','COMPLETED',1,'6-4 6-3',2,0),
 (131527,'API_TENNIS','12167805','B. Tomic','M. Bellucci',DATE '2026-10-02','SCHEDULED','WALKOVER',NULL,NULL,NULL,NULL),
 (131536,'API_TENNIS','12167901','H. Rocha','J. Kym',DATE '2026-10-03','SCHEDULED','WALKOVER',NULL,NULL,NULL,NULL),
 (131637,'API_TENNIS','12168174','M. Purcell','B. Bayldon',DATE '2026-10-05','SCHEDULED','WALKOVER',NULL,NULL,NULL,NULL)
),
latest AS (
 SELECT e.*, mv.match_version_id,mv.match_status,mv.match_start_at,
        mv.player_1_id,mv.player_2_id
 FROM expected e JOIN LATERAL (
  SELECT * FROM match_versions
  WHERE match_id=e.match_id
  ORDER BY as_of DESC,source_observed_at DESC,match_version_id DESC LIMIT 1
 ) mv ON TRUE
)
SELECT l.match_id,l.result_status,l.winner_side,l.scoreline,l.sets_1,l.sets_2
FROM latest l JOIN players p1 ON p1.player_id=l.player_1_id
JOIN players p2 ON p2.player_id=l.player_2_id
WHERE p1.canonical_name=l.first_name AND p2.canonical_name=l.second_name
 AND (l.match_start_at AT TIME ZONE 'UTC')::date=l.tournament_day
 AND l.match_status::text=l.existing_status
 AND EXISTS (
  SELECT 1 FROM match_source_ids si
  WHERE si.match_id=l.match_id AND si.source_system=l.source_system AND si.source_match_id=l.event_key
 )
 AND EXISTS (
  SELECT 1 FROM match_version_sources vs
  WHERE vs.match_version_id=l.match_version_id AND vs.source_system=l.source_system AND vs.source_match_id=l.event_key
 )
 AND (
  SELECT COUNT(DISTINCT si.match_id) FROM match_source_ids si
  WHERE si.source_system=l.source_system AND si.source_match_id=l.event_key
 )=1;
DO $guard$
BEGIN
 IF (SELECT COUNT(*) FROM _verified_public_results)<>4
 THEN RAISE EXCEPTION 'PUBLIC_RECOVERY_GUARD_IDENTITY_MISMATCH'; END IF;
 IF EXISTS (SELECT 1 FROM match_result_observations o JOIN _verified_public_results x USING(match_id))
 THEN RAISE EXCEPTION 'PUBLIC_RECOVERY_GUARD_RESULT_ALREADY_PRESENT'; END IF;
END
$guard$;
WITH audit AS (
 INSERT INTO ingest_runs
 (source_system,source_dataset,started_at,finished_at,status,source_version,source_file_name,rows_seen,rows_inserted,rows_rejected)
 VALUES ('PUBLIC_SCORE_CROSSCHECK','manual_verified_terminal_results',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'COMPLETED',
         'independent_public_two_source_v1','ops/incident/tennis_public_verified_result_recovery_20261030.sql',4,4,0)
 RETURNING ingest_run_id
), inserted AS (
 INSERT INTO match_result_observations
 (match_id,outcome_status,winner_side,retired_side,sets_won_player_1,sets_won_player_2,
  scoreline_text,source_observed_at,as_of,ingest_run_id)
 SELECT x.match_id,x.result_status::result_status,x.winner_side,NULL,x.sets_1,x.sets_2,x.scoreline,
        CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,audit.ingest_run_id
 FROM _verified_public_results x CROSS JOIN audit
 ORDER BY x.match_id
 RETURNING match_id,outcome_status,winner_side
)
SELECT 'PUBLIC_RECOVERY_INSERTED' AS marker,match_id,outcome_status,winner_side
FROM inserted ORDER BY match_id;
DO $verify$
BEGIN
 IF (SELECT COUNT(*) FROM match_result_observations o JOIN _verified_public_results x USING(match_id))<>4
 OR (SELECT COUNT(*) FROM match_result_observations o WHERE o.match_id=131036 AND o.outcome_status='COMPLETED' AND o.winner_side=1 AND o.scoreline_text='6-4 6-3')<>1
 OR (SELECT COUNT(*) FROM match_result_observations o WHERE o.match_id IN (131527,131536,131637) AND o.outcome_status='WALKOVER' AND o.winner_side IS NULL)<>3
 THEN RAISE EXCEPTION 'PUBLIC_RECOVERY_VERIFICATION_FAILED_ALL_ROWS_ROLLBACK'; END IF;
END
$verify$;
COMMIT;
SELECT 'PUBLIC_RECOVERY_FOUR_CROSSCHECKED_MATCHES_COMMITTED' AS marker;
