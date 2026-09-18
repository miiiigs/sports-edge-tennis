from datetime import UTC, datetime, timedelta

from sports_edge_tennis.live.sportradar_a3_probe import (
    AGENT_CODE,
    A3SportradarTarget,
    link_target,
    load_targets,
    summarize_evidence,
)


def _summary(event_id, start, first_id, first_name, second_id, second_name, *, status="closed", level="challenger"):
    return {
        "sport_event": {
            "id": event_id,
            "start_time": start,
            "sport_event_context": {
                "category": {"name": "Challenger"},
                "competition": {
                    "id": "sr:competition:1",
                    "name": "ATP Challenger Test Men Singles",
                    "type": "singles",
                    "level": level,
                },
            },
            "competitors": [
                {"id": first_id, "name": first_name},
                {"id": second_id, "name": second_name},
            ],
        },
        "sport_event_status": {"status": status},
    }


def test_link_target_resolves_unique_pair_by_name_and_time():
    target = A3SportradarTarget(
        match_id=10,
        first_name="Dar. Blanch",
        second_name="B. Nakashima",
        match_start_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        surface="HARD",
    )
    payload = {
        "summaries": [
            _summary(
                "sr:sport_event:10",
                "2026-09-18T13:20:00+00:00",
                "sr:competitor:873695",
                "Blanch, Darwin",
                "sr:competitor:294300",
                "Nakashima, Brandon",
                status="not_started",
            )
        ]
    }

    status, candidates = link_target(target, payload)

    assert status == "EXACT"
    assert len(candidates) == 1
    assert candidates[0].first_id == "sr:competitor:873695"
    assert candidates[0].second_id == "sr:competitor:294300"
    assert candidates[0].start_delta_minutes == 80


def test_link_target_fails_closed_on_multiple_in_window_candidates():
    target = A3SportradarTarget(
        match_id=10,
        first_name="Dar. Blanch",
        second_name="B. Nakashima",
        match_start_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        surface="HARD",
    )
    payload = {
        "summaries": [
            _summary(
                "sr:sport_event:10",
                "2026-09-18T12:20:00+00:00",
                "sr:competitor:873695",
                "Blanch, Darwin",
                "sr:competitor:294300",
                "Nakashima, Brandon",
                status="not_started",
            ),
            _summary(
                "sr:sport_event:11",
                "2026-09-18T12:40:00+00:00",
                "sr:competitor:873695",
                "Blanch, Darwin",
                "sr:competitor:294300",
                "Nakashima, Brandon",
                status="not_started",
            ),
        ]
    }

    status, candidates = link_target(target, payload)

    assert status == "AMBIGUOUS"
    assert len(candidates) == 2


def test_summarize_evidence_uses_same_agent3_raw_thresholds():
    p1 = "sr:competitor:1"
    p2 = "sr:competitor:2"
    shared = [f"sr:competitor:{n}" for n in range(100, 105)]

    h2h = {
        "summaries": [
            _summary("h1", "2026-01-01T12:00:00+00:00", p1, "P1", p2, "P2"),
            _summary("h2", "2026-02-01T12:00:00+00:00", p2, "P2", p1, "P1"),
        ]
    }
    first = {
        "summaries": [
            _summary(f"f{i}", "2026-03-01T12:00:00+00:00", p1, "P1", opp, f"O{i}")
            for i, opp in enumerate(shared)
        ]
    }
    second = {
        "summaries": [
            _summary(f"s{i}", "2026-03-02T12:00:00+00:00", p2, "P2", opp, f"O{i}")
            for i, opp in enumerate(shared)
        ]
    }

    evidence = summarize_evidence(
        h2h,
        first,
        second,
        first_id=p1,
        second_id=p2,
    )

    assert evidence.direct_matches == 2
    assert evidence.shared_opponents == 5
    assert evidence.direct_support_met is True
    assert evidence.shared_support_met is True


def test_summarize_evidence_ignores_open_and_doubles_rows():
    p1 = "sr:competitor:1"
    p2 = "sr:competitor:2"
    open_row = _summary(
        "open",
        "2026-09-18T12:00:00+00:00",
        p1,
        "P1",
        p2,
        "P2",
        status="not_started",
    )
    doubles = _summary(
        "doubles",
        "2026-08-18T12:00:00+00:00",
        p1,
        "P1",
        p2,
        "P2",
    )
    doubles["sport_event"]["sport_event_context"]["competition"]["type"] = "doubles"

    evidence = summarize_evidence(
        {"summaries": [open_row, doubles]},
        {"summaries": []},
        {"summaries": []},
        first_id=p1,
        second_id=p2,
    )

    assert evidence.direct_matches == 0
    assert evidence.direct_support_met is False


class _FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return _FakeRows(self.rows)


def test_load_targets_mirrors_api_tennis_forward_horizon_contract():
    as_of = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    start_at = datetime(2026, 9, 19, 9, 0, tzinfo=UTC)
    conn = _FakeConn(
        [(101, "Player One", "Player Two", start_at, "HARD")]
    )

    targets = load_targets(conn, as_of=as_of, horizon_days=3, limit=10)

    assert targets == (
        A3SportradarTarget(
            match_id=101,
            first_name="Player One",
            second_name="Player Two",
            match_start_at=start_at,
            surface="HARD",
        ),
    )
    assert conn.params == (
        AGENT_CODE,
        as_of,
        as_of + timedelta(days=3),
        10,
    )
    assert "api_p1.source_system = 'API_TENNIS'" in conn.sql
    assert "api_p2.source_system = 'API_TENNIS'" in conn.sql
    assert "mv.match_start_at >= %s" in conn.sql
    assert "mv.match_start_at < %s" in conn.sql
