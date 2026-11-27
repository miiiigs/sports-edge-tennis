"""A1 reliability-intelligence research shadow.

Separate from Agent 1's probability and from Agent 3.  Agent 1 keeps its
original probability and selected winner.  This module scores only the
*reliability of the information supporting that already-issued forecast*.

The module has three parts:

1. A frozen feature contract and a deterministic, pre-registered reliability
   score (``A1_RELIABILITY_SHADOW_V1``).
2. An outcome-free prospective capture format and a separate settlement step.
3. Evaluation utilities (risk-coverage, AURC/AUGRC, error AUROC, clustered
   paired differences) and synthetic-only model utilities for R2/R3/R4.

No database access, provider call, model training on the production corpus or
production write occurs here.  R2/R3/R4 fitting is exercised on synthetic data
only until commercial training rights are established.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from statistics import mean
from typing import Any

VERSION = "A1_RELIABILITY_SHADOW_V1"
FEATURE_CONTRACT_VERSION = "A1_RELIABILITY_FEATURES_V1"
PROSPECTIVE_SCHEMA_VERSION = "A1_RELIABILITY_PROSPECTIVE_V1"
SETTLEMENT_SCHEMA_VERSION = "A1_RELIABILITY_SETTLEMENT_V1"

# Pre-registered before the evaluation.  Each risk point is worth 0.5 pp of
# confidence in the selection score.  The coefficient is deliberately smaller
# than one 10-point confidence band and much smaller than the observed marginal
# calibration gap, so reliability can reorder near-neighbours but cannot
# dominate Agent 1's probability.
RELIABILITY_CONFIDENCE_COEFFICIENT = 0.005
MAX_RISK_POINTS = 11.0
TIER_HIGH_MAX_POINTS = 2.0
TIER_MEDIUM_MAX_POINTS = 5.0

OUTCOME_KEYS = frozenset(
    {
        "winner_side",
        "winner",
        "y",
        "correct",
        "correct_corrected",
        "correct_original",
        "result",
        "outcome",
        "outcome_status",
        "score",
        "scoreline",
        "settled_at",
        "result_observed_at",
        "result_as_of",
    }
)

RESOLVED_METHODS = frozenset({"ALIAS", "PERSISTED_BRIDGE", "HISTORICAL_ID"})
IDENTITY_METHODS = RESOLVED_METHODS | {"AMBIGUOUS", "NO_HISTORICAL_CANDIDATE", "UNKNOWN"}
A2_STATUSES = frozenset({"PRESENT", "MISSING"})
SURFACE_TRANSITION_STATES = frozenset(
    {
        "CONTINUING",
        "FIRST_ON_NEW_SURFACE",
        "SECOND_ON_SURFACE",
        "THIRD_ON_SURFACE",
        "NO_PRIOR_OBSERVED_MATCH",
        "UNKNOWN_SURFACE",
        "UNKNOWN",
    }
)


class ReliabilityValidationError(ValueError):
    """Raised when a feature, capture or settlement is structurally invalid."""


# ---------------------------------------------------------------------------
# Frozen feature contract
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReliabilityFeatureSpec:
    name: str
    dtype: str
    required: bool
    pit_rule: str
    missing_policy: str


FEATURE_CONTRACT: tuple[ReliabilityFeatureSpec, ...] = (
    ReliabilityFeatureSpec("prediction_id", "int", True, "prediction row identity", "reject"),
    ReliabilityFeatureSpec("match_id", "int", True, "prediction row identity", "reject"),
    ReliabilityFeatureSpec("prediction_as_of", "iso8601", True, "original issue timestamp", "reject"),
    ReliabilityFeatureSpec("match_start_at", "iso8601", True, "scheduled start; capture must precede it", "reject"),
    ReliabilityFeatureSpec("feature_as_of", "iso8601", True, "must be <= prediction_as_of", "reject"),
    ReliabilityFeatureSpec("a1_probability_p1", "float", True, "original Agent 1 probability", "reject"),
    ReliabilityFeatureSpec("a1_selected_side", "int", True, "original selected side", "reject"),
    ReliabilityFeatureSpec("effective_rating_gap_abs", "float", True, "PIT effective ratings", "reject"),
    ReliabilityFeatureSpec("surface_rating_gap_abs", "float", True, "PIT surface ratings", "neutral 0.5 point if unavailable"),
    ReliabilityFeatureSpec("surface_rating_missing", "bool", True, "PIT surface ratings", "reject"),
    ReliabilityFeatureSpec("rating_divergence_favourite", "float", True, "PIT overall vs surface", "reject"),
    ReliabilityFeatureSpec("favourite_overall_matches", "int", True, "PIT prior matches", "reject"),
    ReliabilityFeatureSpec("underdog_overall_matches", "int", True, "PIT prior matches", "reject"),
    ReliabilityFeatureSpec("min_overall_matches", "int", True, "PIT prior matches", "reject"),
    ReliabilityFeatureSpec("favourite_surface_matches", "int", True, "PIT prior surface matches", "reject"),
    ReliabilityFeatureSpec("min_surface_matches", "int", True, "PIT prior surface matches", "reject"),
    ReliabilityFeatureSpec("inactivity_favourite_days", "float", False, "PIT last visible match", "neutral 0.5 point"),
    ReliabilityFeatureSpec("inactivity_underdog_days", "float", False, "PIT last visible match", "neutral 0.5 point"),
    ReliabilityFeatureSpec("identity_method_favourite", "category", True, "as-of resolution", "reject"),
    ReliabilityFeatureSpec("identity_candidate_count_favourite", "int", True, "as-of resolution", "reject"),
    ReliabilityFeatureSpec("identity_any_unresolved", "bool", True, "as-of resolution", "reject"),
    ReliabilityFeatureSpec("identity_any_bridged", "bool", True, "as-of resolution", "reject"),
    ReliabilityFeatureSpec("recovered_matches_favourite", "int", True, "PIT corrected minus original", "reject"),
    ReliabilityFeatureSpec("forecast_probability_delta_abs", "float", True, "original vs alternative replay", "reject"),
    ReliabilityFeatureSpec("forecast_side_changed", "bool", True, "original vs alternative replay", "reject"),
    ReliabilityFeatureSpec("surface_transition_state", "category", True, "PIT prior observed surfaces", "neutral 0.5 point"),
    ReliabilityFeatureSpec("tour", "category", True, "fixture context", "reject"),
    ReliabilityFeatureSpec("surface", "category", True, "fixture context", "reject"),
    ReliabilityFeatureSpec("round", "category", True, "fixture context", "reject"),
    ReliabilityFeatureSpec("qualifying", "bool", True, "fixture context", "reject"),
    ReliabilityFeatureSpec("a2_status", "category", True, "specialist availability", "R1 ignores; R4 requires PRESENT"),
    ReliabilityFeatureSpec("a2_selected_side", "int", False, "PIT Agent 2 selected side", "R4 requires PRESENT"),
    ReliabilityFeatureSpec("a2_probability_favourite", "float", False, "PIT Agent 2 probability", "R4 requires PRESENT"),
    ReliabilityFeatureSpec("a2_agrees", "bool", False, "PIT Agent 2 vs Agent 1 side", "R4 requires PRESENT"),
)


def feature_contract() -> dict[str, Any]:
    return {
        "version": FEATURE_CONTRACT_VERSION,
        "features": [asdict(spec) for spec in FEATURE_CONTRACT],
        "outcome_keys_rejected": sorted(OUTCOME_KEYS),
    }


@dataclass(frozen=True)
class ReliabilityFeatures:
    prediction_id: int
    match_id: int
    prediction_as_of: str
    match_start_at: str
    feature_as_of: str
    a1_probability_p1: float
    a1_selected_side: int
    effective_rating_gap_abs: float
    surface_rating_gap_abs: float
    surface_rating_missing: bool
    rating_divergence_favourite: float
    favourite_overall_matches: int
    underdog_overall_matches: int
    min_overall_matches: int
    favourite_surface_matches: int
    min_surface_matches: int
    identity_method_favourite: str
    identity_candidate_count_favourite: int
    identity_any_unresolved: bool
    identity_any_bridged: bool
    recovered_matches_favourite: int
    forecast_probability_delta_abs: float
    forecast_side_changed: bool
    surface_transition_state: str
    tour: str
    surface: str
    round: str
    qualifying: bool
    a2_status: str = "MISSING"
    inactivity_favourite_days: float | None = None
    inactivity_underdog_days: float | None = None
    a2_selected_side: int | None = None
    a2_probability_favourite: float | None = None
    a2_agrees: bool | None = None

    def validate(self) -> None:
        _validate_features(self)


def _parse_time(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ReliabilityValidationError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ReliabilityValidationError(f"{field_name} must include a timezone")
    return parsed


def _require_finite(value: float, *, field_name: str, low: float, high: float) -> None:
    if not math.isfinite(value) or value < low or value > high:
        raise ReliabilityValidationError(f"{field_name} must be finite in [{low}, {high}]")


def _validate_features(features: ReliabilityFeatures) -> None:
    _require_finite(features.a1_probability_p1, field_name="a1_probability_p1", low=0.0, high=1.0)
    expected_side = 1 if features.a1_probability_p1 >= 0.5 else 2
    if features.a1_selected_side != expected_side:
        raise ReliabilityValidationError("a1_selected_side must match the original probability convention")
    if _parse_time(features.feature_as_of, field_name="feature_as_of") > _parse_time(
        features.prediction_as_of, field_name="prediction_as_of"
    ):
        raise ReliabilityValidationError("feature_as_of must not be later than prediction_as_of")
    if _parse_time(features.prediction_as_of, field_name="prediction_as_of") >= _parse_time(
        features.match_start_at, field_name="match_start_at"
    ):
        raise ReliabilityValidationError("prediction_as_of must precede match_start_at")
    for name in (
        "favourite_overall_matches",
        "underdog_overall_matches",
        "min_overall_matches",
        "favourite_surface_matches",
        "min_surface_matches",
        "identity_candidate_count_favourite",
        "recovered_matches_favourite",
    ):
        value = getattr(features, name)
        if not isinstance(value, int) or value < 0:
            raise ReliabilityValidationError(f"{name} must be a non-negative integer")
    for name in (
        "effective_rating_gap_abs",
        "surface_rating_gap_abs",
        "rating_divergence_favourite",
        "forecast_probability_delta_abs",
    ):
        value = getattr(features, name)
        if not math.isfinite(value) or value < 0:
            raise ReliabilityValidationError(f"{name} must be finite and non-negative")
    if features.identity_method_favourite not in IDENTITY_METHODS:
        raise ReliabilityValidationError("unsupported identity method")
    if features.surface_transition_state not in SURFACE_TRANSITION_STATES:
        raise ReliabilityValidationError("unsupported surface transition state")
    if features.tour not in ("ATP_MAIN", "CHALLENGER"):
        raise ReliabilityValidationError("unsupported tour")
    if features.surface not in ("HARD", "CLAY", "GRASS", "CARPET", "UNKNOWN"):
        raise ReliabilityValidationError("unsupported surface")
    if features.a2_status not in A2_STATUSES:
        raise ReliabilityValidationError("unsupported a2_status")
    if features.a2_status == "PRESENT":
        if features.a2_selected_side not in (1, 2):
            raise ReliabilityValidationError("a2_selected_side is required when a2_status=PRESENT")
        if features.a2_probability_favourite is None or not 0.5 <= features.a2_probability_favourite <= 1.0:
            raise ReliabilityValidationError("a2_probability_favourite is required when a2_status=PRESENT")
        if features.a2_agrees is None:
            raise ReliabilityValidationError("a2_agrees is required when a2_status=PRESENT")
    else:
        if any(
            value is not None
            for value in (
                features.a2_selected_side,
                features.a2_probability_favourite,
                features.a2_agrees,
            )
        ):
            raise ReliabilityValidationError("A2 fields must be absent when a2_status=MISSING")
    for name in ("inactivity_favourite_days", "inactivity_underdog_days"):
        value = getattr(features, name)
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ReliabilityValidationError(f"{name} must be finite and non-negative when present")


# ---------------------------------------------------------------------------
# Deterministic reliability score
# ---------------------------------------------------------------------------
def _favourite_history_points(matches: int) -> int:
    if matches <= 4:
        return 3
    if matches <= 9:
        return 2
    if matches <= 19:
        return 1
    return 0


def risk_breakdown(features: ReliabilityFeatures) -> dict[str, Any]:
    """Pre-registered risk points.  Missing context contributes a neutral 0.5."""
    _validate_features(features)
    contributions: list[dict[str, Any]] = []
    missing: list[str] = []

    def add(name: str, points: float, reason: str) -> None:
        contributions.append({"indicator": name, "points": points, "reason": reason})

    favourite_points = _favourite_history_points(features.favourite_overall_matches)
    add(
        "favourite_history",
        float(favourite_points),
        f"favourite observed matches={features.favourite_overall_matches}",
    )
    surface_points = 1.0 if features.min_surface_matches < 5 else 0.0
    add(
        "surface_experience",
        surface_points,
        f"min surface matches={features.min_surface_matches}",
    )
    identity_points = 2.0 if features.identity_any_unresolved else (
        1.0 if features.identity_candidate_count_favourite > 1 else 0.0
    )
    add(
        "identity_resolution",
        identity_points,
        (
            f"method={features.identity_method_favourite}; "
            f"candidates={features.identity_candidate_count_favourite}; "
            f"unresolved={features.identity_any_unresolved}"
        ),
    )
    instability_points = 2.0 if (
        features.forecast_side_changed or features.forecast_probability_delta_abs >= 0.10
    ) else 0.0
    add(
        "forecast_stability",
        instability_points,
        (
            f"side_changed={features.forecast_side_changed}; "
            f"abs_probability_delta={features.forecast_probability_delta_abs:.4f}"
        ),
    )
    divergence_points = 0.0
    if features.surface_rating_missing:
        missing.append("surface_rating")
        divergence_points = 0.5
    elif features.rating_divergence_favourite >= 100.0:
        divergence_points = 1.0
    add(
        "overall_surface_divergence",
        divergence_points,
        (
            f"missing={features.surface_rating_missing}; "
            f"favourite overall/surface divergence={features.rating_divergence_favourite:.1f}"
        ),
    )
    narrow_gap_points = 1.0 if features.effective_rating_gap_abs < 50.0 else 0.0
    add(
        "narrow_rating_gap",
        narrow_gap_points,
        f"abs effective rating gap={features.effective_rating_gap_abs:.1f}",
    )
    inactivity_values = (
        features.inactivity_favourite_days,
        features.inactivity_underdog_days,
    )
    if any(value is None for value in inactivity_values):
        missing.append("inactivity")
        add("inactivity_missing", 0.5, "at least one player inactivity is unavailable")
    else:
        add("inactivity_missing", 0.0, "both inactivity values present")
    if features.surface == "UNKNOWN":
        missing.append("surface")
        add("surface_context_missing", 0.5, "surface is unknown")
    else:
        add("surface_context_missing", 0.0, f"surface={features.surface}")

    total = sum(item["points"] for item in contributions)
    total = min(total, MAX_RISK_POINTS)
    score = max(0.0, min(1.0, 1.0 - total / MAX_RISK_POINTS))
    if total <= TIER_HIGH_MAX_POINTS:
        tier = "HIGH"
    elif total <= TIER_MEDIUM_MAX_POINTS:
        tier = "MEDIUM"
    else:
        tier = "LOW"
    selection_score = max(
        0.5,
        min(
            1.0,
            max(features.a1_probability_p1, 1.0 - features.a1_probability_p1)
            - RELIABILITY_CONFIDENCE_COEFFICIENT * total,
        ),
    )
    return {
        "version": VERSION,
        "risk_points": total,
        "max_risk_points": MAX_RISK_POINTS,
        "reliability_score": score,
        "reliability_tier": tier,
        "selection_score": selection_score,
        "contributions": contributions,
        "missing_features": missing,
        "a1_probability_p1": features.a1_probability_p1,
        "a1_selected_side": features.a1_selected_side,
    }


@dataclass(frozen=True)
class ReliabilityAssessment:
    version: str
    risk_points: float
    reliability_score: float
    reliability_tier: str
    selection_score: float
    contributions: tuple[dict[str, Any], ...]
    missing_features: tuple[str, ...]
    a1_probability_p1: float
    a1_selected_side: int
    feature_snapshot_sha256: str


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_payload(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def feature_snapshot(features: ReliabilityFeatures) -> dict[str, Any]:
    _validate_features(features)
    return asdict(features)


def score_reliability(features: ReliabilityFeatures) -> ReliabilityAssessment:
    breakdown = risk_breakdown(features)
    snapshot = feature_snapshot(features)
    return ReliabilityAssessment(
        version=VERSION,
        risk_points=breakdown["risk_points"],
        reliability_score=breakdown["reliability_score"],
        reliability_tier=breakdown["reliability_tier"],
        selection_score=breakdown["selection_score"],
        contributions=tuple(breakdown["contributions"]),
        missing_features=tuple(breakdown["missing_features"]),
        a1_probability_p1=features.a1_probability_p1,
        a1_selected_side=features.a1_selected_side,
        feature_snapshot_sha256=sha256_payload(snapshot),
    )


# ---------------------------------------------------------------------------
# Outcome-free prospective capture and separate settlement
# ---------------------------------------------------------------------------
def _assert_no_outcome_keys(payload: Mapping[str, Any]) -> None:
    present = OUTCOME_KEYS.intersection(payload)
    if present:
        raise ReliabilityValidationError(f"outcome fields are not allowed: {sorted(present)}")


def build_capture(
    features: ReliabilityFeatures,
    *,
    captured_at: str,
) -> dict[str, Any]:
    """Build an outcome-free prospective reliability record."""
    assessment = score_reliability(features)
    captured = _parse_time(captured_at, field_name="captured_at")
    if captured < _parse_time(features.prediction_as_of, field_name="prediction_as_of"):
        raise ReliabilityValidationError("captured_at must not precede prediction_as_of")
    if captured >= _parse_time(features.match_start_at, field_name="match_start_at"):
        raise ReliabilityValidationError("captured_at must precede match_start_at")
    record = {
        "record_type": "a1_reliability_capture",
        "schema_version": PROSPECTIVE_SCHEMA_VERSION,
        "reliability_version": VERSION,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "prediction_id": features.prediction_id,
        "match_id": features.match_id,
        "prediction_as_of": features.prediction_as_of,
        "captured_at": captured_at,
        "a1_probability_p1": features.a1_probability_p1,
        "a1_selected_side": features.a1_selected_side,
        "risk_points": assessment.risk_points,
        "reliability_score": assessment.reliability_score,
        "reliability_tier": assessment.reliability_tier,
        "selection_score": assessment.selection_score,
        "contributions": list(assessment.contributions),
        "missing_features": list(assessment.missing_features),
        "feature_snapshot": feature_snapshot(features),
        "feature_snapshot_sha256": assessment.feature_snapshot_sha256,
        "outcome_included": False,
        "production_changed": False,
        "can_promote": False,
    }
    record["capture_id"] = sha256_payload(record)
    _assert_no_outcome_keys(record)
    return record


CAPTURE_REQUIRED_KEYS = frozenset(
    {
        "record_type",
        "schema_version",
        "reliability_version",
        "feature_contract_version",
        "prediction_id",
        "match_id",
        "prediction_as_of",
        "captured_at",
        "a1_probability_p1",
        "a1_selected_side",
        "risk_points",
        "reliability_score",
        "reliability_tier",
        "selection_score",
        "contributions",
        "missing_features",
        "feature_snapshot",
        "feature_snapshot_sha256",
        "outcome_included",
        "production_changed",
        "can_promote",
        "capture_id",
    }
)


def validate_capture(record: Mapping[str, Any]) -> None:
    _assert_no_outcome_keys(record)
    missing = CAPTURE_REQUIRED_KEYS - set(record)
    extra = set(record) - CAPTURE_REQUIRED_KEYS
    if missing:
        raise ReliabilityValidationError(f"capture is missing keys: {sorted(missing)}")
    if extra:
        raise ReliabilityValidationError(f"capture has unexpected keys: {sorted(extra)}")
    if record["record_type"] != "a1_reliability_capture":
        raise ReliabilityValidationError("invalid record_type")
    if record["schema_version"] != PROSPECTIVE_SCHEMA_VERSION:
        raise ReliabilityValidationError("invalid schema_version")
    if record["reliability_version"] != VERSION:
        raise ReliabilityValidationError("invalid reliability_version")
    if record["feature_contract_version"] != FEATURE_CONTRACT_VERSION:
        raise ReliabilityValidationError("invalid feature_contract_version")
    if record["outcome_included"] is not False:
        raise ReliabilityValidationError("capture must have outcome_included=false")
    if record["production_changed"] is not False or record["can_promote"] is not False:
        raise ReliabilityValidationError("capture must remain research-only")
    if record["a1_selected_side"] != (1 if record["a1_probability_p1"] >= 0.5 else 2):
        raise ReliabilityValidationError("capture must preserve the original Agent 1 side")
    snapshot = record["feature_snapshot"]
    if record["feature_snapshot_sha256"] != sha256_payload(snapshot):
        raise ReliabilityValidationError("feature snapshot hash mismatch")
    expected_capture = dict(record)
    expected_capture.pop("capture_id")
    if record["capture_id"] != sha256_payload(expected_capture):
        raise ReliabilityValidationError("capture_id hash mismatch")


def settle_capture(
    capture: Mapping[str, Any],
    *,
    winner_side: int,
    settled_at: str,
) -> dict[str, Any]:
    """Add the outcome in a separate record; never mutate the capture."""
    validate_capture(capture)
    if winner_side not in (1, 2):
        raise ReliabilityValidationError("winner_side must be 1 or 2")
    _parse_time(settled_at, field_name="settled_at")
    return {
        "record_type": "a1_reliability_settlement",
        "schema_version": SETTLEMENT_SCHEMA_VERSION,
        "capture_id": capture["capture_id"],
        "prediction_id": capture["prediction_id"],
        "match_id": capture["match_id"],
        "a1_selected_side": capture["a1_selected_side"],
        "winner_side": winner_side,
        "a1_correct": capture["a1_selected_side"] == winner_side,
        "settled_at": settled_at,
        "outcome_included": True,
        "production_changed": False,
        "can_promote": False,
    }


# ---------------------------------------------------------------------------
# Evaluation utilities
# ---------------------------------------------------------------------------
def coverage_count(n: int, fraction: float) -> int:
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be within [0, 1]")
    if fraction == 1.0:
        return n
    return math.floor(fraction * n + 1e-9)


def rank_rows(
    rows: Sequence[Mapping[str, Any]],
    score_key: str,
    *,
    tie_keys: Sequence[str] = ("prediction_id", "match_id"),
) -> list[Mapping[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (-float(row[score_key]),) + tuple(row[key] for key in tie_keys),
    )


def wilson_interval(wins: int, n: int, z: float = 1.959963984540054) -> list[float] | None:
    if n <= 0:
        return None
    p = wins / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return [max(0.0, centre - half), min(1.0, centre + half)]


def selection_metrics(
    rows: Sequence[Mapping[str, Any]],
    selected: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    n = len(selected)
    if n == 0:
        return {"n": 0, "wins": 0, "errors": 0, "accuracy": None, "error_rate": None}
    wins = sum(1 for row in selected if row["correct"])
    by_tour = {}
    for tour in ("ATP_MAIN", "CHALLENGER"):
        subset = [row for row in selected if row["tour"] == tour]
        w = sum(1 for row in subset if row["correct"])
        by_tour[tour] = {
            "n": len(subset),
            "wins": w,
            "accuracy": w / len(subset) if subset else None,
        }
    return {
        "n": n,
        "wins": wins,
        "errors": n - wins,
        "accuracy": wins / n,
        "error_rate": 1.0 - wins / n,
        "wilson95": wilson_interval(wins, n),
        "mean_confidence": mean(row["confidence"] for row in selected),
        "mean_reliability": mean(row["reliability_score"] for row in selected),
        "mean_risk_points": mean(row["risk_points"] for row in selected),
        "by_tour": by_tour,
    }


def set_difference_metrics(
    rows: Sequence[Mapping[str, Any]],
    baseline: Sequence[Mapping[str, Any]],
    candidate: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base_ids = {row["prediction_id"] for row in baseline}
    cand_ids = {row["prediction_id"] for row in candidate}
    by_id = {row["prediction_id"]: row for row in rows}
    retained = base_ids & cand_ids
    removed = base_ids - cand_ids
    added = cand_ids - base_ids

    def summarise(ids: set[int]) -> dict[str, Any]:
        subset = [by_id[i] for i in ids]
        wins = sum(1 for row in subset if row["correct"])
        return {
            "n": len(subset),
            "wins": wins,
            "accuracy": wins / len(subset) if subset else None,
        }

    return {
        "retained": summarise(retained),
        "removed": summarise(removed),
        "added": summarise(added),
        "retained_n": len(retained),
        "removed_n": len(removed),
        "added_n": len(added),
    }


def risk_coverage(
    rows: Sequence[Mapping[str, Any]],
    score_key: str,
) -> dict[str, Any]:
    ranked = rank_rows(rows, score_key)
    n = len(ranked)
    cumulative_errors = 0
    aurc_values = []
    augrc = 0.0
    curve = []
    for index, row in enumerate(ranked):
        cumulative_errors += 0 if row["correct"] else 1
        risk = cumulative_errors / (index + 1)
        aurc_values.append(risk)
        if not row["correct"]:
            augrc += (n - index - 0.5) / (n * n)
        curve.append(
            {
                "rank": index + 1,
                "coverage": (index + 1) / n,
                "selective_risk": risk,
                "generalized_risk": cumulative_errors / n,
            }
        )
    return {
        "n": n,
        "errors": cumulative_errors,
        "aurc": mean(aurc_values) if aurc_values else None,
        "augrc_paper": augrc,
        "curve": curve,
    }


def auroc(scores: Sequence[float], labels: Sequence[int]) -> float | None:
    """Rank-based AUROC with exact tie averaging (label 1 = error)."""
    if len(scores) != len(labels) or not scores:
        return None
    pairs = sorted(zip(scores, labels, strict=True), key=lambda item: item[0])
    n1 = sum(labels)
    n0 = len(labels) - n1
    if n1 == 0 or n0 == 0:
        return None
    rank_sum = 0.0
    index = 0
    while index < len(pairs):
        end = index + 1
        while end < len(pairs) and pairs[end][0] == pairs[index][0]:
            end += 1
        average_rank = (index + 1 + end) / 2.0
        rank_sum += average_rank * sum(label for _score, label in pairs[index:end])
        index = end
    return (rank_sum - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def tier_calibration(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out = {}
    for tier in ("HIGH", "MEDIUM", "LOW"):
        subset = [row for row in rows if row["reliability_tier"] == tier]
        if not subset:
            out[tier] = {"n": 0}
            continue
        errors = sum(1 for row in subset if not row["correct"])
        out[tier] = {
            "n": len(subset),
            "errors": errors,
            "error_rate": errors / len(subset),
            "accuracy": 1.0 - errors / len(subset),
            "mean_confidence": mean(row["confidence"] for row in subset),
            "mean_reliability": mean(row["reliability_score"] for row in subset),
        }
    return out


def _cluster_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    *,
    key_a: str,
    key_b: str,
    fractions: Sequence[float],
    cluster_key: str,
    reps: int,
    seed: int,
) -> dict[str, Any]:
    clusters: dict[Any, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        clusters[row[cluster_key]].append(row)
    keys = list(clusters)
    if len(keys) < 5:
        return {"status": "INSUFFICIENT_CLUSTERS", "clusters": len(keys)}
    rng = random.Random(seed)
    diffs: dict[str, list[float]] = {f"{fraction:.2f}": [] for fraction in fractions}
    for _ in range(reps):
        sample: list[Mapping[str, Any]] = []
        for _ in range(len(keys)):
            sample.extend(clusters[rng.choice(keys)])
        for fraction in fractions:
            k = coverage_count(len(sample), fraction)
            baseline = rank_rows(sample, key_a)[:k]
            candidate = rank_rows(sample, key_b)[:k]
            base_acc = sum(1 for row in baseline if row["correct"]) / max(1, len(baseline))
            cand_acc = sum(1 for row in candidate if row["correct"]) / max(1, len(candidate))
            diffs[f"{fraction:.2f}"].append(cand_acc - base_acc)
    out = {}
    for fraction, values in diffs.items():
        values.sort()
        out[fraction] = {
            "median": values[len(values) // 2],
            "ci95": [
                values[int(0.025 * (len(values) - 1))],
                values[int(0.975 * (len(values) - 1))],
            ],
        }
    return {
        "status": "RESEARCH_ONLY_CLUSTER_BOOTSTRAP",
        "clusters": len(keys),
        "reps": reps,
        "seed": seed,
        "cluster_key": cluster_key,
        "differences_candidate_minus_baseline": out,
    }


def paired_cluster_cis(
    rows: Sequence[Mapping[str, Any]],
    *,
    fractions: Sequence[float] = (0.40, 0.45, 0.50),
    reps: int = 2000,
    seed: int = 20261127,
) -> dict[str, Any]:
    """Player and tournament-proxy cluster intervals for R1-R0 accuracy."""
    return {
        "player": _cluster_bootstrap(
            rows,
            key_a="confidence",
            key_b="selection_score",
            fractions=fractions,
            cluster_key="player_cluster",
            reps=reps,
            seed=seed,
        ),
        "tournament_proxy": _cluster_bootstrap(
            rows,
            key_a="confidence",
            key_b="selection_score",
            fractions=fractions,
            cluster_key="tournament_proxy",
            reps=reps,
            seed=seed + 1,
        ),
    }


# ---------------------------------------------------------------------------
# Synthetic-only model utilities (R2/R3/R4)
# ---------------------------------------------------------------------------
def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def fit_ridge_logistic(
    features: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    l2: float = 1.0,
    iterations: int = 200,
) -> dict[str, Any]:
    """Full-batch ridge logistic with an unpenalised intercept."""
    if len(features) != len(labels) or not features:
        raise ReliabilityValidationError("features and labels must be non-empty and aligned")
    if min(labels) == max(labels):
        raise ReliabilityValidationError("both classes are required")
    width = len(features[0])
    if any(len(row) != width for row in features):
        raise ReliabilityValidationError("ragged feature matrix")
    n = len(features)
    weights = [0.0] * width
    for _ in range(iterations):
        gradient = [0.0] * width
        for row, label in zip(features, labels, strict=True):
            predicted = _sigmoid(sum(w * x for w, x in zip(weights, row, strict=True)))
            for index, value in enumerate(row):
                gradient[index] += (predicted - label) * value
        for index in range(1, width):
            gradient[index] = gradient[index] / n + l2 * weights[index] / n
        gradient[0] /= n
        for index in range(width):
            weights[index] -= 0.5 * gradient[index]
    return {"weights": weights, "l2": l2, "iterations": iterations, "n": n}


def score_logistic(model: Mapping[str, Any], row: Sequence[float]) -> float:
    weights = model["weights"]
    if len(weights) != len(row):
        raise ReliabilityValidationError("model/feature dimension mismatch")
    return _sigmoid(sum(w * x for w, x in zip(weights, row, strict=True)))


def platt_calibrate(
    probabilities: Sequence[float],
    labels: Sequence[int],
) -> dict[str, Any]:
    """One-dimensional Platt scaling fitted on a calibration split."""
    if len(probabilities) != len(labels) or len(probabilities) < 20:
        return {"status": "INSUFFICIENT", "slope": None, "intercept": None}
    xs = [math.log(min(max(p, 1e-6), 1 - 1e-6) / (1 - min(max(p, 1e-6), 1 - 1e-6))) for p in probabilities]
    model = fit_ridge_logistic([[1.0, x] for x in xs], labels, l2=0.0)
    return {
        "status": "OK",
        "slope": model["weights"][1],
        "intercept": model["weights"][0],
        "n": len(labels),
    }


def apply_platt(calibration: Mapping[str, Any], probability: float) -> float:
    if calibration.get("status") != "OK":
        return probability
    clipped = min(max(probability, 1e-6), 1 - 1e-6)
    x = math.log(clipped / (1 - clipped))
    return _sigmoid(calibration["intercept"] + calibration["slope"] * x)


def fit_gradient_boosting_stumps(
    features: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    rounds: int = 20,
    learning_rate: float = 0.1,
    min_samples_leaf: int = 40,
) -> dict[str, Any]:
    """Deterministic depth-1 gradient boosting for synthetic validation only."""
    if len(features) != len(labels) or not features:
        raise ReliabilityValidationError("features and labels must be non-empty and aligned")
    if min(labels) == max(labels):
        raise ReliabilityValidationError("both classes are required")
    n = len(features)
    width = len(features[0])
    base = math.log(sum(labels) / (n - sum(labels)))
    scores = [base] * n
    stumps = []
    thresholds = []
    for feature_index in range(width):
        values = sorted({row[feature_index] for row in features})
        if len(values) < 2:
            thresholds.append([])
            continue
        quantiles = [values[int(q * (len(values) - 1))] for q in (0.2, 0.4, 0.6, 0.8)]
        thresholds.append(sorted(set(quantiles)))
    for _ in range(rounds):
        residuals = [
            label - _sigmoid(score) for score, label in zip(scores, labels, strict=True)
        ]
        best = None
        for feature_index in range(width):
            for threshold in thresholds[feature_index]:
                left = [i for i, row in enumerate(features) if row[feature_index] <= threshold]
                right = [i for i, row in enumerate(features) if row[feature_index] > threshold]
                if len(left) < min_samples_leaf or len(right) < min_samples_leaf:
                    continue
                left_value = sum(residuals[i] for i in left) / len(left)
                right_value = sum(residuals[i] for i in right) / len(right)
                sse = sum((residuals[i] - left_value) ** 2 for i in left) + sum(
                    (residuals[i] - right_value) ** 2 for i in right
                )
                if best is None or sse < best["sse"]:
                    best = {
                        "sse": sse,
                        "feature": feature_index,
                        "threshold": threshold,
                        "left_value": left_value,
                        "right_value": right_value,
                    }
        if best is None:
            break
        stumps.append(best)
        for i, row in enumerate(features):
            value = best["left_value"] if row[best["feature"]] <= best["threshold"] else best["right_value"]
            scores[i] += learning_rate * value
    return {
        "base": base,
        "stumps": stumps,
        "learning_rate": learning_rate,
        "rounds": len(stumps),
        "n": n,
    }


def score_boosting(model: Mapping[str, Any], row: Sequence[float]) -> float:
    score = model["base"]
    for stump in model["stumps"]:
        value = (
            stump["left_value"]
            if row[stump["feature"]] <= stump["threshold"]
            else stump["right_value"]
        )
        score += model["learning_rate"] * value
    return _sigmoid(score)


def standardise(
    features: Sequence[Sequence[float]],
) -> tuple[list[list[float]], list[float], list[float]]:
    if not features:
        return [], [], []
    width = len(features[0])
    means = [mean(row[i] for row in features) for i in range(width)]
    stds = []
    for i in range(width):
        variance = mean((row[i] - means[i]) ** 2 for row in features)
        stds.append(math.sqrt(variance) if variance > 0 else 1.0)
    transformed = [
        [(row[i] - means[i]) / stds[i] for i in range(width)] for row in features
    ]
    return transformed, means, stds


def apply_standardisation(
    features: Sequence[Sequence[float]],
    means: Sequence[float],
    stds: Sequence[float],
) -> list[list[float]]:
    return [
        [(row[i] - means[i]) / stds[i] for i in range(len(row))] for row in features
    ]


# ---------------------------------------------------------------------------
# Synthetic experiment
# ---------------------------------------------------------------------------
def synthetic_population(
    n: int = 4000,
    *,
    seed: int = 20261127,
    include_a2: bool = True,
) -> list[dict[str, Any]]:
    """Deterministic synthetic data with a known error-generating process."""
    rng = random.Random(seed)
    rows = []
    for index in range(n):
        confidence = rng.uniform(0.50, 0.95)
        favourite_bucket = rng.choices(
            ["0-4", "5-9", "10-19", "20-49", "50-99", "100+"],
            weights=[0.05, 0.07, 0.12, 0.25, 0.22, 0.29],
        )[0]
        favourite_matches = {
            "0-4": rng.randint(0, 4),
            "5-9": rng.randint(5, 9),
            "10-19": rng.randint(10, 19),
            "20-49": rng.randint(20, 49),
            "50-99": rng.randint(50, 99),
            "100+": rng.randint(100, 500),
        }[favourite_bucket]
        surface_matches = max(0, int(favourite_matches * rng.uniform(0.1, 0.8)))
        unresolved = rng.random() < 0.12
        unstable = rng.random() < 0.10
        divergence = rng.uniform(0, 250)
        gap = max(0.0, rng.gauss(120, 80))
        a2_present = include_a2 and rng.random() < 0.9
        a2_agrees = rng.random() < 0.72 if a2_present else None
        logit_error = (
            -1.8
            - 4.0 * (confidence - 0.70)
            + (2.6 if favourite_matches <= 4 else 1.5 if favourite_matches <= 9 else 0.6 if favourite_matches <= 19 else 0.0)
            + 1.4 * unresolved
            + 1.0 * unstable
            + 0.8 * (divergence >= 100)
            + 0.5 * (gap < 50)
            + (1.2 if a2_present and not a2_agrees else 0.7 if not a2_present else 0.0)
        )
        error_probability = _sigmoid(logit_error)
        correct = 0 if rng.random() < error_probability else 1
        rows.append(
            {
                "synthetic_id": index,
                "confidence": confidence,
                "a1_probability_p1": confidence if rng.random() < 0.5 else 1 - confidence,
                "favourite_matches": favourite_matches,
                "surface_matches": surface_matches,
                "unresolved": unresolved,
                "unstable": unstable,
                "divergence": divergence,
                "gap": gap,
                "a2_present": a2_present,
                "a2_agrees": a2_agrees,
                "correct": bool(correct),
            }
        )
    return rows


def synthetic_design_matrix(
    rows: Sequence[Mapping[str, Any]],
    *,
    include_a2: bool,
) -> list[list[float]]:
    matrix = []
    for row in rows:
        values = [
            1.0,
            float(row["confidence"]),
            float(row["favourite_matches"] <= 4),
            float(row["favourite_matches"] <= 9),
            float(row["surface_matches"] < 5),
            float(row["unresolved"]),
            float(row["unstable"]),
            float(row["divergence"]),
            float(row["gap"]),
        ]
        if include_a2:
            values.extend(
                [
                    float(row["a2_present"]),
                    float(bool(row["a2_agrees"])) if row["a2_present"] else 0.0,
                ]
            )
        matrix.append(values)
    return matrix


def split_synthetic(
    rows: Sequence[Mapping[str, Any]],
    *,
    train_fraction: float = 0.60,
    calibration_fraction: float = 0.20,
) -> dict[str, list[Mapping[str, Any]]]:
    n = len(rows)
    train_end = int(n * train_fraction)
    calibration_end = train_end + int(n * calibration_fraction)
    return {
        "train": list(rows[:train_end]),
        "calibration": list(rows[train_end:calibration_end]),
        "test": list(rows[calibration_end:]),
    }


def evaluate_synthetic_model(
    *,
    model_kind: str,
    train_rows: Sequence[Mapping[str, Any]],
    calibration_rows: Sequence[Mapping[str, Any]],
    test_rows: Sequence[Mapping[str, Any]],
    include_a2: bool,
    target_coverage: float = 0.45,
) -> dict[str, Any]:
    train_x = synthetic_design_matrix(train_rows, include_a2=include_a2)
    cal_x = synthetic_design_matrix(calibration_rows, include_a2=include_a2)
    test_x = synthetic_design_matrix(test_rows, include_a2=include_a2)
    train_y = [0 if row["correct"] else 1 for row in train_rows]
    cal_y = [0 if row["correct"] else 1 for row in calibration_rows]
    if model_kind == "R2":
        standard_train, means, stds = standardise([row[1:] for row in train_x])
        train_x = [[1.0] + row for row in standard_train]
        cal_x = [[1.0] + row for row in apply_standardisation([row[1:] for row in cal_x], means, stds)]
        test_x = [[1.0] + row for row in apply_standardisation([row[1:] for row in test_x], means, stds)]
        model = fit_ridge_logistic(train_x, train_y, l2=2.0)
        cal_prob = [score_logistic(model, row) for row in cal_x]
        test_prob = [score_logistic(model, row) for row in test_x]
    elif model_kind == "R3":
        model = fit_gradient_boosting_stumps(train_x, train_y)
        cal_prob = [score_boosting(model, row) for row in cal_x]
        test_prob = [score_boosting(model, row) for row in test_x]
    else:
        raise ReliabilityValidationError("model_kind must be R2 or R3")
    calibration = platt_calibrate(cal_prob, cal_y)
    test_prob = [apply_platt(calibration, probability) for probability in test_prob]
    scored = []
    for row, error_probability in zip(test_rows, test_prob, strict=True):
        scored_row = dict(row)
        scored_row["error_probability"] = error_probability
        scored_row["selection_score"] = row["confidence"] - 0.25 * error_probability
        scored.append(scored_row)
    k = coverage_count(len(scored), target_coverage)
    ranked = sorted(scored, key=lambda row: (-row["selection_score"], row["synthetic_id"]))
    selected = ranked[:k]
    baseline = sorted(scored, key=lambda row: (-row["confidence"], row["synthetic_id"]))[:k]
    return {
        "model_kind": model_kind,
        "include_a2": include_a2,
        "train_n": len(train_rows),
        "calibration_n": len(calibration_rows),
        "test_n": len(test_rows),
        "target_coverage": target_coverage,
        "selected_n": len(selected),
        "candidate_accuracy": sum(1 for row in selected if row["correct"]) / max(1, len(selected)),
        "baseline_accuracy": sum(1 for row in baseline if row["correct"]) / max(1, len(baseline)),
        "calibration": calibration,
        "test_brier": mean((row["error_probability"] - (0 if row["correct"] else 1)) ** 2 for row in scored),
        "test_error_auroc": auroc([row["error_probability"] for row in scored], [0 if row["correct"] else 1 for row in scored]),
    }


def synthetic_suite() -> dict[str, Any]:
    rows = synthetic_population(6000, seed=20261127, include_a2=True)
    split = split_synthetic(rows)
    r2 = evaluate_synthetic_model(
        model_kind="R2",
        train_rows=split["train"],
        calibration_rows=split["calibration"],
        test_rows=split["test"],
        include_a2=False,
    )
    r3_gate = len(split["train"]) >= 2000 and sum(1 for row in split["train"] if not row["correct"]) >= 200
    r3 = (
        evaluate_synthetic_model(
            model_kind="R3",
            train_rows=split["train"],
            calibration_rows=split["calibration"],
            test_rows=split["test"],
            include_a2=False,
        )
        if r3_gate
        else {"status": "SAMPLE_SIZE_GATE_NOT_MET"}
    )
    r4_a2_coverage = sum(1 for row in split["train"] if row["a2_present"]) / len(split["train"])
    r4 = (
        evaluate_synthetic_model(
            model_kind="R2",
            train_rows=split["train"],
            calibration_rows=split["calibration"],
            test_rows=split["test"],
            include_a2=True,
        )
        if r4_a2_coverage >= 0.80
        else {"status": "A2_COVERAGE_GATE_NOT_MET", "coverage": r4_a2_coverage}
    )
    return {
        "version": "A1_RELIABILITY_SYNTHETIC_V1",
        "seed": 20261127,
        "n": len(rows),
        "split": {name: len(value) for name, value in split.items()},
        "a2_train_coverage": r4_a2_coverage,
        "r2": r2,
        "r3": r3,
        "r3_gate_met": r3_gate,
        "r4": r4,
        "note": "Synthetic only. No production or historical outcome data are used for fitting.",
    }
