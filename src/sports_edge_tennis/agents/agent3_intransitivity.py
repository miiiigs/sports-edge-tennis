from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, localcontext
from enum import Enum
from typing import Literal


AGENT_CODE = "AGENT_3_INTRANSITIVITY"
SUPPORTED_SURFACES = ("HARD", "CLAY", "GRASS", "CARPET")
_PROBABILITY_QUANTUM = Decimal("0.000001")

Surface = Literal["HARD", "CLAY", "GRASS", "CARPET"]
ActivationMode = Literal["DIRECT_PLUS_NETWORK", "DIRECT_ONLY", "NETWORK_ONLY"]


class Agent3ValidationError(ValueError):
    """Raised when Agent 3 receives structurally invalid input."""


class AbstainReason(str, Enum):
    UNKNOWN_SURFACE = "UNKNOWN_SURFACE"
    PIT_STATE_UNRESOLVED = "PIT_STATE_UNRESOLVED"
    UNSUPPORTED_OR_UNKNOWN = "UNSUPPORTED_OR_UNKNOWN"
    INSUFFICIENT_MATCHUP_SUPPORT = "INSUFFICIENT_MATCHUP_SUPPORT"
    CONFLICTING_DIRECT_NETWORK = "CONFLICTING_DIRECT_NETWORK"
    WEAK_SIGNAL = "WEAK_SIGNAL"


def _as_decimal(value: Decimal | int | str, *, field_name: str) -> Decimal:
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:  # pragma: no cover - defensive conversion path
        raise Agent3ValidationError(f"{field_name} must be Decimal-compatible") from exc
    if not decimal_value.is_finite():
        raise Agent3ValidationError(f"{field_name} must be finite")
    return decimal_value


def _locked_decimal(value: Decimal | int | str, *, field_name: str, expected: str) -> Decimal:
    decimal_value = _as_decimal(value, field_name=field_name)
    if decimal_value != Decimal(expected):
        raise Agent3ValidationError(f"{field_name} must remain locked at {expected}")
    return decimal_value


def _locked_string(value: str, *, field_name: str, expected: str) -> str:
    if value != expected:
        raise Agent3ValidationError(f"{field_name} must remain locked at {expected}")
    return value


def _decimal_to_payload(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _validate_surface(surface: str | None, *, allow_none: bool = True) -> None:
    if surface is None:
        if allow_none:
            return
        raise Agent3ValidationError("surface must be known for Agent 3 prediction")
    if surface not in SUPPORTED_SURFACES:
        raise Agent3ValidationError(f"surface must be one of {SUPPORTED_SURFACES}")


def _canonical_pair(player_a_id: int, player_b_id: int) -> tuple[int, int]:
    return (player_a_id, player_b_id) if player_a_id < player_b_id else (player_b_id, player_a_id)


def _oriented_value(
    canonical_value: Decimal,
    *,
    requested_player_a_id: int,
    requested_player_b_id: int,
    player_low_id: int,
    player_high_id: int,
) -> Decimal:
    if (requested_player_a_id, requested_player_b_id) == (player_low_id, player_high_id):
        return canonical_value
    if (requested_player_a_id, requested_player_b_id) == (player_high_id, player_low_id):
        return -canonical_value
    raise Agent3ValidationError("requested orientation must match the canonical pair members")


def _context_precision(config: "Agent3Config") -> int:
    return max(config.internal_decimal_precision, 50)


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


@dataclass(frozen=True)
class Agent3Config:
    half_life_days: int = 730
    lookback_days: int = 1460
    k_g: Decimal = Decimal("8")
    k_d: Decimal = Decimal("3")
    k_n: Decimal = Decimal("8")
    k_surface: Decimal = Decimal("8")
    direct_weight: Decimal = Decimal("0.70")
    network_weight: Decimal = Decimal("0.30")
    logistic_lambda: Decimal = Decimal("0.85")
    probability_min: Decimal = Decimal("0.350000")
    probability_max: Decimal = Decimal("0.650000")
    activation_probability_floor: Decimal = Decimal("0.530000")
    signal_floor: Decimal = Decimal("0.12")
    direct_raw_support: int = 2
    direct_effective_support: Decimal = Decimal("2.5")
    network_edge_raw_support: int = 1
    network_edge_effective_support: Decimal = Decimal("1.0")
    normal_network_shared_support: int = 5
    normal_network_q_support: Decimal = Decimal("8")
    network_only_shared_support: int = 8
    network_only_q_support: Decimal = Decimal("16")
    network_only_signal_floor: Decimal = Decimal("0.16")
    conflict_component_floor: Decimal = Decimal("0.12")
    expected_gap_cap: Decimal = Decimal("0.60")
    internal_decimal_precision: int = 28
    probability_decimal_places: int = 6
    model_version: str = "agent3_intransitivity_v1"
    feature_version: str = "agent3_matchup_network_inputs_v1"
    config_version: str = "agent3_intransitivity_config_v1"
    parameter_schema_version: str = "agent3_intransitivity_params_v1"

    def __post_init__(self) -> None:
        if self.half_life_days != 730:
            raise Agent3ValidationError("half_life_days must remain locked at 730")
        if self.lookback_days != 1460:
            raise Agent3ValidationError("lookback_days must remain locked at 1460")
        object.__setattr__(self, "k_g", _locked_decimal(self.k_g, field_name="k_g", expected="8"))
        object.__setattr__(self, "k_d", _locked_decimal(self.k_d, field_name="k_d", expected="3"))
        object.__setattr__(self, "k_n", _locked_decimal(self.k_n, field_name="k_n", expected="8"))
        object.__setattr__(
            self,
            "k_surface",
            _locked_decimal(self.k_surface, field_name="k_surface", expected="8"),
        )
        object.__setattr__(
            self,
            "direct_weight",
            _locked_decimal(self.direct_weight, field_name="direct_weight", expected="0.70"),
        )
        object.__setattr__(
            self,
            "network_weight",
            _locked_decimal(self.network_weight, field_name="network_weight", expected="0.30"),
        )
        object.__setattr__(
            self,
            "logistic_lambda",
            _locked_decimal(self.logistic_lambda, field_name="logistic_lambda", expected="0.85"),
        )
        object.__setattr__(
            self,
            "probability_min",
            _locked_decimal(self.probability_min, field_name="probability_min", expected="0.350000"),
        )
        object.__setattr__(
            self,
            "probability_max",
            _locked_decimal(self.probability_max, field_name="probability_max", expected="0.650000"),
        )
        object.__setattr__(
            self,
            "activation_probability_floor",
            _locked_decimal(
                self.activation_probability_floor,
                field_name="activation_probability_floor",
                expected="0.530000",
            ),
        )
        object.__setattr__(
            self,
            "signal_floor",
            _locked_decimal(self.signal_floor, field_name="signal_floor", expected="0.12"),
        )
        if self.direct_raw_support != 2:
            raise Agent3ValidationError("direct_raw_support must remain locked at 2")
        object.__setattr__(
            self,
            "direct_effective_support",
            _locked_decimal(
                self.direct_effective_support,
                field_name="direct_effective_support",
                expected="2.5",
            ),
        )
        if self.network_edge_raw_support != 1:
            raise Agent3ValidationError("network_edge_raw_support must remain locked at 1")
        object.__setattr__(
            self,
            "network_edge_effective_support",
            _locked_decimal(
                self.network_edge_effective_support,
                field_name="network_edge_effective_support",
                expected="1.0",
            ),
        )
        if self.normal_network_shared_support != 5:
            raise Agent3ValidationError("normal_network_shared_support must remain locked at 5")
        object.__setattr__(
            self,
            "normal_network_q_support",
            _locked_decimal(
                self.normal_network_q_support,
                field_name="normal_network_q_support",
                expected="8",
            ),
        )
        if self.network_only_shared_support != 8:
            raise Agent3ValidationError("network_only_shared_support must remain locked at 8")
        object.__setattr__(
            self,
            "network_only_q_support",
            _locked_decimal(
                self.network_only_q_support,
                field_name="network_only_q_support",
                expected="16",
            ),
        )
        object.__setattr__(
            self,
            "network_only_signal_floor",
            _locked_decimal(
                self.network_only_signal_floor,
                field_name="network_only_signal_floor",
                expected="0.16",
            ),
        )
        object.__setattr__(
            self,
            "conflict_component_floor",
            _locked_decimal(
                self.conflict_component_floor,
                field_name="conflict_component_floor",
                expected="0.12",
            ),
        )
        object.__setattr__(
            self,
            "expected_gap_cap",
            _locked_decimal(
                self.expected_gap_cap,
                field_name="expected_gap_cap",
                expected="0.60",
            ),
        )
        if self.internal_decimal_precision < 28:
            raise Agent3ValidationError("internal_decimal_precision must be at least 28")
        if self.probability_decimal_places != 6:
            raise Agent3ValidationError("probability_decimal_places must remain locked at 6")
        object.__setattr__(
            self,
            "model_version",
            _locked_string(
                self.model_version,
                field_name="model_version",
                expected="agent3_intransitivity_v1",
            ),
        )
        object.__setattr__(
            self,
            "feature_version",
            _locked_string(
                self.feature_version,
                field_name="feature_version",
                expected="agent3_matchup_network_inputs_v1",
            ),
        )
        object.__setattr__(
            self,
            "config_version",
            _locked_string(
                self.config_version,
                field_name="config_version",
                expected="agent3_intransitivity_config_v1",
            ),
        )
        object.__setattr__(
            self,
            "parameter_schema_version",
            _locked_string(
                self.parameter_schema_version,
                field_name="parameter_schema_version",
                expected="agent3_intransitivity_params_v1",
            ),
        )

    @property
    def probability_quantum(self) -> Decimal:
        return Decimal("1").scaleb(-self.probability_decimal_places)

    def to_payload(self) -> dict[str, object]:
        return {
            "half_life_days": self.half_life_days,
            "lookback_days": self.lookback_days,
            "k_g": str(self.k_g),
            "k_d": str(self.k_d),
            "k_n": str(self.k_n),
            "k_surface": str(self.k_surface),
            "direct_weight": str(self.direct_weight),
            "network_weight": str(self.network_weight),
            "logistic_lambda": str(self.logistic_lambda),
            "probability_min": str(self.probability_min),
            "probability_max": str(self.probability_max),
            "activation_probability_floor": str(self.activation_probability_floor),
            "signal_floor": str(self.signal_floor),
            "direct_raw_support": self.direct_raw_support,
            "direct_effective_support": str(self.direct_effective_support),
            "network_edge_raw_support": self.network_edge_raw_support,
            "network_edge_effective_support": str(self.network_edge_effective_support),
            "normal_network_shared_support": self.normal_network_shared_support,
            "normal_network_q_support": str(self.normal_network_q_support),
            "network_only_shared_support": self.network_only_shared_support,
            "network_only_q_support": str(self.network_only_q_support),
            "network_only_signal_floor": str(self.network_only_signal_floor),
            "conflict_component_floor": str(self.conflict_component_floor),
            "expected_gap_cap": str(self.expected_gap_cap),
            "internal_decimal_precision": self.internal_decimal_precision,
            "probability_decimal_places": self.probability_decimal_places,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "config_version": self.config_version,
            "parameter_schema_version": self.parameter_schema_version,
        }


DEFAULT_CONFIG = Agent3Config()


@dataclass(frozen=True)
class OutcomeObservation:
    observed_at: datetime
    value: int

    def __post_init__(self) -> None:
        if self.value not in (-1, 1):
            raise Agent3ValidationError("OutcomeObservation value must be +1 or -1")


@dataclass(frozen=True)
class ResidualObservation:
    observed_at: datetime
    residual: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "residual", _as_decimal(self.residual, field_name="residual"))


@dataclass(frozen=True)
class PlayerHistorySnapshot:
    player_id: int
    all_observations: tuple[OutcomeObservation, ...] = ()
    surface_observations: dict[Surface, tuple[OutcomeObservation, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.player_id <= 0:
            raise Agent3ValidationError("player_id must be positive")
        for surface in self.surface_observations:
            _validate_surface(surface, allow_none=False)


@dataclass(frozen=True)
class PairHistorySnapshot:
    player_low_id: int
    player_high_id: int
    all_observations: tuple[ResidualObservation, ...] = ()
    surface_observations: dict[Surface, tuple[ResidualObservation, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.player_low_id <= 0 or self.player_high_id <= 0:
            raise Agent3ValidationError("pair player ids must be positive")
        if self.player_low_id >= self.player_high_id:
            raise Agent3ValidationError("pair storage must use canonical low/high orientation")
        for surface in self.surface_observations:
            _validate_surface(surface, allow_none=False)


@dataclass(frozen=True)
class Agent3HistorySnapshot:
    players: dict[int, PlayerHistorySnapshot] = field(default_factory=dict)
    pairs: dict[tuple[int, int], PairHistorySnapshot] = field(default_factory=dict)
    adjacency: dict[int, tuple[int, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictionRequest:
    player_1_id: int
    player_2_id: int
    surface: Surface | None
    history_snapshot: Agent3HistorySnapshot
    as_of: datetime
    pit_state_resolved: bool = True
    supported_matchup: bool = True

    def __post_init__(self) -> None:
        if self.player_1_id <= 0 or self.player_2_id <= 0:
            raise Agent3ValidationError("player ids must be positive")
        if self.player_1_id == self.player_2_id:
            raise Agent3ValidationError("player_1_id and player_2_id must differ")
        _validate_surface(self.surface)


@dataclass(frozen=True)
class PlayerBucketSummary:
    raw_count: int = 0
    effective_count: Decimal = Decimal("0")
    weighted_mean: Decimal = Decimal("0")
    baseline: Decimal = Decimal("0")


@dataclass(frozen=True)
class PairResidualSummary:
    raw_count: int = 0
    effective_count: Decimal = Decimal("0")
    weighted_mean_residual: Decimal = Decimal("0")
    residual_score: Decimal = Decimal("0")
    direct_supported: bool = False


@dataclass(frozen=True)
class NetworkSummary:
    shared_opponent_count: int = 0
    q_total: Decimal = Decimal("0")
    weighted_mean_gap: Decimal = Decimal("0")
    network_score: Decimal = Decimal("0")
    normal_supported: bool = False
    network_only_structural_supported: bool = False


@dataclass(frozen=True)
class PredictionDiagnostics:
    direct_raw_count_all: int
    direct_effective_count_all: Decimal
    direct_raw_count_surface: int
    direct_effective_count_surface: Decimal
    network_shared_opponents_all: int
    network_q_all: Decimal
    network_shared_opponents_surface: int
    network_q_surface: Decimal
    D_all: Decimal
    D_surface: Decimal
    N_all: Decimal
    N_surface: Decimal
    D_blended: Decimal
    N_blended: Decimal
    S_all: Decimal | None
    S_surface: Decimal | None
    eta: Decimal
    final_score: Decimal | None
    activation_mode: ActivationMode | None
    abstain_reason: AbstainReason | None

    def to_payload(self) -> dict[str, object]:
        return {
            "direct_raw_count_all": self.direct_raw_count_all,
            "direct_effective_count_all": str(self.direct_effective_count_all),
            "direct_raw_count_surface": self.direct_raw_count_surface,
            "direct_effective_count_surface": str(self.direct_effective_count_surface),
            "network_shared_opponents_all": self.network_shared_opponents_all,
            "network_q_all": str(self.network_q_all),
            "network_shared_opponents_surface": self.network_shared_opponents_surface,
            "network_q_surface": str(self.network_q_surface),
            "D_all": str(self.D_all),
            "D_surface": str(self.D_surface),
            "N_all": str(self.N_all),
            "N_surface": str(self.N_surface),
            "D_blended": str(self.D_blended),
            "N_blended": str(self.N_blended),
            "S_all": _decimal_to_payload(self.S_all),
            "S_surface": _decimal_to_payload(self.S_surface),
            "eta": str(self.eta),
            "final_score": _decimal_to_payload(self.final_score),
            "activation_mode": self.activation_mode,
            "abstain_reason": None if self.abstain_reason is None else self.abstain_reason.value,
        }


@dataclass(frozen=True)
class PredictionResult:
    agent_code: str
    prediction_status: Literal["PREDICTED", "ABSTAINED"]
    selected_side: Literal[1, 2] | None
    selected_probability: Decimal | None
    player_1_probability: Decimal | None
    player_2_probability: Decimal | None
    abstain_reason: AbstainReason | None
    model_version: str
    feature_version: str
    config_version: str
    parameter_schema_version: str
    as_of: datetime
    diagnostics: PredictionDiagnostics


@dataclass(frozen=True)
class _BucketEvaluation:
    pair_summary: PairResidualSummary = PairResidualSummary()
    network_summary: NetworkSummary = NetworkSummary()
    score_if_direct: Decimal | None = None
    direct_component: Decimal | None = None
    network_component_for_direct: Decimal | None = None
    network_only_candidate: Decimal | None = None


def shrink(n: Decimal | int | str, k: Decimal | int | str) -> Decimal:
    support = _as_decimal(n, field_name="n")
    k_value = _as_decimal(k, field_name="k")
    if support <= 0:
        return Decimal("0")
    with localcontext() as ctx:
        ctx.prec = 50
        return support / (support + k_value)


def _timedelta_to_days_decimal(observed_at: datetime, target_as_of: datetime) -> Decimal:
    delta = target_as_of - observed_at
    total_seconds = delta.days * 86400 + delta.seconds
    with localcontext() as ctx:
        ctx.prec = 50
        return (Decimal(total_seconds) / Decimal("86400")) + (
            Decimal(delta.microseconds) / Decimal("86400000000")
        )


def temporal_weight(
    observed_at: datetime,
    target_as_of: datetime,
    config: Agent3Config = DEFAULT_CONFIG,
) -> Decimal:
    age_days = _timedelta_to_days_decimal(observed_at, target_as_of)
    if age_days < 0 or age_days > Decimal(config.lookback_days):
        return Decimal("0")
    if age_days == 0:
        return Decimal("1")
    with localcontext() as ctx:
        ctx.prec = _context_precision(config)
        exponent = -(Decimal("2").ln() * age_days) / Decimal(config.half_life_days)
        return exponent.exp()


def weighted_mean(
    weighted_values: list[tuple[Decimal, Decimal]] | tuple[tuple[Decimal, Decimal], ...],
) -> Decimal:
    total_weight = sum((weight for _, weight in weighted_values), Decimal("0"))
    if total_weight == Decimal("0"):
        return Decimal("0")
    with localcontext() as ctx:
        ctx.prec = 50
        weighted_sum = sum((value * weight for value, weight in weighted_values), Decimal("0"))
        return weighted_sum / total_weight


def summarize_player_bucket(
    snapshot: Agent3HistorySnapshot,
    player_id: int,
    *,
    surface: Surface | None,
    as_of: datetime,
    config: Agent3Config = DEFAULT_CONFIG,
) -> PlayerBucketSummary:
    player_snapshot = snapshot.players.get(player_id)
    if player_snapshot is None:
        return PlayerBucketSummary()
    if surface is None:
        observations = player_snapshot.all_observations
    else:
        observations = player_snapshot.surface_observations.get(surface, ())
    weighted_values: list[tuple[Decimal, Decimal]] = []
    for observation in observations:
        weight = temporal_weight(observation.observed_at, as_of, config)
        if weight == Decimal("0"):
            continue
        weighted_values.append((Decimal(observation.value), weight))
    raw_count = len(weighted_values)
    effective_count = sum((weight for _, weight in weighted_values), Decimal("0"))
    mean_value = weighted_mean(weighted_values)
    baseline = shrink(effective_count, config.k_g) * mean_value if effective_count > 0 else Decimal("0")
    return PlayerBucketSummary(
        raw_count=raw_count,
        effective_count=effective_count,
        weighted_mean=mean_value,
        baseline=baseline,
    )


def residual_for_match(
    player_baseline: Decimal | int | str,
    opponent_baseline: Decimal | int | str,
    *,
    outcome: int,
    config: Agent3Config = DEFAULT_CONFIG,
) -> Decimal:
    if outcome not in (-1, 1):
        raise Agent3ValidationError("outcome must be +1 or -1")
    baseline_value = _as_decimal(player_baseline, field_name="player_baseline")
    opponent_value = _as_decimal(opponent_baseline, field_name="opponent_baseline")
    expected_gap = _clamp(
        baseline_value - opponent_value,
        -config.expected_gap_cap,
        config.expected_gap_cap,
    )
    return Decimal(outcome) - expected_gap


def summarize_pair_residual_bucket(
    snapshot: Agent3HistorySnapshot,
    player_a_id: int,
    player_b_id: int,
    *,
    surface: Surface | None,
    as_of: datetime,
    config: Agent3Config = DEFAULT_CONFIG,
) -> PairResidualSummary:
    pair_key = _canonical_pair(player_a_id, player_b_id)
    pair_snapshot = snapshot.pairs.get(pair_key)
    if pair_snapshot is None:
        return PairResidualSummary()
    if surface is None:
        observations = pair_snapshot.all_observations
    else:
        observations = pair_snapshot.surface_observations.get(surface, ())
    weighted_values: list[tuple[Decimal, Decimal]] = []
    for observation in observations:
        weight = temporal_weight(observation.observed_at, as_of, config)
        if weight == Decimal("0"):
            continue
        residual = _oriented_value(
            observation.residual,
            requested_player_a_id=player_a_id,
            requested_player_b_id=player_b_id,
            player_low_id=pair_snapshot.player_low_id,
            player_high_id=pair_snapshot.player_high_id,
        )
        weighted_values.append((residual, weight))
    raw_count = len(weighted_values)
    effective_count = sum((weight for _, weight in weighted_values), Decimal("0"))
    mean_residual = weighted_mean(weighted_values)
    residual_score = shrink(effective_count, config.k_d) * mean_residual if effective_count > 0 else Decimal("0")
    return PairResidualSummary(
        raw_count=raw_count,
        effective_count=effective_count,
        weighted_mean_residual=mean_residual,
        residual_score=residual_score,
        direct_supported=(
            raw_count >= config.direct_raw_support
            and effective_count >= config.direct_effective_support
        ),
    )


def summarize_network_bucket(
    snapshot: Agent3HistorySnapshot,
    player_a_id: int,
    player_b_id: int,
    *,
    surface: Surface | None,
    as_of: datetime,
    config: Agent3Config = DEFAULT_CONFIG,
) -> NetworkSummary:
    neighbors_a = set(snapshot.adjacency.get(player_a_id, ()))
    neighbors_b = set(snapshot.adjacency.get(player_b_id, ()))
    shared_opponents = sorted((neighbors_a & neighbors_b) - {player_a_id, player_b_id})
    eligible_count = 0
    q_total = Decimal("0")
    weighted_gaps: list[tuple[Decimal, Decimal]] = []
    for opponent_id in shared_opponents:
        pair_a = summarize_pair_residual_bucket(
            snapshot,
            player_a_id,
            opponent_id,
            surface=surface,
            as_of=as_of,
            config=config,
        )
        pair_b = summarize_pair_residual_bucket(
            snapshot,
            player_b_id,
            opponent_id,
            surface=surface,
            as_of=as_of,
            config=config,
        )
        if (
            pair_a.raw_count < config.network_edge_raw_support
            or pair_b.raw_count < config.network_edge_raw_support
            or pair_a.effective_count < config.network_edge_effective_support
            or pair_b.effective_count < config.network_edge_effective_support
        ):
            continue
        eligible_count += 1
        q_value = min(pair_a.effective_count, pair_b.effective_count)
        q_total += q_value
        weighted_gaps.append((pair_a.residual_score - pair_b.residual_score, q_value))
    mean_gap = weighted_mean(weighted_gaps)
    network_score = shrink(q_total, config.k_n) * mean_gap if q_total > 0 else Decimal("0")
    return NetworkSummary(
        shared_opponent_count=eligible_count,
        q_total=q_total,
        weighted_mean_gap=mean_gap,
        network_score=network_score,
        normal_supported=(
            eligible_count >= config.normal_network_shared_support
            and q_total >= config.normal_network_q_support
        ),
        network_only_structural_supported=(
            eligible_count >= config.network_only_shared_support
            and q_total >= config.network_only_q_support
        ),
    )


def surface_blend_weight(
    effective_direct_surface: Decimal | int | str,
    q_surface: Decimal | int | str,
    config: Agent3Config = DEFAULT_CONFIG,
) -> Decimal:
    direct_support = _as_decimal(effective_direct_surface, field_name="effective_direct_surface")
    network_support = _as_decimal(q_surface, field_name="q_surface")
    evidence = direct_support + network_support
    if evidence == Decimal("0"):
        return Decimal("0")
    with localcontext() as ctx:
        ctx.prec = _context_precision(config)
        return evidence / (evidence + config.k_surface)


def combined_score(
    direct_component: Decimal | int | str,
    network_component: Decimal | int | str,
    config: Agent3Config = DEFAULT_CONFIG,
) -> Decimal:
    direct_value = _as_decimal(direct_component, field_name="direct_component")
    network_value = _as_decimal(network_component, field_name="network_component")
    with localcontext() as ctx:
        ctx.prec = _context_precision(config)
        return (config.direct_weight * direct_value) + (config.network_weight * network_value)


def logistic_probability(score: Decimal | int | str, config: Agent3Config = DEFAULT_CONFIG) -> Decimal:
    score_value = _as_decimal(score, field_name="score")
    if score_value == Decimal("0"):
        return Decimal("0.5")
    magnitude = abs(score_value)
    with localcontext() as ctx:
        ctx.prec = _context_precision(config)
        exp_term = (-(config.logistic_lambda * magnitude)).exp()
        positive_probability = Decimal("1") / (Decimal("1") + exp_term)
        probability = (
            positive_probability
            if score_value > 0
            else Decimal("1") - positive_probability
        )
    return _clamp(probability, config.probability_min, config.probability_max)


def _blend_available(
    eta: Decimal,
    all_value: Decimal | None,
    surface_value: Decimal | None,
    *,
    config: Agent3Config,
) -> Decimal | None:
    if all_value is None and surface_value is None:
        return None
    if all_value is None:
        return surface_value
    if surface_value is None:
        return all_value
    with localcontext() as ctx:
        ctx.prec = _context_precision(config)
        return (eta * surface_value) + ((Decimal("1") - eta) * all_value)


def _evaluate_bucket(
    snapshot: Agent3HistorySnapshot,
    *,
    player_a_id: int,
    player_b_id: int,
    surface: Surface | None,
    as_of: datetime,
    config: Agent3Config,
) -> _BucketEvaluation:
    pair_summary = summarize_pair_residual_bucket(
        snapshot,
        player_a_id,
        player_b_id,
        surface=surface,
        as_of=as_of,
        config=config,
    )
    network_summary = summarize_network_bucket(
        snapshot,
        player_a_id,
        player_b_id,
        surface=surface,
        as_of=as_of,
        config=config,
    )
    if pair_summary.direct_supported:
        network_component = (
            network_summary.network_score if network_summary.normal_supported else Decimal("0")
        )
        return _BucketEvaluation(
            pair_summary=pair_summary,
            network_summary=network_summary,
            score_if_direct=combined_score(
                pair_summary.residual_score,
                network_component,
                config=config,
            ),
            direct_component=pair_summary.residual_score,
            network_component_for_direct=network_component,
        )
    return _BucketEvaluation(
        pair_summary=pair_summary,
        network_summary=network_summary,
        network_only_candidate=(
            network_summary.network_score
            if network_summary.network_only_structural_supported
            else None
        ),
    )


def _build_diagnostics(
    all_bucket: _BucketEvaluation,
    surface_bucket: _BucketEvaluation,
    *,
    eta: Decimal,
    D_blended: Decimal = Decimal("0"),
    N_blended: Decimal = Decimal("0"),
    final_score: Decimal | None = None,
    activation_mode: ActivationMode | None = None,
    abstain_reason: AbstainReason | None = None,
) -> PredictionDiagnostics:
    return PredictionDiagnostics(
        direct_raw_count_all=all_bucket.pair_summary.raw_count,
        direct_effective_count_all=all_bucket.pair_summary.effective_count,
        direct_raw_count_surface=surface_bucket.pair_summary.raw_count,
        direct_effective_count_surface=surface_bucket.pair_summary.effective_count,
        network_shared_opponents_all=all_bucket.network_summary.shared_opponent_count,
        network_q_all=all_bucket.network_summary.q_total,
        network_shared_opponents_surface=surface_bucket.network_summary.shared_opponent_count,
        network_q_surface=surface_bucket.network_summary.q_total,
        D_all=all_bucket.pair_summary.residual_score,
        D_surface=surface_bucket.pair_summary.residual_score,
        N_all=all_bucket.network_summary.network_score,
        N_surface=surface_bucket.network_summary.network_score,
        D_blended=D_blended,
        N_blended=N_blended,
        S_all=all_bucket.score_if_direct,
        S_surface=surface_bucket.score_if_direct,
        eta=eta,
        final_score=final_score,
        activation_mode=activation_mode,
        abstain_reason=abstain_reason,
    )


def _abstain(
    request: PredictionRequest,
    *,
    reason: AbstainReason,
    all_bucket: _BucketEvaluation | None = None,
    surface_bucket: _BucketEvaluation | None = None,
    eta: Decimal = Decimal("0"),
    D_blended: Decimal = Decimal("0"),
    N_blended: Decimal = Decimal("0"),
    final_score: Decimal | None = None,
    activation_mode: ActivationMode | None = None,
    config: Agent3Config,
) -> PredictionResult:
    diagnostics = _build_diagnostics(
        all_bucket or _BucketEvaluation(),
        surface_bucket or _BucketEvaluation(),
        eta=eta,
        D_blended=D_blended,
        N_blended=N_blended,
        final_score=final_score,
        activation_mode=activation_mode,
        abstain_reason=reason,
    )
    return PredictionResult(
        agent_code=AGENT_CODE,
        prediction_status="ABSTAINED",
        selected_side=None,
        selected_probability=None,
        player_1_probability=None,
        player_2_probability=None,
        abstain_reason=reason,
        model_version=config.model_version,
        feature_version=config.feature_version,
        config_version=config.config_version,
        parameter_schema_version=config.parameter_schema_version,
        as_of=request.as_of,
        diagnostics=diagnostics,
    )


def _quantize_probability(value: Decimal, config: Agent3Config) -> Decimal:
    return value.quantize(config.probability_quantum)


def _selected_probability(
    probability: Decimal,
    *,
    score: Decimal,
    config: Agent3Config,
) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = _context_precision(config)
        return probability if score > 0 else Decimal("1") - probability


def predict(
    request: PredictionRequest,
    config: Agent3Config = DEFAULT_CONFIG,
) -> PredictionResult:
    if request.surface is None:
        return _abstain(request, reason=AbstainReason.UNKNOWN_SURFACE, config=config)
    if not request.pit_state_resolved:
        return _abstain(request, reason=AbstainReason.PIT_STATE_UNRESOLVED, config=config)
    if not request.supported_matchup:
        return _abstain(request, reason=AbstainReason.UNSUPPORTED_OR_UNKNOWN, config=config)

    all_bucket = _evaluate_bucket(
        request.history_snapshot,
        player_a_id=request.player_1_id,
        player_b_id=request.player_2_id,
        surface=None,
        as_of=request.as_of,
        config=config,
    )
    surface_bucket = _evaluate_bucket(
        request.history_snapshot,
        player_a_id=request.player_1_id,
        player_b_id=request.player_2_id,
        surface=request.surface,
        as_of=request.as_of,
        config=config,
    )
    eta = surface_blend_weight(
        surface_bucket.pair_summary.effective_count,
        surface_bucket.network_summary.q_total,
        config,
    )

    direct_all = all_bucket.score_if_direct is not None
    direct_surface = surface_bucket.score_if_direct is not None
    if direct_all or direct_surface:
        final_score = _blend_available(
            eta,
            all_bucket.score_if_direct if direct_all else None,
            surface_bucket.score_if_direct if direct_surface else None,
            config=config,
        )
        D_blended = _blend_available(
            eta,
            all_bucket.direct_component if direct_all else None,
            surface_bucket.direct_component if direct_surface else None,
            config=config,
        ) or Decimal("0")
        N_blended = _blend_available(
            eta,
            all_bucket.network_component_for_direct if direct_all else None,
            surface_bucket.network_component_for_direct if direct_surface else None,
            config=config,
        ) or Decimal("0")
        activation_mode: ActivationMode = (
            "DIRECT_PLUS_NETWORK"
            if (
                (direct_all and all_bucket.network_summary.normal_supported)
                or (direct_surface and surface_bucket.network_summary.normal_supported)
            )
            else "DIRECT_ONLY"
        )
        diagnostics = _build_diagnostics(
            all_bucket,
            surface_bucket,
            eta=eta,
            D_blended=D_blended,
            N_blended=N_blended,
            final_score=final_score,
            activation_mode=activation_mode,
        )
        if (
            activation_mode == "DIRECT_PLUS_NETWORK"
            and abs(D_blended) >= config.conflict_component_floor
            and abs(N_blended) >= config.conflict_component_floor
            and _sign(D_blended) != _sign(N_blended)
        ):
            return _abstain(
                request,
                reason=AbstainReason.CONFLICTING_DIRECT_NETWORK,
                all_bucket=all_bucket,
                surface_bucket=surface_bucket,
                eta=eta,
                D_blended=D_blended,
                N_blended=N_blended,
                final_score=final_score,
                activation_mode=activation_mode,
                config=config,
            )
        if final_score is None:
            return _abstain(
                request,
                reason=AbstainReason.INSUFFICIENT_MATCHUP_SUPPORT,
                all_bucket=all_bucket,
                surface_bucket=surface_bucket,
                eta=eta,
                D_blended=D_blended,
                N_blended=N_blended,
                config=config,
            )
        if final_score == Decimal("0") or abs(final_score) < config.signal_floor:
            return _abstain(
                request,
                reason=AbstainReason.WEAK_SIGNAL,
                all_bucket=all_bucket,
                surface_bucket=surface_bucket,
                eta=eta,
                D_blended=D_blended,
                N_blended=N_blended,
                final_score=final_score,
                activation_mode=activation_mode,
                config=config,
            )
        probability = logistic_probability(final_score, config)
        selected_probability = _selected_probability(
            probability,
            score=final_score,
            config=config,
        )
        if selected_probability < config.activation_probability_floor:
            return _abstain(
                request,
                reason=AbstainReason.WEAK_SIGNAL,
                all_bucket=all_bucket,
                surface_bucket=surface_bucket,
                eta=eta,
                D_blended=D_blended,
                N_blended=N_blended,
                final_score=final_score,
                activation_mode=activation_mode,
                config=config,
            )
        selected_side: Literal[1, 2] = 1 if final_score > 0 else 2
        quantized_selected = _quantize_probability(selected_probability, config)
        quantized_other = _quantize_probability(Decimal("1") - quantized_selected, config)
        if selected_side == 1:
            player_1_probability = quantized_selected
            player_2_probability = quantized_other
        else:
            player_1_probability = quantized_other
            player_2_probability = quantized_selected
        if player_1_probability == Decimal("0.500000"):
            return _abstain(
                request,
                reason=AbstainReason.WEAK_SIGNAL,
                all_bucket=all_bucket,
                surface_bucket=surface_bucket,
                eta=eta,
                D_blended=D_blended,
                N_blended=N_blended,
                final_score=final_score,
                activation_mode=activation_mode,
                config=config,
            )
        return PredictionResult(
            agent_code=AGENT_CODE,
            prediction_status="PREDICTED",
            selected_side=selected_side,
            selected_probability=selected_probability,
            player_1_probability=player_1_probability,
            player_2_probability=player_2_probability,
            abstain_reason=None,
            model_version=config.model_version,
            feature_version=config.feature_version,
            config_version=config.config_version,
            parameter_schema_version=config.parameter_schema_version,
            as_of=request.as_of,
            diagnostics=diagnostics,
        )

    network_all = all_bucket.network_only_candidate is not None
    network_surface = surface_bucket.network_only_candidate is not None
    if not (network_all or network_surface):
        return _abstain(
            request,
            reason=AbstainReason.INSUFFICIENT_MATCHUP_SUPPORT,
            all_bucket=all_bucket,
            surface_bucket=surface_bucket,
            eta=eta,
            config=config,
        )
    N_blended = _blend_available(
        eta,
        all_bucket.network_only_candidate if network_all else None,
        surface_bucket.network_only_candidate if network_surface else None,
        config=config,
    )
    if N_blended is None or abs(N_blended) < config.network_only_signal_floor:
        return _abstain(
            request,
            reason=AbstainReason.INSUFFICIENT_MATCHUP_SUPPORT,
            all_bucket=all_bucket,
            surface_bucket=surface_bucket,
            eta=eta,
            N_blended=N_blended or Decimal("0"),
            config=config,
        )
    probability = logistic_probability(N_blended, config)
    selected_probability = _selected_probability(
        probability,
        score=N_blended,
        config=config,
    )
    if selected_probability < config.activation_probability_floor:
        return _abstain(
            request,
            reason=AbstainReason.INSUFFICIENT_MATCHUP_SUPPORT,
            all_bucket=all_bucket,
            surface_bucket=surface_bucket,
            eta=eta,
            N_blended=N_blended,
            final_score=N_blended,
            activation_mode="NETWORK_ONLY",
            config=config,
        )
    diagnostics = _build_diagnostics(
        all_bucket,
        surface_bucket,
        eta=eta,
        N_blended=N_blended,
        final_score=N_blended,
        activation_mode="NETWORK_ONLY",
    )
    selected_side = 1 if N_blended > 0 else 2
    quantized_selected = _quantize_probability(selected_probability, config)
    quantized_other = _quantize_probability(Decimal("1") - quantized_selected, config)
    if selected_side == 1:
        player_1_probability = quantized_selected
        player_2_probability = quantized_other
    else:
        player_1_probability = quantized_other
        player_2_probability = quantized_selected
    if N_blended == Decimal("0") or player_1_probability == Decimal("0.500000"):
        return _abstain(
            request,
            reason=AbstainReason.WEAK_SIGNAL,
            all_bucket=all_bucket,
            surface_bucket=surface_bucket,
            eta=eta,
            N_blended=N_blended,
            final_score=N_blended,
            activation_mode="NETWORK_ONLY",
            config=config,
        )
    return PredictionResult(
        agent_code=AGENT_CODE,
        prediction_status="PREDICTED",
        selected_side=selected_side,
        selected_probability=selected_probability,
        player_1_probability=player_1_probability,
        player_2_probability=player_2_probability,
        abstain_reason=None,
        model_version=config.model_version,
        feature_version=config.feature_version,
        config_version=config.config_version,
        parameter_schema_version=config.parameter_schema_version,
        as_of=request.as_of,
        diagnostics=diagnostics,
    )
