from __future__ import annotations

from decimal import Decimal

from sports_edge_tennis.dashboard.evidence_app import _summary_payload
from sports_edge_tennis.performance import CalibrationBin, ProspectivePerformanceSummary


def test_summary_payload_is_json_safe_and_keeps_evidence_layers_separate():
    summary = ProspectivePerformanceSummary(
        initialized=True,
        total_decisions=9,
        bet_decisions=3,
        no_bet_decisions=5,
        vetoed_decisions=1,
        settled_bets=2,
        pending_bets=1,
        wins=1,
        losses=1,
        one_unit_profit=Decimal("0.15"),
        roi=Decimal("0.075"),
        hit_rate=0.5,
        calibration_sample=7,
        brier_score=0.21,
        log_loss=0.62,
        ece=0.04,
        bins=(CalibrationBin(0.5, 0.6, 7, 0.56, 0.57, 0.01),),
    )

    payload = _summary_payload(summary)

    assert payload["decision_counts"] == {
        "total": 9,
        "bet": 3,
        "no_bet": 5,
        "vetoed": 1,
    }
    assert payload["settlement"]["one_unit_profit"] == "0.15"
    assert payload["settlement"]["roi"] == "0.075"
    assert payload["calibration"]["sample"] == 7
    assert payload["calibration"]["bins"][0]["absolute_gap"] == 0.01
