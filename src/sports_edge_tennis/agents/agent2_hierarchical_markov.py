from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from enum import Enum
from functools import lru_cache
from typing import Literal


AGENT_CODE = "AGENT_2_HIERARCHICAL_MARKOV"
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


class Agent2ValidationError(ValueError):
    """Raised when Agent 2 receives structurally invalid input."""


class PopulationFallbackLevel(str, Enum):
    SURFACE_TOUR = "SURFACE_TOUR"
    SURFACE = "SURFACE"
    GLOBAL = "GLOBAL"


class ScoringRuleVersion(str, Enum):
    BO3_TB7_ALL_SETS = "BO3_TB7_ALL_SETS"
    BO3_DECIDER_TB10_AT_6_6 = "BO3_DECIDER_TB10_AT_6_6"
    BO5_DECIDER_ADVANTAGE = "BO5_DECIDER_ADVANTAGE"
    BO5_DECIDER_TB7_AT_6_6 = "BO5_DECIDER_TB7_AT_6_6"
    BO5_DECIDER_TB7_AT_12_12 = "BO5_DECIDER_TB7_AT_12_12"
    BO5_DECIDER_TB10_AT_6_6 = "BO5_DECIDER_TB10_AT_6_6"


class AbstainReason(str, Enum):
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INSUFFICIENT_POINT_HISTORY = "INSUFFICIENT_POINT_HISTORY"
    UNKNOWN_SURFACE = "UNKNOWN_SURFACE"
    UNSUPPORTED_SCORING = "UNSUPPORTED_SCORING"
    UNSUPPORTED_OR_UNKNOWN = "UNSUPPORTED_OR_UNKNOWN"
    ONE_SIDED_HISTORY = "ONE_SIDED_HISTORY"
    INVALID_STAT_ROW = "INVALID_STAT_ROW"
    PIT_STATE_UNRESOLVED = "PIT_STATE_UNRESOLVED"


def _as_decimal(value: Decimal | int | str, *, field_name: str) -> Decimal:
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:  # pragma: no cover - defensive conversion path
        raise Agent2ValidationError(f"{field_name} must be Decimal-compatible") from exc
    if not decimal_value.is_finite():
        raise Agent2ValidationError(f"{field_name} must be finite")
    return decimal_value


def _validate_surface(surface: str | None) -> None:
    if surface not in SUPPORTED_SURFACES:
        raise Agent2ValidationError(f"surface must be one of {SUPPORTED_SURFACES}")


def _validate_tour_scope(tour_scope: str) -> None:
    if tour_scope not in SUPPORTED_TOUR_SCOPES:
        raise Agent2ValidationError(f"tour_scope must be one of {SUPPORTED_TOUR_SCOPES}")


def _validate_competition_tier(competition_tier: str) -> None:
    if competition_tier not in SUPPORTED_COMPETITION_TIERS:
        raise Agent2ValidationError(
            f"competition_tier must be one of {SUPPORTED_COMPETITION_TIERS}"
        )


def _locked_decimal(value: Decimal | int | str, *, field_name: str, expected: str) -> Decimal:
    decimal_value = _as_decimal(value, field_name=field_name)
    if decimal_value != Decimal(expected):
        raise Agent2ValidationError(f"{field_name} must remain locked at {expected}")
    return decimal_value


def _locked_string(value: str, *, field_name: str, expected: str) -> str:
    if value != expected:
        raise Agent2ValidationError(f"{field_name} must remain locked at {expected}")
    return value


def _decimal_to_payload(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


@dataclass(frozen=True)
class Agent2Config:
    k_serve_overall: Decimal = Decimal("142.64921751742673")
    k_serve_surface: Decimal = Decimal("207.4613530571349")
    k_return_overall: Decimal = Decimal("348.87619155831686")
    k_return_surface: Decimal = Decimal("193.2713757201798")
    minimum_prior_usable_matches: int = 5
    minimum_prior_service_points: int = 250
    minimum_prior_return_points: int = 250
    internal_decimal_precision: int = 28
    probability_decimal_places: int = 6
    model_version: str = "agent2_hierarchical_markov_v1"
    feature_version: str = "agent2_serve_return_inputs_v1"
    config_version: str = "agent2_hierarchical_markov_config_v1"
    parameter_schema_version: str = "agent2_hierarchical_markov_params_v1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "k_serve_overall",
            _locked_decimal(
                self.k_serve_overall,
                field_name="k_serve_overall",
                expected="142.64921751742673",
            ),
        )
        object.__setattr__(
            self,
            "k_serve_surface",
            _locked_decimal(
                self.k_serve_surface,
                field_name="k_serve_surface",
                expected="207.4613530571349",
            ),
        )
        object.__setattr__(
            self,
            "k_return_overall",
            _locked_decimal(
                self.k_return_overall,
                field_name="k_return_overall",
                expected="348.87619155831686",
            ),
        )
        object.__setattr__(
            self,
            "k_return_surface",
            _locked_decimal(
                self.k_return_surface,
                field_name="k_return_surface",
                expected="193.2713757201798",
            ),
        )
        if self.minimum_prior_usable_matches != 5:
            raise Agent2ValidationError("minimum_prior_usable_matches must remain locked at 5")
        if self.minimum_prior_service_points != 250:
            raise Agent2ValidationError("minimum_prior_service_points must remain locked at 250")
        if self.minimum_prior_return_points != 250:
            raise Agent2ValidationError("minimum_prior_return_points must remain locked at 250")
        if self.internal_decimal_precision < 28:
            raise Agent2ValidationError("internal_decimal_precision must be at least 28")
        if self.probability_decimal_places != 6:
            raise Agent2ValidationError("probability_decimal_places must remain locked at 6")
        object.__setattr__(
            self,
            "model_version",
            _locked_string(
                self.model_version,
                field_name="model_version",
                expected="agent2_hierarchical_markov_v1",
            ),
        )
        object.__setattr__(
            self,
            "feature_version",
            _locked_string(
                self.feature_version,
                field_name="feature_version",
                expected="agent2_serve_return_inputs_v1",
            ),
        )
        object.__setattr__(
            self,
            "config_version",
            _locked_string(
                self.config_version,
                field_name="config_version",
                expected="agent2_hierarchical_markov_config_v1",
            ),
        )
        object.__setattr__(
            self,
            "parameter_schema_version",
            _locked_string(
                self.parameter_schema_version,
                field_name="parameter_schema_version",
                expected="agent2_hierarchical_markov_params_v1",
            ),
        )

    @property
    def probability_quantum(self) -> Decimal:
        return Decimal("1").scaleb(-self.probability_decimal_places)

    def to_payload(self) -> dict[str, object]:
        return {
            "k_serve_overall": str(self.k_serve_overall),
            "k_serve_surface": str(self.k_serve_surface),
            "k_return_overall": str(self.k_return_overall),
            "k_return_surface": str(self.k_return_surface),
            "minimum_prior_usable_matches": self.minimum_prior_usable_matches,
            "minimum_prior_service_points": self.minimum_prior_service_points,
            "minimum_prior_return_points": self.minimum_prior_return_points,
            "internal_decimal_precision": self.internal_decimal_precision,
            "probability_decimal_places": self.probability_decimal_places,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "config_version": self.config_version,
            "parameter_schema_version": self.parameter_schema_version,
        }


DEFAULT_CONFIG = Agent2Config()


@dataclass(frozen=True)
class PlayerServeReturnState:
    player_id: int
    prior_usable_matches: int
    overall_serve_points_won: int
    overall_serve_points_played: int
    overall_return_points_won: int
    overall_return_points_played: int
    surface_serve_points_won: int
    surface_serve_points_played: int
    surface_return_points_won: int
    surface_return_points_played: int

    def __post_init__(self) -> None:
        if self.player_id <= 0:
            raise Agent2ValidationError("player_id must be positive")
        for field_name in (
            "prior_usable_matches",
            "overall_serve_points_won",
            "overall_serve_points_played",
            "overall_return_points_won",
            "overall_return_points_played",
            "surface_serve_points_won",
            "surface_serve_points_played",
            "surface_return_points_won",
            "surface_return_points_played",
        ):
            if getattr(self, field_name) < 0:
                raise Agent2ValidationError(f"{field_name} must be non-negative")
        if self.overall_serve_points_won > self.overall_serve_points_played:
            raise Agent2ValidationError(
                "overall_serve_points_won must be <= overall_serve_points_played"
            )
        if self.overall_return_points_won > self.overall_return_points_played:
            raise Agent2ValidationError(
                "overall_return_points_won must be <= overall_return_points_played"
            )
        if self.surface_serve_points_won > self.surface_serve_points_played:
            raise Agent2ValidationError(
                "surface_serve_points_won must be <= surface_serve_points_played"
            )
        if self.surface_return_points_won > self.surface_return_points_played:
            raise Agent2ValidationError(
                "surface_return_points_won must be <= surface_return_points_played"
            )


@dataclass(frozen=True)
class PopulationPriorState:
    surface_tour_service_points_won: int
    surface_tour_service_points_played: int
    surface_service_points_won: int
    surface_service_points_played: int
    global_service_points_won: int
    global_service_points_played: int

    def __post_init__(self) -> None:
        for field_name in (
            "surface_tour_service_points_won",
            "surface_tour_service_points_played",
            "surface_service_points_won",
            "surface_service_points_played",
            "global_service_points_won",
            "global_service_points_played",
        ):
            if getattr(self, field_name) < 0:
                raise Agent2ValidationError(f"{field_name} must be non-negative")
        if self.surface_tour_service_points_won > self.surface_tour_service_points_played:
            raise Agent2ValidationError(
                "surface_tour_service_points_won must be <= surface_tour_service_points_played"
            )
        if self.surface_service_points_won > self.surface_service_points_played:
            raise Agent2ValidationError(
                "surface_service_points_won must be <= surface_service_points_played"
            )
        if self.global_service_points_won > self.global_service_points_played:
            raise Agent2ValidationError(
                "global_service_points_won must be <= global_service_points_played"
            )


@dataclass(frozen=True)
class PredictionRequest:
    player_1_id: int
    player_2_id: int
    surface: Surface | None
    tour_scope: TourScope
    competition_tier: CompetitionTier
    best_of_sets: int
    scoring_rule_version: ScoringRuleVersion | None
    player_1_state: PlayerServeReturnState
    player_2_state: PlayerServeReturnState
    population_prior_state: PopulationPriorState
    as_of: datetime
    resolver_abstain_reason: AbstainReason | None = None

    def __post_init__(self) -> None:
        if self.player_1_id <= 0 or self.player_2_id <= 0:
            raise Agent2ValidationError("player ids must be positive")
        if self.player_1_id == self.player_2_id:
            raise Agent2ValidationError("player_1_id and player_2_id must differ")
        if self.surface is not None:
            _validate_surface(self.surface)
        _validate_tour_scope(self.tour_scope)
        _validate_competition_tier(self.competition_tier)
        if self.best_of_sets not in (3, 5):
            raise Agent2ValidationError("best_of_sets must be 3 or 5")
        if self.player_1_state.player_id != self.player_1_id:
            raise Agent2ValidationError("player_1_state.player_id must match player_1_id")
        if self.player_2_state.player_id != self.player_2_id:
            raise Agent2ValidationError("player_2_state.player_id must match player_2_id")


@dataclass(frozen=True)
class PlayerPosterior:
    serve_overall: Decimal
    serve_surface: Decimal
    return_overall: Decimal
    return_surface: Decimal


@dataclass(frozen=True)
class PredictionDiagnostics:
    player_1_prior_usable_matches: int
    player_2_prior_usable_matches: int
    player_1_overall_serve_points: int
    player_2_overall_serve_points: int
    player_1_overall_return_points: int
    player_2_overall_return_points: int
    player_1_surface_serve_points: int
    player_2_surface_serve_points: int
    player_1_surface_return_points: int
    player_2_surface_return_points: int
    population_fallback_level: PopulationFallbackLevel | None
    mu_c: Decimal | None
    player_1_shrunk_overall_serve: Decimal | None
    player_2_shrunk_overall_serve: Decimal | None
    player_1_shrunk_surface_serve: Decimal | None
    player_2_shrunk_surface_serve: Decimal | None
    player_1_shrunk_overall_return: Decimal | None
    player_2_shrunk_overall_return: Decimal | None
    player_1_shrunk_surface_return: Decimal | None
    player_2_shrunk_surface_return: Decimal | None
    p_1_serve_vs_2: Decimal | None
    p_2_serve_vs_1: Decimal | None
    scoring_rule_version: ScoringRuleVersion | None
    best_of_sets: int
    first_server_marginalized: bool
    abstain_reason: AbstainReason | None

    def to_payload(self) -> dict[str, object]:
        return {
            "player_1_prior_usable_matches": self.player_1_prior_usable_matches,
            "player_2_prior_usable_matches": self.player_2_prior_usable_matches,
            "player_1_overall_serve_points": self.player_1_overall_serve_points,
            "player_2_overall_serve_points": self.player_2_overall_serve_points,
            "player_1_overall_return_points": self.player_1_overall_return_points,
            "player_2_overall_return_points": self.player_2_overall_return_points,
            "player_1_surface_serve_points": self.player_1_surface_serve_points,
            "player_2_surface_serve_points": self.player_2_surface_serve_points,
            "player_1_surface_return_points": self.player_1_surface_return_points,
            "player_2_surface_return_points": self.player_2_surface_return_points,
            "population_fallback_level": (
                None if self.population_fallback_level is None else self.population_fallback_level.value
            ),
            "mu_c": _decimal_to_payload(self.mu_c),
            "player_1_shrunk_overall_serve": _decimal_to_payload(self.player_1_shrunk_overall_serve),
            "player_2_shrunk_overall_serve": _decimal_to_payload(self.player_2_shrunk_overall_serve),
            "player_1_shrunk_surface_serve": _decimal_to_payload(self.player_1_shrunk_surface_serve),
            "player_2_shrunk_surface_serve": _decimal_to_payload(self.player_2_shrunk_surface_serve),
            "player_1_shrunk_overall_return": _decimal_to_payload(self.player_1_shrunk_overall_return),
            "player_2_shrunk_overall_return": _decimal_to_payload(self.player_2_shrunk_overall_return),
            "player_1_shrunk_surface_return": _decimal_to_payload(
                self.player_1_shrunk_surface_return
            ),
            "player_2_shrunk_surface_return": _decimal_to_payload(
                self.player_2_shrunk_surface_return
            ),
            "p_1_serve_vs_2": _decimal_to_payload(self.p_1_serve_vs_2),
            "p_2_serve_vs_1": _decimal_to_payload(self.p_2_serve_vs_1),
            "scoring_rule_version": (
                None if self.scoring_rule_version is None else self.scoring_rule_version.value
            ),
            "best_of_sets": self.best_of_sets,
            "first_server_marginalized": self.first_server_marginalized,
            "abstain_reason": None if self.abstain_reason is None else self.abstain_reason.value,
        }


@dataclass(frozen=True)
class PredictionResult:
    agent_code: str
    prediction_status: Literal["PREDICTED", "ABSTAINED"]
    selection_side: Literal[1, 2] | None
    win_probability: Decimal | None
    player_1_probability: Decimal | None
    player_2_probability: Decimal | None
    model_version: str
    feature_version: str
    config_version: str
    parameter_schema_version: str
    as_of: datetime
    diagnostics: PredictionDiagnostics


def _context_precision(config: Agent2Config) -> int:
    return max(config.internal_decimal_precision, 50)


def _quantize_probability(value: Decimal, config: Agent2Config) -> Decimal:
    return value.quantize(config.probability_quantum)


def _reconcile_distribution(
    distribution: dict[tuple[Literal[1, 2], Literal[1, 2]], Decimal],
    *,
    precision: int,
) -> dict[tuple[Literal[1, 2], Literal[1, 2]], Decimal]:
    total = sum(distribution.values(), Decimal("0"))
    if total == Decimal("1"):
        return distribution
    delta = Decimal("1") - total
    tolerance = Decimal("1").scaleb(-24)
    if abs(delta) > tolerance:
        raise Agent2ValidationError("distribution must sum to 1 within Decimal tolerance")
    largest_key = max(distribution, key=distribution.__getitem__)
    reconciled = dict(distribution)
    reconciled[largest_key] = reconciled[largest_key] + delta
    return reconciled


def _validate_probability(value: Decimal, *, field_name: str) -> None:
    if not (Decimal("0") < value < Decimal("1")):
        raise Agent2ValidationError(f"{field_name} must be strictly within (0, 1)")


def _ratio_to_probability(numerator: Decimal, denominator: Decimal, *, field_name: str) -> Decimal:
    if denominator <= 0:
        raise Agent2ValidationError(f"{field_name} denominator must be positive")
    with localcontext() as ctx:
        ctx.prec = 50
        value = numerator / denominator
    return value


def _odds(probability: Decimal) -> Decimal:
    _validate_probability(probability, field_name="probability")
    with localcontext() as ctx:
        ctx.prec = 50
        return probability / (Decimal("1") - probability)


def deuce_probability(point_win_probability: Decimal) -> Decimal:
    p = _as_decimal(point_win_probability, field_name="point_win_probability")
    if p == Decimal("0"):
        return Decimal("0")
    if p == Decimal("1"):
        return Decimal("1")
    q = Decimal("1") - p
    with localcontext() as ctx:
        ctx.prec = 50
        return (p * p) / ((p * p) + (q * q))


def game_probability(point_win_probability: Decimal) -> Decimal:
    p = _as_decimal(point_win_probability, field_name="point_win_probability")
    if p == Decimal("0"):
        return Decimal("0")
    if p == Decimal("1"):
        return Decimal("1")
    q = Decimal("1") - p
    deuce = deuce_probability(p)
    with localcontext() as ctx:
        ctx.prec = 50
        return (p**4) * (Decimal("1") + Decimal("4") * q + Decimal("10") * (q**2)) + (
            Decimal("20") * (p**3) * (q**3) * deuce
        )


def _gaussian_elimination(
    matrix: list[list[Decimal]],
    vector: list[Decimal],
    *,
    precision: int,
) -> list[Decimal]:
    size = len(matrix)
    augmented = [row[:] + [vector[idx]] for idx, row in enumerate(matrix)]
    with localcontext() as ctx:
        ctx.prec = precision
        for pivot_index in range(size):
            pivot_row = None
            for candidate in range(pivot_index, size):
                if augmented[candidate][pivot_index] != Decimal("0"):
                    pivot_row = candidate
                    break
            if pivot_row is None:
                raise Agent2ValidationError("linear system is singular")
            if pivot_row != pivot_index:
                augmented[pivot_index], augmented[pivot_row] = (
                    augmented[pivot_row],
                    augmented[pivot_index],
                )
            pivot = augmented[pivot_index][pivot_index]
            for column in range(pivot_index, size + 1):
                augmented[pivot_index][column] = augmented[pivot_index][column] / pivot
            for row_index in range(size):
                if row_index == pivot_index:
                    continue
                factor = augmented[row_index][pivot_index]
                if factor == Decimal("0"):
                    continue
                for column in range(pivot_index, size + 1):
                    augmented[row_index][column] = (
                        augmented[row_index][column]
                        - factor * augmented[pivot_index][column]
                    )
        return [augmented[idx][size] for idx in range(size)]


def _other_side(side: int) -> Literal[1, 2]:
    if side not in (1, 2):
        raise Agent2ValidationError("side must be 1 or 2")
    return 2 if side == 1 else 1


def tiebreak_server_for_points_played(
    *,
    first_server: Literal[1, 2],
    points_played: int,
) -> Literal[1, 2]:
    if points_played < 0:
        raise Agent2ValidationError("points_played must be non-negative")
    if points_played % 4 in (0, 3):
        return first_server
    return _other_side(first_server)


def _tail_state_phase_from_mod(
    *,
    diff: int,
    current_mod: int,
    base_mod: int,
) -> int:
    if diff == 0:
        if current_mod == base_mod:
            return 0
        if current_mod == (base_mod + 2) % 4:
            return 1
    elif diff in (-1, 1):
        if current_mod == (base_mod + 1) % 4:
            return 0
        if current_mod == (base_mod + 3) % 4:
            return 1
    raise Agent2ValidationError("tail state phase could not be resolved")


def _solve_tiebreak_tail(
    *,
    point_target: int,
    first_server: Literal[1, 2],
    player_1_serve_probability: Decimal,
    player_2_serve_probability: Decimal,
    precision: int,
) -> dict[tuple[int, int], Decimal]:
    if point_target < 2:
        raise Agent2ValidationError("point_target must be at least 2")
    base_mod = (2 * (point_target - 1)) % 4
    states = [
        (-1, 0),
        (0, 0),
        (1, 0),
        (-1, 1),
        (0, 1),
        (1, 1),
    ]
    state_index = {state: idx for idx, state in enumerate(states)}
    matrix: list[list[Decimal]] = [[Decimal("0") for _ in states] for _ in states]
    vector = [Decimal("0") for _ in states]

    for diff, phase in states:
        row = state_index[(diff, phase)]
        matrix[row][row] = Decimal("1")
        if diff == 0:
            current_mod = (base_mod + 2 * phase) % 4
        else:
            current_mod = (base_mod + 1 + 2 * phase) % 4
        server = tiebreak_server_for_points_played(
            first_server=first_server,
            points_played=current_mod,
        )
        point_probability = (
            player_1_serve_probability
            if server == 1
            else Decimal("1") - player_2_serve_probability
        )
        next_mod = (current_mod + 1) % 4
        next_diff_if_win = diff + 1
        next_diff_if_loss = diff - 1

        if next_diff_if_win >= 2:
            vector[row] += point_probability
        else:
            next_phase = _tail_state_phase_from_mod(
                diff=next_diff_if_win,
                current_mod=next_mod,
                base_mod=base_mod,
            )
            matrix[row][state_index[(next_diff_if_win, next_phase)]] -= point_probability

        if next_diff_if_loss <= -2:
            continue
        next_phase = _tail_state_phase_from_mod(
            diff=next_diff_if_loss,
            current_mod=next_mod,
            base_mod=base_mod,
        )
        matrix[row][state_index[(next_diff_if_loss, next_phase)]] -= (
            Decimal("1") - point_probability
        )

    solution = _gaussian_elimination(matrix, vector, precision=precision)
    return {state: solution[idx] for state, idx in state_index.items()}


def tiebreak_probability(
    *,
    player_1_serve_probability: Decimal,
    player_2_serve_probability: Decimal,
    point_target: int,
    first_server: Literal[1, 2],
    config: Agent2Config = DEFAULT_CONFIG,
) -> Decimal:
    p1_serve = _as_decimal(player_1_serve_probability, field_name="player_1_serve_probability")
    p2_serve = _as_decimal(player_2_serve_probability, field_name="player_2_serve_probability")
    _validate_probability(p1_serve, field_name="player_1_serve_probability")
    _validate_probability(p2_serve, field_name="player_2_serve_probability")
    if point_target not in (7, 10):
        raise Agent2ValidationError("point_target must be 7 or 10")
    if first_server not in (1, 2):
        raise Agent2ValidationError("first_server must be 1 or 2")

    tail = _solve_tiebreak_tail(
        point_target=point_target,
        first_server=first_server,
        player_1_serve_probability=p1_serve,
        player_2_serve_probability=p2_serve,
        precision=_context_precision(config),
    )

    @lru_cache(maxsize=None)
    def recurse(points_a: int, points_b: int) -> Decimal:
        if points_a >= point_target and points_a - points_b >= 2:
            return Decimal("1")
        if points_b >= point_target and points_b - points_a >= 2:
            return Decimal("0")
        if points_a >= point_target - 1 and points_b >= point_target - 1:
            diff = points_a - points_b
            if diff not in (-1, 0, 1):
                raise Agent2ValidationError("unexpected tiebreak tail state")
            phase = _tail_state_phase_from_mod(
                diff=diff,
                current_mod=(points_a + points_b) % 4,
                base_mod=(2 * (point_target - 1)) % 4,
            )
            return tail[(diff, phase)]
        points_played = points_a + points_b
        server = tiebreak_server_for_points_played(
            first_server=first_server,
            points_played=points_played,
        )
        point_probability = p1_serve if server == 1 else Decimal("1") - p2_serve
        with localcontext() as ctx:
            ctx.prec = _context_precision(config)
            return point_probability * recurse(points_a + 1, points_b) + (
                Decimal("1") - point_probability
            ) * recurse(points_a, points_b + 1)

    return recurse(0, 0)


def _solve_advantage_set_tail(
    *,
    player_1_hold_probability: Decimal,
    player_2_hold_probability: Decimal,
    precision: int,
) -> dict[tuple[int, int], dict[tuple[Literal[1, 2], Literal[1, 2]], Decimal]]:
    states = [
        (-1, 1),
        (0, 1),
        (1, 1),
        (-1, 2),
        (0, 2),
        (1, 2),
    ]
    absorbing_outcomes = ((1, 1), (1, 2), (2, 1), (2, 2))
    state_index = {state: idx for idx, state in enumerate(states)}
    matrix: list[list[Decimal]] = [[Decimal("0") for _ in states] for _ in states]
    outcome_vectors = {
        outcome: [Decimal("0") for _ in states] for outcome in absorbing_outcomes
    }

    for diff, server in states:
        row = state_index[(diff, server)]
        matrix[row][row] = Decimal("1")
        game_win_probability = (
            player_1_hold_probability
            if server == 1
            else Decimal("1") - player_2_hold_probability
        )
        next_server_state = _other_side(server)
        if diff + 1 >= 2:
            outcome_vectors[(1, next_server_state)][row] += game_win_probability
        else:
            matrix[row][state_index[(diff + 1, next_server_state)]] -= game_win_probability
        if diff - 1 <= -2:
            outcome_vectors[(2, next_server_state)][row] += Decimal("1") - game_win_probability
        else:
            matrix[row][state_index[(diff - 1, next_server_state)]] -= (
                Decimal("1") - game_win_probability
            )

    solved_outcomes = {
        outcome: _gaussian_elimination(matrix, vector, precision=precision)
        for outcome, vector in outcome_vectors.items()
    }
    return {
        state: {
            outcome: solved_outcomes[outcome][state_index[state]]
            for outcome in absorbing_outcomes
        }
        for state in states
    }


def _non_deciding_set_rule(
    scoring_rule_version: ScoringRuleVersion,
) -> tuple[int | None, int | None]:
    if scoring_rule_version == ScoringRuleVersion.BO3_TB7_ALL_SETS:
        return 6, 7
    return 6, 7


def _deciding_set_rule(
    scoring_rule_version: ScoringRuleVersion,
) -> tuple[int | None, int | None]:
    if scoring_rule_version == ScoringRuleVersion.BO3_TB7_ALL_SETS:
        return 6, 7
    if scoring_rule_version == ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6:
        return 6, 10
    if scoring_rule_version == ScoringRuleVersion.BO5_DECIDER_ADVANTAGE:
        return None, None
    if scoring_rule_version == ScoringRuleVersion.BO5_DECIDER_TB7_AT_6_6:
        return 6, 7
    if scoring_rule_version == ScoringRuleVersion.BO5_DECIDER_TB7_AT_12_12:
        return 12, 7
    if scoring_rule_version == ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6:
        return 6, 10
    raise Agent2ValidationError("unsupported scoring_rule_version")


def set_transition_distribution(
    *,
    player_1_serve_probability: Decimal,
    player_2_serve_probability: Decimal,
    scoring_rule_version: ScoringRuleVersion,
    is_deciding_set: bool,
    next_server: Literal[1, 2],
    config: Agent2Config = DEFAULT_CONFIG,
) -> dict[tuple[Literal[1, 2], Literal[1, 2]], Decimal]:
    p1_serve = _as_decimal(player_1_serve_probability, field_name="player_1_serve_probability")
    p2_serve = _as_decimal(player_2_serve_probability, field_name="player_2_serve_probability")
    _validate_probability(p1_serve, field_name="player_1_serve_probability")
    _validate_probability(p2_serve, field_name="player_2_serve_probability")
    if next_server not in (1, 2):
        raise Agent2ValidationError("next_server must be 1 or 2")

    p1_hold = game_probability(p1_serve)
    p2_hold = game_probability(p2_serve)
    tiebreak_at_games, tiebreak_points = (
        _deciding_set_rule(scoring_rule_version)
        if is_deciding_set
        else _non_deciding_set_rule(scoring_rule_version)
    )
    advantage_tail = None
    if tiebreak_at_games is None:
        advantage_tail = _solve_advantage_set_tail(
            player_1_hold_probability=p1_hold,
            player_2_hold_probability=p2_hold,
            precision=_context_precision(config),
        )

    @lru_cache(maxsize=None)
    def recurse(
        games_a: int,
        games_b: int,
        server: Literal[1, 2],
    ) -> tuple[tuple[tuple[Literal[1, 2], Literal[1, 2]], Decimal], ...]:
        if games_a >= 6 and games_a - games_b >= 2:
            return (((1, server), Decimal("1")),)
        if games_b >= 6 and games_b - games_a >= 2:
            return (((2, server), Decimal("1")),)
        if tiebreak_at_games is not None and games_a == tiebreak_at_games and games_b == tiebreak_at_games:
            tb_probability = tiebreak_probability(
                player_1_serve_probability=p1_serve,
                player_2_serve_probability=p2_serve,
                point_target=tiebreak_points,
                first_server=server,
                config=config,
            )
            next_set_first_server = _other_side(server)
            return (
                ((1, next_set_first_server), tb_probability),
                ((2, next_set_first_server), Decimal("1") - tb_probability),
            )
        if tiebreak_at_games is None and games_a >= 5 and games_b >= 5:
            diff = games_a - games_b
            if diff not in (-1, 0, 1):
                raise Agent2ValidationError("unexpected advantage-set tail state")
            return tuple(sorted(advantage_tail[(diff, server)].items()))
        game_win_probability = p1_hold if server == 1 else Decimal("1") - p2_hold
        next_server_state = _other_side(server)
        next_if_win = recurse(games_a + 1, games_b, next_server_state)
        next_if_loss = recurse(games_a, games_b + 1, next_server_state)
        outcomes: dict[tuple[Literal[1, 2], Literal[1, 2]], Decimal] = {}
        with localcontext() as ctx:
            ctx.prec = _context_precision(config)
            for key, value in next_if_win:
                outcomes[key] = outcomes.get(key, Decimal("0")) + game_win_probability * value
            for key, value in next_if_loss:
                outcomes[key] = outcomes.get(key, Decimal("0")) + (
                    Decimal("1") - game_win_probability
                ) * value
        return tuple(sorted(outcomes.items()))

    distribution = dict(recurse(0, 0, next_server))
    return _reconcile_distribution(distribution, precision=_context_precision(config))


def match_probability(
    *,
    player_1_serve_probability: Decimal,
    player_2_serve_probability: Decimal,
    scoring_rule_version: ScoringRuleVersion,
    first_server: Literal[1, 2],
    config: Agent2Config = DEFAULT_CONFIG,
) -> Decimal:
    target_sets = 2 if scoring_rule_version in (
        ScoringRuleVersion.BO3_TB7_ALL_SETS,
        ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6,
    ) else 3

    @lru_cache(maxsize=None)
    def recurse(
        sets_a: int,
        sets_b: int,
        next_server: Literal[1, 2],
    ) -> Decimal:
        if sets_a >= target_sets:
            return Decimal("1")
        if sets_b >= target_sets:
            return Decimal("0")
        is_deciding_set = sets_a == target_sets - 1 and sets_b == target_sets - 1
        transitions = set_transition_distribution(
            player_1_serve_probability=player_1_serve_probability,
            player_2_serve_probability=player_2_serve_probability,
            scoring_rule_version=scoring_rule_version,
            is_deciding_set=is_deciding_set,
            next_server=next_server,
            config=config,
        )
        with localcontext() as ctx:
            ctx.prec = _context_precision(config)
            total = Decimal("0")
            for (winner, next_set_server), probability in transitions.items():
                if winner == 1:
                    total += probability * recurse(sets_a + 1, sets_b, next_set_server)
                else:
                    total += probability * recurse(sets_a, sets_b + 1, next_set_server)
            return total

    return recurse(0, 0, first_server)


def resolve_population_prior(
    population_prior_state: PopulationPriorState,
) -> tuple[Decimal, Decimal, PopulationFallbackLevel] | None:
    fallback_chain = (
        (
            population_prior_state.surface_tour_service_points_won,
            population_prior_state.surface_tour_service_points_played,
            PopulationFallbackLevel.SURFACE_TOUR,
        ),
        (
            population_prior_state.surface_service_points_won,
            population_prior_state.surface_service_points_played,
            PopulationFallbackLevel.SURFACE,
        ),
        (
            population_prior_state.global_service_points_won,
            population_prior_state.global_service_points_played,
            PopulationFallbackLevel.GLOBAL,
        ),
    )
    for numerator, denominator, fallback_level in fallback_chain:
        if denominator <= 0:
            continue
        mu_serve = _ratio_to_probability(
            Decimal(numerator),
            Decimal(denominator),
            field_name="population prior",
        )
        if not (Decimal("0") < mu_serve < Decimal("1")):
            raise Agent2ValidationError("population serve prior must be strictly within (0, 1)")
        mu_return = Decimal("1") - mu_serve
        return mu_serve, mu_return, fallback_level
    return None


def shrink_player_state(
    *,
    player_state: PlayerServeReturnState,
    mu_serve_context: Decimal,
    mu_return_context: Decimal,
    config: Agent2Config = DEFAULT_CONFIG,
) -> PlayerPosterior:
    _validate_probability(mu_serve_context, field_name="mu_serve_context")
    _validate_probability(mu_return_context, field_name="mu_return_context")
    with localcontext() as ctx:
        ctx.prec = _context_precision(config)
        serve_overall = (
            Decimal(player_state.overall_serve_points_won)
            + config.k_serve_overall * mu_serve_context
        ) / (Decimal(player_state.overall_serve_points_played) + config.k_serve_overall)
        serve_surface = (
            Decimal(player_state.surface_serve_points_won)
            + config.k_serve_surface * serve_overall
        ) / (Decimal(player_state.surface_serve_points_played) + config.k_serve_surface)
        return_overall = (
            Decimal(player_state.overall_return_points_won)
            + config.k_return_overall * mu_return_context
        ) / (Decimal(player_state.overall_return_points_played) + config.k_return_overall)
        return_surface = (
            Decimal(player_state.surface_return_points_won)
            + config.k_return_surface * return_overall
        ) / (Decimal(player_state.surface_return_points_played) + config.k_return_surface)
    for field_name, value in (
        ("serve_overall", serve_overall),
        ("serve_surface", serve_surface),
        ("return_overall", return_overall),
        ("return_surface", return_surface),
    ):
        _validate_probability(value, field_name=field_name)
    return PlayerPosterior(
        serve_overall=serve_overall,
        serve_surface=serve_surface,
        return_overall=return_overall,
        return_surface=return_surface,
    )


def matchup_serve_probability(
    *,
    server_serve_probability: Decimal,
    returner_return_probability: Decimal,
    context_serve_probability: Decimal,
    config: Agent2Config = DEFAULT_CONFIG,
) -> Decimal:
    serve_probability = _as_decimal(server_serve_probability, field_name="server_serve_probability")
    return_probability = _as_decimal(
        returner_return_probability,
        field_name="returner_return_probability",
    )
    context_probability = _as_decimal(
        context_serve_probability,
        field_name="context_serve_probability",
    )
    _validate_probability(serve_probability, field_name="server_serve_probability")
    _validate_probability(return_probability, field_name="returner_return_probability")
    _validate_probability(context_probability, field_name="context_serve_probability")
    with localcontext() as ctx:
        ctx.prec = _context_precision(config)
        odds_value = _odds(serve_probability) * _odds(Decimal("1") - return_probability) / _odds(
            context_probability
        )
        probability = odds_value / (Decimal("1") + odds_value)
    _validate_probability(probability, field_name="matchup serve probability")
    return probability


def _build_diagnostics(
    request: PredictionRequest,
    *,
    population_fallback_level: PopulationFallbackLevel | None = None,
    mu_c: Decimal | None = None,
    player_1_posterior: PlayerPosterior | None = None,
    player_2_posterior: PlayerPosterior | None = None,
    p_1_serve_vs_2: Decimal | None = None,
    p_2_serve_vs_1: Decimal | None = None,
    abstain_reason: AbstainReason | None = None,
) -> PredictionDiagnostics:
    return PredictionDiagnostics(
        player_1_prior_usable_matches=request.player_1_state.prior_usable_matches,
        player_2_prior_usable_matches=request.player_2_state.prior_usable_matches,
        player_1_overall_serve_points=request.player_1_state.overall_serve_points_played,
        player_2_overall_serve_points=request.player_2_state.overall_serve_points_played,
        player_1_overall_return_points=request.player_1_state.overall_return_points_played,
        player_2_overall_return_points=request.player_2_state.overall_return_points_played,
        player_1_surface_serve_points=request.player_1_state.surface_serve_points_played,
        player_2_surface_serve_points=request.player_2_state.surface_serve_points_played,
        player_1_surface_return_points=request.player_1_state.surface_return_points_played,
        player_2_surface_return_points=request.player_2_state.surface_return_points_played,
        population_fallback_level=population_fallback_level,
        mu_c=mu_c,
        player_1_shrunk_overall_serve=(
            None if player_1_posterior is None else player_1_posterior.serve_overall
        ),
        player_2_shrunk_overall_serve=(
            None if player_2_posterior is None else player_2_posterior.serve_overall
        ),
        player_1_shrunk_surface_serve=(
            None if player_1_posterior is None else player_1_posterior.serve_surface
        ),
        player_2_shrunk_surface_serve=(
            None if player_2_posterior is None else player_2_posterior.serve_surface
        ),
        player_1_shrunk_overall_return=(
            None if player_1_posterior is None else player_1_posterior.return_overall
        ),
        player_2_shrunk_overall_return=(
            None if player_2_posterior is None else player_2_posterior.return_overall
        ),
        player_1_shrunk_surface_return=(
            None if player_1_posterior is None else player_1_posterior.return_surface
        ),
        player_2_shrunk_surface_return=(
            None if player_2_posterior is None else player_2_posterior.return_surface
        ),
        p_1_serve_vs_2=p_1_serve_vs_2,
        p_2_serve_vs_1=p_2_serve_vs_1,
        scoring_rule_version=request.scoring_rule_version,
        best_of_sets=request.best_of_sets,
        first_server_marginalized=True,
        abstain_reason=abstain_reason,
    )


def _abstain(
    request: PredictionRequest,
    *,
    reason: AbstainReason,
    population_fallback_level: PopulationFallbackLevel | None = None,
    mu_c: Decimal | None = None,
    player_1_posterior: PlayerPosterior | None = None,
    player_2_posterior: PlayerPosterior | None = None,
    p_1_serve_vs_2: Decimal | None = None,
    p_2_serve_vs_1: Decimal | None = None,
    config: Agent2Config,
) -> PredictionResult:
    return PredictionResult(
        agent_code=AGENT_CODE,
        prediction_status="ABSTAINED",
        selection_side=None,
        win_probability=None,
        player_1_probability=None,
        player_2_probability=None,
        model_version=config.model_version,
        feature_version=config.feature_version,
        config_version=config.config_version,
        parameter_schema_version=config.parameter_schema_version,
        as_of=request.as_of,
        diagnostics=_build_diagnostics(
            request,
            population_fallback_level=population_fallback_level,
            mu_c=mu_c,
            player_1_posterior=player_1_posterior,
            player_2_posterior=player_2_posterior,
            p_1_serve_vs_2=p_1_serve_vs_2,
            p_2_serve_vs_1=p_2_serve_vs_1,
            abstain_reason=reason,
        ),
    )


def predict(
    request: PredictionRequest,
    config: Agent2Config = DEFAULT_CONFIG,
) -> PredictionResult:
    if request.surface is None:
        return _abstain(request, reason=AbstainReason.UNKNOWN_SURFACE, config=config)
    if request.resolver_abstain_reason is not None:
        return _abstain(request, reason=request.resolver_abstain_reason, config=config)
    if request.scoring_rule_version is None:
        return _abstain(request, reason=AbstainReason.UNSUPPORTED_SCORING, config=config)
    if (
        request.player_1_state.prior_usable_matches < config.minimum_prior_usable_matches
        or request.player_2_state.prior_usable_matches < config.minimum_prior_usable_matches
    ):
        return _abstain(request, reason=AbstainReason.INSUFFICIENT_HISTORY, config=config)
    if (
        request.player_1_state.overall_serve_points_played < config.minimum_prior_service_points
        or request.player_2_state.overall_serve_points_played < config.minimum_prior_service_points
        or request.player_1_state.overall_return_points_played < config.minimum_prior_return_points
        or request.player_2_state.overall_return_points_played < config.minimum_prior_return_points
    ):
        return _abstain(request, reason=AbstainReason.INSUFFICIENT_POINT_HISTORY, config=config)

    population_prior = resolve_population_prior(request.population_prior_state)
    if population_prior is None:
        return _abstain(request, reason=AbstainReason.PIT_STATE_UNRESOLVED, config=config)
    mu_serve_context, mu_return_context, fallback_level = population_prior
    player_1_posterior = shrink_player_state(
        player_state=request.player_1_state,
        mu_serve_context=mu_serve_context,
        mu_return_context=mu_return_context,
        config=config,
    )
    player_2_posterior = shrink_player_state(
        player_state=request.player_2_state,
        mu_serve_context=mu_serve_context,
        mu_return_context=mu_return_context,
        config=config,
    )
    p_1_serve_vs_2 = matchup_serve_probability(
        server_serve_probability=player_1_posterior.serve_surface,
        returner_return_probability=player_2_posterior.return_surface,
        context_serve_probability=mu_serve_context,
        config=config,
    )
    p_2_serve_vs_1 = matchup_serve_probability(
        server_serve_probability=player_2_posterior.serve_surface,
        returner_return_probability=player_1_posterior.return_surface,
        context_serve_probability=mu_serve_context,
        config=config,
    )
    player_1_probability_a_serves = match_probability(
        player_1_serve_probability=p_1_serve_vs_2,
        player_2_serve_probability=p_2_serve_vs_1,
        scoring_rule_version=request.scoring_rule_version,
        first_server=1,
        config=config,
    )
    player_1_probability_b_serves = match_probability(
        player_1_serve_probability=p_1_serve_vs_2,
        player_2_serve_probability=p_2_serve_vs_1,
        scoring_rule_version=request.scoring_rule_version,
        first_server=2,
        config=config,
    )
    with localcontext() as ctx:
        ctx.prec = _context_precision(config)
        player_1_probability_raw = (
            player_1_probability_a_serves + player_1_probability_b_serves
        ) / Decimal("2")
    if not (Decimal("0") < player_1_probability_raw < Decimal("1")):
        raise Agent2ValidationError("player_1 match probability must be strictly within (0, 1)")
    selection_side: Literal[1, 2] = 1 if player_1_probability_raw >= Decimal("0.5") else 2
    player_1_probability = _quantize_probability(player_1_probability_raw, config)
    player_2_probability = _quantize_probability(Decimal("1") - player_1_probability, config)
    win_probability = player_1_probability if selection_side == 1 else player_2_probability
    return PredictionResult(
        agent_code=AGENT_CODE,
        prediction_status="PREDICTED",
        selection_side=selection_side,
        win_probability=win_probability,
        player_1_probability=player_1_probability,
        player_2_probability=player_2_probability,
        model_version=config.model_version,
        feature_version=config.feature_version,
        config_version=config.config_version,
        parameter_schema_version=config.parameter_schema_version,
        as_of=request.as_of,
        diagnostics=_build_diagnostics(
            request,
            population_fallback_level=fallback_level,
            mu_c=mu_serve_context,
            player_1_posterior=player_1_posterior,
            player_2_posterior=player_2_posterior,
            p_1_serve_vs_2=p_1_serve_vs_2,
            p_2_serve_vs_1=p_2_serve_vs_1,
        ),
    )
