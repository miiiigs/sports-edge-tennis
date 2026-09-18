from datetime import UTC, date, datetime

from sports_edge_tennis.live.sportradar_history_probe import (
    MAX_COMPETITIONS,
    ChallengerCompetition,
    discover_current_challenger_singles,
    fetch_bounded_season_summaries,
    select_previous_season,
)
from sports_edge_tennis.live.sportradar_probe import RequestPacer


def _summary(competition_id, name, *, competition_type="singles", category="Challenger"):
    return {
        "sport_event": {
            "id": f"sr:sport_event:{competition_id.rsplit(':', 1)[-1]}",
            "sport_event_context": {
                "category": {"name": category},
                "competition": {
                    "id": competition_id,
                    "name": name,
                    "type": competition_type,
                    "level": "challenger" if category == "Challenger" else "atp",
                },
            },
        }
    }


def test_discovery_returns_unique_deterministic_challenger_singles_only():
    payload = {
        "summaries": [
            _summary("sr:competition:30", "Rennes Men Singles"),
            _summary("sr:competition:10", "Tiburon Men Singles"),
            _summary("sr:competition:30", "Rennes Men Singles"),
            _summary(
                "sr:competition:20",
                "Rennes Men Doubles",
                competition_type="doubles",
            ),
            _summary(
                "sr:competition:40",
                "ATP 250 Singles",
                category="ATP",
            ),
        ]
    }

    result = discover_current_challenger_singles(payload, limit=5)

    assert result == (
        ChallengerCompetition("sr:competition:10", "Tiburon Men Singles"),
        ChallengerCompetition("sr:competition:30", "Rennes Men Singles"),
    )


def test_discovery_respects_bound():
    payload = {
        "summaries": [
            _summary(f"sr:competition:{index}", f"Challenger {index}")
            for index in range(1, MAX_COMPETITIONS + 3)
        ]
    }

    result = discover_current_challenger_singles(payload, limit=2)

    assert len(result) == 2


def test_select_previous_season_chooses_latest_completed_edition():
    payload = {
        "seasons": [
            {
                "id": "sr:season:2024",
                "name": "Rennes 2024",
                "start_date": "2024-09-09",
                "end_date": "2024-09-15",
                "year": "2024",
            },
            {
                "id": "sr:season:2025",
                "name": "Rennes 2025",
                "start_date": "2025-09-08",
                "end_date": "2025-09-14",
                "year": "2025",
            },
            {
                "id": "sr:season:2026",
                "name": "Rennes 2026",
                "start_date": "2026-09-14",
                "end_date": "2026-09-20",
                "year": "2026",
            },
        ]
    }

    selected = select_previous_season(payload, target_date=date(2026, 9, 18))

    assert selected is not None
    assert selected.season_id == "sr:season:2025"


def test_select_previous_season_fails_closed_without_completed_prior_edition():
    payload = {
        "seasons": [
            {
                "id": "sr:season:2026",
                "name": "New Event 2026",
                "start_date": "2026-09-14",
                "end_date": "2026-09-20",
                "year": "2026",
            }
        ]
    }

    assert select_previous_season(payload, target_date=date(2026, 9, 18)) is None


def test_bounded_season_summaries_pages_until_short_page(capsys):
    calls = []

    def fetcher(api_key, season_id, **kwargs):
        calls.append((api_key, season_id, kwargs["start"]))
        count = 200 if kwargs["start"] == 0 else 17
        rows = [{"sport_event": {"id": f"event-{kwargs['start']}-{i}"}} for i in range(count)]
        return datetime(2026, 9, 18, tzinfo=UTC), {"summaries": rows}

    payload, pages = fetch_bounded_season_summaries(
        api_key="secret",
        season_id="sr:season:2025",
        max_pages=3,
        transport={"access_level": "trial", "language": "en"},
        pacer=RequestPacer(0.0),
        access_level="trial",
        retry_429_seconds=0.0,
        fetcher=fetcher,
    )

    assert pages == 2
    assert len(payload["summaries"]) == 217
    assert [call[2] for call in calls] == [0, 200]
    output = capsys.readouterr().out
    assert "SPORTRADAR_HISTORY_PAGE season_id=sr:season:2025 page=1 start=0 rows=200" in output
    assert "SPORTRADAR_HISTORY_PAGE season_id=sr:season:2025 page=2 start=200 rows=17" in output
