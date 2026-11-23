-- 2026-10-30: Data-only, exactly 4 verified API-Tennis WALKOVER observations.
-- Backup: Railway volume tennis-pgbackup-pre0021-20261030,
-- /backup/tennis-pre-0021-20261030.dump, sha256
-- eae5ecb08812abc66e3bb74ec9c906b5db5537f30231c135076b4cdaf58e0d4e.
-- All 4 canonical latest match_versions were read-verified WALKOVER from
-- exact API_TENNIS source event IDs; all had zero terminal result observations.
-- If ANY canonical identity, newest status or preexisting result differs,
-- raise and abort the entire transaction. Never infer a winner.
BEGIN;
SET LOCAL lock_timeout='10s';
SET LOCAL statement_timeout='90s';
SELECT pg_advisory_xact_lock(20261030, 21);
CREATE TEMP TABLE _verified_walkovers ON COMMIT DROP AS
WITH expected(match_id, event_id) AS (
  VALUES (132211, '12171502'), (132533, '12172898'),
         (132535, '12172976'), (132664, '12174015')
),
latest AS (
  SELECT e.match_id, e.event_id,
         mv.match_version_id, mv.match_status, mv.as_of,
         mv.source_observed_at, mv.ingest_run_id
  FROM expected e
  JOIN LATERAL (
    SELECT match_version_id, match_status, as_of, source_observed_at, ingest_run_id
    FROM match_versions
    WHERE match_id=e.match_id
    ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
    LIMIT 1
  ) mv ON TRUE
)
SELECT DISTINCT ON (l.match_id)
  l.match_id, l.event_id, l.as_of, l.source_observed_at, l.ingest_run_id
FROM latest l
JOIN match_version_sources mvs
  ON mvs.match_version_id=l.match_version_id
 AND mvs.source_system='API_TENNIS'
 AND mvs.source_match_id=l.event_id
WHERE l.match_status='WALKOVER'
  AND l.ingest_run_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM match_source_ids msi
    WHERE msi.match_id=l.match_id AND msi.source_system='API_TENNIS'
      AND msi.source_match_id=l.event_id
  )
  AND (
    SELECT COUNT(DISTINCT msi.match_id) FROM match_source_ids msi
    WHERE msi.source_system='API_TENNIS' AND msi.source_match_id=l.event_id
  )=1
ORDER BY l.match_id;
DO $guard$
BEGIN
  IF (SELECT COUNT(*) FROM _verified_walkovers)<>4 THEN
    RAISE EXCEPTION 'WALKOVER_REPAIR_BLOCKED: identity/terminal evidence count not four';
  END IF;
  IF EXISTS (
    SELECT 1 FROM match_result_observations o
    JOIN _verified_walkovers v ON v.match_id=o.match_id
  ) THEN
    RAISE EXCEPTION 'WALKOVER_REPAIR_BLOCKED: existing result observations require review';
  END IF;
END
$guard$;
WITH inserted AS (
  INSERT INTO match_result_observations
    (match_id, outcome_status, winner_side, retired_side,
     source_observed_at, as_of, ingest_run_id)
  SELECT match_id, 'WALKOVER'::result_status, NULL, NULL,
         source_observed_at, as_of, ingest_run_id
  FROM _verified_walkovers
  ORDER BY match_id
  ON CONFLICT DO NOTHING
  RETURNING match_id
)
SELECT 'WALKOVER_RECOVERY_INSERTED' AS marker, match_id
FROM inserted ORDER BY match_id;
DO $verify$
BEGIN
  IF (SELECT COUNT(*) FROM match_result_observations o
      JOIN _verified_walkovers v ON o.match_id=v.match_id)<>4
    OR (SELECT COUNT(*) FROM match_result_observations o
        JOIN _verified_walkovers v ON o.match_id=v.match_id
        WHERE o.outcome_status='WALKOVER' AND o.winner_side IS NULL
          AND o.retired_side IS NULL)<>4 THEN
    RAISE EXCEPTION 'WALKOVER_REPAIR_ROLLBACK: expected four winnerless WALKOVER results';
  END IF;
END
$verify$;
COMMIT;
SELECT 'WALKOVER_RECOVERY_COMMITTED_FOUR_VOIDS' AS status;
