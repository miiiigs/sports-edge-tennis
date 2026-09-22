from __future__ import annotations

import csv
import json
from datetime import date

from sports_edge_tennis.validation import provider_cutover_rehearsal as rehearsal


def test_rehearsal_writes_shadow_artifacts_without_provider_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(
        rehearsal,
        "load_secret",
        lambda name: "secret" if name == "ODDSPAPI_API_KEY" else None,
    )

    result = rehearsal.run_rehearsal(
        output_dir=tmp_path,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 21),
        fetch_providers=False,
    )

    expected = {
        "historical-summary.json",
        "oddspapi-fixture-chunks.csv",
        "sportradar-reconciliation.csv",
        "oddspapi-reconciliation.csv",
        "old-vs-new.csv",
        "market-comparison.csv",
        "settlement-validation.csv",
        "clv-validation.csv",
        "request-economics.json",
        "acceptance-gates.json",
    }
    assert {path.name for path in tmp_path.iterdir()} == expected
    summary = json.loads((tmp_path / "historical-summary.json").read_text())
    assert summary["run_id"] == rehearsal.RUN_ID
    assert summary["lineage"]["odds_provider"] == "ODDSPAPI_V4"
    assert summary["production_mutation"] is False
    assert result["gates"]["overall"] == "FAIL"


def test_rehearsal_uses_daily_completed_fixture_chunks(tmp_path, monkeypatch):
    calls = []

    def fake_fixtures(api_key, *, from_at, to_at, status_id, bookmakers):
        calls.append((from_at, to_at, status_id, bookmakers))
        day = from_at.date().isoformat()
        return from_at, [
            {
                "fixtureId": f"fix-{day}",
                "sportId": 12,
                "tournamentId": 10,
                "participant1Id": "p1",
                "participant1Name": "Alpha Player",
                "participant2Id": "p2",
                "participant2Name": "Beta Player",
                "startTime": f"{day}T12:00:00Z",
            }
        ]

    monkeypatch.setattr(rehearsal, "load_secret", lambda name: "secret")
    monkeypatch.setattr(rehearsal, "fetch_oddspapi_tennis_fixtures", fake_fixtures)
    monkeypatch.setattr(
        rehearsal,
        "fetch_oddspapi_tennis_odds_by_tournaments",
        lambda *args, **kwargs: (calls[-1][0], []),
    )

    rehearsal.run_rehearsal(
        output_dir=tmp_path,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 12),
    )

    assert len(calls) == 3
    assert {call[2] for call in calls} == {2}
    assert all((call[1] - call[0]).total_seconds() == 24 * 60 * 60 for call in calls)

    with (tmp_path / "oddspapi-fixture-chunks.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["fixture_rows"] for row in rows] == ["1", "1", "1"]
    assert [row["unique_fixture_rows"] for row in rows] == ["1", "2", "3"]


def test_oddspapi_bookmaker_state_records_active_prices(tmp_path, monkeypatch):
    payload = {
        "fixtureId": "fix-1",
        "sportId": 12,
        "tournamentId": 99,
        "participant1Id": "p1",
        "participant1Name": "Alpha Player",
        "participant2Id": "p2",
        "participant2Name": "Beta Player",
        "startTime": "2026-09-10T12:00:00Z",
        "updatedAt": "2026-09-10T11:00:00Z",
        "externalProviders": {"betradarId": "sr:sport_event:123"},
        "bookmakerOdds": {
            "pinnacle+30": {
                "suspended": False,
                "markets": {
                    "121": {
                        "marketActive": True,
                        "outcomes": {
                            "121": {"players": {"0": {"active": True, "price": "1.90", "changedAt": "2026-09-10T11:59:00Z"}}},
                            "122": {"players": {"0": {"active": True, "price": "2.05", "changedAt": "2026-09-10T11:59:01Z"}}},
                        },
                    }
                },
            }
        },
    }
    monkeypatch.setattr(rehearsal, "load_secret", lambda name: "secret")
    monkeypatch.setattr(
        rehearsal,
        "_fetch_oddspapi_window",
        lambda **kwargs: ([payload], [payload], [], []),
    )

    rehearsal.run_rehearsal(
        output_dir=tmp_path,
        start_date=date(2026, 9, 10),
        end_date=date(2026, 9, 10),
    )

    with (tmp_path / "oddspapi-reconciliation.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["PINNACLE_status"] == "AVAILABLE"
    assert rows[0]["PINNACLE_player_1_price"] == "1.90"
    assert rows[0]["PINNACLE_player_2_price"] == "2.05"
    assert rows[0]["BET365_status"] == "MISSING"
