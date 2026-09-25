"""Replay manifest construction and reproducibility hashing.

A manifest records exactly what produced a replay execution so that the same
replay version plus the same input snapshot can be re-run and compared. The
reproducibility hash deliberately excludes wall-clock metadata (``generated_at``)
so two identical replays hash identically.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping

from sports_edge_tennis.replay.contracts import ReplayResult
from sports_edge_tennis.replay.first_qualified import (
    first_qualified_model_picks,
    first_qualified_value_bets,
)
from sports_edge_tennis.replay.serde import canonical_json, payload_sha256, sha256_hex


@dataclass(frozen=True)
class ReplayVersionSet:
    a1_config_version: str
    a2_config_version: str
    a3_config_version: str
    manager_policy_version: str
    market_policy_version: str
    veto_policy_version: str
    staking_rule_version: str
    identity_pit_policy_version: str = "identity_pit_v1"
    surface_pit_policy_version: str = "surface_pit_v1"


@dataclass(frozen=True)
class ManifestMetadata:
    git_sha: str
    generated_at: datetime
    historical_range_start: date
    historical_range_end: date
    input_snapshot_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReplayManifest:
    replay_id: str
    replay_schema_version: str
    git_sha: str
    generated_at: datetime
    historical_range_start: date
    historical_range_end: date
    input_snapshot_ids: tuple[str, ...]
    a1_config_version: str
    a2_config_version: str
    a3_config_version: str
    manager_version: str
    market_version: str
    veto_version: str
    staking_version: str
    identity_pit_policy_version: str
    surface_pit_policy_version: str
    run_count: int
    matches_considered: int
    matches_excluded: int
    exclusion_reason_counts: Mapping[str, int]
    model_pick_rows_generated: int
    first_qualified_model_picks: int
    value_bet_rows_generated: int
    first_qualified_value_bets: int
    pit_incomplete_cases: int
    missing_historical_odds: int
    identity_failures: int
    surface_failures: int
    data_not_available_pit_count: int
    provisional: bool
    provisional_label: str | None
    ledger_sha256: str


def _distinct_matches(result: ReplayResult) -> set[int]:
    return {
        match.sporting_match_id
        for run in result.runs
        for match in run.model_picks + run.exclusions
    } | {exclusion.sporting_match_id for exclusion in result.exclusions}


def build_manifest(
    result: ReplayResult,
    *,
    metadata: ManifestMetadata,
    versions: ReplayVersionSet,
    replay_schema_version: str,
) -> ReplayManifest:
    exclusion_counts = Counter(exclusion.reason for exclusion in result.exclusions)

    model_pick_count = len(result.model_picks)
    first_model_picks = first_qualified_model_picks(result.model_picks)
    value_bet_count = len(result.value_bets)
    first_value_bets = first_qualified_value_bets(result.value_bets)

    pit_incomplete = sum(
        1 for opportunity in result.opportunities if opportunity.outcome == "DATA_NOT_AVAILABLE_PIT"
    )
    missing_odds = exclusion_counts["MISSING_MARKET_ODDS"] + exclusion_counts[
        "SHARP_REFERENCE_UNAVAILABLE_PIT"
    ]
    identity_failures = exclusion_counts["IDENTITY_BLOCKED"] + exclusion_counts[
        "IDENTITY_NOT_AVAILABLE_PIT"
    ]
    surface_failures = (
        exclusion_counts["SURFACE_BLOCKED"]
        + exclusion_counts["SURFACE_NOT_AVAILABLE_PIT"]
        + exclusion_counts["SURFACE_CONFLICT_AT_T"]
    )

    ledger_payload = {
        "model_picks": result.model_picks,
        "value_bets": result.value_bets,
        "exclusions": result.exclusions,
        "opportunities": result.opportunities,
    }
    ledger_sha256 = payload_sha256(ledger_payload)

    return ReplayManifest(
        replay_id=result.replay_id,
        replay_schema_version=replay_schema_version,
        git_sha=metadata.git_sha,
        generated_at=metadata.generated_at,
        historical_range_start=metadata.historical_range_start,
        historical_range_end=metadata.historical_range_end,
        input_snapshot_ids=metadata.input_snapshot_ids,
        a1_config_version=versions.a1_config_version,
        a2_config_version=versions.a2_config_version,
        a3_config_version=versions.a3_config_version,
        manager_version=versions.manager_policy_version,
        market_version=versions.market_policy_version,
        veto_version=versions.veto_policy_version,
        staking_version=versions.staking_rule_version,
        identity_pit_policy_version=versions.identity_pit_policy_version,
        surface_pit_policy_version=versions.surface_pit_policy_version,
        run_count=len(result.runs),
        matches_considered=len(_distinct_matches(result)),
        matches_excluded=len({exclusion.sporting_match_id for exclusion in result.exclusions}),
        exclusion_reason_counts=dict(sorted(exclusion_counts.items())),
        model_pick_rows_generated=model_pick_count,
        first_qualified_model_picks=len(first_model_picks),
        value_bet_rows_generated=value_bet_count,
        first_qualified_value_bets=len(first_value_bets),
        pit_incomplete_cases=pit_incomplete,
        missing_historical_odds=missing_odds,
        identity_failures=identity_failures,
        surface_failures=surface_failures,
        data_not_available_pit_count=pit_incomplete + len(result.exclusions),
        provisional=result.provisional,
        provisional_label=result.provisional_label,
        ledger_sha256=ledger_sha256,
    )


def reproducibility_sha256(manifest: ReplayManifest) -> str:
    """Hash the deterministic manifest content, excluding wall-clock metadata."""

    payload = dataclasses.asdict(manifest)
    payload.pop("generated_at", None)
    return payload_sha256(payload)


def manifest_canonical_json(manifest: ReplayManifest) -> str:
    return canonical_json(manifest)


__all__ = [
    "ReplayVersionSet",
    "ManifestMetadata",
    "ReplayManifest",
    "build_manifest",
    "reproducibility_sha256",
    "manifest_canonical_json",
    "sha256_hex",
]
