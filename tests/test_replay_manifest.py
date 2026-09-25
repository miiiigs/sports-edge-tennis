from __future__ import annotations

from datetime import date

from replay_helpers import model_pick, run_result, utc, value_bet

from sports_edge_tennis.replay.contracts import (
    REPLAY_ID,
    REPLAY_SCHEMA_VERSION,
    OpportunityRecord,
    ReplayExclusion,
    ReplayResult,
)
from sports_edge_tennis.replay.manifest import (
    ManifestMetadata,
    ReplayVersionSet,
    build_manifest,
    reproducibility_sha256,
)

VERSIONS = ReplayVersionSet(
    a1_config_version="a1-v1",
    a2_config_version="a2-v1",
    a3_config_version="a3-v1",
    manager_policy_version="v1",
    market_policy_version="v1",
    veto_policy_version="tennis_veto_v1",
    staking_rule_version="QUARTER_KELLY_CAP_2_V1",
)


def _metadata(generated_at=None, snapshot_ids=("snap-a", "snap-b")):
    return ManifestMetadata(
        git_sha="deadbeef",
        generated_at=generated_at or utc(20),
        historical_range_start=date(2026, 9, 10),
        historical_range_end=date(2026, 9, 24),
        input_snapshot_ids=snapshot_ids,
    )


def _result(*, extra_pick=None) -> ReplayResult:
    pick = model_pick(match_id=1)
    bet = value_bet(match_id=1, actual_result="WIN", units=None)
    picks = (pick,) if extra_pick is None else (pick, extra_pick)
    exclusions = (
        ReplayExclusion(sporting_match_id=2, reason="IDENTITY_BLOCKED"),
        ReplayExclusion(sporting_match_id=3, reason="SURFACE_BLOCKED"),
        ReplayExclusion(sporting_match_id=4, reason="MISSING_MARKET_ODDS"),
    )
    opportunities = (
        OpportunityRecord(sporting_match_id=1, run_id="run-1", outcome="QUALIFIED_VALUE_BET"),
        OpportunityRecord(
            sporting_match_id=5, run_id="run-1", outcome="DATA_NOT_AVAILABLE_PIT"
        ),
    )
    return ReplayResult(
        replay_id=REPLAY_ID,
        runs=(run_result(1, model_picks=picks, value_bets=(bet,)),),
        model_picks=picks,
        value_bets=(bet,),
        exclusions=exclusions,
        opportunities=opportunities,
        provisional=True,
        provisional_label="PROVISIONAL - HISTORICAL RECOVERY SNAPSHOT NOT FROZEN",
    )


def test_manifest_counts_and_provisional_label():
    manifest = build_manifest(
        _result(),
        metadata=_metadata(),
        versions=VERSIONS,
        replay_schema_version=REPLAY_SCHEMA_VERSION,
    )
    assert manifest.replay_id == REPLAY_ID
    assert manifest.model_pick_rows_generated == 1
    assert manifest.first_qualified_model_picks == 1
    assert manifest.value_bet_rows_generated == 1
    assert manifest.first_qualified_value_bets == 1
    assert manifest.matches_excluded == 3
    assert manifest.identity_failures == 1
    assert manifest.surface_failures == 1
    assert manifest.missing_historical_odds == 1
    assert manifest.pit_incomplete_cases == 1
    assert manifest.data_not_available_pit_count == 1 + 3
    assert manifest.provisional is True
    assert manifest.provisional_label == "PROVISIONAL - HISTORICAL RECOVERY SNAPSHOT NOT FROZEN"


def test_reproducibility_hash_ignores_wall_clock_but_tracks_ledger():
    first = build_manifest(
        _result(),
        metadata=_metadata(generated_at=utc(20)),
        versions=VERSIONS,
        replay_schema_version=REPLAY_SCHEMA_VERSION,
    )
    later = build_manifest(
        _result(),
        metadata=_metadata(generated_at=utc(21)),
        versions=VERSIONS,
        replay_schema_version=REPLAY_SCHEMA_VERSION,
    )
    assert reproducibility_sha256(first) == reproducibility_sha256(later)

    changed = build_manifest(
        _result(extra_pick=model_pick(match_id=2)),
        metadata=_metadata(),
        versions=VERSIONS,
        replay_schema_version=REPLAY_SCHEMA_VERSION,
    )
    assert reproducibility_sha256(changed) != reproducibility_sha256(first)
    assert changed.ledger_sha256 != first.ledger_sha256
