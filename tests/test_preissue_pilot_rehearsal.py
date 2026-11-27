from __future__ import annotations

from sports_edge_tennis.validation.preissue_pilot_rehearsal import (
    run_rehearsal,
)


def test_rehearsal_completes_the_full_sequence_and_fails_closed():
    report = run_rehearsal()
    assert all(report["assertions"].values())
    assert report["provider_calls"] > 0
    assert report["actual_reservations"] == report["provider_calls"]
    assert report["original_time_qualified"] == 3
    assert report["production_side_effects"] == {
        "provider_calls": 0,
        "database_writes": 0,
        "production_budget_used": 0,
    }

    happy = report["scenarios"]["happy_path"]
    assert happy["identity_resolution"]["orientation"] == "DIRECT"
    assert happy["reservation_rows"] == 3
    assert happy["persisted_observations"] == 3
    assert happy["qualified"] == 2
    assert happy["forecast"]["issued"] is True

    for name in (
        "late_response", "crash", "missing_prices", "rescheduled_fixture",
        "lost_acknowledgement", "retrospective_reconstruction",
    ):
        scenario = report["scenarios"][name]
        assert scenario["qualified"] == 0, name
        assert scenario["forecast"]["abstained"] is True, name
