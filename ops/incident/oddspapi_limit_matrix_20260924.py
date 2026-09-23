from __future__ import annotations

"""Raw sanitized OddsPapi odds-by-tournaments limit matrix."""

import json
import os
import time
from datetime import UTC, datetime, timedelta

import requests

from sports_edge_tennis.live.credentials import load_secret

BASE = "https://api.oddspapi.io/v4"
DEFAULT_BOOKS = "pinnacle+30"
ALL_BOOKS = "pinnacle+30,1xbet,unibet,bet365"


def main() -> int:
    key = load_secret("ODDSPAPI_API_KEY")
    if not key:
        print("RAW_ODDSPAPI_BLOCKED no_key")
        return 2
    now = datetime.now(UTC)
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(days=2)
    fixtures = requests.get(
        f"{BASE}/fixtures",
        params={
            "sportId": 12,
            "from": start.isoformat().replace("+00:00", "Z"),
            "to": end.isoformat().replace("+00:00", "Z"),
            "statusId": 0,
            "hasOdds": "true",
            "bookmakers": DEFAULT_BOOKS,
            "language": "en",
            "apiKey": key,
        },
        timeout=20,
    ).json()
    rows = fixtures if isinstance(fixtures, list) else fixtures.get("fixtures", [])
    tids = []
    for row in rows:
        tid = row.get("tournamentId")
        if tid is not None and int(tid) not in tids:
            tids.append(int(tid))

    cases = [
        ("1tid_1book", tids[:1], DEFAULT_BOOKS),
        ("1tid_allbooks", tids[:1], ALL_BOOKS),
        ("5tid_1book", tids[:5], DEFAULT_BOOKS),
        ("10tid_1book", tids[:10], DEFAULT_BOOKS),
        ("10tid_allbooks", tids[:10], ALL_BOOKS),
    ]
    report = []
    for label, ids, books in cases:
        if not ids:
            continue
        time.sleep(1.6)
        try:
            resp = requests.get(
                f"{BASE}/odds-by-tournaments",
                params={
                    "tournamentIds": ",".join(str(i) for i in ids),
                    "bookmakers": books,
                    "language": "en",
                    "verbosity": 3,
                    "oddsFormat": "decimal",
                    "apiKey": key,
                },
                timeout=20,
            )
            entry = {"case": label, "n": len(ids), "status": resp.status_code}
            try:
                body = resp.json()
                if isinstance(body, list):
                    entry["rows"] = len(body)
                else:
                    entry["body"] = str(body)[:200].replace(key, "<redacted>")
            except ValueError:
                entry["body_prefix"] = resp.text[:150]
            report.append(entry)
        except Exception as exc:
            report.append({"case": label, "exception": type(exc).__name__})
    print("RAW_ODDSPAPI_MATRIX " + json.dumps({"report": report}, sort_keys=True)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
