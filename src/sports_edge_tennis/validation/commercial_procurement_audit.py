"""Offline procurement evaluation; consumes independently verified normalized exports.

No provider transport, database access, credential handling, or production writes.
"""
from collections import Counter
from datetime import datetime
from math import ceil, isfinite

CHECKPOINTS = ("24h", "12h", "6h", "3h", "1h", "30m", "issue")
TOURS = ("ATP_MAIN", "CHALLENGER")
HOURS = dict(zip(CHECKPOINTS[:-1], (24, 12, 6, 3, 1, .5)))


def clock(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def price(value):
    return type(value) in (float, int) and isfinite(value) and value > 1


def budget(fixtures=160, books=1, tournament_batches=None):
    """Planning scenarios, not entitlement or instructions to execute requests."""
    if type(fixtures) is not int or type(books) is not int or fixtures < 0 or books < 1:
        raise ValueError("invalid counts")
    if tournament_batches is not None and (type(tournament_batches) is not int or tournament_batches < 0):
        raise ValueError("invalid batch count")
    history = fixtures * ceil(books / 3)
    return {"fixture_checkpoint_calls": fixtures * len(CHECKPOINTS),
            "tournament_calls": None if tournament_batches is None else tournament_batches * books,
            "historical_calls": history, "historical_quota_units_documented": 0,
            "historical_min_spacing_seconds": max(0, history - 1) * 5,
            "excludes_discovery_and_retries": True}


def audit(roster, observations, *, freshness_seconds=3600):
    """Roster must be independent; checkpoint schedule versions must be pre-frozen.

    Each observation is a same-book, same-market paired snapshot from the
    bookmaker worker, including mapping verification, capture and quote clocks.
    Only latest known observation per bookmaker is eligible; never resurrect
    a suspended or invalid newer quote. Archive-only records are not live proof.
    """
    if not isfinite(freshness_seconds) or freshness_seconds < 0:
        raise ValueError("invalid freshness")
    ids = {}
    for row in roster:
        key = row["fixture_id"]
        if not key or key in ids or row["tour"] not in TOURS:
            raise ValueError("duplicate, empty or out-of-scope fixture")
        ids[key] = row
    indexed = {}
    for row in observations:
        if row["fixture_id"] not in ids:
            raise ValueError("observation outside frozen roster")
        indexed.setdefault(row["fixture_id"], []).append(row)
    result = {}
    for tour in TOURS:
        population = [r for r in roster if r["tour"] == tour]
        cells = {}
        for cp in CHECKPOINTS:
            counts = Counter(eligible=len(population))
            for fixture in population:
                target = fixture.get("checkpoints", {}).get(cp, {})
                cutoff = clock(target.get("cutoff"))
                schedule = clock(target.get("schedule_known_at"))
                start = clock(target.get("scheduled_start"))
                if not cutoff or not schedule or not start or schedule > cutoff or cutoff >= start or (cp in HOURS and (start-cutoff).total_seconds() != HOURS[cp]*3600):
                    counts["missing_checkpoint"] += 1
                    continue
                rows = indexed.get(fixture["fixture_id"], [])
                if any(not clock(x.get("received_at")) or not clock(x.get("quote_at")) for x in rows):
                    counts["fixture_with_missing_timestamp"] += 1
                known = [x for x in rows if clock(x.get("received_at")) and clock(x["received_at"]) <= cutoff and x.get("archive_only") is False]
                if not known:
                    counts["not_discovered"] += 1
                    continue
                counts["discovered"] += 1
                if any(x.get("identity_status") == "INCORRECT" for x in known):
                    counts["incorrect_mapping_observed"] += 1
                mapped = [x for x in known if x.get("identity_status") == "VERIFIED" and clock(x.get("identity_verified_at")) and clock(x["identity_verified_at"]) <= cutoff]
                if not mapped:
                    counts["mapping_unverified"] += 1
                    continue
                counts["correct_mapping"] += 1
                books = {}
                for row in mapped:
                    if row.get("bookmaker_verified") is not True or not row.get("bookmaker") or row.get("market") != "MATCH_WINNER":
                        continue
                    books.setdefault(row["bookmaker"], []).append(row)
                if books:
                    counts["verified_bookmaker"] += 1
                valid = False
                for entries in books.values():
                    latest_time = max(clock(x["received_at"]) for x in entries)
                    latest = [x for x in entries if clock(x["received_at"]) == latest_time]
                    if len(latest) != 1:
                        continue
                    x = latest[0]
                    quote = clock(x.get("quote_at"))
                    if not quote or quote > latest_time or x.get("active") is not True or not price(x.get("p1")) or not price(x.get("p2")):
                        continue
                    if (cutoff-quote).total_seconds() > freshness_seconds:
                        continue
                    valid = True
                counts["valid_fresh_pair" if valid else "missing_valid_market"] += 1
            n = counts["eligible"]
            cells[cp] = {**dict(counts), "correct_mapping_rate": counts["correct_mapping"]/n if n else None,
                         "valid_fresh_pair_rate": counts["valid_fresh_pair"]/n if n else None}
        result[tour] = cells
    return {"status": "OFFLINE_EXPORT_EVALUATION", "cohort_size": len(ids),
            "balanced_target_met": all(sum(r["tour"] == tour for r in roster) == 80 for tour in TOURS),
            "freshness_seconds": freshness_seconds, "by_tour": result}
