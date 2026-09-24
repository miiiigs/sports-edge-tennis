from __future__ import annotations

"""Sanitized diagnostic for the OddsPapi odds-by-tournaments HTTP 404.

Prints request shape (never the API key) and sanitized response metadata for the
known-good 130870 tournament id (34770) and a few variants. Read-only.
"""

import json
import os

import requests

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.oddspapi import ODDSPAPI_BASE_URL, ODDSPAPI_TENNIS_SPORT_ID

BASE = ODDSPAPI_BASE_URL
KEY = load_secret("ODDSPAPI_API_KEY") or ""


def _redact(text: str) -> str:
    if KEY and KEY in text:
        text = text.replace(KEY, "<redacted>")
    return text[:600]


def _describe(path: str, params: dict) -> dict:
    sanitized = {k: ("<redacted>" if k == "apiKey" else v) for k, v in params.items()}
    request = requests.Request("GET", f"{BASE}{path}", params=params)
    prepared = request.prepare()
    return {
        "method": "GET",
        "path": path,
        "params": sanitized,
        "encoded_query_without_key": _redact(
            prepared.url.split("?", 1)[1] if "?" in prepared.url else ""
        ),
    }


def _call(label: str, path: str, params: dict) -> None:
    print("ODDSPAPI_DIAG_REQUEST " + json.dumps({"label": label, **_describe(path, params)}, sort_keys=True), flush=True)
    try:
        response = requests.get(f"{BASE}{path}", params=params, timeout=20)
    except Exception as exc:  # noqa: BLE001
        print("ODDSPAPI_DIAG_RESPONSE " + json.dumps({"label": label, "exception": type(exc).__name__}), flush=True)
        return
    body = response.text[:600]
    print(
        "ODDSPAPI_DIAG_RESPONSE "
        + json.dumps(
            {
                "label": label,
                "status": response.status_code,
                "content_type": response.headers.get("Content-Type"),
                "body_prefix": _redact(body),
            },
            sort_keys=True,
        ),
        flush=True,
    )


def main() -> int:
    if not KEY:
        print("ODDSPAPI_DIAG_BLOCKED no_key", flush=True)
        return 2
    common = {"language": "en", "verbosity": 3, "oddsFormat": "decimal"}
    # 1) known-good 130870 tournament id from the successful 08:07 cycle, singular bookmaker
    _call("known_tid_singular", "/odds-by-tournaments", {"apiKey": KEY, "tournamentIds": "34770", "bookmaker": "pinnacle+30", **common})
    # 2) known-good tournament id, plural bookmakers param
    _call("known_tid_plural", "/odds-by-tournaments", {"apiKey": KEY, "tournamentIds": "34770", "bookmakers": "pinnacle+30", **common})
    # 3) no tournamentIds
    _call("no_tournament_ids", "/odds-by-tournaments", {"apiKey": KEY, "bookmaker": "pinnacle+30", **common})
    # 4) fresh discovery then qualification on the discovered tournament id
    try:
        fixtures = requests.get(
            f"{BASE}/fixtures",
            params={
                "apiKey": KEY,
                "sportId": ODDSPAPI_TENNIS_SPORT_ID,
                "bookmakers": "pinnacle+30",
                "language": "en",
                "hasOdds": "true",
                "statusId": 0,
            },
            timeout=20,
        )
        rows = fixtures.json() if fixtures.status_code == 200 else []
        tid = None
        for row in rows if isinstance(rows, list) else []:
            if row.get("tournamentId") is not None:
                tid = int(row["tournamentId"])
                break
        print("ODDSPAPI_DIAG_DISCOVERY " + json.dumps({"status": fixtures.status_code, "fixtures": len(rows) if isinstance(rows, list) else 0, "first_tournament_id": tid}, sort_keys=True), flush=True)
        if tid is not None:
            _call("discovered_tid_singular", "/odds-by-tournaments", {"apiKey": KEY, "tournamentIds": str(tid), "bookmaker": "pinnacle+30", **common})
    except Exception as exc:  # noqa: BLE001
        print("ODDSPAPI_DIAG_DISCOVERY " + json.dumps({"exception": type(exc).__name__}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
