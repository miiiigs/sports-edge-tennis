from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    AbstainReason,
    ScoringRuleVersion,
)
from sports_edge_tennis.data.agent2_history import (
    ORDINARY_ATP_TOURNAMENT_ALIASES_V1,
    TRUE_QUALIFYING_ROUND_CODES,
    Agent2HistoricalMatch,
    Agent2MatchStatRow,
    build_history_snapshot,
    build_prediction_request_from_snapshot,
    historical_match_is_eligible_for_update,
    is_true_qualifying_round,
    load_agent2_history,
    normalize_tournament_alias,
    replay_historical_predictions,
    resolve_scoring_rule,
)
from sports_edge_tennis.data.normalize import normalize_person_lookup


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def _insert_player(conn: psycopg.Connection, name: str) -> int:
    return conn.execute(
        "INSERT INTO players (canonical_name, status) VALUES (%s, 'ACTIVE') RETURNING player_id",
        (name,),
    ).fetchone()[0]


def _insert_source_id(
    conn: psycopg.Connection,
    *,
    player_id: int,
    source_system: str,
    source_player_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO player_source_ids (player_id, source_system, source_player_id)
        VALUES (%s, %s, %s)
        """,
        (player_id, source_system, source_player_id),
    )


def _insert_alias(
    conn: psycopg.Connection,
    *,
    player_id: int,
    source_system: str,
    alias: str,
) -> None:
    conn.execute(
        """
        INSERT INTO player_aliases (player_id, source_system, alias, normalized_alias)
        VALUES (%s, %s, %s, %s)
        """,
        (player_id, source_system, alias, normalize_person_lookup(alias)),
    )


def _insert_match_shell(
    conn: psycopg.Connection,
    *,
    tournament_name: str,
    season_year: int,
    tour_scope: str,
    competition_tier: str,
    surface: str | None,
    start_date: str,
    end_date: str,
) -> tuple[int, int]:
    tournament_id = conn.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, %s, %s)
        RETURNING tournament_id
        """,
        (tournament_name, tour_scope, competition_tier),
    ).fetchone()[0]
    edition_id = conn.execute(
        """
        INSERT INTO tournament_editions (
            tournament_id,
            season_year,
            edition_name,
            surface,
            start_date,
            end_date
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING tournament_edition_id
        """,
        (tournament_id, season_year, f"{tournament_name} {season_year}", surface, start_date, end_date),
    ).fetchone()[0]
    match_id = conn.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]
    return match_id, tournament_id


def _insert_match_version(
    conn: psycopg.Connection,
    *,
    match_id: int,
    player_1_id: int,
    player_2_id: int,
    tour_scope: str,
    competition_tier: str,
    surface: str | None,
    round_code: str,
    best_of_sets: int,
    qualifying_flag: bool,
    match_status: str,
    match_start_at: datetime,
    source_observed_at: datetime,
    as_of: datetime,
    ingested_at: datetime | None = None,
) -> int:
    return conn.execute(
        """
        INSERT INTO match_versions (
            match_id,
            player_1_id,
            player_2_id,
            match_start_at,
            tour_scope,
            competition_tier,
            surface,
            round_code,
            best_of_sets,
            qualifying_flag,
            match_status,
            source_observed_at,
            as_of,
            ingested_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP))
        RETURNING match_version_id
        """,
        (
            match_id,
            player_1_id,
            player_2_id,
            match_start_at,
            tour_scope,
            competition_tier,
            surface,
            round_code,
            best_of_sets,
            qualifying_flag,
            match_status,
            source_observed_at,
            as_of,
            ingested_at,
        ),
    ).fetchone()[0]


def _insert_result(
    conn: psycopg.Connection,
    *,
    match_id: int,
    outcome_status: str,
    winner_side: int | None,
    source_observed_at: datetime,
    as_of: datetime,
    ingested_at: datetime | None = None,
) -> int:
    return conn.execute(
        """
        INSERT INTO match_result_observations (
            match_id,
            outcome_status,
            winner_side,
            retired_side,
            sets_won_player_1,
            sets_won_player_2,
            games_won_player_1,
            games_won_player_2,
            scoreline_text,
            source_observed_at,
            as_of,
            ingested_at
        )
        VALUES (%s, %s, %s, NULL, NULL, NULL, NULL, NULL, NULL, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP))
        RETURNING match_result_observation_id
        """,
        (match_id, outcome_status, winner_side, source_observed_at, as_of, ingested_at),
    ).fetchone()[0]


def _insert_stat(
    conn: psycopg.Connection,
    *,
    match_id: int,
    player_id: int,
    source_record_id: str,
    source_observed_at: datetime,
    as_of: datetime,
    service_points_played: int,
    first_serve_points_won: int,
    second_serve_points_won: int,
    return_points_won: int | None = None,
    ingested_at: datetime | None = None,
    source_system: str = "SACKMANN",
) -> int:
    return conn.execute(
        """
        INSERT INTO match_stat_observations (
            match_id,
            player_id,
            source_system,
            source_record_id,
            observation_kind,
            service_points_played,
            first_serve_points_won,
            second_serve_points_won,
            return_points_won,
            source_observed_at,
            as_of,
            ingested_at
        )
        VALUES (%s, %s, %s, %s, 'RAW_SOURCE', %s, %s, %s, %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP))
        RETURNING match_stat_observation_id
        """,
        (
            match_id,
            player_id,
            source_system,
            source_record_id,
            service_points_played,
            first_serve_points_won,
            second_serve_points_won,
            return_points_won,
            source_observed_at,
            as_of,
            ingested_at,
        ),
    ).fetchone()[0]


def _stat_row(
    *,
    match_stat_observation_id: int,
    player_id: int,
    source_observed_at: datetime,
    as_of: datetime,
    service_points_played: int,
    first_serve_points_won: int,
    second_serve_points_won: int,
) -> Agent2MatchStatRow:
    return Agent2MatchStatRow(
        match_stat_observation_id=match_stat_observation_id,
        player_id=player_id,
        source_system="SACKMANN",
        source_observed_at=source_observed_at,
        as_of=as_of,
        first_serve_points_won=first_serve_points_won,
        second_serve_points_won=second_serve_points_won,
        service_points_played=service_points_played,
        return_points_won=None,
    )


def _historical_match(
    *,
    match_id: int,
    player_1_id: int,
    player_2_id: int,
    season_year: int = 2025,
    tournament_name: str = "ATP 250 Example",
    surface: str | None = "HARD",
    tour_scope: str = "ATP_MAIN",
    competition_tier: str = "ATP_250",
    round_code: str = "R32",
    best_of_sets: int = 3,
    qualifying_flag: bool = False,
    outcome_status: str = "COMPLETED",
    winner_side: int | None = 1,
    result_source_observed_at: datetime | None = None,
    result_as_of: datetime | None = None,
    version_source_observed_at: datetime | None = None,
    version_as_of: datetime | None = None,
    stat_rows: tuple[Agent2MatchStatRow, ...] = (),
) -> Agent2HistoricalMatch:
    result_source_observed_at = result_source_observed_at or _utc(2025, 1, 10, 23, 0)
    result_as_of = result_as_of or result_source_observed_at
    version_source_observed_at = version_source_observed_at or _utc(2025, 1, 10, 8, 0)
    version_as_of = version_as_of or version_source_observed_at
    return Agent2HistoricalMatch(
        match_id=match_id,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        match_start_at=_utc(2025, 1, 10, 9, 0),
        surface=surface,
        tour_scope=tour_scope,
        competition_tier=competition_tier,
        round_code=round_code,
        best_of_sets=best_of_sets,
        qualifying_flag=qualifying_flag,
        tournament_id=1,
        canonical_tournament_name=tournament_name,
        normalized_tournament_alias=normalize_tournament_alias(tournament_name),
        season_year=season_year,
        outcome_status=outcome_status,
        winner_side=winner_side,
        result_source_observed_at=result_source_observed_at,
        result_as_of=result_as_of,
        version_source_observed_at=version_source_observed_at,
        version_as_of=version_as_of,
        stat_rows=stat_rows,
    )


def test_true_qualifying_rounds_use_exact_whitelist():
    assert TRUE_QUALIFYING_ROUND_CODES == frozenset({"Q1", "Q2", "Q3"})
    assert is_true_qualifying_round("Q1") is True
    assert is_true_qualifying_round("Q2") is True
    assert is_true_qualifying_round("Q3") is True
    assert is_true_qualifying_round("QF") is False
    assert is_true_qualifying_round("Q4") is False


def test_resolver_trusts_round_code_over_legacy_qualifying_flag_and_excludes_qf():
    resolved = resolve_scoring_rule(
        canonical_tournament_name="Australian Open",
        season_year=2024,
        tour_scope="ATP_MAIN",
        competition_tier="GRAND_SLAM",
        round_code="QF",
        best_of_sets=5,
        qualifying_flag=True,
    )

    assert resolved.scoring_rule_version == ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6
    assert resolved.qualifying_flag_disagrees is True
    assert resolved.authoritative_qualifying is False


def test_unknown_round_code_fails_closed_when_qualifying_semantics_matter():
    resolved = resolve_scoring_rule(
        canonical_tournament_name="Wimbledon",
        season_year=2023,
        tour_scope="ATP_MAIN",
        competition_tier="GRAND_SLAM",
        round_code="QX",
        best_of_sets=3,
        qualifying_flag=True,
    )

    assert resolved.scoring_rule_version is None
    assert resolved.abstain_reason == AbstainReason.UNSUPPORTED_OR_UNKNOWN


def test_resolver_recognizes_frozen_ordinary_atp_tour_identities():
    for tournament_name in ("Washington", "Barcelona", "Dubai", "Vienna", "Rotterdam"):
        resolved = resolve_scoring_rule(
            canonical_tournament_name=tournament_name,
            season_year=2024,
            tour_scope="ATP_MAIN",
            competition_tier="ATP_TOUR",
            round_code="R32",
            best_of_sets=3,
            qualifying_flag=False,
        )

        assert normalize_tournament_alias(tournament_name) in ORDINARY_ATP_TOURNAMENT_ALIASES_V1
        assert resolved.scoring_rule_version == ScoringRuleVersion.BO3_TB7_ALL_SETS
        assert resolved.abstain_reason is None


def test_frozen_ordinary_atp_tour_identity_fails_closed_on_unexpected_format():
    resolved = resolve_scoring_rule(
        canonical_tournament_name="Washington",
        season_year=2024,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
        round_code="R32",
        best_of_sets=5,
        qualifying_flag=False,
    )

    assert resolved.scoring_rule_version is None
    assert resolved.abstain_reason == AbstainReason.UNSUPPORTED_OR_UNKNOWN


def test_ordinary_atp_tour_identity_can_update_history_when_stats_are_usable():
    source_time = _utc(2025, 5, 10, 23, 0)
    observation = _historical_match(
        match_id=1,
        player_1_id=1,
        player_2_id=2,
        tournament_name="Washington",
        competition_tier="ATP_TOUR",
        stat_rows=(
            _stat_row(
                match_stat_observation_id=1,
                player_id=1,
                source_observed_at=source_time,
                as_of=source_time,
                service_points_played=60,
                first_serve_points_won=30,
                second_serve_points_won=15,
            ),
            _stat_row(
                match_stat_observation_id=2,
                player_id=2,
                source_observed_at=source_time,
                as_of=source_time,
                service_points_played=58,
                first_serve_points_won=27,
                second_serve_points_won=14,
            ),
        ),
    )

    assert historical_match_is_eligible_for_update(observation) is True


def test_resolver_covers_reviewed_event_era_matrix_and_special_event_policy():
    cases = [
        ("Australian Open", 2018, "ATP_MAIN", "GRAND_SLAM", "R32", 5, False, ScoringRuleVersion.BO5_DECIDER_ADVANTAGE),
        ("Doha Aus Open Qualies", 2021, "ATP_MAIN", "GRAND_SLAM", "Q1", 3, True, ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6),
        ("Australian Open", 2024, "ATP_MAIN", "GRAND_SLAM", "Q1", 3, True, ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6),
        ("Roland Garros", 2021, "ATP_MAIN", "GRAND_SLAM", "R32", 5, False, ScoringRuleVersion.BO5_DECIDER_ADVANTAGE),
        ("Roland Garros", 2023, "ATP_MAIN", "GRAND_SLAM", "Q2", 3, True, ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6),
        ("Wimbledon", 2020, "ATP_MAIN", "GRAND_SLAM", "R32", 5, False, ScoringRuleVersion.BO5_DECIDER_TB7_AT_12_12),
        ("Wimbledon", 2021, "ATP_MAIN", "GRAND_SLAM", "Q3", 5, True, ScoringRuleVersion.BO5_DECIDER_TB7_AT_12_12),
        ("Wimbledon", 2023, "ATP_MAIN", "GRAND_SLAM", "Q3", 5, True, ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6),
        ("US Open", 2021, "ATP_MAIN", "GRAND_SLAM", "R32", 5, False, ScoringRuleVersion.BO5_DECIDER_TB7_AT_6_6),
        ("US Open", 2024, "ATP_MAIN", "GRAND_SLAM", "Q1", 3, True, ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6),
        ("ATP Cup", 2021, "ATP_MAIN", "ATP_TOUR", "RR", 3, False, ScoringRuleVersion.BO3_TB7_ALL_SETS),
        ("United Cup", 2024, "ATP_MAIN", "ATP_TOUR", "RR", 3, False, ScoringRuleVersion.BO3_TB7_ALL_SETS),
        ("Nitto ATP Finals", 2024, "ATP_MAIN", "ATP_TOUR", "RR", 3, False, ScoringRuleVersion.BO3_TB7_ALL_SETS),
        ("Olympic Games", 2016, "ATP_MAIN", "ATP_TOUR", "F", 5, False, ScoringRuleVersion.BO5_DECIDER_TB7_AT_6_6),
        ("Olympic Games", 2024, "ATP_MAIN", "ATP_TOUR", "R32", 3, False, ScoringRuleVersion.BO3_TB7_ALL_SETS),
        ("Bordeaux Challenger", 2024, "CHALLENGER", "CHALLENGER", "R32", 3, False, ScoringRuleVersion.BO3_TB7_ALL_SETS),
        ("Geneva Open", 2024, "ATP_MAIN", "ATP_250", "R32", 3, False, ScoringRuleVersion.BO3_TB7_ALL_SETS),
        ("Basel Open", 2024, "ATP_MAIN", "ATP_500", "R32", 3, False, ScoringRuleVersion.BO3_TB7_ALL_SETS),
        ("Cincinnati Masters", 2024, "ATP_MAIN", "MASTERS_1000", "R32", 3, False, ScoringRuleVersion.BO3_TB7_ALL_SETS),
    ]

    resolved_rules = {
        resolve_scoring_rule(
            canonical_tournament_name=name,
            season_year=season,
            tour_scope=tour_scope,
            competition_tier=competition_tier,
            round_code=round_code,
            best_of_sets=best_of_sets,
            qualifying_flag=qualifying_flag,
        ).scoring_rule_version
        for name, season, tour_scope, competition_tier, round_code, best_of_sets, qualifying_flag, _ in cases
    }

    for name, season, tour_scope, competition_tier, round_code, best_of_sets, qualifying_flag, expected in cases:
        resolved = resolve_scoring_rule(
            canonical_tournament_name=name,
            season_year=season,
            tour_scope=tour_scope,
            competition_tier=competition_tier,
            round_code=round_code,
            best_of_sets=best_of_sets,
            qualifying_flag=qualifying_flag,
        )
        assert resolved.scoring_rule_version == expected
        assert resolved.abstain_reason is None

    assert resolved_rules == {
        ScoringRuleVersion.BO3_TB7_ALL_SETS,
        ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6,
        ScoringRuleVersion.BO5_DECIDER_ADVANTAGE,
        ScoringRuleVersion.BO5_DECIDER_TB7_AT_6_6,
        ScoringRuleVersion.BO5_DECIDER_TB7_AT_12_12,
        ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6,
    }

    for special_name in ("NextGen Finals", "Next Gen Finals", "Laver Cup"):
        special = resolve_scoring_rule(
            canonical_tournament_name=special_name,
            season_year=2024,
            tour_scope="ATP_MAIN",
            competition_tier="ATP_TOUR",
            round_code="R32",
            best_of_sets=3,
            qualifying_flag=False,
        )
        assert special.scoring_rule_version is None
        assert special.abstain_reason == AbstainReason.UNSUPPORTED_OR_UNKNOWN


def test_resolver_covers_reviewed_2026_grand_slam_matrix_and_fails_closed_after():
    cases = [
        ("Australian Open", 2026, "GRAND_SLAM", "R32", 5, False, ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6),
        ("Australian Open", 2026, "GRAND_SLAM", "Q1", 3, True, ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6),
        ("Roland Garros", 2026, "GRAND_SLAM", "R32", 5, False, ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6),
        ("Roland Garros", 2026, "GRAND_SLAM", "Q2", 3, True, ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6),
        ("Wimbledon", 2026, "GRAND_SLAM", "SF", 5, False, ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6),
        ("Wimbledon", 2026, "GRAND_SLAM", "Q1", 3, True, ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6),
        ("Wimbledon", 2026, "GRAND_SLAM", "Q2", 3, True, ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6),
        ("Wimbledon", 2026, "GRAND_SLAM", "Q3", 5, True, ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6),
        ("US Open", 2026, "GRAND_SLAM", "F", 5, False, ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6),
        ("US Open", 2026, "GRAND_SLAM", "Q1", 3, True, ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6),
        # 2025 behaviour unchanged
        ("Wimbledon", 2025, "GRAND_SLAM", "Q3", 5, True, ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6),
        ("Australian Open", 2025, "GRAND_SLAM", "R32", 5, False, ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6),
        # Non-slam behaviour unchanged
        ("Cincinnati Masters", 2026, "MASTERS_1000", "R32", 3, False, ScoringRuleVersion.BO3_TB7_ALL_SETS),
        ("Bordeaux Challenger", 2026, "CHALLENGER", "R32", 3, False, ScoringRuleVersion.BO3_TB7_ALL_SETS),
        ("Bordeaux Challenger", 2026, "CHALLENGER", "Q1", 3, True, ScoringRuleVersion.BO3_TB7_ALL_SETS),
    ]
    for name, season, tier, round_code, best_of, qualifying, expected in cases:
        resolved = resolve_scoring_rule(
            canonical_tournament_name=name,
            season_year=season,
            tour_scope="ATP_MAIN" if tier != "CHALLENGER" else "CHALLENGER",
            competition_tier=tier,
            round_code=round_code,
            best_of_sets=best_of,
            qualifying_flag=qualifying,
        )
        assert resolved.scoring_rule_version == expected, (name, season, round_code)
        assert resolved.abstain_reason is None

    # Masters qualifying generic-tier behaviour is unchanged (no canonical fallback).
    masters_qual = resolve_scoring_rule(
        canonical_tournament_name="Shanghai",
        season_year=2026,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
        round_code="Q1",
        best_of_sets=3,
        qualifying_flag=True,
    )
    assert masters_qual.scoring_rule_version is None
    assert masters_qual.abstain_reason == AbstainReason.UNSUPPORTED_OR_UNKNOWN

    # Unsupported future seasons still fail closed.
    for name, round_code, best_of, qualifying in (
        ("Australian Open", "R32", 5, False),
        ("Roland Garros", "Q1", 3, True),
        ("Wimbledon", "SF", 5, False),
        ("US Open", "Q1", 3, True),
    ):
        future = resolve_scoring_rule(
            canonical_tournament_name=name,
            season_year=2027,
            tour_scope="ATP_MAIN",
            competition_tier="GRAND_SLAM",
            round_code=round_code,
            best_of_sets=best_of,
            qualifying_flag=qualifying,
        )
        assert future.scoring_rule_version is None, (name, round_code)
        assert future.abstain_reason == AbstainReason.UNSUPPORTED_OR_UNKNOWN


def test_unknown_atp_tour_events_fail_closed_and_cannot_update_history():
    for tournament_name in (
        "Mystery Team Challenge",
        "Legends Invitational",
        "Unknown Tour Event",
        "Future Special Cup",
    ):
        resolved = resolve_scoring_rule(
            canonical_tournament_name=tournament_name,
            season_year=2024,
            tour_scope="ATP_MAIN",
            competition_tier="ATP_TOUR",
            round_code="R32",
            best_of_sets=3,
            qualifying_flag=False,
        )

        assert resolved.scoring_rule_version is None
        assert resolved.abstain_reason == AbstainReason.UNSUPPORTED_OR_UNKNOWN
        assert (
            historical_match_is_eligible_for_update(
                _historical_match(
                    match_id=1,
                    player_1_id=1,
                    player_2_id=2,
                    tournament_name=tournament_name,
                    competition_tier="ATP_TOUR",
                    stat_rows=(
                        _stat_row(
                            match_stat_observation_id=1,
                            player_id=1,
                            source_observed_at=_utc(2025, 1, 10, 23, 0),
                            as_of=_utc(2025, 1, 10, 23, 0),
                            service_points_played=60,
                            first_serve_points_won=30,
                            second_serve_points_won=15,
                        ),
                        _stat_row(
                            match_stat_observation_id=2,
                            player_id=2,
                            source_observed_at=_utc(2025, 1, 10, 23, 0),
                            as_of=_utc(2025, 1, 10, 23, 0),
                            service_points_played=58,
                            first_serve_points_won=27,
                            second_serve_points_won=14,
                        ),
                    ),
                )
            )
            is False
        )


def test_load_agent2_history_selects_latest_result_and_latest_compatible_version_and_exact_stat_bundle(
    db: psycopg.Connection,
):
    player_1 = _insert_player(db, "Player One")
    player_2 = _insert_player(db, "Player Two")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="Australian Open",
        season_year=2024,
        tour_scope="ATP_MAIN",
        competition_tier="GRAND_SLAM",
        surface="HARD",
        start_date="2024-01-10",
        end_date="2024-01-28",
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="GRAND_SLAM",
        surface="HARD",
        round_code="R32",
        best_of_sets=5,
        qualifying_flag=False,
        match_status="SCHEDULED",
        match_start_at=_utc(2024, 1, 16, 9, 0),
        source_observed_at=_utc(2024, 1, 15, 9, 0),
        as_of=_utc(2024, 1, 15, 9, 0),
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="GRAND_SLAM",
        surface="HARD",
        round_code="R16",
        best_of_sets=5,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2024, 1, 16, 9, 0),
        source_observed_at=_utc(2024, 1, 16, 10, 0),
        as_of=_utc(2024, 1, 16, 10, 0),
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2024, 1, 16, 23, 59, 0),
        as_of=_utc(2024, 1, 16, 23, 59, 0),
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=2,
        source_observed_at=_utc(2024, 1, 17, 2, 0),
        as_of=_utc(2024, 1, 17, 2, 0),
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_1,
        source_record_id="old-1",
        source_observed_at=_utc(2024, 1, 16, 23, 59, 0),
        as_of=_utc(2024, 1, 16, 23, 59, 0),
        service_points_played=60,
        first_serve_points_won=28,
        second_serve_points_won=14,
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_2,
        source_record_id="old-2",
        source_observed_at=_utc(2024, 1, 16, 23, 59, 0),
        as_of=_utc(2024, 1, 16, 23, 59, 0),
        service_points_played=58,
        first_serve_points_won=27,
        second_serve_points_won=13,
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_1,
        source_record_id="new-1",
        source_observed_at=_utc(2024, 1, 17, 2, 0),
        as_of=_utc(2024, 1, 17, 2, 0),
        service_points_played=62,
        first_serve_points_won=31,
        second_serve_points_won=14,
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_2,
        source_record_id="new-2",
        source_observed_at=_utc(2024, 1, 17, 2, 0),
        as_of=_utc(2024, 1, 17, 2, 0),
        service_points_played=61,
        first_serve_points_won=30,
        second_serve_points_won=13,
    )

    observations = load_agent2_history(db, cutoff=_utc(2024, 1, 17, 3, 0))

    assert [item.match_id for item in observations] == [match_id]
    observation = observations[0]
    assert observation.winner_side == 2
    assert observation.round_code == "R16"
    assert observation.best_of_sets == 5
    assert observation.normalized_tournament_alias == "AUSTRALIAN OPEN"
    assert len(observation.stat_rows) == 2
    assert {row.player_id for row in observation.stat_rows} == {player_1, player_2}
    assert {row.service_points_played for row in observation.stat_rows} == {61, 62}


def test_load_agent2_history_respects_source_observed_at_and_as_of_cutoffs_but_not_ingested_at(
    db: psycopg.Connection,
):
    player_1 = _insert_player(db, "Player One")
    player_2 = _insert_player(db, "Player Two")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="ATP 250 Example",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date="2025-01-01",
        end_date="2025-01-07",
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 1, 4, 10, 0),
        source_observed_at=_utc(2025, 1, 4, 10, 0),
        as_of=_utc(2025, 1, 4, 10, 0),
        ingested_at=_utc(2025, 1, 20, 10, 0),
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 1, 4, 23, 0),
        as_of=_utc(2025, 1, 4, 23, 0),
        ingested_at=_utc(2025, 1, 20, 23, 0),
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_1,
        source_record_id="late-1",
        source_observed_at=_utc(2025, 1, 4, 23, 0),
        as_of=_utc(2025, 1, 4, 23, 0),
        service_points_played=60,
        first_serve_points_won=30,
        second_serve_points_won=15,
        ingested_at=_utc(2025, 1, 20, 23, 5),
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_2,
        source_record_id="late-2",
        source_observed_at=_utc(2025, 1, 4, 23, 0),
        as_of=_utc(2025, 1, 4, 23, 0),
        service_points_played=58,
        first_serve_points_won=27,
        second_serve_points_won=14,
        ingested_at=_utc(2025, 1, 20, 23, 5),
    )

    assert load_agent2_history(db, cutoff=_utc(2025, 1, 4, 22, 0)) == []
    same_day = load_agent2_history(db, cutoff=_utc(2025, 1, 4, 23, 0))
    assert len(same_day) == 1

    conn_future = _insert_match_shell(
        db,
        tournament_name="Future Cutoff Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="CLAY",
        start_date="2025-02-01",
        end_date="2025-02-07",
    )
    future_match_id = conn_future[0]
    _insert_match_version(
        db,
        match_id=future_match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="CLAY",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 2, 4, 10, 0),
        source_observed_at=_utc(2025, 2, 4, 10, 0),
        as_of=_utc(2025, 2, 10, 10, 0),
    )
    _insert_result(
        db,
        match_id=future_match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 2, 4, 23, 0),
        as_of=_utc(2025, 2, 10, 10, 0),
    )

    observations = load_agent2_history(db, cutoff=_utc(2025, 2, 5, 0, 0))
    assert [item.match_id for item in observations] == [match_id]


def test_load_agent2_history_rejects_later_version_than_selected_result_revision(
    db: psycopg.Connection,
):
    player_1 = _insert_player(db, "Player One")
    player_2 = _insert_player(db, "Player Two")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="Version Coherence Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date="2025-03-01",
        end_date="2025-03-07",
    )
    compatible_source = _utc(2025, 3, 4, 22, 0)
    result_source = _utc(2025, 3, 4, 23, 0)
    shared_as_of = _utc(2025, 3, 5, 12, 0)
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 3, 4, 10, 0),
        source_observed_at=compatible_source,
        as_of=shared_as_of,
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="CLAY",
        round_code="R16",
        best_of_sets=3,
        qualifying_flag=True,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 3, 4, 10, 0),
        source_observed_at=_utc(2025, 3, 4, 23, 30),
        as_of=shared_as_of,
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=result_source,
        as_of=shared_as_of,
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_1,
        source_record_id="r1-p1",
        source_observed_at=result_source,
        as_of=shared_as_of,
        service_points_played=60,
        first_serve_points_won=30,
        second_serve_points_won=15,
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_2,
        source_record_id="r1-p2",
        source_observed_at=result_source,
        as_of=shared_as_of,
        service_points_played=58,
        first_serve_points_won=27,
        second_serve_points_won=14,
    )

    observations = load_agent2_history(db, cutoff=shared_as_of)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.result_source_observed_at == result_source
    assert observation.version_source_observed_at == compatible_source
    assert observation.surface == "HARD"
    assert observation.round_code == "R32"
    assert observation.best_of_sets == 3
    assert observation.qualifying_flag is False
    assert {row.service_points_played for row in observation.stat_rows} == {58, 60}


def test_load_agent2_history_uses_coherent_correction_bundle_before_and_after_visibility(
    db: psycopg.Connection,
):
    player_1 = _insert_player(db, "Player One")
    player_2 = _insert_player(db, "Player Two")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="Correction Bundle Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date="2025-04-01",
        end_date="2025-04-07",
    )
    first_result_source = _utc(2025, 4, 4, 23, 0)
    first_as_of = _utc(2025, 4, 5, 0, 0)
    second_result_source = _utc(2025, 4, 5, 23, 30)
    second_as_of = _utc(2025, 4, 6, 0, 0)
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 4, 4, 10, 0),
        source_observed_at=_utc(2025, 4, 4, 22, 0),
        as_of=first_as_of,
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=first_result_source,
        as_of=first_as_of,
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_1,
        source_record_id="r1-p1",
        source_observed_at=first_result_source,
        as_of=first_as_of,
        service_points_played=60,
        first_serve_points_won=30,
        second_serve_points_won=15,
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_2,
        source_record_id="r1-p2",
        source_observed_at=first_result_source,
        as_of=first_as_of,
        service_points_played=58,
        first_serve_points_won=27,
        second_serve_points_won=14,
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="CLAY",
        round_code="R16",
        best_of_sets=3,
        qualifying_flag=True,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 4, 4, 10, 0),
        source_observed_at=_utc(2025, 4, 5, 23, 0),
        as_of=second_as_of,
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=2,
        source_observed_at=second_result_source,
        as_of=second_as_of,
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_1,
        source_record_id="r2-p1",
        source_observed_at=second_result_source,
        as_of=second_as_of,
        service_points_played=62,
        first_serve_points_won=31,
        second_serve_points_won=15,
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_2,
        source_record_id="r2-p2",
        source_observed_at=second_result_source,
        as_of=second_as_of,
        service_points_played=61,
        first_serve_points_won=30,
        second_serve_points_won=14,
    )

    before_correction = load_agent2_history(db, cutoff=first_as_of)
    after_correction = load_agent2_history(db, cutoff=second_as_of)

    assert len(before_correction) == 1
    assert before_correction[0].winner_side == 1
    assert before_correction[0].surface == "HARD"
    assert before_correction[0].round_code == "R32"
    assert before_correction[0].qualifying_flag is False
    assert before_correction[0].result_source_observed_at == first_result_source
    assert before_correction[0].version_source_observed_at == _utc(2025, 4, 4, 22, 0)
    assert {row.service_points_played for row in before_correction[0].stat_rows} == {58, 60}

    assert len(after_correction) == 1
    assert after_correction[0].winner_side == 2
    assert after_correction[0].surface == "CLAY"
    assert after_correction[0].round_code == "R16"
    assert after_correction[0].qualifying_flag is True
    assert after_correction[0].result_source_observed_at == second_result_source
    assert after_correction[0].version_source_observed_at == _utc(2025, 4, 5, 23, 0)
    assert {row.service_points_played for row in after_correction[0].stat_rows} == {61, 62}


def test_hybrid_or_one_sided_stat_bundles_do_not_update_history(db: psycopg.Connection):
    player_1 = _insert_player(db, "Player One")
    player_2 = _insert_player(db, "Player Two")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="ATP 250 Example",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date="2025-03-01",
        end_date="2025-03-07",
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 3, 4, 10, 0),
        source_observed_at=_utc(2025, 3, 4, 10, 0),
        as_of=_utc(2025, 3, 4, 10, 0),
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 3, 4, 23, 0),
        as_of=_utc(2025, 3, 4, 23, 0),
    )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_1,
        source_record_id="old-1",
        source_observed_at=_utc(2025, 3, 3, 23, 0),
        as_of=_utc(2025, 3, 3, 23, 0),
        service_points_played=60,
        first_serve_points_won=30,
        second_serve_points_won=15,
    )

    observations = load_agent2_history(db, cutoff=_utc(2025, 3, 5, 0, 0))
    assert len(observations) == 1
    assert observations[0].stat_rows == ()
    assert historical_match_is_eligible_for_update(observations[0]) is False


def _seed_single_match(
    db: psycopg.Connection,
) -> tuple[int, int, int]:
    player_1 = _insert_player(db, "Player One")
    player_2 = _insert_player(db, "Player Two")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="ATP 250 Example",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date="2025-06-08",
        end_date="2025-06-16",
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 6, 10, 10, 0),
        source_observed_at=_utc(2025, 6, 10, 10, 0),
        as_of=_utc(2025, 6, 10, 10, 0),
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 6, 10, 23, 0),
        as_of=_utc(2025, 6, 10, 23, 0),
    )
    return match_id, player_1, player_2


def _seed_bridgeable_historical_player(
    db: psycopg.Connection,
    *,
    name: str,
    source_player_id: str,
    observed_at: datetime,
) -> int:
    player_id = _insert_player(db, name)
    _insert_source_id(
        db,
        player_id=player_id,
        source_system="SACKMANN",
        source_player_id=source_player_id,
    )
    _insert_alias(db, player_id=player_id, source_system="TENNIS_DATA", alias=name)
    opponent = _insert_player(db, f"{name} Historical Opponent")
    _insert_source_id(
        db,
        player_id=opponent,
        source_system="SACKMANN",
        source_player_id=f"{source_player_id}-opp",
    )
    match_id, _ = _insert_match_shell(
        db,
        tournament_name=f"{name} History Open",
        season_year=observed_at.year,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date=observed_at.date().isoformat(),
        end_date=observed_at.date().isoformat(),
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_id,
        player_2_id=opponent,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=observed_at,
        source_observed_at=observed_at,
        as_of=observed_at,
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=observed_at,
        as_of=observed_at,
    )
    return player_id


def test_load_agent2_history_bridges_live_stats_to_historical_model_identity(
    db: psycopg.Connection,
):
    historical_at = _utc(2025, 1, 1, 12, 0)
    live_at = _utc(2025, 6, 10, 10, 0)
    cutoff = _utc(2025, 6, 16, 0, 0)
    hist_1 = _seed_bridgeable_historical_player(
        db,
        name="Bridge Alpha",
        source_player_id="hist-bridge-alpha",
        observed_at=historical_at,
    )
    hist_2 = _seed_bridgeable_historical_player(
        db,
        name="Bridge Beta",
        source_player_id="hist-bridge-beta",
        observed_at=historical_at,
    )
    live_1 = _insert_player(db, "Bridge Alpha")
    live_2 = _insert_player(db, "Bridge Beta")
    _insert_source_id(db, player_id=live_1, source_system="API_TENNIS", source_player_id="api-alpha")
    _insert_source_id(db, player_id=live_2, source_system="SPORTRADAR", source_player_id="sr:beta")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="Live Bridge Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date="2025-06-08",
        end_date="2025-06-16",
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=live_1,
        player_2_id=live_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=live_at,
        source_observed_at=live_at,
        as_of=live_at,
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 6, 15, 12, 0),
        as_of=_utc(2025, 6, 15, 12, 0),
    )
    for player_id, source_record_id, service_points_played in (
        (live_1, "api-alpha-stat", 60),
        (live_2, "api-beta-stat", 58),
    ):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=_utc(2025, 6, 15, 12, 0),
            as_of=_utc(2025, 6, 15, 12, 0),
            service_points_played=service_points_played,
            first_serve_points_won=30,
            second_serve_points_won=15,
            source_system="API_TENNIS",
        )

    observations = load_agent2_history(db, cutoff=cutoff)
    bridged = next(observation for observation in observations if observation.match_id == match_id)
    snapshot = build_history_snapshot((bridged,), cutoff=cutoff)

    assert (bridged.player_1_id, bridged.player_2_id) == (hist_1, hist_2)
    assert {row.player_id for row in bridged.stat_rows} == {hist_1, hist_2}
    assert {row.source_system for row in bridged.stat_rows} == {"API_TENNIS"}
    assert snapshot.players[hist_1].prior_usable_matches == 1
    assert snapshot.players[hist_2].prior_usable_matches == 1


def test_load_agent2_history_preserves_existing_historical_ids(
    db: psycopg.Connection,
):
    match_id, player_1, player_2 = _seed_single_match(db)
    _insert_source_id(db, player_id=player_1, source_system="SACKMANN", source_player_id="hist-p1")
    _insert_source_id(db, player_id=player_2, source_system="SACKMANN", source_player_id="hist-p2")
    for player_id, source_record_id in ((player_1, "hist-stat-1"), (player_2, "hist-stat-2")):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=_utc(2025, 6, 15, 12, 0),
            as_of=_utc(2025, 6, 15, 12, 0),
            service_points_played=60,
            first_serve_points_won=30,
            second_serve_points_won=15,
        )

    observation = load_agent2_history(db, cutoff=_utc(2025, 6, 16, 0, 0))[0]

    assert (observation.player_1_id, observation.player_2_id) == (player_1, player_2)
    assert {row.player_id for row in observation.stat_rows} == {player_1, player_2}


def test_load_agent2_history_fails_closed_on_ambiguous_live_bridge(
    db: psycopg.Connection,
):
    historical_at = _utc(2025, 1, 1, 12, 0)
    first = _seed_bridgeable_historical_player(
        db,
        name="Ambiguous Player",
        source_player_id="hist-ambiguous-1",
        observed_at=historical_at,
    )
    _seed_bridgeable_historical_player(
        db,
        name="Ambiguous Player",
        source_player_id="hist-ambiguous-2",
        observed_at=historical_at,
    )
    live = _insert_player(db, "Ambiguous Player")
    _insert_source_id(db, player_id=live, source_system="API_TENNIS", source_player_id="api-ambiguous")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="Ambiguous Bridge Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date="2025-06-08",
        end_date="2025-06-16",
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=live,
        player_2_id=first,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 6, 10, 10, 0),
        source_observed_at=_utc(2025, 6, 10, 10, 0),
        as_of=_utc(2025, 6, 10, 10, 0),
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 6, 15, 12, 0),
        as_of=_utc(2025, 6, 15, 12, 0),
    )
    for player_id, source_record_id in ((live, "api-live"), (first, "api-hist")):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=_utc(2025, 6, 15, 12, 0),
            as_of=_utc(2025, 6, 15, 12, 0),
            service_points_played=60,
            first_serve_points_won=30,
            second_serve_points_won=15,
            source_system="API_TENNIS",
        )

    observation = next(
        row for row in load_agent2_history(db, cutoff=_utc(2025, 6, 16, 0, 0))
        if row.match_id == match_id
    )

    assert observation.player_1_id == live
    assert observation.player_2_id == first
    assert {row.player_id for row in observation.stat_rows} == {live, first}


def test_load_agent2_history_drops_match_when_live_players_collapse_to_one_model_id(
    db: psycopg.Connection,
):
    historical_at = _utc(2025, 1, 1, 12, 0)
    hist = _seed_bridgeable_historical_player(
        db,
        name="Collision Player",
        source_player_id="hist-collision",
        observed_at=historical_at,
    )
    live_1 = _insert_player(db, "Collision Player")
    live_2 = _insert_player(db, "Collision Player")
    _insert_source_id(db, player_id=live_1, source_system="API_TENNIS", source_player_id="api-collision-1")
    _insert_source_id(db, player_id=live_2, source_system="SPORTRADAR", source_player_id="sr:collision-2")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="Collision Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date="2025-06-08",
        end_date="2025-06-16",
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=live_1,
        player_2_id=live_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 6, 10, 10, 0),
        source_observed_at=_utc(2025, 6, 10, 10, 0),
        as_of=_utc(2025, 6, 10, 10, 0),
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 6, 15, 12, 0),
        as_of=_utc(2025, 6, 15, 12, 0),
    )
    for player_id, source_record_id in ((live_1, "collision-1"), (live_2, "collision-2")):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=_utc(2025, 6, 15, 12, 0),
            as_of=_utc(2025, 6, 15, 12, 0),
            service_points_played=60,
            first_serve_points_won=30,
            second_serve_points_won=15,
            source_system="API_TENNIS",
        )

    observation = next(
        row for row in load_agent2_history(db, cutoff=_utc(2025, 6, 16, 0, 0))
        if row.match_id == match_id
    )
    snapshot = build_history_snapshot((observation,), cutoff=_utc(2025, 6, 16, 0, 0))

    assert observation.player_1_id == hist
    assert observation.player_2_id == hist
    assert observation.stat_rows == ()
    assert hist not in snapshot.players


def test_load_agent2_history_does_not_double_count_collapsed_stat_rows(
    db: psycopg.Connection,
):
    historical_at = _utc(2025, 1, 1, 12, 0)
    hist = _seed_bridgeable_historical_player(
        db,
        name="Duplicate Guard",
        source_player_id="hist-duplicate-guard",
        observed_at=historical_at,
    )
    opponent = _seed_bridgeable_historical_player(
        db,
        name="Duplicate Opponent",
        source_player_id="hist-duplicate-opponent",
        observed_at=historical_at,
    )
    live = _insert_player(db, "Duplicate Guard")
    _insert_source_id(db, player_id=live, source_system="API_TENNIS", source_player_id="api-duplicate")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="Duplicate Guard Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date="2025-06-08",
        end_date="2025-06-16",
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=live,
        player_2_id=opponent,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 6, 10, 10, 0),
        source_observed_at=_utc(2025, 6, 10, 10, 0),
        as_of=_utc(2025, 6, 10, 10, 0),
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 6, 15, 12, 0),
        as_of=_utc(2025, 6, 15, 12, 0),
    )
    for player_id, source_record_id in ((live, "dup-live"), (hist, "dup-hist")):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=_utc(2025, 6, 15, 12, 0),
            as_of=_utc(2025, 6, 15, 12, 0),
            service_points_played=60,
            first_serve_points_won=30,
            second_serve_points_won=15,
            source_system="API_TENNIS",
        )

    observation = next(
        row for row in load_agent2_history(db, cutoff=_utc(2025, 6, 16, 0, 0))
        if row.match_id == match_id
    )
    snapshot = build_history_snapshot((observation,), cutoff=_utc(2025, 6, 16, 0, 0))

    assert observation.stat_rows == ()
    assert snapshot.players == {}


def test_load_agent2_history_honors_pit_cutoff_for_live_identity_bridge(
    db: psycopg.Connection,
):
    historical_at = _utc(2025, 7, 1, 12, 0)
    hist = _seed_bridgeable_historical_player(
        db,
        name="Future Bridge",
        source_player_id="hist-future-bridge",
        observed_at=historical_at,
    )
    live = _insert_player(db, "Future Bridge")
    opponent = _insert_player(db, "Future Opponent")
    _insert_source_id(db, player_id=live, source_system="API_TENNIS", source_player_id="api-future")
    _insert_source_id(db, player_id=opponent, source_system="SACKMANN", source_player_id="hist-future-opp")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="Future Bridge Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date="2025-06-08",
        end_date="2025-06-16",
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=live,
        player_2_id=opponent,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 6, 10, 10, 0),
        source_observed_at=_utc(2025, 6, 10, 10, 0),
        as_of=_utc(2025, 6, 10, 10, 0),
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 6, 15, 12, 0),
        as_of=_utc(2025, 6, 15, 12, 0),
    )

    before = next(
        row for row in load_agent2_history(db, cutoff=_utc(2025, 6, 16, 0, 0))
        if row.match_id == match_id
    )
    after = next(
        row for row in load_agent2_history(db, cutoff=_utc(2025, 7, 2, 0, 0))
        if row.match_id == match_id
    )

    assert before.player_1_id == live
    assert after.player_1_id == hist


def test_load_agent2_history_attaches_api_tennis_stats_with_distinct_result_timestamp(
    db: psycopg.Connection,
):
    match_id, player_1, player_2 = _seed_single_match(db)
    for player_id, source_record_id, service_points_played in (
        (player_1, "api-1", 60),
        (player_2, "api-2", 58),
    ):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=_utc(2025, 6, 15, 12, 0),
            as_of=_utc(2025, 6, 15, 12, 0),
            service_points_played=service_points_played,
            first_serve_points_won=30,
            second_serve_points_won=15,
            source_system="API_TENNIS",
        )

    observations = load_agent2_history(db, cutoff=_utc(2025, 6, 16, 0, 0))

    assert len(observations) == 1
    observation = observations[0]
    assert len(observation.stat_rows) == 2
    assert {row.source_system for row in observation.stat_rows} == {"API_TENNIS"}
    assert historical_match_is_eligible_for_update(observation) is True


def test_load_agent2_history_keeps_api_stats_when_later_result_observation_arrives(
    db: psycopg.Connection,
):
    match_id, player_1, player_2 = _seed_single_match(db)
    for player_id, source_record_id, service_points_played in (
        (player_1, "api-1", 60),
        (player_2, "api-2", 58),
    ):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=_utc(2025, 6, 15, 12, 0),
            as_of=_utc(2025, 6, 15, 12, 0),
            service_points_played=service_points_played,
            first_serve_points_won=30,
            second_serve_points_won=15,
            source_system="API_TENNIS",
        )
    later_result_source = _utc(2025, 6, 20, 18, 0)
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=2,
        source_observed_at=later_result_source,
        as_of=later_result_source,
    )

    observations = load_agent2_history(db, cutoff=_utc(2025, 6, 21, 0, 0))

    assert len(observations) == 1
    observation = observations[0]
    assert observation.result_source_observed_at == later_result_source
    assert len(observation.stat_rows) == 2
    assert {row.source_system for row in observation.stat_rows} == {"API_TENNIS"}
    assert historical_match_is_eligible_for_update(observation) is True


def test_load_agent2_history_prefers_latest_coherent_stat_provider_snapshot(
    db: psycopg.Connection,
):
    match_id, player_1, player_2 = _seed_single_match(db)
    for player_id, source_record_id, service_points_played in (
        (player_1, "api-1", 60),
        (player_2, "api-2", 58),
    ):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=_utc(2025, 6, 15, 12, 0),
            as_of=_utc(2025, 6, 15, 12, 0),
            service_points_played=service_points_played,
            first_serve_points_won=30,
            second_serve_points_won=15,
            source_system="API_TENNIS",
        )
    sportradar_source = _utc(2025, 6, 20, 18, 0)
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=2,
        source_observed_at=sportradar_source,
        as_of=sportradar_source,
    )
    for player_id, source_record_id, service_points_played in (
        (player_1, "sr-1", 64),
        (player_2, "sr-2", 63),
    ):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=sportradar_source,
            as_of=sportradar_source,
            service_points_played=service_points_played,
            first_serve_points_won=32,
            second_serve_points_won=16,
            source_system="SPORTRADAR",
        )

    observations = load_agent2_history(db, cutoff=_utc(2025, 6, 21, 0, 0))

    assert len(observations) == 1
    observation = observations[0]
    assert len(observation.stat_rows) == 2
    assert {row.source_system for row in observation.stat_rows} == {"SPORTRADAR"}
    assert {row.service_points_played for row in observation.stat_rows} == {63, 64}


def test_load_agent2_history_does_not_mix_providers_for_partial_sportradar_snapshot(
    db: psycopg.Connection,
):
    match_id, player_1, player_2 = _seed_single_match(db)
    for player_id, source_record_id, service_points_played in (
        (player_1, "api-1", 60),
        (player_2, "api-2", 58),
    ):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=_utc(2025, 6, 15, 12, 0),
            as_of=_utc(2025, 6, 15, 12, 0),
            service_points_played=service_points_played,
            first_serve_points_won=30,
            second_serve_points_won=15,
            source_system="API_TENNIS",
        )
    _insert_stat(
        db,
        match_id=match_id,
        player_id=player_1,
        source_record_id="sr-partial-1",
        source_observed_at=_utc(2025, 6, 20, 18, 0),
        as_of=_utc(2025, 6, 20, 18, 0),
        service_points_played=64,
        first_serve_points_won=32,
        second_serve_points_won=16,
        source_system="SPORTRADAR",
    )

    observations = load_agent2_history(db, cutoff=_utc(2025, 6, 21, 0, 0))

    assert len(observations) == 1
    observation = observations[0]
    assert len(observation.stat_rows) == 2
    assert {row.source_system for row in observation.stat_rows} == {"API_TENNIS"}
    assert {row.service_points_played for row in observation.stat_rows} == {58, 60}


def test_load_agent2_history_ignores_stats_observed_after_cutoff(
    db: psycopg.Connection,
):
    match_id, player_1, player_2 = _seed_single_match(db)
    for player_id, source_record_id in ((player_1, "future-1"), (player_2, "future-2")):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=_utc(2025, 6, 25, 12, 0),
            as_of=_utc(2025, 6, 25, 12, 0),
            service_points_played=60,
            first_serve_points_won=30,
            second_serve_points_won=15,
            source_system="API_TENNIS",
        )

    observations = load_agent2_history(db, cutoff=_utc(2025, 6, 20, 0, 0))

    assert len(observations) == 1
    assert observations[0].stat_rows == ()
    assert historical_match_is_eligible_for_update(observations[0]) is False


def test_load_agent2_history_fails_closed_when_stat_players_do_not_match_canonical(
    db: psycopg.Connection,
):
    match_id, player_1, _ = _seed_single_match(db)
    unrelated_player = _insert_player(db, "Unrelated Player")
    for player_id, source_record_id in (
        (player_1, "api-1"),
        (unrelated_player, "api-2"),
    ):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=_utc(2025, 6, 15, 12, 0),
            as_of=_utc(2025, 6, 15, 12, 0),
            service_points_played=60,
            first_serve_points_won=30,
            second_serve_points_won=15,
            source_system="API_TENNIS",
        )

    observations = load_agent2_history(db, cutoff=_utc(2025, 6, 16, 0, 0))

    assert len(observations) == 1
    assert observations[0].stat_rows == ()
    assert historical_match_is_eligible_for_update(observations[0]) is False


def test_load_agent2_history_recovers_surface_from_tournament_history(
    db: psycopg.Connection,
):
    player_1 = _insert_player(db, "Player One")
    player_2 = _insert_player(db, "Player Two")
    match_id, tournament_id = _insert_match_shell(
        db,
        tournament_name="Surface Recovery Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface=None,
        start_date="2025-05-01",
        end_date="2025-05-07",
    )
    prior_edition_id = db.execute(
        """
        INSERT INTO tournament_editions (
            tournament_id, season_year, edition_name, surface, start_date, end_date
        )
        VALUES (%s, 2024, 'Surface Recovery Open 2024', 'HARD', '2024-05-01', '2024-05-07')
        RETURNING tournament_edition_id
        """,
        (tournament_id,),
    ).fetchone()[0]
    prior_match_id = db.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (prior_edition_id,),
    ).fetchone()[0]
    _insert_match_version(
        db,
        match_id=prior_match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2024, 5, 4, 10, 0),
        source_observed_at=_utc(2024, 5, 4, 10, 0),
        as_of=_utc(2024, 5, 4, 10, 0),
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface=None,
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=_utc(2025, 5, 4, 10, 0),
        source_observed_at=_utc(2025, 5, 4, 10, 0),
        as_of=_utc(2025, 5, 4, 10, 0),
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 5, 4, 23, 0),
        as_of=_utc(2025, 5, 4, 23, 0),
    )
    for player_id, source_record_id in ((player_1, "sr-1"), (player_2, "sr-2")):
        _insert_stat(
            db,
            match_id=match_id,
            player_id=player_id,
            source_record_id=source_record_id,
            source_observed_at=_utc(2025, 5, 4, 23, 0),
            as_of=_utc(2025, 5, 4, 23, 0),
            service_points_played=60,
            first_serve_points_won=30,
            second_serve_points_won=15,
            source_system="API_TENNIS",
        )

    observations = load_agent2_history(db, cutoff=_utc(2025, 5, 5, 0, 0))

    assert len(observations) == 1
    observation = observations[0]
    assert observation.surface == "HARD"
    assert historical_match_is_eligible_for_update(observation) is True


def test_build_history_snapshot_counts_each_sporting_match_once_and_derives_serve_return_points():
    source_time = _utc(2025, 4, 10, 23, 0)
    observation = _historical_match(
        match_id=1,
        player_1_id=1,
        player_2_id=2,
        stat_rows=(
            _stat_row(
                match_stat_observation_id=1,
                player_id=1,
                source_observed_at=source_time,
                as_of=source_time,
                service_points_played=60,
                first_serve_points_won=30,
                second_serve_points_won=15,
            ),
            _stat_row(
                match_stat_observation_id=2,
                player_id=2,
                source_observed_at=source_time,
                as_of=source_time,
                service_points_played=58,
                first_serve_points_won=27,
                second_serve_points_won=14,
            ),
        ),
    )

    snapshot = build_history_snapshot((observation,), cutoff=_utc(2025, 4, 11, 0, 0))
    player_1 = snapshot.players[1]
    player_2 = snapshot.players[2]

    assert player_1.prior_usable_matches == 1
    assert player_2.prior_usable_matches == 1
    assert player_1.overall_serve_points_won == 45
    assert player_1.overall_serve_points_played == 60
    assert player_1.overall_return_points_won == 17
    assert player_1.overall_return_points_played == 58
    assert player_2.overall_serve_points_won == 41
    assert player_2.overall_serve_points_played == 58
    assert player_2.overall_return_points_won == 15
    assert player_2.overall_return_points_played == 60
    assert snapshot.population.surface_tour_service_points[("HARD", "ATP_MAIN")] == (86, 118)
    assert snapshot.population.surface_service_points["HARD"] == (86, 118)
    assert snapshot.population.global_service_points_won == 86
    assert snapshot.population.global_service_points_played == 118


def test_build_history_snapshot_uses_strict_pre_cutoff_boundary():
    cutoff = _utc(2025, 4, 10, 23, 0)
    prior_observation = _historical_match(
        match_id=1,
        player_1_id=1,
        player_2_id=2,
        result_source_observed_at=_utc(2025, 4, 10, 22, 59, 59),
        result_as_of=_utc(2025, 4, 10, 22, 59, 59),
        stat_rows=(
            _stat_row(
                match_stat_observation_id=1,
                player_id=1,
                source_observed_at=_utc(2025, 4, 10, 22, 59, 59),
                as_of=_utc(2025, 4, 10, 22, 59, 59),
                service_points_played=60,
                first_serve_points_won=30,
                second_serve_points_won=15,
            ),
            _stat_row(
                match_stat_observation_id=2,
                player_id=2,
                source_observed_at=_utc(2025, 4, 10, 22, 59, 59),
                as_of=_utc(2025, 4, 10, 22, 59, 59),
                service_points_played=58,
                first_serve_points_won=27,
                second_serve_points_won=14,
            ),
        ),
    )
    at_cutoff = _historical_match(
        match_id=2,
        player_1_id=1,
        player_2_id=2,
        result_source_observed_at=cutoff,
        result_as_of=cutoff,
        stat_rows=prior_observation.stat_rows,
    )
    after_cutoff = _historical_match(
        match_id=3,
        player_1_id=1,
        player_2_id=2,
        result_source_observed_at=_utc(2025, 4, 10, 23, 0, 1),
        result_as_of=_utc(2025, 4, 10, 23, 0, 1),
        stat_rows=prior_observation.stat_rows,
    )

    snapshot = build_history_snapshot((prior_observation, at_cutoff, after_cutoff), cutoff=cutoff)

    assert snapshot.players[1].prior_usable_matches == 1
    assert snapshot.players[2].prior_usable_matches == 1
    assert snapshot.population.global_service_points_played == 118


def test_unsupported_special_events_do_not_update_history():
    source_time = _utc(2025, 5, 10, 23, 0)
    next_gen = _historical_match(
        match_id=1,
        player_1_id=1,
        player_2_id=2,
        tournament_name="Next Gen Finals",
        stat_rows=(
            _stat_row(
                match_stat_observation_id=1,
                player_id=1,
                source_observed_at=source_time,
                as_of=source_time,
                service_points_played=60,
                first_serve_points_won=30,
                second_serve_points_won=15,
            ),
            _stat_row(
                match_stat_observation_id=2,
                player_id=2,
                source_observed_at=source_time,
                as_of=source_time,
                service_points_played=58,
                first_serve_points_won=27,
                second_serve_points_won=14,
            ),
        ),
    )
    laver = _historical_match(
        match_id=2,
        player_1_id=3,
        player_2_id=4,
        tournament_name="Laver Cup",
        stat_rows=next_gen.stat_rows,
    )

    snapshot = build_history_snapshot((next_gen, laver), cutoff=_utc(2025, 5, 11, 0, 0))

    assert snapshot.players == {}
    assert snapshot.population.global_service_points_played == 0


def test_build_prediction_request_from_snapshot_uses_round_code_resolver_and_surface_context():
    prehistory = _historical_match(
        match_id=1,
        player_1_id=1,
        player_2_id=3,
        stat_rows=(
            _stat_row(
                match_stat_observation_id=1,
                player_id=1,
                source_observed_at=_utc(2025, 6, 1, 23, 0),
                as_of=_utc(2025, 6, 1, 23, 0),
                service_points_played=60,
                first_serve_points_won=31,
                second_serve_points_won=14,
            ),
            _stat_row(
                match_stat_observation_id=2,
                player_id=3,
                source_observed_at=_utc(2025, 6, 1, 23, 0),
                as_of=_utc(2025, 6, 1, 23, 0),
                service_points_played=59,
                first_serve_points_won=28,
                second_serve_points_won=14,
            ),
        ),
    )
    snapshot = build_history_snapshot((prehistory,), cutoff=_utc(2025, 6, 2, 0, 0))
    target = _historical_match(
        match_id=2,
        player_1_id=1,
        player_2_id=2,
        tournament_name="Wimbledon",
        season_year=2023,
        competition_tier="GRAND_SLAM",
        round_code="Q3",
        best_of_sets=5,
        qualifying_flag=True,
        stat_rows=(),
    )

    request = build_prediction_request_from_snapshot(target, snapshot)

    assert request.scoring_rule_version == ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6
    assert request.player_1_state.prior_usable_matches == 1
    assert request.population_prior_state.global_service_points_played == 119


def test_same_cohort_adversarial_replay_freezes_player_and_population_state():
    prehistory: list[Agent2HistoricalMatch] = []
    match_id = 1
    for player_id, base_won in ((1, 45), (2, 44), (3, 43)):
        for offset in range(5):
            opponent_id = 100 + player_id * 10 + offset
            observed_at = _utc(2025, 7, 1 + offset, 23, 0)
            prehistory.append(
                _historical_match(
                    match_id=match_id,
                    player_1_id=player_id,
                    player_2_id=opponent_id,
                    stat_rows=(
                        _stat_row(
                            match_stat_observation_id=match_id * 2 - 1,
                            player_id=player_id,
                            source_observed_at=observed_at,
                            as_of=observed_at,
                            service_points_played=60,
                            first_serve_points_won=base_won - 15,
                            second_serve_points_won=15,
                        ),
                        _stat_row(
                            match_stat_observation_id=match_id * 2,
                            player_id=opponent_id,
                            source_observed_at=observed_at,
                            as_of=observed_at,
                            service_points_played=58,
                            first_serve_points_won=28,
                            second_serve_points_won=14,
                        ),
                    ),
                )
            )
            match_id += 1

    cohort_time = _utc(2025, 7, 20, 23, 0)
    match_one = _historical_match(
        match_id=match_id,
        player_1_id=1,
        player_2_id=3,
        result_source_observed_at=cohort_time,
        result_as_of=cohort_time,
        stat_rows=(
            _stat_row(
                match_stat_observation_id=2001,
                player_id=1,
                source_observed_at=cohort_time,
                as_of=cohort_time,
                service_points_played=60,
                first_serve_points_won=38,
                second_serve_points_won=18,
            ),
            _stat_row(
                match_stat_observation_id=2002,
                player_id=3,
                source_observed_at=cohort_time,
                as_of=cohort_time,
                service_points_played=60,
                first_serve_points_won=10,
                second_serve_points_won=5,
            ),
        ),
    )
    match_two = _historical_match(
        match_id=match_id + 1,
        player_1_id=1,
        player_2_id=2,
        result_source_observed_at=cohort_time,
        result_as_of=cohort_time,
        stat_rows=(
            _stat_row(
                match_stat_observation_id=2003,
                player_id=1,
                source_observed_at=cohort_time,
                as_of=cohort_time,
                service_points_played=60,
                first_serve_points_won=30,
                second_serve_points_won=15,
            ),
            _stat_row(
                match_stat_observation_id=2004,
                player_id=2,
                source_observed_at=cohort_time,
                as_of=cohort_time,
                service_points_played=60,
                first_serve_points_won=29,
                second_serve_points_won=15,
            ),
        ),
    )
    later_match = _historical_match(
        match_id=match_id + 2,
        player_1_id=1,
        player_2_id=2,
        result_source_observed_at=_utc(2025, 7, 21, 23, 0),
        result_as_of=_utc(2025, 7, 21, 23, 0),
        stat_rows=match_two.stat_rows,
    )
    altered_match_one = _historical_match(
        match_id=match_one.match_id,
        player_1_id=1,
        player_2_id=3,
        result_source_observed_at=cohort_time,
        result_as_of=cohort_time,
        stat_rows=(
            _stat_row(
                match_stat_observation_id=3001,
                player_id=1,
                source_observed_at=cohort_time,
                as_of=cohort_time,
                service_points_played=60,
                first_serve_points_won=5,
                second_serve_points_won=5,
            ),
            _stat_row(
                match_stat_observation_id=3002,
                player_id=3,
                source_observed_at=cohort_time,
                as_of=cohort_time,
                service_points_played=60,
                first_serve_points_won=38,
                second_serve_points_won=18,
            ),
        ),
    )

    original = replay_historical_predictions(tuple(prehistory + [match_one, match_two, later_match]))
    altered = replay_historical_predictions(tuple(prehistory + [altered_match_one, match_two, later_match]))

    original_match_two = [record for record in original.records if record.observation.match_id == match_two.match_id][0]
    altered_match_two = [record for record in altered.records if record.observation.match_id == match_two.match_id][0]
    original_later = [record for record in original.records if record.observation.match_id == later_match.match_id][0]
    altered_later = [record for record in altered.records if record.observation.match_id == later_match.match_id][0]

    assert original_match_two.result == altered_match_two.result
    assert original_later.result != altered_later.result
