"""Freeze the specific actionable pending population, without claiming settlement."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / (
    "research/tennis_pending_recovery_20261030/actionable_17.csv"
)
EXPECTED_IDS = {
    117860, 130363, 130686, 130717, 131036, 130903, 131307,
    131446, 131527, 131536, 131637, 131636, 131712,
    132211, 132533, 132535, 132664,
}
EXPECTED_ACTION_COUNTS = {
    "BACKUP_GATED_WINNERLESS_WALKOVER": 4,
    "REVIEW_CANONICAL_API_TENNIS_ID": 1,
    "VERIFY_HISTORICAL_PROVIDER_EVENT": 9,
    "VERIFY_TERMINAL_WINNER_EVIDENCE": 3,
}


def test_actionable_snapshot_is_unique_and_matches_production_diagnostics() -> None:
    with MANIFEST.open(encoding="utf-8", newline="") as fh:
        items = list(csv.DictReader(fh))
    ids = [int(x["match_id"]) for x in items]
    assert len(ids) == 17
    assert len(set(ids)) == 17
    assert set(ids) == EXPECTED_IDS
    assert dict(Counter(x["next_action"] for x in items)) == EXPECTED_ACTION_COUNTS
    assert all(x["latest_start_utc_date"].startswith("2026-") for x in items)
    assert all(x["last_fixture_observed_utc"].endswith("Z") for x in items)
    assert all(x["last_fixture_status"] in {"SCHEDULED", "COMPLETED", "WALKOVER"}
               for x in items)


def test_no_match_is_preemptively_scored_and_type_a_is_explicit() -> None:
    text = MANIFEST.read_text(encoding="utf-8")
    assert "winner_side" not in text
    assert "result_status" not in text
    with MANIFEST.open(encoding="utf-8", newline="") as fh:
        rows = {int(x["match_id"]): x for x in csv.DictReader(fh)}
    assert {i for i, x in rows.items() if "TYPE_A_BET" in x["flags"]} == {
        130717, 132211,
    }
    assert {i for i, x in rows.items()
            if x["next_action"] == "BACKUP_GATED_WINNERLESS_WALKOVER"} == {
        132211, 132533, 132535, 132664,
    }
    assert all(x["last_fixture_status"] == "WALKOVER"
               for x in rows.values()
               if x["next_action"] == "BACKUP_GATED_WINNERLESS_WALKOVER")
    assert rows[130903]["api_tennis_event_id"] == ""
    assert rows[131036]["api_tennis_event_id"] == ""
