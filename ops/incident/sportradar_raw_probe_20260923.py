from __future__ import annotations

"""Raw sanitized Sportradar transport diagnostic (one request)."""

import os

import certifi
import requests

from sports_edge_tennis.live.credentials import load_secret


def main() -> int:
    key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not key:
        print("RAW_PROBE_BLOCKED missing_key")
        return 2
    access = os.environ.get("SPORTRADAR_TENNIS_ACCESS_LEVEL", "trial")
    language = os.environ.get("SPORTRADAR_TENNIS_LANGUAGE", "en")
    url = (
        f"https://api.sportradar.com/tennis/{access}/v3/{language}"
        "/schedules/2026-09-23/summaries.json"
    )
    try:
        resp = requests.get(
            url,
            headers={"x-api-key": key, "accept": "application/json"},
            timeout=20,
            verify=certifi.where(),
        )
    except Exception as exc:
        print(f"RAW_PROBE_EXCEPTION type={type(exc).__name__} msg={str(exc)[:300]}")
        return 0
    print(f"RAW_PROBE status={resp.status_code} content_type={resp.headers.get('Content-Type')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
