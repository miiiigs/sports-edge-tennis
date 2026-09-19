from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from sports_edge_tennis.live import live_tennis_surface_probe as module
from sports_edge_tennis.live.live_tennis_surface_probe import (
    LiveTennisApiError,
    fetch_fixture_page,
    fetch_fixture_pages,
    link_surface_target,
    normalize_surface,
    run_probe,
)
from sports_edge_tennis.live.sportradar_benchmark import CanonicalGapTarget


def _target(*, start: datetime | None = None) -> CanonicalGapTarget:
    return CanonicalGapTarget(
        match_id=130470,
        prediction_as_of=datetime(2026, 9, 19, 5, 1, tzinfo=UTC),
        player_1_id=1,
        player_1_name="John Smith",
        player_2_id=2,
        player_2_name="Alex Jones",
        match_start_at=start or datetime(2026, 9, 19, 10, 0, tzinfo=UTC),
        tournament_id=657,
        tournament_name="ATP Davis Cup - World Group",
        season_year=2026,
        player_1_identity_status="BRIDGED",
        player_2_identity_status="BRIDGED",
        surface_status="UNRESOLVED",
    )


def _fixture(
    *,
    fixture_id: int = 99,
    p1: str = "John Smith",
    p2: str = "Alex Jones",
    event_date: str = "2026-09-19",
    start_time: str = "10:20:00",
    surface: str | None = "hard",
):
    return {
        "id": fixture_id,
        "event_date": event_date,
        "start_time": start_time,
        "player1_name": p1,
        "player2_name": p2,
        "tournament": "Davis Cup",
        "surface": surface,
    }


def test_normalize_surface_is_explicit_and_fail_closed():
    assert normalize_surface("hard") == "HARD"
    assert normalize_surface("Clay") == "CLAY"
    assert normalize_surface("grass") == "GRASS"
    assert normalize_surface("carpet") == "CARPET"
    assert normalize_surface("indoor") is None
    assert normalize_surface(None) is None


def test_link_surface_target_requires_pair_and_bounded_time():
    result = link_surface_target(_target(), [_fixture()])

    assert result.status == "EXACT_SURFACE"
    assert result.candidates[0].fixture_id == 99
    assert result.candidates[0].canonical_surface == "HARD"
    assert result.candidates[0].start_delta_minutes == 20


def test_link_surface_target_allows_reversed_participant_order_only():
    result = link_surface_target(
        _target(),
        [_fixture(p1="Alex Jones", p2="John Smith", surface="clay")],
    )

    assert result.status == "EXACT_SURFACE"
    assert result.candidates[0].canonical_surface == "CLAY"


def test_link_surface_target_preserves_null_surface():
    result = link_surface_target(_target(), [_fixture(surface=None)])

    assert result.status == "NULL_SURFACE"
    assert result.candidates[0].canonical_surface is None


def test_link_surface_target_fails_closed_on_ambiguous_pair():
    rows = [
        _fixture(fixture_id=1, start_time="10:10:00"),
        _fixture(fixture_id=2, start_time="10:30:00"),
    ]

    result = link_surface_target(_target(), rows)

    assert result.status == "AMBIGUOUS"
    assert len(result.candidates) == 2


def test_link_surface_target_reports_time_mismatch():
    result = link_surface_target(
        _target(),
        [_fixture(start_time="18:00:00")],
        max_start_delta_minutes=180,
    )

    assert result.status == "TIME_MISMATCH"


def test_link_surface_target_skips_malformed_fixture_id():
    result = link_surface_target(
        _target(),
        [{**_fixture(), "id": None}],
    )

    assert result.status == "UNRESOLVED"


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = {"data": []} if payload is None else payload

    def json(self):
        return self._payload


def test_fetch_fixture_page_uses_documented_key_header_and_pagination():
    calls = []

    def fake_get(url, *, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        return _Response(payload={"data": [_fixture()]})

    _received_at, payload = fetch_fixture_page(
        "secret",
        limit=50,
        offset=100,
        get=fake_get,
    )

    assert payload["data"][0]["id"] == 99
    assert calls == [
        (
            "https://api.livetennisapi.com/api/public/v1/fixtures",
            {"limit": 50, "offset": 100},
            {"X-API-Key": "secret"},
            10.0,
        )
    ]


@pytest.mark.parametrize(
    ("status_code", "kind"),
    [(401, "AUTH"), (403, "AUTH"), (429, "HTTP_429"), (500, "HTTP_500")],
)
def test_fetch_fixture_page_fails_closed_on_http_error(status_code, kind):
    def fake_get(*args, **kwargs):
        return _Response(status_code=status_code)

    with pytest.raises(LiveTennisApiError) as exc:
        fetch_fixture_page("secret", get=fake_get)

    assert exc.value.kind == kind


def test_fetch_fixture_pages_stops_on_short_page():
    calls = []

    def fake_fetch(api_key, *, limit, offset):
        calls.append((api_key, limit, offset))
        if offset == 0:
            return datetime(2026, 9, 19, 5, 0, tzinfo=UTC), {
                "data": [_fixture(fixture_id=1), _fixture(fixture_id=2)]
            }
        raise AssertionError("short page should stop pagination")

    _received_at, fixtures, request_count = fetch_fixture_pages(
        "secret",
        max_pages=3,
        page_size=50,
        fetch_page=fake_fetch,
    )

    assert len(fixtures) == 2
    assert request_count == 1
    assert calls == [("secret", 50, 0)]


def test_run_probe_is_read_only_and_reports_surface(monkeypatch, capsys):
    target = _target()
    monkeypatch.setattr(
        module,
        "load_latest_gap_targets",
        lambda conn, *, target_date: (
            340,
            datetime(2026, 9, 19, 5, 1, tzinfo=UTC),
            (target,),
        ),
    )

    status = run_probe(
        object(),
        api_key="secret",
        target_date=date(2026, 9, 19),
        max_pages=1,
        fetch_pages=lambda *args, **kwargs: (
            datetime(2026, 9, 19, 5, 10, tzinfo=UTC),
            (_fixture(),),
            1,
        ),
    )

    assert status == 0
    out = capsys.readouterr().out
    assert "LIVE_TENNIS_SURFACE_PROBE_START" in out
    assert "status=EXACT_SURFACE" in out
    assert "surface=HARD" in out
    assert "mapping_changed=0" in out
    assert "writes=0" in out
