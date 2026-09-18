from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from sports_edge_tennis.live.history_refresh import HistoryRefreshChunkOutcome
from sports_edge_tennis.live.history_refresh_resilient import (
    A2_STATS_APPLY_AUTHORIZATION,
    A2_STATS_APPLY_AUTH_ENV,
    A2_STATS_APPLY_ENABLED_ENV,
    A2_STATS_APPLY_END_DATE_ENV,
    A2_STATS_APPLY_NOTE_ENV,
    A2_STATS_APPLY_REGIME_CODE_ENV,
    A2_STATS_APPLY_START_DATE_ENV,
    A2_STATS_DRY_RUN_CHUNK_DAYS_ENV,
    A2_STATS_DRY_RUN_DAYS_ENV,
    A2_STATS_DRY_RUN_END_DATE_ENV,
    A2_STATS_DRY_RUN_ENABLED_ENV,
    _load_checkpoint,
    _run_optional_a2_stats_apply,
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


def test_optional_a2_stats_apply_is_disabled_by_default(db):
    calls = []

    _run_optional_a2_stats_apply(
        db,
        api_key="test-key",
        validation_end_date=date(2026, 9, 17),
        env={},
        runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert calls == []


def test_optional_a2_stats_apply_requires_authorization_token(db):
    with pytest.raises(ValueError, match="authorization token mismatch"):
        _run_optional_a2_stats_apply(
            db,
            api_key="test-key",
            validation_end_date=date(2026, 9, 17),
            env={
                A2_STATS_APPLY_ENABLED_ENV: "true",
                A2_STATS_APPLY_AUTH_ENV: "wrong",
                A2_STATS_APPLY_START_DATE_ENV: "2026-08-18",
                A2_STATS_APPLY_END_DATE_ENV: "2026-09-17",
                A2_STATS_APPLY_REGIME_CODE_ENV: "A2_TEST",
            },
            schema_ensurer=lambda conn: "EXISTING",
            activator=lambda *args, **kwargs: None,
            runner=lambda *args, **kwargs: None,
        )


def test_optional_a2_stats_apply_rejects_dry_run_conflict(db):
    with pytest.raises(ValueError, match="mutually exclusive"):
        _run_optional_a2_stats_apply(
            db,
            api_key="test-key",
            validation_end_date=date(2026, 9, 17),
            env={
                A2_STATS_APPLY_ENABLED_ENV: "true",
                A2_STATS_DRY_RUN_ENABLED_ENV: "true",
            },
            schema_ensurer=lambda conn: "EXISTING",
            activator=lambda *args, **kwargs: None,
            runner=lambda *args, **kwargs: None,
        )


def test_optional_a2_stats_apply_dispatches_guarded_regime(db, capsys):
    calls = []
    fixed_activation = datetime(2026, 9, 18, 13, 45, tzinfo=UTC)

    def activator(conn, **kwargs):
        calls.append(("activate", conn, kwargs))
        return SimpleNamespace(
            regime_code=kwargs["regime_code"],
            activated_at=kwargs["activated_at"],
            window_start=kwargs["window_start"],
            window_end=kwargs["window_end"],
            chunk_days=kwargs["chunk_days"],
        )

    def runner(conn, **kwargs):
        calls.append(("run", conn, kwargs))
        return object()

    _run_optional_a2_stats_apply(
        db,
        api_key="test-key",
        validation_end_date=date(2026, 9, 17),
        env={
            A2_STATS_APPLY_ENABLED_ENV: "true",
            A2_STATS_APPLY_AUTH_ENV: A2_STATS_APPLY_AUTHORIZATION,
            A2_STATS_APPLY_START_DATE_ENV: "2026-08-18",
            A2_STATS_APPLY_END_DATE_ENV: "2026-09-17",
            A2_STATS_APPLY_REGIME_CODE_ENV: "A2_API_TENNIS_HISTORY_20260818_20260917_V1",
            A2_STATS_APPLY_NOTE_ENV: "verified 31-day dry run",
        },
        schema_ensurer=lambda conn: "EXISTING",
        activator=activator,
        runner=runner,
        activated_at=fixed_activation,
    )

    assert calls[0][0] == "activate"
    assert calls[0][2] == {
        "regime_code": "A2_API_TENNIS_HISTORY_20260818_20260917_V1",
        "window_start": date(2026, 8, 18),
        "window_end": date(2026, 9, 17),
        "chunk_days": 1,
        "activated_at": fixed_activation,
        "activation_note": "verified 31-day dry run",
    }
    assert calls[1][0] == "run"
    assert calls[1][2] == {
        "api_key": "test-key",
        "start_date": date(2026, 8, 18),
        "end_date": date(2026, 9, 17),
        "chunk_days": 1,
        "dry_run": False,
    }

    out = capsys.readouterr().out
    assert "A2_STATS_BACKFILL_SCHEMA status=EXISTING migration=0011" in out
    assert (
        "A2_STATS_BACKFILL_ACTIVATION "
        "regime=A2_API_TENNIS_HISTORY_20260818_20260917_V1"
    ) in out
    assert "A2_STATS_BACKFILL_APPLY_DISPATCH" in out
    assert "days=31 chunk_days=1 writes=1" in out


def test_optional_a2_stats_apply_rejects_window_over_31_days(db):
    with pytest.raises(ValueError, match="may not exceed 31 days"):
        _run_optional_a2_stats_apply(
            db,
            api_key="test-key",
            validation_end_date=date(2026, 9, 17),
            env={
                A2_STATS_APPLY_ENABLED_ENV: "true",
                A2_STATS_APPLY_AUTH_ENV: A2_STATS_APPLY_AUTHORIZATION,
                A2_STATS_APPLY_START_DATE_ENV: "2026-08-17",
                A2_STATS_APPLY_END_DATE_ENV: "2026-09-17",
                A2_STATS_APPLY_REGIME_CODE_ENV: "A2_TEST",
            },
            schema_ensurer=lambda conn: "EXISTING",
            activator=lambda *args, **kwargs: None,
            runner=lambda *args, **kwargs: None,
        )


def test_optional_a2_stats_apply_rejects_end_after_validation_boundary(db):
    with pytest.raises(ValueError, match="may not exceed validation end date"):
        _run_optional_a2_stats_apply(
            db,
            api_key="test-key",
            validation_end_date=date(2026, 9, 17),
            env={
                A2_STATS_APPLY_ENABLED_ENV: "true",
                A2_STATS_APPLY_AUTH_ENV: A2_STATS_APPLY_AUTHORIZATION,
                A2_STATS_APPLY_START_DATE_ENV: "2026-09-01",
                A2_STATS_APPLY_END_DATE_ENV: "2026-09-18",
                A2_STATS_APPLY_REGIME_CODE_ENV: "A2_TEST",
            },
            schema_ensurer=lambda conn: "EXISTING",
            activator=lambda *args, **kwargs: None,
            runner=lambda *args, **kwargs: None,
        )
