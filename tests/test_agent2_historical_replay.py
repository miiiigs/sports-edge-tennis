from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

import sports_edge_tennis.validation.agent2_historical_replay as replay_mod
from sports_edge_tennis.data.agent2_history import (
    Agent2HistoricalMatch,
    Agent2MatchStatRow,
)
from sports_edge_tennis.validation.agent2_historical_replay import (
    _as_retrospective,
    _load_observations_read_only,
    _prune_inactive,
    _score,
    _without_sportradar_stats,
)

MATCH_START = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
FETCH_TIME = datetime(2026, 9, 22, 22, 32, tzinfo=UTC)


def _stat_row(*, source_system: str, player_id: int) -> Agent2MatchStatRow:
    return Agent2MatchStatRow(
        match_stat_observation_id=player_id,
        player_id=player_id,
        source_system=source_system,
        source_observed_at=FETCH_TIME,
        as_of=FETCH_TIME,
        first_serve_points_won=20,
        second_serve_points_won=10,
        service_points_played=50,
        return_points_won=18,
    )


def _observation(*, match_id: int, stat_rows=(), match_start_at=MATCH_START) -> Agent2HistoricalMatch:
    return Agent2HistoricalMatch(
        match_id=match_id,
        player_1_id=11,
        player_2_id=22,
        match_start_at=match_start_at,
        surface="HARD",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        tournament_id=1,
        canonical_tournament_name="Test Challenger",
        normalized_tournament_alias="TEST CHALLENGER",
        season_year=2026,
        outcome_status="COMPLETED",
        winner_side=1,
        result_source_observed_at=FETCH_TIME,
        result_as_of=FETCH_TIME,
        version_source_observed_at=MATCH_START,
        version_as_of=MATCH_START,
        stat_rows=tuple(stat_rows),
    )


def test_retrospective_mode_reorders_by_match_chronology() -> None:
    observation = _observation(match_id=1)
    (reordered,) = _as_retrospective([observation])
    assert reordered.match_start_at == MATCH_START
    assert reordered.result_source_observed_at == MATCH_START
    assert reordered.result_as_of == MATCH_START
    assert reordered.visibility_cohort == MATCH_START
    # Original observation is untouched (research simulation is in memory only).
    assert observation.result_source_observed_at == FETCH_TIME


def test_retrospective_mode_handles_missing_match_start() -> None:
    observation = _observation(match_id=1, match_start_at=None)
    (reordered,) = _as_retrospective([observation])
    # Falls back to the stored visibility so the ordering key is never null.
    assert reordered.result_source_observed_at == FETCH_TIME
    assert reordered.result_as_of == FETCH_TIME
    assert reordered.visibility_cohort == FETCH_TIME


def test_without_sportradar_stats_drops_only_sportradar_rows() -> None:
    observation = _observation(
        match_id=1,
        stat_rows=[
            _stat_row(source_system="SPORTRADAR", player_id=11),
            _stat_row(source_system="API_TENNIS", player_id=11),
        ],
    )
    (stripped,) = _without_sportradar_stats([observation])
    assert [row.source_system for row in stripped.stat_rows] == ["API_TENNIS"]


def test_prune_inactive_keeps_window_and_state_affecting_observations() -> None:
    window = _observation(match_id=1, match_start_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC))
    statless_nonwindow = _observation(match_id=2, match_start_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    eligible_nonwindow = _observation(
        match_id=3,
        match_start_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        stat_rows=[
            _stat_row(source_system="SPORTRADAR", player_id=11),
            _stat_row(source_system="SPORTRADAR", player_id=22),
        ],
    )
    kept = _prune_inactive(
        [window, statless_nonwindow, eligible_nonwindow],
        evaluation_start=date(2026, 9, 10),
        evaluation_end=date(2026, 9, 21),
    )
    assert {observation.match_id for observation in kept} == {1, 3}


def test_prediction_filter_preserves_window_predictions() -> None:
    from dataclasses import replace as dc_replace

    from sports_edge_tennis.data.agent2_history import replay_historical_predictions

    def build(match_id: int, day: int) -> Agent2HistoricalMatch:
        ts = datetime(2026, 9, day, 12, 0, tzinfo=UTC)
        base = _observation(
            match_id=match_id,
            match_start_at=ts,
            stat_rows=[
                _stat_row(source_system="SPORTRADAR", player_id=11),
                _stat_row(source_system="SPORTRADAR", player_id=22),
            ],
        )
        return dc_replace(base, result_source_observed_at=ts, result_as_of=ts)

    observations = [build(1, 10), build(2, 11), build(3, 20)]
    full = {
        record.observation.match_id: record.result
        for record in replay_historical_predictions(observations).records
    }
    filtered = replay_historical_predictions(
        observations,
        prediction_filter=lambda observation: (
            date(2026, 9, 20)
            <= (observation.match_start_at or observation.result_source_observed_at).date()
            <= date(2026, 9, 21)
        ),
    ).records
    assert [record.observation.match_id for record in filtered] == [3]
    for record in filtered:
        reference = full[record.observation.match_id]
        assert record.result.selection_side == reference.selection_side
        assert record.result.win_probability == reference.win_probability


@dataclass
class _FakeResult:
    prediction_status: str
    selection_side: int | None
    abstain_reason: str | None


@dataclass
class _FakeDiagnostics:
    abstain_reason: str | None


@dataclass
class _FakeRecord:
    observation: Agent2HistoricalMatch
    result: object


def _record(*, match_id, day, predictor, winner=1, predicted=False, side=1, reason="INSUFFICIENT_HISTORY"):
    observation = _observation(
        match_id=match_id,
        match_start_at=datetime(2026, 9, day, 12, 0, tzinfo=UTC),
    )
    object.__setattr__(observation, "winner_side", winner)
    result = _FakeResult(
        prediction_status="PREDICTED" if predicted else "ABSTAINED",
        selection_side=side if predicted else None,
        abstain_reason=None if predicted else reason,
    )
    result.diagnostics = _FakeDiagnostics(abstain_reason=None if predicted else reason)
    return _FakeRecord(observation=observation, result=result)


def test_score_filters_by_window_and_counts_abstentions() -> None:
    records = [
        _record(match_id=1, day=12, predictor=True, predicted=True, side=1, winner=1),
        _record(match_id=2, day=15, predictor=True, predicted=True, side=2, winner=1),
        _record(match_id=3, day=20, predictor=False, reason="INSUFFICIENT_HISTORY"),
        _record(match_id=4, day=25, predictor=True, predicted=True, side=1, winner=1),
    ]
    score = _score(
        records,
        evaluation_start=date(2026, 9, 10),
        evaluation_end=date(2026, 9, 21),
    )
    assert score["sample"] == 3
    assert score["predicted"] == 2
    assert score["abstained"] == 1
    assert score["correct"] == 1
    assert score["incorrect"] == 1
    assert score["accuracy"] == 0.5
    assert score["abstain_reasons"] == {"INSUFFICIENT_HISTORY": 1}


class _FakeConn:
    def __init__(self, read_only: str) -> None:
        self.statements: list[str] = []
        self.rolled_back = False
        self._read_only = read_only

    def execute(self, sql, params=None):
        self.statements.append(str(sql))
        return self

    def fetchone(self):
        return (self._read_only,)

    def rollback(self) -> None:
        self.rolled_back = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_load_enforces_read_only_transaction(monkeypatch) -> None:
    conn = _FakeConn("on")
    monkeypatch.setattr(replay_mod.psycopg, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(replay_mod, "load_agent2_history", lambda *a, **k: [])

    observations, read_only, _seconds = _load_observations_read_only(
        "postgresql://unused",
        cutoff=datetime(2026, 9, 23, tzinfo=UTC),
        year_start=2016,
    )

    assert observations == []
    assert read_only == "on"
    assert conn.rolled_back is True
    joined = " ".join(conn.statements).upper()
    assert "SET TRANSACTION READ ONLY" in joined
    assert "SHOW TRANSACTION_READ_ONLY" in joined
    for forbidden in ("INSERT", "UPDATE", "DELETE"):
        assert forbidden not in joined


def test_load_fails_closed_when_not_read_only(monkeypatch) -> None:
    conn = _FakeConn("off")
    monkeypatch.setattr(replay_mod.psycopg, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(replay_mod, "load_agent2_history", lambda *a, **k: [])

    with pytest.raises(RuntimeError, match="read_only"):
        _load_observations_read_only(
            "postgresql://unused",
            cutoff=datetime(2026, 9, 23, tzinfo=UTC),
            year_start=2016,
        )
