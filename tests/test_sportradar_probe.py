import pytest

from datetime import UTC, date, datetime
from types import SimpleNamespace

from sports_edge_tennis.live.sportradar import SportradarTransportError
from sports_edge_tennis.live.sportradar_identity_bridge_apply import (
    ENABLED_ENV as IDENTITY_BRIDGE_ENABLED_ENV,
    EVENT_ID_ENV as IDENTITY_BRIDGE_EVENT_ID_ENV,
    MATCH_ID_ENV as IDENTITY_BRIDGE_MATCH_ID_ENV,
)
from sports_edge_tennis.live.sportradar_probe import (
    A3_EVIDENCE_ENABLED_ENV,
    A3_EVIDENCE_HORIZON_DAYS_ENV,
    A3_EVIDENCE_MAX_PAIRS_ENV,
    HISTORY_DEPTH_ENABLED_ENV,
    HISTORY_DEPTH_MAX_COMPETITIONS_ENV,
    HISTORY_DEPTH_MAX_PAGES_ENV,
    GAP_SURFACE_ENABLED_ENV,
    GAP_SURFACE_MAX_COMPETITIONS_ENV,
    RequestPacer,
    _augment_identity_bridge_exact_links,
    _paced_fetch,
    _run_optional_a3_evidence,
    _run_optional_history_depth,
    _run_optional_gap_surface_probe,
    find_competitions,
    find_player_candidates,
    normalize_sportradar_surface,
    select_season,
    summary_has_statistics,
)


class _BridgeReadConn:
    def __init__(self):
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def rollback(self):
        self.rollbacks += 1


def _bridge_env():
    return {
        IDENTITY_BRIDGE_ENABLED_ENV: "true",
        IDENTITY_BRIDGE_MATCH_ID_ENV: "130469",
        IDENTITY_BRIDGE_EVENT_ID_ENV: "sr:sport_event:74801010",
    }


def test_identity_bridge_revalidation_is_disabled_by_default():
    assert (
        _augment_identity_bridge_exact_links(
            dsn="postgresql://example",
            daily={},
            exact_links=(),
            env={},
            connect=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("no connection")
            ),
        )
        == ()
    )


def test_identity_bridge_revalidation_uses_current_batch_link(capsys):
    target = SimpleNamespace(match_id=130469)
    candidate = SimpleNamespace(event_id="sr:sport_event:74801010")

    links = _augment_identity_bridge_exact_links(
        dsn="postgresql://example",
        daily={},
        exact_links=((target, candidate),),
        env=_bridge_env(),
        connect=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no fallback connection")
        ),
    )

    assert links == ((target, candidate),)
    assert "status=CURRENT_BATCH" in capsys.readouterr().out


def test_identity_bridge_revalidation_loads_aged_pre_match_target(capsys):
    conn = _BridgeReadConn()
    target = SimpleNamespace(
        match_id=130469,
        prediction_as_of=datetime(2026, 9, 18, 18, 32, tzinfo=UTC),
    )
    candidate = SimpleNamespace(
        event_id="sr:sport_event:74801010",
        start_delta_minutes=20,
    )
    result = SimpleNamespace(status="EXACT", candidates=(candidate,))
    loaded = []

    links = _augment_identity_bridge_exact_links(
        dsn="postgresql://example",
        daily={"summaries": []},
        exact_links=(),
        env=_bridge_env(),
        connect=lambda *args, **kwargs: conn,
        target_loader=lambda received_conn, *, match_id: (
            loaded.append((received_conn, match_id)) or target
        ),
        linker=lambda received_target, daily: result,
    )

    assert loaded == [(conn, 130469)]
    assert conn.rollbacks == 1
    assert links == ((target, candidate),)
    output = capsys.readouterr().out
    assert "status=EXACT" in output
    assert "prediction_as_of=2026-09-18T18:32:00+00:00" in output
    assert "writes=0" in output


def test_identity_bridge_revalidation_refuses_requested_event_mismatch(capsys):
    conn = _BridgeReadConn()
    target = SimpleNamespace(
        match_id=130469,
        prediction_as_of=datetime(2026, 9, 18, 18, 32, tzinfo=UTC),
    )
    candidate = SimpleNamespace(
        event_id="sr:sport_event:other",
        start_delta_minutes=20,
    )

    links = _augment_identity_bridge_exact_links(
        dsn="postgresql://example",
        daily={},
        exact_links=(),
        env=_bridge_env(),
        connect=lambda *args, **kwargs: conn,
        target_loader=lambda *args, **kwargs: target,
        linker=lambda *args, **kwargs: SimpleNamespace(
            status="EXACT",
            candidates=(candidate,),
        ),
    )

    assert links == ()
    assert conn.rollbacks == 1
    output = capsys.readouterr().out
    assert "candidate_event_id=sr:sport_event:other" in output
    assert "writes=0" in output


def test_find_competitions_is_case_insensitive_and_bounded():
    payload = {
        "competitions": [
            {"id": "sr:competition:1", "name": "ATP Challenger Rennes Men Singles"},
            {"id": "sr:competition:2", "name": "Rennes Men Doubles"},
            {"id": "sr:competition:3", "name": "Other"},
        ]
    }

    matches = find_competitions(payload, "rennes", limit=1)

    assert len(matches) == 1
    assert matches[0]["id"] == "sr:competition:1"


def test_player_candidates_combine_rankings_and_daily_without_duplicate_ids():
    rankings = {
        "rankings": [
            {
                "competitor_rankings": [
                    {
                        "competitor": {
                            "id": "sr:competitor:10",
                            "name": "Nakashima, Brandon",
                        }
                    }
                ]
            }
        ]
    }
    daily = {
        "summaries": [
            {
                "sport_event": {
                    "competitors": [
                        {
                            "id": "sr:competitor:10",
                            "name": "Nakashima, Brandon",
                        },
                        {
                            "id": "sr:competitor:11",
                            "name": "Nakashima, Bryce",
                        },
                    ]
                }
            }
        ]
    }

    candidates = find_player_candidates(rankings, daily, "Nakashima")

    assert [row["id"] for row in candidates] == [
        "sr:competitor:10",
        "sr:competitor:11",
    ]


def test_select_season_prefers_date_containing_edition():
    payload = {
        "seasons": [
            {
                "id": "sr:season:2025",
                "year": "2025",
                "start_date": "2025-09-08",
                "end_date": "2025-09-14",
            },
            {
                "id": "sr:season:2026",
                "year": "2026",
                "start_date": "2026-09-14",
                "end_date": "2026-09-20",
            },
        ]
    }

    selected = select_season(payload, target_date=date(2026, 9, 17))

    assert selected is not None
    assert selected["id"] == "sr:season:2026"


def test_select_season_falls_back_to_latest():
    payload = {
        "seasons": [
            {"id": "sr:season:2025", "year": "2025", "start_date": "2025-09-01"},
            {"id": "sr:season:2026", "year": "2026", "start_date": "2026-09-01"},
        ]
    }

    selected = select_season(payload, target_date=date(2026, 1, 1))

    assert selected is not None
    assert selected["id"] == "sr:season:2026"


def test_summary_statistics_requires_non_empty_mapping():
    assert summary_has_statistics({"statistics": {"totals": {}}})
    assert not summary_has_statistics({"statistics": {}})
    assert not summary_has_statistics({})



def test_request_pacer_enforces_minimum_interval():
    clock = {"now": 10.0}
    sleeps = []

    def monotonic():
        return clock["now"]

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    pacer = RequestPacer(1.1, monotonic=monotonic, sleep=sleep)

    pacer.wait()
    clock["now"] += 0.4
    pacer.wait()

    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.7)
    assert clock["now"] == pytest.approx(11.1)



def test_paced_fetch_retries_one_trial_429_then_succeeds(capsys):
    clock = {"now": 0.0}
    sleeps = []
    calls = {"count": 0}

    def monotonic():
        return clock["now"]

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    def fetcher(api_key, **transport):
        calls["count"] += 1
        if calls["count"] == 1:
            raise SportradarTransportError("HTTP_429", "rate limited")
        return (datetime(2026, 9, 18, tzinfo=UTC), {"ok": True})

    pacer = RequestPacer(2.0, monotonic=monotonic, sleep=sleep)
    received_at, payload = _paced_fetch(
        endpoint="competitions",
        fetcher=fetcher,
        args=("secret",),
        transport={"access_level": "trial", "language": "en"},
        pacer=pacer,
        access_level="trial",
        retry_429_seconds=3.0,
    )

    assert payload == {"ok": True}
    assert calls["count"] == 2
    assert sleeps == [3.0]
    output = capsys.readouterr().out
    assert "SPORTRADAR_RATE_LIMIT endpoint=competitions retry=1" in output
    assert "SPORTRADAR_REQUEST endpoint=competitions status=OK retry=1" in output


def test_paced_fetch_does_not_retry_non_429():
    def fetcher(api_key, **transport):
        raise SportradarTransportError("HTTP_403", "forbidden")

    pacer = RequestPacer(0.0)
    with pytest.raises(SportradarTransportError) as exc:
        _paced_fetch(
            endpoint="competitions",
            fetcher=fetcher,
            args=("secret",),
            transport={"access_level": "trial", "language": "en"},
            pacer=pacer,
            access_level="trial",
            retry_429_seconds=3.0,
        )

    assert exc.value.kind == "HTTP_403"


def test_optional_history_depth_is_disabled_by_default():
    calls = []
    sleeps = []

    def runner(**kwargs):
        calls.append(kwargs)
        return 0

    status = _run_optional_history_depth(
        api_key="secret",
        target_date=date(2026, 9, 18),
        access_level="trial",
        language="en",
        env={},
        runner=runner,
        sleep=sleeps.append,
    )

    assert status == 0
    assert calls == []
    assert sleeps == []


def test_optional_history_depth_dispatches_bounded_probe_when_enabled(capsys):
    calls = []
    sleeps = []

    def runner(**kwargs):
        calls.append(kwargs)
        return 0

    status = _run_optional_history_depth(
        api_key="secret",
        target_date=date(2026, 9, 18),
        access_level="trial",
        language="en",
        env={
            HISTORY_DEPTH_ENABLED_ENV: "true",
            HISTORY_DEPTH_MAX_COMPETITIONS_ENV: "2",
            HISTORY_DEPTH_MAX_PAGES_ENV: "1",
        },
        runner=runner,
        sleep=sleeps.append,
    )

    assert status == 0
    assert sleeps == [2.0]
    assert calls == [
        {
            "api_key": "secret",
            "target_date": date(2026, 9, 18),
            "access_level": "trial",
            "language": "en",
            "max_competitions": 2,
            "max_pages": 1,
        }
    ]
    output = capsys.readouterr().out
    assert (
        "SPORTRADAR_HISTORY_DEPTH_DISPATCH date=2026-09-18 "
        "max_competitions=2 max_pages=1 writes=0"
    ) in output


def test_optional_history_depth_rejects_non_integer_bounds():
    with pytest.raises(ValueError, match="bounds must be integers"):
        _run_optional_history_depth(
            api_key="secret",
            target_date=date(2026, 9, 18),
            access_level="trial",
            language="en",
            env={
                HISTORY_DEPTH_ENABLED_ENV: "1",
                HISTORY_DEPTH_MAX_COMPETITIONS_ENV: "nope",
            },
            runner=lambda **kwargs: 0,
            sleep=lambda seconds: None,
        )


def test_optional_a3_evidence_is_disabled_by_default():
    calls = []
    sleeps = []

    def runner(conn, **kwargs):
        calls.append((conn, kwargs))
        return 0

    status = _run_optional_a3_evidence(
        object(),
        api_key="secret",
        as_of=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        access_level="trial",
        language="en",
        env={},
        runner=runner,
        sleep=sleeps.append,
    )

    assert status == 0
    assert calls == []
    assert sleeps == []


def test_optional_a3_evidence_dispatches_bounded_probe_when_enabled(capsys):
    calls = []
    sleeps = []
    conn = object()

    def runner(received_conn, **kwargs):
        calls.append((received_conn, kwargs))
        return 0

    status = _run_optional_a3_evidence(
        conn,
        api_key="secret",
        as_of=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        access_level="trial",
        language="en",
        env={
            A3_EVIDENCE_ENABLED_ENV: "true",
            A3_EVIDENCE_MAX_PAIRS_ENV: "2",
            A3_EVIDENCE_HORIZON_DAYS_ENV: "3",
        },
        runner=runner,
        sleep=sleeps.append,
    )

    assert status == 0
    assert sleeps == [2.0]
    assert calls == [
        (
            conn,
            {
                "api_key": "secret",
                "as_of": datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
                "max_pairs": 2,
                "horizon_days": 3,
                "access_level": "trial",
                "language": "en",
            },
        )
    ]
    output = capsys.readouterr().out
    assert (
        "SPORTRADAR_A3_DISPATCH as_of=2026-09-18T12:00:00+00:00 "
        "max_pairs=2 horizon_days=3 writes=0"
    ) in output


def test_optional_a3_evidence_rejects_out_of_range_pairs():
    with pytest.raises(ValueError, match="between 1 and 10"):
        _run_optional_a3_evidence(
            object(),
            api_key="secret",
            as_of=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
            access_level="trial",
            language="en",
            env={
                A3_EVIDENCE_ENABLED_ENV: "1",
                A3_EVIDENCE_MAX_PAIRS_ENV: "11",
            },
            runner=lambda conn, **kwargs: 0,
            sleep=lambda seconds: None,
        )


def test_optional_a3_evidence_rejects_out_of_range_horizon():
    with pytest.raises(ValueError, match="horizon days must be between 1 and 7"):
        _run_optional_a3_evidence(
            object(),
            api_key="secret",
            as_of=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
            access_level="trial",
            language="en",
            env={
                A3_EVIDENCE_ENABLED_ENV: "1",
                A3_EVIDENCE_HORIZON_DAYS_ENV: "8",
            },
            runner=lambda conn, **kwargs: 0,
            sleep=lambda seconds: None,
        )



def test_normalize_sportradar_surface_handles_known_variants():
    assert normalize_sportradar_surface("hardcourt_indoor") == "HARD"
    assert normalize_sportradar_surface("Hardcourt Outdoor") == "HARD"
    assert normalize_sportradar_surface("clay") == "CLAY"
    assert normalize_sportradar_surface("grass") == "GRASS"
    assert normalize_sportradar_surface("carpet_indoor") == "CARPET"
    assert normalize_sportradar_surface("synthetic") is None


def test_optional_gap_surface_probe_is_disabled_by_default():
    calls = []

    def paced(*args):
        calls.append(args)
        raise AssertionError("should not be called")

    _run_optional_gap_surface_probe(
        exact_links=(),
        api_key="secret",
        target_date=date(2026, 9, 18),
        paced=paced,
        env={},
    )

    assert calls == []


def test_optional_gap_surface_probe_dedupes_competitions_and_reports_candidates(capsys):
    calls = []

    def paced(endpoint, fetcher, api_key, provider_id):
        calls.append((endpoint, provider_id))
        if endpoint == "gap_competition_seasons":
            return (
                datetime(2026, 9, 18, tzinfo=UTC),
                {
                    "seasons": [
                        {
                            "id": "sr:season:2026",
                            "year": "2026",
                            "start_date": "2026-09-10",
                            "end_date": "2026-09-20",
                        }
                    ]
                },
            )
        if endpoint == "gap_season_info":
            return (
                datetime(2026, 9, 18, tzinfo=UTC),
                {"season": {"info": {"surface": "hardcourt_indoor"}}},
            )
        raise AssertionError(endpoint)

    target_a = SimpleNamespace(match_id=1, surface_status="UNRESOLVED")
    target_b = SimpleNamespace(match_id=2, surface_status="UNRESOLVED")
    candidate_a = SimpleNamespace(
        competition_id="sr:competition:10",
        competition_name="Davis Cup World Group I",
    )
    candidate_b = SimpleNamespace(
        competition_id="sr:competition:10",
        competition_name="Davis Cup World Group I",
    )

    _run_optional_gap_surface_probe(
        exact_links=((target_a, candidate_a), (target_b, candidate_b)),
        api_key="secret",
        target_date=date(2026, 9, 18),
        paced=paced,
        env={
            GAP_SURFACE_ENABLED_ENV: "true",
            GAP_SURFACE_MAX_COMPETITIONS_ENV: "4",
        },
    )

    assert calls == [
        ("gap_competition_seasons", "sr:competition:10"),
        ("gap_season_info", "sr:season:2026"),
    ]
    out = capsys.readouterr().out
    assert out.count("SPORTRADAR_GAP_SURFACE_CANDIDATE ") == 2
    assert "canonical_surface=HARD" in out
    assert "resolved_matches=2" in out
    assert "writes=0" in out


def test_optional_gap_surface_probe_rejects_unbounded_request_count():
    with pytest.raises(ValueError, match="between 1 and 12"):
        _run_optional_gap_surface_probe(
            exact_links=(),
            api_key="secret",
            target_date=date(2026, 9, 18),
            paced=lambda *args: None,
            env={
                GAP_SURFACE_ENABLED_ENV: "true",
                GAP_SURFACE_MAX_COMPETITIONS_ENV: "13",
            },
        )
