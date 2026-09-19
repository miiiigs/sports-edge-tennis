from __future__ import annotations

from datetime import UTC, datetime

import pytest

import sports_edge_tennis.live.goalserve as goalserve
from sports_edge_tennis.live.goalserve import (
    fetch_goalserve_tennis_json,
    iter_goalserve_tennis_envelopes,
    iter_goalserve_tennis_matches,
)
from sports_edge_tennis.live.transport import TransportError


class _Response:
    def __init__(self, *, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_goalserve_transport_does_not_leak_credential_url(monkeypatch) -> None:
    secret_url = "https://example.invalid/secret-token/tennis.json"

    def fail(*args, **kwargs):
        raise goalserve.requests.exceptions.Timeout("secret")

    monkeypatch.setattr(goalserve.requests, "get", fail)
    with pytest.raises(TransportError) as exc:
        fetch_goalserve_tennis_json(secret_url)
    assert secret_url not in str(exc.value)
    assert "secret-token" not in str(exc.value)


def test_goalserve_transport_requires_json_object(monkeypatch) -> None:
    monkeypatch.setattr(
        goalserve.requests,
        "get",
        lambda *a, **k: _Response(payload=[{"id": "bad-root"}]),
    )
    with pytest.raises(TransportError, match="JSON object"):
        fetch_goalserve_tennis_json(
            "https://example.invalid/tennis.json",
            clock=lambda: datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        )


def test_goalserve_match_iterator_handles_singletons_lists_and_dedupes() -> None:
    payload = {
        "scores": {
            "category": [
                {
                    "id": "100",
                    "name": "Atp - Singles: Test, Hard",
                    "match": {
                        "id": "1",
                        "date": "19.09.2026",
                        "time": "12:00",
                        "player": [
                            {"id": "p1", "name": "A. Player"},
                            {"id": "p2", "name": "B. Player"},
                        ],
                    },
                },
                {
                    "id": "200",
                    "name": "Atp - Singles: Other, Clay",
                    "match": [
                        {
                            "id": "2",
                            "date": "19.09.2026",
                            "time": "13:00",
                            "player": [
                                {"id": "p3", "name": "C. Player"},
                                {"id": "p4", "name": "D. Player"},
                            ],
                        },
                        {
                            "id": "1",
                            "date": "19.09.2026",
                            "time": "12:00",
                            "player": [
                                {"id": "p1", "name": "A. Player"},
                                {"id": "p2", "name": "B. Player"},
                            ],
                        },
                    ],
                },
            ]
        }
    }
    matches = iter_goalserve_tennis_matches(payload)
    assert [match["id"] for match in matches] == ["1", "2"]

    envelopes = iter_goalserve_tennis_envelopes(payload)
    assert [row.provider_event_id for row in envelopes] == ["1", "2"]
    assert envelopes[0].category_id == "100"
    assert envelopes[0].match_start_at == datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    assert envelopes[0].player_1_id == "p1"
    assert envelopes[0].player_2_id == "p2"
