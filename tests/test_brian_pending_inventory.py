"""Read-only pending inventory includes all rows, never only actionable cases."""
from datetime import UTC, date, datetime, timedelta

from ops.brian_pending_inventory import detailed_inventory
from sports_edge_tennis.live.pending_result_diagnostics import (
    PendingPickEvidence, PendingPickDiagnosis, PendingResultDiagnosticsReport,
)


def test_inventory_keeps_future_and_terminal_cases_and_marks_unverified():
    now = datetime(2026, 11, 11, 9, tzinfo=UTC)
    cases = []
    for idx, (reason, age) in enumerate([
        ("NOT_STARTED", -3),
        ("AWAITING_RESULT", 2),
        ("TERMINAL_FIXTURE_WITHOUT_RESULT", 230),
        ("NO_API_TENNIS_LINK", 1000),
        ("PROVIDER_FIXTURE_NOT_REOBSERVED", 49),
    ], start=1):
        ev = PendingPickEvidence(
            match_id=130000+idx, pick_source="MANAGER_CONSENSUS",
            picked_at=now-timedelta(days=2), selected_side=1,
            has_type_a_bet=False,
            pit_match_start_at=now-timedelta(hours=age),
            latest_match_start_at=now-timedelta(hours=age),
            latest_match_status=(
                "WALKOVER" if reason=="TERMINAL_FIXTURE_WITHOUT_RESULT"
                else "SCHEDULED"
            ),
            last_fixture_observed_at=now-timedelta(hours=6),
            fixture_versions=2, source_systems=("API_TENNIS",),
            api_tennis_event_key="12345", tournament="Test Tour",
            competition_tier="CHALLENGER", player_1="A", player_2="B",
        )
        cases.append(PendingPickDiagnosis(
            evidence=ev, reason=reason, flags=(),
            age_bucket="NOT_STARTED" if age<0 else "GT_72H",
            hours_since_start=age, hours_since_last_observed=6,
        ))
    report = PendingResultDiagnosticsReport(
        generated_at=now, recovery_window_start=date(2026,11,9),
        recovery_window_end=date(2026,11,11), items=tuple(cases),
    )
    data = detailed_inventory(report)
    assert data["total"] == 5
    assert len(data["items"]) == 5
    assert data["unverified_future_count"] == 1
    assert data["older_than_24h"] == 3
    assert data["older_than_72h"] == 2
    assert data["status"] == "STORED_EVIDENCE_NOT_PROVIDER_LIVE_RECONFIRMED"
    rows = {x["reason"]:x for x in data["items"]}
    assert rows["NOT_STARTED"]["provider_independently_rechecked_now"] is False
    assert rows["TERMINAL_FIXTURE_WITHOUT_RESULT"]["overdue_alert"] == "OVER_72_HOURS"
    assert "DO_NOT_INFER" in rows["TERMINAL_FIXTURE_WITHOUT_RESULT"]["recommended_action"]
    assert rows["NO_API_TENNIS_LINK"]["player_1"] == "A"
    assert rows["PROVIDER_FIXTURE_NOT_REOBSERVED"]["player_2"] == "B"
