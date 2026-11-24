"""Offline diagnostic on authorized, sanitized fixture and odds exports.

No network, DB, credentials, cache mutation or live prediction imports.
See docs/research/ODDS_IDENTITY_MARKET_FUNNEL_20261124.md for input contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BOOKS = ("pinnacle+30", "1xbet", "bet365")
NAMESPACES = ("ODDSPAPI", "SPORTRADAR", "PINNACLE")
MAX_AGE = timedelta(hours=6)
MAX_SPAN = timedelta(minutes=5)
MAX_DRIFT = timedelta(minutes=30)


def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timezone required")
    return result


def keys(ids):
    """Never compare raw IDs across provider namespaces."""
    out = set()
    for ns in NAMESPACES:
        value = ids.get(ns)
        if value is None or isinstance(value, bool):
            continue
        value = str(value).strip()
        if ns == "SPORTRADAR":
            value = value.removeprefix("sr:sport_event:")
            if not value.isascii() or not value.isdecimal():
                continue
        if value:
            out.add((ns, value))
    return out


def event_keys(event):
    return keys({**event.get("external_ids", {}), "ODDSPAPI": event["event_id"]})


def unique_rows(rows):
    return [json.loads(v) for v in sorted({json.dumps(r, sort_keys=True) for r in rows})]


def identity(fixture, events, index, audit_at):
    candidates = {i for key in keys(fixture["source_ids"]) for i in index.get(key, ())}
    if not candidates:
        return "MISSING_IDENTITY", None
    if len(candidates) != 1:
        return "IDENTITY_CONFLICT", None
    event = events[next(iter(candidates))]
    # Conflicting known identifiers must not be ignored when one other ID agrees.
    fk, ek = dict(keys(fixture["source_ids"])), dict(event_keys(event))
    if any(fk[k] != ek[k] for k in fk.keys() & ek.keys()):
        return "IDENTITY_CONFLICT", None
    try:
        seen = stamp(event["observed_at"])
        if seen > audit_at or audit_at - seen > timedelta(hours=24):
            return "STALE_OR_FUTURE_IDENTITY", None
        if event["tournament_id"] not in fixture["verified_tournament_ids"]:
            return "UNVERIFIED_TOURNAMENT", None
        if abs(stamp(event["start_at"]) - stamp(fixture["start_at"])) > MAX_DRIFT:
            return "IDENTITY_START_CONFLICT", None
        sides = event["canonical_players"]
        if len(sides) != 2 or len(set(sides)) != 2 or set(sides) != set(fixture["players"]):
            return "INVALID_PLAYER_SIDE_MAPPING", None
    except (KeyError, TypeError, ValueError):
        return "UNVERIFIED_IDENTITY_EVIDENCE", None
    return "VERIFIED_IDENTITY", event


def market(fixture, event, quotes, audit_at, book):
    cutoff = stamp(fixture["cutoff"])
    if cutoff > audit_at or cutoff > stamp(fixture["start_at"]) - timedelta(minutes=30):
        return "INVALID_CUTOFF", None
    rows = [q for q in quotes if q.get("bookmaker") == book
            and q.get("market") == "MATCH_WINNER"]
    if not rows:
        return "NO_STORED_MARKET", None
    valid, invalid_time = [], False
    for q in rows:
        try:
            observed, as_of, ingested = map(stamp, (q["observed_at"], q["as_of"], q["ingested_at"]))
            if observed > as_of or as_of > ingested:
                invalid_time = True
                continue
            if max(observed, as_of, ingested) <= cutoff:
                valid.append((q, observed, ingested))
        except (KeyError, TypeError, ValueError):
            invalid_time = True
    if invalid_time:
        return "INVALID_QUOTE_TIMESTAMP", None
    if not valid:
        return "LATE_QUOTATION", None
    # Latest state per side, including suspended updates. Never resurrect an
    # earlier open price after a suspension. Require one data source per pair.
    by_side = defaultdict(list)
    for q, observed, ingested in valid:
        if q.get("player_id") not in event["canonical_players"] or not q.get("source"):
            return "INVALID_PLAYER_SIDE_MAPPING", None
        by_side[q["player_id"]].append((q, observed, ingested))
    if any(not by_side[p] for p in fixture["players"]):
        return "INCOMPLETE_MARKET", None
    pair = []
    for p in fixture["players"]:
        newest = max(x[1] for x in by_side[p])
        ties = [x for x in by_side[p] if x[1] == newest]
        if len(ties) != 1:
            return "AMBIGUOUS_QUOTE_STATE", None
        pair.append(ties[0])
    if any(x[0].get("status") == "SUSPENDED" for x in pair):
        return "SUSPENDED_MARKET", None
    if any(x[0].get("status") != "OPEN" for x in pair):
        return "NON_OPEN_MARKET", None
    if any(cutoff - x[1] > MAX_AGE for x in pair):
        return "STALE_QUOTATION", None
    if pair[0][0]["source"] != pair[1][0]["source"] or abs(pair[0][1] - pair[1][1]) > MAX_SPAN:
        return "UNSYNCHRONIZED_MARKET", None
    try:
        odds = [float(x[0]["price"]) for x in pair]
        if any(not math.isfinite(x) or x <= 1 for x in odds):
            raise ValueError
        implied = [1 / x for x in odds]
        total = sum(implied)
        if not .95 <= total <= 1.25:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        return "INVALID_PRICE", None
    return "QUALIFIED", {"p_player1": implied[0] / total, "prices": odds,
                         "observed_at": [x[1].isoformat() for x in pair],
                         "ingested_at": [x[2].isoformat() for x in pair],
                         "source": pair[0][0]["source"]}


def audit(data):
    audit_at = stamp(data["audit_at"])
    fixtures = unique_rows(data["fixtures"])
    if len({f["match_id"] for f in fixtures}) != len(fixtures):
        raise ValueError("conflicting duplicate canonical fixture")
    events = unique_rows(data["events"])
    index = defaultdict(set)
    for i, e in enumerate(events):
        for key in event_keys(e):
            index[key].add(i)
    by_event = defaultdict(list)
    for q in unique_rows(data["quotes"]):
        by_event[q["event_id"]].append(q)
    resolved = [(f, *identity(f, events, index, audit_at)) for f in fixtures]
    owners = Counter(e["event_id"] for _, _, e in resolved if e is not None)
    result = []
    for f, reason, e in sorted(resolved, key=lambda x: str(x[0]["match_id"])):
        if e is not None and owners[e["event_id"]] > 1:
            reason, e = "IDENTITY_CONFLICT", None
        row = {"match_id": f["match_id"], "identity": reason, "books": {}}
        if e is not None:
            for book in BOOKS:
                state, quote = market(f, e, by_event[e["event_id"]], audit_at, book)
                row["books"][book] = {"state": state, "quote": quote}
        result.append(row)
    identity_counts = Counter(r["identity"] for r in result)
    qualified = sum(any(b["state"] == "QUALIFIED" for b in r["books"].values()) for r in result)
    return {"research_only": True, "provider_calls": 0, "database_writes": False,
            "audit_at": audit_at.isoformat(), "fixture_count": len(result),
            "input_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
            "identity_counts": dict(identity_counts),
            "book_states": {b: dict(Counter(r["books"][b]["state"] for r in result if b in r["books"])) for b in BOOKS},
            "qualified_any_book": qualified, "rows": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(json.loads(args.export.read_text())), sort_keys=True))


if __name__ == "__main__":
    main()
