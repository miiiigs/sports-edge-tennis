"""Offline log-evidence classifier; not a durable work-product receipt.

Never equate a successful exit line with successful fixture discovery or
independent archival. Logs can diagnose a problem, not authorize activation.
"""
from __future__ import annotations

from datetime import datetime


def _time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("aware time required")
    return result


def assess_slot(row):
    slot = _time(row["slot"])
    starts, completions, heartbeats = (row[key] for key in
        ("starts", "completions", "heartbeats"))
    if not starts:
        state = "MISSING_START"
    elif len(starts) != 1 or len(completions) > 1 or len(heartbeats) > 1:
        state = "AMBIGUOUS_LOG_CARDINALITY"
    elif not completions:
        state = "MISSING_COMPLETION"
    else:
        completion = completions[0]
        if not slot <= _time(starts[0]) <= _time(completion["at"]):
            state = "INVALID_LOG_ORDER"
        elif any(type(completion.get(k)) is not int or completion[k] < 0
                 for k in ("slates", "failed_slates")):
            state = "INVALID_SLATE_COUNTS"
        elif completion["failed_slates"]:
            state = "PARTIAL_SLATES"
        elif row["unavailable"]:
            state = "CONFLICTING_DISCOVERY_LOGS"
        elif completion["slates"] == 0:
            state = "UNVERIFIED_EMPTY"
        else:
            state = "COMPLETION_LOG_ONLY"
    archive = "UNVERIFIED"
    if len(heartbeats) == 1:
        heartbeat = heartbeats[0]
        if heartbeat["persistent_storage_verified"] is True:
            archive = "PERSISTENCE_CLAIM_NOT_INDEPENDENT"
        elif heartbeat["records_logged"] > 0:
            archive = "CAPTURE_LOGGED_PERSISTENCE_UNVERIFIED"
        else:
            archive = "NO_CAPTURE_LOGGED_PERSISTENCE_UNVERIFIED"
    return {"state":state,"archive_state":archive,
            "independent_receipt_verified":False,"healthy_for_activation":False}


def summarize(rows):
    from collections import Counter
    results = [assess_slot(row) for row in rows]
    return {"expected_slots":len(rows),
            "execution_states":dict(Counter(x["state"] for x in results)),
            "archive_states":dict(Counter(x["archive_state"] for x in results)),
            "activation_healthy_slots":0}
