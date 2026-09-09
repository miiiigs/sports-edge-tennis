from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.data.sources.api_tennis_live import parse_api_tennis_fixtures
from sports_edge_tennis.live.api_tennis_fixture_compat import (
    enrich_api_tennis_fixture_surfaces,
    normalize_api_tennis_upcoming_status,
)


def _received_at() -> datetime:
    return datetime(2026, 9, 8, 0, 0, tzinfo=UTC)


def _fixture(**overrides):
    payload = {
        "event_key": "900001",
        "tournament_key": "4901",
        "tournament_name": "ATP Challenger Test",
        "event_type_type": "Challenger Men Singles",
        "event_date": "2026-09-08",
        "event_time": "12:00",
        "event_status": "",
        "event_live": "0",
        "event_winner": None,
        "event_final_result": "-",
        "event_first_player": "Player One",
        "event_second_player": "Player Two",
        "first_player_key": "101",
        "second_player_key": "202",
        "event_surface": "Hard",
    }
    payload.update(overrides)
    return payload


def test_safe_blank_future_fixture_is_promoted_to_scheduled():
    normalized = normalize_api_tennis_upcoming_status(
        [_fixture()],
        received_at=_received_at(),
    )

    assert normalized[0]["event_status"] == "Not Started"
    records, rejected = parse_api_tennis_fixtures(
        normalized,
        received_at=_received_at(),
    )
    assert rejected == []
    assert len(records) == 1
    assert records[0].match_status == "SCHEDULED"


def test_blank_status_remains_fail_closed_when_fixture_is_live():
    normalized = normalize_api_tennis_upcoming_status(
        [_fixture(event_live="1")],
        received_at=_received_at(),
    )

    assert normalized[0]["event_status"] == ""
    records, rejected = parse_api_tennis_fixtures(normalized, received_at=_received_at())
    assert records == []
    assert rejected[0]["reason"] == "missing_required_field:event_status"


def test_blank_status_remains_fail_closed_when_winner_or_result_exists():
    normalized = normalize_api_tennis_upcoming_status(
        [
            _fixture(event_key="900002", event_winner="First Player"),
            _fixture(event_key="900003", event_final_result="2 - 0"),
        ],
        received_at=_received_at(),
    )

    assert [row["event_status"] for row in normalized] == ["", ""]


def test_blank_status_remains_fail_closed_for_non_future_fixture():
    normalized = normalize_api_tennis_upcoming_status(
        [_fixture(event_date="2026-09-07")],
        received_at=_received_at(),
    )

    assert normalized[0]["event_status"] == ""


def test_explicit_provider_status_is_never_overwritten():
    normalized = normalize_api_tennis_upcoming_status(
        [_fixture(event_status="Finished", event_final_result="2 - 0")],
        received_at=_received_at(),
    )

    assert normalized[0]["event_status"] == "Finished"


def test_missing_surface_uses_one_draw_call_per_tournament_season(db: psycopg.Connection):
    calls = []

    def fetch_draw(api_key: str, tournament_key: str, season_year: int):
        calls.append((api_key, tournament_key, season_year))
        return _received_at(), {
            "tournament": {
                "tournament_key": tournament_key,
                "tournament_season": str(season_year),
                "tournament_surface": "Hard",
            }
        }

    rows = enrich_api_tennis_fixture_surfaces(
        db,
        [
            _fixture(event_key="900010", event_surface=None),
            _fixture(
                event_key="900011",
                event_surface=None,
                event_first_player="Player Three",
                event_second_player="Player Four",
                first_player_key="303",
                second_player_key="404",
            ),
        ],
        api_key="KEY123",
        fetch_draw=fetch_draw,
    )

    assert calls == [("KEY123", "4901", 2026)]
    assert [row["surface"] for row in rows] == ["Hard", "Hard"]


def test_existing_fixture_surface_skips_draw(db: psycopg.Connection):
    calls = []

    def fetch_draw(api_key: str, tournament_key: str, season_year: int):
        calls.append((api_key, tournament_key, season_year))
        raise AssertionError("draw should not be called")

    rows = enrich_api_tennis_fixture_surfaces(
        db,
        [_fixture(event_surface="Clay")],
        api_key="KEY123",
        fetch_draw=fetch_draw,
    )

    assert calls == []
    assert rows[0]["event_surface"] == "Clay"


def test_unsupported_draw_surface_remains_missing(db: psycopg.Connection):
    def fetch_draw(api_key: str, tournament_key: str, season_year: int):
        return _received_at(), {
            "tournament": {"tournament_surface": "Unknown Surface"}
        }

    rows = enrich_api_tennis_fixture_surfaces(
        db,
        [_fixture(event_surface=None)],
        api_key="KEY123",
        fetch_draw=fetch_draw,
    )

    assert rows[0].get("surface") is None
