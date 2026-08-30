from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Literal


AGENT_CODE = "AGENT_1_SURFACE_ELO"
SUPPORTED_SURFACES = ("HARD", "CLAY", "GRASS", "CARPET")
SUPPORTED_TOUR_SCOPES = ("ATP_MAIN", "CHALLENGER")
SUPPORTED_COMPETITION_TIERS = (
    "GRAND_SLAM",
    "MASTERS_1000",
    "ATP_TOUR",
    "ATP_500",
    "ATP_250",
    "CHALLENGER",
)
SUPPORTED_OUTCOME_STATUSES = ("COMPLETED", "RETIRED", "WALKOVER", "CANCELLED", "POSTPONED")
_PROBABILITY_QUANTUM = Decimal("0.000001")

Surface = Literal["HARD", "CLAY", "GRASS", "CARPET"]
TourScope = Literal["ATP_MAIN", "CHALLENGER"]
CompetitionTier = Literal[
    "GRAND_SLAM",
    "MASTERS_1000",
    "ATP_TOUR",
    "ATP_500",
    "ATP_250",
    "CHALLENGER",
]
OutcomeStatus = Literal["COMPLETED", "RETIRED", "WALKOVER", "CANCELLED", "POSTPONED"]


class SurfaceEloValidationError(ValueError):
    """Raised when Agent 1 receives structurally invalid input."""


def _as_decimal(value: Decimal | int | str, *, field_name: str) -> Decimal:
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:  # pragma: no cover - defensive conversion path
        raise SurfaceEloValidationError(f"{field_name} must be Decimal-compatible") from exc
    if not decimal_value.is_finite():
        raise SurfaceEloValidationError(f"{field_name} must be finite")
    return decimal_value


def _validate_surface(surface: str | None, *, field_name: str = "surface") -> None:
    if surface is None:
        return
    if surface not in SUPPORTED_SURFACES:
        raise SurfaceEloValidationError(
            f"{field_name} must be one of {SUPPORTED_SURFACES} or None"
        )


def _validate_tour_scope(tour_scope: str) -> None:
    if tour_scope not in SUPPORTED_TOUR_SCOPES:
        raise SurfaceEloValidationError(
            f"tour_scope must be one of {SUPPORTED_TOUR_SCOPES}"
        )


def _validate_competition_tier(competition_tier: str) -> None:
    if competition_tier not in SUPPORTED_COMPETITION_TIERS:
        raise SurfaceEloValidationError(
            f"competition_tier must be one of {SUPPORTED_COMPETITION_TIERS}"
        )


def _validate_outcome_status(outcome_status: str) -> None:
    if outcome_status not in SUPPORTED_OUTCOME_STATUSES:
        raise SurfaceEloValidationError(
            f"outcome_status must be one of {SUPPORTED_OUTCOME_STATUSES}"
        )


@dataclass(frozen=True)
class SurfaceEloConfig:
    initial_rating: Decimal = Decimal("1500")
    probability_scale: Decimal = Decimal("400")
    k_scale: Decimal = Decimal("250")
    k_offset: Decimal = Decimal("5")
    k_exponent: Decimal = Decimal("0.4")
    surface_max_weight: Decimal = Decimal("0.5")
    surface_full_weight_match_count: int = 10
    carpet_policy: str = "EXPLICIT_SURFACE_BOOK"
    unknown_surface_policy: str = "OVERALL_ONLY"
    retired_policy: str = "SKIP"
    walkover_policy: str = "SKIP"
    cancelled_policy: str = "SKIP"
    postponed_policy: str = "SKIP"
    qualifying_policy: str = "INCLUDE"
    tour_pool_policy: str = "SHARED"
    tie_selection_policy: str = "PLAYER_1"
    numeric_precision_policy: str = "DECIMAL_CONTEXT_28"
    internal_decimal_precision: int = 28
    probability_decimal_places: int = 6
    rating_quantization: str = "NONE"
    model_version: str = "agent1_surface_elo_v1"
    feature_version: str = "agent1_surface_elo_inputs_v1"
    config_version: str = "agent1_surface_elo_config_v1"
    parameter_schema_version: str = "agent1_surface_elo_params_v1"

    def __post_init__(self) -> None:
        decimal_fields = (
            "initial_rating",
            "probability_scale",
            "k_scale",
            "k_offset",
            "k_exponent",
            "surface_max_weight",
        )
        for field_name in decimal_fields:
            object.__setattr__(
                self,
                field_name,
                _as_decimal(getattr(self, field_name), field_name=field_name),
            )
        if self.initial_rating <= 0:
            raise SurfaceEloValidationError("initial_rating must be positive")
        if self.probability_scale <= 0:
            raise SurfaceEloValidationError("probability_scale must be positive")
        if self.k_scale <= 0:
            raise SurfaceEloValidationError("k_scale must be positive")
        if self.k_offset <= 0:
            raise SurfaceEloValidationError("k_offset must be positive")
        if self.k_exponent <= 0:
            raise SurfaceEloValidationError("k_exponent must be positive")
        if self.surface_full_weight_match_count <= 0:
            raise SurfaceEloValidationError("surface_full_weight_match_count must be positive")
        if not (Decimal("0") < self.surface_max_weight <= Decimal("1")):
            raise SurfaceEloValidationError("surface_max_weight must be within (0, 1]")
        if self.tie_selection_policy != "PLAYER_1":
            raise SurfaceEloValidationError("tie_selection_policy must be PLAYER_1 in V1")
        if self.unknown_surface_policy != "OVERALL_ONLY":
            raise SurfaceEloValidationError("unknown_surface_policy must be OVERALL_ONLY in V1")
        if self.carpet_policy != "EXPLICIT_SURFACE_BOOK":
            raise SurfaceEloValidationError("carpet_policy must be EXPLICIT_SURFACE_BOOK in V1")
        for policy_name in (
            "retired_policy",
            "walkover_policy",
            "cancelled_policy",
            "postponed_policy",
        ):
            if getattr(self, policy_name) != "SKIP":
                raise SurfaceEloValidationError(f"{policy_name} must be SKIP in V1")
        if self.qualifying_policy != "INCLUDE":
            raise SurfaceEloValidationError("qualifying_policy must be INCLUDE in V1")
        if self.tour_pool_policy != "SHARED":
            raise SurfaceEloValidationError("tour_pool_policy must be SHARED in V1")
        if self.numeric_precision_policy != "DECIMAL_CONTEXT_28":
            raise SurfaceEloValidationError(
                "numeric_precision_policy must be DECIMAL_CONTEXT_28 in V1"
            )
        if self.internal_decimal_precision < 28:
            raise SurfaceEloValidationError("internal_decimal_precision must be at least 28")
        if self.probability_decimal_places != 6:
            raise SurfaceEloValidationError("probability_decimal_places must be 6 in V1")
        if self.rating_quantization != "NONE":
            raise SurfaceEloValidationError("rating_quantization must be NONE in V1")
        for version_field in (
            "model_version",
            "feature_version",
            "config_version",
            "parameter_schema_version",
        ):
            if not getattr(self, version_field):
                raise SurfaceEloValidationError(f"{version_field} must be non-empty")

    @property
    def probability_quantum(self) -> Decimal:
        return Decimal("1").scaleb(-self.probability_decimal_places)

    def to_payload(self) -> dict[str, object]:
        return {
            "initial_rating": str(self.initial_rating),
            "probability_scale": str(self.probability_scale),
            "k_scale": str(self.k_scale),
            "k_offset": str(self.k_offset),
            "k_exponent": str(self.k_exponent),
            "surface_max_weight": str(self.surface_max_weight),
            "surface_full_weight_match_count": self.surface_full_weight_match_count,
            "carpet_policy": self.carpet_policy,
            "unknown_surface_policy": self.unknown_surface_policy,
            "retired_policy": self.retired_policy,
            "walkover_policy": self.walkover_policy,
            "cancelled_policy": self.cancelled_policy,
            "postponed_policy": self.postponed_policy,
            "qualifying_policy": self.qualifying_policy,
            "tour_pool_policy": self.tour_pool_policy,
            "tie_selection_policy": self.tie_selection_policy,
            "numeric_precision_policy": self.numeric_precision_policy,
            "internal_decimal_precision": self.internal_decimal_precision,
            "probability_decimal_places": self.probability_decimal_places,
            "rating_quantization": self.rating_quantization,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "config_version": self.config_version,
            "parameter_schema_version": self.parameter_schema_version,
        }


DEFAULT_CONFIG = SurfaceEloConfig()


@dataclass(frozen=True)
class HistoricalMatchObservation:
    match_id: int
    player_1_id: int
    player_2_id: int
    surface: Surface | None
    tour_scope: TourScope
    competition_tier: CompetitionTier
    best_of_sets: int
    qualifying_flag: bool
    match_start_at: datetime | None
    outcome_status: OutcomeStatus
    winner_side: int | None
    source_observed_at: datetime
    as_of: datetime

    def __post_init__(self) -> None:
        if self.match_id <= 0:
            raise SurfaceEloValidationError("match_id must be positive")
        if self.player_1_id <= 0 or self.player_2_id <= 0:
            raise SurfaceEloValidationError("player ids must be positive")
        if self.player_1_id == self.player_2_id:
            raise SurfaceEloValidationError("player_1_id and player_2_id must differ")
        _validate_surface(self.surface)
        _validate_tour_scope(self.tour_scope)
        _validate_competition_tier(self.competition_tier)
        if self.best_of_sets <= 0:
            raise SurfaceEloValidationError("best_of_sets must be positive")
        _validate_outcome_status(self.outcome_status)
        if self.outcome_status == "COMPLETED":
            if self.winner_side not in (1, 2):
                raise SurfaceEloValidationError("COMPLETED observations require winner_side 1 or 2")
        elif self.winner_side not in (None, 1, 2):
            raise SurfaceEloValidationError("winner_side must be None, 1, or 2")
        if self.as_of < self.source_observed_at:
            raise SurfaceEloValidationError("observation as_of must be >= source_observed_at")


@dataclass(frozen=True)
class PlayerRatingState:
    player_id: int
    overall_rating: Decimal
    overall_match_count: int = 0
    surface_ratings: dict[Surface, Decimal] = field(default_factory=dict)
    surface_match_counts: dict[Surface, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.player_id <= 0:
            raise SurfaceEloValidationError("player_id must be positive")
        object.__setattr__(
            self,
            "overall_rating",
            _as_decimal(self.overall_rating, field_name="overall_rating"),
        )
        if self.overall_match_count < 0:
            raise SurfaceEloValidationError("overall_match_count must be non-negative")

        normalized_surface_ratings: dict[Surface, Decimal] = {}
        for surface, rating in self.surface_ratings.items():
            _validate_surface(surface, field_name="surface_ratings key")
            normalized_surface_ratings[surface] = _as_decimal(
                rating, field_name=f"surface_ratings[{surface}]"
            )
        normalized_surface_counts: dict[Surface, int] = {}
        for surface, count in self.surface_match_counts.items():
            _validate_surface(surface, field_name="surface_match_counts key")
            if count < 0:
                raise SurfaceEloValidationError(
                    f"surface_match_counts[{surface}] must be non-negative"
                )
            normalized_surface_counts[surface] = count
        object.__setattr__(self, "surface_ratings", normalized_surface_ratings)
        object.__setattr__(self, "surface_match_counts", normalized_surface_counts)

    def surface_rating(self, surface: Surface, config: SurfaceEloConfig) -> Decimal:
        return self.surface_ratings.get(surface, config.initial_rating)

    def surface_match_count(self, surface: Surface) -> int:
        return self.surface_match_counts.get(surface, 0)


@dataclass(frozen=True)
class ReplayResult:
    player_states: dict[int, PlayerRatingState]
    eligible_history_match_count: int
    skipped_future_observation_count: int
    skipped_retired_match_count: int
    skipped_walkover_match_count: int
    skipped_cancelled_match_count: int
    skipped_postponed_match_count: int


@dataclass(frozen=True)
class PredictionRequest:
    player_1_id: int
    player_2_id: int
    surface: Surface | None
    tour_scope: TourScope
    competition_tier: CompetitionTier
    best_of_sets: int
    qualifying_flag: bool
    as_of: datetime

    def __post_init__(self) -> None:
        if self.player_1_id <= 0 or self.player_2_id <= 0:
            raise SurfaceEloValidationError("player ids must be positive")
        if self.player_1_id == self.player_2_id:
            raise SurfaceEloValidationError("player_1_id and player_2_id must differ")
        _validate_surface(self.surface)
        _validate_tour_scope(self.tour_scope)
        _validate_competition_tier(self.competition_tier)
        if self.best_of_sets <= 0:
            raise SurfaceEloValidationError("best_of_sets must be positive")


@dataclass(frozen=True)
class PredictionDiagnostics:
    player_1_overall_matches: int
    player_2_overall_matches: int
    player_1_surface_matches: int
    player_2_surface_matches: int
    player_1_cold_start: bool
    player_2_cold_start: bool
    player_1_surface_cold_start: bool
    player_2_surface_cold_start: bool
    player_1_surface_weight: Decimal
    player_2_surface_weight: Decimal
    unknown_surface: bool
    tie: bool
    eligible_history_match_count: int
    skipped_future_observation_count: int
    skipped_retired_match_count: int
    skipped_walkover_match_count: int
    skipped_cancelled_match_count: int
    skipped_postponed_match_count: int

    def to_payload(self) -> dict[str, object]:
        return {
            "player_1_overall_matches": self.player_1_overall_matches,
            "player_2_overall_matches": self.player_2_overall_matches,
            "player_1_surface_matches": self.player_1_surface_matches,
            "player_2_surface_matches": self.player_2_surface_matches,
            "player_1_cold_start": self.player_1_cold_start,
            "player_2_cold_start": self.player_2_cold_start,
            "player_1_surface_cold_start": self.player_1_surface_cold_start,
            "player_2_surface_cold_start": self.player_2_surface_cold_start,
            "player_1_surface_weight": str(self.player_1_surface_weight),
            "player_2_surface_weight": str(self.player_2_surface_weight),
            "unknown_surface": self.unknown_surface,
            "tie": self.tie,
            "eligible_history_match_count": self.eligible_history_match_count,
            "skipped_future_observation_count": self.skipped_future_observation_count,
            "skipped_retired_match_count": self.skipped_retired_match_count,
            "skipped_walkover_match_count": self.skipped_walkover_match_count,
            "skipped_cancelled_match_count": self.skipped_cancelled_match_count,
            "skipped_postponed_match_count": self.skipped_postponed_match_count,
        }


@dataclass(frozen=True)
class PredictionResult:
    agent_code: str
    prediction_status: Literal["PREDICTED"]
    selection_side: Literal[1, 2]
    win_probability: Decimal
    player_1_probability: Decimal
    player_2_probability: Decimal
    model_version: str
    feature_version: str
    as_of: datetime
    diagnostics: PredictionDiagnostics


def _quantize_probability(value: Decimal, config: SurfaceEloConfig) -> Decimal:
    return value.quantize(config.probability_quantum)


def _pow10(exponent: Decimal, config: SurfaceEloConfig) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = config.internal_decimal_precision
        return Decimal("10") ** exponent


def expected_score(
    rating_a: Decimal, rating_b: Decimal, config: SurfaceEloConfig = DEFAULT_CONFIG
) -> Decimal:
    rating_a = _as_decimal(rating_a, field_name="rating_a")
    rating_b = _as_decimal(rating_b, field_name="rating_b")
    exponent = (rating_b - rating_a) / config.probability_scale
    denominator = Decimal("1") + _pow10(exponent, config)
    with localcontext() as ctx:
        ctx.prec = config.internal_decimal_precision
        return Decimal("1") / denominator


def dynamic_k(match_count: int, config: SurfaceEloConfig = DEFAULT_CONFIG) -> Decimal:
    if match_count < 0:
        raise SurfaceEloValidationError("match_count must be non-negative")
    with localcontext() as ctx:
        ctx.prec = config.internal_decimal_precision
        denominator = (Decimal(match_count) + config.k_offset) ** config.k_exponent
        return config.k_scale / denominator


def surface_weight(
    surface_match_count: int, config: SurfaceEloConfig = DEFAULT_CONFIG
) -> Decimal:
    if surface_match_count < 0:
        raise SurfaceEloValidationError("surface_match_count must be non-negative")
    capped_count = min(surface_match_count, config.surface_full_weight_match_count)
    with localcontext() as ctx:
        ctx.prec = config.internal_decimal_precision
        return (
            Decimal(capped_count)
            / Decimal(config.surface_full_weight_match_count)
            * config.surface_max_weight
        )


def _default_player_state(player_id: int, config: SurfaceEloConfig) -> PlayerRatingState:
    return PlayerRatingState(player_id=player_id, overall_rating=config.initial_rating)


def effective_rating(
    player_state: PlayerRatingState, surface: Surface | None, config: SurfaceEloConfig = DEFAULT_CONFIG
) -> Decimal:
    if surface is None:
        return player_state.overall_rating
    weight = surface_weight(player_state.surface_match_count(surface), config)
    surface_rating_value = player_state.surface_rating(surface, config)
    with localcontext() as ctx:
        ctx.prec = config.internal_decimal_precision
        return (
            (Decimal("1") - weight) * player_state.overall_rating
            + weight * surface_rating_value
        )


def update_rating_pair(
    rating_a: Decimal,
    rating_b: Decimal,
    match_count_a: int,
    match_count_b: int,
    winner_side: int,
    config: SurfaceEloConfig = DEFAULT_CONFIG,
) -> tuple[Decimal, Decimal]:
    if winner_side not in (1, 2):
        raise SurfaceEloValidationError("winner_side must be 1 or 2")
    expected_a = expected_score(rating_a, rating_b, config)
    expected_b = Decimal("1") - expected_a
    actual_a = Decimal("1") if winner_side == 1 else Decimal("0")
    actual_b = Decimal("1") - actual_a
    k_a = dynamic_k(match_count_a, config)
    k_b = dynamic_k(match_count_b, config)
    with localcontext() as ctx:
        ctx.prec = config.internal_decimal_precision
        new_a = rating_a + k_a * (actual_a - expected_a)
        new_b = rating_b + k_b * (actual_b - expected_b)
    return new_a, new_b


def _status_is_eligible(outcome_status: OutcomeStatus, config: SurfaceEloConfig) -> bool:
    if outcome_status == "COMPLETED":
        return True
    policy_by_status = {
        "RETIRED": config.retired_policy,
        "WALKOVER": config.walkover_policy,
        "CANCELLED": config.cancelled_policy,
        "POSTPONED": config.postponed_policy,
    }
    return policy_by_status[outcome_status] != "SKIP"


def _history_sort_key(observation: HistoricalMatchObservation) -> tuple[datetime, datetime, int, int, int]:
    return (
        observation.as_of,
        observation.source_observed_at,
        observation.match_id,
        observation.player_1_id,
        observation.player_2_id,
    )


def _updated_player_states_for_observation(
    player_states: dict[int, PlayerRatingState],
    observation: HistoricalMatchObservation,
    config: SurfaceEloConfig = DEFAULT_CONFIG,
) -> tuple[PlayerRatingState, PlayerRatingState]:
    if observation.outcome_status != "COMPLETED":
        raise SurfaceEloValidationError(
            "_updated_player_states_for_observation requires outcome_status COMPLETED"
        )

    player_1_state = player_states.get(
        observation.player_1_id, _default_player_state(observation.player_1_id, config)
    )
    player_2_state = player_states.get(
        observation.player_2_id, _default_player_state(observation.player_2_id, config)
    )

    new_overall_1, new_overall_2 = update_rating_pair(
        player_1_state.overall_rating,
        player_2_state.overall_rating,
        player_1_state.overall_match_count,
        player_2_state.overall_match_count,
        observation.winner_side,
        config,
    )

    updated_player_1 = PlayerRatingState(
        player_id=player_1_state.player_id,
        overall_rating=new_overall_1,
        overall_match_count=player_1_state.overall_match_count + 1,
        surface_ratings=dict(player_1_state.surface_ratings),
        surface_match_counts=dict(player_1_state.surface_match_counts),
    )
    updated_player_2 = PlayerRatingState(
        player_id=player_2_state.player_id,
        overall_rating=new_overall_2,
        overall_match_count=player_2_state.overall_match_count + 1,
        surface_ratings=dict(player_2_state.surface_ratings),
        surface_match_counts=dict(player_2_state.surface_match_counts),
    )

    if observation.surface is not None:
        player_1_surface_count = player_1_state.surface_match_count(observation.surface)
        player_2_surface_count = player_2_state.surface_match_count(observation.surface)
        new_surface_1, new_surface_2 = update_rating_pair(
            player_1_state.surface_rating(observation.surface, config),
            player_2_state.surface_rating(observation.surface, config),
            player_1_surface_count,
            player_2_surface_count,
            observation.winner_side,
            config,
        )
        updated_surface_ratings_1 = dict(updated_player_1.surface_ratings)
        updated_surface_ratings_2 = dict(updated_player_2.surface_ratings)
        updated_surface_counts_1 = dict(updated_player_1.surface_match_counts)
        updated_surface_counts_2 = dict(updated_player_2.surface_match_counts)
        updated_surface_ratings_1[observation.surface] = new_surface_1
        updated_surface_ratings_2[observation.surface] = new_surface_2
        updated_surface_counts_1[observation.surface] = player_1_surface_count + 1
        updated_surface_counts_2[observation.surface] = player_2_surface_count + 1
        updated_player_1 = PlayerRatingState(
            player_id=updated_player_1.player_id,
            overall_rating=updated_player_1.overall_rating,
            overall_match_count=updated_player_1.overall_match_count,
            surface_ratings=updated_surface_ratings_1,
            surface_match_counts=updated_surface_counts_1,
        )
        updated_player_2 = PlayerRatingState(
            player_id=updated_player_2.player_id,
            overall_rating=updated_player_2.overall_rating,
            overall_match_count=updated_player_2.overall_match_count,
            surface_ratings=updated_surface_ratings_2,
            surface_match_counts=updated_surface_counts_2,
        )

    return updated_player_1, updated_player_2


def _apply_historical_observation_in_place(
    player_states: dict[int, PlayerRatingState],
    observation: HistoricalMatchObservation,
    config: SurfaceEloConfig = DEFAULT_CONFIG,
) -> None:
    updated_player_1, updated_player_2 = _updated_player_states_for_observation(
        player_states,
        observation,
        config,
    )
    player_states[updated_player_1.player_id] = updated_player_1
    player_states[updated_player_2.player_id] = updated_player_2


def apply_historical_observation(
    player_states: dict[int, PlayerRatingState],
    observation: HistoricalMatchObservation,
    config: SurfaceEloConfig = DEFAULT_CONFIG,
) -> dict[int, PlayerRatingState]:
    current_states = dict(player_states)
    if observation.outcome_status != "COMPLETED":
        return current_states
    _apply_historical_observation_in_place(current_states, observation, config)
    return current_states


def replay_history(
    history: list[HistoricalMatchObservation] | tuple[HistoricalMatchObservation, ...],
    prediction_as_of: datetime,
    config: SurfaceEloConfig = DEFAULT_CONFIG,
) -> ReplayResult:
    eligible_observations: list[HistoricalMatchObservation] = []
    skipped_future_observation_count = 0
    skipped_retired_match_count = 0
    skipped_walkover_match_count = 0
    skipped_cancelled_match_count = 0
    skipped_postponed_match_count = 0

    for observation in sorted(history, key=_history_sort_key):
        if observation.source_observed_at > prediction_as_of or observation.as_of > prediction_as_of:
            skipped_future_observation_count += 1
            continue
        if not _status_is_eligible(observation.outcome_status, config):
            if observation.outcome_status == "RETIRED":
                skipped_retired_match_count += 1
            elif observation.outcome_status == "WALKOVER":
                skipped_walkover_match_count += 1
            elif observation.outcome_status == "CANCELLED":
                skipped_cancelled_match_count += 1
            elif observation.outcome_status == "POSTPONED":
                skipped_postponed_match_count += 1
            continue
        eligible_observations.append(observation)

    player_states: dict[int, PlayerRatingState] = {}
    for observation in eligible_observations:
        _apply_historical_observation_in_place(player_states, observation, config)

    return ReplayResult(
        player_states=player_states,
        eligible_history_match_count=len(eligible_observations),
        skipped_future_observation_count=skipped_future_observation_count,
        skipped_retired_match_count=skipped_retired_match_count,
        skipped_walkover_match_count=skipped_walkover_match_count,
        skipped_cancelled_match_count=skipped_cancelled_match_count,
        skipped_postponed_match_count=skipped_postponed_match_count,
    )


def predict(
    request: PredictionRequest,
    history: list[HistoricalMatchObservation] | tuple[HistoricalMatchObservation, ...] = (),
    config: SurfaceEloConfig = DEFAULT_CONFIG,
) -> PredictionResult:
    replay = replay_history(history, request.as_of, config)
    player_1_state = replay.player_states.get(
        request.player_1_id, _default_player_state(request.player_1_id, config)
    )
    player_2_state = replay.player_states.get(
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
        selection_side: Literal[1, 2] = 1
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
        eligible_history_match_count=replay.eligible_history_match_count,
        skipped_future_observation_count=replay.skipped_future_observation_count,
        skipped_retired_match_count=replay.skipped_retired_match_count,
        skipped_walkover_match_count=replay.skipped_walkover_match_count,
        skipped_cancelled_match_count=replay.skipped_cancelled_match_count,
        skipped_postponed_match_count=replay.skipped_postponed_match_count,
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
