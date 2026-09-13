from __future__ import annotations

from datetime import date

import pytest

from sports_edge_tennis.live.history_refresh import HistoryRefreshChunkOutcome
from sports_edge_tennis.live.history_refresh_resilient import (
    _load_checkpoint,
    run_resilient_history_refresh,
)
from sports_edge_tennis.live.transport import TransportError


def _outcome(start_date: date, end_date: date) -> HistoryRefreshChunkOutcome:
    return HistoryRefreshChunkOutcome(
        start_date=start_date,
        end_date=end_date,
        fixtures_seen=0,
        fixtures_ingested=0,
        fixture_rejections=0,
        terminal_results_seen=0,
        results_inserted=0,
        results_unchanged=0,
        result_rejections=0,
    )


def test_transient_timeout_retries_then_commits_checkpoint(db):
    calls: list[tuple[date, date]] = []
    sleeps: list[float] = []

    def fake_refresh(conn, *, api_key, start_date, end_date):
        del conn, api_key
        calls.append((start_date, end_date))
        if len(calls) == 1:
            raise TransportError("TIMEOUT", "temporary provider timeout")
        return _outcome(start_date, end_date)

    totals = run_resilient_history_refresh(
        db,
        api_key="test-key",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        chunk_days=2,
        checkpoint_key="TEST_RETRY",
        max_attempts=2,
        backoff_seconds=0.25,
        restart_window=True,
        refresh_chunk=fake_refresh,
        sleep=sleeps.append,
    )

    assert calls == [
        (date(2026, 1, 1), date(2026, 1, 2)),
        (date(2026, 1, 1), date(2026, 1, 2)),
    ]
    assert sleeps == [0.25]
    assert totals.completed_chunks == 1

    checkpoint = _load_checkpoint(db, checkpoint_key="TEST_RETRY")
    assert checkpoint is not None
    assert checkpoint.next_start_date == date(2026, 1, 3)
    assert checkpoint.last_completed_end == date(2026, 1, 2)
    assert checkpoint.last_error_kind is None


def test_nonretryable_failure_resumes_at_first_uncompleted_chunk(db):
    first_calls: list[tuple[date, date]] = []

    def first_refresh(conn, *, api_key, start_date, end_date):
        del conn, api_key
        first_calls.append((start_date, end_date))
        if start_date == date(2026, 1, 3):
            raise TransportError("PARSE", "bad provider payload")
        return _outcome(start_date, end_date)

    with pytest.raises(TransportError) as exc_info:
        run_resilient_history_refresh(
            db,
            api_key="test-key",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 4),
            chunk_days=2,
            checkpoint_key="TEST_RESUME",
            max_attempts=3,
            restart_window=True,
            refresh_chunk=first_refresh,
            sleep=lambda _: None,
        )
    assert exc_info.value.kind == "PARSE"
    assert first_calls == [
        (date(2026, 1, 1), date(2026, 1, 2)),
        (date(2026, 1, 3), date(2026, 1, 4)),
    ]

    checkpoint = _load_checkpoint(db, checkpoint_key="TEST_RESUME")
    assert checkpoint is not None
    assert checkpoint.next_start_date == date(2026, 1, 3)
    assert checkpoint.last_completed_end == date(2026, 1, 2)
    assert checkpoint.last_error_kind == "PARSE"

    resumed_calls: list[tuple[date, date]] = []

    def resumed_refresh(conn, *, api_key, start_date, end_date):
        del conn, api_key
        resumed_calls.append((start_date, end_date))
        return _outcome(start_date, end_date)

    totals = run_resilient_history_refresh(
        db,
        api_key="test-key",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 4),
        chunk_days=2,
        checkpoint_key="TEST_RESUME",
        max_attempts=3,
        refresh_chunk=resumed_refresh,
        sleep=lambda _: None,
    )

    assert resumed_calls == [(date(2026, 1, 3), date(2026, 1, 4))]
    assert totals.completed_chunks == 1
    checkpoint = _load_checkpoint(db, checkpoint_key="TEST_RESUME")
    assert checkpoint is not None
    assert checkpoint.next_start_date == date(2026, 1, 5)
    assert checkpoint.last_error_kind is None


def test_persistent_timeout_adaptively_splits_to_minimum_chunk(db):
    calls: list[tuple[date, date]] = []

    def fake_refresh(conn, *, api_key, start_date, end_date):
        del conn, api_key
        calls.append((start_date, end_date))
        span_days = (end_date - start_date).days + 1
        if span_days > 1:
            raise TransportError("TIMEOUT", "window too large")
        return _outcome(start_date, end_date)

    totals = run_resilient_history_refresh(
        db,
        api_key="test-key",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 4),
        chunk_days=4,
        min_chunk_days=1,
        checkpoint_key="TEST_SPLIT",
        max_attempts=1,
        restart_window=True,
        refresh_chunk=fake_refresh,
        sleep=lambda _: None,
    )

    assert calls == [
        (date(2026, 1, 1), date(2026, 1, 4)),
        (date(2026, 1, 1), date(2026, 1, 2)),
        (date(2026, 1, 1), date(2026, 1, 1)),
        (date(2026, 1, 2), date(2026, 1, 2)),
        (date(2026, 1, 3), date(2026, 1, 4)),
        (date(2026, 1, 3), date(2026, 1, 3)),
        (date(2026, 1, 4), date(2026, 1, 4)),
    ]
    assert totals.completed_chunks == 4
    checkpoint = _load_checkpoint(db, checkpoint_key="TEST_SPLIT")
    assert checkpoint is not None
    assert checkpoint.next_start_date == date(2026, 1, 5)
    assert checkpoint.completed_chunks == 4
