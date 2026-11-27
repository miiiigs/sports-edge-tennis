"""Chronological Grand Slam best-of-five validation.

The question is narrow: does a format-sensitive adjustment improve Grand Slam
match forecasts when it is fitted only on earlier tournaments and evaluated on
later ones? Six arms are compared:

A. the existing format-insensitive Agent 1 probability;
B. a theoretically justified set-level adjustment (requires a *set-win*
   probability, never a match-win probability silently relabelled);
C. a historical logistic recalibration fitted on earlier Grand Slams;
D. Agent 2's existing best-of-five Markov forecast;
E. a separately validated Agent 1/Agent 2 specialist logit combination;
F. Manager C as a reference where a probability exists.

Hard rules enforced by construction and by tests:

* a match-winning probability is not a set-winning probability; converting one
  requires an explicit, labelled i.i.d.-sets assumption and is reported as
  assumption-dependent, not as a theoretical result;
* no arbitrary favourite inflation: the set-level transform is the exact
  independent-set identity, is monotone, fixes 0.5 and is symmetric;
* calibration and blending parameters are fitted only on earlier editions;
* every evaluation edition starts strictly after the last training edition;
* an arm's coverage is reported, and paired metrics use only matches where both
  arms produced a probability.

The module never calls a provider, never writes a database and never uses
unlicensed data. ``run_chronological_experiment`` accepts a dataset whose
records explicitly declare their rights status.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Callable, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Probability semantics
# ---------------------------------------------------------------------------

SET_PROBABILITY = "SET_WIN_PROBABILITY"
MATCH_PROBABILITY = "MATCH_WIN_PROBABILITY"
IID_SETS_ASSUMPTION = "IID_SETS_ASSUMPTION"
THEORETICAL = "THEORETICAL_SET_LEVEL"
ASSUMPTION_DEPENDENT = "ASSUMPTION_DEPENDENT"
HISTORICAL_CALIBRATION = "HISTORICAL_CALIBRATION"
MARKOV_MODEL = "MARKOV_MODEL"
SPECIALIST_COMBINATION = "SPECIALIST_COMBINATION"
FORMAT_INSENSITIVE = "FORMAT_INSENSITIVE"
MANAGER_REFERENCE = "MANAGER_REFERENCE"

COMMERCIAL_AUTHORIZED = "COMMERCIAL_AUTHORIZED"


class DataRightsError(RuntimeError):
    """Raised when a dataset cannot be used for a commercial experiment."""


class ProbabilitySemanticsError(ValueError):
    """Raised when a match probability is used as a set probability."""


def best_of_match_probability(p_set: float, best_of: int) -> float:
    """Exact probability of winning a best-of-N match from an i.i.d. set model.

    ``p_set`` is the probability of winning one set. This is the identity used
    by the published best-of-five analysis; it is not a fitted favourite boost.
    """

    p = _unit(p_set, "p_set")
    if best_of == 3:
        return p * p * (3.0 - 2.0 * p)
    if best_of == 5:
        return p**3 * (10.0 - 15.0 * p + 6.0 * p * p)
    if best_of == 1:
        return p
    raise ValueError("best_of must be 1, 3 or 5")


def implied_set_probability(p_match: float, best_of: int) -> float:
    """Invert the independent-set identity (assumption-dependent)."""

    target = _unit(p_match, "p_match")
    low, high = 0.0, 1.0
    for _ in range(80):
        middle = (low + high) / 2.0
        if best_of_match_probability(middle, best_of) < target:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def theoretical_format_adjustment(
    probability: float,
    *,
    source_semantics: str,
    source_best_of: int,
    target_best_of: int,
    allow_iid_assumption: bool = False,
) -> tuple[float, str]:
    """Return the format-adjusted probability and its evidence label.

    A set-level probability is transformed exactly. A match-level probability is
    **rejected** unless the caller explicitly opts into the i.i.d.-sets
    assumption; the result is then labelled assumption-dependent.
    """

    if source_semantics == SET_PROBABILITY:
        return (
            best_of_match_probability(probability, target_best_of),
            THEORETICAL,
        )
    if source_semantics != MATCH_PROBABILITY:
        raise ProbabilitySemanticsError(
            f"unknown probability semantics {source_semantics!r}"
        )
    if not allow_iid_assumption:
        raise ProbabilitySemanticsError(
            "a match-winning probability is not a set-winning probability; "
            "set allow_iid_assumption=True to make the assumption explicit"
        )
    implied_set = implied_set_probability(probability, source_best_of)
    return (
        best_of_match_probability(implied_set, target_best_of),
        ASSUMPTION_DEPENDENT,
    )


def _unit(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be finite and within [0, 1]")
    return number


# ---------------------------------------------------------------------------
# Dataset contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SlamMatch:
    """One historical Grand Slam match with pre-match arm probabilities."""

    match_id: str
    slam: str
    season: int
    edition_start: date
    round_code: str
    draw_stage: str  # MAIN_DRAW or QUALIFYING
    surface: str
    best_of: int
    winner_is_player_1: bool
    a1_probability: float
    a2_bo5_probability: float | None = None
    a2_bo3_probability: float | None = None
    set_win_probability: float | None = None
    manager_c_probability: float | None = None
    rating_difference: float | None = None
    matches_last_365: int | None = None
    days_since_last_match: int | None = None
    data_rights: str = "UNKNOWN"
    source: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if self.draw_stage not in ("MAIN_DRAW", "QUALIFYING"):
            raise ValueError("draw_stage must be MAIN_DRAW or QUALIFYING")
        if self.best_of not in (3, 5):
            raise ValueError("best_of must be 3 or 5")
        if self.draw_stage == "QUALIFYING" and self.best_of != 3:
            raise ValueError("Grand Slam qualifying is best-of-three")
        _unit(self.a1_probability, "a1_probability")
        for name in (
            "a2_bo5_probability",
            "a2_bo3_probability",
            "set_win_probability",
            "manager_c_probability",
        ):
            value = getattr(self, name)
            if value is not None:
                _unit(value, name)
        if self.season < 1968:
            raise ValueError("open-era Grand Slam season expected")

    @property
    def edition_id(self) -> tuple[date, str]:
        return (self.edition_start, self.slam)

    @property
    def label(self) -> int:
        return 1 if self.winner_is_player_1 else 0

    @property
    def round_group(self) -> str:
        if self.draw_stage == "QUALIFYING":
            return "QUALIFYING"
        if self.round_code in ("R128", "R64", "R32"):
            return "EARLY_ROUNDS"
        if self.round_code in ("R16", "QF", "SF", "F"):
            return "LATER_ROUNDS"
        return "OTHER"


def validate_dataset(
    matches: Sequence[SlamMatch],
    *,
    require_commercial_rights: bool = False,
) -> None:
    if not matches:
        raise ValueError("dataset is empty")
    ids = [match.match_id for match in matches]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate match_id")
    if require_commercial_rights:
        rejected = sorted(
            {
                match.source
                for match in matches
                if match.data_rights != COMMERCIAL_AUTHORIZED
            }
        )
        if rejected:
            raise DataRightsError(
                "dataset contains non-commercial sources: " + ", ".join(rejected)
            )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _clip(probability: float) -> float:
    return min(1.0 - 1e-15, max(1e-15, probability))


def brier(probabilities: Sequence[float], labels: Sequence[int]) -> float:
    if not probabilities:
        raise ValueError("no observations")
    return sum(
        (probability - label) ** 2
        for probability, label in zip(probabilities, labels)
    ) / len(probabilities)


def log_loss(probabilities: Sequence[float], labels: Sequence[int]) -> float:
    if not probabilities:
        raise ValueError("no observations")
    total = 0.0
    for probability, label in zip(probabilities, labels):
        value = _clip(probability)
        total -= label * math.log(value) + (1 - label) * math.log(1.0 - value)
    return total / len(probabilities)


def accuracy(probabilities: Sequence[float], labels: Sequence[int]) -> float:
    if not probabilities:
        raise ValueError("no observations")
    return sum(
        (1 if probability >= 0.5 else 0) == label
        for probability, label in zip(probabilities, labels)
    ) / len(probabilities)


def expected_calibration_error(
    probabilities: Sequence[float],
    labels: Sequence[int],
    *,
    bins: int = 10,
) -> float:
    if not probabilities:
        raise ValueError("no observations")
    buckets: dict[int, list[tuple[float, int]]] = {}
    for probability, label in zip(probabilities, labels):
        index = min(bins - 1, int(probability * bins))
        buckets.setdefault(index, []).append((probability, label))
    total = 0.0
    for values in buckets.values():
        mean_probability = sum(p for p, _ in values) / len(values)
        mean_label = sum(y for _, y in values) / len(values)
        total += abs(mean_probability - mean_label) * len(values)
    return total / len(probabilities)


def reliability_table(
    probabilities: Sequence[float],
    labels: Sequence[int],
    *,
    bins: int = 10,
) -> list[dict[str, object]]:
    buckets: dict[int, list[tuple[float, int]]] = {}
    for probability, label in zip(probabilities, labels):
        index = min(bins - 1, int(probability * bins))
        buckets.setdefault(index, []).append((probability, label))
    rows = []
    for index in sorted(buckets):
        values = buckets[index]
        rows.append(
            {
                "bin": index,
                "lower": index / bins,
                "upper": (index + 1) / bins,
                "n": len(values),
                "mean_probability": sum(p for p, _ in values) / len(values),
                "observed_rate": sum(y for _, y in values) / len(values),
            }
        )
    return rows


def metric_bundle(
    probabilities: Sequence[float],
    labels: Sequence[int],
    *,
    total_population: int | None = None,
) -> dict[str, object]:
    return {
        "n": len(probabilities),
        "coverage": (
            len(probabilities) / total_population
            if total_population
            else None
        ),
        "accuracy": accuracy(probabilities, labels),
        "brier": brier(probabilities, labels),
        "log_loss": log_loss(probabilities, labels),
        "ece_10": expected_calibration_error(probabilities, labels),
        "reliability": reliability_table(probabilities, labels),
    }


def paired_bootstrap(
    rows: Sequence[Mapping[str, object]],
    *,
    arm: str,
    baseline: str = "A",
    iterations: int = 1000,
    seed: int = 20261127,
) -> dict[str, object]:
    """Cluster-bootstrap paired differences, resampling editions.

    Resampling matches independently would understate uncertainty because
    matches inside one tournament share conditions. Editions are the cluster.
    """

    paired = [
        row
        for row in rows
        if row.get(arm) is not None and row.get(baseline) is not None
    ]
    if not paired:
        return {"n": 0, "iterations": 0, "brier_delta": None, "log_loss_delta": None}
    clusters: dict[tuple[date, str], list[Mapping[str, object]]] = {}
    for row in paired:
        clusters.setdefault(row["edition_id"], []).append(row)
    keys = sorted(clusters)
    generator = random.Random(seed)
    brier_deltas: list[float] = []
    logloss_deltas: list[float] = []
    for _ in range(iterations):
        sample: list[Mapping[str, object]] = []
        for _ in range(len(keys)):
            sample.extend(clusters[keys[generator.randrange(len(keys))]])
        arm_p = [float(row[arm]) for row in sample]
        base_p = [float(row[baseline]) for row in sample]
        labels = [int(row["label"]) for row in sample]
        brier_deltas.append(brier(arm_p, labels) - brier(base_p, labels))
        logloss_deltas.append(log_loss(arm_p, labels) - log_loss(base_p, labels))
    return {
        "n": len(paired),
        "clusters": len(keys),
        "iterations": iterations,
        "brier_delta": _interval(brier_deltas),
        "log_loss_delta": _interval(logloss_deltas),
        "delta_convention": "arm_minus_baseline_negative_is_better",
    }


def _interval(values: Sequence[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "mean": sum(ordered) / len(ordered),
        "lower_95": ordered[int(0.025 * (len(ordered) - 1))],
        "upper_95": ordered[int(0.975 * (len(ordered) - 1))],
    }


# ---------------------------------------------------------------------------
# Fitting: logistic calibration and specialist blending
# ---------------------------------------------------------------------------


def _logit(probability: float) -> float:
    value = _clip(probability)
    return math.log(value / (1.0 - value))


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponent = math.exp(-value)
        return 1.0 / (1.0 + exponent)
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def fit_logistic_calibration(
    probabilities: Sequence[float],
    labels: Sequence[int],
    *,
    l2: float = 1e-4,
    iterations: int = 60,
) -> tuple[float, float]:
    """Fit ``sigmoid(a * logit(p) + b)`` by Newton's method.

    The intercept is never hand-set and cannot encode an arbitrary favourite
    boost. Fitted only on the training editions by the caller.
    """

    slope, intercept = 1.0, 0.0
    for _ in range(iterations):
        gradient_slope = 0.0
        gradient_intercept = 0.0
        hessian_ss = 0.0
        hessian_si = 0.0
        hessian_ii = 0.0
        for probability, label in zip(probabilities, labels):
            feature = _logit(probability)
            predicted = _sigmoid(slope * feature + intercept)
            residual = predicted - label
            weight = max(predicted * (1.0 - predicted), 1e-9)
            gradient_slope += residual * feature
            gradient_intercept += residual
            hessian_ss += weight * feature * feature
            hessian_si += weight * feature
            hessian_ii += weight
        gradient_slope += l2 * slope
        hessian_ss += l2
        determinant = hessian_ss * hessian_ii - hessian_si * hessian_si
        if determinant <= 0:
            break
        step_slope = (
            hessian_ii * gradient_slope - hessian_si * gradient_intercept
        ) / determinant
        step_intercept = (
            -hessian_si * gradient_slope + hessian_ss * gradient_intercept
        ) / determinant
        slope -= step_slope
        intercept -= step_intercept
        if max(abs(step_slope), abs(step_intercept)) < 1e-10:
            break
    return float(slope), float(intercept)


def apply_calibration(
    probability: float, parameters: tuple[float, float]
) -> float:
    slope, intercept = parameters
    return _sigmoid(slope * _logit(probability) + intercept)


def fit_blend_weight(
    a_probabilities: Sequence[float],
    d_probabilities: Sequence[float],
    labels: Sequence[int],
    *,
    step: float = 0.05,
) -> float:
    """Choose a logit-space blend weight on training data only.

    The weight is constrained to ``[0, 1]``; there is no free additive term, so
    the combination cannot inflate the favourite beyond the inputs.
    """

    best_weight = 0.0
    best_loss = float("inf")
    steps = int(round(1.0 / step))
    for index in range(steps + 1):
        weight = index * step
        blended = [
            _sigmoid(
                (1.0 - weight) * _logit(a) + weight * _logit(d)
            )
            for a, d in zip(a_probabilities, d_probabilities)
        ]
        loss = log_loss(blended, labels)
        if loss < best_loss:
            best_loss = loss
            best_weight = weight
    return best_weight


# ---------------------------------------------------------------------------
# Arms
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmDefinition:
    code: str
    name: str
    evidence_class: str
    fitted_on_history: bool


ARM_DEFINITIONS: tuple[ArmDefinition, ...] = (
    ArmDefinition("A", "Agent 1 format-insensitive", FORMAT_INSENSITIVE, False),
    ArmDefinition("B", "Set-level theoretical adjustment", THEORETICAL, False),
    ArmDefinition("C", "Historical calibration", HISTORICAL_CALIBRATION, True),
    ArmDefinition("D", "Agent 2 BO5 Markov", MARKOV_MODEL, False),
    ArmDefinition("E", "A1/A2 specialist combination", SPECIALIST_COMBINATION, True),
    ArmDefinition("F", "Manager C reference", MANAGER_REFERENCE, False),
)


def arm_probability(match: SlamMatch, arm: str) -> float | None:
    if arm == "A":
        return match.a1_probability
    if arm == "B":
        if match.set_win_probability is None:
            return None
        return best_of_match_probability(match.set_win_probability, match.best_of)
    if arm == "D":
        if match.best_of == 5:
            return match.a2_bo5_probability
        return match.a2_bo3_probability
    if arm == "F":
        return match.manager_c_probability
    if arm in ("C", "E"):
        return None  # filled per fold
    raise ValueError(f"unknown arm {arm!r}")


# ---------------------------------------------------------------------------
# Chronological experiment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExperimentConfig:
    min_training_editions: int = 4
    bootstrap_iterations: int = 1000
    bootstrap_seed: int = 20261127
    require_commercial_rights: bool = False
    include_qualifying: bool = True


@dataclass(frozen=True)
class FoldResult:
    test_edition: tuple[date, str]
    training_editions: tuple[tuple[date, str], ...]
    training_matches: int
    test_matches: int
    calibration: tuple[float, float] | None
    blend_weight: float | None
    arms: dict[str, dict[str, object]]
    paired: dict[str, dict[str, object]]

    def to_payload(self) -> dict[str, object]:
        return {
            "test_edition": [self.test_edition[0].isoformat(), self.test_edition[1]],
            "training_editions": [
                [edition[0].isoformat(), edition[1]]
                for edition in self.training_editions
            ],
            "training_matches": self.training_matches,
            "test_matches": self.test_matches,
            "calibration": (
                {"slope": self.calibration[0], "intercept": self.calibration[1]}
                if self.calibration
                else None
            ),
            "blend_weight": self.blend_weight,
            "arms": self.arms,
            "paired_vs_A": self.paired,
        }


def run_chronological_experiment(
    matches: Sequence[SlamMatch],
    *,
    config: ExperimentConfig = ExperimentConfig(),
) -> dict[str, object]:
    """Fit on earlier editions and evaluate on each subsequent edition."""

    validate_dataset(
        matches, require_commercial_rights=config.require_commercial_rights
    )
    population = [
        match
        for match in matches
        if config.include_qualifying or match.draw_stage == "MAIN_DRAW"
    ]
    editions = sorted({match.edition_id for match in population})
    if len(editions) <= config.min_training_editions:
        raise ValueError(
            f"need more than {config.min_training_editions} editions, "
            f"have {len(editions)}"
        )

    folds: list[FoldResult] = []
    evaluation_rows: list[dict[str, object]] = []
    for test_edition in editions[config.min_training_editions:]:
        training_editions = tuple(
            edition for edition in editions if edition[0] < test_edition[0]
        )
        if not training_editions:
            raise AssertionError("chronological training set is empty")
        if max(edition[0] for edition in training_editions) >= test_edition[0]:
            raise AssertionError("future edition leaked into training")
        train = [
            match for match in population if match.edition_id in training_editions
        ]
        test = [match for match in population if match.edition_id == test_edition]
        if not test:
            continue

        calibration = fit_logistic_calibration(
            [match.a1_probability for match in train],
            [match.label for match in train],
        )
        d_pairs = [
            (match, match.a2_bo5_probability)
            for match in train
            if match.a2_bo5_probability is not None
        ]
        blend_weight = (
            fit_blend_weight(
                [match.a1_probability for match, _ in d_pairs],
                [probability for _, probability in d_pairs],
                [match.label for match, _ in d_pairs],
            )
            if d_pairs
            else None
        )

        arm_values: dict[str, list[float]] = {arm.code: [] for arm in ARM_DEFINITIONS}
        for match in test:
            values = {
                "A": match.a1_probability,
                "B": arm_probability(match, "B"),
                "C": apply_calibration(match.a1_probability, calibration),
                "D": arm_probability(match, "D"),
                "F": match.manager_c_probability,
            }
            values["E"] = (
                _sigmoid(
                    (1.0 - blend_weight) * _logit(match.a1_probability)
                    + blend_weight * _logit(match.a2_bo5_probability)
                )
                if blend_weight is not None
                and match.a2_bo5_probability is not None
                and match.best_of == 5
                else None
            )
            row = {
                "match_id": match.match_id,
                "edition_id": match.edition_id,
                "slam": match.slam,
                "season": match.season,
                "draw_stage": match.draw_stage,
                "round_group": match.round_group,
                "surface": match.surface,
                "best_of": match.best_of,
                "label": match.label,
                "rating_difference": match.rating_difference,
                "matches_last_365": match.matches_last_365,
                "days_since_last_match": match.days_since_last_match,
            }
            for arm, value in values.items():
                row[arm] = value
            evaluation_rows.append(row)
            for arm, value in values.items():
                if value is not None:
                    arm_values[arm].append(value)

        arms_payload = {}
        for arm in ARM_DEFINITIONS:
            arm_rows = [
                row
                for row in evaluation_rows
                if row["edition_id"] == test_edition and row.get(arm.code) is not None
            ]
            if arm_rows:
                arms_payload[arm.code] = {
                    "name": arm.name,
                    "evidence_class": arm.evidence_class,
                    **_metrics_for_rows(arm_rows, arm.code, len(test)),
                }
            else:
                arms_payload[arm.code] = {
                    "name": arm.name,
                    "evidence_class": arm.evidence_class,
                    "n": 0,
                    "coverage": 0.0,
                    "accuracy": None,
                    "brier": None,
                    "log_loss": None,
                    "ece_10": None,
                    "reliability": [],
                }

        fold_rows = [
            row for row in evaluation_rows if row["edition_id"] == test_edition
        ]
        paired = {
            arm.code: paired_bootstrap(
                fold_rows,
                arm=arm.code,
                baseline="A",
                iterations=config.bootstrap_iterations,
                seed=config.bootstrap_seed,
            )
            for arm in ARM_DEFINITIONS
            if arm.code != "A"
        }
        folds.append(
            FoldResult(
                test_edition=test_edition,
                training_editions=training_editions,
                training_matches=len(train),
                test_matches=len(test),
                calibration=calibration,
                blend_weight=blend_weight,
                arms=arms_payload,
                paired=paired,
            )
        )

    if not evaluation_rows:
        raise ValueError("no evaluation rows produced")

    overall = {}
    for arm in ARM_DEFINITIONS:
        arm_rows = [
            row for row in evaluation_rows if row.get(arm.code) is not None
        ]
        if arm_rows:
            overall[arm.code] = {
                "name": arm.name,
                "evidence_class": arm.evidence_class,
                **_metrics_for_rows(arm_rows, arm.code, len(evaluation_rows)),
            }
        else:
            overall[arm.code] = {
                "name": arm.name,
                "evidence_class": arm.evidence_class,
                "n": 0,
                "coverage": 0.0,
                "accuracy": None,
                "brier": None,
                "log_loss": None,
                "ece_10": None,
                "reliability": [],
            }
    paired_overall = {
        arm.code: paired_bootstrap(
            evaluation_rows,
            arm=arm.code,
            baseline="A",
            iterations=config.bootstrap_iterations,
            seed=config.bootstrap_seed,
        )
        for arm in ARM_DEFINITIONS
        if arm.code != "A"
    }

    return {
        "version": "GRAND_SLAM_BO5_CHRONOLOGY_20261127_V1",
        "design": "EXPANDING_WINDOW_BY_TOURNAMENT_EDITION",
        "population": len(population),
        "editions": len(editions),
        "evaluation_editions": len(folds),
        "evaluation_matches": len(evaluation_rows),
        "min_training_editions": config.min_training_editions,
        "arms": [
            {
                "code": arm.code,
                "name": arm.name,
                "evidence_class": arm.evidence_class,
                "fitted_on_history": arm.fitted_on_history,
            }
            for arm in ARM_DEFINITIONS
        ],
        "overall": overall,
        "paired_vs_A": paired_overall,
        "folds": [fold.to_payload() for fold in folds],
        "stratified": _stratified(evaluation_rows),
        "guards": {
            "match_probability_never_silently_treated_as_set_probability": True,
            "arbitrary_favourite_inflation": False,
            "training_always_strictly_earlier": True,
            "qualifying_separated": True,
            "main_draw_and_qualifying_best_of_distinct": True,
            "coverage_reported_per_arm": True,
        },
    }


def _metrics_for_rows(
    rows: Sequence[Mapping[str, object]], arm: str, total: int
) -> dict[str, object]:
    probabilities = [float(row[arm]) for row in rows]
    labels = [int(row["label"]) for row in rows]
    return metric_bundle(probabilities, labels, total_population=total)


def _stratified(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    def group(key: Callable[[Mapping[str, object]], str]):
        buckets: dict[str, list[Mapping[str, object]]] = {}
        for row in rows:
            buckets.setdefault(key(row), []).append(row)
        return buckets

    def rating_band(row: Mapping[str, object]) -> str:
        value = row.get("rating_difference")
        if value is None:
            return "UNKNOWN"
        number = float(value)
        if number < -100:
            return "LT_MINUS_100"
        if number < 0:
            return "MINUS_100_TO_0"
        if number < 100:
            return "0_TO_100"
        return "GE_100"

    def experience_band(row: Mapping[str, object]) -> str:
        value = row.get("matches_last_365")
        if value is None:
            return "UNKNOWN"
        number = int(value)
        if number < 20:
            return "LT_20"
        if number < 50:
            return "20_TO_49"
        return "GE_50"

    dimensions = {
        "draw_stage": lambda row: str(row["draw_stage"]),
        "round_group": lambda row: str(row["round_group"]),
        "surface": lambda row: str(row["surface"]),
        "rating_difference": rating_band,
        "experience": experience_band,
    }
    output: dict[str, object] = {}
    for name, key in dimensions.items():
        output[name] = {}
        for value, bucket in group(key).items():
            output[name][value] = {
                arm: {
                    "n": sum(1 for row in bucket if row.get(arm) is not None),
                    "brier": (
                        brier(
                            [float(row[arm]) for row in bucket if row.get(arm) is not None],
                            [int(row["label"]) for row in bucket if row.get(arm) is not None],
                        )
                        if any(row.get(arm) is not None for row in bucket)
                        else None
                    ),
                }
                for arm in ("A", "B", "C", "D", "E", "F")
            }
    return output


# ---------------------------------------------------------------------------
# Synthetic validation with a known data-generating process
# ---------------------------------------------------------------------------


def synthetic_dataset(
    *,
    editions: int = 12,
    matches_per_edition: int = 90,
    seed: int = 20261127,
    a1_compression: float = 0.82,
    slam_effect: float = 0.10,
) -> list[SlamMatch]:
    """Generate a chronological synthetic Grand Slam corpus.

    The data-generating process is an i.i.d. set model with a latent per-match
    set probability. Agent 1 is deliberately format-insensitive and
    under-confident; Agent 2 observes a noisy set-level signal; a set-level
    source exists for a subset of matches. The synthetic labels are sampled from
    the true best-of-five process, so the theoretical arm has a known target.

    This is a validation harness, not empirical evidence.
    """

    generator = random.Random(seed)
    slams = (
        ("Australian Open", "HARD"),
        ("Roland Garros", "CLAY"),
        ("Wimbledon", "GRASS"),
        ("US Open", "HARD"),
    )
    start = date(2016, 1, 18)
    output: list[SlamMatch] = []
    for edition_index in range(editions):
        slam, surface = slams[edition_index % len(slams)]
        year = 2016 + edition_index // len(slams)
        edition_start = date(year, 1, 18) if slam == "Australian Open" else _month_day(
            year, slam
        )
        for index in range(matches_per_edition):
            strength = generator.gauss(0.0, 0.85)
            set_probability = _sigmoid(strength)
            true_bo5 = best_of_match_probability(set_probability, 5)
            label = 1 if generator.random() < true_bo5 else 0
            bo3_equivalent = best_of_match_probability(set_probability, 3)
            # Agent 1: format-insensitive, systematically compressed and noisy.
            a1_logit = (
                a1_compression * _logit(bo3_equivalent)
                + slam_effect
                + generator.gauss(0.0, 0.28)
            )
            a1 = _sigmoid(a1_logit)
            # Agent 2: noisy serve-level view, converted through the BO5 Markov.
            a2_set = _sigmoid(strength + 0.05 + generator.gauss(0.0, 0.20))
            a2_bo5 = best_of_match_probability(a2_set, 5)
            a2_bo3 = best_of_match_probability(a2_set, 3)
            # A set-level source exists for a subset only.
            set_probability_observed = (
                set_probability + generator.gauss(0.0, 0.03)
                if generator.random() < 0.8
                else None
            )
            manager_c = (
                _sigmoid(
                    0.5 * _logit(a1) + 0.5 * _logit(a2_bo5)
                    + generator.gauss(0.0, 0.12)
                )
                if generator.random() < 0.6
                else None
            )
            qualifying = index >= matches_per_edition - 20
            best_of = 3 if qualifying else 5
            round_code = (
                generator.choice(("Q1", "Q2", "Q3"))
                if qualifying
                else generator.choice(("R128", "R64", "R32", "R16", "QF", "SF", "F"))
            )
            output.append(
                SlamMatch(
                    match_id=f"{year}-{slam[:3]}-{index:04d}",
                    slam=slam,
                    season=year,
                    edition_start=edition_start,
                    round_code=round_code,
                    draw_stage="QUALIFYING" if qualifying else "MAIN_DRAW",
                    surface="HARD" if qualifying else surface,
                    best_of=best_of,
                    winner_is_player_1=bool(label),
                    a1_probability=a1,
                    a2_bo5_probability=a2_bo5,
                    a2_bo3_probability=a2_bo3,
                    set_win_probability=set_probability_observed,
                    manager_c_probability=manager_c,
                    rating_difference=strength * 200,
                    matches_last_365=generator.choice((10, 35, 60)),
                    days_since_last_match=generator.choice((7, 30, 120)),
                    data_rights="SYNTHETIC",
                    source="SYNTHETIC_IID_SET_MODEL",
                )
            )
    return output


def _month_day(year: int, slam: str) -> date:
    if slam == "Roland Garros":
        return date(year, 5, 25)
    if slam == "Wimbledon":
        return date(year, 6, 29)
    return date(year, 8, 31)


def synthetic_validation(seed: int = 20261127) -> dict[str, object]:
    dataset = synthetic_dataset(seed=seed)
    result = run_chronological_experiment(
        dataset,
        config=ExperimentConfig(
            min_training_editions=4,
            bootstrap_iterations=400,
            bootstrap_seed=seed,
        ),
    )
    overall = result["overall"]
    checks = {
        "B_beats_A_on_brier": overall["B"]["brier"] < overall["A"]["brier"],
        "C_beats_A_on_brier": overall["C"]["brier"] < overall["A"]["brier"],
        "D_beats_A_on_brier": overall["D"]["brier"] < overall["A"]["brier"],
        "E_beats_A_on_brier": overall["E"]["brier"] < overall["A"]["brier"],
        "B_has_bounded_coverage": overall["B"]["coverage"] < 1.0,
        "A_full_coverage": overall["A"]["coverage"] == 1.0,
        "all_metrics_finite": all(
            math.isfinite(overall[arm][metric])
            for arm in ("A", "B", "C", "D", "E")
            for metric in ("brier", "log_loss", "accuracy", "ece_10")
        ),
        "theoretical_transform_is_not_inflation": all(
            theoretical_format_adjustment(
                p,
                source_semantics=SET_PROBABILITY,
                source_best_of=3,
                target_best_of=5,
            )[0]
            >= p
            for p in (0.5, 0.55, 0.6, 0.7, 0.8)
        ),
    }
    return {
        **result,
        "synthetic_validation": {
            "seed": seed,
            "data_generating_process": "IID_SET_MODEL_WITH_KNOWN_BO5_LABEL",
            "is_empirical_evidence": False,
            "checks": checks,
            "all_checks_passed": all(checks.values()),
        },
    }


def dataset_from_payload(payload: Mapping[str, object]) -> list[SlamMatch]:
    records = payload.get("matches")
    if not isinstance(records, list):
        raise ValueError("dataset payload must contain a matches array")
    output = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("each match must be an object")
        output.append(
            SlamMatch(
                match_id=str(record["match_id"]),
                slam=str(record["slam"]),
                season=int(record["season"]),
                edition_start=date.fromisoformat(str(record["edition_start"])),
                round_code=str(record["round_code"]),
                draw_stage=str(record["draw_stage"]),
                surface=str(record["surface"]),
                best_of=int(record["best_of"]),
                winner_is_player_1=bool(record["winner_is_player_1"]),
                a1_probability=float(record["a1_probability"]),
                a2_bo5_probability=_optional_float(record.get("a2_bo5_probability")),
                a2_bo3_probability=_optional_float(record.get("a2_bo3_probability")),
                set_win_probability=_optional_float(record.get("set_win_probability")),
                manager_c_probability=_optional_float(record.get("manager_c_probability")),
                rating_difference=_optional_float(record.get("rating_difference")),
                matches_last_365=_optional_int(record.get("matches_last_365")),
                days_since_last_match=_optional_int(record.get("days_since_last_match")),
                data_rights=str(record.get("data_rights") or "UNKNOWN"),
                source=str(record.get("source") or "UNKNOWN"),
            )
        )
    return output


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)
