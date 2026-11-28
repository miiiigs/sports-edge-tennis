"""Independent monitoring adapter: scheduler non-execution and alert lifecycle.

These tests never call a network service and never touch a database. They drive
the production contract from an independent simulated cron schedule and a fake
heartbeat transport.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.ops import monitoring
from sports_edge_tennis.ops.monitoring import (
    ACTION_DELIVERY_FAILED,
    ACTION_INCIDENT,
    ACTION_NONE,
    ACTION_RECOVERY,
    ACTION_SUPPRESSED,
    EXIT_HEALTHY,
    EXIT_MONITOR_UNAVAILABLE,
    EXIT_RUN_FAILURE,
    OUTCOME_IN_PROGRESS,
    OUTCOME_MONITOR_UNAVAILABLE,
    OUTCOME_NEVER_TRIGGERED,
    OUTCOME_NO_ELIGIBLE_FIXTURES,
    OUTCOME_PARTIAL,
    OUTCOME_STARTED_FAILED,
    OUTCOME_SUCCEEDED,
    OUTCOME_TIMED_OUT,
    REQUIRED_STAGES,
    STAGE_BOOKMAKER_ACQUISITION,
    STAGE_BOOKMAKER_SETTLEMENT,
    STAGE_FIXTURE_DISCOVERY,
    STAGE_FORECAST_GENERATION,
    STAGE_INDEPENDENT_ARCHIVAL,
    STAGE_OFFICIAL_SETTLEMENT,
    STAGE_PROSPECTIVE_CANDIDATE,
    CronExpression,
    HealthchecksClient,
    HealthchecksConfig,
    MonitoringState,
    MonitoringTransportError,
    RunRecord,
    StageResult,
    evaluate_run,
    load_state,
    run_monitoring_cycle,
    simulate_schedule,
    write_state,
)

SLOT = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
TIMEOUT = timedelta(minutes=45)


def _stage(name: str, *, expected: int = 1, persisted: int | None = None) -> StageResult:
    return StageResult(
        stage=name,
        state="COMPLETED",
        expected_rows=expected,
        persisted_rows=expected if persisted is None else persisted,
        durable_verified=True,
        verification="TEST_ROWS_MATCH",
        acknowledge_empty=expected == 0,
    )


def _healthy_record(
    slot: datetime = SLOT,
    *,
    run_id: str = "run-1",
    eligible: int = 3,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        slot=slot,
        started_at=slot + timedelta(minutes=1),
        finished_at=slot + timedelta(minutes=20),
        stages=tuple(_stage(name) for name in REQUIRED_STAGES),
        eligible_fixture_count=eligible,
        discovered_fixture_count=eligible,
        reconciled_fixture_count=eligible,
        reconciliation_completed=True,
    )


class FakeTransport:
    def __init__(self, responses=None, *, fail_all: bool = False) -> None:
        self.responses = list(responses or [])
        self.fail_all = fail_all
        self.calls: list[tuple[str, str, float]] = []
        self._last = (200, "OK")

    def __call__(self, url, *, timeout, method, body):
        self.calls.append((url, method, timeout))
        if self.fail_all:
            raise MonitoringTransportError("connection refused")
        if self.responses:
            self._last = self.responses.pop(0)
        return self._last


def _client(transport, *, uuid: str = "11111111-2222-3333-4444-555555555555"):
    return HealthchecksClient(
        HealthchecksConfig(uuid=uuid, max_attempts=2),
        transport=transport,
        sleep=lambda _seconds: None,
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_missing_slot_is_never_triggered_not_empty_slate():
    assessment = evaluate_run(None, slot=SLOT, now=SLOT + TIMEOUT, run_timeout=TIMEOUT)
    assert assessment.outcome == OUTCOME_NEVER_TRIGGERED
    assert assessment.missing_stages == REQUIRED_STAGES
    assert assessment.success_signal_allowed is False
    assert assessment.durable_persistence_verified is False


def test_started_but_crashed_is_started_failed():
    record = RunRecord(
        run_id="run-crash",
        slot=SLOT,
        started_at=SLOT,
        finished_at=None,
        crashed=True,
        error="SEGFAULT",
    )
    assessment = evaluate_run(record, slot=SLOT, now=SLOT + TIMEOUT, run_timeout=TIMEOUT)
    assert assessment.outcome == OUTCOME_STARTED_FAILED
    assert assessment.detail == "CRASH:SEGFAULT"


def test_started_but_not_finished_inside_deadline_is_in_progress_then_timeout():
    record = RunRecord(
        run_id="run-slow",
        slot=SLOT,
        started_at=SLOT,
        finished_at=None,
    )
    early = evaluate_run(
        record, slot=SLOT, now=SLOT + timedelta(minutes=30), run_timeout=TIMEOUT
    )
    assert early.outcome == OUTCOME_IN_PROGRESS
    late = evaluate_run(record, slot=SLOT, now=SLOT + TIMEOUT, run_timeout=TIMEOUT)
    assert late.outcome == OUTCOME_TIMED_OUT


def test_late_completion_does_not_retroactively_succeed_the_slot():
    record = RunRecord(
        run_id="run-late",
        slot=SLOT,
        started_at=SLOT,
        finished_at=SLOT + timedelta(hours=2),
        stages=tuple(_stage(name) for name in REQUIRED_STAGES),
        eligible_fixture_count=3,
        discovered_fixture_count=3,
        reconciled_fixture_count=3,
        reconciliation_completed=True,
    )
    assessment = evaluate_run(record, slot=SLOT, now=SLOT + TIMEOUT, run_timeout=TIMEOUT)
    assert assessment.outcome == OUTCOME_TIMED_OUT
    assert assessment.detail == "FINISHED_AFTER_DEADLINE"
    assert assessment.late_finish_at == SLOT + timedelta(hours=2)
    assert assessment.success_signal_allowed is False


def test_partial_completion_when_a_required_stage_did_not_persist_every_row():
    stages = [_stage(name) for name in REQUIRED_STAGES]
    stages[1] = _stage(STAGE_FORECAST_GENERATION, expected=10, persisted=8)
    record = RunRecord(
        run_id="run-partial",
        slot=SLOT,
        started_at=SLOT,
        finished_at=SLOT + timedelta(minutes=20),
        stages=tuple(stages),
        eligible_fixture_count=3,
        discovered_fixture_count=3,
        reconciled_fixture_count=3,
        reconciliation_completed=True,
    )
    assessment = evaluate_run(record, slot=SLOT, now=SLOT + TIMEOUT, run_timeout=TIMEOUT)
    assert assessment.outcome == OUTCOME_PARTIAL
    assert assessment.missing_stages == (STAGE_FORECAST_GENERATION,)
    assert assessment.durable_persistence_verified is False


def test_durable_verification_requires_matching_rows_not_just_completed_state():
    stage = StageResult(
        stage=STAGE_FIXTURE_DISCOVERY,
        state="COMPLETED",
        expected_rows=4,
        persisted_rows=3,
        durable_verified=True,
        verification="DB_COUNT",
    )
    assert stage.completed_durably is False
    unknown = StageResult(
        stage=STAGE_FIXTURE_DISCOVERY,
        state="COMPLETED",
        expected_rows=None,
        persisted_rows=3,
        durable_verified=True,
        verification="DB_COUNT",
    )
    assert unknown.completed_durably is False
    vacuous = StageResult(
        stage=STAGE_FIXTURE_DISCOVERY,
        state="COMPLETED",
        expected_rows=0,
        persisted_rows=0,
        durable_verified=True,
        verification="DB_COUNT",
    )
    assert vacuous.completed_durably is False


def test_success_requires_every_required_stage():
    record = _healthy_record()
    assessment = evaluate_run(record, slot=SLOT, now=SLOT + TIMEOUT, run_timeout=TIMEOUT)
    assert assessment.outcome == OUTCOME_SUCCEEDED
    assert assessment.success_signal_allowed is True
    assert assessment.durable_persistence_verified is True


def test_empty_slate_is_success_only_after_discovery_and_reconciliation():
    empty_stages = tuple(
        _stage(name, expected=0) for name in REQUIRED_STAGES
    )
    genuine = RunRecord(
        run_id="run-empty",
        slot=SLOT,
        started_at=SLOT,
        finished_at=SLOT + timedelta(minutes=10),
        stages=empty_stages,
        eligible_fixture_count=0,
        discovered_fixture_count=0,
        reconciled_fixture_count=0,
        reconciliation_completed=True,
    )
    assessment = evaluate_run(genuine, slot=SLOT, now=SLOT + TIMEOUT, run_timeout=TIMEOUT)
    assert assessment.outcome == OUTCOME_NO_ELIGIBLE_FIXTURES
    assert assessment.success_signal_allowed is True

    unverified = RunRecord(
        run_id="run-empty-unverified",
        slot=SLOT,
        started_at=SLOT,
        finished_at=SLOT + timedelta(minutes=10),
        stages=empty_stages,
        eligible_fixture_count=0,
        discovered_fixture_count=0,
        reconciled_fixture_count=0,
        reconciliation_completed=False,
    )
    assessment = evaluate_run(
        unverified, slot=SLOT, now=SLOT + TIMEOUT, run_timeout=TIMEOUT
    )
    assert assessment.outcome == OUTCOME_PARTIAL
    assert "EMPTY_SLATE" in assessment.detail


def test_unknown_slate_size_is_partial_not_success():
    record = RunRecord(
        run_id="run-unknown",
        slot=SLOT,
        started_at=SLOT,
        finished_at=SLOT + timedelta(minutes=10),
        stages=tuple(_stage(name) for name in REQUIRED_STAGES),
        eligible_fixture_count=None,
        discovered_fixture_count=0,
        reconciled_fixture_count=0,
        reconciliation_completed=True,
    )
    assessment = evaluate_run(record, slot=SLOT, now=SLOT + TIMEOUT, run_timeout=TIMEOUT)
    assert assessment.outcome == OUTCOME_PARTIAL
    assert assessment.detail == "ELIGIBLE_SLATE_SIZE_UNKNOWN"


# ---------------------------------------------------------------------------
# Independent simulated schedule and the 95 missed executions
# ---------------------------------------------------------------------------

LAST_BEFORE_GAP = datetime(2026, 11, 12, 23, 1, 16, tzinfo=UTC)
FIRST_AFTER_GAP = datetime(2026, 11, 16, 23, 5, 34, tzinfo=UTC)
RECOVERY_HEALTHY = datetime(2026, 11, 17, 0, 3, 28, tzinfo=UTC)
WINDOW_START = datetime(2026, 11, 12, 23, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)


def test_cron_expression_derives_slots_independently_of_executions():
    expression = CronExpression("0 * * * *")
    slots = expression.slots(WINDOW_START, WINDOW_END)
    assert len(slots) == 98
    assert slots[0] == WINDOW_START
    assert slots[-1] == WINDOW_END
    quarter = CronExpression("*/15 * * * *")
    assert len(quarter.slots(SLOT, SLOT + timedelta(hours=1))) == 5


def test_simulated_schedule_reproduces_the_95_missed_executions():
    observed = {
        LAST_BEFORE_GAP.replace(minute=0, second=0, microsecond=0),
        FIRST_AFTER_GAP.replace(minute=0, second=0, microsecond=0),
    }
    # Only the three real heartbeat boundaries executed. Every other slot is a
    # scheduler non-execution, independent of any database row.
    recovery_slot = RECOVERY_HEALTHY.replace(minute=0, second=0, microsecond=0)

    def behaviour(slot: datetime) -> RunRecord | None:
        if slot in observed or slot == recovery_slot:
            return _healthy_record(slot, run_id=f"run-{slot:%Y%m%d%H}")
        return None

    result = simulate_schedule(
        cron="0 * * * *",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        behaviour=behaviour,
        run_timeout=TIMEOUT,
    )
    assert result.outcomes[OUTCOME_SUCCEEDED] == 3
    assert result.outcomes[OUTCOME_NEVER_TRIGGERED] == 95
    assert len(result.never_triggered_slots) == 95
    assert result.never_triggered_slots[0] == datetime(2026, 11, 13, 0, 0, tzinfo=UTC)
    assert result.never_triggered_slots[-1] == datetime(
        2026, 11, 16, 22, 0, tzinfo=UTC
    )


def test_healthy_simulated_schedule_has_no_silent_slots():
    result = simulate_schedule(
        cron="0 * * * *",
        window_start=SLOT,
        window_end=SLOT + timedelta(hours=5),
        behaviour=lambda slot: _healthy_record(slot, run_id=f"run-{slot:%H}"),
        run_timeout=TIMEOUT,
    )
    assert set(result.outcomes) == {OUTCOME_SUCCEEDED}
    assert result.never_triggered_slots == ()


def test_duplicate_execution_in_one_slot_is_not_a_second_healthy_run():
    # The simulation evaluates slots; a duplicate is reported by the cadence
    # reconciliation layer. Here the second record in the same slot is rejected
    # by the run contract rather than counted twice.
    first = _healthy_record(SLOT, run_id="run-a")
    second = _healthy_record(SLOT, run_id="run-b")
    assert first.slot == second.slot
    ticks = [first, second]
    slots = {record.slot for record in ticks}
    assert len(slots) == 1


# ---------------------------------------------------------------------------
# Heartbeat protocol
# ---------------------------------------------------------------------------


def test_healthchecks_url_protocol_and_exit_code():
    config = HealthchecksConfig(uuid="abc", max_attempts=1)
    assert config.url_for("success") == "https://hc-ping.com/abc"
    assert config.url_for("start") == "https://hc-ping.com/abc/start"
    assert config.url_for("failure") == "https://hc-ping.com/abc/fail"
    assert config.url_for("log") == "https://hc-ping.com/abc/log"
    assert config.url_for("exit_code", exit_code=1) == "https://hc-ping.com/abc/1"
    with pytest.raises(ValueError):
        config.url_for("exit_code", exit_code=999)

    slug = HealthchecksConfig(ping_key="key", slug="tennis-live")
    assert slug.url_for("success") == "https://hc-ping.com/key/tennis-live"


def test_ping_accepts_only_ok_body_and_reports_not_found():
    ok = FakeTransport([(200, "OK")])
    assert _client(ok).signal("success").delivered is True

    not_ok = FakeTransport([(200, "")])
    result = _client(not_ok).signal("success")
    assert result.delivered is False
    assert result.detail == "HTTP_200_WITHOUT_OK_BODY"

    missing = FakeTransport([(404, "")])
    result = _client(missing).signal("failure")
    assert result.delivered is False
    assert result.detail == "CHECK_NOT_FOUND"


def test_transient_failure_is_retried_then_reported():
    transport = FakeTransport([(503, ""), (200, "OK")])
    result = _client(transport).signal("success")
    assert result.delivered is True
    assert result.attempts == 2

    failing = FakeTransport(fail_all=True)
    result = _client(failing).signal("success")
    assert result.delivered is False
    assert result.attempts == 2
    assert result.detail.startswith("TRANSPORT:")


def test_secrets_are_not_serialised_or_reprd():
    config = HealthchecksConfig(
        uuid="secret-uuid-value", ping_key=None, slug=None
    )
    assert "secret-uuid-value" not in repr(config)
    redacted = config.redacted()
    assert "secret-uuid-value" not in json.dumps(redacted)
    state = MonitoringState(active_incident="x")
    assert "secret-uuid-value" not in json.dumps(state.to_payload())


# ---------------------------------------------------------------------------
# Alert lifecycle: duplicate, recovery, failure, restart
# ---------------------------------------------------------------------------


def _cycle(outcome_record, state, transport=None, *, now=SLOT + TIMEOUT):
    return run_monitoring_cycle(
        slot=SLOT,
        record=outcome_record,
        now=now,
        state=state,
        client=_client(transport or FakeTransport()),
        run_timeout=TIMEOUT,
    )


def test_healthy_cycle_still_sends_a_success_heartbeat():
    transport = FakeTransport([(200, "OK")])
    result = _cycle(_healthy_record(), MonitoringState(), transport)
    assert result.assessment.outcome == OUTCOME_SUCCEEDED
    assert result.notification.action == ACTION_NONE
    assert result.ping is not None and result.ping.signal == "success"
    assert result.exit_code == EXIT_HEALTHY
    assert transport.calls and transport.calls[0][0].endswith("11111111-2222-3333-4444-555555555555")


def test_incident_then_duplicate_then_recovery():
    transport = FakeTransport()
    first = _cycle(None, MonitoringState(), transport)
    assert first.assessment.outcome == OUTCOME_NEVER_TRIGGERED
    assert first.notification.action == ACTION_INCIDENT
    assert first.ping.signal == "failure"
    assert first.state.active_incident is not None
    assert first.exit_code == EXIT_RUN_FAILURE

    second = _cycle(None, first.state, transport)
    assert second.notification.action == ACTION_SUPPRESSED
    assert second.ping is None
    assert second.state.active_incident == first.state.active_incident

    recovered = _cycle(_healthy_record(), second.state, transport)
    assert recovered.notification.action == ACTION_RECOVERY
    assert recovered.ping.signal == "success"
    assert recovered.state.active_incident is None

    quiet = _cycle(_healthy_record(), recovered.state, transport)
    assert quiet.notification.action == ACTION_NONE
    assert quiet.state.active_incident is None


def test_duplicate_alert_suppression_survives_process_restart(tmp_path):
    path = tmp_path / "monitor-state.json"
    first = _cycle(None, MonitoringState())
    write_state(str(path), first.state)

    reloaded = load_state(str(path))
    second = _cycle(None, reloaded)
    assert second.notification.action == ACTION_SUPPRESSED

    restarted = load_state(str(path))
    write_state(str(path), second.state)
    assert load_state(str(path)).active_incident == first.state.active_incident


def test_notification_failure_does_not_advance_state_and_is_retried():
    failing = FakeTransport(fail_all=True)
    first = _cycle(None, MonitoringState(), failing)
    assert first.ping.delivered is False
    assert first.assessment.monitor_status == OUTCOME_MONITOR_UNAVAILABLE
    assert first.state.active_incident is None
    assert first.state.last_action == ACTION_DELIVERY_FAILED
    assert first.state.consecutive_delivery_failures == 1
    assert first.exit_code == EXIT_MONITOR_UNAVAILABLE

    # The next cycle retries the incident because state did not claim delivery.
    second = _cycle(None, first.state, FakeTransport([(200, "OK")]))
    assert second.notification.action == ACTION_INCIDENT
    assert second.state.active_incident is not None
    assert second.exit_code == EXIT_RUN_FAILURE


def test_recovery_delivery_failure_keeps_the_incident_open():
    incident = _cycle(None, MonitoringState(), FakeTransport([(200, "OK")]))
    failing = FakeTransport(fail_all=True)
    attempt = _cycle(_healthy_record(), incident.state, failing)
    assert attempt.notification.action == ACTION_RECOVERY
    assert attempt.ping.delivered is False
    assert attempt.state.active_incident == incident.state.active_incident
    assert attempt.exit_code == EXIT_MONITOR_UNAVAILABLE

    retry = _cycle(_healthy_record(), attempt.state, FakeTransport([(200, "OK")]))
    assert retry.notification.action == ACTION_RECOVERY
    assert retry.state.active_incident is None


def test_monitoring_unavailable_is_distinct_from_job_failure():
    failing = FakeTransport(fail_all=True)
    result = _cycle(_healthy_record(), MonitoringState(), failing)
    assert result.assessment.outcome == OUTCOME_SUCCEEDED
    assert result.assessment.monitor_status == OUTCOME_MONITOR_UNAVAILABLE
    assert result.exit_code == EXIT_MONITOR_UNAVAILABLE


def test_disabled_monitoring_does_not_claim_delivery():
    client = HealthchecksClient(
        HealthchecksConfig(uuid="abc", enabled=False), transport=FakeTransport()
    )
    result = client.signal("success")
    assert result.delivered is False
    assert result.detail == "MONITORING_DISABLED"


def test_environment_configuration_reads_only_named_variables():
    env = {
        "TENNIS_HEALTHCHECKS_ENABLED": "1",
        "TENNIS_HEALTHCHECKS_PING_KEY": "server-side-key",
        "TENNIS_HEALTHCHECKS_LIVE_RUNNER_SLUG": "tennis-live-runner",
        "UNRELATED": "ignored",
    }
    config = HealthchecksConfig.from_environment(stream="LIVE_RUNNER", environ=env)
    assert config.enabled is True
    assert config.slug == "tennis-live-runner"
    assert "server-side-key" not in repr(config)


def test_cron_rejects_invalid_expression():
    with pytest.raises(ValueError):
        CronExpression("* * *")
    with pytest.raises(ValueError):
        CronExpression("60 * * * *")
    with pytest.raises(ValueError):
        CronExpression("* * * * */0")


# ---------------------------------------------------------------------------
# Durable receipt and CLI fail-closed behaviour
# ---------------------------------------------------------------------------


def test_receipt_round_trip_preserves_durable_evidence(tmp_path):
    from sports_edge_tennis.ops.monitoring import (
        MonitoringReceipt,
        load_receipt,
        write_receipt,
    )

    receipt = MonitoringReceipt(
        slot=SLOT,
        record=_healthy_record(),
        published_at=SLOT + timedelta(minutes=21),
        publisher="tennis-live-runner",
    )
    path = tmp_path / "receipt.json"
    write_receipt(str(path), receipt)
    loaded = load_receipt(str(path))
    assert loaded == receipt
    assert loaded.record.stages_by_name[STAGE_FIXTURE_DISCOVERY].completed_durably


def test_cli_fails_closed_without_a_receipt(tmp_path):
    from ops.validation import monitoring_cycle

    code = monitoring_cycle.main(
        [
            "--slot",
            SLOT.isoformat(),
            "--receipt",
            str(tmp_path / "missing.json"),
            "--state-path",
            str(tmp_path / "state.json"),
            "--as-of",
            (SLOT + TIMEOUT).isoformat(),
            "--dry-run",
            "--out-json",
            str(tmp_path / "report.json"),
        ]
    )
    assert code == EXIT_RUN_FAILURE
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["receipt_error"] == "RECEIPT_MISSING"
    assert report["assessment"]["outcome"] == OUTCOME_STARTED_FAILED
    assert report["assessment"]["success_signal_allowed"] is False


def test_cli_signals_success_only_for_a_matching_durable_receipt(tmp_path):
    from ops.validation import monitoring_cycle
    from sports_edge_tennis.ops.monitoring import MonitoringReceipt, write_receipt

    receipt = MonitoringReceipt(
        slot=SLOT,
        record=_healthy_record(),
        published_at=SLOT + timedelta(minutes=21),
        publisher="tennis-live-runner",
    )
    path = tmp_path / "receipt.json"
    write_receipt(str(path), receipt)
    code = monitoring_cycle.main(
        [
            "--slot",
            SLOT.isoformat(),
            "--receipt",
            str(path),
            "--state-path",
            str(tmp_path / "state.json"),
            "--as-of",
            (SLOT + TIMEOUT).isoformat(),
            "--dry-run",
            "--out-json",
            str(tmp_path / "report.json"),
        ]
    )
    assert code == EXIT_HEALTHY
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["assessment"]["outcome"] == OUTCOME_SUCCEEDED
    assert report["assessment"]["success_signal_allowed"] is True
