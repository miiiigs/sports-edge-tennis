-- Versioned identity reference data and durable runtime resolution provenance.
--
-- This migration closes the last point-in-time gaps in the live-to-history identity
-- bridge:
--
--   * ``players.canonical_name`` is mutable in place. ``player_name_versions`` is an
--     append-only history whose ``valid_from`` is the only timestamp a PIT resolver is
--     allowed to use. The table has no ``valid_to`` column on purpose: closing a row
--     would be an UPDATE, which contradicts append-only history. An effective interval
--     is derived from the next row's ``valid_from``.
--   * ``player_aliases`` identity linkage can be re-pointed out of band. The guard
--     trigger rejects any change to the linkage columns. Re-observation may still move
--     ``last_observed_at``/``updated_at``. ``player_alias_versions`` records every
--     linkage insertion so a later replay does not have to trust the current row.
--   * ``identity_resolution_provenance`` stores the exact runtime decision per player:
--     original id, model id, method, evidence, reference digest, observation time and
--     the immutable ``match_version_id`` it was made against.
--
-- Backfill never invents a timestamp. A version starts at the earliest time the current
-- value is provably true, and a post-creation rename is never asserted to have existed
-- from row creation. Nothing is backdated to make a historical replay resolve more
-- matches.
--
-- Migration numbering: 0023 (Manager C feature snapshots) and 0024 (OddsPapi identity
-- history) are owned by integration PR #392. This branch imports that train and uses
-- the next free version, 0025, for identity provenance. Do not renumber this file.

BEGIN;

CREATE TABLE player_name_versions (
    player_name_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_id BIGINT NOT NULL REFERENCES players(player_id),
    canonical_name TEXT NOT NULL CHECK (btrim(canonical_name) <> ''),
    valid_from TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    actor TEXT NOT NULL CHECK (btrim(actor) <> ''),
    reason TEXT NOT NULL CHECK (btrim(reason) <> '')
);

CREATE UNIQUE INDEX uq_player_name_versions_player_valid_from
    ON player_name_versions (player_id, valid_from);
CREATE INDEX idx_player_name_versions_lookup
    ON player_name_versions (player_id, valid_from DESC);

CREATE TRIGGER trg_player_name_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON player_name_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TABLE player_alias_versions (
    player_alias_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_alias_id BIGINT NOT NULL,
    player_id BIGINT NOT NULL REFERENCES players(player_id),
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    alias TEXT NOT NULL CHECK (btrim(alias) <> ''),
    normalized_alias TEXT NOT NULL CHECK (btrim(normalized_alias) <> ''),
    first_observed_at TIMESTAMPTZ,
    valid_from TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    actor TEXT NOT NULL CHECK (btrim(actor) <> ''),
    reason TEXT NOT NULL CHECK (btrim(reason) <> '')
);

CREATE UNIQUE INDEX uq_player_alias_versions_alias_valid_from
    ON player_alias_versions (player_alias_id, valid_from);
CREATE INDEX idx_player_alias_versions_pit
    ON player_alias_versions (normalized_alias, valid_from DESC, player_id);
CREATE INDEX idx_player_alias_versions_player
    ON player_alias_versions (player_id, valid_from DESC);

CREATE TRIGGER trg_player_alias_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON player_alias_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

-- Backfill: one initial version per existing row. ``valid_from`` is the earliest time
-- the *current* value is provably true: ``GREATEST(created_at, updated_at)``. Using
-- created_at alone would assert that a post-creation rename was always the name. In the
-- observed production corpus ``updated_at = created_at`` for every player, so this is
-- not lossy there; if a rename did occur, the backfill stays conservative.
INSERT INTO player_name_versions (
    player_id, canonical_name, valid_from, recorded_at, actor, reason
)
SELECT player_id, canonical_name, GREATEST(created_at, updated_at), CURRENT_TIMESTAMP,
       'migration_0025', 'backfill_players_current_value_lower_bound'
FROM players;

INSERT INTO player_alias_versions (
    player_alias_id, player_id, source_system, alias, normalized_alias,
    first_observed_at, valid_from, recorded_at, ingest_run_id, actor, reason
)
SELECT pa.player_alias_id, pa.player_id, pa.source_system, pa.alias,
       pa.normalized_alias, pa.first_observed_at,
       COALESCE(ir.finished_at, ir.started_at, pa.created_at),
       CURRENT_TIMESTAMP, pa.ingest_run_id,
       'migration_0025', 'backfill_player_alias_ingest_visibility'
FROM player_aliases pa
LEFT JOIN ingest_runs ir ON ir.ingest_run_id = pa.ingest_run_id;

-- New player names become a version at the row's own creation time.
CREATE OR REPLACE FUNCTION capture_player_name_version()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO player_name_versions (
            player_id, canonical_name, valid_from, recorded_at, actor, reason
        )
        VALUES (
            NEW.player_id, NEW.canonical_name, NEW.created_at, CURRENT_TIMESTAMP,
            'players_insert', 'initial_name'
        )
        ON CONFLICT (player_id, valid_from) DO NOTHING;
        RETURN NEW;
    END IF;

    IF NEW.canonical_name IS DISTINCT FROM OLD.canonical_name THEN
        INSERT INTO player_name_versions (
            player_id, canonical_name, valid_from, recorded_at, actor, reason
        )
        VALUES (
            NEW.player_id, NEW.canonical_name, NEW.updated_at, CURRENT_TIMESTAMP,
            'players_update', 'canonical_name_changed'
        )
        ON CONFLICT (player_id, valid_from) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_players_name_version_after_insert
AFTER INSERT ON players
FOR EACH ROW
EXECUTE FUNCTION capture_player_name_version();

CREATE TRIGGER trg_players_name_version_after_update
AFTER UPDATE OF canonical_name ON players
FOR EACH ROW
EXECUTE FUNCTION capture_player_name_version();

-- New aliases become an immutable version at insertion time.
CREATE OR REPLACE FUNCTION capture_player_alias_version()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    visible_from TIMESTAMPTZ;
BEGIN
    SELECT COALESCE(ir.finished_at, ir.started_at, NEW.created_at)
    INTO visible_from
    FROM ingest_runs ir
    WHERE ir.ingest_run_id = NEW.ingest_run_id;

    IF visible_from IS NULL THEN
        visible_from := NEW.created_at;
    END IF;

    INSERT INTO player_alias_versions (
        player_alias_id, player_id, source_system, alias, normalized_alias,
        first_observed_at, valid_from, recorded_at, ingest_run_id, actor, reason
    )
    VALUES (
        NEW.player_alias_id, NEW.player_id, NEW.source_system, NEW.alias,
        NEW.normalized_alias, NEW.first_observed_at, visible_from,
        CURRENT_TIMESTAMP, NEW.ingest_run_id, 'player_aliases_insert',
        'linkage_inserted'
    )
    ON CONFLICT (player_alias_id, valid_from) DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_player_aliases_version_after_insert
AFTER INSERT ON player_aliases
FOR EACH ROW
EXECUTE FUNCTION capture_player_alias_version();

-- Reject out-of-band linkage mutation. The pipeline upsert only refreshes observation
-- timestamps, so normal traffic is unaffected. A reviewed repair can opt in with
-- ``SET LOCAL tennis.identity_reference_maintenance = 'on'`` inside one transaction;
-- that escape hatch is deliberately session-scoped and logged by the repair itself.
CREATE OR REPLACE FUNCTION guard_player_alias_identity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF current_setting('tennis.identity_reference_maintenance', true) = 'on' THEN
        RETURN NEW;
    END IF;

    IF (to_jsonb(NEW) - ARRAY['last_observed_at', 'updated_at'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['last_observed_at', 'updated_at']) THEN
        RAISE EXCEPTION USING
            MESSAGE = 'player_aliases identity linkage is immutable under '
                      '0025_identity_provenance_v2; insert a new reviewed alias '
                      'version instead of re-pointing the existing row.',
            ERRCODE = 'P0001';
    END IF;

    IF NEW.last_observed_at IS NOT NULL
       AND OLD.last_observed_at IS NOT NULL
       AND NEW.last_observed_at < OLD.last_observed_at THEN
        RAISE EXCEPTION USING
            MESSAGE = 'player_aliases.last_observed_at must be monotonic.',
            ERRCODE = 'P0001';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_player_aliases_identity_guard
BEFORE UPDATE ON player_aliases
FOR EACH ROW
EXECUTE FUNCTION guard_player_alias_identity();

CREATE TABLE identity_resolution_provenance (
    identity_resolution_provenance_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prediction_run_id BIGINT NOT NULL REFERENCES prediction_runs(prediction_run_id),
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    match_version_id BIGINT NOT NULL REFERENCES match_versions(match_version_id),
    predicted_at TIMESTAMPTZ NOT NULL,
    original_player_id BIGINT NOT NULL REFERENCES players(player_id),
    model_player_id BIGINT NOT NULL REFERENCES players(player_id),
    resolution_method TEXT NOT NULL CHECK (
        resolution_method IN (
            'HISTORICAL_ID', 'PERSISTED_BRIDGE', 'ALIAS', 'AMBIGUOUS',
            'NO_CANDIDATE', 'NO_HISTORICAL_CANDIDATE', 'NO_PLAYER'
        )
    ),
    evidence TEXT NOT NULL CHECK (btrim(evidence) <> ''),
    name_source TEXT NOT NULL CHECK (btrim(name_source) <> ''),
    candidate_count INTEGER NOT NULL DEFAULT 0 CHECK (candidate_count >= 0),
    matched_alias TEXT,
    bridge_event_id BIGINT
        REFERENCES model_identity_bridge_events(model_identity_bridge_event_id),
    visible_from TIMESTAMPTZ,
    reference_policy TEXT NOT NULL CHECK (btrim(reference_policy) <> ''),
    reference_sha256 TEXT NOT NULL CHECK (length(reference_sha256) = 64),
    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
    source_record_sha256 TEXT CHECK (
        source_record_sha256 IS NULL OR length(source_record_sha256) = 64
    ),
    capture_source TEXT NOT NULL CHECK (
        capture_source IN ('LIVE_RUNTIME', 'FORWARD_SHADOW')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (visible_from IS NULL OR visible_from <= predicted_at)
);

CREATE UNIQUE INDEX uq_identity_resolution_provenance_key
    ON identity_resolution_provenance (
        prediction_run_id, match_id, original_player_id
    );
CREATE INDEX idx_identity_resolution_provenance_match
    ON identity_resolution_provenance (match_id, predicted_at);
CREATE INDEX idx_identity_resolution_provenance_model
    ON identity_resolution_provenance (model_player_id);

CREATE TRIGGER trg_identity_resolution_provenance_append_only_mutation
BEFORE UPDATE OR DELETE ON identity_resolution_provenance
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMENT ON TABLE player_name_versions IS
    'Append-only canonical-name history for PIT identity resolution. Effective '
    'interval is [valid_from, next valid_from). Never backdated to improve replay.';
COMMENT ON TABLE player_alias_versions IS
    'Append-only alias linkage history. The current player_aliases row is '
    'value-immutable for linkage columns under migration 0025.';
COMMENT ON TABLE identity_resolution_provenance IS
    'Exact per-player runtime identity resolution: original id, model id, method, '
    'evidence, reference digest, observation time and immutable match version id.';

COMMIT;
