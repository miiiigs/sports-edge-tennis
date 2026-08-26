BEGIN;

CREATE TYPE tour_scope AS ENUM ('ATP_MAIN', 'CHALLENGER');
CREATE TYPE player_status AS ENUM ('ACTIVE', 'INACTIVE', 'RETIRED', 'UNKNOWN');
CREATE TYPE competition_tier AS ENUM (
    'GRAND_SLAM',
    'MASTERS_1000',
    'ATP_500',
    'ATP_250',
    'CHALLENGER'
);
CREATE TYPE tennis_surface AS ENUM ('HARD', 'CLAY', 'GRASS', 'CARPET');
CREATE TYPE identity_resolution_status AS ENUM ('RESOLVED', 'UNMATCHED', 'AMBIGUOUS');
CREATE TYPE match_status AS ENUM (
    'SCHEDULED',
    'COMPLETED',
    'RETIRED',
    'WALKOVER',
    'CANCELLED',
    'POSTPONED'
);
CREATE TYPE result_status AS ENUM ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED');
CREATE TYPE match_stat_observation_kind AS ENUM ('RAW_SOURCE', 'NORMALIZED_SOURCE');
CREATE TYPE odds_market_type AS ENUM (
    'MATCH_WINNER',
    'SET_BETTING',
    'TOTAL_GAMES',
    'GAME_HANDICAP'
);
CREATE TYPE market_selection_type AS ENUM (
    'PLAYER_1',
    'PLAYER_2',
    'OVER',
    'UNDER',
    'SIDE_1',
    'SIDE_2'
);
CREATE TYPE market_snapshot_status AS ENUM ('OPEN', 'SUSPENDED', 'CLOSED', 'UNKNOWN');
CREATE TYPE rating_metric AS ENUM ('ELO', 'SPW_ADJUSTED', 'RPW_ADJUSTED');
CREATE TYPE agent_code AS ENUM (
    'AGENT_1_SURFACE_ELO',
    'AGENT_2_HIERARCHICAL_MARKOV',
    'AGENT_3_INTRANSITIVITY'
);
CREATE TYPE prediction_status AS ENUM ('PREDICTED', 'ABSTAINED');
CREATE TYPE signal_type AS ENUM ('TYPE_A_MODEL_EDGE', 'TYPE_B_PRICE_DISPERSION');
CREATE TYPE decision_status AS ENUM ('BET', 'NO_BET', 'VETOED');
CREATE TYPE decision_reason_code AS ENUM (
    'NONE',
    'CONSENSUS_FAILURE',
    'PRICE_FAILURE',
    'STALE_DATA',
    'ABSTENTION_RELATED',
    'DIRECTIONAL_DISAGREEMENT',
    'PROBABILITY_SPREAD',
    'PROBABILITY_FLOOR',
    'DATA_SUFFICIENCY',
    'VETO_TRIGGERED'
);
CREATE TYPE agent_participation_status AS ENUM ('ACTIVE', 'ABSTAINED', 'INACTIVE');
CREATE TYPE settlement_status AS ENUM ('PENDING', 'WON', 'LOST', 'VOID', 'PUSH', 'CANCELLED');
CREATE TYPE ingest_run_status AS ENUM ('RUNNING', 'COMPLETED', 'FAILED', 'PARTIAL', 'CANCELLED');
CREATE TYPE freshness_state AS ENUM ('FRESH', 'STALE', 'UNKNOWN');
CREATE TYPE health_status AS ENUM ('HEALTHY', 'WARNING', 'ERROR');

CREATE TABLE ingest_runs (
    ingest_run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    source_dataset TEXT NOT NULL CHECK (btrim(source_dataset) <> ''),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status ingest_run_status NOT NULL,
    source_version TEXT,
    source_file_name TEXT,
    source_file_hash TEXT,
    rows_seen INTEGER NOT NULL DEFAULT 0 CHECK (rows_seen >= 0),
    rows_inserted INTEGER NOT NULL DEFAULT 0 CHECK (rows_inserted >= 0),
    rows_updated INTEGER NOT NULL DEFAULT 0 CHECK (rows_updated >= 0),
    rows_rejected INTEGER NOT NULL DEFAULT 0 CHECK (rows_rejected >= 0),
    rows_unmatched INTEGER NOT NULL DEFAULT 0 CHECK (rows_unmatched >= 0),
    error_summary JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE players (
    player_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    primary_tour_scope tour_scope,
    status player_status NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE player_source_ids (
    player_source_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_id BIGINT NOT NULL REFERENCES players(player_id),
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    source_player_id TEXT NOT NULL CHECK (btrim(source_player_id) <> ''),
    source_record_hash TEXT,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_system, source_player_id)
);

CREATE TABLE player_aliases (
    player_alias_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_id BIGINT NOT NULL REFERENCES players(player_id),
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    alias TEXT NOT NULL CHECK (btrim(alias) <> ''),
    normalized_alias TEXT NOT NULL CHECK (btrim(normalized_alias) <> ''),
    first_observed_at TIMESTAMPTZ,
    last_observed_at TIMESTAMPTZ,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (last_observed_at IS NULL OR first_observed_at IS NULL OR last_observed_at >= first_observed_at),
    UNIQUE (player_id, source_system, normalized_alias)
);

CREATE TABLE identity_resolution_events (
    identity_resolution_event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    source_record_id TEXT,
    raw_player_name TEXT NOT NULL CHECK (btrim(raw_player_name) <> ''),
    normalized_player_name TEXT NOT NULL CHECK (btrim(normalized_player_name) <> ''),
    resolution_status identity_resolution_status NOT NULL,
    resolved_player_id BIGINT REFERENCES players(player_id),
    context_tournament_name TEXT,
    context_match_start_at TIMESTAMPTZ,
    source_observed_at TIMESTAMPTZ NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (as_of >= source_observed_at),
    CHECK (ingested_at >= source_observed_at),
    CHECK (
        (resolution_status = 'RESOLVED' AND resolved_player_id IS NOT NULL)
        OR (resolution_status = 'UNMATCHED' AND resolved_player_id IS NULL)
        OR (resolution_status = 'AMBIGUOUS' AND resolved_player_id IS NULL)
    ),
    UNIQUE (source_system, source_record_id, raw_player_name, source_observed_at)
);

CREATE TABLE identity_resolution_candidates (
    identity_resolution_candidate_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    identity_resolution_event_id BIGINT NOT NULL REFERENCES identity_resolution_events(identity_resolution_event_id),
    player_id BIGINT NOT NULL REFERENCES players(player_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (identity_resolution_event_id, player_id)
);

CREATE TABLE tournaments (
    tournament_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    tour_scope tour_scope NOT NULL,
    competition_tier competition_tier NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tournament_source_ids (
    tournament_source_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tournament_id BIGINT NOT NULL REFERENCES tournaments(tournament_id),
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    source_tournament_id TEXT NOT NULL CHECK (btrim(source_tournament_id) <> ''),
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_system, source_tournament_id)
);

CREATE TABLE tournament_editions (
    tournament_edition_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tournament_id BIGINT NOT NULL REFERENCES tournaments(tournament_id),
    season_year INTEGER NOT NULL CHECK (season_year BETWEEN 1900 AND 2100),
    edition_name TEXT NOT NULL CHECK (btrim(edition_name) <> ''),
    surface tennis_surface,
    location_name TEXT,
    timezone_name TEXT,
    start_date DATE,
    end_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date),
    UNIQUE (tournament_id, season_year, edition_name)
);

CREATE TABLE matches (
    match_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tournament_edition_id BIGINT NOT NULL REFERENCES tournament_editions(tournament_edition_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE match_versions (
    match_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    player_1_id BIGINT NOT NULL REFERENCES players(player_id),
    player_2_id BIGINT NOT NULL REFERENCES players(player_id),
    match_start_at TIMESTAMPTZ NOT NULL,
    tour_scope tour_scope NOT NULL,
    competition_tier competition_tier NOT NULL,
    surface tennis_surface,
    round_code TEXT,
    best_of_sets SMALLINT NOT NULL CHECK (best_of_sets IN (3, 5)),
    qualifying_flag BOOLEAN NOT NULL DEFAULT FALSE,
    match_status match_status NOT NULL,
    source_observed_at TIMESTAMPTZ NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (player_1_id <> player_2_id),
    CHECK (as_of >= source_observed_at),
    CHECK (ingested_at >= source_observed_at),
    UNIQUE (match_id, as_of, source_observed_at)
);

CREATE TABLE match_source_ids (
    match_source_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    match_version_id BIGINT REFERENCES match_versions(match_version_id),
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    source_match_id TEXT,
    source_record_hash TEXT,
    source_record_locator TEXT,
    source_file_name TEXT,
    source_version_hash TEXT,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (source_match_id IS NOT NULL OR source_record_hash IS NOT NULL)
);

CREATE TABLE match_version_sources (
    match_version_source_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_version_id BIGINT NOT NULL REFERENCES match_versions(match_version_id),
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    source_match_id TEXT,
    source_record_hash TEXT,
    source_record_locator TEXT,
    source_file_name TEXT,
    source_version_hash TEXT,
    source_observed_at TIMESTAMPTZ NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (source_match_id IS NOT NULL OR source_record_hash IS NOT NULL),
    CHECK (as_of >= source_observed_at),
    CHECK (ingested_at >= source_observed_at)
);

CREATE TABLE match_result_observations (
    match_result_observation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    outcome_status result_status NOT NULL,
    winner_side SMALLINT CHECK (winner_side IN (1, 2)),
    retired_side SMALLINT CHECK (retired_side IN (1, 2)),
    sets_won_player_1 SMALLINT CHECK (sets_won_player_1 IS NULL OR sets_won_player_1 >= 0),
    sets_won_player_2 SMALLINT CHECK (sets_won_player_2 IS NULL OR sets_won_player_2 >= 0),
    games_won_player_1 INTEGER CHECK (games_won_player_1 IS NULL OR games_won_player_1 >= 0),
    games_won_player_2 INTEGER CHECK (games_won_player_2 IS NULL OR games_won_player_2 >= 0),
    scoreline_text TEXT,
    source_observed_at TIMESTAMPTZ NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (as_of >= source_observed_at),
    CHECK (ingested_at >= source_observed_at),
    CHECK (
        (outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER') AND winner_side IS NOT NULL)
        OR (outcome_status = 'CANCELLED' AND winner_side IS NULL)
    ),
    CHECK (retired_side IS NULL OR outcome_status = 'RETIRED'),
    UNIQUE (match_id, as_of, source_observed_at)
);

CREATE TABLE match_stat_observations (
    match_stat_observation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    player_id BIGINT NOT NULL REFERENCES players(player_id),
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    source_record_id TEXT,
    source_record_hash TEXT,
    observation_kind match_stat_observation_kind NOT NULL,
    first_serves_in INTEGER CHECK (first_serves_in IS NULL OR first_serves_in >= 0),
    first_serve_points_won INTEGER CHECK (first_serve_points_won IS NULL OR first_serve_points_won >= 0),
    second_serve_points_won INTEGER CHECK (second_serve_points_won IS NULL OR second_serve_points_won >= 0),
    service_points_played INTEGER CHECK (service_points_played IS NULL OR service_points_played >= 0),
    return_points_won INTEGER CHECK (return_points_won IS NULL OR return_points_won >= 0),
    aces INTEGER CHECK (aces IS NULL OR aces >= 0),
    double_faults INTEGER CHECK (double_faults IS NULL OR double_faults >= 0),
    break_points_saved INTEGER CHECK (break_points_saved IS NULL OR break_points_saved >= 0),
    break_points_faced INTEGER CHECK (break_points_faced IS NULL OR break_points_faced >= 0),
    source_observed_at TIMESTAMPTZ NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (source_record_id IS NOT NULL OR source_record_hash IS NOT NULL),
    CHECK (as_of >= source_observed_at),
    CHECK (ingested_at >= source_observed_at),
    CHECK (
        service_points_played IS NULL
        OR (
            COALESCE(first_serve_points_won, 0) + COALESCE(second_serve_points_won, 0)
        ) <= service_points_played
    )
);

CREATE TABLE odds_snapshots (
    odds_snapshot_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    bookmaker_code TEXT NOT NULL CHECK (btrim(bookmaker_code) <> ''),
    market_type odds_market_type NOT NULL,
    selection_type market_selection_type NOT NULL,
    line_value NUMERIC(8, 3),
    decimal_price NUMERIC(12, 6) NOT NULL CHECK (decimal_price > 1),
    market_status market_snapshot_status NOT NULL DEFAULT 'OPEN',
    booksum NUMERIC(12, 8) CHECK (booksum IS NULL OR booksum > 0),
    source_snapshot_id TEXT,
    source_record_hash TEXT,
    source_observed_at TIMESTAMPTZ NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (source_snapshot_id IS NOT NULL OR source_record_hash IS NOT NULL),
    CHECK (as_of >= source_observed_at),
    CHECK (ingested_at >= source_observed_at),
    CHECK (
        market_type <> 'MATCH_WINNER'
        OR (line_value IS NULL AND selection_type IN ('PLAYER_1', 'PLAYER_2'))
    )
);

CREATE TABLE rating_snapshots (
    rating_snapshot_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_id BIGINT NOT NULL REFERENCES players(player_id),
    rating_metric rating_metric NOT NULL,
    surface tennis_surface,
    rating_value NUMERIC(12, 6) NOT NULL,
    sample_match_count INTEGER CHECK (sample_match_count IS NULL OR sample_match_count >= 0),
    model_version TEXT NOT NULL CHECK (btrim(model_version) <> ''),
    feature_version TEXT NOT NULL CHECK (btrim(feature_version) <> ''),
    experiment_id TEXT,
    walk_forward_fold INTEGER,
    variant_id TEXT,
    as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE predictions (
    prediction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    agent_code agent_code NOT NULL,
    prediction_status prediction_status NOT NULL,
    selection_side SMALLINT CHECK (selection_side IN (1, 2)),
    win_probability NUMERIC(8, 6) CHECK (win_probability IS NULL OR (win_probability >= 0 AND win_probability <= 1)),
    model_version TEXT NOT NULL CHECK (btrim(model_version) <> ''),
    feature_version TEXT NOT NULL CHECK (btrim(feature_version) <> ''),
    experiment_id TEXT,
    walk_forward_fold INTEGER,
    variant_id TEXT,
    as_of TIMESTAMPTZ NOT NULL,
    prediction_locked_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (prediction_status = 'PREDICTED' AND selection_side IS NOT NULL AND win_probability IS NOT NULL)
        OR (prediction_status = 'ABSTAINED' AND selection_side IS NULL AND win_probability IS NULL)
    ),
    CHECK (prediction_locked_at >= as_of),
    CHECK (created_at >= as_of)
);

CREATE TABLE decisions (
    decision_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    signal_type signal_type NOT NULL,
    decision_status decision_status NOT NULL,
    reason_code decision_reason_code NOT NULL DEFAULT 'NONE',
    market_type odds_market_type NOT NULL,
    selection_type market_selection_type,
    selection_side SMALLINT CHECK (selection_side IN (1, 2)),
    line_value NUMERIC(8, 3),
    consensus_probability NUMERIC(8, 6) CHECK (consensus_probability IS NULL OR (consensus_probability >= 0 AND consensus_probability <= 1)),
    consensus_fair_odds NUMERIC(12, 6) CHECK (consensus_fair_odds IS NULL OR consensus_fair_odds > 1),
    market_decimal_price NUMERIC(12, 6) CHECK (market_decimal_price IS NULL OR market_decimal_price > 1),
    sharp_reference_price NUMERIC(12, 6) CHECK (sharp_reference_price IS NULL OR sharp_reference_price > 1),
    price_source_system TEXT,
    price_bookmaker_code TEXT,
    devig_method TEXT,
    devig_fair_odds NUMERIC(12, 6) CHECK (devig_fair_odds IS NULL OR devig_fair_odds > 1),
    edge_pct NUMERIC(10, 6),
    min_acceptable_odds NUMERIC(12, 6) CHECK (min_acceptable_odds IS NULL OR min_acceptable_odds > 1),
    active_agent_count SMALLINT NOT NULL DEFAULT 0 CHECK (active_agent_count BETWEEN 0 AND 3),
    abstaining_agent_count SMALLINT NOT NULL DEFAULT 0 CHECK (abstaining_agent_count BETWEEN 0 AND 3),
    directional_disagreement BOOLEAN NOT NULL DEFAULT FALSE,
    probability_spread NUMERIC(8, 6) CHECK (probability_spread IS NULL OR probability_spread >= 0),
    model_version TEXT NOT NULL CHECK (btrim(model_version) <> ''),
    feature_version TEXT NOT NULL CHECK (btrim(feature_version) <> ''),
    experiment_id TEXT,
    walk_forward_fold INTEGER,
    variant_id TEXT,
    as_of TIMESTAMPTZ NOT NULL,
    prediction_locked_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMPTZ,
    CHECK (
        (
            decision_status = 'BET'
            AND selection_type IS NOT NULL
            AND (
                (
                    selection_type IN ('PLAYER_1', 'PLAYER_2', 'SIDE_1', 'SIDE_2')
                    AND selection_side IS NOT NULL
                )
                OR (
                    selection_type IN ('OVER', 'UNDER')
                    AND selection_side IS NULL
                )
            )
        )
        OR (decision_status IN ('NO_BET', 'VETOED'))
    ),
    CHECK (
        (decision_status = 'BET' AND reason_code = 'NONE')
        OR (decision_status = 'NO_BET' AND reason_code NOT IN ('NONE', 'VETO_TRIGGERED'))
        OR (decision_status = 'VETOED' AND reason_code = 'VETO_TRIGGERED')
    ),
    CHECK (prediction_locked_at >= as_of),
    CHECK (created_at >= as_of),
    CHECK (
        decision_status <> 'BET'
        OR (
            selection_type IS NOT NULL
            AND consensus_probability IS NOT NULL
            AND consensus_fair_odds IS NOT NULL
            AND market_decimal_price IS NOT NULL
            AND sharp_reference_price IS NOT NULL
            AND price_source_system IS NOT NULL
            AND price_bookmaker_code IS NOT NULL
            AND devig_method IS NOT NULL
            AND devig_fair_odds IS NOT NULL
            AND edge_pct IS NOT NULL
            AND min_acceptable_odds IS NOT NULL
            AND active_agent_count > 0
        )
    ),
    CHECK (
        decision_status <> 'NO_BET'
        OR (
            selection_type IS NULL
            AND selection_side IS NULL
            AND line_value IS NULL
            AND min_acceptable_odds IS NULL
        )
    ),
    CHECK (
        selection_side IS NULL
        OR selection_type IS NOT NULL
    ),
    CHECK (
        CASE
            WHEN selection_type IN ('PLAYER_1', 'SIDE_1') THEN selection_side IS NOT DISTINCT FROM 1
            WHEN selection_type IN ('PLAYER_2', 'SIDE_2') THEN selection_side IS NOT DISTINCT FROM 2
            ELSE TRUE
        END
    ),
    CHECK (
        market_type <> 'MATCH_WINNER'
        OR selection_type IS NULL
        OR line_value IS NULL
    ),
    CHECK (
        market_type <> 'MATCH_WINNER'
        OR selection_side IS NULL
        OR (
            (selection_side = 1 AND selection_type = 'PLAYER_1')
            OR (selection_side = 2 AND selection_type = 'PLAYER_2')
        )
    ),
    CHECK (
        selection_type NOT IN ('OVER', 'UNDER', 'SIDE_1', 'SIDE_2')
        OR line_value IS NOT NULL
    )
);

CREATE TABLE decision_agents (
    decision_id BIGINT NOT NULL REFERENCES decisions(decision_id),
    agent_code agent_code NOT NULL,
    participation_status agent_participation_status NOT NULL,
    proposed_selection_side SMALLINT CHECK (proposed_selection_side IN (1, 2)),
    proposed_probability NUMERIC(8, 6) CHECK (proposed_probability IS NULL OR (proposed_probability >= 0 AND proposed_probability <= 1)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (decision_id, agent_code),
    CHECK (
        (participation_status = 'ACTIVE' AND proposed_selection_side IS NOT NULL AND proposed_probability IS NOT NULL)
        OR (participation_status = 'ABSTAINED' AND proposed_selection_side IS NULL AND proposed_probability IS NULL)
        OR (participation_status = 'INACTIVE')
    )
);

CREATE TABLE decision_settlements (
    decision_id BIGINT PRIMARY KEY REFERENCES decisions(decision_id),
    match_result_observation_id BIGINT REFERENCES match_result_observations(match_result_observation_id),
    settlement_status settlement_status NOT NULL DEFAULT 'PENDING',
    settled_at TIMESTAMPTZ,
    settlement_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE decision_clv (
    decision_id BIGINT PRIMARY KEY REFERENCES decisions(decision_id),
    price_taken NUMERIC(12, 6) NOT NULL CHECK (price_taken > 1),
    price_taken_at TIMESTAMPTZ NOT NULL,
    closing_same_book_price NUMERIC(12, 6) CHECK (closing_same_book_price IS NULL OR closing_same_book_price > 1),
    closing_sharp_price NUMERIC(12, 6) CHECK (closing_sharp_price IS NULL OR closing_sharp_price > 1),
    closing_margin NUMERIC(12, 8) CHECK (closing_margin IS NULL OR closing_margin > 0),
    closing_observed_at TIMESTAMPTZ,
    calculation_version TEXT NOT NULL CHECK (btrim(calculation_version) <> ''),
    clv_pct NUMERIC(12, 8),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (closing_observed_at IS NULL OR closing_observed_at >= price_taken_at)
);

CREATE TABLE data_health_checks (
    data_health_check_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    dataset_name TEXT NOT NULL CHECK (btrim(dataset_name) <> ''),
    checked_at TIMESTAMPTZ NOT NULL,
    latest_observation_at TIMESTAMPTZ,
    freshness_state freshness_state NOT NULL,
    coverage_ratio NUMERIC(6, 5) CHECK (coverage_ratio IS NULL OR (coverage_ratio >= 0 AND coverage_ratio <= 1)),
    unmatched_count INTEGER NOT NULL DEFAULT 0 CHECK (unmatched_count >= 0),
    error_count INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
    status health_status NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (latest_observation_at IS NULL OR latest_observation_at <= checked_at)
);

CREATE FUNCTION validate_identity_resolution_candidate_state(target_event_id BIGINT)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    target_status identity_resolution_status;
    candidate_count INTEGER;
BEGIN
    SELECT
        e.resolution_status,
        COUNT(c.identity_resolution_candidate_id)::INTEGER
    INTO target_status, candidate_count
    FROM identity_resolution_events e
    LEFT JOIN identity_resolution_candidates c
        ON c.identity_resolution_event_id = e.identity_resolution_event_id
    WHERE e.identity_resolution_event_id = target_event_id
    GROUP BY e.resolution_status;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    IF target_status = 'UNMATCHED' AND candidate_count <> 0 THEN
        RAISE EXCEPTION
            'UNMATCHED identity resolution events cannot retain candidates (event_id=%).',
            target_event_id;
    END IF;

    IF target_status = 'RESOLVED' AND candidate_count <> 0 THEN
        RAISE EXCEPTION
            'RESOLVED identity resolution events cannot retain candidates (event_id=%).',
            target_event_id;
    END IF;

    IF target_status = 'AMBIGUOUS' AND candidate_count < 2 THEN
        RAISE EXCEPTION
            'AMBIGUOUS identity resolution events require at least two candidates (event_id=%).',
            target_event_id;
    END IF;
END;
$$;

CREATE FUNCTION enforce_identity_resolution_candidate_state()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    target_event_id BIGINT;
    previous_event_id BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_event_id := OLD.identity_resolution_event_id;
    ELSE
        target_event_id := NEW.identity_resolution_event_id;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        previous_event_id := OLD.identity_resolution_event_id;
    END IF;

    PERFORM validate_identity_resolution_candidate_state(target_event_id);

    IF previous_event_id IS NOT NULL AND previous_event_id <> target_event_id THEN
        PERFORM validate_identity_resolution_candidate_state(previous_event_id);
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_identity_resolution_events_candidate_state
    AFTER INSERT OR UPDATE ON identity_resolution_events
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION enforce_identity_resolution_candidate_state();

CREATE CONSTRAINT TRIGGER trg_identity_resolution_candidates_candidate_state
    AFTER INSERT OR UPDATE OR DELETE ON identity_resolution_candidates
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION enforce_identity_resolution_candidate_state();

CREATE UNIQUE INDEX uq_match_source_ids_source_match
    ON match_source_ids (source_system, source_match_id)
    WHERE source_match_id IS NOT NULL;

CREATE UNIQUE INDEX uq_match_source_ids_record_hash
    ON match_source_ids (source_system, source_record_hash)
    WHERE source_match_id IS NULL AND source_record_hash IS NOT NULL;

CREATE UNIQUE INDEX uq_match_version_sources_source_match
    ON match_version_sources (
        source_system,
        source_match_id,
        match_version_id,
        source_observed_at
    )
    WHERE source_match_id IS NOT NULL;

CREATE UNIQUE INDEX uq_match_version_sources_record_hash
    ON match_version_sources (
        source_system,
        source_record_hash,
        match_version_id,
        source_observed_at
    )
    WHERE source_match_id IS NULL AND source_record_hash IS NOT NULL;

CREATE UNIQUE INDEX uq_match_stat_source_record
    ON match_stat_observations (
        source_system,
        source_record_id,
        match_id,
        player_id,
        observation_kind,
        source_observed_at
    )
    WHERE source_record_id IS NOT NULL;

CREATE UNIQUE INDEX uq_match_stat_source_hash
    ON match_stat_observations (
        source_system,
        source_record_hash,
        match_id,
        player_id,
        observation_kind,
        source_observed_at
    )
    WHERE source_record_id IS NULL AND source_record_hash IS NOT NULL;

CREATE UNIQUE INDEX uq_odds_snapshots_source_record
    ON odds_snapshots (
        source_system,
        source_snapshot_id,
        bookmaker_code,
        match_id,
        market_type,
        selection_type,
        line_value,
        source_observed_at
    ) NULLS NOT DISTINCT
    WHERE source_snapshot_id IS NOT NULL;

CREATE UNIQUE INDEX uq_odds_snapshots_source_hash
    ON odds_snapshots (
        source_system,
        source_record_hash,
        bookmaker_code,
        match_id,
        market_type,
        selection_type,
        line_value,
        source_observed_at
    ) NULLS NOT DISTINCT
    WHERE source_snapshot_id IS NULL AND source_record_hash IS NOT NULL;

CREATE UNIQUE INDEX uq_rating_snapshots_identity
    ON rating_snapshots (
        player_id,
        rating_metric,
        surface,
        model_version,
        feature_version,
        experiment_id,
        walk_forward_fold,
        variant_id,
        as_of
    ) NULLS NOT DISTINCT;

CREATE UNIQUE INDEX uq_predictions_identity
    ON predictions (
        match_id,
        agent_code,
        model_version,
        feature_version,
        COALESCE(experiment_id, ''),
        COALESCE(walk_forward_fold, -1),
        COALESCE(variant_id, ''),
        as_of
    );

CREATE UNIQUE INDEX uq_decisions_identity
    ON decisions (
        match_id,
        signal_type,
        model_version,
        feature_version,
        COALESCE(experiment_id, ''),
        COALESCE(walk_forward_fold, -1),
        COALESCE(variant_id, ''),
        as_of
    );

CREATE INDEX idx_match_versions_match_as_of
    ON match_versions (match_id, as_of DESC);

CREATE INDEX idx_match_versions_match_start
    ON match_versions (match_start_at);

CREATE INDEX idx_match_result_observations_match_as_of
    ON match_result_observations (match_id, as_of DESC);

CREATE INDEX idx_match_stat_observations_player_as_of
    ON match_stat_observations (player_id, as_of DESC);

CREATE INDEX idx_odds_snapshots_match_market_as_of
    ON odds_snapshots (match_id, market_type, source_observed_at DESC);

CREATE INDEX idx_rating_snapshots_player_as_of
    ON rating_snapshots (player_id, as_of DESC);

CREATE INDEX idx_predictions_match_as_of
    ON predictions (match_id, as_of DESC);

CREATE INDEX idx_decisions_match_as_of
    ON decisions (match_id, as_of DESC);

COMMIT;

BEGIN;

ALTER TYPE competition_tier
    ADD VALUE IF NOT EXISTS 'ATP_TOUR' BEFORE 'ATP_500';

COMMIT;

BEGIN;

ALTER TABLE match_versions
    ALTER COLUMN match_start_at DROP NOT NULL;

COMMIT;

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM predictions
        UNION ALL
        SELECT 1 FROM decisions
        UNION ALL
        SELECT 1 FROM decision_agents
    ) THEN
        RAISE EXCEPTION
            '0004_prediction_runs_and_policy_versions.up.sql requires predictions, decisions, and decision_agents to be empty because exact T-ED1 lineage cannot be reconstructed safely.';
    END IF;
END;
$$;

CREATE TABLE prediction_runs (
    prediction_run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_status TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    prediction_finalized_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    experiment_id TEXT,
    parent_prediction_run_id BIGINT REFERENCES prediction_runs(prediction_run_id),
    parent_relation_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_prediction_runs_status
        CHECK (run_status IN ('RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')),
    CONSTRAINT chk_prediction_runs_prediction_finalized_after_started
        CHECK (prediction_finalized_at IS NULL OR prediction_finalized_at >= started_at),
    CONSTRAINT chk_prediction_runs_prediction_finalized_after_as_of
        CHECK (prediction_finalized_at IS NULL OR prediction_finalized_at >= as_of),
    CONSTRAINT chk_prediction_runs_ended_after_started
        CHECK (ended_at IS NULL OR ended_at >= started_at),
    CONSTRAINT chk_prediction_runs_ended_after_prediction_finalized
        CHECK (prediction_finalized_at IS NULL OR ended_at IS NULL OR ended_at >= prediction_finalized_at),
    CONSTRAINT chk_prediction_runs_running_has_no_ended_at
        CHECK (run_status <> 'RUNNING' OR ended_at IS NULL),
    CONSTRAINT chk_prediction_runs_terminal_has_ended_at
        CHECK (run_status = 'RUNNING' OR ended_at IS NOT NULL),
    CONSTRAINT chk_prediction_runs_parent_pairing
        CHECK (
            (parent_prediction_run_id IS NULL AND parent_relation_type IS NULL)
            OR (parent_prediction_run_id IS NOT NULL AND parent_relation_type IS NOT NULL)
        ),
    CONSTRAINT chk_prediction_runs_parent_relation_type
        CHECK (parent_relation_type IS NULL OR parent_relation_type IN ('RETRY', 'RECALCULATION')),
    CONSTRAINT chk_prediction_runs_not_self_parent
        CHECK (parent_prediction_run_id IS NULL OR parent_prediction_run_id <> prediction_run_id),
    CONSTRAINT uq_prediction_runs_id_as_of
        UNIQUE (prediction_run_id, as_of),
    CONSTRAINT uq_prediction_runs_id_prediction_finalized_at
        UNIQUE (prediction_run_id, prediction_finalized_at)
);

CREATE INDEX idx_prediction_runs_status_started_at
    ON prediction_runs (run_status, started_at DESC);

CREATE INDEX idx_prediction_runs_parent_prediction_run_id
    ON prediction_runs (parent_prediction_run_id);

CREATE TABLE decision_policy_versions (
    decision_policy_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_key TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    consensus_mode TEXT NOT NULL,
    allow_agent3_abstention BOOLEAN NOT NULL,
    max_probability_spread NUMERIC(8, 6) NOT NULL,
    min_active_agent_probability NUMERIC(8, 6) NOT NULL,
    requires_data_sufficiency BOOLEAN NOT NULL,
    min_recent_matches_with_serve_stats INTEGER NOT NULL,
    odds_feed_max_staleness_minutes INTEGER NOT NULL,
    match_data_max_staleness_hours INTEGER NOT NULL,
    ratings_max_staleness_hours INTEGER NOT NULL,
    fixture_feed_max_staleness_hours INTEGER NOT NULL,
    atp_main_all_agents_min_edge_pct NUMERIC(8, 6) NOT NULL,
    atp_main_agent3_abstain_min_edge_pct NUMERIC(8, 6) NOT NULL,
    challenger_min_edge_pct NUMERIC(8, 6) NOT NULL,
    default_devig_method TEXT NOT NULL,
    sharp_reference_policy_code TEXT NOT NULL,
    veto_policy_code TEXT NOT NULL,
    publication_requires_min_acceptable_odds BOOLEAN NOT NULL,
    publish_retractions_required BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_decision_policy_versions_identity
        UNIQUE (policy_key, policy_version),
    CONSTRAINT chk_decision_policy_versions_consensus_mode
        CHECK (consensus_mode = 'UNANIMOUS_ACTIVE_AGENTS'),
    CONSTRAINT chk_decision_policy_versions_allow_agent3_abstention
        CHECK (allow_agent3_abstention = TRUE),
    CONSTRAINT chk_decision_policy_versions_max_probability_spread
        CHECK (max_probability_spread >= 0 AND max_probability_spread <= 1),
    CONSTRAINT chk_decision_policy_versions_min_active_agent_probability
        CHECK (min_active_agent_probability >= 0 AND min_active_agent_probability <= 1),
    CONSTRAINT chk_decision_policy_versions_min_recent_matches
        CHECK (min_recent_matches_with_serve_stats >= 0),
    CONSTRAINT chk_decision_policy_versions_odds_feed_staleness
        CHECK (odds_feed_max_staleness_minutes >= 0),
    CONSTRAINT chk_decision_policy_versions_match_data_staleness
        CHECK (match_data_max_staleness_hours >= 0),
    CONSTRAINT chk_decision_policy_versions_ratings_staleness
        CHECK (ratings_max_staleness_hours >= 0),
    CONSTRAINT chk_decision_policy_versions_fixture_feed_staleness
        CHECK (fixture_feed_max_staleness_hours >= 0),
    CONSTRAINT chk_decision_policy_versions_atp_main_all_agents_min_edge
        CHECK (atp_main_all_agents_min_edge_pct >= 0),
    CONSTRAINT chk_decision_policy_versions_atp_main_agent3_abstain_min_edge
        CHECK (atp_main_agent3_abstain_min_edge_pct >= 0),
    CONSTRAINT chk_decision_policy_versions_challenger_min_edge
        CHECK (challenger_min_edge_pct >= 0),
    CONSTRAINT chk_decision_policy_versions_default_devig_method
        CHECK (default_devig_method IN ('POWER', 'SHIN'))
);

CREATE TABLE agent_config_versions (
    agent_config_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_code agent_code NOT NULL,
    config_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    calibration_version TEXT,
    parameter_schema_version TEXT NOT NULL,
    config_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_agent_config_versions_identity
        UNIQUE (agent_code, config_version),
    CONSTRAINT uq_agent_config_versions_lineage_identity
        UNIQUE (agent_config_version_id, agent_code, model_version, feature_version),
    CONSTRAINT chk_agent_config_versions_config_version
        CHECK (btrim(config_version) <> ''),
    CONSTRAINT chk_agent_config_versions_model_version
        CHECK (btrim(model_version) <> ''),
    CONSTRAINT chk_agent_config_versions_feature_version
        CHECK (btrim(feature_version) <> ''),
    CONSTRAINT chk_agent_config_versions_parameter_schema_version
        CHECK (btrim(parameter_schema_version) <> ''),
    CONSTRAINT chk_agent_config_versions_config_payload_object
        CHECK (jsonb_typeof(config_payload) = 'object')
);

CREATE INDEX idx_agent_config_versions_agent_created_at
    ON agent_config_versions (agent_code, created_at DESC);

DROP INDEX IF EXISTS uq_predictions_identity;
DROP INDEX IF EXISTS uq_decisions_identity;

ALTER TABLE predictions
    ADD COLUMN prediction_run_id BIGINT NOT NULL,
    ADD COLUMN agent_config_version_id BIGINT NOT NULL,
    ADD CONSTRAINT fk_predictions_prediction_run
        FOREIGN KEY (prediction_run_id)
        REFERENCES prediction_runs(prediction_run_id),
    ADD CONSTRAINT fk_predictions_prediction_run_as_of
        FOREIGN KEY (prediction_run_id, as_of)
        REFERENCES prediction_runs(prediction_run_id, as_of),
    ADD CONSTRAINT fk_predictions_prediction_run_finalization
        FOREIGN KEY (prediction_run_id, prediction_locked_at)
        REFERENCES prediction_runs(prediction_run_id, prediction_finalized_at),
    ADD CONSTRAINT fk_predictions_agent_config_lineage
        FOREIGN KEY (agent_config_version_id, agent_code, model_version, feature_version)
        REFERENCES agent_config_versions(
            agent_config_version_id,
            agent_code,
            model_version,
            feature_version
        ),
    ADD CONSTRAINT uq_predictions_run_match_agent
        UNIQUE (prediction_run_id, match_id, agent_code),
    ADD CONSTRAINT uq_predictions_lineage_identity
        UNIQUE (prediction_id, prediction_run_id, match_id, agent_code);

ALTER TABLE decisions
    ADD COLUMN prediction_run_id BIGINT NOT NULL,
    ADD COLUMN decision_policy_version_id BIGINT NOT NULL,
    ADD CONSTRAINT fk_decisions_prediction_run
        FOREIGN KEY (prediction_run_id)
        REFERENCES prediction_runs(prediction_run_id),
    ADD CONSTRAINT fk_decisions_policy_version
        FOREIGN KEY (decision_policy_version_id)
        REFERENCES decision_policy_versions(decision_policy_version_id),
    ADD CONSTRAINT uq_decisions_lineage_identity
        UNIQUE (decision_id, prediction_run_id, match_id);

ALTER TABLE decision_agents
    ADD COLUMN prediction_run_id BIGINT NOT NULL,
    ADD COLUMN match_id BIGINT NOT NULL,
    ADD COLUMN prediction_id BIGINT,
    ADD CONSTRAINT fk_decision_agents_decision_lineage
        FOREIGN KEY (decision_id, prediction_run_id, match_id)
        REFERENCES decisions(decision_id, prediction_run_id, match_id),
    ADD CONSTRAINT fk_decision_agents_prediction_lineage
        FOREIGN KEY (prediction_id, prediction_run_id, match_id, agent_code)
        REFERENCES predictions(prediction_id, prediction_run_id, match_id, agent_code),
    ADD CONSTRAINT chk_decision_agents_participation_nullability
        CHECK (
            (participation_status = 'ACTIVE' AND prediction_id IS NOT NULL AND proposed_selection_side IS NOT NULL AND proposed_probability IS NOT NULL)
            OR (participation_status = 'ABSTAINED' AND prediction_id IS NOT NULL AND proposed_selection_side IS NULL AND proposed_probability IS NULL)
            OR (participation_status = 'INACTIVE' AND prediction_id IS NULL AND proposed_selection_side IS NULL AND proposed_probability IS NULL)
        );

CREATE OR REPLACE FUNCTION validate_decision_agent_lineage_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    linked_prediction_status prediction_status;
    linked_selection_side SMALLINT;
    linked_win_probability NUMERIC(8, 6);
BEGIN
    IF TG_TABLE_NAME = 'decision_agents' THEN
        IF NEW.participation_status = 'ACTIVE' THEN
            SELECT p.prediction_status, p.selection_side, p.win_probability
            INTO linked_prediction_status, linked_selection_side, linked_win_probability
            FROM predictions p
            WHERE p.prediction_id = NEW.prediction_id
              AND p.prediction_run_id = NEW.prediction_run_id
              AND p.match_id = NEW.match_id
              AND p.agent_code = NEW.agent_code;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'decision_agents ACTIVE row must reference an existing aligned prediction row.';
            END IF;

            IF linked_prediction_status <> 'PREDICTED' THEN
                RAISE EXCEPTION
                    'decision_agents ACTIVE rows must reference predictions with prediction_status = PREDICTED.';
            END IF;

            IF NEW.proposed_selection_side IS DISTINCT FROM linked_selection_side THEN
                RAISE EXCEPTION
                    'decision_agents ACTIVE proposed_selection_side must equal predictions.selection_side.';
            END IF;

            IF NEW.proposed_probability IS DISTINCT FROM linked_win_probability THEN
                RAISE EXCEPTION
                    'decision_agents ACTIVE proposed_probability must equal predictions.win_probability.';
            END IF;
        ELSIF NEW.participation_status = 'ABSTAINED' THEN
            SELECT p.prediction_status
            INTO linked_prediction_status
            FROM predictions p
            WHERE p.prediction_id = NEW.prediction_id
              AND p.prediction_run_id = NEW.prediction_run_id
              AND p.match_id = NEW.match_id
              AND p.agent_code = NEW.agent_code;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'decision_agents ABSTAINED row must reference an existing aligned prediction row.';
            END IF;

            IF linked_prediction_status <> 'ABSTAINED' THEN
                RAISE EXCEPTION
                    'decision_agents ABSTAINED rows must reference predictions with prediction_status = ABSTAINED.';
            END IF;
        ELSIF NEW.participation_status = 'INACTIVE' THEN
            IF NEW.prediction_id IS NOT NULL THEN
                RAISE EXCEPTION
                    'decision_agents INACTIVE rows must not reference a prediction.';
            END IF;

            IF NEW.proposed_selection_side IS NOT NULL OR NEW.proposed_probability IS NOT NULL THEN
                RAISE EXCEPTION
                    'decision_agents INACTIVE rows must not store proposal audit values.';
            END IF;
        END IF;

        RETURN NEW;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM decision_agents da
        WHERE da.prediction_id = NEW.prediction_id
          AND (
              (da.participation_status = 'ACTIVE' AND (
                  NEW.prediction_status <> 'PREDICTED'
                  OR da.proposed_selection_side IS DISTINCT FROM NEW.selection_side
                  OR da.proposed_probability IS DISTINCT FROM NEW.win_probability
              ))
              OR (da.participation_status = 'ABSTAINED' AND NEW.prediction_status <> 'ABSTAINED')
          )
    ) THEN
        RAISE EXCEPTION
            'predictions update would violate decision_agents lineage integrity.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_decision_agents_lineage_integrity
AFTER INSERT OR UPDATE ON decision_agents
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION validate_decision_agent_lineage_integrity();

CREATE CONSTRAINT TRIGGER trg_predictions_decision_agent_lineage_integrity
AFTER UPDATE OF prediction_status, selection_side, win_probability ON predictions
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION validate_decision_agent_lineage_integrity();

COMMIT;
