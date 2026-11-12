"""All-history Tennis Model Picks accountability, not a five-day-only audit.

One row for every original aligned first-issued sporting match, across EVERY
Manila reporting day since the actual first recorded pick. There is no
lookback truncation, quiet record deletion, stale-data fallback, or guessed
VOID. A match with no source-backed terminal outcome stays PENDING and gets
an operational follow-up classification, even after 90+ days.

The endpoint and export are read-only. No provider calls, result repairs,
selection changes or automated voids are performed here.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sports_edge_tennis.dashboard.aligned_model_picks import (
    aligned_production_model_picks_cte,
)
from sports_edge_tennis.dashboard.qualified_parallel import CONFIDENCE_FLOOR

TERMINAL = ("COMPLETED", "RETIRED", "WALKOVER", "CANCELLED")
OUTCOMES = ("WON", "LOST", "VOID", "PENDING")
LOOKBACK = "ALL_ISSUED_ALIGNED_MODEL_PICKS_NO_DATE_CUTOFF"


def query() -> str:
    """One SQL query with hard as-of fences on mutable match observations."""
    return f"""
        WITH {aligned_production_model_picks_cte()}
        SELECT p.match_id, p.picked_at, p.selected_side,
               p.aligned_probability, p.aligned_pick_type,
               pit.match_start_at AS original_start,
               pit.player_1_id AS original_player_1_id,
               pit.player_2_id AS original_player_2_id,
               pit.ingested_at AS pit_ingested_at,
               pit.source_observed_at AS pit_observed_at,
               latest.match_start_at AS latest_start,
               latest.match_status::text AS latest_fixture_status,
               latest.source_observed_at AS latest_fixture_observed_at,
               latest.ingested_at AS latest_fixture_ingested_at,
               r.outcome_status, r.winner_side,
               r.source_observed_at AS result_source_observed_at,
               r.ingested_at AS result_ingested_at,
               r.match_result_observation_id AS result_observation_id,
               p1.canonical_name AS player_1, p2.canonical_name AS player_2,
               EXISTS (
                   SELECT 1 FROM match_source_ids ids
                   WHERE ids.match_id=p.match_id
                     AND ids.source_system='API_TENNIS'
               ) AS mapped_api_tennis
        FROM aligned_model_picks p
        LEFT JOIN LATERAL (
            SELECT mv.match_start_at,mv.player_1_id,mv.player_2_id,
                   mv.ingested_at,mv.source_observed_at
            FROM match_versions mv
            WHERE mv.match_id=p.match_id
              AND mv.as_of <= p.picked_at
              AND mv.source_observed_at <= p.picked_at
              AND mv.ingested_at <= p.picked_at
            ORDER BY mv.as_of DESC,mv.source_observed_at DESC,
                     mv.match_version_id DESC LIMIT 1
        ) pit ON TRUE
        LEFT JOIN LATERAL (
            SELECT mv.match_start_at,mv.match_status,mv.source_observed_at,
                   mv.ingested_at
            FROM match_versions mv
            WHERE mv.match_id=p.match_id
              AND mv.as_of <= %(as_of)s
              AND mv.source_observed_at <= %(as_of)s
              AND mv.ingested_at <= %(as_of)s
            ORDER BY mv.as_of DESC,mv.source_observed_at DESC,
                     mv.match_version_id DESC LIMIT 1
        ) latest ON TRUE
        LEFT JOIN LATERAL (
            SELECT mro.outcome_status::text AS outcome_status,
                   mro.winner_side,mro.source_observed_at,
                   mro.ingested_at,mro.match_result_observation_id
            FROM match_result_observations mro
            WHERE mro.match_id=p.match_id
              AND mro.outcome_status IN (
                  'COMPLETED','RETIRED','WALKOVER','CANCELLED'
              )
              AND mro.as_of <= %(as_of)s
              AND mro.source_observed_at <= %(as_of)s
              AND mro.ingested_at <= %(as_of)s
            ORDER BY mro.as_of DESC,mro.source_observed_at DESC,
                     mro.match_result_observation_id DESC LIMIT 1
        ) r ON TRUE
        LEFT JOIN players p1 ON p1.player_id=pit.player_1_id
        LEFT JOIN players p2 ON p2.player_id=pit.player_2_id
        WHERE p.picked_at <= %(as_of)s
        ORDER BY p.picked_at,p.match_id
    """


def _iso(value):
    return value.isoformat() if value is not None else None


def _state(terminal, winner, side):
    if terminal == "COMPLETED" and winner in (1, 2):
        return "WON" if winner == side else "LOST"
    if terminal in ("RETIRED", "WALKOVER", "CANCELLED"):
        return "VOID"
    return "PENDING"


def _pending_reason(*, now, start, latest_status, mapped, observation):
    """Operational substatus, NOT an inferred tennis settlement."""
    if latest_status in TERMINAL:
        return "TERMINAL_NO_USABLE_RESULT"
    if latest_status == "POSTPONED":
        return "POSTPONED_AWAITING_OFFICIAL_UPDATE"
    if start is None:
        return "SCHEDULE_UNVERIFIED"
    if start > now:
        return "FUTURE_STORED_START_UNVERIFIED"
    if not mapped:
        return "MISSING_RESULT_PROVIDER_EVENT_MAPPING"
    overdue_hours = (now-start).total_seconds()/3600
    if overdue_hours >= 72:
        return "OVERDUE_PROVIDER_REVIEW"
    if overdue_hours >= 24:
        return "RESULT_24H_OVERDUE"
    if observation is None:
        return "PROVIDER_NOT_OBSERVED"
    return "AWAITING_OFFICIAL_RESULT"


def _action(reason):
    return {
        "TERMINAL_NO_USABLE_RESULT":"VERIFY_WINNER_OR_AUTHORITATIVE_VOID",
        "POSTPONED_AWAITING_OFFICIAL_UPDATE":"RECHECK_OFFICIAL_SCHEDULE",
        "SCHEDULE_UNVERIFIED":"VERIFY_PROVIDER_IDENTITY_AND_SCHEDULE",
        "FUTURE_STORED_START_UNVERIFIED":"RECONFIRM_UPCOMING_PROVIDER_STATUS",
        "MISSING_RESULT_PROVIDER_EVENT_MAPPING":"REPAIR_EVENT_ID_MAPPING",
        "OVERDUE_PROVIDER_REVIEW":"RETRY_HISTORY_AND_ESCALATE",
        "RESULT_24H_OVERDUE":"RETRY_RESULT_COLLECTION",
        "PROVIDER_NOT_OBSERVED":"VERIFY_PROVIDER_FIXTURE_EXISTS",
        "AWAITING_OFFICIAL_RESULT":"RETRY_AFTER_GRACE_PERIOD",
    }.get(reason, "MANUAL_REVIEW")


def parse(rows, *, as_of):
    """Classify every first-issued pick; duplicate or later facts fail closed."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("timezone aware as_of required")
    from zoneinfo import ZoneInfo
    manila=ZoneInfo("Asia/Manila")
    seen=set()
    items=[]
    for row in rows:
        (mid,issued,side,confidence,cohort,original_start,player1_id,
         player2_id,pit_ingested,pit_observed,latest_start,latest_status,
         fixture_observed,fixture_ingested,terminal,winner,result_observed,
         result_ingested,result_id,p1,p2,mapped) = row
        mid=int(mid)
        if mid in seen:
            raise ValueError(f"duplicate immutable sporting match: {mid}")
        seen.add(mid)
        if side not in (1, 2) or issued > as_of:
            raise ValueError(f"invalid first-issued prediction: {mid}")
        try:
            probability=Decimal(confidence)
        except (InvalidOperation,TypeError,ValueError) as exc:
            raise ValueError(f"invalid original probability: {mid}") from exc
        if not probability.is_finite() or not 0<=probability<=1:
            raise ValueError(f"invalid original probability range: {mid}")
        for name,dt in (
            ("PIT fixture",pit_ingested),("PIT provider",pit_observed),
            ("latest fixture",fixture_observed),("fixture ingested",fixture_ingested),
            ("terminal provider",result_observed),("terminal ingested",result_ingested),
        ):
            if dt is not None and dt > as_of:
                raise ValueError(f"{name} ahead of audit cutoff for match {mid}")
        state=_state(terminal,winner,side)
        if original_start and issued < original_start and pit_ingested and pit_observed:
            pit_ok=True
        else:
            pit_ok=False
        # The lifetime reporting day is FROZEN from the earliest observable
        # match schedule at the time of first issuance, never latest reschedule.
        start_for_bucket=original_start or issued
        day=start_for_bucket.astimezone(manila).date().isoformat()
        pending_reason=None
        age_hours=None
        if state=="PENDING":
            pending_reason=_pending_reason(
                now=as_of,start=latest_start,latest_status=latest_status,
                mapped=bool(mapped),observation=fixture_observed,
            )
            if latest_start is not None:
                age_hours=round((as_of-latest_start).total_seconds()/3600,2)
        items.append({
            "match_id":mid,"day":day,
            "first_issued_at":_iso(issued),
            "original_start_at":_iso(original_start),
            "latest_stored_start_at":_iso(latest_start),
            "player_1":p1,"player_2":p2,
            "selected_side":side,
            "confidence":str(probability),
            "qualified_ge70":probability>=CONFIDENCE_FLOOR,
            "aligned_type":cohort,
            "status":state,
            "terminal_type":terminal,
            "winner_side":winner,
            "result_observation_id":result_id,
            "result_source_observed_at":_iso(result_observed),
            "result_ingested_at":_iso(result_ingested),
            "latest_stored_fixture_status":latest_status,
            "latest_fixture_observed_at":_iso(fixture_observed),
            "mapped_result_provider":bool(mapped),
            "pit_timestamp_verified":pit_ok,
            "pending_reason":pending_reason,
            "pending_age_hours":age_hours,
            "next_action":_action(pending_reason) if pending_reason else None,
            "external_provider_rechecked_during_audit":False,
        })
    return items


def _summary(items):
    items=list(items)
    status=Counter(x["status"] for x in items)
    scored=status["WON"]+status["LOST"]
    return {
        "selections":len(items),
        "won":status["WON"],"lost":status["LOST"],
        "void":status["VOID"],"pending":status["PENDING"],
        "graded":scored,
        "settled_accuracy":status["WON"]/scored if scored else None,
    }


def summarize(items, *, as_of):
    """All dates, including 0-pick dates; keep VOID/PENDING permanently."""
    if len({x["match_id"] for x in items})!=len(items):
        raise ValueError("duplicate all-history selections")
    per_day=defaultdict(list)
    for item in items:
        if item["status"] not in OUTCOMES:
            raise ValueError("unknown selection state")
        per_day[item["day"]].append(item)
    days=[]
    if items:
        from datetime import date
        first=date.fromisoformat(min(per_day))
        last=max(
            as_of.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Manila")).date(),
            date.fromisoformat(max(per_day)),
        )
        for i in range((last-first).days+1):
            day=(first+timedelta(days=i)).isoformat()
            all_day=per_day[day]
            original=_summary(all_day)
            retained=[x for x in all_day if x["qualified_ge70"]]
            filtered=_summary(retained)
            filtered["coverage"]=len(retained)/len(all_day) if all_day else None
            pending_reasons=Counter(x["pending_reason"] for x in all_day
                                    if x["status"]=="PENDING")
            days.append({
                "day":day,"original":original,"research_ge70":filtered,
                "pending_reasons":dict(sorted(pending_reasons.items())),
                "provisional":original["pending"]>0,
            })
    totals=_summary(items)
    qual=_summary(x for x in items if x["qualified_ge70"])
    qual["coverage"]=qual["selections"]/totals["selections"] if totals["selections"] else None
    pending=[x for x in items if x["status"]=="PENDING"]
    aged=[x for x in pending if x["pending_reason"] in {
        "TERMINAL_NO_USABLE_RESULT","OVERDUE_PROVIDER_REVIEW",
        "RESULT_24H_OVERDUE","MISSING_RESULT_PROVIDER_EVENT_MAPPING",
        "POSTPONED_AWAITING_OFFICIAL_UPDATE","SCHEDULE_UNVERIFIED",
    }]
    if totals["selections"]!=sum(totals[k] for k in ("won","lost","void","pending")):
        raise ValueError("all-history Original total does not reconcile")
    if qual["selections"]!=sum(qual[k] for k in ("won","lost","void","pending")):
        raise ValueError("all-history filtered total does not reconcile")
    if sum(day["original"]["selections"] for day in days)!=totals["selections"]:
        raise ValueError("all-day picks are not complete")
    return {
        "status":"REQUIRES_PROVIDER_RECONCILIATION" if pending else "ALL_RECORDED_GRADED_NOT_EXTERNALLY_RECHECKED",
        "source":"RAILWAY_POSTGRES_FIRST_ISSUED_ALIGNED_MODEL_PICKS",
        "as_of":as_of.isoformat(),
        "scope":LOOKBACK,
        "starts_at":days[0]["day"] if days else None,
        "ends_at":days[-1]["day"] if days else None,
        "original":totals,"existing_ge70_research":qual,
        "all_days":days,
        "total_historical_pending":len(pending),
        "older_pending_needing_action":len(aged),
        "pending_reason_counts":dict(sorted(Counter(
            x["pending_reason"] for x in pending
        ).items())),
        "unverified_first_issue_match_ids":[
            x["match_id"] for x in items if not x["pit_timestamp_verified"]
        ],
        "unresolved_matches":pending,
        "all_match_records":items,
        "note":(
            "NO PENDING MATCH IS VOIDED MERELY BECAUSE IT IS OLD. "
            "Only canonical observed CANCELLED/RETIRED/WALKOVER becomes VOID; "
            "unknown and stale provider data remain pending with a clear action. "
            "Provider records were NOT independently rechecked in this read."
        ),
    }


def load_lifetime_reconciliation(conn, *, as_of):
    rows=conn.execute(query(), {"as_of":as_of}).fetchall()
    return summarize(parse(rows, as_of=as_of),as_of=as_of)
