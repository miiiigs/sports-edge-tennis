from __future__ import annotations

"""Sanitized API-Tennis incident probe.

Makes the minimum live requests needed to classify the production failure.
Never prints the API key, the full credential-bearing URL, or any secret.
"""

import json
import os
import time
from datetime import date

import requests

BASE_URL = "https://api.api-tennis.com/tennis/"


def _safe_scalar(value, *, limit: int = 300):
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return f"<list len={len(value)}>"
    if isinstance(value, dict):
        return f"<dict keys={sorted(value.keys())}>"
    return f"<{type(value).__name__}>"


def probe(api_key: str, target_date: date) -> dict:
    params = {
        "method": "get_fixtures",
        "APIkey": api_key,
        "date_start": target_date.isoformat(),
        "date_stop": target_date.isoformat(),
        "timezone": "UTC",
    }
    started = time.monotonic()
    try:
        resp = requests.get(BASE_URL, params=params, timeout=20)
    except requests.exceptions.RequestException as exc:
        return {
            "target_date": target_date.isoformat(),
            "transport_exception": type(exc).__name__,
        }
    latency_ms = int((time.monotonic() - started) * 1000)

    result = {
        "target_date": target_date.isoformat(),
        "http_status": resp.status_code,
        "latency_ms": latency_ms,
        "content_type": resp.headers.get("Content-Type"),
        "retry_after": resp.headers.get("Retry-After"),
    }
    try:
        data = resp.json()
    except ValueError:
        result["valid_json"] = False
        result["body_prefix"] = resp.text[:200]
        return result

    result["valid_json"] = True
    result["top_level_type"] = type(data).__name__
    if isinstance(data, dict):
        result["top_level_keys"] = sorted(data.keys())
        result["success"] = data.get("success")
        result["result_type"] = type(data.get("result")).__name__
        result["result_sample"] = _safe_scalar(data.get("result"))
        if isinstance(data.get("result"), list):
            result["result_len"] = len(data["result"])
            if data["result"]:
                first = data["result"][0]
                result["first_row_keys"] = (
                    sorted(first.keys()) if isinstance(first, dict) else type(first).__name__
                )
                if isinstance(first, dict):
                    result["first_row"] = {
                        key: _safe_scalar(first.get(key)) for key in sorted(first.keys())
                    }
        result["error_field"] = _safe_scalar(data.get("error"))
    else:
        result["body_prefix"] = str(data)[:200]
    return result


def main() -> int:
    api_key = os.environ.get("API_TENNIS_API_KEY")
    if not api_key:
        print("PROBE_BLOCKED no_api_key")
        return 2
    dates = [date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25)]
    report = [probe(api_key, target) for target in dates]
    print("API_TENNIS_PROBE " + json.dumps({"requests": len(report), "probes": report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
