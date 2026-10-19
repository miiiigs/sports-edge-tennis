"""Agent 2 recent-history lookback shadow experiment.

The harness is deliberately outside the production package.  It never writes to
the database, never changes Agent 2 configuration, and never activates a model.

Why this is a *tournament-cohort* replay rather than a per-match pre-match
backtest
--------------------------------------------------------------------------
The 2016-2025 Sackmann corpus in the canonical database has no trustworthy
``match_versions.match_start_at`` before 2026.  Its stored versions/results also
become visible at the tournament-end boundary.  A prediction at
``match_start_at - 6h`` therefore cannot be reconstructed for the historical
period.  Instead, every match in one tournament edition is predicted at the
edition's *visibility cohort* (the maximum timestamp of the selected fixture,
result and stat evidence), before any result from that edition is applied.  All
four history policies use the same cohorts, matches and outcomes.

This is an offline history-window evaluation, not a claim about genuinely
forward-issued Model Picks.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

try:  # numpy is a research-only dependency; see research/a2_form_lookback/requirements.txt
    import numpy as np
except ImportError:  # pragma: no cover - exercised only in minimal environments
    np = None  # type: ignore[assignment]

from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    DEFAULT_CONFIG,
    SUPPORTED_COMPETITION_TIERS,
    SUPPORTED_SURFACES,
    SUPPORTED_TOUR_SCOPES,
    Agent2Config,
    predict,
)
from sports_edge_tennis.data.agent2_history import (
    Agent2HistoricalMatch,
    Agent2MatchStatRow,
    _MutablePlayerAccumulator,
    _MutablePopulationAccumulator,
    apply_historical_match_in_place,
    build_prediction_request_from_snapshot,
    historical_match_is_eligible_for_update,
    normalize_tournament_alias,
    snapshot_history_state,
)
from sports_edge_tennis.validation.agent2_recency import (
    _remove_historical_match_in_place,
)

EXPERIMENT_ID = "a2_form_lookback_shadow_20261019"
VARIANTS: tuple[tuple[str, int | None], ...] = (
    ("baseline", None),
    ("365", 365),
    ("730", 730),
    ("1460", 1460),
)
SPLIT_BURN_IN = "burn_in"
SPLIT_VALIDATION = "validation"
SPLIT_TEST = "test"
EVALUATION_SPLITS = (SPLIT_VALIDATION, SPLIT_TEST)
DEFAULT_SEED = 20261019
INACTIVITY_DAYS = 180


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class FrozenMatch:
    """One selected historical match from the frozen JSONL snapshot."""

    match_id: int
    season_year: int
    tournament_id: int
    canonical_tournament_name: str
    edition_start_date: date | None
    edition_end_date: date | None
    outcome_status: str
    winner_side: int | None
    result_source_observed_at: datetime
    result_as_of: datetime
    match_version_id: int
    player_1_id: int
    player_2_id: int
    match_start_at: datetime | None
    tour_scope: str
    competition_tier: str
    surface: str | None
    round_code: str | None
    best_of_sets: int
    qualifying_flag: bool
    version_source_observed_at: datetime
    version_as_of: datetime
    stat_source_system: str | None
    stat_source_observed_at: datetime | None
    stat_as_of: datetime | None
    stats: tuple[dict[str, object], ...]

    @classmethod
    def from_dict(cls, raw: dict) -> FrozenMatch:
        return cls(
            match_id=int(raw["match_id"]),
            season_year=int(raw["season_year"]),
            tournament_id=int(raw["tournament_id"]),
            canonical_tournament_name=str(raw["canonical_tournament_name"]),
            edition_start_date=(
                date.fromisoformat(raw["edition_start_date"])
                if raw.get("edition_start_date")
                else None
            ),
            edition_end_date=(
                date.fromisoformat(raw["edition_end_date"])
                if raw.get("edition_end_date")
                else None
            ),
            outcome_status=str(raw["outcome_status"]),
            winner_side=int(raw["winner_side"]) if raw.get("winner_side") is not None else None,
            result_source_observed_at=_parse_dt(raw["result_source_observed_at"]),  # type: ignore[arg-type]
            result_as_of=_parse_dt(raw["result_as_of"]),  # type: ignore[arg-type]
            match_version_id=int(raw["match_version_id"]),
            player_1_id=int(raw["player_1_id"]),
            player_2_id=int(raw["player_2_id"]),
            match_start_at=_parse_dt(raw.get("match_start_at")),
            tour_scope=str(raw["tour_scope"]),
            competition_tier=str(raw["competition_tier"]),
            surface=raw.get("surface"),
            round_code=raw.get("round_code"),
            best_of_sets=int(raw["best_of_sets"]),
            qualifying_flag=bool(raw["qualifying_flag"]),
            version_source_observed_at=_parse_dt(raw["version_source_observed_at"]),  # type: ignore[arg-type]
            version_as_of=_parse_dt(raw["version_as_of"]),  # type: ignore[arg-type]
            stat_source_system=raw.get("stat_source_system"),
            stat_source_observed_at=_parse_dt(raw.get("stat_source_observed_at")),
            stat_as_of=_parse_dt(raw.get("stat_as_of")),
            stats=tuple(raw.get("stats") or ()),
        )

    @property
    def history_visible_at(self) -> datetime:
        values = [
            self.result_source_observed_at,
            self.result_as_of,
            self.version_source_observed_at,
            self.version_as_of,
        ]
        if self.stat_source_observed_at is not None:
            values.append(self.stat_source_observed_at)
        if self.stat_as_of is not None:
            values.append(self.stat_as_of)
        return max(values)

    @property
    def match_date(self) -> date:
        if self.edition_start_date is not None:
            return self.edition_start_date
        return self.history_visible_at.date()

    @property
    def split(self) -> str:
        if self.season_year <= 2018:
            return SPLIT_BURN_IN
        if self.season_year <= 2022:
            return SPLIT_VALIDATION
        return SPLIT_TEST

    def to_observation(self) -> Agent2HistoricalMatch:
        stat_observed = self.stat_source_observed_at or self.history_visible_at
        stat_as_of = self.stat_as_of or self.history_visible_at
        stat_rows = tuple(
            Agent2MatchStatRow(
                match_stat_observation_id=self.match_id * 10 + index,
                player_id=int(stat["player_id"]),
                source_system=str(self.stat_source_system or "FROZEN"),
                source_observed_at=stat_observed,
                as_of=stat_as_of,
                first_serve_points_won=(
                    int(stat["first_serve_points_won"])
                    if stat.get("first_serve_points_won") is not None
                    else None
                ),
                second_serve_points_won=(
                    int(stat["second_serve_points_won"])
                    if stat.get("second_serve_points_won") is not None
                    else None
                ),
                service_points_played=(
                    int(stat["service_points_played"])
                    if stat.get("service_points_played") is not None
                    else None
                ),
                return_points_won=(
                    int(stat["return_points_won"])
                    if stat.get("return_points_won") is not None
                    else None
                ),
            )
            for index, stat in enumerate(self.stats)
        )
        return Agent2HistoricalMatch(
            match_id=self.match_id,
            player_1_id=self.player_1_id,
            player_2_id=self.player_2_id,
            match_start_at=self.match_start_at or self.history_visible_at,
            surface=self.surface,  # type: ignore[arg-type]
            tour_scope=self.tour_scope,  # type: ignore[arg-type]
            competition_tier=self.competition_tier,  # type: ignore[arg-type]
            round_code=self.round_code,
            best_of_sets=self.best_of_sets,
            qualifying_flag=self.qualifying_flag,
            tournament_id=self.tournament_id,
            canonical_tournament_name=self.canonical_tournament_name,
            normalized_tournament_alias=normalize_tournament_alias(
                self.canonical_tournament_name
            ),
            season_year=self.season_year,
            outcome_status=self.outcome_status,
            winner_side=self.winner_side,
            result_source_observed_at=self.result_source_observed_at,
            result_as_of=self.result_as_of,
            version_source_observed_at=self.version_source_observed_at,
            version_as_of=self.version_as_of,
            stat_rows=stat_rows,
        )


@dataclass(frozen=True)
class EligibleMatch:
    frozen: FrozenMatch
    observation: Agent2HistoricalMatch


@dataclass
class TournamentGroup:
    key: tuple[int, int]
    matches: list[EligibleMatch]
    cohort: datetime
    season_year: int
    match_date: date

    @property
    def split(self) -> str:
        return self.matches[0].frozen.split


def load_dataset(path: Path) -> tuple[list[FrozenMatch], dict]:
    """Load and verify the frozen JSONL snapshot."""
    opener = gzip.open if path.suffix == ".gz" else open
    raw_records: list[dict] = []
    manifest: dict | None = None
    with opener(path, "rt", encoding="utf-8-sig") as handle:  # type: ignore[arg-type]
        for line in handle:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "_manifest" in obj:
                manifest = obj["_manifest"]
                continue
            raw_records.append(obj)
    if manifest is None:
        raise ValueError("frozen dataset is missing the _manifest line")
    digest = hashlib.sha256()
    for raw in raw_records:
        digest.update(_canonical(raw).encode("utf-8"))
        digest.update(b"\n")
    if digest.hexdigest() != manifest.get("dataset_sha256"):
        raise ValueError("frozen dataset hash does not match its manifest")
    records = [FrozenMatch.from_dict(raw) for raw in raw_records]
    return records, manifest


def _is_eligible(frozen: FrozenMatch) -> EligibleMatch | None:
    if frozen.outcome_status != "COMPLETED" or frozen.winner_side not in (1, 2):
        return None
    if frozen.player_1_id == frozen.player_2_id:
        return None
    if frozen.surface not in SUPPORTED_SURFACES:
        return None
    if frozen.tour_scope not in SUPPORTED_TOUR_SCOPES:
        return None
    if frozen.competition_tier not in SUPPORTED_COMPETITION_TIERS:
        return None
    if frozen.edition_start_date is None:
        return None
    if frozen.stats is None or len(frozen.stats) != 2:
        return None
    observation = frozen.to_observation()
    if not historical_match_is_eligible_for_update(observation):
        return None
    return EligibleMatch(frozen=frozen, observation=observation)


def eligible_matches(records: Sequence[FrozenMatch]) -> list[EligibleMatch]:
    eligible: list[EligibleMatch] = []
    for record in records:
        match = _is_eligible(record)
        if match is not None:
            eligible.append(match)
    return eligible


def population_sha256(matches: Sequence[EligibleMatch]) -> str:
    digest = hashlib.sha256()
    for match in sorted(matches, key=lambda item: item.frozen.match_id):
        frozen = match.frozen
        digest.update(
            _canonical(
                {
                    "match_id": frozen.match_id,
                    "players": [frozen.player_1_id, frozen.player_2_id],
                    "tournament": frozen.tournament_id,
                    "season": frozen.season_year,
                    "surface": frozen.surface,
                    "tier": frozen.competition_tier,
                    "winner": frozen.winner_side,
                    "result_visible_at": frozen.result_source_observed_at.isoformat(),
                    "result_as_of": frozen.result_as_of.isoformat(),
                    "version_visible_at": frozen.version_source_observed_at.isoformat(),
                    "version_as_of": frozen.version_as_of.isoformat(),
                    "stat_visible_at": (
                        frozen.stat_source_observed_at.isoformat()
                        if frozen.stat_source_observed_at
                        else None
                    ),
                    "stat_as_of": frozen.stat_as_of.isoformat() if frozen.stat_as_of else None,
                    "stats": frozen.stats,
                }
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def build_groups(matches: Sequence[EligibleMatch]) -> list[TournamentGroup]:
    buckets: dict[tuple[int, int], list[EligibleMatch]] = defaultdict(list)
    for match in matches:
        buckets[(match.frozen.tournament_id, match.frozen.season_year)].append(match)
    groups: list[TournamentGroup] = []
    for key, bucket in buckets.items():
        cohort = max(item.frozen.history_visible_at for item in bucket)
        match_date = min(item.frozen.match_date for item in bucket)
        groups.append(
            TournamentGroup(
                key=key,
                matches=sorted(bucket, key=lambda item: item.frozen.match_id),
                cohort=cohort,
                season_year=bucket[0].frozen.season_year,
                match_date=match_date,
            )
        )
    groups.sort(key=lambda group: (group.cohort, group.key))
    return groups


def active_group_keys(
    groups: Sequence[TournamentGroup],
    current_index: int,
    window_days: int | None,
) -> list[tuple[int, int]]:
    """Return prior groups visible inside the history window.

    The window is defined on evidence-visibility time because that is the only
    trustworthy timestamp in the 2016-2025 corpus.  Groups are sorted by
    ``cohort``, so the active set is a suffix of the already-processed groups.
    """

    if window_days is None:
        return [group.key for group in groups[:current_index]]
    cutoff = groups[current_index].cohort - timedelta(days=window_days)
    return [group.key for group in groups[:current_index] if group.cohort >= cutoff]


def _wilson_interval(successes: int, trials: int, z: float = 1.959963984540054) -> tuple[float, float] | None:
    if trials <= 0:
        return None
    proportion = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (proportion + z * z / (2.0 * trials)) / denominator
    half = (
        z
        * ((proportion * (1.0 - proportion) / trials) + z * z / (4.0 * trials * trials)) ** 0.5
        / denominator
    )
    return max(0.0, centre - half), min(1.0, centre + half)


def _point_metrics(records: Sequence[dict]) -> dict:
    eligible = len(records)
    predicted_records = [record for record in records if record["predicted"]]
    predicted = len(predicted_records)
    abstained = eligible - predicted
    correct = sum(record["y"] for record in predicted_records)
    probabilities = [record["probability"] for record in predicted_records]
    outcomes = [record["y"] for record in predicted_records]
    result: dict[str, object] = {
        "eligible": eligible,
        "predicted": predicted,
        "abstained": abstained,
        "settled": predicted,
        "correct": correct,
        "accuracy": (correct / predicted) if predicted else None,
        "brier": None,
        "log_loss": None,
        "ece": None,
        "coverage": (predicted / eligible) if eligible else None,
        "mean_probability": (sum(probabilities) / predicted) if predicted else None,
        "mean_outcome": (sum(outcomes) / predicted) if predicted else None,
        "accuracy_wilson": _wilson_interval(correct, predicted),
        "coverage_wilson": _wilson_interval(predicted, eligible),
        "reliability": [],
        "confidence_cumulative": [],
    }
    if not predicted:
        return result
    brier = sum((probability - outcome) ** 2 for probability, outcome in zip(probabilities, outcomes)) / predicted
    eps = 1e-15
    log_loss = sum(
        -(
            outcome * math.log(max(eps, min(1 - eps, probability)))
            + (1 - outcome) * math.log(max(eps, min(1 - eps, 1 - probability)))
        )
        for probability, outcome in zip(probabilities, outcomes)
    ) / predicted
    ece, reliability = _ece(probabilities, outcomes)
    result["brier"] = brier
    result["log_loss"] = log_loss
    result["ece"] = ece
    result["reliability"] = reliability
    result["confidence_cumulative"] = [
        _confidence_band(probabilities, outcomes, threshold)
        for threshold in (0.70, 0.80, 0.90)
    ]
    return result


def _ece(probabilities: Sequence[float], outcomes: Sequence[int]) -> tuple[float, list[dict]]:
    bins: list[list[tuple[float, int]]] = [[] for _ in range(10)]
    for probability, outcome in zip(probabilities, outcomes):
        index = int((probability - 0.5) / 0.05)
        index = max(0, min(9, index))
        bins[index].append((probability, outcome))
    total = len(probabilities)
    ece = 0.0
    reliability: list[dict] = []
    for index, bucket in enumerate(bins):
        lower = 0.5 + index * 0.05
        upper = lower + 0.05
        if not bucket:
            reliability.append(
                {
                    "lower": lower,
                    "upper": upper,
                    "n": 0,
                    "mean_probability": None,
                    "observed": None,
                    "gap": None,
                }
            )
            continue
        mean_probability = sum(item[0] for item in bucket) / len(bucket)
        observed = sum(item[1] for item in bucket) / len(bucket)
        gap = abs(observed - mean_probability)
        ece += (len(bucket) / total) * gap
        reliability.append(
            {
                "lower": lower,
                "upper": upper,
                "n": len(bucket),
                "mean_probability": mean_probability,
                "observed": observed,
                "gap": gap,
            }
        )
    return ece, reliability


def _confidence_band(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    threshold: float,
) -> dict:
    selected = [
        (probability, outcome)
        for probability, outcome in zip(probabilities, outcomes)
        if probability >= threshold
    ]
    if not selected:
        return {
            "threshold": threshold,
            "n": 0,
            "mean_probability": None,
            "observed": None,
            "gap": None,
            "wilson": None,
        }
    mean_probability = sum(item[0] for item in selected) / len(selected)
    wins = sum(item[1] for item in selected)
    observed = wins / len(selected)
    return {
        "threshold": threshold,
        "n": len(selected),
        "mean_probability": mean_probability,
        "observed": observed,
        "gap": abs(observed - mean_probability),
        "wilson": _wilson_interval(wins, len(selected)),
    }


def _bootstrap_metrics(
    records: Sequence[dict],
    *,
    reps: int,
    seed: int,
) -> dict:
    if np is None or not records:
        return {}
    probabilities = np.asarray([record["probability"] for record in records], dtype=float)
    outcomes = np.asarray([record["y"] for record in records], dtype=float)
    n = len(probabilities)
    if n == 0:
        return {}
    rng = np.random.default_rng(seed)
    accuracy: list[float] = []
    brier: list[float] = []
    log_loss: list[float] = []
    ece: list[float] = []
    for _ in range(reps):
        indices = rng.integers(0, n, n)
        p = probabilities[indices]
        y = outcomes[indices]
        accuracy.append(float(y.mean()))
        brier.append(float(((p - y) ** 2).mean()))
        clipped = np.clip(p, 1e-15, 1 - 1e-15)
        log_loss.append(float(-(y * np.log(clipped) + (1 - y) * np.log(1 - clipped)).mean()))
        ece.append(_ece_numpy(p, y))
    return {
        "reps": reps,
        "seed": seed,
        "accuracy": _percentile_interval(accuracy),
        "brier": _percentile_interval(brier),
        "log_loss": _percentile_interval(log_loss),
        "ece": _percentile_interval(ece),
    }


def _ece_numpy(probabilities, outcomes) -> float:
    bins = np.clip(((probabilities - 0.5) / 0.05).astype(int), 0, 9)
    counts = np.bincount(bins, minlength=10)
    prob_sums = np.bincount(bins, weights=probabilities, minlength=10)
    outcome_sums = np.bincount(bins, weights=outcomes, minlength=10)
    n = len(probabilities)
    value = 0.0
    for index in range(10):
        if counts[index] == 0:
            continue
        mean_p = prob_sums[index] / counts[index]
        mean_y = outcome_sums[index] / counts[index]
        value += (counts[index] / n) * abs(mean_p - mean_y)
    return float(value)


def _percentile_interval(values: Sequence[float]) -> list[float] | None:
    if not values:
        return None
    return [float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))]


def _coverage_bootstrap(
    records: Sequence[dict],
    *,
    reps: int,
    seed: int,
) -> list[float] | None:
    if np is None or not records:
        return None
    predicted = np.asarray([1 if record["predicted"] else 0 for record in records], dtype=float)
    n = len(predicted)
    rng = np.random.default_rng(seed)
    values = [float(predicted[rng.integers(0, n, n)].mean()) for _ in range(reps)]
    return _percentile_interval(values)


def _paired_bootstrap(
    baseline_records: Sequence[dict],
    variant_records: Sequence[dict],
    *,
    reps: int,
    seed: int,
) -> dict:
    if np is None:
        return {}
    baseline_by_id = {record["match_id"]: record for record in baseline_records}
    variant_by_id = {record["match_id"]: record for record in variant_records}
    common = sorted(set(baseline_by_id) & set(variant_by_id))
    if not common:
        return {"n_common": 0}
    base = [baseline_by_id[match_id] for match_id in common]
    variant = [variant_by_id[match_id] for match_id in common]
    p_base = np.asarray([record["probability"] for record in base], dtype=float)
    y_base = np.asarray([record["y"] for record in base], dtype=float)
    p_var = np.asarray([record["probability"] for record in variant], dtype=float)
    y_var = np.asarray([record["y"] for record in variant], dtype=float)
    n = len(common)
    rng = np.random.default_rng(seed)
    differences = {
        "accuracy": [],
        "brier": [],
        "log_loss": [],
        "ece": [],
    }
    for _ in range(reps):
        indices = rng.integers(0, n, n)
        yb = y_base[indices]
        yv = y_var[indices]
        pb = p_base[indices]
        pv = p_var[indices]
        differences["accuracy"].append(float(yv.mean() - yb.mean()))
        differences["brier"].append(
            float(((pv - yv) ** 2).mean() - ((pb - yb) ** 2).mean())
        )
        cb = np.clip(pb, 1e-15, 1 - 1e-15)
        cv = np.clip(pv, 1e-15, 1 - 1e-15)
        differences["log_loss"].append(
            float(
                -(yv * np.log(cv) + (1 - yv) * np.log(1 - cv)).mean()
                + (yb * np.log(cb) + (1 - yb) * np.log(1 - cb)).mean()
            )
        )
        differences["ece"].append(float(_ece_numpy(pv, yv) - _ece_numpy(pb, yb)))
    base_metrics = _point_metrics(base)
    variant_metrics = _point_metrics(variant)
    return {
        "n_common": n,
        "baseline": {
            key: base_metrics.get(key)
            for key in ("accuracy", "brier", "log_loss", "ece", "predicted")
        },
        "variant": {
            key: variant_metrics.get(key)
            for key in ("accuracy", "brier", "log_loss", "ece", "predicted")
        },
        "difference": {
            key: (
                (variant_metrics[key] - base_metrics[key])
                if variant_metrics.get(key) is not None and base_metrics.get(key) is not None
                else None
            )
            for key in ("accuracy", "brier", "log_loss", "ece")
        },
        "difference_ci": {
            key: _percentile_interval(values) for key, values in differences.items()
        },
        "reps": reps,
        "seed": seed,
    }


def _group_records(records: Sequence[dict], key: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[str(record.get(key))].append(record)
    return dict(grouped)


def _subgroup_metrics(records: Sequence[dict], key: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for value, bucket in sorted(_group_records(records, key).items()):
        result[value] = _point_metrics(bucket)
    return result


def analyze_records(
    records_by_variant: dict[str, list[dict]],
    *,
    manifest: dict,
    population_sha: str,
    population_count: int,
    groups: int,
    reps: int,
    seed: int,
    runtime_seconds: float,
) -> dict:
    all_ids: dict[str, set[int]] = {
        variant: {record["match_id"] for record in records}
        for variant, records in records_by_variant.items()
    }
    id_sets = list(all_ids.values())
    if not all(ids == id_sets[0] for ids in id_sets):
        raise ValueError("variants do not share the same eligible match population")
    analysis: dict[str, object] = {
        "experiment_id": EXPERIMENT_ID,
        "dataset_manifest": manifest,
        "population_sha256": population_sha,
        "population_matches": population_count,
        "tournament_groups": groups,
        "runtime_seconds": runtime_seconds,
        "variants": {},
        "split_comparisons": {},
    }
    for split in EVALUATION_SPLITS + ("all_eval",):
        split_records = {
            variant: [
                record
                for record in records
                if (record["split"] == split or split == "all_eval" and record["split"] in EVALUATION_SPLITS)
            ]
            for variant, records in records_by_variant.items()
        }
        if not split_records["baseline"]:
            continue
        predicted_sets = {
            variant: {record["match_id"] for record in records if record["predicted"]}
            for variant, records in split_records.items()
        }
        common_ids = set.intersection(*predicted_sets.values())
        split_analysis: dict[str, object] = {
            "overall": {},
            "common_prediction": {},
            "paired_vs_baseline": {},
            "by_surface": {},
            "by_tier": {},
            "by_tour": {},
            "by_season": {},
            "by_history_bucket": {},
            "by_inactivity": {},
            "by_qualifying": {},
            "abstain_reasons": {},
        }
        for variant, records in split_records.items():
            overall = _point_metrics(records)
            overall["coverage_ci"] = _coverage_bootstrap(records, reps=reps, seed=seed)
            if overall["predicted"]:
                overall["bootstrap"] = _bootstrap_metrics(
                    [record for record in records if record["predicted"]],
                    reps=reps,
                    seed=seed + (0 if variant == "baseline" else int(variant)),
                )
            split_analysis["overall"][variant] = overall
            common_records = [record for record in records if record["match_id"] in common_ids]
            split_analysis["common_prediction"][variant] = _point_metrics(common_records)
            split_analysis["abstain_reasons"][variant] = dict(
                Counter(
                    record["abstain_reason"]
                    for record in records
                    if not record["predicted"]
                )
            )
            split_analysis["by_surface"][variant] = _subgroup_metrics(records, "surface")
            split_analysis["by_tier"][variant] = _subgroup_metrics(records, "competition_tier")
            split_analysis["by_tour"][variant] = _subgroup_metrics(records, "tour_scope")
            split_analysis["by_season"][variant] = _subgroup_metrics(records, "season_year")
            split_analysis["by_history_bucket"][variant] = _subgroup_metrics(
                records, "history_bucket"
            )
            split_analysis["by_inactivity"][variant] = _subgroup_metrics(
                records, "inactivity_bucket"
            )
            split_analysis["by_qualifying"][variant] = _subgroup_metrics(
                records, "qualifying_bucket"
            )
        for variant, records in split_records.items():
            if variant == "baseline":
                continue
            common_records = [record for record in records if record["match_id"] in common_ids]
            baseline_common = [
                record
                for record in split_records["baseline"]
                if record["match_id"] in common_ids and record["predicted"]
            ]
            variant_common = [record for record in common_records if record["predicted"]]
            split_analysis["paired_vs_baseline"][variant] = _paired_bootstrap(
                baseline_common,
                variant_common,
                reps=reps,
                seed=seed,
            )
        analysis["variants"][split] = split_analysis  # type: ignore[index]
    return analysis


@dataclass
class _VariantState:
    window_days: int | None
    players: dict[int, _MutablePlayerAccumulator] = field(default_factory=dict)
    population: _MutablePopulationAccumulator = field(default_factory=_MutablePopulationAccumulator)
    active_matches: set[int] = field(default_factory=set)
    active_groups: deque[TournamentGroup] = field(default_factory=deque)

    def activate(self, group: TournamentGroup) -> None:
        for match in group.matches:
            observation = match.observation
            if apply_historical_match_in_place(self.players, self.population, observation):
                self.active_matches.add(observation.match_id)
        self.active_groups.append(group)

    def expire(self, cutoff: datetime) -> None:
        if self.window_days is None:
            return
        while self.active_groups and self.active_groups[0].cohort < cutoff:
            group = self.active_groups.popleft()
            for match in group.matches:
                observation = match.observation
                if observation.match_id not in self.active_matches:
                    continue
                _remove_historical_match_in_place(
                    self.players, self.population, observation
                )
                self.active_matches.discard(observation.match_id)


def _history_bucket(prior: int | None) -> str:
    if prior is None:
        return "unknown"
    if prior < 5:
        return "insufficient_lt5"
    if prior < 10:
        return "5_9"
    if prior < 20:
        return "10_19"
    if prior < 50:
        return "20_49"
    return "50_plus"


def _inactivity_bucket(max_gap: int | None) -> str:
    if max_gap is None:
        return "no_prior_history"
    if max_gap >= INACTIVITY_DAYS:
        return "return_ge_180d"
    return "active_lt_180d"


def _predict_group(
    state: _VariantState,
    group: TournamentGroup,
    *,
    variant: str,
    window_days: int | None,
    config: Agent2Config,
    record_predictions: bool,
    global_last_dates: dict[int, date],
) -> list[dict]:
    if not record_predictions:
        return []
    snapshot = snapshot_history_state(state.players, state.population)
    records: list[dict] = []
    for match in group.matches:
        frozen = match.frozen
        observation = match.observation
        last_dates = [
            global_last_dates.get(frozen.player_1_id),
            global_last_dates.get(frozen.player_2_id),
        ]
        gaps = [
            (frozen.match_date - last_date).days if last_date is not None else None
            for last_date in last_dates
        ]
        valid_gaps = [gap for gap in gaps if gap is not None and gap >= 0]
        max_gap = max(valid_gaps) if valid_gaps else None
        request = build_prediction_request_from_snapshot(
            observation,
            snapshot,
            prediction_as_of=group.cohort,
        )
        result = predict(request, config)
        predicted = result.prediction_status == "PREDICTED"
        probability = float(result.win_probability) if predicted and result.win_probability is not None else None
        selection_side = result.selection_side if predicted else None
        outcome = (
            1
            if predicted and selection_side == frozen.winner_side
            else 0
        )
        prior_1 = result.diagnostics.player_1_prior_usable_matches
        prior_2 = result.diagnostics.player_2_prior_usable_matches
        prior = min(prior_1, prior_2)
        records.append(
            {
                "match_id": frozen.match_id,
                "variant": variant,
                "window_days": window_days,
                "split": frozen.split,
                "season_year": frozen.season_year,
                "cohort": group.cohort.isoformat(),
                "match_date": frozen.match_date.isoformat(),
                "tournament_id": frozen.tournament_id,
                "surface": frozen.surface,
                "tour_scope": frozen.tour_scope,
                "competition_tier": frozen.competition_tier,
                "round_code": frozen.round_code,
                "qualifying_flag": frozen.qualifying_flag,
                "predicted": predicted,
                "status": result.prediction_status,
                "abstain_reason": (
                    result.diagnostics.abstain_reason.value
                    if result.diagnostics.abstain_reason is not None
                    else None
                ),
                "selection_side": selection_side,
                "probability": probability,
                "winner_side": frozen.winner_side,
                "y": outcome if predicted else None,
                "correct": outcome if predicted else None,
                "player_1_prior": prior_1,
                "player_2_prior": prior_2,
                "min_prior": prior,
                "history_bucket": _history_bucket(prior),
                "player_1_inactivity_days": gaps[0],
                "player_2_inactivity_days": gaps[1],
                "max_inactivity_days": max_gap,
                "inactivity_bucket": _inactivity_bucket(max_gap),
                "return_from_inactivity": bool(
                    max_gap is not None and max_gap >= INACTIVITY_DAYS
                ),
                "qualifying_bucket": "qualifying" if frozen.qualifying_flag else "main_draw",
            }
        )
    return records


def run_variant(
    dataset_path: Path,
    *,
    variant: str,
    window_days: int | None,
    output_path: Path,
    config: Agent2Config = DEFAULT_CONFIG,
    max_groups: int | None = None,
    max_predict_groups: int | None = None,
) -> dict:
    started = time.time()
    records, manifest = load_dataset(dataset_path)
    matches = eligible_matches(records)
    groups = build_groups(matches)
    state = _VariantState(window_days=window_days)
    predictions: list[dict] = []
    predict_groups = 0
    global_last_dates: dict[int, date] = {}
    for index, group in enumerate(groups):
        if max_groups is not None and index >= max_groups:
            break
        if window_days is not None:
            state.expire(group.cohort - timedelta(days=window_days))
        record_predictions = group.split in EVALUATION_SPLITS
        if (
            record_predictions
            and max_predict_groups is not None
            and predict_groups >= max_predict_groups
        ):
            record_predictions = False
        if record_predictions:
            predictions.extend(
                _predict_group(
                    state,
                    group,
                    variant=variant,
                    window_days=window_days,
                    config=config,
                    record_predictions=True,
                    global_last_dates=global_last_dates,
                )
            )
            predict_groups += 1
        state.activate(group)
        for match in group.matches:
            for player_id in (match.frozen.player_1_id, match.frozen.player_2_id):
                prior = global_last_dates.get(player_id)
                if prior is None or match.frozen.match_date > prior:
                    global_last_dates[player_id] = match.frozen.match_date
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for record in predictions:
            handle.write(_canonical(record) + "\n")
    return {
        "variant": variant,
        "window_days": window_days,
        "dataset_manifest": manifest,
        "eligible_matches": len(matches),
        "groups": len(groups),
        "prediction_records": len(predictions),
        "runtime_seconds": round(time.time() - started, 2),
    }


def _run_worker(payload: dict) -> dict:
    return run_variant(
        Path(payload["dataset_path"]),
        variant=payload["variant"],
        window_days=payload["window_days"],
        output_path=Path(payload["output_path"]),
        max_groups=payload.get("max_groups"),
        max_predict_groups=payload.get("max_predict_groups"),
    )


def run_all(
    dataset_path: Path,
    output_dir: Path,
    *,
    variants: Sequence[tuple[str, int | None]] = VARIANTS,
    parallel: bool = True,
    max_groups: int | None = None,
    max_predict_groups: int | None = None,
) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = [
        {
            "dataset_path": str(dataset_path),
            "variant": variant,
            "window_days": window_days,
            "output_path": str(output_dir / f"records_{variant}.jsonl"),
            "max_groups": max_groups,
            "max_predict_groups": max_predict_groups,
        }
        for variant, window_days in variants
    ]
    if parallel and len(payloads) > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=min(len(payloads), os.cpu_count() or 1)) as pool:
            return list(pool.map(_run_worker, payloads))
    return [_run_worker(payload) for payload in payloads]


def load_records(output_dir: Path, variants: Sequence[tuple[str, int | None]] = VARIANTS) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for variant, _ in variants:
        path = output_dir / f"records_{variant}.jsonl"
        with path.open("r", encoding="utf-8") as handle:
            result[variant] = [json.loads(line) for line in handle if line.strip()]
    return result


def _git_sha() -> str:
    try:
        import subprocess

        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("tmp/a2_form_lookback"))
    parser.add_argument(
        "--mode",
        choices=("run", "analyze", "run-and-analyze"),
        default="run-and-analyze",
    )
    parser.add_argument("--reps", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--no-parallel", action="store_true")
    parser.add_argument("--max-groups", type=int, default=None)
    parser.add_argument("--max-predict-groups", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.time()
    if args.mode in ("run", "run-and-analyze"):
        summaries = run_all(
            args.dataset,
            args.output_dir,
            parallel=not args.no_parallel,
            max_groups=args.max_groups,
            max_predict_groups=args.max_predict_groups,
        )
        print(json.dumps({"summaries": summaries}, indent=2, default=str))
    if args.mode in ("analyze", "run-and-analyze"):
        records_by_variant = load_records(args.output_dir)
        records, manifest = load_dataset(args.dataset)
        matches = eligible_matches(records)
        groups = build_groups(matches)
        analysis = analyze_records(
            records_by_variant,
            manifest=manifest,
            population_sha=population_sha256(matches),
            population_count=len(matches),
            groups=len(groups),
            reps=args.reps,
            seed=args.seed,
            runtime_seconds=round(time.time() - started, 2),
        )
        analysis["git_sha"] = _git_sha()
        analysis["numpy_version"] = None if np is None else np.__version__
        output_path = args.output_dir / "analysis.json"
        output_path.write_text(json.dumps(analysis, indent=2, default=str), encoding="utf-8")
        print(f"analysis={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
