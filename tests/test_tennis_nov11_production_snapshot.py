"""Live Nov6-10 scorecard evidence, frozen at 2026-11-11 13:04 PHT.

Does NOT assert A3 or A4 accuracy. Pending and void are kept distinct.
"""
import json
from pathlib import Path

import pytest

from research.tennis_a3_a4_guide_20261110.analyze_production_snapshot import (
    analyze_snapshot,
)


def test_nov11_1304_complete_five_day_source_baseline_and_uncertainty():
    path = Path(__file__).parents[1] / (
        "research/tennis_a3_a4_guide_20261110/"
        "production_snapshot_20261111_1205PHT.json"
    )
    report = analyze_snapshot(json.loads(path.read_text(encoding="utf-8")))
    assert report["period"] == {"start": "2026-11-06", "end": "2026-11-10"}
    assert report["totals"]["original"] == {
        "picks": 127, "won": 74, "lost": 42, "void": 6,
        "pending": 5, "graded": 116,
    }
    assert report["totals"]["confidence70"] == {
        "picks": 58, "won": 38, "lost": 15, "void": 3,
        "pending": 2, "graded": 53,
    }
    assert report["totals"]["manager_only"] == {
        "picks": 67, "won": 38, "lost": 24, "void": 3,
        "pending": 2, "graded": 62,
    }
    assert report["original_win_rate"] == pytest.approx(74/116)
    assert report["confidence70_win_rate"] == pytest.approx(38/53)
    assert report["manager_only_win_rate"] == pytest.approx(38/62)
    assert report["confidence70_selection_coverage"] == pytest.approx(58/127)
    assert report["avoided_losses"] == 27
    assert report["missed_winners"] == 36
    assert report["fully_settled_days_met_73"] == {
        "original": 2, "confidence70": 2, "manager_only": 1,
    }


def test_nov9_below_73_even_if_last_pending_wins_and_nov10_is_not_a4():
    path = Path(__file__).parents[1] / (
        "research/tennis_a3_a4_guide_20261110/"
        "production_snapshot_20261111_1205PHT.json"
    )
    data = analyze_snapshot(json.loads(path.read_text(encoding="utf-8")))
    days = {day["date"]: day for day in data["daily"]}
    nov9 = days["2026-11-09"]["confidence70"]
    assert (nov9["won"], nov9["lost"], nov9["void"], nov9["pending"]) == (
        17, 7, 3, 1,
    )
    assert nov9["best_if_all_pending_win"] == pytest.approx(18/25)
    assert nov9["research_day_status"] == "UNSETTLED_MAX_BELOW_73"
    nov10 = days["2026-11-10"]
    assert nov10["original"]["won"] == 17
    assert nov10["original"]["lost"] == 4
    assert nov10["confidence70"]["won"] == 7
    assert nov10["confidence70"]["lost"] == 1
    assert nov10["confidence70"]["research_day_status"] == "MET_73"
    assert data["status"] == "OBSERVATIONAL_BASELINE_ONLY_NOT_A4_V3_VALIDATION"
