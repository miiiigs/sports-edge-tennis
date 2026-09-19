from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from sports_edge_tennis.live import history_refresh_resilient as resilient_module
from sports_edge_tennis.live.history_refresh import HistoryRefreshChunkOutcome
from sports_edge_tennis.live.history_refresh_resilient import (
    A2_REGIME_AUDIT_CODE_ENV,
    A2_REGIME_AUDIT_ENABLED_ENV,
    LIVE_TENNIS_SURFACE_PROBE_ENABLED_ENV,
    LIVE_TENNIS_SURFACE_PROBE_MAX_PAGES_ENV,
    MARKET_STALE_SELFTEST_ENABLED_ENV,
    OFFERED_MARKET_AUDIT_MAX_DECISIONS,
    _run_offered_market_history_audit,
    PINNACLE_SCHEDULE_AUDIT_ENABLED_ENV,
    PINNACLE_SCHEDULE_AUDIT_MATCH_IDS_ENV,
    A2_STATS_DRY_RUN_CHUNK_DAYS_ENV,
    A2_STATS_DRY_RUN_DAYS_ENV,
    A2_STATS_DRY_RUN_END_DATE_ENV,
    A2_STATS_DRY_RUN_ENABLED_ENV,
    _load_checkpoint,
    _run_optional_a2_regime_audit,
    _run_optional_live_tennis_surface_probe,
    _run_optional_stale_market_selftest,
    _run_optional_pinnacle_schedule_audit,
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


def test_optional_stale_market_selftest_is_disabled_by_default(capsys):
    _run_optional_stale_market_selftest(env={})
    assert capsys.readouterr().out == ""


def test_optional_stale_market_selftest_passes_fail_closed_invariant(capsys):
    _run_optional_stale_market_selftest(
        env={MARKET_STALE_SELFTEST_ENABLED_ENV: "true"}
    )

    out = capsys.readouterr().out
    assert "MARKET_STALE_SELFTEST status=PASS" in out
    assert "decision=NO_BET" in out
    assert "reason=STALE_MARKET" in out
    assert "stake=-" in out
    assert "writes=0" in out


class _ScheduleAuditCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _ScheduleAuditConn:
    def __init__(self, version_rows=(), source_rows=()):
        self.version_rows = list(version_rows)
        self.source_rows = list(source_rows)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((str(sql), params))
        rendered = str(sql)
        if "FROM match_versions mv" in rendered:
            return _ScheduleAuditCursor(self.version_rows)
        if "FROM match_source_ids" in rendered:
            return _ScheduleAuditCursor(self.source_rows)
        raise AssertionError("unexpected schedule audit query")


def test_optional_pinnacle_schedule_audit_is_disabled_by_default():
    conn = _ScheduleAuditConn()

    _run_optional_pinnacle_schedule_audit(conn, env={})

    assert conn.calls == []


def test_optional_pinnacle_schedule_audit_requires_valid_match_ids():
    conn = _ScheduleAuditConn()

    with pytest.raises(ValueError, match="match IDs are required"):
        _run_optional_pinnacle_schedule_audit(
            conn,
            env={PINNACLE_SCHEDULE_AUDIT_ENABLED_ENV: "true"},
        )

    with pytest.raises(ValueError, match="must be integers"):
        _run_optional_pinnacle_schedule_audit(
            conn,
            env={
                PINNACLE_SCHEDULE_AUDIT_ENABLED_ENV: "true",
                PINNACLE_SCHEDULE_AUDIT_MATCH_IDS_ENV: "130521,abc",
            },
        )


def test_optional_pinnacle_schedule_audit_prints_version_and_source_history(capsys):
    observed = datetime(2026, 9, 19, 1, 0, tzinfo=UTC)
    source_observed = datetime(2026, 9, 19, 0, 55, tzinfo=UTC)
    conn = _ScheduleAuditConn(
        version_rows=[
            (
                9001,
                130521,
                "A. Moro Canas",
                "J. Clarke",
                "Biella",
                datetime(2026, 9, 19, 13, 0, tzinfo=UTC),
                "SCHEDULED",
                observed,
                observed,
                "API_TENNIS",
                "fixture:130521",
                source_observed,
                observed,
            )
        ],
        source_rows=[
            (
                130521,
                "API_TENNIS",
                "fixture:130521",
                9001,
                observed,
            )
        ],
    )

    _run_optional_pinnacle_schedule_audit(
        conn,
        env={
            PINNACLE_SCHEDULE_AUDIT_ENABLED_ENV: "true",
            PINNACLE_SCHEDULE_AUDIT_MATCH_IDS_ENV: "130521,130522,130521",
        },
    )

    out = capsys.readouterr().out
    assert "PINNACLE_SCHEDULE_AUDIT_START matches=130521,130522" in out
    assert "match_id=130521 match_version_id=9001" in out
    assert "player1=A._Moro_Canas player2=J._Clarke" in out
    assert "source_system=API_TENNIS source_match_id=fixture:130521" in out
    assert "writes=0" in out


def test_optional_live_tennis_surface_probe_is_disabled_by_default(db):
    calls = []

    _run_optional_live_tennis_surface_probe(
        db,
        target_date=date(2026, 9, 19),
        env={},
        runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert calls == []


def test_optional_live_tennis_surface_probe_requires_key(db, monkeypatch):
    monkeypatch.setattr(resilient_module, "load_secret", lambda name: None)

    with pytest.raises(ValueError, match="LIVE_TENNIS_API_KEY"):
        _run_optional_live_tennis_surface_probe(
            db,
            target_date=date(2026, 9, 19),
            env={LIVE_TENNIS_SURFACE_PROBE_ENABLED_ENV: "true"},
            runner=lambda *args, **kwargs: None,
        )


def test_optional_live_tennis_surface_probe_rejects_large_page_bound(db):
    with pytest.raises(ValueError, match="between 1 and 5"):
        _run_optional_live_tennis_surface_probe(
            db,
            target_date=date(2026, 9, 19),
            env={
                LIVE_TENNIS_SURFACE_PROBE_ENABLED_ENV: "true",
                LIVE_TENNIS_SURFACE_PROBE_MAX_PAGES_ENV: "6",
            },
            runner=lambda *args, **kwargs: None,
        )


def test_optional_live_tennis_surface_probe_dispatches_read_only(
    db,
    monkeypatch,
    capsys,
):
    calls = []
    monkeypatch.setattr(
        resilient_module,
        "load_secret",
        lambda name: "live-tennis-key" if name == "LIVE_TENNIS_API_KEY" else None,
    )

    def runner(conn, **kwargs):
        calls.append((conn, kwargs))
        return 0

    _run_optional_live_tennis_surface_probe(
        db,
        target_date=date(2026, 9, 19),
        env={
            LIVE_TENNIS_SURFACE_PROBE_ENABLED_ENV: "true",
            LIVE_TENNIS_SURFACE_PROBE_MAX_PAGES_ENV: "3",
        },
        runner=runner,
    )

    assert len(calls) == 1
    assert calls[0][0] is db
    assert calls[0][1] == {
        "api_key": "live-tennis-key",
        "target_date": date(2026, 9, 19),
        "max_pages": 3,
    }
    out = capsys.readouterr().out
    assert "LIVE_TENNIS_SURFACE_PROBE_DISPATCH" in out
    assert "max_pages=3" in out
    assert "writes=0" in out


class _OfferedAuditCursor:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = [] if rows is None else rows

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _OfferedAuditConn:
    def __init__(self, *, decisions, raw_by_event, offered_by_match):
        self.decisions = list(decisions)
        self.raw_by_event = dict(raw_by_event)
        self.offered_by_match = dict(offered_by_match)
        self.calls = []

    def execute(self, sql, params=()):
        rendered = str(sql)
        self.calls.append((rendered, params))
        if "FROM decisions d" in rendered:
            return _OfferedAuditCursor(rows=self.decisions[: int(params[0])])
        if "FROM raw_odds_observations" in rendered:
            event_id = str(params[0])
            return _OfferedAuditCursor(row=self.raw_by_event[event_id])
        if "FROM odds_snapshots" in rendered:
            match_id = int(params[-1])
            return _OfferedAuditCursor(row=self.offered_by_match[match_id])
        raise AssertionError("unexpected offered-market audit query")


def _missing_market_decision(
    *,
    decision_id=1,
    prediction_run_id=10,
    match_id=100,
    as_of=None,
    selected_side=1,
    source_match_id="evt-100",
    ttl=15,
):
    return (
        decision_id,
        prediction_run_id,
        match_id,
        as_of or datetime(2026, 9, 19, 4, 6, tzinfo=UTC),
        selected_side,
        source_match_id,
        ttl,
    )


def test_offered_market_history_audit_rejects_unbounded_limit():
    with pytest.raises(ValueError, match="between 1 and 20"):
        _run_offered_market_history_audit(
            _OfferedAuditConn(decisions=[], raw_by_event={}, offered_by_match={}),
            max_decisions=OFFERED_MARKET_AUDIT_MAX_DECISIONS + 1,
        )


@pytest.mark.parametrize(
    ("raw_row", "offered_row", "expected"),
    [
        ((0, None), (0, 0, None), "PROVIDER_EVENT_ABSENT"),
        (
            (1, datetime(2026, 9, 19, 4, 5, tzinfo=UTC)),
            (0, 0, None),
            "PROVIDER_PRESENT_NO_NORMALIZED_MARKET",
        ),
        (
            (1, datetime(2026, 9, 19, 4, 5, tzinfo=UTC)),
            (4, 0, datetime(2026, 9, 19, 3, 40, tzinfo=UTC)),
            "PROVIDER_PRESENT_STALE_SELECTED_SIDE",
        ),
        (
            (1, datetime(2026, 9, 19, 4, 5, tzinfo=UTC)),
            (4, 0, datetime(2026, 9, 19, 4, 4, tzinfo=UTC)),
            "PROVIDER_PRESENT_SELECTED_SIDE_GAP",
        ),
        (
            (1, datetime(2026, 9, 19, 4, 5, tzinfo=UTC)),
            (4, 1, datetime(2026, 9, 19, 4, 4, tzinfo=UTC)),
            "OTHER",
        ),
    ],
)
def test_offered_market_history_audit_classifies_pit_lineage(
    raw_row,
    offered_row,
    expected,
    capsys,
):
    conn = _OfferedAuditConn(
        decisions=[_missing_market_decision()],
        raw_by_event={"evt-100": raw_row},
        offered_by_match={100: offered_row},
    )

    count = _run_offered_market_history_audit(conn, max_decisions=5)

    assert count == 1
    out = capsys.readouterr().out
    assert "OFFERED_MARKET_HISTORY_AUDIT_START decisions=1" in out
    assert f"classification={expected}" in out
    assert "writes=0 provider_requests=0" in out
    assert "OFFERED_MARKET_HISTORY_AUDIT_SUMMARY decisions=1" in out


def test_offered_market_history_audit_handles_missing_api_event_id(capsys):
    conn = _OfferedAuditConn(
        decisions=[_missing_market_decision(source_match_id=None)],
        raw_by_event={},
        offered_by_match={100: (0, 0, None)},
    )

    _run_offered_market_history_audit(conn, max_decisions=5)

    out = capsys.readouterr().out
    assert "api_event_id=-" in out
    assert "classification=NO_API_TENNIS_EVENT_ID" in out
    assert not any("FROM raw_odds_observations" in sql for sql, _ in conn.calls)
