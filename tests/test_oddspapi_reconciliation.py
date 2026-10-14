from __future__ import annotations

from datetime import UTC, datetime

from sports_edge_tennis.live.oddspapi import parse_oddspapi_tennis_fixture
from sports_edge_tennis.live.oddspapi_reconciliation import reconcile_oddspapi_event


class _Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Conn:
    def __init__(self, start_at):
        self.start_at = start_at
        self.calls = []
        self.other_fixture_rows = []

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split()).lower()
        self.calls.append((normalized, params))
        if "where source_system = %s and source_match_id = %s" in normalized:
            return _Result([])
        if "source_match_id is distinct from %s" in normalized:
            return _Result(list(self.other_fixture_rows))
        if (
            "select distinct match_id" in normalized
            and "source_match_id = any" in normalized
        ):
            assert params[0] == "SPORTRADAR"
            assert "sr:sport_event:69653792" in params[1]
            return _Result([(77,)])
        if "with latest as" in normalized:
            return _Result(
                [
                    (
                        77,
                        700,
                        11,
                        22,
                        self.start_at,
                        "Carlos Alcaraz",
                        "Jannik Sinner",
                    )
                ]
            )
        raise AssertionError(normalized)


def _fixture():
    return parse_oddspapi_tennis_fixture(
        {
            "fixtureId": "id1200257971563726",
            "participant1Id": 1001,
            "participant2Id": 1002,
            "participant1Name": "Carlos Alcaraz",
            "participant2Name": "Jannik Sinner",
            "sportId": 12,
            "tournamentId": 500,
            "startTime": "2026-09-21T15:00:00Z",
            "externalProviders": {"betradarId": 69653792},
        }
    )


def test_betradar_bridge_resolves_exact_candidate_without_writing() -> None:
    conn = _Conn(datetime(2026, 9, 21, 15, 0, tzinfo=UTC))
    result = reconcile_oddspapi_event(
        conn,
        envelope=_fixture(),
        candidate_match_ids=(77, 88),
        as_of=datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
        persist=False,
    )
    assert result.status == "EXACT"
    assert result.reason == "BETRADAR_ID"
    assert result.match_id == 77
    assert result.candidate is not None
    assert result.candidate.provider_orientation == "DIRECT"
    assert result.candidate.start_delta_minutes == 0
