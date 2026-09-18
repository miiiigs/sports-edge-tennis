from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from sports_edge_tennis.live.history_refresh import HistoryRefreshChunkOutcome
from sports_edge_tennis.live.history_refresh_resilient import (
    A2_REGIME_AUDIT_CODE_ENV,
    A2_REGIME_AUDIT_ENABLED_ENV,
    A2_STATS_DRY_RUN_CHUNK_DAYS_ENV,
    A2_STATS_DRY_RUN_DAYS_ENV,
    A2_STATS_DRY_RUN_END_DATE_ENV,
    A2_STATS_DRY_RUN_ENABLED_ENV,
    _load_checkpoint,
    _run_optional_a2_regime_audit,
    _run_optional_a2_stats_dry_run,
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


def test_optional_a2_stats_dry_run_is_disabled_by_default(db):
    calls = []

    _run_optional_a2_stats_dry_run(
        db,
        api_key="test-key",
        end_date=date(2026, 9, 17),
        env={},
        runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert calls == []


def test_optional_a2_stats_dry_run_dispatches_recent_bounded_window(db, capsys):
    calls = []

    def runner(conn, **kwargs):
        calls.append((conn, kwargs))
        return object()

    _run_optional_a2_stats_dry_run(
        db,
        api_key="test-key",
        end_date=date(2026, 9, 17),
        env={
            A2_STATS_DRY_RUN_ENABLED_ENV: "true",
            A2_STATS_DRY_RUN_DAYS_ENV: "7",
            A2_STATS_DRY_RUN_CHUNK_DAYS_ENV: "1",
        },
        runner=runner,
    )

    assert len(calls) == 1
    conn, kwargs = calls[0]
    assert conn is db
    assert kwargs == {
        "api_key": "test-key",
        "start_date": date(2026, 9, 11),
        "end_date": date(2026, 9, 17),
        "chunk_days": 1,
        "dry_run": True,
    }
    output = capsys.readouterr().out
    assert (
        "A2_STATS_BACKFILL_DRY_RUN_DISPATCH "
        "start=2026-09-11 end=2026-09-17 days=7 chunk_days=1 writes=0"
    ) in output


def test_optional_a2_stats_dry_run_rejects_large_window(db):
    with pytest.raises(ValueError, match="between 1 and 31"):
        _run_optional_a2_stats_dry_run(
            db,
            api_key="test-key",
            end_date=date(2026, 9, 17),
            env={
                A2_STATS_DRY_RUN_ENABLED_ENV: "1",
                A2_STATS_DRY_RUN_DAYS_ENV: "32",
            },
            runner=lambda *args, **kwargs: None,
        )


def test_optional_a2_stats_dry_run_rejects_large_chunk(db):
    with pytest.raises(ValueError, match="chunk days must be between 1 and 7"):
        _run_optional_a2_stats_dry_run(
            db,
            api_key="test-key",
            end_date=date(2026, 9, 17),
            env={
                A2_STATS_DRY_RUN_ENABLED_ENV: "1",
                A2_STATS_DRY_RUN_DAYS_ENV: "7",
                A2_STATS_DRY_RUN_CHUNK_DAYS_ENV: "8",
            },
            runner=lambda *args, **kwargs: None,
        )


def test_optional_a2_stats_dry_run_supports_exact_past_end_date(db):
    calls = []

    def runner(conn, **kwargs):
        calls.append((conn, kwargs))
        return object()

    _run_optional_a2_stats_dry_run(
        db,
        api_key="test-key",
        end_date=date(2026, 9, 17),
        env={
            A2_STATS_DRY_RUN_ENABLED_ENV: "true",
            A2_STATS_DRY_RUN_DAYS_ENV: "7",
            A2_STATS_DRY_RUN_CHUNK_DAYS_ENV: "1",
            A2_STATS_DRY_RUN_END_DATE_ENV: "2026-09-07",
        },
        runner=runner,
    )

    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["start_date"] == date(2026, 9, 1)
    assert kwargs["end_date"] == date(2026, 9, 7)
    assert kwargs["chunk_days"] == 1
    assert kwargs["dry_run"] is True


def test_optional_a2_stats_dry_run_rejects_future_end_override(db):
    with pytest.raises(ValueError, match="may not exceed validation end date"):
        _run_optional_a2_stats_dry_run(
            db,
            api_key="test-key",
            end_date=date(2026, 9, 17),
            env={
                A2_STATS_DRY_RUN_ENABLED_ENV: "1",
                A2_STATS_DRY_RUN_END_DATE_ENV: "2026-09-18",
            },
            runner=lambda *args, **kwargs: None,
        )


def test_optional_a2_stats_dry_run_rejects_invalid_end_override(db):
    with pytest.raises(ValueError, match="ISO YYYY-MM-DD"):
        _run_optional_a2_stats_dry_run(
            db,
            api_key="test-key",
            end_date=date(2026, 9, 17),
            env={
                A2_STATS_DRY_RUN_ENABLED_ENV: "1",
                A2_STATS_DRY_RUN_END_DATE_ENV: "not-a-date",
            },
            runner=lambda *args, **kwargs: None,
        )


class _AuditCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _AuditConn:
    def __init__(self, regime_row=None, counts_row=None):
        self.regime_row = regime_row
        self.counts_row = counts_row
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "FROM a2_history_data_regimes" in sql:
            return _AuditCursor(self.regime_row)
        if "WITH scoped AS" in sql:
            return _AuditCursor(self.counts_row)
        raise AssertionError("unexpected audit query")


def test_optional_a2_regime_audit_is_disabled_by_default():
    conn = _AuditConn()

    _run_optional_a2_regime_audit(conn, env={})

    assert conn.calls == []


def test_optional_a2_regime_audit_requires_regime_code():
    with pytest.raises(ValueError, match="audit code is required"):
        _run_optional_a2_regime_audit(
            _AuditConn(),
            env={A2_REGIME_AUDIT_ENABLED_ENV: "true"},
        )


def test_optional_a2_regime_audit_reports_expected_accounting(capsys):
    activated = datetime(2026, 9, 18, 13, 54, 37, 439596, tzinfo=UTC)
    first_seen = datetime(2026, 9, 18, 13, 54, 38, tzinfo=UTC)
    last_seen = datetime(2026, 9, 18, 13, 57, 10, tzinfo=UTC)
    conn = _AuditConn(
        regime_row=(
            "A2_API_TENNIS_HISTORY_20260818_20260917_V1",
            "API_TENNIS",
            date(2026, 8, 18),
            date(2026, 9, 17),
            1,
            activated,
            "verified dry run",
        ),
        counts_row=(2942, 2894, 48, 1471, 1471, first_seen, last_seen),
    )

    _run_optional_a2_regime_audit(
        conn,
        env={
            A2_REGIME_AUDIT_ENABLED_ENV: "true",
            A2_REGIME_AUDIT_CODE_ENV: "A2_API_TENNIS_HISTORY_20260818_20260917_V1",
        },
    )

    out = capsys.readouterr().out
    assert "A2_HISTORY_REGIME_AUDIT status=FOUND" in out
    assert "total_rows=2942" in out
    assert "post_activation_rows=2894" in out
    assert "pre_activation_rows=48" in out
    assert "distinct_matches=1471" in out
    assert "complete_two_player_matches=1471" in out
