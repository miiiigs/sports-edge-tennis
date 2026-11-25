from decimal import Decimal

import pytest

from sports_edge_tennis.agents.agent1_surface_elo import PlayerRatingState
from sports_edge_tennis.data.feature_snapshots import (
    FeatureSnapshotError,
    build_agent1_feature_snapshot,
    persist_feature_snapshot,
)


def _state(player_id: int, *, overall_count: int = 12, surface_count: int = 4) -> PlayerRatingState:
    return PlayerRatingState(
        player_id=player_id,
        overall_rating=Decimal("1512.5"),
        overall_match_count=overall_count,
        surface_ratings={"HARD": Decimal("1495.25")},
        surface_match_counts={"HARD": surface_count},
    )


def _build(**overrides):
    kwargs = dict(
        prediction_id=11,
        prediction_run_id=22,
        match_id=33,
        player_1=_state(101),
        player_2=_state(202),
        surface="HARD",
        qualifying_flag=False,
        model_version="agent1_surface_elo_v1",
        feature_version="agent1_surface_elo_inputs_v1",
        as_of=__import__("datetime").datetime(2026, 11, 20, 10, 0, tzinfo=__import__("datetime").UTC),
        prediction_locked_at=__import__("datetime").datetime(
            2026, 11, 20, 10, 5, tzinfo=__import__("datetime").UTC
        ),
    )
    kwargs.update(overrides)
    return build_agent1_feature_snapshot(**kwargs)


def test_snapshot_records_exact_available_features():
    snapshot = _build()
    assert snapshot.player_1_overall_rating == Decimal("1512.5")
    assert snapshot.player_1_overall_match_count == 12
    assert snapshot.player_1_surface_rating == Decimal("1495.25")
    assert snapshot.player_1_surface_match_count == 4
    assert snapshot.identity_complete is True
    assert snapshot.surface_known is True
    assert snapshot.main_draw_verified is True
    # Not computed by Agent 1, therefore never fabricated.
    assert snapshot.rating_uncertainty is None
    assert snapshot.rating_uncertainty_source is None


def test_cold_start_is_not_identity_complete():
    snapshot = _build(player_1=_state(101, overall_count=0, surface_count=0))
    assert snapshot.identity_complete is False
    assert snapshot.player_1_overall_match_count == 0


def test_unknown_surface_keeps_surface_fields_unset():
    snapshot = _build(surface=None)
    assert snapshot.surface_known is False
    assert snapshot.surface is None
    assert snapshot.player_1_surface_rating is None
    assert snapshot.player_1_surface_match_count is None
    assert snapshot.identity_complete is True


def test_qualifying_fixture_is_not_verified_main_draw():
    snapshot = _build(qualifying_flag=True)
    assert snapshot.main_draw_verified is False


def test_uncertainty_requires_a_source():
    with pytest.raises(FeatureSnapshotError):
        _build(rating_uncertainty=Decimal("0.1"))
    with pytest.raises(FeatureSnapshotError):
        _build(rating_uncertainty_source="unlinked")


def test_uncertainty_is_stored_when_genuinely_supplied():
    snapshot = _build(
        rating_uncertainty=Decimal("0.125"),
        rating_uncertainty_source="agent1_replay_standard_error_v1",
    )
    assert snapshot.rating_uncertainty == Decimal("0.125")


def test_as_of_after_lock_is_rejected():
    import datetime as dt

    with pytest.raises(FeatureSnapshotError):
        _build(
            as_of=dt.datetime(2026, 11, 20, 10, 6, tzinfo=dt.UTC),
            prediction_locked_at=dt.datetime(2026, 11, 20, 10, 5, tzinfo=dt.UTC),
        )


def test_unsupported_surface_is_rejected():
    with pytest.raises(FeatureSnapshotError):
        _build(surface="WOOD")


def test_same_player_twice_is_rejected():
    state = _state(101)
    with pytest.raises(FeatureSnapshotError):
        _build(player_1=state, player_2=state)


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return _FakeResult(self.row)


def test_persist_writes_one_row_and_returns_id():
    conn = _FakeConn((77,))
    snapshot = _build()
    assert persist_feature_snapshot(conn, snapshot) == 77
    sql, params = conn.calls[0]
    assert "prediction_feature_snapshots" in sql
    assert params[0] == 11
    assert params[3] == "AGENT_1_SURFACE_ELO"


def test_persist_fails_closed_without_id():
    conn = _FakeConn(None)
    with pytest.raises(FeatureSnapshotError):
        persist_feature_snapshot(conn, _build())


def test_migration_enforces_append_only_and_nullable_uncertainty():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sql = (root / "db" / "migrations" / "0023_prediction_feature_snapshots.up.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE TABLE prediction_feature_snapshots" in sql
    assert "UNIQUE" in sql and "prediction_id" in sql
    assert "trg_prediction_feature_snapshots_append_only_mutation" in sql
    assert "raise_append_only_mutation" in sql
    assert "rating_uncertainty NUMERIC(12, 6)" in sql
