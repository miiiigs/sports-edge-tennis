import pytest

from datetime import date

from sports_edge_tennis.live.sportradar import SportradarTransportError
from sports_edge_tennis.live.sportradar_probe import (
    RequestPacer,
    _paced_fetch,
    find_competitions,
    find_player_candidates,
    select_season,
    summary_has_statistics,
)


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
        return (
            __import__("datetime").datetime(2026, 9, 18, tzinfo=__import__("datetime").timezone.utc),
            {"ok": True},
        )

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
