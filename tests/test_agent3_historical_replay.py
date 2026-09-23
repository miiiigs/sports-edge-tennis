from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

import sports_edge_tennis.validation.agent3_historical_replay as replay_mod
from sports_edge_tennis.data.agent3_history import Agent3HistoricalMatch
from sports_edge_tennis.validation.agent3_historical_replay import (
    _as_retrospective,
    _load_observations_read_only,
    _score,
)

MATCH_START = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
FETCH_TIME = datetime(2026, 9, 22, 22, 32, tzinfo=UTC)


def _observation(*, match_id: int, match_start_at=MATCH_START) -> Agent3HistoricalMatch:
    return Agent3HistoricalMatch(
        match_id=match_id,
        player_1_id=11,
        player_2_id=22,
        match_start_at=match_start_at,
        surface="HARD",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        outcome_status="COMPLETED",
        winner_side=1,
        result_source_observed_at=FETCH_TIME,
        result_as_of=FETCH_TIME,
        version_source_observed_at=MATCH_START,
        version_as_of=MATCH_START,
    )


def test_retrospective_mode_reorders_by_match_chronology() -> None:
    observation = _observation(match_id=1)
    (reordered,) = _as_retrospective([observation])
    assert reordered.result_source_observed_at == MATCH_START
    assert reordered.result_as_of == MATCH_START
    assert observation.result_source_observed_at == FETCH_TIME


def test_retrospective_mode_handles_missing_match_start() -> None:
    observation = _observation(match_id=1, match_start_at=None)
    (reordered,) = _as_retrospective([observation])
    assert reordered.result_source_observed_at == FETCH_TIME


@dataclass
class _FakeResult:
    prediction_status: str
    selected_side: int | None
    abstain_reason: str | None


@dataclass
class _FakeRecord:
    observation: Agent3HistoricalMatch
    result: _FakeResult


def _record(*, match_id, day, predicted, side=1, reason="INSUFFICIENT_MATCHUP_SUPPORT"):
    observation = _observation(
        match_id=match_id,
        match_start_at=datetime(2026, 9, day, 12, 0, tzinfo=UTC),
    )
    return _FakeRecord(
        observation=observation,
        result=_FakeResult(
            prediction_status="PREDICTED" if predicted else "ABSTAINED",
            selected_side=side if predicted else None,
            abstain_reason=None if predicted else reason,
        ),
    )


def test_score_filters_by_window_and_counts_abstentions() -> None:
    records = [
        _record(match_id=1, day=12, predicted=True, side=1),
        _record(match_id=2, day=15, predicted=True, side=2),
        _record(match_id=3, day=20, predicted=False),
        _record(match_id=4, day=25, predicted=True, side=1),
    ]
    score = _score(records, evaluation_start=date(2026, 9, 10), evaluation_end=date(2026, 9, 21))
    assert score["sample"] == 3
    assert score["predicted"] == 2
    assert score["abstained"] == 1
    assert score["correct"] == 1
    assert score["accuracy"] == 0.5
    assert score["abstain_reasons"] == {"INSUFFICIENT_MATCHUP_SUPPORT": 1}


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
    monkeypatch.setattr(replay_mod, "load_agent3_history", lambda *a, **k: [])

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
    monkeypatch.setattr(replay_mod, "load_agent3_history", lambda *a, **k: [])

    with pytest.raises(RuntimeError, match="read_only"):
        _load_observations_read_only(
            "postgresql://unused",
            cutoff=datetime(2026, 9, 23, tzinfo=UTC),
            year_start=2016,
        )
