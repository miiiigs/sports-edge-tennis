from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

import sports_edge_tennis.validation.agent1_historical_replay as replay_mod
from sports_edge_tennis.agents.agent1_surface_elo import HistoricalMatchObservation
from sports_edge_tennis.validation.agent1_historical_replay import (
    _anchor,
    _load_observations_read_only,
    _replay,
    _score,
)

MATCH_START = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
FETCH_TIME = datetime(2026, 9, 22, 22, 32, tzinfo=UTC)


def _observation(match_id: int, *, start=MATCH_START, status="COMPLETED", winner=1) -> HistoricalMatchObservation:
    return HistoricalMatchObservation(
        match_id=match_id,
        player_1_id=match_id * 10 + 1,
        player_2_id=match_id * 10 + 2,
        surface="HARD",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        best_of_sets=3,
        qualifying_flag=False,
        match_start_at=start,
        outcome_status=status,
        winner_side=winner,
        source_observed_at=FETCH_TIME,
        as_of=FETCH_TIME,
    )


def test_anchor_uses_stored_visibility_in_strict_and_start_in_retrospective() -> None:
    observation = _observation(1)
    assert _anchor(observation, "strict") == FETCH_TIME
    assert _anchor(observation, "retrospective") == MATCH_START


def test_replay_produces_prediction_per_observation_without_leakage() -> None:
    observations = [
        _observation(1, start=datetime(2026, 9, 10, 12, 0, tzinfo=UTC)),
        _observation(2, start=datetime(2026, 9, 11, 12, 0, tzinfo=UTC)),
        _observation(3, start=datetime(2026, 9, 12, 12, 0, tzinfo=UTC)),
    ]
    records = _replay(observations, mode="retrospective")
    assert len(records) == 3
    # Same-cohort freeze: two matches at the same instant must not see each other.
    same = [
        _observation(1, start=MATCH_START),
        _observation(2, start=MATCH_START),
    ]
    frozen = _replay(same, mode="retrospective")
    assert [result.player_1_probability for _obs, result in frozen]
    assert len({result.player_1_probability for _obs, result in frozen}) == 1


@dataclass
class _FakeDiagnostics:
    unknown_surface: bool = False
    player_1_cold_start: bool = False
    player_2_cold_start: bool = False
    abstain_reason: str | None = None


@dataclass
class _FakeResult:
    prediction_status: str
    selection_side: int | None
    diagnostics: _FakeDiagnostics


def _record(day: int, *, side: int, predicted: bool = True, winner: int | None = None) -> tuple[HistoricalMatchObservation, object]:
    observation = _observation(
        1,
        start=datetime(2026, 9, day, 12, 0, tzinfo=UTC),
        winner=side if winner is None else winner,
    )
    if predicted:
        return observation, _FakeResult("PREDICTED", side, _FakeDiagnostics())
    return observation, _FakeResult(
        "ABSTAINED", None, _FakeDiagnostics(unknown_surface=True, abstain_reason="UNKNOWN_SURFACE")
    )


def test_score_filters_by_window_and_reports_gaps() -> None:
    records = [
        _record(11, side=1, predicted=True),
        _record(15, side=2, predicted=True, winner=1),
        _record(20, side=1, predicted=False),
        _record(25, side=1, predicted=True),
    ]
    score = _score(records, evaluation_start=date(2026, 9, 10), evaluation_end=date(2026, 9, 21))
    assert score["sample"] == 3
    assert score["predicted"] == 2
    assert score["abstained"] == 1
    assert score["correct"] == 1
    assert score["unknown_surface"] == 1
    assert score["abstain_reasons"] == {"UNKNOWN_SURFACE": 1}


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
    monkeypatch.setattr(replay_mod, "load_agent1_history", lambda *a, **k: [])

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
    for forbidden in ("INSERT", "UPDATE", "DELETE"):
        assert forbidden not in joined


def test_load_fails_closed_when_not_read_only(monkeypatch) -> None:
    conn = _FakeConn("off")
    monkeypatch.setattr(replay_mod.psycopg, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(replay_mod, "load_agent1_history", lambda *a, **k: [])

    with pytest.raises(RuntimeError, match="read_only"):
        _load_observations_read_only(
            "postgresql://unused",
            cutoff=datetime(2026, 9, 23, tzinfo=UTC),
            year_start=2016,
        )
