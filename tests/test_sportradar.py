from __future__ import annotations

from datetime import UTC, datetime

import pytest
import requests

from sports_edge_tennis.live.sportradar import (
    SportradarTransportError,
    fetch_competition_seasons,
    fetch_competitions,
)


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_competitions_uses_header_auth_and_never_query_key():
    captured = {}

    def fake_get(url, *, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse(200, {"competitions": []})

    received_at, payload = fetch_competitions(
        "secret-key",
        access_level="trial",
        language="en",
        base_url="https://example.test/tennis",
        clock=lambda: datetime(2026, 9, 18, tzinfo=UTC),
        http_get=fake_get,
    )

    assert received_at == datetime(2026, 9, 18, tzinfo=UTC)
    assert payload == {"competitions": []}
    assert captured["url"] == "https://example.test/tennis/trial/v3/en/competitions.json"
    assert "secret-key" not in captured["url"]
    assert captured["headers"]["x-api-key"] == "secret-key"
    assert captured["headers"]["accept"] == "application/json"


def test_resource_ids_preserve_sportradar_urn_shape():
    captured = {}

    def fake_get(url, *, headers, timeout):
        captured["url"] = url
        return FakeResponse(200, {"seasons": []})

    fetch_competition_seasons(
        "secret-key",
        "sr:competition:1234",
        base_url="https://example.test/tennis",
        http_get=fake_get,
    )

    assert captured["url"].endswith(
        "/trial/v3/en/competitions/sr:competition:1234/seasons.json"
    )


def test_non_200_is_classified_without_secret_in_message():
    def fake_get(url, *, headers, timeout):
        return FakeResponse(401, {"message": "unauthorized"})

    with pytest.raises(SportradarTransportError) as exc:
        fetch_competitions(
            "do-not-leak",
            base_url="https://example.test/tennis",
            http_get=fake_get,
        )

    assert exc.value.kind == "HTTP_401"
    assert "do-not-leak" not in str(exc.value)


def test_timeout_is_classified():
    def fake_get(url, *, headers, timeout):
        raise requests.exceptions.Timeout("boom")

    with pytest.raises(SportradarTransportError) as exc:
        fetch_competitions(
            "secret-key",
            base_url="https://example.test/tennis",
            http_get=fake_get,
        )

    assert exc.value.kind == "TIMEOUT"
