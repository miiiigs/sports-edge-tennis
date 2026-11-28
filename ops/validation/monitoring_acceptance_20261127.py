"""Deterministic acceptance matrix for the independent monitoring adapter.

No network, no database, no provider, no clock dependency: every scenario uses
a fixed UTC clock and an in-process fake transport. The emitted evidence is the
operational acceptance record for PR #391.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta

from sports_edge_tennis.ops.monitoring import (
    ACTION_DELIVERY_FAILED,
    ACTION_INCIDENT,
    ACTION_NONE,
    ACTION_RECOVERY,
    ACTION_SUPPRESSED,
    EXIT_HEALTHY,
    EXIT_MONITOR_UNAVAILABLE,
    EXIT_RUN_FAILURE,
    OUTCOME_MONITOR_UNAVAILABLE,
    OUTCOME_NEVER_TRIGGERED,
    OUTCOME_NO_ELIGIBLE_FIXTURES,
    OUTCOME_PARTIAL,
    OUTCOME_STARTED_FAILED,
    OUTCOME_SUCCEEDED,
    OUTCOME_TIMED_OUT,
    REQUIRED_STAGES,
    STAGE_FORECAST_GENERATION,
    CronExpression,
    HealthchecksClient,
    HealthchecksConfig,
    MonitoringState,
    MonitoringTransportError,
    RunRecord,
    StageResult,
    describe_configuration,
    evaluate_run,
    load_state,
    run_monitoring_cycle,
    simulate_schedule,
    write_state,
)

SLOT = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
TIMEOUT = timedelta(minutes=45)
VERSION = "TENNIS_INDEPENDENT_MONITORING_ACCEPTANCE_20261127_V1"


class FakeTransport:
    def __init__(self, responses=None, *, fail_all: bool = False) -> None:
        self.responses = list(responses or [])
        self.fail_all = fail_all
        self.calls: list[str] = []
        self._last = (200, "OK")

    def __call__(self, url, *, timeout, method, body):
        self.calls.append(url)
        if self.fail_all:
            raise MonitoringTransportError("connection refused")
        if self.responses:
            self._last = self.responses.pop(0)
        return self._last


def stage(name: str, *, expected: int = 1, persisted: int | None = None) -> StageResult:
    return StageResult(
        stage=name,
        state="COMPLETED",
        expected_rows=expected,
        persisted_rows=expected if persisted is None else persisted,
        durable_verified=True,
        verification="TEST_ROWS_MATCH",
        acknowledge_empty=expected == 0,
    )


def healthy(slot: datetime = SLOT, *, run_id: str = "run-1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        slot=slot,
        started_at=slot + timedelta(minutes=1),
        finished_at=slot + timedelta(minutes=20),
        stages=tuple(stage(name) for name in REQUIRED_STAGES),
        eligible_fixture_count=3,
        discovered_fixture_count=3,
        reconciled_fixture_count=3,
        reconciliation_completed=True,
    )


def empty_verified(slot: datetime = SLOT) -> RunRecord:
    return RunRecord(
        run_id="run-empty",
        slot=slot,
        started_at=slot + timedelta(minutes=1),
        finished_at=slot + timedelta(minutes=5),
        stages=tuple(stage(name, expected=0) for name in REQUIRED_STAGES),
        eligible_fixture_count=0,
        discovered_fixture_count=0,
        reconciled_fixture_count=0,
        reconciliation_completed=True,
    )


def client(transport, *, uuid: str = "check-uuid") -> HealthchecksClient:
    return HealthchecksClient(
        HealthchecksConfig(uuid=uuid, max_attempts=2),
        transport=transport,
        sleep=lambda _seconds: None,
    )


def cycle(record, state, transport=None, *, now=SLOT + TIMEOUT, dry_run=False):
    return run_monitoring_cycle(
        slot=SLOT,
        record=record,
        now=now,
        state=state,
        client=None if dry_run else client(transport or FakeTransport()),
        run_timeout=TIMEOUT,
        dry_run=dry_run,
    )


def scenario(name, description, cycle_result, **extra):
    payload = {
        "scenario": name,
        "description": description,
        "outcome": cycle_result.assessment.outcome,
        "detail": cycle_result.assessment.detail,
        "success_signal_allowed": cycle_result.assessment.success_signal_allowed,
        "durable_verified": cycle_result.assessment.durable_persistence_verified,
        "notification_action": cycle_result.notification.action,
        "ping_signal": cycle_result.ping.signal if cycle_result.ping else None,
        "ping_delivered": cycle_result.ping.delivered if cycle_result.ping else None,
        "monitor_status": cycle_result.assessment.monitor_status,
        "exit_code": cycle_result.exit_code,
        "state_active_incident": cycle_result.state.active_incident,
        "state_last_action": cycle_result.state.last_action,
        **extra,
    }
    return payload


def build_evidence(tmp_path: str | None = None) -> dict[str, object]:
    import tempfile

    if tmp_path is None:
        tmp_path = tempfile.mkdtemp(prefix="tennis-monitoring-acceptance-")
    scenarios: list[dict[str, object]] = []

    # 1. Independent simulated schedule: the real 95-slot gap.
    window_start = datetime(2026, 11, 12, 23, 0, tzinfo=UTC)
    window_end = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
    observed = {
        datetime(2026, 11, 12, 23, 0, tzinfo=UTC),
        datetime(2026, 11, 16, 23, 0, tzinfo=UTC),
        datetime(2026, 11, 17, 0, 0, tzinfo=UTC),
    }

    def behaviour(slot):
        return healthy(slot, run_id=f"run-{slot:%Y%m%d%H}") if slot in observed else None

    simulation = simulate_schedule(
        cron="0 * * * *",
        window_start=window_start,
        window_end=window_end,
        behaviour=behaviour,
        run_timeout=TIMEOUT,
    )
    scenarios.append(
        {
            "scenario": "SCHEDULER_NON_EXECUTION_95_SLOTS",
            "description": (
                "Independent hourly schedule over 12-17 Nov 2026 with only the "
                "three real heartbeat boundaries executing."
            ),
            "expected_slot_count": len(simulation.ticks),
            "outcomes": simulation.outcomes,
            "never_triggered_count": len(simulation.never_triggered_slots),
            "first_missing_slot": simulation.never_triggered_slots[0].isoformat(),
            "last_missing_slot": simulation.never_triggered_slots[-1].isoformat(),
            "independent_of_observed_rows": True,
            "result": "PASS" if len(simulation.never_triggered_slots) == 95 else "FAIL",
        }
    )

    healthy_cycle = cycle(healthy(), MonitoringState())
    scenarios.append(
        scenario(
            "HEALTHY_HEARTBEAT",
            "All required stages completed with matching durable rows.",
            healthy_cycle,
            result="PASS"
            if healthy_cycle.exit_code == EXIT_HEALTHY
            and healthy_cycle.ping
            and healthy_cycle.ping.signal == "success"
            else "FAIL",
        )
    )

    crash = RunRecord(
        run_id="run-crash",
        slot=SLOT,
        started_at=SLOT,
        finished_at=None,
        crashed=True,
        error="APPLICATION_CRASH",
    )
    crash_cycle = cycle(crash, MonitoringState())
    scenarios.append(
        scenario(
            "APPLICATION_CRASH",
            "Process started and terminated before finishing any stage.",
            crash_cycle,
            result="PASS"
            if crash_cycle.assessment.outcome == OUTCOME_STARTED_FAILED
            and crash_cycle.exit_code == EXIT_RUN_FAILURE
            else "FAIL",
        )
    )

    timeout = RunRecord(run_id="run-timeout", slot=SLOT, started_at=SLOT, finished_at=None)
    timeout_cycle = cycle(timeout, MonitoringState(), now=SLOT + TIMEOUT)
    scenarios.append(
        scenario(
            "TIMEOUT",
            "Run still open at the configured deadline.",
            timeout_cycle,
            result="PASS"
            if timeout_cycle.assessment.outcome == OUTCOME_TIMED_OUT
            else "FAIL",
        )
    )

    stages = [stage(name) for name in REQUIRED_STAGES]
    stages[1] = stage(STAGE_FORECAST_GENERATION, expected=10, persisted=8)
    partial = RunRecord(
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
    partial_cycle = cycle(partial, MonitoringState())
    scenarios.append(
        scenario(
            "PARTIAL_COMPLETION",
            "Forecast stage completed in memory but persisted 8 of 10 rows.",
            partial_cycle,
            missing_stages=list(partial_cycle.assessment.missing_stages),
            result="PASS"
            if partial_cycle.assessment.outcome == OUTCOME_PARTIAL
            and partial_cycle.exit_code == EXIT_RUN_FAILURE
            else "FAIL",
        )
    )

    late = RunRecord(
        run_id="run-late",
        slot=SLOT,
        started_at=SLOT,
        finished_at=SLOT + timedelta(hours=2),
        stages=tuple(stage(name) for name in REQUIRED_STAGES),
        eligible_fixture_count=3,
        discovered_fixture_count=3,
        reconciled_fixture_count=3,
        reconciliation_completed=True,
    )
    late_cycle = cycle(late, MonitoringState(), now=SLOT + TIMEOUT)
    scenarios.append(
        scenario(
            "LATE_COMPLETION",
            "Durable completion arrived after the slot deadline.",
            late_cycle,
            late_finish_at=late_cycle.assessment.late_finish_at.isoformat()
            if late_cycle.assessment.late_finish_at
            else None,
            result="PASS"
            if late_cycle.assessment.outcome == OUTCOME_TIMED_OUT
            else "FAIL",
        )
    )

    empty_cycle = cycle(empty_verified(), MonitoringState())
    scenarios.append(
        scenario(
            "EMPTY_SLATE_VERIFIED",
            "Zero eligible fixtures after completed discovery and reconciliation.",
            empty_cycle,
            result="PASS"
            if empty_cycle.assessment.outcome == OUTCOME_NO_ELIGIBLE_FIXTURES
            and empty_cycle.exit_code == EXIT_HEALTHY
            else "FAIL",
        )
    )

    unverified = RunRecord(
        run_id="run-empty-unverified",
        slot=SLOT,
        started_at=SLOT,
        finished_at=SLOT + timedelta(minutes=5),
        stages=tuple(stage(name, expected=0) for name in REQUIRED_STAGES),
        eligible_fixture_count=0,
        discovered_fixture_count=0,
        reconciled_fixture_count=0,
        reconciliation_completed=False,
    )
    unverified_cycle = cycle(unverified, MonitoringState())
    scenarios.append(
        scenario(
            "EMPTY_SLATE_UNVERIFIED",
            "Zero eligible fixtures without a completed reconciliation.",
            unverified_cycle,
            result="PASS"
            if unverified_cycle.assessment.outcome == OUTCOME_PARTIAL
            else "FAIL",
        )
    )

    # Duplicate alert, recovery, and silence after recovery.
    transport = FakeTransport()
    incident = cycle(None, MonitoringState(), transport)
    duplicate = cycle(None, incident.state, transport)
    recovered = cycle(healthy(), duplicate.state, transport)
    quiet = cycle(healthy(), recovered.state, transport)
    scenarios.append(
        {
            "scenario": "DUPLICATE_AND_RECOVERY",
            "description": "One incident, suppressed duplicate, one recovery, silence.",
            "incident_action": incident.notification.action,
            "duplicate_action": duplicate.notification.action,
            "recovery_action": recovered.notification.action,
            "post_recovery_action": quiet.notification.action,
            "incident_pings": 1 if incident.ping else 0,
            "duplicate_pings": 1 if duplicate.ping else 0,
            "recovery_pings": 1 if recovered.ping else 0,
            "result": "PASS"
            if (
                incident.notification.action == ACTION_INCIDENT
                and duplicate.notification.action == ACTION_SUPPRESSED
                and recovered.notification.action == ACTION_RECOVERY
                and quiet.notification.action == ACTION_NONE
            )
            else "FAIL",
        }
    )

    failure = cycle(None, MonitoringState(), FakeTransport(fail_all=True))
    retried = cycle(None, failure.state, FakeTransport([(200, "OK")]))
    scenarios.append(
        {
            "scenario": "NOTIFICATION_FAILURE_AND_RETRY",
            "description": "Heartbeat delivery fails; state must not claim delivery.",
            "first_exit_code": failure.exit_code,
            "first_monitor_status": failure.assessment.monitor_status,
            "first_state_action": failure.state.last_action,
            "first_active_incident": failure.state.active_incident,
            "retry_action": retried.notification.action,
            "retry_active_incident": retried.state.active_incident,
            "result": "PASS"
            if (
                failure.exit_code == EXIT_MONITOR_UNAVAILABLE
                and failure.assessment.monitor_status == OUTCOME_MONITOR_UNAVAILABLE
                and failure.state.last_action == ACTION_DELIVERY_FAILED
                and failure.state.active_incident is None
                and retried.notification.action == ACTION_INCIDENT
            )
            else "FAIL",
        }
    )

    state_path = os.path.join(tmp_path, "state.json")
    write_state(state_path, incident.state)
    restarted = cycle(None, load_state(state_path))
    scenarios.append(
        {
            "scenario": "DURABLE_STATE_RESTART",
            "description": "Duplicate suppression survives a process restart.",
            "restarted_action": restarted.notification.action,
            "result": "PASS"
            if restarted.notification.action == ACTION_SUPPRESSED
            else "FAIL",
        }
    )

    success_monitor_down = cycle(
        healthy(), MonitoringState(), FakeTransport(fail_all=True)
    )
    scenarios.append(
        scenario(
            "MONITOR_UNAVAILABLE_DISTINCT",
            "Job succeeded but the heartbeat service was unreachable.",
            success_monitor_down,
            result="PASS"
            if success_monitor_down.exit_code == EXIT_MONITOR_UNAVAILABLE
            and success_monitor_down.assessment.outcome == OUTCOME_SUCCEEDED
            else "FAIL",
        )
    )

    disabled = HealthchecksClient(
        HealthchecksConfig(uuid="check-uuid", enabled=False),
        transport=FakeTransport(),
        sleep=lambda _seconds: None,
    )
    disabled_result = disabled.signal("success")
    scenarios.append(
        {
            "scenario": "MONITORING_DISABLED",
            "description": "Disabled monitoring must never claim a delivered signal.",
            "delivered": disabled_result.delivered,
            "detail": disabled_result.detail,
            "result": "PASS"
            if disabled_result.delivered is False
            and disabled_result.detail == "MONITORING_DISABLED"
            else "FAIL",
        }
    )

    cron = CronExpression("0 * * * *")
    scenarios.append(
        {
            "scenario": "INDEPENDENT_SCHEDULE_DERIVATION",
            "description": "Hourly slots are derived from cron, not from run rows.",
            "slot_count": len(cron.slots(window_start, window_end)),
            "first_slot": cron.slots(window_start, window_end)[0].isoformat(),
            "last_slot": cron.slots(window_start, window_end)[-1].isoformat(),
            "result": "PASS"
            if len(cron.slots(window_start, window_end)) == 98
            else "FAIL",
        }
    )

    return {
        "version": VERSION,
        "report_as_of_utc": datetime.now(UTC).isoformat(),
        "clock_fixed_at": SLOT.isoformat(),
        "mode": "ISOLATED_SIMULATION",
        "provider_calls": 0,
        "database_writes": 0,
        "network_calls": 0,
        "external_account_provisioned": False,
        "paid_services_activated": False,
        "activation_status": "NOT_ACTIVATED",
        "activation_blockers": [
            "Owner approval not requested.",
            "No external monitoring account provisioned.",
            "No notifier destination or on-call route authorised.",
            "No writable durable state path assigned in production.",
            "The production pipeline does not yet publish a durable per-slot "
            "receipt; without it the monitor fails closed rather than reporting "
            "a false success.",
        ],
        "configuration": {
            "streams_defined": [
                "FIXTURE_DISCOVERY",
                "FORECAST_GENERATION",
                "BOOKMAKER_ACQUISITION",
                "INDEPENDENT_ARCHIVAL",
                "OFFICIAL_SETTLEMENT",
                "BOOKMAKER_SETTLEMENT",
                "PROSPECTIVE_CANDIDATE",
            ],
            "credentials_location": "SERVER_ENVIRONMENT_ONLY",
            "external_account_provisioned": False,
        },
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "all_passed": all(item.get("result") == "PASS" for item in scenarios),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-json",
        default="docs/research/evidence/monitoring_acceptance_20261127.json",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="optional scratch directory for the durable-state rehearsal",
    )
    args = parser.parse_args(argv)
    evidence = build_evidence(args.workdir)
    text = json.dumps(evidence, indent=2, sort_keys=True)
    print(text)
    if args.out_json:
        directory = os.path.dirname(os.path.abspath(args.out_json))
        os.makedirs(directory, exist_ok=True)
        with open(args.out_json, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0 if evidence["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
