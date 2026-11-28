-- Reverse 0025_identity_provenance_v2.
--
-- The version tables are reconstructable from the current reference rows, but the
-- runtime provenance rows are not. This down migration is intentionally destructive
-- and must only run in a reviewed rollback where the evidence archive is preserved.

BEGIN;

DROP TABLE IF EXISTS identity_resolution_provenance;

DROP TRIGGER IF EXISTS trg_player_aliases_identity_guard ON player_aliases;
DROP FUNCTION IF EXISTS guard_player_alias_identity();

DROP TRIGGER IF EXISTS trg_player_aliases_version_after_insert ON player_aliases;
DROP FUNCTION IF EXISTS capture_player_alias_version();

DROP TRIGGER IF EXISTS trg_players_name_version_after_update ON players;
DROP TRIGGER IF EXISTS trg_players_name_version_after_insert ON players;
DROP FUNCTION IF EXISTS capture_player_name_version();

DROP TABLE IF EXISTS player_alias_versions;
DROP TABLE IF EXISTS player_name_versions;

COMMIT;
