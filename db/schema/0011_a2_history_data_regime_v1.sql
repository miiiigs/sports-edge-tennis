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

BEGIN;

ALTER TABLE predictions
    DROP CONSTRAINT fk_predictions_prediction_run_finalization;

ALTER TABLE predictions
    ADD CONSTRAINT fk_predictions_prediction_run_finalization
        FOREIGN KEY (prediction_run_id, prediction_locked_at)
        REFERENCES prediction_runs(prediction_run_id, prediction_finalized_at)
        DEFERRABLE INITIALLY IMMEDIATE;

CREATE OR REPLACE FUNCTION raise_append_only_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION USING
        MESSAGE = format(
            '%I is append-only under 0005_append_only_enforcement_and_lock_guards; %s is not allowed.',
            TG_TABLE_NAME,
            TG_OP
        );
END;
$$;

CREATE OR REPLACE FUNCTION validate_prediction_run_lifecycle()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['run_status', 'prediction_finalized_at', 'ended_at'])
        IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['run_status', 'prediction_finalized_at', 'ended_at']) THEN
        RAISE EXCEPTION
            'prediction_runs identity fields are immutable under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    IF OLD.run_status <> 'RUNNING' THEN
        RAISE EXCEPTION
            'prediction_runs terminal rows are immutable under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    IF NEW.run_status = 'RUNNING' THEN
        IF OLD.prediction_finalized_at IS NULL
           AND NEW.prediction_finalized_at IS NOT NULL
           AND OLD.ended_at IS NULL
           AND NEW.ended_at IS NULL THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'prediction_runs only allows RUNNING -> RUNNING finalization updates that set prediction_finalized_at once while ended_at stays NULL.';
    END IF;

    IF NEW.run_status = 'COMPLETED' THEN
        IF OLD.ended_at IS NULL
           AND NEW.ended_at IS NOT NULL
           AND (
               (OLD.prediction_finalized_at IS NOT NULL AND NEW.prediction_finalized_at = OLD.prediction_finalized_at)
               OR (OLD.prediction_finalized_at IS NULL AND NEW.prediction_finalized_at IS NOT NULL)
           ) THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'prediction_runs RUNNING -> COMPLETED requires ended_at NULL -> timestamp and prediction_finalized_at either unchanged after prior finalization or set in the same terminal update.';
    END IF;

    IF NEW.run_status IN ('FAILED', 'CANCELLED') THEN
        IF OLD.ended_at IS NULL
           AND NEW.ended_at IS NOT NULL
           AND (
               (OLD.prediction_finalized_at IS NULL)
               OR (NEW.prediction_finalized_at = OLD.prediction_finalized_at)
           ) THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'prediction_runs RUNNING -> FAILED/CANCELLED requires ended_at NULL -> timestamp and does not allow prediction_finalized_at to be cleared or rewritten.';
    END IF;

    RAISE EXCEPTION
        'prediction_runs allows only RUNNING -> RUNNING finalization and RUNNING -> COMPLETED/FAILED/CANCELLED terminal transitions under 0005_append_only_enforcement_and_lock_guards.';
END;
$$;

CREATE OR REPLACE FUNCTION validate_ingest_run_lifecycle()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY[
            'finished_at',
            'status',
            'rows_seen',
            'rows_inserted',
            'rows_updated',
            'rows_rejected',
            'rows_unmatched',
            'error_summary'
        ])
        IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY[
            'finished_at',
            'status',
            'rows_seen',
            'rows_inserted',
            'rows_updated',
            'rows_rejected',
            'rows_unmatched',
            'error_summary'
        ]) THEN
        RAISE EXCEPTION
            'ingest_runs source identity fields are immutable under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    IF OLD.status <> 'RUNNING' THEN
        RAISE EXCEPTION
            'ingest_runs terminal rows are immutable under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    IF NEW.status NOT IN ('COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED') THEN
        RAISE EXCEPTION
            'ingest_runs only allows one terminal RUNNING -> COMPLETED/PARTIAL/FAILED/CANCELLED update under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    IF OLD.finished_at IS NOT NULL OR NEW.finished_at IS NULL THEN
        RAISE EXCEPTION
            'ingest_runs terminalization requires finished_at NULL -> timestamp in the same update.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_decision_publication_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - 'published_at') IS DISTINCT FROM (to_jsonb(OLD) - 'published_at') THEN
        RAISE EXCEPTION
            'decisions rows are immutable under 0005_append_only_enforcement_and_lock_guards except for one-way published_at publication.';
    END IF;

    IF OLD.published_at IS NOT NULL THEN
        RAISE EXCEPTION
            'decisions.published_at may only transition from NULL to a timestamp once.';
    END IF;

    IF NEW.published_at IS NULL THEN
        RAISE EXCEPTION
            'decisions UPDATE is allowed only for published_at NULL -> timestamp.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION guard_prediction_parent_run_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    parent_run_status prediction_runs.run_status%TYPE;
    parent_prediction_finalized_at prediction_runs.prediction_finalized_at%TYPE;
BEGIN
    SELECT run_status, prediction_finalized_at
    INTO parent_run_status, parent_prediction_finalized_at
    FROM prediction_runs
    WHERE prediction_run_id = NEW.prediction_run_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'predictions insert requires an existing prediction_runs parent row (prediction_run_id=%).',
            NEW.prediction_run_id;
    END IF;

    IF parent_run_status <> 'RUNNING' OR parent_prediction_finalized_at IS NOT NULL THEN
        RAISE EXCEPTION
            'predictions insert requires parent prediction_runs row to be RUNNING and unfinalized under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_prediction_runs_lifecycle_guard
BEFORE UPDATE ON prediction_runs
FOR EACH ROW
EXECUTE FUNCTION validate_prediction_run_lifecycle();

CREATE TRIGGER trg_prediction_runs_delete_blocker
BEFORE DELETE ON prediction_runs
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_predictions_parent_run_lock_guard
BEFORE INSERT ON predictions
FOR EACH ROW
EXECUTE FUNCTION guard_prediction_parent_run_insert();

CREATE TRIGGER trg_predictions_append_only_mutation
BEFORE UPDATE OR DELETE ON predictions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_decision_policy_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON decision_policy_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_agent_config_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON agent_config_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_decisions_publication_guard
BEFORE UPDATE ON decisions
FOR EACH ROW
EXECUTE FUNCTION validate_decision_publication_update();

CREATE TRIGGER trg_decisions_delete_blocker
BEFORE DELETE ON decisions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_decision_agents_append_only_mutation
BEFORE UPDATE OR DELETE ON decision_agents
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_rating_snapshots_append_only_mutation
BEFORE UPDATE OR DELETE ON rating_snapshots
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_odds_snapshots_append_only_mutation
BEFORE UPDATE OR DELETE ON odds_snapshots
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_match_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON match_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_match_source_ids_append_only_mutation
BEFORE UPDATE OR DELETE ON match_source_ids
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_match_version_sources_append_only_mutation
BEFORE UPDATE OR DELETE ON match_version_sources
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_match_result_observations_append_only_mutation
BEFORE UPDATE OR DELETE ON match_result_observations
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_match_stat_observations_append_only_mutation
BEFORE UPDATE OR DELETE ON match_stat_observations
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_player_source_ids_append_only_mutation
BEFORE UPDATE OR DELETE ON player_source_ids
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_identity_resolution_events_append_only_mutation
BEFORE UPDATE OR DELETE ON identity_resolution_events
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_identity_resolution_candidates_append_only_mutation
BEFORE UPDATE OR DELETE ON identity_resolution_candidates
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_tournament_source_ids_append_only_mutation
BEFORE UPDATE OR DELETE ON tournament_source_ids
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_matches_append_only_mutation
BEFORE UPDATE OR DELETE ON matches
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_data_health_checks_append_only_mutation
BEFORE UPDATE OR DELETE ON data_health_checks
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_ingest_runs_lifecycle_guard
BEFORE UPDATE ON ingest_runs
FOR EACH ROW
EXECUTE FUNCTION validate_ingest_run_lifecycle();

CREATE TRIGGER trg_ingest_runs_delete_blocker
BEFORE DELETE ON ingest_runs
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;

BEGIN;

DROP TRIGGER IF EXISTS trg_predictions_decision_agent_lineage_integrity ON predictions;
DROP TRIGGER IF EXISTS trg_decision_agents_lineage_integrity ON decision_agents;
DROP TRIGGER IF EXISTS trg_decision_agents_append_only_mutation ON decision_agents;
DROP TRIGGER IF EXISTS trg_predictions_append_only_mutation ON predictions;
DROP TRIGGER IF EXISTS trg_predictions_parent_run_lock_guard ON predictions;
DROP TRIGGER IF EXISTS trg_prediction_runs_lifecycle_guard ON prediction_runs;

DROP FUNCTION IF EXISTS validate_decision_agent_lineage_integrity();
DROP FUNCTION IF EXISTS guard_prediction_parent_run_insert();
DROP FUNCTION IF EXISTS validate_prediction_run_lifecycle();

ALTER TABLE prediction_runs
    ADD COLUMN prediction_contract_version TEXT;

UPDATE prediction_runs
SET prediction_contract_version = 'legacy_prediction_ledger_v1'
WHERE prediction_contract_version IS NULL;

ALTER TABLE prediction_runs
    ALTER COLUMN prediction_contract_version SET NOT NULL,
    ADD CONSTRAINT chk_prediction_runs_contract_version
        CHECK (prediction_contract_version IN ('legacy_prediction_ledger_v1', 'predictive_agent_result_v1'));

ALTER TABLE predictions
    ALTER COLUMN win_probability TYPE NUMERIC(51, 50),
    ADD COLUMN probability_representation TEXT;

UPDATE predictions
SET probability_representation = 'legacy_persisted_win_probability_v1'
WHERE prediction_status = 'PREDICTED'
  AND probability_representation IS NULL;

ALTER TABLE predictions
    ADD CONSTRAINT chk_predictions_probability_representation
        CHECK (
            (prediction_status = 'PREDICTED' AND probability_representation IS NOT NULL AND btrim(probability_representation) <> '')
            OR (prediction_status = 'ABSTAINED' AND probability_representation IS NULL)
        ),
    ADD CONSTRAINT chk_predictions_live_probability_representation
        CHECK (
            probability_representation IS NULL
            OR probability_representation IN (
                'agent1_raw_probability_v1',
                'agent2_raw_probability_v1',
                'agent3_raw_probability_v1',
                'legacy_persisted_win_probability_v1'
            )
        );

ALTER TABLE predictions
    ALTER COLUMN probability_representation SET DEFAULT NULL;

ALTER TABLE decision_agents
    ALTER COLUMN proposed_probability TYPE NUMERIC(51, 50),
    ADD COLUMN probability_representation TEXT;

UPDATE decision_agents da
SET probability_representation = p.probability_representation
FROM predictions p
WHERE da.participation_status = 'ACTIVE'
  AND da.prediction_id = p.prediction_id
  AND da.prediction_run_id = p.prediction_run_id
  AND da.match_id = p.match_id
  AND da.agent_code = p.agent_code
  AND da.probability_representation IS NULL;

ALTER TABLE decision_agents
    ADD CONSTRAINT chk_decision_agents_probability_representation
        CHECK (
            (participation_status = 'ACTIVE' AND probability_representation IS NOT NULL AND btrim(probability_representation) <> '')
            OR (participation_status IN ('ABSTAINED', 'INACTIVE') AND probability_representation IS NULL)
        ),
    ADD CONSTRAINT chk_decision_agents_live_probability_representation
        CHECK (
            probability_representation IS NULL
            OR probability_representation IN (
                'agent1_raw_probability_v1',
                'agent2_raw_probability_v1',
                'agent3_raw_probability_v1',
                'legacy_persisted_win_probability_v1'
            )
        );

ALTER TABLE decision_agents
    DROP CONSTRAINT chk_decision_agents_participation_nullability,
    ADD CONSTRAINT chk_decision_agents_participation_nullability
        CHECK (
            (
                participation_status = 'ACTIVE'
                AND prediction_id IS NOT NULL
                AND proposed_selection_side IS NOT NULL
                AND proposed_probability IS NOT NULL
                AND probability_representation IS NOT NULL
            )
            OR (
                participation_status = 'ABSTAINED'
                AND prediction_id IS NOT NULL
                AND proposed_selection_side IS NULL
                AND proposed_probability IS NULL
                AND probability_representation IS NULL
            )
            OR (
                participation_status = 'INACTIVE'
                AND prediction_id IS NULL
                AND proposed_selection_side IS NULL
                AND proposed_probability IS NULL
                AND probability_representation IS NULL
            )
        );

CREATE TABLE prediction_run_targets (
    prediction_run_id BIGINT NOT NULL REFERENCES prediction_runs(prediction_run_id),
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (prediction_run_id, match_id)
);

CREATE INDEX idx_prediction_run_targets_match
    ON prediction_run_targets (match_id);

CREATE INDEX idx_predictions_run_representation
    ON predictions (prediction_run_id, probability_representation);

CREATE INDEX idx_decision_agents_run_representation
    ON decision_agents (prediction_run_id, probability_representation);

CREATE OR REPLACE FUNCTION validate_prediction_run_v1_completeness(target_prediction_run_id BIGINT)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM predictions p
        LEFT JOIN prediction_run_targets t
          ON t.prediction_run_id = p.prediction_run_id
         AND t.match_id = p.match_id
        WHERE p.prediction_run_id = target_prediction_run_id
          AND t.prediction_run_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'predictive_agent_result_v1 runs cannot finalize with predictions outside prediction_run_targets.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM prediction_run_targets t
        CROSS JOIN (
            VALUES
                ('AGENT_1_SURFACE_ELO'::agent_code),
                ('AGENT_2_HIERARCHICAL_MARKOV'::agent_code),
                ('AGENT_3_INTRANSITIVITY'::agent_code)
        ) expected(agent_code)
        WHERE t.prediction_run_id = target_prediction_run_id
          AND NOT EXISTS (
              SELECT 1
              FROM predictions p
              WHERE p.prediction_run_id = t.prediction_run_id
                AND p.match_id = t.match_id
                AND p.agent_code = expected.agent_code
          )
    ) THEN
        RAISE EXCEPTION
            'predictive_agent_result_v1 runs require exactly the canonical three-agent prediction set for every target.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM predictions p
        WHERE p.prediction_run_id = target_prediction_run_id
          AND p.agent_code NOT IN (
              'AGENT_1_SURFACE_ELO',
              'AGENT_2_HIERARCHICAL_MARKOV',
              'AGENT_3_INTRANSITIVITY'
          )
    ) THEN
        RAISE EXCEPTION
            'predictive_agent_result_v1 runs cannot finalize with non-canonical predictive agents.';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION validate_prediction_run_lifecycle()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['run_status', 'prediction_finalized_at', 'ended_at'])
        IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['run_status', 'prediction_finalized_at', 'ended_at']) THEN
        RAISE EXCEPTION
            'prediction_runs identity fields are immutable under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    IF OLD.run_status <> 'RUNNING' THEN
        RAISE EXCEPTION
            'prediction_runs terminal rows are immutable under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    IF NEW.prediction_contract_version = 'predictive_agent_result_v1'
       AND OLD.prediction_finalized_at IS NULL
       AND NEW.prediction_finalized_at IS NOT NULL THEN
        PERFORM validate_prediction_run_v1_completeness(NEW.prediction_run_id);
    END IF;

    IF NEW.run_status = 'RUNNING' THEN
        IF OLD.prediction_finalized_at IS NULL
           AND NEW.prediction_finalized_at IS NOT NULL
           AND OLD.ended_at IS NULL
           AND NEW.ended_at IS NULL THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'prediction_runs only allows RUNNING -> RUNNING finalization updates that set prediction_finalized_at once while ended_at stays NULL.';
    END IF;

    IF NEW.run_status = 'COMPLETED' THEN
        IF OLD.ended_at IS NULL
           AND NEW.ended_at IS NOT NULL
           AND (
               (OLD.prediction_finalized_at IS NOT NULL AND NEW.prediction_finalized_at = OLD.prediction_finalized_at)
               OR (OLD.prediction_finalized_at IS NULL AND NEW.prediction_finalized_at IS NOT NULL)
           ) THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'prediction_runs RUNNING -> COMPLETED requires ended_at NULL -> timestamp and prediction_finalized_at either unchanged after prior finalization or set in the same terminal update.';
    END IF;

    IF NEW.run_status IN ('FAILED', 'CANCELLED') THEN
        IF OLD.ended_at IS NULL
           AND NEW.ended_at IS NOT NULL
           AND (
               (OLD.prediction_finalized_at IS NULL)
               OR (NEW.prediction_finalized_at = OLD.prediction_finalized_at)
           ) THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'prediction_runs RUNNING -> FAILED/CANCELLED requires ended_at NULL -> timestamp and does not allow prediction_finalized_at to be cleared or rewritten.';
    END IF;

    RAISE EXCEPTION
        'prediction_runs allows only RUNNING -> RUNNING finalization and RUNNING -> COMPLETED/FAILED/CANCELLED terminal transitions under 0005_append_only_enforcement_and_lock_guards.';
END;
$$;

CREATE OR REPLACE FUNCTION validate_prediction_run_target_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    parent_run_status prediction_runs.run_status%TYPE;
    parent_prediction_finalized_at prediction_runs.prediction_finalized_at%TYPE;
    parent_contract_version prediction_runs.prediction_contract_version%TYPE;
BEGIN
    SELECT run_status, prediction_finalized_at, prediction_contract_version
    INTO parent_run_status, parent_prediction_finalized_at, parent_contract_version
    FROM prediction_runs
    WHERE prediction_run_id = NEW.prediction_run_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'prediction_run_targets insert requires an existing prediction_runs parent row.';
    END IF;

    IF parent_contract_version <> 'predictive_agent_result_v1' THEN
        RAISE EXCEPTION
            'prediction_run_targets are valid only for predictive_agent_result_v1 runs.';
    END IF;

    IF parent_run_status <> 'RUNNING' OR parent_prediction_finalized_at IS NOT NULL THEN
        RAISE EXCEPTION
            'prediction_run_targets insert requires parent prediction_runs row to be RUNNING and unfinalized.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION expected_probability_representation(
    target_agent_code agent_code,
    target_contract_version TEXT,
    target_prediction_status prediction_status
)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
BEGIN
    IF target_prediction_status = 'ABSTAINED' THEN
        RETURN NULL;
    END IF;

    IF target_contract_version = 'legacy_prediction_ledger_v1' THEN
        RETURN 'legacy_persisted_win_probability_v1';
    END IF;

    IF target_contract_version = 'predictive_agent_result_v1' THEN
        CASE target_agent_code
            WHEN 'AGENT_1_SURFACE_ELO' THEN RETURN 'agent1_raw_probability_v1';
            WHEN 'AGENT_2_HIERARCHICAL_MARKOV' THEN RETURN 'agent2_raw_probability_v1';
            WHEN 'AGENT_3_INTRANSITIVITY' THEN RETURN 'agent3_raw_probability_v1';
        END CASE;
    END IF;

    RAISE EXCEPTION 'unsupported prediction contract version %', target_contract_version;
END;
$$;

CREATE OR REPLACE FUNCTION guard_prediction_parent_run_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    parent_run_status prediction_runs.run_status%TYPE;
    parent_prediction_finalized_at prediction_runs.prediction_finalized_at%TYPE;
    parent_contract_version prediction_runs.prediction_contract_version%TYPE;
    expected_representation TEXT;
BEGIN
    SELECT run_status, prediction_finalized_at, prediction_contract_version
    INTO parent_run_status, parent_prediction_finalized_at, parent_contract_version
    FROM prediction_runs
    WHERE prediction_run_id = NEW.prediction_run_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'predictions insert requires an existing prediction_runs parent row (prediction_run_id=%).',
            NEW.prediction_run_id;
    END IF;

    IF parent_run_status <> 'RUNNING' OR parent_prediction_finalized_at IS NOT NULL THEN
        RAISE EXCEPTION
            'predictions insert requires parent prediction_runs row to be RUNNING and unfinalized under 0005_append_only_enforcement_and_lock_guards.';
    END IF;

    expected_representation := expected_probability_representation(
        NEW.agent_code,
        parent_contract_version,
        NEW.prediction_status
    );

    IF parent_contract_version = 'legacy_prediction_ledger_v1'
       AND NEW.prediction_status = 'PREDICTED'
       AND NEW.probability_representation IS NULL THEN
        NEW.probability_representation := expected_representation;
    END IF;

    IF NEW.probability_representation IS DISTINCT FROM expected_representation THEN
        RAISE EXCEPTION
            'predictions probability_representation does not match agent, status, and prediction contract.';
    END IF;

    IF parent_contract_version = 'predictive_agent_result_v1' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM prediction_run_targets t
            WHERE t.prediction_run_id = NEW.prediction_run_id
              AND t.match_id = NEW.match_id
        ) THEN
            RAISE EXCEPTION
                'predictive_agent_result_v1 predictions must reference a prediction_run_targets row.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION populate_decision_agent_probability_representation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    linked_probability_representation TEXT;
BEGIN
    IF NEW.participation_status = 'ACTIVE'
       AND NEW.probability_representation IS NULL
       AND NEW.prediction_id IS NOT NULL THEN
        SELECT p.probability_representation
        INTO linked_probability_representation
        FROM predictions p
        WHERE p.prediction_id = NEW.prediction_id
          AND p.prediction_run_id = NEW.prediction_run_id
          AND p.match_id = NEW.match_id
          AND p.agent_code = NEW.agent_code;

        NEW.probability_representation := linked_probability_representation;
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_decision_agent_lineage_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    linked_prediction_status prediction_status;
    linked_selection_side SMALLINT;
    linked_win_probability NUMERIC(51, 50);
    linked_probability_representation TEXT;
BEGIN
    IF TG_TABLE_NAME = 'decision_agents' THEN
        IF NEW.participation_status = 'ACTIVE' THEN
            SELECT p.prediction_status, p.selection_side, p.win_probability, p.probability_representation
            INTO linked_prediction_status, linked_selection_side, linked_win_probability, linked_probability_representation
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

            IF NEW.probability_representation IS DISTINCT FROM linked_probability_representation THEN
                RAISE EXCEPTION
                    'decision_agents ACTIVE probability_representation must equal predictions.probability_representation.';
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

            IF NEW.proposed_selection_side IS NOT NULL
               OR NEW.proposed_probability IS NOT NULL
               OR NEW.probability_representation IS NOT NULL THEN
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
                  OR da.probability_representation IS DISTINCT FROM NEW.probability_representation
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

CREATE TRIGGER trg_prediction_runs_lifecycle_guard
BEFORE UPDATE ON prediction_runs
FOR EACH ROW
EXECUTE FUNCTION validate_prediction_run_lifecycle();

CREATE TRIGGER trg_prediction_run_targets_parent_run_lock_guard
BEFORE INSERT ON prediction_run_targets
FOR EACH ROW
EXECUTE FUNCTION validate_prediction_run_target_insert();

CREATE TRIGGER trg_prediction_run_targets_append_only_mutation
BEFORE UPDATE OR DELETE ON prediction_run_targets
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_predictions_parent_run_lock_guard
BEFORE INSERT ON predictions
FOR EACH ROW
EXECUTE FUNCTION guard_prediction_parent_run_insert();

CREATE TRIGGER trg_predictions_append_only_mutation
BEFORE UPDATE OR DELETE ON predictions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_decision_agents_probability_representation_default
BEFORE INSERT ON decision_agents
FOR EACH ROW
EXECUTE FUNCTION populate_decision_agent_probability_representation();

CREATE CONSTRAINT TRIGGER trg_decision_agents_lineage_integrity
AFTER INSERT OR UPDATE ON decision_agents
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION validate_decision_agent_lineage_integrity();

CREATE CONSTRAINT TRIGGER trg_predictions_decision_agent_lineage_integrity
AFTER UPDATE OF prediction_status, selection_side, win_probability, probability_representation ON predictions
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION validate_decision_agent_lineage_integrity();

CREATE TRIGGER trg_decision_agents_append_only_mutation
BEFORE UPDATE OR DELETE ON decision_agents
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;


BEGIN;

CREATE TYPE manager_consensus_status AS ENUM ('CONSENSUS', 'NO_BET');

CREATE TYPE manager_reason_code AS ENUM (
    'NONE',
    'DIRECTIONAL_DISAGREEMENT',
    'THREE_AGENT_SPREAD_EXCEEDED',
    'AGENT3_ABSTENTION_SPREAD_EXCEEDED',
    'ACTIVE_PROBABILITY_BELOW_MINIMUM',
    'UNSUPPORTED_ABSTENTION_CONFIGURATION',
    'TWO_OR_MORE_ABSTENTIONS'
);

CREATE TABLE manager_policy_versions (
    manager_policy_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_key TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    consensus_mode TEXT NOT NULL,
    allow_agent3_abstention BOOLEAN NOT NULL,
    three_agent_max_probability_spread NUMERIC(8, 6) NOT NULL,
    agent3_abstention_max_probability_spread NUMERIC(8, 6) NOT NULL,
    min_active_agent_probability NUMERIC(8, 6) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_manager_policy_versions_identity
        UNIQUE (policy_key, policy_version),
    CONSTRAINT chk_manager_policy_versions_consensus_mode
        CHECK (consensus_mode = 'UNANIMOUS_ACTIVE_AGENTS'),
    CONSTRAINT chk_manager_policy_versions_allow_agent3_abstention
        CHECK (allow_agent3_abstention = TRUE),
    CONSTRAINT chk_manager_policy_versions_three_agent_spread
        CHECK (
            three_agent_max_probability_spread >= 0
            AND three_agent_max_probability_spread <= 1
        ),
    CONSTRAINT chk_manager_policy_versions_agent3_abstention_spread
        CHECK (
            agent3_abstention_max_probability_spread >= 0
            AND agent3_abstention_max_probability_spread <= 1
        ),
    CONSTRAINT chk_manager_policy_versions_min_active_probability
        CHECK (min_active_agent_probability >= 0 AND min_active_agent_probability <= 1)
);

INSERT INTO manager_policy_versions (
    policy_key,
    policy_version,
    consensus_mode,
    allow_agent3_abstention,
    three_agent_max_probability_spread,
    agent3_abstention_max_probability_spread,
    min_active_agent_probability
)
VALUES (
    'tennis_manager_consensus',
    'v1',
    'UNANIMOUS_ACTIVE_AGENTS',
    TRUE,
    0.080000,
    0.060000,
    0.500000
);

CREATE TABLE manager_consensus_results (
    manager_consensus_result_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prediction_run_id BIGINT NOT NULL,
    match_id BIGINT NOT NULL,
    manager_policy_version_id BIGINT NOT NULL,
    manager_status manager_consensus_status NOT NULL,
    selected_side SMALLINT,
    consensus_probability NUMERIC(51, 50),
    active_agent_count SMALLINT NOT NULL,
    abstaining_agent_count SMALLINT NOT NULL,
    probability_spread NUMERIC(51, 50),
    reason_code manager_reason_code NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_manager_consensus_results_run_match
        UNIQUE (prediction_run_id, match_id),
    CONSTRAINT fk_manager_consensus_results_target
        FOREIGN KEY (prediction_run_id, match_id)
        REFERENCES prediction_run_targets(prediction_run_id, match_id),
    CONSTRAINT fk_manager_consensus_results_run_as_of
        FOREIGN KEY (prediction_run_id, as_of)
        REFERENCES prediction_runs(prediction_run_id, as_of),
    CONSTRAINT fk_manager_consensus_results_policy
        FOREIGN KEY (manager_policy_version_id)
        REFERENCES manager_policy_versions(manager_policy_version_id),
    CONSTRAINT chk_manager_consensus_results_status_reason
        CHECK (
            (manager_status = 'CONSENSUS' AND reason_code = 'NONE')
            OR (manager_status = 'NO_BET' AND reason_code <> 'NONE')
        ),
    CONSTRAINT chk_manager_consensus_results_consensus_fields
        CHECK (
            (manager_status = 'CONSENSUS'
                AND selected_side IS NOT NULL
                AND consensus_probability IS NOT NULL
                AND active_agent_count >= 2)
            OR (manager_status = 'NO_BET'
                AND selected_side IS NULL
                AND consensus_probability IS NULL)
        ),
    CONSTRAINT chk_manager_consensus_results_agent_counts
        CHECK (active_agent_count + abstaining_agent_count = 3),
    CONSTRAINT chk_manager_consensus_results_selected_side
        CHECK (selected_side IS NULL OR selected_side IN (1, 2)),
    CONSTRAINT chk_manager_consensus_results_probability_range
        CHECK (
            consensus_probability IS NULL
            OR (consensus_probability >= 0 AND consensus_probability <= 1)
        ),
    CONSTRAINT chk_manager_consensus_results_spread_nullability
        CHECK (
            (active_agent_count >= 2 AND probability_spread IS NOT NULL)
            OR (active_agent_count < 2 AND probability_spread IS NULL)
        ),
    CONSTRAINT chk_manager_consensus_results_spread_range
        CHECK (
            probability_spread IS NULL
            OR (probability_spread >= 0 AND probability_spread <= 1)
        )
);

CREATE INDEX idx_manager_consensus_results_match
    ON manager_consensus_results (match_id);

CREATE INDEX idx_manager_consensus_results_status
    ON manager_consensus_results (manager_status);

CREATE OR REPLACE FUNCTION guard_manager_consensus_result_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    parent_contract_version TEXT;
    parent_run_status TEXT;
    parent_prediction_finalized_at TIMESTAMPTZ;
BEGIN
    SELECT prediction_contract_version, run_status, prediction_finalized_at
    INTO parent_contract_version, parent_run_status, parent_prediction_finalized_at
    FROM prediction_runs
    WHERE prediction_run_id = NEW.prediction_run_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'manager_consensus_results insert requires an existing prediction_runs parent row (prediction_run_id=%).',
            NEW.prediction_run_id;
    END IF;

    IF parent_contract_version <> 'predictive_agent_result_v1' THEN
        RAISE EXCEPTION
            'manager_consensus_results requires prediction_contract_version = predictive_agent_result_v1.';
    END IF;

    IF parent_run_status <> 'COMPLETED' THEN
        RAISE EXCEPTION
            'manager_consensus_results requires a COMPLETED prediction run.';
    END IF;

    IF parent_prediction_finalized_at IS NULL THEN
        RAISE EXCEPTION
            'manager_consensus_results requires a finalized prediction run.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_manager_consensus_results_eligibility_guard
BEFORE INSERT ON manager_consensus_results
FOR EACH ROW
EXECUTE FUNCTION guard_manager_consensus_result_insert();

CREATE TRIGGER trg_manager_consensus_results_append_only_mutation
BEFORE UPDATE OR DELETE ON manager_consensus_results
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_manager_policy_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON manager_policy_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM decisions) THEN
        RAISE EXCEPTION
            '0008_market_value_v1.up.sql requires decisions to be empty before adding Market lineage columns.';
    END IF;
END;
$$;

ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'NOT_MANAGER_ELIGIBLE';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'MISSING_MARKET';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'INCOMPLETE_MARKET';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'STALE_MARKET';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'MARKET_SUSPENDED';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'CROSSED_MARKET';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'INVALID_PRICE';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'NO_MODEL_EDGE_VS_SHARP';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'EDGE_BELOW_THRESHOLD';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'UNSUPPORTED_TIER';
ALTER TYPE decision_reason_code
    ADD VALUE IF NOT EXISTS 'MISSING_SHARP_REFERENCE';

CREATE TYPE market_snapshot_role AS ENUM (
    'REFERENCE_PLAYER_1',
    'REFERENCE_PLAYER_2',
    'OFFERED_SELECTION'
);

CREATE TABLE market_policy_versions (
    market_policy_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_key TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    atp_main_min_edge_pct NUMERIC(8, 6) NOT NULL,
    challenger_min_edge_pct NUMERIC(8, 6) NOT NULL,
    long_price_threshold NUMERIC(12, 6) NOT NULL,
    long_price_min_edge_pct NUMERIC(8, 6) NOT NULL,
    default_devig_method TEXT NOT NULL,
    sharp_reference_policy_code TEXT NOT NULL,
    odds_max_staleness_minutes INTEGER NOT NULL,
    type_b_enabled BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_market_policy_versions_identity
        UNIQUE (policy_key, policy_version),
    CONSTRAINT chk_market_policy_versions_atp_main_min_edge
        CHECK (atp_main_min_edge_pct >= 0),
    CONSTRAINT chk_market_policy_versions_challenger_min_edge
        CHECK (challenger_min_edge_pct >= 0),
    CONSTRAINT chk_market_policy_versions_long_price_threshold
        CHECK (long_price_threshold > 1),
    CONSTRAINT chk_market_policy_versions_long_price_min_edge
        CHECK (long_price_min_edge_pct >= 0),
    CONSTRAINT chk_market_policy_versions_default_devig_method
        CHECK (default_devig_method IN ('POWER', 'SHIN')),
    CONSTRAINT chk_market_policy_versions_odds_staleness
        CHECK (odds_max_staleness_minutes >= 0),
    CONSTRAINT chk_market_policy_versions_type_b_enabled
        CHECK (type_b_enabled = FALSE)
);

INSERT INTO market_policy_versions (
    policy_key,
    policy_version,
    atp_main_min_edge_pct,
    challenger_min_edge_pct,
    long_price_threshold,
    long_price_min_edge_pct,
    default_devig_method,
    sharp_reference_policy_code,
    odds_max_staleness_minutes,
    type_b_enabled
)
VALUES (
    'tennis_market_value',
    'v1',
    0.050000,
    0.080000,
    4.000000,
    0.080000,
    'POWER',
    'PINNACLE_SHARP',
    15,
    FALSE
);

ALTER TABLE decisions
    ALTER COLUMN decision_policy_version_id DROP NOT NULL;

ALTER TABLE decisions
    ADD COLUMN manager_consensus_result_id BIGINT,
    ADD COLUMN market_policy_version_id BIGINT,
    ADD COLUMN snapshot_lineage_hash TEXT,
    ADD CONSTRAINT fk_decisions_manager_consensus_result
        FOREIGN KEY (manager_consensus_result_id)
        REFERENCES manager_consensus_results(manager_consensus_result_id),
    ADD CONSTRAINT fk_decisions_market_policy_version
        FOREIGN KEY (market_policy_version_id)
        REFERENCES market_policy_versions(market_policy_version_id),
    ADD CONSTRAINT chk_decisions_market_links_coherent
        CHECK (
            manager_consensus_result_id IS NULL
            OR (
                market_policy_version_id IS NOT NULL
                AND snapshot_lineage_hash IS NOT NULL
            )
        ),
    ADD CONSTRAINT uq_decisions_market_idempotency
        UNIQUE (manager_consensus_result_id, market_policy_version_id, as_of, snapshot_lineage_hash);

CREATE TABLE decision_odds_snapshots (
    decision_id BIGINT NOT NULL REFERENCES decisions(decision_id),
    odds_snapshot_id BIGINT NOT NULL REFERENCES odds_snapshots(odds_snapshot_id),
    snapshot_role market_snapshot_role NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_decision_odds_snapshots_role
        UNIQUE (decision_id, snapshot_role)
);

CREATE INDEX idx_decision_odds_snapshots_odds
    ON decision_odds_snapshots (odds_snapshot_id);

CREATE OR REPLACE FUNCTION guard_market_decision_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    parent_manager_status TEXT;
    parent_run_id BIGINT;
    parent_match_id BIGINT;
BEGIN
    IF NEW.manager_consensus_result_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT manager_status::text, prediction_run_id, match_id
    INTO parent_manager_status, parent_run_id, parent_match_id
    FROM manager_consensus_results
    WHERE manager_consensus_result_id = NEW.manager_consensus_result_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'market decision requires an existing manager_consensus_result parent (manager_consensus_result_id=%).',
            NEW.manager_consensus_result_id;
    END IF;

    IF parent_run_id <> NEW.prediction_run_id OR parent_match_id <> NEW.match_id THEN
        RAISE EXCEPTION
            'market decision prediction_run_id/match_id must match its manager_consensus_result parent.';
    END IF;

    IF NEW.decision_status = 'BET' AND parent_manager_status <> 'CONSENSUS' THEN
        RAISE EXCEPTION
            'a BET market decision requires a CONSENSUS manager_consensus_result parent.';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_market_decisions_eligibility_guard
BEFORE INSERT ON decisions
FOR EACH ROW
EXECUTE FUNCTION guard_market_decision_insert();

CREATE TRIGGER trg_market_policy_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON market_policy_versions
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_decision_odds_snapshots_append_only_mutation
BEFORE UPDATE OR DELETE ON decision_odds_snapshots
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
BEGIN;

CREATE TYPE odds_observation_time_basis AS ENUM ('PROVIDER_TIME', 'RECEIVE_TIME');
CREATE TYPE odds_normalization_class AS ENUM ('SHARP', 'OFFERED', 'RAW_ONLY');
CREATE TYPE odds_origin AS ENUM ('HISTORICAL', 'SYNTHETIC', 'LIVE_PROVIDER');
CREATE TYPE raw_odds_observation_status AS ENUM ('RECEIVED');

CREATE TABLE raw_odds_observations (
    raw_odds_observation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingest_run_id BIGINT NOT NULL REFERENCES ingest_runs(ingest_run_id),
    provider_code TEXT NOT NULL CHECK (btrim(provider_code) <> ''),
    provider_event_id TEXT NOT NULL CHECK (btrim(provider_event_id) <> ''),
    provider_observation_key TEXT NOT NULL CHECK (btrim(provider_observation_key) <> ''),
    provider_request_id TEXT,
    provider_response_id TEXT,
    provider_observed_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL,
    observation_time TIMESTAMPTZ NOT NULL,
    observation_time_basis odds_observation_time_basis NOT NULL,
    payload_hash TEXT NOT NULL CHECK (btrim(payload_hash) <> ''),
    payload_json JSONB NOT NULL,
    adapter_version TEXT NOT NULL CHECK (btrim(adapter_version) <> ''),
    normalizer_version TEXT NOT NULL CHECK (btrim(normalizer_version) <> ''),
    raw_status raw_odds_observation_status NOT NULL DEFAULT 'RECEIVED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_raw_odds_observations_provider_key
        UNIQUE (provider_code, provider_observation_key),
    CONSTRAINT chk_raw_odds_observations_provider_time
        CHECK (
            (
                observation_time_basis = 'PROVIDER_TIME'
                AND provider_observed_at IS NOT NULL
                AND observation_time = provider_observed_at
                AND received_at >= provider_observed_at
            )
            OR (
                observation_time_basis = 'RECEIVE_TIME'
                AND provider_observed_at IS NULL
                AND observation_time = received_at
            )
        )
);

CREATE TABLE provider_bookmaker_mappings (
    provider_bookmaker_mapping_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_code TEXT NOT NULL CHECK (btrim(provider_code) <> ''),
    provider_bookmaker_key TEXT NOT NULL CHECK (btrim(provider_bookmaker_key) <> ''),
    mapping_version INTEGER NOT NULL CHECK (mapping_version > 0),
    bookmaker_code TEXT NOT NULL CHECK (btrim(bookmaker_code) <> ''),
    normalization_class odds_normalization_class NOT NULL,
    effective_from TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_provider_bookmaker_mappings_version
        UNIQUE (provider_code, provider_bookmaker_key, mapping_version),
    CONSTRAINT uq_provider_bookmaker_mappings_effective_from
        UNIQUE (provider_code, provider_bookmaker_key, effective_from)
);

CREATE OR REPLACE FUNCTION guard_provider_bookmaker_mapping_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    max_effective_from TIMESTAMPTZ;
    max_mapping_version INTEGER;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(NEW.provider_code || chr(31) || NEW.provider_bookmaker_key, 0)
    );

    SELECT max(effective_from), max(mapping_version)
    INTO max_effective_from, max_mapping_version
    FROM provider_bookmaker_mappings
    WHERE provider_code = NEW.provider_code
      AND provider_bookmaker_key = NEW.provider_bookmaker_key;

    IF max_effective_from IS NOT NULL AND NEW.effective_from <= max_effective_from THEN
        RAISE EXCEPTION
            'provider_bookmaker_mappings effective_from must move forward for provider_code=%, provider_bookmaker_key=%.',
            NEW.provider_code,
            NEW.provider_bookmaker_key;
    END IF;

    IF max_mapping_version IS NOT NULL AND NEW.mapping_version <= max_mapping_version THEN
        RAISE EXCEPTION
            'provider_bookmaker_mappings mapping_version must move forward for provider_code=%, provider_bookmaker_key=%.',
            NEW.provider_code,
            NEW.provider_bookmaker_key;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_provider_bookmaker_mappings_insert_guard
BEFORE INSERT ON provider_bookmaker_mappings
FOR EACH ROW
EXECUTE FUNCTION guard_provider_bookmaker_mapping_insert();

ALTER TABLE odds_snapshots
    ADD COLUMN raw_odds_observation_id BIGINT,
    ADD COLUMN provider_bookmaker_mapping_id BIGINT,
    ADD COLUMN odds_origin odds_origin,
    ADD CONSTRAINT fk_odds_snapshots_raw_odds_observation
        FOREIGN KEY (raw_odds_observation_id)
        REFERENCES raw_odds_observations(raw_odds_observation_id),
    ADD CONSTRAINT fk_odds_snapshots_provider_bookmaker_mapping
        FOREIGN KEY (provider_bookmaker_mapping_id)
        REFERENCES provider_bookmaker_mappings(provider_bookmaker_mapping_id),
    ADD CONSTRAINT chk_odds_snapshots_live_provider_lineage
        CHECK (
            odds_origin IS DISTINCT FROM 'LIVE_PROVIDER'
            OR (
                raw_odds_observation_id IS NOT NULL
                AND provider_bookmaker_mapping_id IS NOT NULL
                AND source_system IN ('SHARP', 'OFFERED')
            )
        );

CREATE INDEX idx_raw_odds_observations_provider_event
    ON raw_odds_observations (provider_code, provider_event_id, observation_time);

CREATE INDEX idx_provider_bookmaker_mappings_selector
    ON provider_bookmaker_mappings (provider_code, provider_bookmaker_key, effective_from DESC);

CREATE INDEX idx_odds_snapshots_raw_odds_observation
    ON odds_snapshots (raw_odds_observation_id)
    WHERE raw_odds_observation_id IS NOT NULL;

CREATE INDEX idx_odds_snapshots_provider_bookmaker_mapping
    ON odds_snapshots (provider_bookmaker_mapping_id)
    WHERE provider_bookmaker_mapping_id IS NOT NULL;

CREATE TRIGGER trg_raw_odds_observations_append_only_mutation
BEFORE UPDATE OR DELETE ON raw_odds_observations
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

CREATE TRIGGER trg_provider_bookmaker_mappings_append_only_mutation
BEFORE UPDATE OR DELETE ON provider_bookmaker_mappings
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
BEGIN;

ALTER TABLE decisions
    ADD COLUMN staking_rule_version TEXT,
    ADD COLUMN staking_probability NUMERIC(18, 12),
    ADD COLUMN staking_decimal_price NUMERIC(18, 8),
    ADD COLUMN full_kelly_fraction NUMERIC(18, 12),
    ADD COLUMN quarter_kelly_fraction NUMERIC(18, 12),
    ADD COLUMN suggested_stake_fraction NUMERIC(18, 12),
    ADD CONSTRAINT chk_decisions_staking_probability
        CHECK (
            staking_probability IS NULL
            OR (staking_probability >= 0 AND staking_probability <= 1)
        ),
    ADD CONSTRAINT chk_decisions_staking_decimal_price
        CHECK (staking_decimal_price IS NULL OR staking_decimal_price > 1),
    ADD CONSTRAINT chk_decisions_suggested_stake_fraction
        CHECK (
            suggested_stake_fraction IS NULL
            OR (suggested_stake_fraction >= 0 AND suggested_stake_fraction <= 0.020000000000)
        ),
    ADD CONSTRAINT chk_decisions_no_positive_stake_without_bet
        CHECK (
            decision_status = 'BET'
            OR suggested_stake_fraction IS NULL
            OR suggested_stake_fraction = 0
        ),
    ADD CONSTRAINT chk_decisions_staking_rule_coherent
        CHECK (
            (
                staking_rule_version IS NULL
                AND staking_probability IS NULL
                AND staking_decimal_price IS NULL
                AND full_kelly_fraction IS NULL
                AND quarter_kelly_fraction IS NULL
                AND suggested_stake_fraction IS NULL
            )
            OR (
                staking_rule_version = 'QUARTER_KELLY_CAP_2_V1'
                AND staking_probability IS NOT NULL
                AND staking_decimal_price IS NOT NULL
                AND full_kelly_fraction IS NOT NULL
                AND quarter_kelly_fraction IS NOT NULL
                AND suggested_stake_fraction IS NOT NULL
            )
        );

COMMIT;

BEGIN;

CREATE TABLE a2_history_data_regimes (
    regime_code TEXT PRIMARY KEY CHECK (btrim(regime_code) <> ''),
    source_system TEXT NOT NULL CHECK (source_system = 'API_TENNIS'),
    window_start DATE NOT NULL,
    window_end DATE NOT NULL,
    chunk_days SMALLINT NOT NULL CHECK (chunk_days BETWEEN 1 AND 31),
    activated_at TIMESTAMPTZ NOT NULL,
    activation_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (window_end >= window_start)
);

CREATE TRIGGER trg_a2_history_data_regimes_append_only_mutation
BEFORE UPDATE OR DELETE ON a2_history_data_regimes
FOR EACH ROW
EXECUTE FUNCTION raise_append_only_mutation();

COMMIT;
