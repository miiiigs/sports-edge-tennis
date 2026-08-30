from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import time
import tracemalloc
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    AGENT_CODE,
    DEFAULT_CONFIG,
    HistoricalMatchObservation,
    PlayerRatingState,
    PredictionDiagnostics,
    PredictionRequest,
    PredictionResult,
    SurfaceEloConfig,
    _apply_historical_observation_in_place,
    _default_player_state,
    _quantize_probability,
    effective_rating,
    expected_score,
    surface_weight,
)
from sports_edge_tennis.data.contracts import SackmannMatchRecord
from sports_edge_tennis.data.historical_validation import build_local_sackmann_documents
from sports_edge_tennis.data.sources.sackmann import parse_sackmann_csv

RUNNER_VERSION = "agent1_walk_forward_validation_v1"
WARMUP_YEAR_START = 2016
WARMUP_YEAR_END = 2018
EVALUATION_YEAR_START = 2019
EVALUATION_YEAR_END = 2025
SMALL_SAMPLE_THRESHOLD = 100
LOG_LOSS_EPSILON = 1e-15
DEFAULT_ARTIFACT_ROOT = Path("artifacts/agent1_validation")
DEFAULT_HISTORICAL_SUMMARY = Path(
    "artifacts/historical_ingest/full_2016_2025/full_ingest_summary.json"
)
DEFAULT_ACQUISITION_MANIFEST = Path(
    "artifacts/historical_ingest/full_2016_2025/acquisition_manifest.json"
)
DEFAULT_RAW_ROOT = Path("data/raw/sackmann")
SCORABLE_STATUSES = frozenset({"COMPLETED"})
EVALUATION_STATUSES = ("COMPLETED", "RETIRED", "WALKOVER", "CANCELLED", "POSTPONED")
CALIBRATION_BINS = (
    ("[0.0,0.1)", Decimal("0.0"), Decimal("0.1"), False),
    ("[0.1,0.2)", Decimal("0.1"), Decimal("0.2"), False),
    ("[0.2,0.3)", Decimal("0.2"), Decimal("0.3"), False),
    ("[0.3,0.4)", Decimal("0.3"), Decimal("0.4"), False),
    ("[0.4,0.5)", Decimal("0.4"), Decimal("0.5"), False),
    ("[0.5,0.6)", Decimal("0.5"), Decimal("0.6"), False),
    ("[0.6,0.7)", Decimal("0.6"), Decimal("0.7"), False),
    ("[0.7,0.8)", Decimal("0.7"), Decimal("0.8"), False),
    ("[0.8,0.9)", Decimal("0.8"), Decimal("0.9"), False),
    ("[0.9,1.0]", Decimal("0.9"), Decimal("1.0"), True),
)
CONFIDENCE_BINS = (
    ("0.50-0.55", Decimal("0.50"), Decimal("0.55"), False),
    ("0.55-0.60", Decimal("0.55"), Decimal("0.60"), False),
    ("0.60-0.65", Decimal("0.60"), Decimal("0.65"), False),
    ("0.65-0.70", Decimal("0.65"), Decimal("0.70"), False),
    ("0.70-0.75", Decimal("0.70"), Decimal("0.75"), False),
    ("0.75-0.80", Decimal("0.75"), Decimal("0.80"), False),
    ("0.80+", Decimal("0.80"), Decimal("1.0"), True),
)


class ValidationError(RuntimeError):
    """Raised when the walk-forward run cannot be trusted."""


@dataclass(frozen=True)
class ValidationObservation:
    season_year: int
    match_id: int
    player_1_id: int
    player_2_id: int
    surface: str | None
    tour_scope: str
    competition_tier: str
    best_of_sets: int
    qualifying_flag: bool
    match_start_at: datetime | None
    outcome_status: str
    winner_side: int | None
    source_observed_at: datetime
    as_of: datetime
    original_player_1_id: int | None = None
    original_player_2_id: int | None = None
    original_winner_side: int | None = None
    side_swapped: bool = False

    def __post_init__(self) -> None:
        if self.season_year <= 0:
            raise ValidationError("season_year must be positive")
        if self.match_id <= 0:
            raise ValidationError("match_id must be positive")
        if self.player_1_id <= 0 or self.player_2_id <= 0:
            raise ValidationError("player ids must be positive")
        if self.player_1_id == self.player_2_id:
            raise ValidationError("player ids must differ")
        if self.as_of < self.source_observed_at:
            raise ValidationError("observation as_of must be >= source_observed_at")

    @property
    def visibility_at(self) -> datetime:
        return max(self.source_observed_at, self.as_of)

    def history_sort_key(self) -> tuple[datetime, datetime, int, int, int]:
        return (
            self.as_of,
            self.source_observed_at,
            self.match_id,
            self.player_1_id,
            self.player_2_id,
        )

    def to_historical_observation(self) -> HistoricalMatchObservation:
        return HistoricalMatchObservation(
            match_id=self.match_id,
            player_1_id=self.player_1_id,
            player_2_id=self.player_2_id,
            surface=self.surface,
            tour_scope=self.tour_scope,
            competition_tier=self.competition_tier,
            best_of_sets=self.best_of_sets,
            qualifying_flag=self.qualifying_flag,
            match_start_at=self.match_start_at,
            outcome_status=self.outcome_status,
            winner_side=self.winner_side,
            source_observed_at=self.source_observed_at,
            as_of=self.as_of,
        )


@dataclass(frozen=True)
class EvaluationTarget:
    season_year: int
    match_id: int
    player_1_id: int
    player_2_id: int
    surface: str | None
    tour_scope: str
    competition_tier: str
    best_of_sets: int
    qualifying_flag: bool
    prediction_boundary: datetime
    original_player_1_id: int
    original_player_2_id: int
    side_swapped: bool

    def to_request(self) -> PredictionRequest:
        return PredictionRequest(
            player_1_id=self.player_1_id,
            player_2_id=self.player_2_id,
            surface=self.surface,
            tour_scope=self.tour_scope,
            competition_tier=self.competition_tier,
            best_of_sets=self.best_of_sets,
            qualifying_flag=self.qualifying_flag,
            as_of=self.prediction_boundary,
        )


@dataclass
class RunnerState:
    player_states: dict[int, PlayerRatingState]
    eligible_history_match_count: int = 0
    skipped_retired_match_count: int = 0
    skipped_walkover_match_count: int = 0
    skipped_cancelled_match_count: int = 0
    skipped_postponed_match_count: int = 0

    @classmethod
    def empty(cls) -> RunnerState:
        return cls(player_states={})

    def apply_revealed_observation(
        self,
        observation: ValidationObservation,
        config: SurfaceEloConfig = DEFAULT_CONFIG,
    ) -> None:
        history_observation = observation.to_historical_observation()
        if history_observation.outcome_status == "COMPLETED":
            _apply_historical_observation_in_place(self.player_states, history_observation, config)
            self.eligible_history_match_count += 1
            return
        if history_observation.outcome_status == "RETIRED":
            self.skipped_retired_match_count += 1
        elif history_observation.outcome_status == "WALKOVER":
            self.skipped_walkover_match_count += 1
        elif history_observation.outcome_status == "CANCELLED":
            self.skipped_cancelled_match_count += 1
        elif history_observation.outcome_status == "POSTPONED":
            self.skipped_postponed_match_count += 1
        else:  # pragma: no cover - protected by historical ingestion gates
            raise ValidationError(f"unsupported outcome status: {history_observation.outcome_status}")


@dataclass(frozen=True)
class PredictionRecord:
    season_year: int
    match_id: int
    evaluation_player_1_id: int
    evaluation_player_2_id: int
    original_player_1_id: int
    original_player_2_id: int
    original_winner_side: int | None
    actual_winner_side: int | None
    side_swapped: bool
    surface: str | None
    tour_scope: str
    competition_tier: str
    qualifying_flag: bool
    visibility_cohort: datetime
    prediction_as_of: datetime
    outcome_status: str
    scored: bool
    p1: Decimal
    p2: Decimal
    selected_side: int
    selected_probability: Decimal
    y: int | None
    selected_side_won: bool | None
    exact_tie: bool
    player_1_overall_prior_matches: int
    player_2_overall_prior_matches: int
    player_1_surface_prior_matches: int
    player_2_surface_prior_matches: int
    player_1_cold_start: bool
    player_2_cold_start: bool
    player_1_surface_cold_start: bool
    player_2_surface_cold_start: bool
    player_1_surface_weight: Decimal
    player_2_surface_weight: Decimal
    unknown_surface: bool
    model_version: str
    feature_version: str
    config_version: str
    parameter_schema_version: str

    @property
    def overall_history_band(self) -> str:
        return _overall_history_band(
            min(self.player_1_overall_prior_matches, self.player_2_overall_prior_matches)
        )

    @property
    def surface_history_band(self) -> str:
        return _surface_history_band(
            min(self.player_1_surface_prior_matches, self.player_2_surface_prior_matches)
        )

    @property
    def selected_confidence(self) -> Decimal:
        return self.selected_probability

    @property
    def surface_label(self) -> str:
        return self.surface or "UNKNOWN"

    @property
    def qualifying_label(self) -> str:
        return "qualifying" if self.qualifying_flag else "non_qualifying"

    def to_row(self) -> dict[str, object]:
        return {
            "season_year": self.season_year,
            "match_id": self.match_id,
            "evaluation_player_1_id": self.evaluation_player_1_id,
            "evaluation_player_2_id": self.evaluation_player_2_id,
            "original_player_1_id": self.original_player_1_id,
            "original_player_2_id": self.original_player_2_id,
            "original_winner_side": self.original_winner_side,
            "actual_winner_side": self.actual_winner_side,
            "side_swapped": self.side_swapped,
            "surface": self.surface_label,
            "tour_scope": self.tour_scope,
            "competition_tier": self.competition_tier,
            "qualifying_flag": self.qualifying_flag,
            "visibility_cohort": self.visibility_cohort.isoformat(),
            "prediction_as_of": self.prediction_as_of.isoformat(),
            "outcome_status": self.outcome_status,
            "scored": self.scored,
            "p1": str(self.p1),
            "p2": str(self.p2),
            "selected_side": self.selected_side,
            "selected_probability": str(self.selected_probability),
            "y": self.y,
            "selected_side_won": self.selected_side_won,
            "exact_tie": self.exact_tie,
            "player_1_overall_prior_matches": self.player_1_overall_prior_matches,
            "player_2_overall_prior_matches": self.player_2_overall_prior_matches,
            "player_1_surface_prior_matches": self.player_1_surface_prior_matches,
            "player_2_surface_prior_matches": self.player_2_surface_prior_matches,
            "player_1_cold_start": self.player_1_cold_start,
            "player_2_cold_start": self.player_2_cold_start,
            "player_1_surface_cold_start": self.player_1_surface_cold_start,
            "player_2_surface_cold_start": self.player_2_surface_cold_start,
            "player_1_surface_weight": str(self.player_1_surface_weight),
            "player_2_surface_weight": str(self.player_2_surface_weight),
            "unknown_surface": self.unknown_surface,
            "overall_history_band": self.overall_history_band,
            "surface_history_band": self.surface_history_band,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "config_version": self.config_version,
            "parameter_schema_version": self.parameter_schema_version,
        }


def load_validation_observations(
    conn: psycopg.Connection,
    *,
    year_start: int = WARMUP_YEAR_START,
    year_end: int = EVALUATION_YEAR_END,
) -> list[ValidationObservation]:
    rows = conn.execute(
        """
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
        ),
        eligible_history AS (
            SELECT
                te.season_year,
                selected_results.match_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.surface::text AS surface,
                mv.tour_scope::text AS tour_scope,
                mv.competition_tier::text AS competition_tier,
                mv.best_of_sets,
                mv.qualifying_flag,
                mv.match_start_at,
                selected_results.outcome_status,
                selected_results.winner_side,
                selected_results.source_observed_at,
                selected_results.as_of,
                ROW_NUMBER() OVER (
                    PARTITION BY selected_results.match_id
                    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
                ) AS version_rank
            FROM selected_results
            JOIN matches m
              ON m.match_id = selected_results.match_id
            JOIN tournament_editions te
              ON te.tournament_edition_id = m.tournament_edition_id
            JOIN match_versions mv
              ON mv.match_id = selected_results.match_id
            WHERE selected_results.result_rank = 1
              AND te.season_year BETWEEN %(year_start)s AND %(year_end)s
              AND mv.source_observed_at <= selected_results.as_of
              AND mv.as_of <= selected_results.as_of
        )
        SELECT
            season_year,
            match_id,
            player_1_id,
            player_2_id,
            surface,
            tour_scope,
            competition_tier,
            best_of_sets,
            qualifying_flag,
            match_start_at,
            outcome_status,
            winner_side,
            source_observed_at,
            as_of
        FROM eligible_history
        WHERE version_rank = 1
        ORDER BY as_of, source_observed_at, match_id, player_1_id, player_2_id
        """,
        {"year_start": year_start, "year_end": year_end},
    ).fetchall()
    observations = [
        ValidationObservation(
            season_year=row[0],
            match_id=row[1],
            player_1_id=row[2],
            player_2_id=row[3],
            surface=row[4],
            tour_scope=row[5],
            competition_tier=row[6],
            best_of_sets=row[7],
            qualifying_flag=row[8],
            match_start_at=row[9],
            outcome_status=row[10],
            winner_side=row[11],
            source_observed_at=row[12],
            as_of=row[13],
        )
        for row in rows
    ]
    return observations


def load_validation_observations_from_raw(
    *,
    raw_root: Path,
    source_revision: str,
    year_start: int = WARMUP_YEAR_START,
    year_end: int = EVALUATION_YEAR_END,
) -> list[ValidationObservation]:
    documents = build_local_sackmann_documents(
        years=range(year_start, year_end + 1),
        raw_root=raw_root,
        source_revision=source_revision,
    )
    observations: list[ValidationObservation] = []
    match_id_by_hash: dict[str, int] = {}
    next_match_id = 1
    for document in documents:
        parse_outcome = parse_sackmann_csv(document)
        for record in parse_outcome.records:
            if record.source_record_hash not in match_id_by_hash:
                match_id_by_hash[record.source_record_hash] = next_match_id
                next_match_id += 1
            observations.append(
                _record_to_validation_observation(
                    record,
                    match_id=match_id_by_hash[record.source_record_hash],
                )
            )
    return sorted(
        observations,
        key=lambda item: (
            item.as_of,
            item.source_observed_at,
            item.match_id,
            item.player_1_id,
            item.player_2_id,
        ),
    )


def normalize_observation_for_evaluation(observation: ValidationObservation) -> ValidationObservation:
    if observation.player_1_id <= observation.player_2_id:
        return ValidationObservation(
            season_year=observation.season_year,
            match_id=observation.match_id,
            player_1_id=observation.player_1_id,
            player_2_id=observation.player_2_id,
            surface=observation.surface,
            tour_scope=observation.tour_scope,
            competition_tier=observation.competition_tier,
            best_of_sets=observation.best_of_sets,
            qualifying_flag=observation.qualifying_flag,
            match_start_at=observation.match_start_at,
            outcome_status=observation.outcome_status,
            winner_side=observation.winner_side,
            source_observed_at=observation.source_observed_at,
            as_of=observation.as_of,
            original_player_1_id=observation.original_player_1_id or observation.player_1_id,
            original_player_2_id=observation.original_player_2_id or observation.player_2_id,
            original_winner_side=observation.original_winner_side
            if observation.original_winner_side is not None
            else observation.winner_side,
            side_swapped=observation.side_swapped,
        )

    remapped_winner_side = observation.winner_side
    if remapped_winner_side == 1:
        remapped_winner_side = 2
    elif remapped_winner_side == 2:
        remapped_winner_side = 1
    return ValidationObservation(
        season_year=observation.season_year,
        match_id=observation.match_id,
        player_1_id=observation.player_2_id,
        player_2_id=observation.player_1_id,
        surface=observation.surface,
        tour_scope=observation.tour_scope,
        competition_tier=observation.competition_tier,
        best_of_sets=observation.best_of_sets,
        qualifying_flag=observation.qualifying_flag,
        match_start_at=observation.match_start_at,
        outcome_status=observation.outcome_status,
        winner_side=remapped_winner_side,
        source_observed_at=observation.source_observed_at,
        as_of=observation.as_of,
        original_player_1_id=observation.original_player_1_id or observation.player_1_id,
        original_player_2_id=observation.original_player_2_id or observation.player_2_id,
        original_winner_side=observation.original_winner_side
        if observation.original_winner_side is not None
        else observation.winner_side,
        side_swapped=not observation.side_swapped,
    )


def build_prediction_target(observation: ValidationObservation) -> EvaluationTarget:
    return EvaluationTarget(
        season_year=observation.season_year,
        match_id=observation.match_id,
        player_1_id=observation.player_1_id,
        player_2_id=observation.player_2_id,
        surface=observation.surface,
        tour_scope=observation.tour_scope,
        competition_tier=observation.competition_tier,
        best_of_sets=observation.best_of_sets,
        qualifying_flag=observation.qualifying_flag,
        prediction_boundary=observation.visibility_at,
        original_player_1_id=observation.original_player_1_id or observation.player_1_id,
        original_player_2_id=observation.original_player_2_id or observation.player_2_id,
        side_swapped=observation.side_swapped,
    )


def predict_from_state(
    target: EvaluationTarget,
    state: RunnerState,
    config: SurfaceEloConfig = DEFAULT_CONFIG,
) -> PredictionResult:
    request = target.to_request()
    player_1_state = state.player_states.get(
        request.player_1_id, _default_player_state(request.player_1_id, config)
    )
    player_2_state = state.player_states.get(
        request.player_2_id, _default_player_state(request.player_2_id, config)
    )

    player_1_surface_matches = 0
    player_2_surface_matches = 0
    player_1_surface_weight = Decimal("0")
    player_2_surface_weight = Decimal("0")
    unknown_surface = request.surface is None
    if request.surface is not None:
        player_1_surface_matches = player_1_state.surface_match_count(request.surface)
        player_2_surface_matches = player_2_state.surface_match_count(request.surface)
        player_1_surface_weight = surface_weight(player_1_surface_matches, config)
        player_2_surface_weight = surface_weight(player_2_surface_matches, config)

    rating_1 = effective_rating(player_1_state, request.surface, config)
    rating_2 = effective_rating(player_2_state, request.surface, config)
    player_1_probability_raw = expected_score(rating_1, rating_2, config)
    player_2_probability_raw = Decimal("1") - player_1_probability_raw
    tie = player_1_probability_raw == Decimal("0.5")
    if player_1_probability_raw >= Decimal("0.5"):
        selection_side = 1
        selected_probability = player_1_probability_raw
    else:
        selection_side = 2
        selected_probability = player_2_probability_raw

    diagnostics = PredictionDiagnostics(
        player_1_overall_matches=player_1_state.overall_match_count,
        player_2_overall_matches=player_2_state.overall_match_count,
        player_1_surface_matches=player_1_surface_matches,
        player_2_surface_matches=player_2_surface_matches,
        player_1_cold_start=player_1_state.overall_match_count == 0,
        player_2_cold_start=player_2_state.overall_match_count == 0,
        player_1_surface_cold_start=player_1_surface_matches == 0,
        player_2_surface_cold_start=player_2_surface_matches == 0,
        player_1_surface_weight=player_1_surface_weight,
        player_2_surface_weight=player_2_surface_weight,
        unknown_surface=unknown_surface,
        tie=tie,
        eligible_history_match_count=state.eligible_history_match_count,
        skipped_future_observation_count=0,
        skipped_retired_match_count=state.skipped_retired_match_count,
        skipped_walkover_match_count=state.skipped_walkover_match_count,
        skipped_cancelled_match_count=state.skipped_cancelled_match_count,
        skipped_postponed_match_count=state.skipped_postponed_match_count,
    )
    return PredictionResult(
        agent_code=AGENT_CODE,
        prediction_status="PREDICTED",
        selection_side=selection_side,
        win_probability=_quantize_probability(selected_probability, config),
        player_1_probability=_quantize_probability(player_1_probability_raw, config),
        player_2_probability=_quantize_probability(player_2_probability_raw, config),
        model_version=config.model_version,
        feature_version=config.feature_version,
        as_of=request.as_of,
        diagnostics=diagnostics,
    )


def run_walk_forward_validation(
    observations: list[ValidationObservation] | tuple[ValidationObservation, ...],
    *,
    config: SurfaceEloConfig = DEFAULT_CONFIG,
    small_sample_threshold: int = SMALL_SAMPLE_THRESHOLD,
) -> dict[str, object]:
    tracemalloc.start()
    started_at = time.perf_counter()
    run_timestamp = datetime.now(UTC)
    source_observations = list(observations)
    observations = sorted(
        [normalize_observation_for_evaluation(item) for item in source_observations],
        key=lambda item: (
            item.visibility_at,
            item.as_of,
            item.source_observed_at,
            item.match_id,
            item.player_1_id,
            item.player_2_id,
        ),
    )
    normalized_observations = list(observations)
    _validate_observation_preconditions(normalized_observations)

    state = RunnerState.empty()
    prediction_records: list[PredictionRecord] = []
    cohort_count = 0
    evaluation_total = 0
    status_counter: Counter[str] = Counter()
    grouped: dict[datetime, list[ValidationObservation]] = defaultdict(list)
    for observation in normalized_observations:
        grouped[observation.visibility_at].append(observation)

    for cohort_visibility in sorted(grouped):
        cohort_count += 1
        cohort = sorted(grouped[cohort_visibility], key=lambda item: item.history_sort_key())
        evaluation_cohort = [
            item
            for item in cohort
            if EVALUATION_YEAR_START <= item.season_year <= EVALUATION_YEAR_END
        ]
        evaluation_total += len(evaluation_cohort)
        predictions: list[tuple[ValidationObservation, PredictionResult]] = []
        for observation in evaluation_cohort:
            target = build_prediction_target(observation)
            prediction = predict_from_state(target, state, config)
            predictions.append((observation, prediction))
        for observation, prediction in predictions:
            status_counter[observation.outcome_status] += 1
            scored = observation.outcome_status in SCORABLE_STATUSES
            y = None
            selected_side_won = None
            if scored:
                if observation.winner_side not in (1, 2):
                    raise ValidationError("completed evaluation observation missing winner_side")
                y = 1 if observation.winner_side == 1 else 0
                selected_side_won = observation.winner_side == prediction.selection_side
            prediction_records.append(
                PredictionRecord(
                    season_year=observation.season_year,
                    match_id=observation.match_id,
                    evaluation_player_1_id=observation.player_1_id,
                    evaluation_player_2_id=observation.player_2_id,
                    original_player_1_id=observation.original_player_1_id or observation.player_1_id,
                    original_player_2_id=observation.original_player_2_id or observation.player_2_id,
                    original_winner_side=observation.original_winner_side,
                    actual_winner_side=observation.winner_side,
                    side_swapped=observation.side_swapped,
                    surface=observation.surface,
                    tour_scope=observation.tour_scope,
                    competition_tier=observation.competition_tier,
                    qualifying_flag=observation.qualifying_flag,
                    visibility_cohort=cohort_visibility,
                    prediction_as_of=cohort_visibility,
                    outcome_status=observation.outcome_status,
                    scored=scored,
                    p1=prediction.player_1_probability,
                    p2=prediction.player_2_probability,
                    selected_side=prediction.selection_side,
                    selected_probability=prediction.win_probability,
                    y=y,
                    selected_side_won=selected_side_won,
                    exact_tie=prediction.diagnostics.tie,
                    player_1_overall_prior_matches=prediction.diagnostics.player_1_overall_matches,
                    player_2_overall_prior_matches=prediction.diagnostics.player_2_overall_matches,
                    player_1_surface_prior_matches=prediction.diagnostics.player_1_surface_matches,
                    player_2_surface_prior_matches=prediction.diagnostics.player_2_surface_matches,
                    player_1_cold_start=prediction.diagnostics.player_1_cold_start,
                    player_2_cold_start=prediction.diagnostics.player_2_cold_start,
                    player_1_surface_cold_start=prediction.diagnostics.player_1_surface_cold_start,
                    player_2_surface_cold_start=prediction.diagnostics.player_2_surface_cold_start,
                    player_1_surface_weight=prediction.diagnostics.player_1_surface_weight,
                    player_2_surface_weight=prediction.diagnostics.player_2_surface_weight,
                    unknown_surface=prediction.diagnostics.unknown_surface,
                    model_version=prediction.model_version,
                    feature_version=prediction.feature_version,
                    config_version=config.config_version,
                    parameter_schema_version=config.parameter_schema_version,
                )
            )
        for observation in cohort:
            state.apply_revealed_observation(observation, config)

    elapsed_seconds = time.perf_counter() - started_at
    _, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    scored_records = [record for record in prediction_records if record.scored]
    if len(prediction_records) != evaluation_total:
        raise ValidationError("prediction generation did not cover every evaluation observation")
    if len({record.match_id for record in prediction_records}) != len(prediction_records):
        raise ValidationError("duplicate evaluation match ids detected")
    quality_checks = _data_quality_checks(normalized_observations, prediction_records)
    if any(value != 0 for value in quality_checks.values()):
        raise ValidationError(f"data quality preconditions failed: {quality_checks}")

    overall_metrics = _score_records(scored_records, small_sample_threshold=small_sample_threshold)
    baseline_log_loss = math.log(2.0)
    baseline_brier = 0.25
    baseline_accuracy = 0.5
    improvement = {
        "log_loss_absolute_improvement": _round_metric(baseline_log_loss - overall_metrics["log_loss"]),
        "log_loss_relative_improvement_pct": _round_metric(
            ((baseline_log_loss - overall_metrics["log_loss"]) / baseline_log_loss) * 100.0
        ),
        "brier_absolute_improvement": _round_metric(baseline_brier - overall_metrics["brier_score"]),
        "brier_relative_improvement_pct": _round_metric(
            ((baseline_brier - overall_metrics["brier_score"]) / baseline_brier) * 100.0
        ),
        "accuracy_difference_vs_50_pct": _round_metric((overall_metrics["accuracy"] - baseline_accuracy) * 100.0),
    }
    calibration_rows, ece, max_gap = _calibration_table(scored_records)
    selected_confidence_rows = _selected_confidence_table(scored_records)
    exact_tie_scored = [record for record in scored_records if record.exact_tie]
    exact_tie_accuracy = (
        sum(1 for record in exact_tie_scored if record.selected_side_won) / len(exact_tie_scored)
        if exact_tie_scored
        else None
    )

    segment_surface = _segment_metrics(scored_records, lambda record: record.surface_label, small_sample_threshold)
    segment_competition = _segment_metrics(
        scored_records, lambda record: record.tour_scope, small_sample_threshold
    )
    segment_qualifying = _segment_metrics(
        scored_records, lambda record: record.qualifying_label, small_sample_threshold
    )
    segment_year = _segment_metrics(
        scored_records, lambda record: str(record.season_year), small_sample_threshold, include_ece=True
    )
    segment_history_overall = _segment_metrics(
        scored_records, lambda record: record.overall_history_band, small_sample_threshold
    )
    segment_history_surface = _segment_metrics(
        scored_records, lambda record: record.surface_history_band, small_sample_threshold
    )

    raw_distribution = _winner_side_distribution(source_observations)
    normalized_distribution = _winner_side_distribution(normalized_observations)
    canonical_artifact_hash = _sha256_text(
        _stable_json_dumps(
            {
                "overall_metrics": overall_metrics,
                "baseline": {
                    "log_loss": baseline_log_loss,
                    "brier": baseline_brier,
                    "accuracy_reference": baseline_accuracy,
                },
                "improvement": improvement,
                "calibration": calibration_rows,
                "selected_confidence": selected_confidence_rows,
                "segment_surface": segment_surface,
                "segment_competition": segment_competition,
                "segment_qualifying": segment_qualifying,
                "segment_year": segment_year,
                "segment_history_overall": segment_history_overall,
                "segment_history_surface": segment_history_surface,
                "quality_checks": quality_checks,
            }
        )
    )

    return {
        "runner_version": RUNNER_VERSION,
        "run_timestamp": run_timestamp,
        "observations": normalized_observations,
        "prediction_records": prediction_records,
        "state": state,
        "cohort_count": cohort_count,
        "quality_checks": quality_checks,
        "evaluation_population": {
            "total_evaluation_observations": evaluation_total,
            "predictions_generated": len(prediction_records),
            "completed": status_counter.get("COMPLETED", 0),
            "retired": status_counter.get("RETIRED", 0),
            "walkover": status_counter.get("WALKOVER", 0),
            "cancelled": status_counter.get("CANCELLED", 0),
            "postponed": status_counter.get("POSTPONED", 0),
            "other": sum(
                count
                for status, count in status_counter.items()
                if status not in EVALUATION_STATUSES
            ),
            "scored_matches": len(scored_records),
            "unscored_matches": len(prediction_records) - len(scored_records),
            "coverage_pct": _round_metric(
                (len(prediction_records) / evaluation_total) * 100.0 if evaluation_total else 0.0
            ),
        },
        "overall_metrics": overall_metrics,
        "baseline_metrics": {
            "log_loss": _round_metric(baseline_log_loss),
            "brier": _round_metric(baseline_brier),
            "accuracy_reference": _round_metric(baseline_accuracy),
        },
        "improvement": improvement,
        "calibration_rows": calibration_rows,
        "ece": _round_metric(ece),
        "max_calibration_gap": _round_metric(max_gap),
        "selected_confidence_rows": selected_confidence_rows,
        "exact_ties": {
            "count": len(exact_tie_scored),
            "rate_pct": _round_metric((len(exact_tie_scored) / len(scored_records)) * 100.0 if scored_records else 0.0),
            "accuracy": _round_metric(exact_tie_accuracy) if exact_tie_accuracy is not None else None,
        },
        "segment_surface": segment_surface,
        "segment_competition": segment_competition,
        "segment_qualifying": segment_qualifying,
        "segment_year": segment_year,
        "segment_history_overall": segment_history_overall,
        "segment_history_surface": segment_history_surface,
        "side_orientation": {
            "raw_policy": "outcome-independent player-id sort",
            "outcome_derived": False,
            "evaluation_normalization_required": False,
            "normalization_rule": "sort player ids ascending and remap winner_side consistently",
            "uses_result_information": False,
            "winner_side_raw_distribution": raw_distribution,
            "winner_side_normalized_distribution": normalized_distribution,
            "exact_tie_leakage_risk": "blocked by outcome-independent side policy",
        },
        "runtime": {
            "validation_wall_time_seconds": _round_metric(elapsed_seconds),
            "peak_memory_bytes": peak_memory,
            "database_queries": 1,
            "replay_complexity": "O(matches + cohorts)",
            "per_match_full_replay": False,
            "canonical_artifact_hash": canonical_artifact_hash,
        },
    }


def write_validation_artifacts(
    artifact_root: Path,
    report: dict[str, object],
    *,
    repository_sha: str,
    config: SurfaceEloConfig,
    corpus_fingerprint: str,
    historical_summary: dict[str, object],
    acquisition_manifest_hash: str,
) -> dict[str, Path]:
    artifact_root.mkdir(parents=True, exist_ok=True)
    prediction_records = report["prediction_records"]
    if not isinstance(prediction_records, list):
        raise ValidationError("prediction_records missing from report")
    summary_payload = {
        "repository_sha": repository_sha,
        "runner_version": report["runner_version"],
        "agent": AGENT_CODE,
        "model_version": config.model_version,
        "feature_version": config.feature_version,
        "config_version": config.config_version,
        "parameter_schema_version": config.parameter_schema_version,
        "config_payload": config.to_payload(),
        "config_fingerprint": _config_fingerprint(config),
        "historical_corpus_fingerprint": corpus_fingerprint,
        "acquisition_manifest_sha256": acquisition_manifest_hash,
        "source_revision": historical_summary.get("source_revision"),
        "historical_range": "2016-2025",
        "warmup_range": "2016-2018",
        "evaluation_range": "2019-2025",
        "run_timestamp": report["run_timestamp"],
        "cohort_count": report["cohort_count"],
        "evaluation_population": report["evaluation_population"],
        "overall_metrics": {
            **report["overall_metrics"],
            "ece": report["ece"],
            "max_calibration_gap": report["max_calibration_gap"],
            "exact_ties": report["exact_ties"],
        },
        "baseline_metrics": report["baseline_metrics"],
        "improvement": report["improvement"],
        "quality_checks": report["quality_checks"],
        "side_orientation": report["side_orientation"],
        "runtime": report["runtime"],
    }
    overall_metrics_payload = {
        "agent": AGENT_CODE,
        "runner_version": RUNNER_VERSION,
        "overall_metrics": report["overall_metrics"],
        "baseline_metrics": report["baseline_metrics"],
        "improvement": report["improvement"],
        "exact_ties": report["exact_ties"],
        "ece": report["ece"],
        "max_calibration_gap": report["max_calibration_gap"],
        "quality_checks": report["quality_checks"],
    }
    paths = {
        "summary": artifact_root / "summary.json",
        "overall_metrics": artifact_root / "overall_metrics.json",
        "calibration": artifact_root / "calibration.csv",
        "selected_confidence": artifact_root / "selected_confidence_calibration.csv",
        "segment_surface": artifact_root / "segment_surface.csv",
        "segment_competition": artifact_root / "segment_competition.csv",
        "segment_qualifying": artifact_root / "segment_qualifying.csv",
        "segment_year": artifact_root / "segment_year.csv",
        "segment_history_overall": artifact_root / "segment_history_overall.csv",
        "segment_history_surface": artifact_root / "segment_history_surface.csv",
        "predictions": artifact_root / "predictions.csv",
    }
    _json_dump(paths["summary"], summary_payload)
    _json_dump(paths["overall_metrics"], overall_metrics_payload)
    _write_csv(paths["calibration"], report["calibration_rows"])
    _write_csv(paths["selected_confidence"], report["selected_confidence_rows"])
    _write_csv(paths["segment_surface"], report["segment_surface"])
    _write_csv(paths["segment_competition"], report["segment_competition"])
    _write_csv(paths["segment_qualifying"], report["segment_qualifying"])
    _write_csv(paths["segment_year"], report["segment_year"])
    _write_csv(paths["segment_history_overall"], report["segment_history_overall"])
    _write_csv(paths["segment_history_surface"], report["segment_history_surface"])
    _write_csv(paths["predictions"], [record.to_row() for record in prediction_records])
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a leakage-safe genuine walk-forward validation for Agent 1."
    )
    parser.add_argument("--db-dsn", help="Disposable local PostgreSQL DSN")
    parser.add_argument(
        "--artifact-root",
        default=str(DEFAULT_ARTIFACT_ROOT),
        help="Ignored artifact directory for validation outputs",
    )
    parser.add_argument(
        "--historical-summary",
        default=str(DEFAULT_HISTORICAL_SUMMARY),
        help="Path to the approved historical ingest summary JSON",
    )
    parser.add_argument(
        "--acquisition-manifest",
        default=str(DEFAULT_ACQUISITION_MANIFEST),
        help="Path to the approved raw acquisition manifest JSON",
    )
    parser.add_argument(
        "--raw-root",
        default=str(DEFAULT_RAW_ROOT),
        help="Local raw Sackmann root for raw-loader fallback",
    )
    parser.add_argument(
        "--source-revision",
        help="Approved raw-source revision used by the local raw-loader fallback",
    )
    parser.add_argument(
        "--force-raw-loader",
        action="store_true",
        help="Load validation observations directly from approved local raw files instead of PostgreSQL",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifact_root = Path(args.artifact_root).resolve()
    summary_path = Path(args.historical_summary).resolve()
    manifest_path = Path(args.acquisition_manifest).resolve()
    historical_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    acquisition_manifest_hash = _sha256_bytes(manifest_path.read_bytes())
    corpus_fingerprint = _corpus_fingerprint(historical_summary, acquisition_manifest_hash)
    observations: list[ValidationObservation]
    if args.force_raw_loader:
        if not args.source_revision:
            raise ValidationError("--source-revision is required with --force-raw-loader")
        observations = load_validation_observations_from_raw(
            raw_root=Path(args.raw_root).resolve(),
            source_revision=args.source_revision,
        )
    else:
        if not args.db_dsn:
            raise ValidationError("--db-dsn is required unless --force-raw-loader is used")
        with psycopg.connect(args.db_dsn) as conn:
            observations = load_validation_observations(conn)
    report = run_walk_forward_validation(observations, config=DEFAULT_CONFIG)
    artifact_paths = write_validation_artifacts(
        artifact_root,
        report,
        repository_sha=_repository_sha(),
        config=DEFAULT_CONFIG,
        corpus_fingerprint=corpus_fingerprint,
        historical_summary=historical_summary,
        acquisition_manifest_hash=acquisition_manifest_hash,
    )
    print(
        json.dumps(
            {
                "result": "VALIDATION_COMPLETE",
                "summary": str(artifact_paths["summary"]),
                "overall_metrics": str(artifact_paths["overall_metrics"]),
                "predictions": str(artifact_paths["predictions"]),
                "canonical_artifact_hash": report["runtime"]["canonical_artifact_hash"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _validate_observation_preconditions(observations: list[ValidationObservation]) -> None:
    if any(item.player_1_id == item.player_2_id for item in observations):
        raise ValidationError("self matches detected in validation observations")
    duplicates = Counter(item.match_id for item in observations if item.season_year >= EVALUATION_YEAR_START)
    duplicate_ids = [match_id for match_id, count in duplicates.items() if count > 1]
    if duplicate_ids:
        raise ValidationError(f"duplicate evaluation match ids detected: {duplicate_ids[:5]}")


def _record_to_validation_observation(
    record: SackmannMatchRecord,
    *,
    match_id: int,
) -> ValidationObservation:
    winner_source_id = _source_player_id_as_int(record.winner.source_player_id)
    loser_source_id = _source_player_id_as_int(record.loser.source_player_id)
    player_1_id, player_2_id = sorted((winner_source_id, loser_source_id))
    winner_side = 1 if winner_source_id == player_1_id else 2
    return ValidationObservation(
        season_year=record.season_year,
        match_id=match_id,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        surface=record.surface,
        tour_scope=record.tour_scope,
        competition_tier=record.competition_tier,
        best_of_sets=record.best_of_sets,
        qualifying_flag=record.qualifying_flag,
        match_start_at=record.match_start_at,
        outcome_status=record.outcome_status,
        winner_side=winner_side if record.outcome_status != "CANCELLED" else None,
        source_observed_at=record.result_source_observed_at,
        as_of=record.result_as_of,
    )


def _source_player_id_as_int(value: str | None) -> int:
    if value is None:
        raise ValidationError("raw validation record is missing stable source player id")
    try:
        player_id = int(value)
    except ValueError as exc:
        raise ValidationError(f"raw validation record has non-integer source player id: {value}") from exc
    if player_id <= 0:
        raise ValidationError("raw validation record has non-positive source player id")
    return player_id


def _data_quality_checks(
    observations: list[ValidationObservation], prediction_records: list[PredictionRecord]
) -> dict[str, int]:
    completed_without_winner = sum(
        1
        for item in observations
        if item.outcome_status == "COMPLETED" and item.winner_side not in (1, 2)
    )
    invalid_probabilities = 0
    nan_probabilities = 0
    probabilities_outside_bounds = 0
    for record in prediction_records:
        for probability in (record.p1, record.p2):
            value = float(probability)
            if math.isnan(value):
                nan_probabilities += 1
            if not (0.0 <= value <= 1.0):
                probabilities_outside_bounds += 1
        if record.p1 + record.p2 != Decimal("1.000000"):
            invalid_probabilities += 1
    return {
        "duplicate_evaluation_match_ids": max(
            0,
            len(prediction_records) - len({record.match_id for record in prediction_records}),
        ),
        "self_matches": sum(1 for item in observations if item.player_1_id == item.player_2_id),
        "unknown_accepted_statuses": sum(
            1 for item in observations if item.outcome_status not in EVALUATION_STATUSES
        ),
        "pit_violations": sum(1 for item in observations if item.as_of < item.source_observed_at),
        "missing_required_player_ids": sum(
            1 for item in observations if item.player_1_id <= 0 or item.player_2_id <= 0
        ),
        "invalid_probabilities": invalid_probabilities,
        "nan_probabilities": nan_probabilities,
        "probabilities_outside_bounds": probabilities_outside_bounds,
        "completed_missing_winner_side": completed_without_winner,
    }


def _score_records(
    records: list[PredictionRecord], *, small_sample_threshold: int
) -> dict[str, object]:
    if not records:
        raise ValidationError("no scored records available for evaluation")
    log_losses: list[float] = []
    briers: list[float] = []
    accuracies = 0
    clamp_count = 0
    confidences: list[float] = []
    for record in records:
        if record.y is None:
            raise ValidationError("scored record missing binary label")
        p1 = float(record.p1)
        clamped = min(max(p1, LOG_LOSS_EPSILON), 1.0 - LOG_LOSS_EPSILON)
        if clamped != p1:
            clamp_count += 1
        log_losses.append(-math.log(clamped if record.y == 1 else (1.0 - clamped)))
        briers.append((p1 - float(record.y)) ** 2)
        accuracies += 1 if record.selected_side_won else 0
        confidences.append(float(record.selected_confidence))
    accuracy = accuracies / len(records)
    return {
        "n": len(records),
        "log_loss": _round_metric(sum(log_losses) / len(log_losses)),
        "brier_score": _round_metric(sum(briers) / len(briers)),
        "accuracy": _round_metric(accuracy),
        "mean_selected_confidence": _round_metric(sum(confidences) / len(confidences)),
        "clamp_epsilon": LOG_LOSS_EPSILON,
        "clamp_count": clamp_count,
        "low_sample": len(records) < small_sample_threshold,
    }


def _calibration_table(records: list[PredictionRecord]) -> tuple[list[dict[str, object]], float, float]:
    rows: list[dict[str, object]] = []
    ece_accumulator = 0.0
    max_gap = 0.0
    total = len(records)
    for label, lower, upper, inclusive_upper in CALIBRATION_BINS:
        bucket = [
            record
            for record in records
            if _value_in_bin(record.p1, lower, upper, inclusive_upper)
        ]
        if bucket:
            mean_predicted = sum(float(record.p1) for record in bucket) / len(bucket)
            observed = sum(record.y or 0 for record in bucket) / len(bucket)
            gap = observed - mean_predicted
        else:
            mean_predicted = None
            observed = None
            gap = None
        if gap is not None:
            abs_gap = abs(gap)
            ece_accumulator += (len(bucket) / total) * abs_gap
            max_gap = max(max_gap, abs_gap)
        rows.append(
            {
                "bin": label,
                "n": len(bucket),
                "mean_predicted_p1": _round_metric(mean_predicted) if mean_predicted is not None else None,
                "observed_p1_win_rate": _round_metric(observed) if observed is not None else None,
                "gap": _round_metric(gap) if gap is not None else None,
            }
        )
    return rows, ece_accumulator, max_gap


def _selected_confidence_table(records: list[PredictionRecord]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for label, lower, upper, inclusive_upper in CONFIDENCE_BINS:
        bucket = [
            record
            for record in records
            if _value_in_bin(record.selected_confidence, lower, upper, inclusive_upper)
        ]
        if bucket:
            mean_confidence = sum(float(record.selected_confidence) for record in bucket) / len(bucket)
            actual_win_rate = sum(1 for record in bucket if record.selected_side_won) / len(bucket)
            gap = actual_win_rate - mean_confidence
        else:
            mean_confidence = None
            actual_win_rate = None
            gap = None
        rows.append(
            {
                "band": label,
                "n": len(bucket),
                "mean_confidence": _round_metric(mean_confidence) if mean_confidence is not None else None,
                "actual_win_rate": _round_metric(actual_win_rate) if actual_win_rate is not None else None,
                "gap": _round_metric(gap) if gap is not None else None,
            }
        )
    return rows


def _segment_metrics(
    records: list[PredictionRecord],
    key_func: Any,
    small_sample_threshold: int,
    *,
    include_ece: bool = False,
) -> list[dict[str, object]]:
    grouped: dict[str, list[PredictionRecord]] = defaultdict(list)
    for record in records:
        grouped[str(key_func(record))].append(record)
    rows: list[dict[str, object]] = []
    for label in sorted(grouped):
        bucket = grouped[label]
        metrics = _score_records(bucket, small_sample_threshold=small_sample_threshold)
        row = {
            "segment": label,
            "n": metrics["n"],
            "log_loss": metrics["log_loss"],
            "brier_score": metrics["brier_score"],
            "accuracy": metrics["accuracy"],
            "mean_selected_confidence": metrics["mean_selected_confidence"],
            "low_sample": metrics["low_sample"],
        }
        if include_ece:
            _, ece, _ = _calibration_table(bucket)
            row["ece"] = _round_metric(ece)
        rows.append(row)
    return rows


def _overall_history_band(match_count: int) -> str:
    if match_count == 0:
        return "0"
    if match_count <= 4:
        return "1-4"
    if match_count <= 9:
        return "5-9"
    if match_count <= 24:
        return "10-24"
    if match_count <= 49:
        return "25-49"
    return "50+"


def _surface_history_band(match_count: int) -> str:
    if match_count == 0:
        return "0"
    if match_count <= 4:
        return "1-4"
    if match_count <= 9:
        return "5-9"
    return "10+"


def _winner_side_distribution(observations: list[ValidationObservation]) -> dict[str, object]:
    relevant = [item.winner_side for item in observations if item.season_year >= EVALUATION_YEAR_START]
    counts = Counter(relevant)
    total = len(relevant)
    return {
        "count_side_1": counts.get(1, 0),
        "count_side_2": counts.get(2, 0),
        "count_null": counts.get(None, 0),
        "pct_side_1": _round_metric((counts.get(1, 0) / total) * 100.0 if total else 0.0),
        "pct_side_2": _round_metric((counts.get(2, 0) / total) * 100.0 if total else 0.0),
    }


def _value_in_bin(value: Decimal, lower: Decimal, upper: Decimal, inclusive_upper: bool) -> bool:
    if value < lower:
        return False
    if inclusive_upper:
        return value <= upper
    return value < upper


def _config_fingerprint(config: SurfaceEloConfig) -> str:
    return _sha256_text(_stable_json_dumps(config.to_payload()))


def _corpus_fingerprint(historical_summary: dict[str, object], manifest_hash: str) -> str:
    payload = {
        "source_revision": historical_summary.get("source_revision"),
        "counts": historical_summary.get("counts"),
        "violations": historical_summary.get("violations"),
        "manifest_sha256": manifest_hash,
    }
    return _sha256_text(_stable_json_dumps(payload))


def _repository_sha() -> str:
    git_root = Path(".git")
    if not git_root.exists():
        return "UNKNOWN"
    if git_root.is_file():
        gitdir_value = git_root.read_text(encoding="utf-8").strip()
        if not gitdir_value.startswith("gitdir: "):
            return "UNKNOWN"
        git_root = (git_root.parent / gitdir_value.removeprefix("gitdir: ").strip()).resolve()
    common_root = git_root
    commondir_path = git_root / "commondir"
    if commondir_path.exists():
        common_root = (git_root / commondir_path.read_text(encoding="utf-8").strip()).resolve()
    head_path = git_root / "HEAD"
    if not head_path.exists():
        return "UNKNOWN"
    head_value = head_path.read_text(encoding="utf-8").strip()
    if head_value.startswith("ref: "):
        ref_name = head_value.removeprefix("ref: ")
        for root in (git_root, common_root):
            ref_path = root / ref_name
            if ref_path.exists():
                return ref_path.read_text(encoding="utf-8").strip()
        for root in (git_root, common_root):
            packed_refs_path = root / "packed-refs"
            if not packed_refs_path.exists():
                continue
            for line in packed_refs_path.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#") or line.startswith("^"):
                    continue
                parts = line.split(" ", maxsplit=1)
                if len(parts) == 2 and parts[1].strip() == ref_name:
                    return parts[0].strip()
        return "UNKNOWN"
    return head_value


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _json_dump(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_stable_json_dumps(payload, pretty=True) + "\n", encoding="utf-8")


def _stable_json_dumps(payload: Any, *, pretty: bool = False) -> str:
    indent = 2 if pretty else None
    return json.dumps(_json_ready(payload), indent=indent, sort_keys=True)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, PredictionRecord):
        return value.to_row()
    return value


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _round_metric(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 12)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
