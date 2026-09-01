from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime
import re

import psycopg

from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    AbstainReason,
    Agent2Config,
    CompetitionTier,
    DEFAULT_CONFIG,
    PlayerServeReturnState,
    PopulationPriorState,
    PredictionRequest,
    PredictionResult,
    ScoringRuleVersion,
    Surface,
    TourScope,
    predict,
)


TRUE_QUALIFYING_ROUND_CODES = frozenset({"Q1", "Q2", "Q3"})
REVIEWED_ROUND_VOCABULARY = frozenset(
    {"BR", "F", "Q1", "Q2", "Q3", "QF", "R128", "R16", "R32", "R64", "RR", "SF"}
)
_ORDINARY_ATP_MAIN_TIERS = frozenset({"MASTERS_1000", "ATP_500", "ATP_250"})
ORDINARY_ATP_TOURNAMENT_ALIASES_V1 = frozenset(
    {
        "ACAPULCO",
        "ADELAIDE",
        "ADELAIDE 1",
        "ADELAIDE 2",
        "ALMATY",
        "ANTALYA",
        "ANTWERP",
        "ASTANA",
        "ATHENS",
        "ATLANTA",
        "ATP RIO DE JANEIRO",
        "AUCKLAND",
        "BANJA LUKA",
        "BARCELONA",
        "BASEL",
        "BASTAD",
        "BEIJING",
        "BELGRADE",
        "BELGRADE 2",
        "BRISBANE",
        "BRUSSELS",
        "BUCHAREST",
        "BUDAPEST",
        "BUENOS AIRES",
        "CAGLIARI",
        "CHENGDU",
        "CHENNAI",
        "COLOGNE 1",
        "COLOGNE 2",
        "CORDOBA",
        "DALLAS",
        "DELRAY BEACH",
        "DOHA",
        "DUBAI",
        "EASTBOURNE",
        "ESTORIL",
        "FLORENCE",
        "GENEVA",
        "GIJON",
        "GREAT OCEAN ROAD OPEN",
        "GSTAAD",
        "HALLE",
        "HAMBURG",
        "HANGZHOU",
        "HONG KONG",
        "HOUSTON",
        "ISTANBUL",
        "KITZBUHEL",
        "LOS CABOS",
        "LYON",
        "MALLORCA",
        "MARBELLA",
        "MARRAKECH",
        "MARSEILLE",
        "MELBOURNE",
        "MEMPHIS",
        "METZ",
        "MONTPELLIER",
        "MOSCOW",
        "MUNICH",
        "MURRAY RIVER OPEN",
        "NAPLES",
        "NEW YORK",
        "NEWPORT",
        "NICE",
        "NOTTINGHAM",
        "NUR SULTAN",
        "PARMA",
        "PUNE",
        "QUEEN S CLUB",
        "QUITO",
        "RIO DE JANEIRO",
        "ROTTERDAM",
        "S HERTOGENBOSCH",
        "SAN DIEGO",
        "SANTIAGO",
        "SAO PAULO",
        "SARDINIA",
        "SEOUL",
        "SHENZHEN",
        "SINGAPORE",
        "SOFIA",
        "ST PETERSBURG",
        "STOCKHOLM",
        "STUTTGART",
        "SYDNEY",
        "TEL AVIV",
        "TOKYO",
        "UMAG",
        "VIENNA",
        "WASHINGTON",
        "WINSTON SALEM",
        "ZHUHAI",
    }
)
_NEXT_GEN_ALIASES = frozenset({"NEXTGEN FINALS", "NEXT GEN FINALS"})
_LAVER_CUP_ALIASES = frozenset({"LAVER CUP"})
_AUSTRALIAN_OPEN_ALIASES = frozenset({"AUSTRALIAN OPEN", "DOHA AUS OPEN QUALIES"})
_ROLAND_GARROS_ALIASES = frozenset({"ROLAND GARROS", "FRENCH OPEN"})
_WIMBLEDON_ALIASES = frozenset({"WIMBLEDON"})
_US_OPEN_ALIASES = frozenset({"US OPEN", "U S OPEN"})
_ATP_CUP_ALIASES = frozenset({"ATP CUP"})
_UNITED_CUP_ALIASES = frozenset({"UNITED CUP"})
_TOUR_FINALS_ALIASES = frozenset({"ATP FINALS", "NITTO ATP FINALS", "TOUR FINALS"})
_OLYMPIC_ALIASES = frozenset(
    {
        "OLYMPIC GAMES",
        "OLYMPICS",
        "RIO OLYMPICS",
        "TOKYO OLYMPICS",
        "PARIS OLYMPICS",
    }
)


@dataclass(frozen=True)
class Agent2MatchStatRow:
    match_stat_observation_id: int
    player_id: int
    source_system: str
    source_observed_at: datetime
    as_of: datetime
    first_serve_points_won: int | None
    second_serve_points_won: int | None
    service_points_played: int | None
    return_points_won: int | None

    @property
    def service_points_won(self) -> int | None:
        if self.first_serve_points_won is None or self.second_serve_points_won is None:
            return None
        return self.first_serve_points_won + self.second_serve_points_won


@dataclass(frozen=True)
class Agent2HistoricalMatch:
    match_id: int
    player_1_id: int
    player_2_id: int
    match_start_at: datetime
    surface: Surface | None
    tour_scope: TourScope
    competition_tier: CompetitionTier
    round_code: str | None
    best_of_sets: int
    qualifying_flag: bool
    tournament_id: int
    canonical_tournament_name: str
    normalized_tournament_alias: str
    season_year: int
    outcome_status: str
    winner_side: int | None
    result_source_observed_at: datetime
    result_as_of: datetime
    version_source_observed_at: datetime
    version_as_of: datetime
    stat_rows: tuple[Agent2MatchStatRow, ...]

    @property
    def visibility_cohort(self) -> datetime:
        return max(self.result_source_observed_at, self.result_as_of)


@dataclass(frozen=True)
class ResolvedScoringRule:
    scoring_rule_version: ScoringRuleVersion | None
    abstain_reason: AbstainReason | None
    authoritative_qualifying: bool | None
    qualifying_flag_disagrees: bool


@dataclass(frozen=True)
class SurfacePointSnapshot:
    serve_points_won: int = 0
    serve_points_played: int = 0
    return_points_won: int = 0
    return_points_played: int = 0


@dataclass(frozen=True)
class PlayerHistorySnapshot:
    player_id: int
    prior_usable_matches: int = 0
    overall_serve_points_won: int = 0
    overall_serve_points_played: int = 0
    overall_return_points_won: int = 0
    overall_return_points_played: int = 0
    surface_points: dict[Surface, SurfacePointSnapshot] = field(default_factory=dict)


@dataclass(frozen=True)
class PopulationHistorySnapshot:
    surface_tour_service_points: dict[tuple[Surface, TourScope], tuple[int, int]] = field(
        default_factory=dict
    )
    surface_service_points: dict[Surface, tuple[int, int]] = field(default_factory=dict)
    global_service_points_won: int = 0
    global_service_points_played: int = 0


@dataclass(frozen=True)
class Agent2HistorySnapshot:
    players: dict[int, PlayerHistorySnapshot]
    population: PopulationHistorySnapshot


@dataclass(frozen=True)
class HistoricalPredictionRecord:
    observation: Agent2HistoricalMatch
    request: PredictionRequest
    result: PredictionResult


@dataclass(frozen=True)
class HistoricalReplayResult:
    records: tuple[HistoricalPredictionRecord, ...]
    final_snapshot: Agent2HistorySnapshot


@dataclass
class _MutableSurfaceAccumulator:
    serve_points_won: int = 0
    serve_points_played: int = 0
    return_points_won: int = 0
    return_points_played: int = 0


@dataclass
class _MutablePlayerAccumulator:
    player_id: int
    prior_usable_matches: int = 0
    overall_serve_points_won: int = 0
    overall_serve_points_played: int = 0
    overall_return_points_won: int = 0
    overall_return_points_played: int = 0
    surface_points: dict[Surface, _MutableSurfaceAccumulator] = field(default_factory=dict)


@dataclass
class _MutablePopulationAccumulator:
    surface_tour_service_points: dict[tuple[Surface, TourScope], list[int]] = field(
        default_factory=dict
    )
    surface_service_points: dict[Surface, list[int]] = field(default_factory=dict)
    global_service_points_won: int = 0
    global_service_points_played: int = 0


def normalize_tournament_alias(name: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", " ", name.upper()).strip()
    return " ".join(normalized.split())


def is_true_qualifying_round(round_code: str | None) -> bool:
    if round_code is None:
        return False
    return round_code.strip().upper() in TRUE_QUALIFYING_ROUND_CODES


def _normalize_round_code(round_code: str | None) -> str | None:
    if round_code is None:
        return None
    normalized = round_code.strip().upper()
    return normalized or None


def resolve_scoring_rule(
    *,
    canonical_tournament_name: str,
    normalized_tournament_alias: str | None = None,
    season_year: int,
    tour_scope: TourScope,
    competition_tier: CompetitionTier,
    round_code: str | None,
    best_of_sets: int,
    qualifying_flag: bool,
) -> ResolvedScoringRule:
    alias = normalized_tournament_alias or normalize_tournament_alias(canonical_tournament_name)
    normalized_round_code = _normalize_round_code(round_code)
    if normalized_round_code in REVIEWED_ROUND_VOCABULARY:
        authoritative_qualifying = is_true_qualifying_round(normalized_round_code)
    elif normalized_round_code is None:
        authoritative_qualifying = None
    else:
        authoritative_qualifying = None
    qualifying_flag_disagrees = (
        authoritative_qualifying is not None and authoritative_qualifying != qualifying_flag
    )

    if alias in _NEXT_GEN_ALIASES or alias in _LAVER_CUP_ALIASES:
        return ResolvedScoringRule(
            scoring_rule_version=None,
            abstain_reason=AbstainReason.UNSUPPORTED_OR_UNKNOWN,
            authoritative_qualifying=authoritative_qualifying,
            qualifying_flag_disagrees=qualifying_flag_disagrees,
        )

    if alias in _AUSTRALIAN_OPEN_ALIASES:
        return _resolve_grand_slam_rule(
            slam="AO",
            season_year=season_year,
            round_code=normalized_round_code,
            best_of_sets=best_of_sets,
            qualifying_flag=qualifying_flag,
        )
    if alias in _ROLAND_GARROS_ALIASES:
        return _resolve_grand_slam_rule(
            slam="RG",
            season_year=season_year,
            round_code=normalized_round_code,
            best_of_sets=best_of_sets,
            qualifying_flag=qualifying_flag,
        )
    if alias in _WIMBLEDON_ALIASES:
        return _resolve_grand_slam_rule(
            slam="WIMBLEDON",
            season_year=season_year,
            round_code=normalized_round_code,
            best_of_sets=best_of_sets,
            qualifying_flag=qualifying_flag,
        )
    if alias in _US_OPEN_ALIASES:
        return _resolve_grand_slam_rule(
            slam="USO",
            season_year=season_year,
            round_code=normalized_round_code,
            best_of_sets=best_of_sets,
            qualifying_flag=qualifying_flag,
        )

    if alias in _ATP_CUP_ALIASES:
        if 2020 <= season_year <= 2022 and best_of_sets == 3:
            return ResolvedScoringRule(
                scoring_rule_version=ScoringRuleVersion.BO3_TB7_ALL_SETS,
                abstain_reason=None,
                authoritative_qualifying=authoritative_qualifying,
                qualifying_flag_disagrees=qualifying_flag_disagrees,
            )
        return _unsupported_rule(authoritative_qualifying, qualifying_flag_disagrees)

    if alias in _UNITED_CUP_ALIASES:
        if season_year >= 2023 and best_of_sets == 3:
            return ResolvedScoringRule(
                scoring_rule_version=ScoringRuleVersion.BO3_TB7_ALL_SETS,
                abstain_reason=None,
                authoritative_qualifying=authoritative_qualifying,
                qualifying_flag_disagrees=qualifying_flag_disagrees,
            )
        return _unsupported_rule(authoritative_qualifying, qualifying_flag_disagrees)

    if alias in _TOUR_FINALS_ALIASES:
        if best_of_sets == 3:
            return ResolvedScoringRule(
                scoring_rule_version=ScoringRuleVersion.BO3_TB7_ALL_SETS,
                abstain_reason=None,
                authoritative_qualifying=authoritative_qualifying,
                qualifying_flag_disagrees=qualifying_flag_disagrees,
            )
        return _unsupported_rule(authoritative_qualifying, qualifying_flag_disagrees)

    if alias in _OLYMPIC_ALIASES:
        if season_year == 2016 and best_of_sets == 5:
            return ResolvedScoringRule(
                scoring_rule_version=ScoringRuleVersion.BO5_DECIDER_TB7_AT_6_6,
                abstain_reason=None,
                authoritative_qualifying=authoritative_qualifying,
                qualifying_flag_disagrees=qualifying_flag_disagrees,
            )
        if best_of_sets == 3:
            return ResolvedScoringRule(
                scoring_rule_version=ScoringRuleVersion.BO3_TB7_ALL_SETS,
                abstain_reason=None,
                authoritative_qualifying=authoritative_qualifying,
                qualifying_flag_disagrees=qualifying_flag_disagrees,
            )
        return _unsupported_rule(authoritative_qualifying, qualifying_flag_disagrees)

    if tour_scope == "CHALLENGER" or competition_tier == "CHALLENGER":
        if best_of_sets == 3:
            return ResolvedScoringRule(
                scoring_rule_version=ScoringRuleVersion.BO3_TB7_ALL_SETS,
                abstain_reason=None,
                authoritative_qualifying=authoritative_qualifying,
                qualifying_flag_disagrees=qualifying_flag_disagrees,
            )
        return _unsupported_rule(authoritative_qualifying, qualifying_flag_disagrees)

    # Local Sackmann level A is encoded as ATP_TOUR, so ordinary ATP events need
    # a frozen positive identity set instead of an unsafe generic ATP_TOUR rule.
    if (
        tour_scope == "ATP_MAIN"
        and competition_tier in _ORDINARY_ATP_MAIN_TIERS
        and best_of_sets == 3
    ):
        return ResolvedScoringRule(
            scoring_rule_version=ScoringRuleVersion.BO3_TB7_ALL_SETS,
            abstain_reason=None,
            authoritative_qualifying=authoritative_qualifying,
            qualifying_flag_disagrees=qualifying_flag_disagrees,
        )

    if (
        tour_scope == "ATP_MAIN"
        and competition_tier == "ATP_TOUR"
        and alias in ORDINARY_ATP_TOURNAMENT_ALIASES_V1
    ):
        if best_of_sets == 3:
            return ResolvedScoringRule(
                scoring_rule_version=ScoringRuleVersion.BO3_TB7_ALL_SETS,
                abstain_reason=None,
                authoritative_qualifying=authoritative_qualifying,
                qualifying_flag_disagrees=qualifying_flag_disagrees,
            )
        return _unsupported_rule(authoritative_qualifying, qualifying_flag_disagrees)

    return _unsupported_rule(authoritative_qualifying, qualifying_flag_disagrees)


def _resolve_grand_slam_rule(
    *,
    slam: str,
    season_year: int,
    round_code: str | None,
    best_of_sets: int,
    qualifying_flag: bool,
) -> ResolvedScoringRule:
    authoritative_qualifying = None if round_code is None else is_true_qualifying_round(round_code)
    qualifying_flag_disagrees = (
        authoritative_qualifying is not None and authoritative_qualifying != qualifying_flag
    )
    if round_code is None or round_code not in REVIEWED_ROUND_VOCABULARY:
        return _unsupported_rule(authoritative_qualifying, qualifying_flag_disagrees)
    if authoritative_qualifying:
        rule = _resolve_grand_slam_qualifying_rule(
            slam=slam,
            season_year=season_year,
            round_code=round_code,
            best_of_sets=best_of_sets,
        )
    else:
        rule = _resolve_grand_slam_main_draw_rule(
            slam=slam,
            season_year=season_year,
            best_of_sets=best_of_sets,
        )
    if rule is None:
        return _unsupported_rule(authoritative_qualifying, qualifying_flag_disagrees)
    return ResolvedScoringRule(
        scoring_rule_version=rule,
        abstain_reason=None,
        authoritative_qualifying=authoritative_qualifying,
        qualifying_flag_disagrees=qualifying_flag_disagrees,
    )


def _resolve_grand_slam_main_draw_rule(
    *,
    slam: str,
    season_year: int,
    best_of_sets: int,
) -> ScoringRuleVersion | None:
    if best_of_sets != 5:
        return None
    if slam == "AO":
        return (
            ScoringRuleVersion.BO5_DECIDER_ADVANTAGE
            if 2016 <= season_year <= 2018
            else ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6
            if 2019 <= season_year <= 2025
            else None
        )
    if slam == "RG":
        return (
            ScoringRuleVersion.BO5_DECIDER_ADVANTAGE
            if 2016 <= season_year <= 2021
            else ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6
            if 2022 <= season_year <= 2025
            else None
        )
    if slam == "WIMBLEDON":
        if 2016 <= season_year <= 2018:
            return ScoringRuleVersion.BO5_DECIDER_ADVANTAGE
        if 2019 <= season_year <= 2021:
            return ScoringRuleVersion.BO5_DECIDER_TB7_AT_12_12
        if 2022 <= season_year <= 2025:
            return ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6
        return None
    if slam == "USO":
        return (
            ScoringRuleVersion.BO5_DECIDER_TB7_AT_6_6
            if 2016 <= season_year <= 2021
            else ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6
            if 2022 <= season_year <= 2025
            else None
        )
    return None


def _resolve_grand_slam_qualifying_rule(
    *,
    slam: str,
    season_year: int,
    round_code: str,
    best_of_sets: int,
) -> ScoringRuleVersion | None:
    if slam == "AO":
        if best_of_sets != 3:
            return None
        return (
            ScoringRuleVersion.BO3_TB7_ALL_SETS
            if 2016 <= season_year <= 2018
            else ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6
            if 2019 <= season_year <= 2025
            else None
        )
    if slam == "RG":
        if best_of_sets != 3:
            return None
        return (
            ScoringRuleVersion.BO3_TB7_ALL_SETS
            if 2016 <= season_year <= 2021
            else ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6
            if 2022 <= season_year <= 2025
            else None
        )
    if slam == "WIMBLEDON":
        if 2016 <= season_year <= 2020 and best_of_sets == 3:
            return ScoringRuleVersion.BO3_TB7_ALL_SETS
        if season_year == 2021:
            if round_code in {"Q1", "Q2"} and best_of_sets == 3:
                return ScoringRuleVersion.BO3_TB7_ALL_SETS
            if round_code == "Q3" and best_of_sets == 5:
                return ScoringRuleVersion.BO5_DECIDER_TB7_AT_12_12
            return None
        if season_year == 2022 and best_of_sets == 3:
            return ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6
        if season_year == 2023:
            if round_code in {"Q1", "Q2"} and best_of_sets == 3:
                return ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6
            if round_code == "Q3" and best_of_sets == 5:
                return ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6
            return None
        if season_year == 2024 and best_of_sets == 3:
            return ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6
        if season_year == 2025:
            if round_code in {"Q1", "Q2"} and best_of_sets == 3:
                return ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6
            if round_code == "Q3" and best_of_sets == 5:
                return ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6
            return None
        return None
    if slam == "USO":
        if best_of_sets != 3:
            return None
        return (
            ScoringRuleVersion.BO3_TB7_ALL_SETS
            if 2016 <= season_year <= 2021
            else ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6
            if 2022 <= season_year <= 2025
            else None
        )
    return None


def _unsupported_rule(
    authoritative_qualifying: bool | None,
    qualifying_flag_disagrees: bool,
) -> ResolvedScoringRule:
    return ResolvedScoringRule(
        scoring_rule_version=None,
        abstain_reason=AbstainReason.UNSUPPORTED_OR_UNKNOWN,
        authoritative_qualifying=authoritative_qualifying,
        qualifying_flag_disagrees=qualifying_flag_disagrees,
    )


def load_agent2_history(
    conn: psycopg.Connection,
    *,
    cutoff: datetime,
    tour_scopes: tuple[str, ...] | list[str] | None = None,
    competition_tiers: tuple[str, ...] | list[str] | None = None,
    year_start: int | None = None,
    year_end: int | None = None,
    date_start: date | None = None,
    date_end: date | None = None,
) -> list[Agent2HistoricalMatch]:
    result_filters = [
        "mro.source_observed_at <= %(cutoff)s",
        "mro.as_of <= %(cutoff)s",
    ]
    version_filters = [
        "mv.source_observed_at <= selected_results.source_observed_at",
        "mv.as_of <= selected_results.as_of",
    ]
    params: dict[str, object] = {"cutoff": cutoff}

    if tour_scopes:
        version_filters.append("mv.tour_scope::text = ANY(%(tour_scopes)s)")
        params["tour_scopes"] = list(tour_scopes)
    if competition_tiers:
        version_filters.append("mv.competition_tier::text = ANY(%(competition_tiers)s)")
        params["competition_tiers"] = list(competition_tiers)
    if year_start is not None:
        version_filters.append("te.season_year >= %(year_start)s")
        params["year_start"] = year_start
    if year_end is not None:
        version_filters.append("te.season_year <= %(year_end)s")
        params["year_end"] = year_end
    if date_start is not None:
        result_filters.append("mro.source_observed_at::date >= %(date_start)s")
        params["date_start"] = date_start
    if date_end is not None:
        result_filters.append("mro.source_observed_at::date <= %(date_end)s")
        params["date_end"] = date_end

    query = f"""
        WITH selected_results AS (
            SELECT
                mro.match_result_observation_id,
                mro.match_id,
                mro.outcome_status::text AS outcome_status,
                mro.winner_side,
                mro.source_observed_at,
                mro.as_of,
                ROW_NUMBER() OVER (
                    PARTITION BY mro.match_id
                    ORDER BY mro.as_of DESC, mro.source_observed_at DESC, mro.match_result_observation_id DESC
                ) AS result_rank
            FROM match_result_observations mro
            WHERE {" AND ".join(result_filters)}
        ),
        selected_versions AS (
            SELECT
                selected_results.match_result_observation_id,
                selected_results.match_id,
                selected_results.outcome_status,
                selected_results.winner_side,
                selected_results.source_observed_at AS result_source_observed_at,
                selected_results.as_of AS result_as_of,
                mv.match_version_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.match_start_at,
                mv.tour_scope::text AS tour_scope,
                mv.competition_tier::text AS competition_tier,
                mv.surface::text AS surface,
                mv.round_code,
                mv.best_of_sets,
                mv.qualifying_flag,
                mv.source_observed_at AS version_source_observed_at,
                mv.as_of AS version_as_of,
                t.tournament_id,
                t.canonical_name,
                te.season_year,
                ROW_NUMBER() OVER (
                    PARTITION BY selected_results.match_id
                    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
                ) AS version_rank
            FROM selected_results
            JOIN matches m
                ON m.match_id = selected_results.match_id
            JOIN tournament_editions te
                ON te.tournament_edition_id = m.tournament_edition_id
            JOIN tournaments t
                ON t.tournament_id = te.tournament_id
            JOIN match_versions mv
                ON mv.match_id = selected_results.match_id
            WHERE selected_results.result_rank = 1
              AND {" AND ".join(version_filters)}
        )
        SELECT
            sv.match_id,
            sv.player_1_id,
            sv.player_2_id,
            sv.match_start_at,
            sv.surface,
            sv.tour_scope,
            sv.competition_tier,
            sv.round_code,
            sv.best_of_sets,
            sv.qualifying_flag,
            sv.tournament_id,
            sv.canonical_name,
            sv.season_year,
            sv.outcome_status,
            sv.winner_side,
            sv.result_source_observed_at,
            sv.result_as_of,
            sv.version_source_observed_at,
            sv.version_as_of,
            mso.match_stat_observation_id,
            mso.player_id,
            mso.source_system,
            mso.source_observed_at,
            mso.as_of,
            mso.first_serve_points_won,
            mso.second_serve_points_won,
            mso.service_points_played,
            mso.return_points_won
        FROM selected_versions sv
        LEFT JOIN match_stat_observations mso
            ON mso.match_id = sv.match_id
           AND mso.observation_kind = 'RAW_SOURCE'
           AND mso.source_observed_at = sv.result_source_observed_at
           AND mso.as_of = sv.result_as_of
        WHERE sv.version_rank = 1
        ORDER BY
            sv.result_as_of,
            sv.result_source_observed_at,
            sv.match_id,
            mso.player_id NULLS LAST,
            mso.match_stat_observation_id NULLS LAST
    """

    rows = conn.execute(query, params).fetchall()
    grouped: dict[int, dict[str, object]] = {}
    ordered_match_ids: list[int] = []
    for row in rows:
        match_id = int(row[0])
        if match_id not in grouped:
            grouped[match_id] = {
                "base": row[:19],
                "stats": [],
            }
            ordered_match_ids.append(match_id)
        if row[19] is not None:
            grouped[match_id]["stats"].append(
                Agent2MatchStatRow(
                    match_stat_observation_id=int(row[19]),
                    player_id=int(row[20]),
                    source_system=row[21],
                    source_observed_at=row[22],
                    as_of=row[23],
                    first_serve_points_won=row[24],
                    second_serve_points_won=row[25],
                    service_points_played=row[26],
                    return_points_won=row[27],
                )
            )
    observations: list[Agent2HistoricalMatch] = []
    for match_id in ordered_match_ids:
        (
            _match_id,
            player_1_id,
            player_2_id,
            match_start_at,
            surface,
            tour_scope,
            competition_tier,
            round_code,
            best_of_sets,
            qualifying_flag,
            tournament_id,
            canonical_name,
            season_year,
            outcome_status,
            winner_side,
            result_source_observed_at,
            result_as_of,
            version_source_observed_at,
            version_as_of,
        ) = grouped[match_id]["base"]
        observations.append(
            Agent2HistoricalMatch(
                match_id=match_id,
                player_1_id=int(player_1_id),
                player_2_id=int(player_2_id),
                match_start_at=match_start_at,
                surface=surface,
                tour_scope=tour_scope,
                competition_tier=competition_tier,
                round_code=round_code,
                best_of_sets=int(best_of_sets),
                qualifying_flag=bool(qualifying_flag),
                tournament_id=int(tournament_id),
                canonical_tournament_name=canonical_name,
                normalized_tournament_alias=normalize_tournament_alias(canonical_name),
                season_year=int(season_year),
                outcome_status=outcome_status,
                winner_side=winner_side,
                result_source_observed_at=result_source_observed_at,
                result_as_of=result_as_of,
                version_source_observed_at=version_source_observed_at,
                version_as_of=version_as_of,
                stat_rows=tuple(grouped[match_id]["stats"]),
            )
        )
    return observations


def _default_player_history_snapshot(player_id: int) -> PlayerHistorySnapshot:
    return PlayerHistorySnapshot(player_id=player_id)


def _player_snapshot_for_surface(
    snapshot: Agent2HistorySnapshot,
    *,
    player_id: int,
    surface: Surface | None,
) -> PlayerServeReturnState:
    player = snapshot.players.get(player_id, _default_player_history_snapshot(player_id))
    surface_state = (
        SurfacePointSnapshot()
        if surface is None
        else player.surface_points.get(surface, SurfacePointSnapshot())
    )
    return PlayerServeReturnState(
        player_id=player_id,
        prior_usable_matches=player.prior_usable_matches,
        overall_serve_points_won=player.overall_serve_points_won,
        overall_serve_points_played=player.overall_serve_points_played,
        overall_return_points_won=player.overall_return_points_won,
        overall_return_points_played=player.overall_return_points_played,
        surface_serve_points_won=surface_state.serve_points_won,
        surface_serve_points_played=surface_state.serve_points_played,
        surface_return_points_won=surface_state.return_points_won,
        surface_return_points_played=surface_state.return_points_played,
    )


def _population_prior_for_context(
    snapshot: Agent2HistorySnapshot,
    *,
    surface: Surface | None,
    tour_scope: TourScope,
) -> PopulationPriorState:
    if surface is None:
        return PopulationPriorState(
            surface_tour_service_points_won=0,
            surface_tour_service_points_played=0,
            surface_service_points_won=0,
            surface_service_points_played=0,
            global_service_points_won=snapshot.population.global_service_points_won,
            global_service_points_played=snapshot.population.global_service_points_played,
        )
    surface_tour = snapshot.population.surface_tour_service_points.get((surface, tour_scope), (0, 0))
    surface_total = snapshot.population.surface_service_points.get(surface, (0, 0))
    return PopulationPriorState(
        surface_tour_service_points_won=surface_tour[0],
        surface_tour_service_points_played=surface_tour[1],
        surface_service_points_won=surface_total[0],
        surface_service_points_played=surface_total[1],
        global_service_points_won=snapshot.population.global_service_points_won,
        global_service_points_played=snapshot.population.global_service_points_played,
    )


def build_prediction_request_from_snapshot(
    observation: Agent2HistoricalMatch,
    snapshot: Agent2HistorySnapshot,
    *,
    prediction_as_of: datetime | None = None,
) -> PredictionRequest:
    resolver = resolve_scoring_rule(
        canonical_tournament_name=observation.canonical_tournament_name,
        normalized_tournament_alias=observation.normalized_tournament_alias,
        season_year=observation.season_year,
        tour_scope=observation.tour_scope,
        competition_tier=observation.competition_tier,
        round_code=observation.round_code,
        best_of_sets=observation.best_of_sets,
        qualifying_flag=observation.qualifying_flag,
    )
    return PredictionRequest(
        player_1_id=observation.player_1_id,
        player_2_id=observation.player_2_id,
        surface=observation.surface,
        tour_scope=observation.tour_scope,
        competition_tier=observation.competition_tier,
        best_of_sets=observation.best_of_sets,
        scoring_rule_version=resolver.scoring_rule_version,
        player_1_state=_player_snapshot_for_surface(
            snapshot, player_id=observation.player_1_id, surface=observation.surface
        ),
        player_2_state=_player_snapshot_for_surface(
            snapshot, player_id=observation.player_2_id, surface=observation.surface
        ),
        population_prior_state=_population_prior_for_context(
            snapshot, surface=observation.surface, tour_scope=observation.tour_scope
        ),
        as_of=prediction_as_of or observation.visibility_cohort,
        resolver_abstain_reason=resolver.abstain_reason,
    )


def _stat_row_is_usable(stat_row: Agent2MatchStatRow) -> bool:
    if stat_row.service_points_played is None or stat_row.service_points_played <= 0:
        return False
    if stat_row.first_serve_points_won is None or stat_row.second_serve_points_won is None:
        return False
    service_points_won = stat_row.service_points_won
    if service_points_won is None or service_points_won < 0:
        return False
    return service_points_won <= stat_row.service_points_played


def _coherent_two_sided_rows(
    observation: Agent2HistoricalMatch,
) -> tuple[Agent2MatchStatRow, Agent2MatchStatRow] | None:
    if len(observation.stat_rows) != 2:
        return None
    rows_by_player = {row.player_id: row for row in observation.stat_rows}
    if set(rows_by_player) != {observation.player_1_id, observation.player_2_id}:
        return None
    player_1_row = rows_by_player[observation.player_1_id]
    player_2_row = rows_by_player[observation.player_2_id]
    if not _stat_row_is_usable(player_1_row) or not _stat_row_is_usable(player_2_row):
        return None
    return player_1_row, player_2_row


def historical_match_is_eligible_for_update(observation: Agent2HistoricalMatch) -> bool:
    if observation.outcome_status != "COMPLETED":
        return False
    if observation.surface is None:
        return False
    resolver = resolve_scoring_rule(
        canonical_tournament_name=observation.canonical_tournament_name,
        normalized_tournament_alias=observation.normalized_tournament_alias,
        season_year=observation.season_year,
        tour_scope=observation.tour_scope,
        competition_tier=observation.competition_tier,
        round_code=observation.round_code,
        best_of_sets=observation.best_of_sets,
        qualifying_flag=observation.qualifying_flag,
    )
    if resolver.scoring_rule_version is None:
        return False
    rows = _coherent_two_sided_rows(observation)
    if rows is None:
        return False
    player_1_row, player_2_row = rows
    return (
        player_1_row.service_points_played is not None
        and player_2_row.service_points_played is not None
        and player_1_row.service_points_played > 0
        and player_2_row.service_points_played > 0
    )


def _mutable_player(players: dict[int, _MutablePlayerAccumulator], player_id: int) -> _MutablePlayerAccumulator:
    accumulator = players.get(player_id)
    if accumulator is None:
        accumulator = _MutablePlayerAccumulator(player_id=player_id)
        players[player_id] = accumulator
    return accumulator


def _mutable_surface_state(
    player: _MutablePlayerAccumulator,
    surface: Surface,
) -> _MutableSurfaceAccumulator:
    accumulator = player.surface_points.get(surface)
    if accumulator is None:
        accumulator = _MutableSurfaceAccumulator()
        player.surface_points[surface] = accumulator
    return accumulator


def _apply_service_totals(
    population: _MutablePopulationAccumulator,
    *,
    surface: Surface,
    tour_scope: TourScope,
    service_points_won: int,
    service_points_played: int,
) -> None:
    surface_tour = population.surface_tour_service_points.setdefault((surface, tour_scope), [0, 0])
    surface_tour[0] += service_points_won
    surface_tour[1] += service_points_played
    surface_total = population.surface_service_points.setdefault(surface, [0, 0])
    surface_total[0] += service_points_won
    surface_total[1] += service_points_played
    population.global_service_points_won += service_points_won
    population.global_service_points_played += service_points_played


def apply_historical_match_in_place(
    players: dict[int, _MutablePlayerAccumulator],
    population: _MutablePopulationAccumulator,
    observation: Agent2HistoricalMatch,
) -> bool:
    if not historical_match_is_eligible_for_update(observation):
        return False
    player_1_row, player_2_row = _coherent_two_sided_rows(observation)  # type: ignore[misc]
    player_1_serve_points_won = player_1_row.service_points_won
    player_2_serve_points_won = player_2_row.service_points_won
    player_1_return_points_won = player_2_row.service_points_played - player_2_serve_points_won
    player_2_return_points_won = player_1_row.service_points_played - player_1_serve_points_won

    player_1 = _mutable_player(players, observation.player_1_id)
    player_2 = _mutable_player(players, observation.player_2_id)
    for player in (player_1, player_2):
        player.prior_usable_matches += 1

    player_1.overall_serve_points_won += player_1_serve_points_won
    player_1.overall_serve_points_played += player_1_row.service_points_played
    player_1.overall_return_points_won += player_1_return_points_won
    player_1.overall_return_points_played += player_2_row.service_points_played

    player_2.overall_serve_points_won += player_2_serve_points_won
    player_2.overall_serve_points_played += player_2_row.service_points_played
    player_2.overall_return_points_won += player_2_return_points_won
    player_2.overall_return_points_played += player_1_row.service_points_played

    player_1_surface = _mutable_surface_state(player_1, observation.surface)
    player_1_surface.serve_points_won += player_1_serve_points_won
    player_1_surface.serve_points_played += player_1_row.service_points_played
    player_1_surface.return_points_won += player_1_return_points_won
    player_1_surface.return_points_played += player_2_row.service_points_played

    player_2_surface = _mutable_surface_state(player_2, observation.surface)
    player_2_surface.serve_points_won += player_2_serve_points_won
    player_2_surface.serve_points_played += player_2_row.service_points_played
    player_2_surface.return_points_won += player_2_return_points_won
    player_2_surface.return_points_played += player_1_row.service_points_played

    _apply_service_totals(
        population,
        surface=observation.surface,
        tour_scope=observation.tour_scope,
        service_points_won=player_1_serve_points_won,
        service_points_played=player_1_row.service_points_played,
    )
    _apply_service_totals(
        population,
        surface=observation.surface,
        tour_scope=observation.tour_scope,
        service_points_won=player_2_serve_points_won,
        service_points_played=player_2_row.service_points_played,
    )
    return True


def _snapshot_players(players: dict[int, _MutablePlayerAccumulator]) -> dict[int, PlayerHistorySnapshot]:
    return {
        player_id: PlayerHistorySnapshot(
            player_id=player.player_id,
            prior_usable_matches=player.prior_usable_matches,
            overall_serve_points_won=player.overall_serve_points_won,
            overall_serve_points_played=player.overall_serve_points_played,
            overall_return_points_won=player.overall_return_points_won,
            overall_return_points_played=player.overall_return_points_played,
            surface_points={
                surface: SurfacePointSnapshot(
                    serve_points_won=surface_state.serve_points_won,
                    serve_points_played=surface_state.serve_points_played,
                    return_points_won=surface_state.return_points_won,
                    return_points_played=surface_state.return_points_played,
                )
                for surface, surface_state in player.surface_points.items()
            },
        )
        for player_id, player in players.items()
    }


def _snapshot_population(population: _MutablePopulationAccumulator) -> PopulationHistorySnapshot:
    return PopulationHistorySnapshot(
        surface_tour_service_points={
            key: (value[0], value[1]) for key, value in population.surface_tour_service_points.items()
        },
        surface_service_points={
            key: (value[0], value[1]) for key, value in population.surface_service_points.items()
        },
        global_service_points_won=population.global_service_points_won,
        global_service_points_played=population.global_service_points_played,
    )


def snapshot_history_state(
    players: dict[int, _MutablePlayerAccumulator],
    population: _MutablePopulationAccumulator,
) -> Agent2HistorySnapshot:
    return Agent2HistorySnapshot(
        players=_snapshot_players(players),
        population=_snapshot_population(population),
    )


def build_history_snapshot(
    observations: list[Agent2HistoricalMatch] | tuple[Agent2HistoricalMatch, ...],
    *,
    cutoff: datetime,
) -> Agent2HistorySnapshot:
    players: dict[int, _MutablePlayerAccumulator] = {}
    population = _MutablePopulationAccumulator()
    for observation in sorted(
        observations,
        key=lambda item: (
            item.result_as_of,
            item.result_source_observed_at,
            item.match_id,
            item.player_1_id,
            item.player_2_id,
        ),
    ):
        if observation.result_source_observed_at >= cutoff or observation.result_as_of >= cutoff:
            continue
        apply_historical_match_in_place(players, population, observation)
    return snapshot_history_state(players, population)


def replay_historical_predictions(
    observations: list[Agent2HistoricalMatch] | tuple[Agent2HistoricalMatch, ...],
    *,
    config: Agent2Config = DEFAULT_CONFIG,
) -> HistoricalReplayResult:
    sorted_observations = sorted(
        observations,
        key=lambda item: (
            item.visibility_cohort,
            item.result_source_observed_at,
            item.match_id,
            item.player_1_id,
            item.player_2_id,
        ),
    )
    grouped: dict[datetime, list[Agent2HistoricalMatch]] = {}
    for observation in sorted_observations:
        grouped.setdefault(observation.visibility_cohort, []).append(observation)

    players: dict[int, _MutablePlayerAccumulator] = {}
    population = _MutablePopulationAccumulator()
    records: list[HistoricalPredictionRecord] = []

    for visibility_cohort in sorted(grouped):
        cohort = sorted(
            grouped[visibility_cohort],
            key=lambda item: (
                item.result_as_of,
                item.result_source_observed_at,
                item.match_id,
                item.player_1_id,
                item.player_2_id,
            ),
        )
        frozen_snapshot = snapshot_history_state(deepcopy(players), deepcopy(population))
        for observation in cohort:
            request = build_prediction_request_from_snapshot(
                observation,
                frozen_snapshot,
                prediction_as_of=visibility_cohort,
            )
            records.append(
                HistoricalPredictionRecord(
                    observation=observation,
                    request=request,
                    result=predict(request, config),
                )
            )
        for observation in cohort:
            apply_historical_match_in_place(players, population, observation)

    return HistoricalReplayResult(
        records=tuple(records),
        final_snapshot=snapshot_history_state(players, population),
    )
