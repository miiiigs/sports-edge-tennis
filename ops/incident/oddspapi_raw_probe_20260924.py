from __future__ import annotations

"""Raw sanitized OddsPapi diagnostic (few requests)."""

import json
import os
from datetime import UTC, datetime, timedelta

import requests

from sports_edge_tennis.live.credentials import load_secret

BASE = "https://api.oddspapi.io/v4"


def _get(path: str, params: dict, key: str) -> dict:
    full = dict(params)
    full["apiKey"] = key
    try:
        resp = requests.get(f"{BASE}/{path}", params=full, timeout=20)
    except Exception as exc:
        return {"path": path, "exception": type(exc).__name__, "msg": str(exc)[:200]}
    out = {"path": path, "status": resp.status_code, "content_type": resp.headers.get("Content-Type")}
    try:
        body = resp.json()
        out["body_type"] = type(body).__name__
        if isinstance(body, dict):
            out["keys"] = sorted(body.keys())[:20]
            out["sample"] = str(body)[:600]
        else:
            out["sample"] = str(body)[:600]
    except ValueError:
        out["body_prefix"] = resp.text[:300]
    return out


def main() -> int:
    key = load_secret("ODDSPAPI_API_KEY")
    if not key:
        print("RAW_ODDSPAPI_BLOCKED no_key")
        return 2
    now = datetime.now(UTC)
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(days=2)
    params_fixtures = {
        "sportId": 12,
        "from": start.isoformat().replace("+00:00", "Z"),
        "to": end.isoformat().replace("+00:00", "Z"),
        "statusId": 0,
        "hasOdds": "true",
        "bookmakers": "pinnacle+30",
        "language": "en",
        "apiKey": key,
    }
    report: list[dict] = []
    tournament_ids: list[int] = []
    try:
        resp = requests.get(f"{BASE}/fixtures", params=params_fixtures, timeout=20)
        entry = {"path": "fixtures", "status": resp.status_code, "content_type": resp.headers.get("Content-Type")}
        body = resp.json()
        rows = body if isinstance(body, list) else body.get("fixtures", [])
        entry["rows"] = len(rows)
        for row in rows[:50]:
            tid = row.get("tournamentId")
            if tid is not None:
                tournament_ids.append(int(tid))
        report.append(entry)
    except Exception as exc:
        report.append({"path": "fixtures", "exception": type(exc).__name__, "msg": str(exc)[:200]})

    first_tid = tournament_ids[0] if tournament_ids else None
    if first_tid is not None:
        for label, params in (
            (
                "odds-by-tournaments",
                {
                    "tournamentIds": str(first_tid),
                    "bookmakers": "pinnacle+30",
                    "language": "en",
                    "verbosity": 3,
                    "oddsFormat": "decimal",
                    "apiKey": key,
                },
            ),
        ):
            try:
                resp = requests.get(f"{BASE}/{label}", params=params, timeout=20)
                entry = {"path": label, "tournamentId": first_tid, "status": resp.status_code}
                try:
                    b = resp.json()
                    entry["body_type"] = type(b).__name__
                    entry["sample"] = str(b)[:400].replace(key, "<redacted>")
                except ValueError:
                    entry["body_prefix"] = resp.text[:200]
                report.append(entry)
            except Exception as exc:
                report.append({"path": label, "exception": type(exc).__name__, "msg": str(exc)[:200]})

    print("RAW_ODDSPAPI " + json.dumps({"report": report}, sort_keys=True)[:5000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

