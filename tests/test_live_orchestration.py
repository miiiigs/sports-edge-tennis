from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS

import psycopg
import pytest

from sports_edge_tennis.live.orchestrator import (
    BlockedPinnapiBookmakerMapping,
    LivePilotApiTennisFailed,
    LivePilotPinnapiFailed,
    LivePilotSportradarFailed,
    _client_state,
    _final_status,
    fixture_reconciliation_summary,
    run_live_mvp,
    unsupported_event_status_counts,
)
from sports_edge_tennis.live.transport import TransportError
from sports_edge_tennis.odds.persistence import insert_bookmaker_mapping


FIXED = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
SLATE = date(2026, 9, 5)


def _ingest_outcome(match_ids, *, rejected_rows=()):
    return NS(
        rows_seen=len(match_ids) + len(rejected_rows),
        rows_ingested=len(match_ids),
        rows_rejected=len(rejected_rows),
        status="COMPLETED",
        matches=tuple(NS(match_id=m, match_status="SCHEDULED") for m in match_ids),
        rejected_rows=tuple(rejected_rows),
    )


def _run_outcome(prediction_run_id=777):
    return NS(prediction_run_id=prediction_run_id)


class Recorder:
    def __init__(self):
        self.events = []
        self.results = []

    def process(self, conn, event, board_identity, received_at, consensus_ids, as_of):
        self.events.append((event, board_identity, tuple(consensus_ids), as_of))
        result = {
            "event_id": event.provider_event_id,
            "reconciliation_status": "RESOLVED",
            "match_id": event._match_id if hasattr(event, "_match_id") else 1001,
            "normalized": True,
        }
        self.results.append(result)
        return result


def _run(*, consensus_ids, board_payload=None, recorder=None, pinnapi_key="K", build_entries=None):
    fetch_calls = []

    def fetch_fixtures(key, slate):
        return FIXED, [{"event_key": "e1"}]

    def fetch_board(key):
        fetch_calls.append(key)
        return FIXED, board_payload

    def load_consensus(conn, rid):
        return list(consensus_ids)

    def parse_board(payload, received_at, board_id):
        ev = NS(
            provider_event_id="evt1",
            home_name="Mark Lajal",
            away_name="Felix Balshaw",
            home_decimal_price=Decimal("1.5"),
            away_decimal_price=Decimal("2.5"),
            raw_payload={},
            _match_id=1001,
        )
        return NS(board_identity="BOARD_ID", events=(ev,), rejected_events=())

    recorder = recorder or Recorder()

    def write_summary(summary, *, artifacts_dir, run_id):
        return "artifacts/live_mvp/tennis_live_mvp_x.json"

    result = run_live_mvp(
        None,
        slate_date=SLATE,
        api_tennis_key="APIKEY",
        pinnapi_key=pinnapi_key,
        preflight=lambda conn: None,
        fetch_fixtures=fetch_fixtures,
        ingest_fixtures=lambda conn, fixtures, received_at: _ingest_outcome([1001]),
        run_slate=lambda conn, ids, as_of: _run_outcome(),
        load_consensus=load_consensus,
        fetch_board=fetch_board,
        parse_board=parse_board,
        process_event=recorder.process,
        ensure_bookmaker_mapping=lambda conn, ot: None,
        build_match_entries=build_entries or (lambda *a, **k: []),
        write_summary=write_summary,
        board_observation_id_factory=lambda: "bid-1",
        clock=lambda: FIXED,
    )
    return result, fetch_calls, recorder


def test_fixture_fetched_once():
    calls = []

    def fetch_fixtures(key, slate):
        calls.append((key, slate))
        return FIXED, [{"event_key": "e1"}]

    run_live_mvp(
        None,
        slate_date=SLATE,
        api_tennis_key="APIKEY",
        pinnapi_key=None,
        preflight=lambda conn: None,
        fetch_fixtures=fetch_fixtures,
        ingest_fixtures=lambda conn, fixtures, received_at: _ingest_outcome([1001]),
        run_slate=lambda conn, ids, as_of: _run_outcome(),
        load_consensus=lambda conn, rid: [],
        build_match_entries=lambda *a, **k: [],
        write_summary=lambda summary, **k: "p",
        clock=lambda: FIXED,
    )
    assert len(calls) == 1
    assert calls[0][0] == "APIKEY"


def test_fixture_reconciliation_survives_to_result_and_summary():
    captured = {}
    rejected = (
        {"reason": "unsupported_surface", "event_key": "safe-1"},
        {"reason": "invalid_date", "event_key": "safe-2"},
        {"reason": "unsupported_surface", "event_key": "safe-3"},
    )

    def write_summary(summary, *, artifacts_dir, run_id):
        captured.update(summary)
        return "artifacts/live_mvp/tennis_live_mvp_x.json"

    result = run_live_mvp(
        None,
        slate_date=SLATE,
        api_tennis_key="APIKEY",
        pinnapi_key=None,
        preflight=lambda conn: None,
        fetch_fixtures=lambda key, slate: (FIXED, [{"event_key": "e1"}] * 5),
        ingest_fixtures=lambda conn, fixtures, received_at: _ingest_outcome(
            [1001, 1002],
            rejected_rows=rejected,
        ),
        run_slate=lambda conn, ids, as_of: _run_outcome(),
        load_consensus=lambda conn, rid: [],
        build_match_entries=lambda *a, **k: [],
        write_summary=write_summary,
        clock=lambda: FIXED,
    )

    assert result.fixture_rows_seen == 5
    assert result.fixture_rows_ingested == 2
    assert result.fixture_rows_rejected == 3
    assert result.fixture_rejection_reasons == (
        ("invalid_date", 1),
        ("unsupported_surface", 2),
    )
    assert captured["fixture_reconciliation"] == {
        "fixture_rows_seen": 5,
        "fixture_rows_ingested": 2,
        "fixture_rows_rejected": 3,
        "fixture_rejection_reasons": {
            "invalid_date": 1,
            "unsupported_surface": 2,
        },
    }
    assert "safe-1" not in str(captured)


def test_fixture_reconciliation_invariant_fails_closed():
    bad = NS(
        rows_seen=3,
        rows_ingested=1,
        rows_rejected=1,
        rejected_rows=({"reason": "invalid_date"},),
    )

    with pytest.raises(RuntimeError, match="rows_seen must equal"):
        fixture_reconciliation_summary(bad)


def test_unsupported_event_status_counts_groups_raw_tokens():
    outcome = _ingest_outcome(
        [],
        rejected_rows=(
            {"reason": "unsupported_event_status", "raw_event_status": "Pending"},
            {"reason": "unsupported_event_status", "raw_event_status": "Pending"},
            {"reason": "unsupported_event_status"},
            {"reason": "out_of_scope_event_type"},
        ),
    )

    assert unsupported_event_status_counts(outcome) == (("Pending", 2), ("UNKNOWN", 1))
    assert unsupported_event_status_counts(_ingest_outcome([1])) == ()


def test_zero_consensus_makes_zero_pinnapi_requests():
    result, fetch_calls, _ = _run(consensus_ids=[])
    assert fetch_calls == []
    assert result.pinnapi_requests == 0
    assert result.status == "LIVE_MVP_SUCCESS_NO_CONSENSUS"


def test_one_consensus_makes_exactly_one_board_call():
    result, fetch_calls, _ = _run(consensus_ids=[1001])
    assert len(fetch_calls) == 1
    assert result.pinnapi_requests == 1
    assert result.status == "LIVE_MVP_SUCCESS"


def test_multiple_consensus_still_one_board_call():
    result, fetch_calls, _ = _run(consensus_ids=[1001, 1002, 1003])
    assert len(fetch_calls) == 1
    assert result.pinnapi_requests == 1


def test_reconcile_candidate_set_is_consensus_only():
    _, _, recorder = _run(consensus_ids=[1001, 1002])
    consensus_passed = recorder.events[0][2]
    assert set(consensus_passed) == {1001, 1002}


def test_missing_pinnapi_key_with_consensus_does_not_fetch():
    result, fetch_calls, _ = _run(consensus_ids=[1001], pinnapi_key=None)
    assert fetch_calls == []
    assert result.pinnapi_requests == 0
    assert "PINNAPI_KEY" in result.notes[0]


def test_api_tennis_transport_error_becomes_live_pilot_failed():
    def fetch_fixtures(key, slate):
        raise TransportError("HTTP_500", "boom")

    with pytest.raises(LivePilotApiTennisFailed):
        run_live_mvp(
            None,
            slate_date=SLATE,
            api_tennis_key="K",
            preflight=lambda conn: None,
            fetch_fixtures=fetch_fixtures,
            ingest_fixtures=lambda conn, fixtures, received_at: _ingest_outcome([]),
            run_slate=lambda conn, ids, as_of: _run_outcome(),
            load_consensus=lambda conn, rid: [],
            build_match_entries=lambda *a, **k: [],
            write_summary=lambda summary, **k: "p",
            clock=lambda: FIXED,
        )


def test_sportradar_transport_error_is_not_mislabeled_api_tennis():
    def fetch_fixtures(key, slate):
        raise TransportError("HTTP_500", "boom")

    with pytest.raises(LivePilotSportradarFailed):
        run_live_mvp(
            None,
            slate_date=SLATE,
            api_tennis_key="K",
            fixture_provider_code="SPORTRADAR_V3",
            preflight=lambda conn: None,
            fetch_fixtures=fetch_fixtures,
            ingest_fixtures=lambda conn, fixtures, received_at: _ingest_outcome([]),
            run_slate=lambda conn, ids, as_of: _run_outcome(),
            load_consensus=lambda conn, rid: [],
            build_match_entries=lambda *a, **k: [],
            write_summary=lambda summary, **k: "p",
            clock=lambda: FIXED,
        )


def test_pinnapi_transport_error_becomes_live_pilot_failed():
    def fetch_fixtures(key, slate):
        return FIXED, [{"event_key": "e1"}]

    def fetch_board(key):
        raise TransportError("HTTP_401", "boom")

    with pytest.raises(LivePilotPinnapiFailed) as captured:
        run_live_mvp(
            None,
            slate_date=SLATE,
            api_tennis_key="K",
            pinnapi_key="K2",
            preflight=lambda conn: None,
            fetch_fixtures=fetch_fixtures,
            ingest_fixtures=lambda conn, fixtures, received_at: _ingest_outcome([1001]),
            run_slate=lambda conn, ids, as_of: _run_outcome(),
            load_consensus=lambda conn, rid: [1001],
            fetch_board=fetch_board,
            parse_board=lambda payload, received_at, board_id: NS(board_identity="B", events=(), rejected_events=()),
            process_event=lambda *a, **k: {"match_id": None, "reconciliation_status": "UNMATCHED"},
            ensure_bookmaker_mapping=lambda conn, ot: None,
            build_match_entries=lambda *a, **k: [],
            write_summary=lambda summary, **k: "p",
            clock=lambda: FIXED,
        )
    assert "kind=HTTP_401" in str(captured.value)
    assert "K2" not in str(captured.value)


def test_client_state_mapping():
    assert _client_state({"result": "NO_BET"}, {}, False) == "NO_BET_MODEL"
    assert _client_state({"result": "CONSENSUS"}, {"reconciliation_status": "RESOLVED"}, True) == "AWAITING_OFFERED_PRICE"
    assert _client_state({"result": "CONSENSUS"}, {"reconciliation_status": "IDENTITY_CONFLICT"}, True) == "RECONCILIATION_FAILED"
    assert _client_state({"result": "CONSENSUS"}, {}, True) == "SHARP_UNAVAILABLE"


def test_final_status_empty_slate():
    assert _final_status((), ()) == "LIVE_MVP_SUCCESS_EMPTY_SLATE"


def test_final_status_non_empty_zero_consensus():
    assert _final_status((1001,), ()) == "LIVE_MVP_SUCCESS_NO_CONSENSUS"


def test_final_status_success_with_consensus():
    assert _final_status((1001,), (1001,)) == "LIVE_MVP_SUCCESS"


def test_empty_slate_makes_zero_pinnapi_requests():
    result, fetch_calls, _ = _run(consensus_ids=[], board_payload=None)
    # _run always seeds one target; build an explicit empty-slate run instead.
    assert result.status == "LIVE_MVP_SUCCESS_NO_CONSENSUS"

    def fetch_fixtures(key, slate):
        return FIXED, [{"event_key": "e1"}]

    result2 = run_live_mvp(
        None,
        slate_date=SLATE,
        api_tennis_key="APIKEY",
        pinnapi_key="K",
        preflight=lambda conn: None,
        fetch_fixtures=fetch_fixtures,
        ingest_fixtures=lambda conn, fixtures, received_at: _ingest_outcome([]),
        run_slate=lambda conn, ids, as_of: _run_outcome(),
        load_consensus=lambda conn, rid: [],
        build_match_entries=lambda *a, **k: [],
        write_summary=lambda summary, **k: "p",
        clock=lambda: FIXED,
    )
    assert result2.status == "LIVE_MVP_SUCCESS_EMPTY_SLATE"
    assert result2.pinnapi_requests == 0


# --- database integration (real reconcile + normalize) ----------------------


def _fetchval(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _insert_match(conn):
    p1 = _fetchval(conn, "INSERT INTO players (canonical_name) VALUES ('Mark Lajal') RETURNING player_id")
    p2 = _fetchval(conn, "INSERT INTO players (canonical_name) VALUES ('Felix Balshaw') RETURNING player_id")
    tid = _fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES ('ATP Challenger Mallorca', 'CHALLENGER', 'CHALLENGER') RETURNING tournament_id",
    )
    eid = _fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, start_date) VALUES (%s, 2026, 'ATP Challenger Mallorca 2026', '2026-09-01') RETURNING tournament_edition_id",
        (tid,),
    )
    mid = _fetchval(conn, "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id", (eid,))
    start = datetime(2026, 9, 5, 13, 10, 0, tzinfo=timezone.utc)
    observed = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
    _fetchval(
        conn,
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            round_code, best_of_sets, qualifying_flag, match_status, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'CHALLENGER', 'CHALLENGER', 'SF', 3, FALSE, 'SCHEDULED', %s, %s)
        RETURNING match_version_id
        """,
        (mid, p1, p2, start, observed, observed),
    )
    return mid


def _board_payload():
    return {
        "events": [
            {
                "event_id": "1635768302",
                "parent_id": None,
                "event_type": "prematch",
                "league_id": "202301",
                "league_name": "ATP Challenger Mallorca - SF",
                "starts": "2026-09-05T13:10:00Z",
                "home": "Mark Lajal",
                "away": "Felix Balshaw",
                "periods": {
                    "num_0": {
                        "status": "open",
                        "description": "Game",
                        "money_line": {"home": 1.5682, "away": 2.55, "draw": None},
                    }
                },
            }
        ]
    }


def test_end_to_end_sharp_persists_and_no_player_lineage(db: psycopg.Connection):
    mid = _insert_match(db)
    insert_bookmaker_mapping(
        db,
        provider_code="PINNAPI",
        provider_bookmaker_key="pinnacle",
        mapping_version=1,
        bookmaker_code="PINNACLE",
        normalization_class="SHARP",
        effective_from=datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc),
    )

    def fetch_fixtures(key, slate):
        return FIXED, [{"event_key": "e1"}]

    def load_consensus(conn, rid):
        return [mid]

    result = run_live_mvp(
        db,
        slate_date=SLATE,
        api_tennis_key="APIKEY",
        pinnapi_key="K",
        preflight=lambda conn: None,
        fetch_fixtures=fetch_fixtures,
        ingest_fixtures=lambda conn, fixtures, received_at: _ingest_outcome([mid]),
        run_slate=lambda conn, ids, as_of: _run_outcome(),
        load_consensus=load_consensus,
        fetch_board=lambda key: (FIXED, _board_payload()),
        build_match_entries=lambda *a, **k: [],
        write_summary=lambda summary, **k: "p",
        board_observation_id_factory=lambda: "bid-1",
        clock=lambda: FIXED,
    )

    assert result.pinnapi_requests == 1
    assert result.status == "LIVE_MVP_SUCCESS"

    raw = db.execute("SELECT count(*) FROM raw_odds_observations WHERE provider_code = 'PINNAPI'").fetchone()[0]
    assert raw == 1

    snapshots = db.execute(
        "SELECT source_system, bookmaker_code, count(*) FROM odds_snapshots WHERE match_id = %s GROUP BY source_system, bookmaker_code",
        (mid,),
    ).fetchall()
    assert snapshots == [("SHARP", "PINNACLE", 2)]

    lineage = db.execute("SELECT count(*) FROM player_source_ids WHERE source_system = 'PINNAPI'").fetchone()[0]
    assert lineage == 0

    mapping = db.execute(
        "SELECT count(*) FROM match_source_ids WHERE source_system = 'PINNAPI' AND match_id = %s",
        (mid,),
    ).fetchone()[0]
    assert mapping == 1


def test_missing_bookmaker_mapping_blocks(db: psycopg.Connection):
    mid = _insert_match(db)

    def fetch_fixtures(key, slate):
        return FIXED, [{"event_key": "e1"}]

    with pytest.raises(BlockedPinnapiBookmakerMapping):
        run_live_mvp(
            db,
            slate_date=SLATE,
            api_tennis_key="K",
            pinnapi_key="K2",
            preflight=lambda conn: None,
            fetch_fixtures=fetch_fixtures,
            ingest_fixtures=lambda conn, fixtures, received_at: _ingest_outcome([mid]),
            run_slate=lambda conn, ids, as_of: _run_outcome(),
            load_consensus=lambda conn, rid: [mid],
            build_match_entries=lambda *a, **k: [],
            write_summary=lambda summary, **k: "p",
            clock=lambda: FIXED,
        )


@pytest.mark.parametrize(
    ("category", "expected_suffix"),
    [
        ("ACCOUNT_OR_PLAN", " category=ACCOUNT_OR_PLAN"),
        ("EDGE_CHALLENGE", " category=EDGE_CHALLENGE"),
        ("raw provider text <script>", ""),
        (None, ""),
    ],
)
def test_pinnapi_failure_surfaces_only_allowlisted_category(category, expected_suffix):
    def fetch_board(key):
        raise TransportError("HTTP_403", "boom", category=category)

    with pytest.raises(LivePilotPinnapiFailed) as captured:
        run_live_mvp(
            None,
            slate_date=SLATE,
            api_tennis_key="K",
            pinnapi_key="K2",
            preflight=lambda conn: None,
            fetch_fixtures=lambda key, slate: (FIXED, [{"event_key": "e1"}]),
            ingest_fixtures=lambda conn, fixtures, received_at: _ingest_outcome([1001]),
            run_slate=lambda conn, ids, as_of: _run_outcome(),
            load_consensus=lambda conn, rid: [1001],
            fetch_board=fetch_board,
            parse_board=lambda payload, received_at, board_id: NS(board_identity="B", events=(), rejected_events=()),
            process_event=lambda *a, **k: {"match_id": None, "reconciliation_status": "UNMATCHED"},
            ensure_bookmaker_mapping=lambda conn, ot: None,
            build_match_entries=lambda *a, **k: [],
            write_summary=lambda summary, **k: "p",
            clock=lambda: FIXED,
        )
    assert str(captured.value) == f"pinnapi transport failed kind=HTTP_403{expected_suffix}"
