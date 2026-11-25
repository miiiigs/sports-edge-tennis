"""Read-only daily breakdown for the existing licensed fixture census.

Unknown stage evidence is never converted to a negative or a zero denominator.
UTC scheduled-day cohort, not issue-day cohort. No provider or DB access here.
"""
from collections import Counter
from datetime import UTC, timedelta

AGENTS = ("AGENT_1_SURFACE_ELO", "AGENT_2_HIERARCHICAL_MARKOV",
          "AGENT_3_INTRANSITIVITY")


def daily_waterfall(manifest, refs, internal, aligned_picks):
    manifest.validate()
    buckets = {}
    details = []
    canonical = {}
    seen = set()
    for ref in refs:
        ref.validate()
        if ref.source_match_id in seen:
            raise ValueError("duplicate official fixture ID")
        seen.add(ref.source_match_id)
        if not manifest.requested_window_start <= ref.scheduled_start < manifest.requested_window_end:
            raise ValueError("out of window fixture")
        day = ref.scheduled_start.astimezone(UTC).date().isoformat()
        key = (day, ref.tour_scope)
        c = buckets.setdefault(key, Counter())
        c["source_rows"] += 1
        row = {"source_match_id": ref.source_match_id, "day_utc": day,
               "tour": ref.tour_scope, "draw_stage": ref.draw_stage,
               "original_scheduled_start": ref.scheduled_start.isoformat(),
               "first_published": ref.first_published.isoformat() if ref.first_published else None,
               "feature_qualified": None, "player_mapping_at_issue": None,
               "published_external_notification": None, "settlement": None}
        reason = "OUT_OF_SCOPE"
        if ref.draw_stage == "MAIN" and ref.match_type == "SINGLES":
            c["main_draw"] += 1
            cutoff = ref.scheduled_start - timedelta(minutes=30)
            if ref.first_published is None:
                reason = "UNKNOWN_SOURCE_PUBLICATION"
            elif ref.first_published > cutoff:
                reason = "LATE_SOURCE_PUBLICATION"
            else:
                c["knowable"] += 1
                m = internal.get(ref.source_match_id, {})
                identity = m.get("identity", "NO_EXACT_PROVIDER_MAPPING")
                mid = m.get("canonical_match_id")
                row.update(canonical_match_id=mid, identity=identity,
                           first_run_id=m.get("first_run_id"),
                           agent_statuses=m.get("original_agent_statuses"),
                           manager_status=m.get("original_manager_status"),
                           match_version_id=m.get("match_version_id"),
                           internal_pit_match_status=m.get("internal_pit_match_status"))
                if mid is not None:
                    if mid in canonical and canonical[mid] != ref.source_match_id:
                        raise ValueError("multiple official fixtures map to one canonical ID")
                    canonical[mid] = ref.source_match_id
                if identity != "EXACT_CANONICAL":
                    reason = identity
                else:
                    c["exact_mapped"] += 1
                    if not m.get("internal_discovered_prematch"):
                        reason = "NO_PIT_MATCH_VERSION"
                    else:
                        c["discovered"] += 1
                        statuses = m.get("original_agent_statuses", {})
                        targeted = m.get("original_run_on_time") is True
                        if targeted:
                            c["targeted"] += 1
                            for code in AGENTS:
                                c[code + "_PREDICTED"] += statuses.get(code) == "PREDICTED"
                                c[code + "_ABSTAINED"] += statuses.get(code) == "ABSTAINED"
                                c[code + "_MISSING"] += code not in statuses
                            c["both_a1_a2"] += all(statuses.get(code) == "PREDICTED" for code in AGENTS[:2])
                            c["manager_consensus"] += m.get("original_manager_status") == "CONSENSUS"
                        pick = aligned_picks.get(mid)
                        if pick and pick[0] <= cutoff:
                            c["aligned_pick"] += 1
                            row["aligned_pick_type"] = pick[1]
                            row["aligned_pick_at"] = pick[0].isoformat()
                            reason = "ALIGNED_PICK"
                        elif not targeted:
                            reason = "NO_ON_TIME_TARGET"
                        elif m.get("original_manager_status") == "CONSENSUS":
                            reason = "CONSENSUS_NO_ALIGNED_PICK"
                        elif not m.get("original_manager_status") or m["original_manager_status"] == "MISSING":
                            reason = "MISSING_MANAGER_DECISION"
                        elif any(statuses.get(code) == "ABSTAINED" for code in AGENTS):
                            reason = "NO_PICK_WITH_AGENT_ABSTENTION"
                        else:
                            reason = "MANAGER_NON_CONSENSUS"
        row["classification"] = reason
        c["reason_" + reason] += 1
        details.append(row)
    result = []
    day = manifest.requested_window_start.astimezone(UTC).date()
    end = manifest.requested_window_end.astimezone(UTC)
    from datetime import datetime
    while datetime.combine(day, datetime.min.time(), UTC) < end:
        for tour in ("ATP_MAIN", "CHALLENGER"):
            key = (day.isoformat(), tour)
            c = buckets.get(key, Counter())
            begin = datetime.combine(day, datetime.min.time(), UTC)
            complete = (manifest.provider_declares_complete_window is True
                        and manifest.requested_window_start <= begin
                        and begin + timedelta(days=1) <= end)
            denom = c["knowable"] if complete and not c["reason_UNKNOWN_SOURCE_PUBLICATION"] else None
            result.append({"day_utc": key[0], "tour": tour,
                           "complete_day_declared": complete,
                           "official_main_draw_total": c["main_draw"] if complete else None,
                           "official_knowable_total": denom,
                           "aligned_pick_coverage_lower_bound": c["aligned_pick"] / denom if denom else None,
                           "feature_qualified": None, "settlement_wlpv": None,
                           "observed_counts": dict(c)})
        day += timedelta(days=1)
    return {"day_basis": "ORIGINAL_SCHEDULE_UTC",
            "daily": result, "fixtures": sorted(details, key=lambda r: (r["day_utc"], r["source_match_id"])),
            "external_publication_verified": False}


def reconcile_changes(previous, current, *, current_complete=False, explicit_removed=()):
    """Compare authorized snapshots without interpreting absence as cancellation.

    Dictionaries key stable provider IDs to original/current scheduled start.
    Explicit removals must come from an independently authorized removal feed.
    Preserve both clocks; never overwrite first-issue records.
    """
    removed = set(explicit_removed)
    if removed & current.keys():
        raise ValueError("contradictory current snapshot and removal feed")
    rows = []
    for fid in sorted(previous.keys() | current.keys() | removed):
        before, after = previous.get(fid), current.get(fid)
        if fid in removed:
            status = "EXPLICIT_REMOVAL"
        elif fid not in previous:
            status = "CREATED_OR_NEWLY_OBSERVED"
        elif fid not in current:
            status = "ABSENT_COMPLETE_SNAPSHOT" if current_complete is True else "UNKNOWN_INCOMPLETE_SNAPSHOT"
        elif before != after:
            status = "RESCHEDULED"
        else:
            status = "UNCHANGED"
        rows.append({"source_match_id": fid, "previous_start": before,
                     "current_start": after, "status": status,
                     "void_inferred": False})
    return rows
