-- Immutable, run-linked feature snapshots for research provenance.
--
-- rating_snapshots (0001) is a per-player, per-as-of research table and is
-- never written by the live runtime. This table records the exact feature
-- values a specific prediction actually consumed, keyed one-to-one to
-- predictions(prediction_id), so a later census can distinguish "value was
-- available and used" from "value was reconstructed or unlinked".
--
-- Only values genuinely computed at prediction time are stored. Rating
-- uncertainty is NULL unless a producer actually computed it; do not backfill
-- or derive it here.
--
-- Lineage is enforced in the database, not just by convention:
--   * (prediction_id, prediction_run_id, match_id, agent_code) must name the
--     exact immutable prediction row the snapshot belongs to;
--   * (match_version_id, player_1_id, player_2_id) must match the bound match
--     version's own player orientation, so a reversed snapshot cannot pass;
--   * agent_config_version_id must reference the configuration used.

BEGIN;

-- Supports the composite orientation foreign key below. match_version_id is
-- already unique on its own, so this index cannot reject existing data.
CREATE UNIQUE INDEX uq_match_versions_id_orientation
    ON match_versions (match_version_id, player_1_id, player_2_id);

CREATE TABLE prediction_feature_snapshots (
    prediction_feature_snapshot_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prediction_id BIGINT NOT NULL UNIQUE,
    prediction_run_id BIGINT NOT NULL
        REFERENCES prediction_runs(prediction_run_id),
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    match_version_id BIGINT NOT NULL REFERENCES match_versions(match_version_id),
    agent_config_version_id BIGINT NOT NULL
        REFERENCES agent_config_versions(agent_config_version_id),
    agent_code agent_code NOT NULL,
    player_1_id BIGINT NOT NULL REFERENCES players(player_id),
    player_2_id BIGINT NOT NULL REFERENCES players(player_id),
    surface tennis_surface,
    player_1_overall_rating NUMERIC(12, 6),
    player_2_overall_rating NUMERIC(12, 6),
    player_1_overall_match_count INTEGER
        CHECK (player_1_overall_match_count IS NULL OR player_1_overall_match_count >= 0),
    player_2_overall_match_count INTEGER
        CHECK (player_2_overall_match_count IS NULL OR player_2_overall_match_count >= 0),
    player_1_surface_rating NUMERIC(12, 6),
    player_2_surface_rating NUMERIC(12, 6),
    player_1_surface_match_count INTEGER
        CHECK (player_1_surface_match_count IS NULL OR player_1_surface_match_count >= 0),
    player_2_surface_match_count INTEGER
        CHECK (player_2_surface_match_count IS NULL OR player_2_surface_match_count >= 0),
    rating_uncertainty NUMERIC(12, 6)
        CHECK (rating_uncertainty IS NULL OR rating_uncertainty >= 0),
    rating_uncertainty_source TEXT,
    identity_complete BOOLEAN NOT NULL,
    surface_known BOOLEAN NOT NULL,
    main_draw_verified BOOLEAN NOT NULL,
    model_version TEXT NOT NULL CHECK (btrim(model_version) <> ''),
    feature_version TEXT NOT NULL CHECK (btrim(feature_version) <> ''),
    as_of TIMESTAMPTZ NOT NULL,
    prediction_locked_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_prediction_feature_snapshot_players
        CHECK (player_1_id <> player_2_id),
    CONSTRAINT fk_prediction_feature_snapshot_prediction_lineage
        FOREIGN KEY (prediction_id, prediction_run_id, match_id, agent_code)
        REFERENCES predictions(
            prediction_id, prediction_run_id, match_id, agent_code
        ),
    CONSTRAINT fk_prediction_feature_snapshot_match_orientation
        FOREIGN KEY (match_version_id, player_1_id, player_2_id)
        REFERENCES match_versions(
            match_version_id, player_1_id, player_2_id
        ),
    CONSTRAINT chk_prediction_feature_snapshot_clocks
        CHECK (as_of <= prediction_locked_at),
    CONSTRAINT chk_prediction_feature_snapshot_identity
        CHECK (
            NOT identity_complete
            OR (
                player_1_overall_match_count IS NOT NULL
                AND player_2_overall_match_count IS NOT NULL
            )
        ),
    CONSTRAINT chk_prediction_feature_snapshot_surface
        CHECK (
            NOT surface_known
            OR (
                surface IS NOT NULL
                AND player_1_surface_match_count IS NOT NULL
                AND player_2_surface_match_count IS NOT NULL
            )
        ),
    CONSTRAINT chk_prediction_feature_snapshot_uncertainty_source
        CHECK (
            (rating_uncertainty IS NULL AND rating_uncertainty_source IS NULL)
            OR (
                rating_uncertainty IS NOT NULL
                AND rating_uncertainty_source IS NOT NULL
                AND btrim(rating_uncertainty_source) <> ''
            )
        )
);

CREATE INDEX idx_prediction_feature_snapshots_match
    ON prediction_feature_snapshots (match_id, as_of DESC);
CREATE INDEX idx_prediction_feature_snapshots_run
    ON prediction_feature_snapshots (prediction_run_id);

COMMENT ON COLUMN prediction_feature_snapshots.identity_complete IS
    'Historical-data completeness only: both players had at least one prior match in the shared rating pool. NOT a verified provider/canonical identity claim.';
COMMENT ON COLUMN prediction_feature_snapshots.main_draw_verified IS
    'True when the tournament target was not a qualifying fixture.';
COMMENT ON COLUMN prediction_feature_snapshots.rating_uncertainty IS
    'NULL unless a producer genuinely computed an uncertainty at prediction time; never derived or backfilled.';
COMMENT ON COLUMN prediction_feature_snapshots.prediction_locked_at IS
    'The prediction_run finalization timestamp that locked the bound prediction.';

CREATE TRIGGER trg_prediction_feature_snapshots_append_only_mutation
BEFORE UPDATE OR DELETE ON prediction_feature_snapshots
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
