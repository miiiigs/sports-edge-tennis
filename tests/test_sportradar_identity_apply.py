from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from sports_edge_tennis.validation import sportradar_identity_apply as apply_module
from sports_edge_tennis.validation.sportradar_identity_apply import (
    DEFAULT_ARTIFACT,
    EXPECTED_ARTIFACT_SHA256,
    build_plan,
    event_key,
    load_candidates,
    sha256_file,
)


def test_reviewed_artifact_hash_and_plan_are_locked():
    assert DEFAULT_ARTIFACT.exists()
    assert sha256_file(DEFAULT_ARTIFACT) == EXPECTED_ARTIFACT_SHA256

    rows = load_candidates(DEFAULT_ARTIFACT)
    plan = build_plan(rows)

    assert len(rows) == 480
    assert len(plan.eligible) == 477
    assert len(plan.blocked) == 3
    assert plan.source_to_players["sr:competitor:328985"] == frozenset({5811, 5988})
    assert {row.match_id for row in plan.blocked} == {130280, 130357, 130716}


def test_reviewed_apply_set_has_unique_events_and_player_bindings():
    plan = build_plan(load_candidates(DEFAULT_ARTIFACT))

    events = {event_key(row.event_id) for row in plan.eligible}
    assert len(events) == 477

    source_to_player: dict[str, int] = {}
    player_to_source: dict[int, str] = {}
    for row in plan.eligible:
        for player_id, source_id in (
            (row.player_1_id, row.player_1_source_id),
            (row.player_2_id, row.player_2_source_id),
        ):
            assert source_to_player.setdefault(source_id, player_id) == player_id
            assert player_to_source.setdefault(player_id, source_id) == source_id

    assert len(source_to_player) == 461


def test_event_key_accepts_prefixed_and_bare_betradar_ids():
    assert event_key("sr:sport_event:123") == "123"
    assert event_key("123") == "123"


def test_apply_requires_explicit_enable_before_database_connection(tmp_path: Path):
    with pytest.raises(RuntimeError, match="SPORTRADAR_TRIPLE_EXACT_APPLY_ENABLED"):
        apply_module.apply_triple_exact_backfill(
            dsn="postgresql://not-used",
            artifact_path=DEFAULT_ARTIFACT,
            env={},
        )


def test_apply_source_is_limited_to_source_identity_tables():
    source = inspect.getsource(apply_module.apply_triple_exact_backfill).lower()
    assert "insert into match_source_ids" in source
    assert "insert into player_source_ids" in source
    assert "insert into matches" not in source
    assert "insert into players" not in source
    assert "update " not in source
    assert "delete " not in source
