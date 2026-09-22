from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.sportradar import fetch_sport_event_summary


EVENT_ID = "sr:sport_event:74861362"


def _status_view(payload: Mapping[str, object]) -> dict[str, object]:
    status = payload.get("sport_event_status")
    if not isinstance(status, Mapping):
        return {}
    keys = (
        "status",
        "match_status",
        "winner_id",
        "winning_reason",
        "home_score",
        "away_score",
        "period_scores",
    )
    return {key: status.get(key) for key in keys if key in status}


def _exception_nodes(value: object, path: str = "$") -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    if isinstance(value, Mapping):
        text = json.dumps(value, sort_keys=True, default=str).casefold()
        if any(token in text for token in ("retir", "walkover", "default")):
            found.append(
                {
                    "path": path,
                    "keys": sorted(str(key) for key in value.keys()),
                    "status": value.get("status"),
                    "match_status": value.get("match_status"),
                    "type": value.get("type"),
                    "reason": value.get("reason"),
                    "winning_reason": value.get("winning_reason"),
                    "time": value.get("time"),
                    "event_time": value.get("event_time"),
                }
            )
        for key, item in value.items():
            found.extend(_exception_nodes(item, f"{path}.{key}"))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            found.extend(_exception_nodes(item, f"{path}[{index}]"))
    return found[:25]


def main() -> int:
    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not api_key:
        raise RuntimeError("SPORTRADAR_TENNIS_API_KEY unavailable")

    summary_received_at, summary = fetch_sport_event_summary(api_key, EVENT_ID)

    result = {
        "status": "OK",
        "event_id": EVENT_ID,
        "provider_requests": 1,
        "writes": 0,
        "summary_received_at": summary_received_at.isoformat(),
        "summary_top_level_keys": sorted(summary.keys()),
        "summary_status": _status_view(summary),
        "summary_exception_nodes": _exception_nodes(summary),
    }
    print(
        "SPORTRADAR_RETIREMENT_DETAIL "
        + json.dumps(result, sort_keys=True, default=str),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
