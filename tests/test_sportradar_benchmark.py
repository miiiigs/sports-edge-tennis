from datetime import UTC, datetime

import sports_edge_tennis.live.sportradar_benchmark as benchmark_module
from sports_edge_tennis.live.sportradar_benchmark import (
    CanonicalGapTarget,
    benchmark_challenger_a2_stats,
    deterministic_name_keys,
    link_gap_target,
    load_latest_gap_targets,
)


def _event(
    event_id,
    start_time,
    home_id,
    home_name,
    away_id,
    away_name,
    *,
    stats=None,
    status="closed",
    category_name="Challenger",
    competition_name="ATP Challenger Rennes Men Singles",
    competition_level="challenger",
):
    row = {
        "sport_event": {
            "id": event_id,
            "start_time": start_time,
            "sport_event_context": {
                "category": {"name": category_name},
                "competition": {
                    "id": "sr:competition:1",
                    "name": competition_name,
                    "level": competition_level,
                },
            },
            "competitors": [
                {"id": home_id, "name": home_name, "qualifier": "home"},
                {"id": away_id, "name": away_name, "qualifier": "away"},
            ],
        },
        "sport_event_status": {"status": status},
    }
    if stats is not None:
        row["statistics"] = {
            "totals": {
                "competitors": [
                    {
                        "id": home_id,
                        "name": home_name,
                        "qualifier": "home",
                        "statistics": stats[0],
                    },
                    {
                        "id": away_id,
                        "name": away_name,
                        "qualifier": "away",
                        "statistics": stats[1],
                    },
                ]
            }
        }
    return row


def _core_stats(**extra):
    row = {
        "first_serve_points_won": 31,
        "second_serve_points_won": 13,
        "service_points_won": 44,
        "service_points_lost": 28,
        "first_serve_successful": 48,
        "second_serve_successful": 20,
    }
    row.update(extra)
    return row


def _target():
    return CanonicalGapTarget(
        match_id=130490,
        player_1_id=1,
        player_1_name="Dar. Blanch",
        player_2_id=2,
        player_2_name="B. Nakashima",
        match_start_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        tournament_id=628,
        tournament_name="Rennes",
        season_year=2026,
        player_1_identity_status="HISTORICAL",
        player_2_identity_status="LIVE_UNRESOLVED",
        surface_status="UNRESOLVED",
    )


def test_deterministic_name_keys_bridge_sportradar_order_and_initials():
    darwin = deterministic_name_keys("Blanch, Darwin")
    abbreviated = deterministic_name_keys("Dar. Blanch")
    brandon = deterministic_name_keys("Nakashima, Brandon")
    short = deterministic_name_keys("B. Nakashima")

    assert darwin & abbreviated
    assert brandon & short


def test_gap_link_resolves_unique_participant_pair_inside_time_window():
    payload = {
        "summaries": [
            _event(
                "sr:sport_event:10",
                "2026-09-18T13:20:00+00:00",
                "sr:competitor:873695",
                "Blanch, Darwin",
                "sr:competitor:294300",
                "Nakashima, Brandon",
            )
        ]
    }

    result = link_gap_target(_target(), payload)

    assert result.status == "EXACT"
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.start_delta_minutes == 80
    assert candidate.player_1_sportradar_id == "sr:competitor:873695"
    assert candidate.player_2_sportradar_id == "sr:competitor:294300"


def test_gap_link_resolves_atp_main_event_without_challenger_filter():
    payload = {
        "summaries": [
            _event(
                "sr:sport_event:12",
                "2026-09-18T12:40:00+00:00",
                "sr:competitor:873695",
                "Blanch, Darwin",
                "sr:competitor:294300",
                "Nakashima, Brandon",
                category_name="Davis Cup",
                competition_name="Davis Cup World Group I",
                competition_level="atp",
            )
        ]
    }

    result = link_gap_target(_target(), payload)

    assert result.status == "EXACT"
    assert result.candidates[0].event_id == "sr:sport_event:12"
    assert result.candidates[0].competition_name == "Davis Cup World Group I"


def test_gap_link_fails_closed_when_multiple_events_fit():
    payload = {
        "summaries": [
            _event(
                "sr:sport_event:10",
                "2026-09-18T12:30:00+00:00",
                "sr:competitor:873695",
                "Blanch, Darwin",
                "sr:competitor:294300",
                "Nakashima, Brandon",
            ),
            _event(
                "sr:sport_event:11",
                "2026-09-18T12:45:00+00:00",
                "sr:competitor:873695",
                "Blanch, Darwin",
                "sr:competitor:861975",
                "Nakashima, Bryce",
            ),
        ]
    }

    result = link_gap_target(_target(), payload)

    assert result.status == "AMBIGUOUS"
    assert len(result.candidates) == 2


def test_gap_link_reports_unique_pair_outside_time_window_without_linking():
    payload = {
        "summaries": [
            _event(
                "sr:sport_event:10",
                "2026-09-18T18:30:00+00:00",
                "sr:competitor:873695",
                "Blanch, Darwin",
                "sr:competitor:294300",
                "Nakashima, Brandon",
            )
        ]
    }

    result = link_gap_target(_target(), payload)

    assert result.status == "TIME_MISMATCH"
    assert result.candidates[0].start_delta_minutes == 390


def test_a2_completeness_requires_two_sided_core_serve_fields():
    complete = _event(
        "sr:sport_event:20",
        "2026-09-18T12:00:00+00:00",
        "sr:competitor:1",
        "Player, One",
        "sr:competitor:2",
        "Player, Two",
        stats=(_core_stats(), _core_stats()),
    )
    partial = _event(
        "sr:sport_event:21",
        "2026-09-18T13:00:00+00:00",
        "sr:competitor:3",
        "Player, Three",
        "sr:competitor:4",
        "Player, Four",
        stats=(
            _core_stats(),
            _core_stats(second_serve_points_won=None),
        ),
    )
    absent = _event(
        "sr:sport_event:22",
        "2026-09-18T14:00:00+00:00",
        "sr:competitor:5",
        "Player, Five",
        "sr:competitor:6",
        "Player, Six",
        stats=None,
        status="not_started",
    )

    result = benchmark_challenger_a2_stats(
        {"summaries": [complete, partial, absent]}
    )

    assert result.challenger_total == 3
    assert result.closed_total == 2
    assert result.summaries_with_statistics == 2
    assert result.two_sided_statistics == 2
    assert result.a2_core_ready == 1
    assert result.serve_detail_ready == 1
    assert result.closed_with_statistics == 2
    assert result.closed_a2_core_ready == 1
    assert ("NO_STATISTICS", 1) in result.missing_reasons
    assert ("second_serve_points_won", 1) in result.missing_reasons


def test_a2_core_rejects_incoherent_service_point_totals():
    bad = _event(
        "sr:sport_event:30",
        "2026-09-18T12:00:00+00:00",
        "sr:competitor:1",
        "Player, One",
        "sr:competitor:2",
        "Player, Two",
        stats=(
            _core_stats(
                first_serve_points_won=50,
                second_serve_points_won=30,
                service_points_won=60,
                service_points_lost=10,
            ),
            _core_stats(),
        ),
    )

    result = benchmark_challenger_a2_stats({"summaries": [bad]})

    assert result.a2_core_ready == 0



class _RowsCursor:
    def __init__(self, *, one=None, many=None):
        self._one = one
        self._many = many or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class _GapBatchConn:
    def __init__(self, latest_row, target_rows):
        self.latest_row = latest_row
        self.target_rows = target_rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "ORDER BY as_of DESC, prediction_run_id DESC" in sql and "LIMIT 1" in sql:
            return _RowsCursor(one=self.latest_row)
        if "WITH batch_runs AS" in sql:
            return _RowsCursor(many=self.target_rows)
        raise AssertionError(f"unexpected query: {sql}")


def test_gap_loader_unions_latest_multi_slate_batch(monkeypatch):
    latest_as_of = datetime(2026, 9, 18, 15, 3, 52, tzinfo=UTC)
    first_as_of = datetime(2026, 9, 18, 15, 3, 40, tzinfo=UTC)
    conn = _GapBatchConn(
        latest_row=(312, latest_as_of),
        target_rows=[
            (
                130431,
                first_as_of,
                10,
                "Player One",
                11,
                "Player Two",
                datetime(2026, 9, 18, 18, 0, tzinfo=UTC),
                None,
                None,
                "ATP_MAIN",
                "ATP_MAIN",
                659,
                "ATP Davis Cup - World Group I",
                2026,
            ),
            (
                130500,
                latest_as_of,
                12,
                "Player Three",
                13,
                "Player Four",
                datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
                None,
                None,
                "ATP_MAIN",
                "ATP_MAIN",
                659,
                "ATP Davis Cup - World Group I",
                2026,
            ),
        ],
    )
    resolved_as_of = []

    def fake_resolve_player(conn_arg, *, player_id, as_of):
        assert conn_arg is conn
        resolved_as_of.append((player_id, as_of))
        return player_id

    monkeypatch.setattr(benchmark_module, "_resolve_model_player_id", fake_resolve_player)
    monkeypatch.setattr(
        benchmark_module,
        "_model_identity_status",
        lambda conn_arg, **kwargs: "BRIDGED",
    )
    monkeypatch.setattr(
        benchmark_module,
        "_resolve_model_surface",
        lambda conn_arg, **kwargs: (None, "UNRESOLVED"),
    )

    latest_id, batch_as_of, gaps = load_latest_gap_targets(
        conn,
        target_date=date(2026, 9, 18),
    )

    assert latest_id == 312
    assert batch_as_of == latest_as_of
    assert [gap.match_id for gap in gaps] == [130431, 130500]
    assert resolved_as_of == [
        (10, first_as_of),
        (11, first_as_of),
        (12, latest_as_of),
        (13, latest_as_of),
    ]

    batch_sql, batch_params = conn.calls[1]
    assert "WITH batch_runs AS" in batch_sql
    assert "DISTINCT ON (prt.match_id)" in batch_sql
    assert "make_interval(mins => %s)" in batch_sql
    assert "WHERE prt.prediction_run_id = %s" not in batch_sql
    assert batch_params == (latest_as_of, 15, latest_as_of)
