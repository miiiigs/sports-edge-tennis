"""Read-only, canonical dashboard-aligned three-day production Model Pick audit.

All original fixtures are grouped by original pre-match scheduled START date
in Asia/Manila, not by latest revised start or when they were settled. One
canonical sporting fixture is counted only once; the original latest trusted
COMPLETED result determines win/loss, terminal nonplayed statuses are VOID,
and no result remains PENDING. This uses the exact production dashboard
Model Picks SQL and never manufactures a new pick, winner, forecast or bet.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
import os

import psycopg

from sports_edge_tennis.dashboard.model_pick_history import (
    load_aligned_daily_model_pick_summaries,
    load_aligned_daily_model_picks,
)
from sports_edge_tennis.dashboard.read_model import get_latest_live_operational_health
from sports_edge_tennis.dashboard.result_coverage import (
    load_terminal_fixtures_without_result,
)

VERSION = "LIVE_ORIGINAL_THREE_DAY_SCORECARD_V1"
LOCAL_TZ = "Asia/Manila"
STATUSES = ("WON", "LOST", "PENDING", "VOID")


def pending_reason(row, now: datetime) -> str:
    """Explain unresolved, without mutating or presuming a void."""
    if row.result_status != "PENDING":
        return "NOT_PENDING"
    fixture = row.latest_fixture_status
    if fixture == "POSTPONED":
        return "POSTPONED_PROVIDER_STATUS"
    if fixture in ("COMPLETED", "RETIRED", "WALKOVER", "DEFAULTED", "CANCELLED"):
        return "TERMINAL_FIXTURE_NO_RESULT_OBSERVATION"
    scheduled = row.latest_match_start_at or row.match_start_at
    if scheduled is None:
        return "MISSING_SCHEDULED_START"
    if scheduled.tzinfo is None:
        raise ValueError("fixture times must be timezone-aware")
    if scheduled > now:
        return "FUTURE_SCHEDULED"
    return "PAST_SCHEDULED_AWAITING_TERMINAL_RESULT"


def _overview_rows(rows) -> dict:
    counts = Counter(row.result_status for row in rows)
    if any(x not in STATUSES for x in counts):
        raise ValueError("unknown Model Pick result status")
    wins, losses, pending, void = (counts[k] for k in STATUSES)
    total = len(rows)
    assert wins + losses + pending + void == total
    return {
        "issued": total,
        "wins": wins, "losses": losses, "pending": pending, "void": void,
        "settled": wins + losses,
        "settled_accuracy": wins / (wins + losses) if wins + losses else None,
    }


def run(conn: psycopg.Connection, *, first_day: date, days: int = 3) -> dict:
    if not 1 <= days <= 31:
        raise ValueError("days must be between 1 and 31")
    # Snapshot consistent across all day/model/bet/pending queries.
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    now = conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    if now is None or now.tzinfo is None:
        raise ValueError("database clock must be timezone aware")
    summaries = {x.decision_date:x for x in load_aligned_daily_model_pick_summaries(
        conn, timezone_name=LOCAL_TZ)}
    report = {
        "version":VERSION,
        "data_as_of_utc":now.astimezone(UTC).isoformat(),
        "original_prediction_day_basis":"FIRST_QUALIFIED_PIT_MATCH_START_ASIA_MANILA",
        "live_model_pick_population":"CANONICAL_DASHBOARD_ALIGNED_FIRST_QUALIFIED",
        "separate_value_bet_market_performance_not_conflated":True,
        "not_independent_official_fixture_coverage":True,
        "days":[],
    }
    all_rows = []
    for i in range(days):
        day = first_day + timedelta(days=i)
        picks = list(load_aligned_daily_model_picks(
            conn,decision_date=day, timezone_name=LOCAL_TZ))
        all_rows.extend(picks)
        overall = _overview_rows(picks)
        summary = summaries.get(day)
        if summary is None:
            if picks:
                raise ValueError("detailed picks contradict absent daily summary")
        elif (overall["issued"],overall["wins"],overall["losses"],
              overall["pending"],overall["void"]) != (
                summary.picks,summary.wins,summary.losses,summary.pending,
                summary.voids):
            raise ValueError("dashboard totals/details contradict, fail closed")
        by_type={}
        for typ in ("MANAGER_CONSENSUS","A1_A2_WIDE_SPREAD"):
            by_type[typ]=_overview_rows([r for r in picks if r.pick_type==typ])
        if sum(v["issued"] for v in by_type.values())!=len(picks):
            raise ValueError("unknown pick type")
        outcomes=[]
        for r in picks:
            outcomes.append({
                "match_id":r.match_id,
                "original_issued_at_utc":r.picked_at.astimezone(UTC).isoformat(),
                "original_match_start_utc":(
                    r.match_start_at.astimezone(UTC).isoformat()
                    if r.match_start_at else None),
                "latest_fixture_start_utc":(
                    r.latest_match_start_at.astimezone(UTC).isoformat()
                    if r.latest_match_start_at else None),
                "latest_fixture_status":r.latest_fixture_status,
                "original_pick_type":r.pick_type,
                "status":r.result_status,
                "is_market_value_bet":r.is_value_bet,
                "rescheduled":r.is_rescheduled,
                "pending_reason":pending_reason(r,now),
                "tournament":r.tournament,
                "selection":r.selection,
            })
        assert len(outcomes)==len({r["match_id"] for r in outcomes})
        report["days"].append({
            "date_manila":day.isoformat(),**overall,
            "by_pick_type":by_type,
            "market_value_bet_count":sum(r.is_value_bet for r in picks),
            "pending_reason_counts":dict(sorted(Counter(
                pending_reason(r,now) for r in picks if r.result_status=="PENDING"
            ).items())),
            "individual_original_fixtures":outcomes,
        })
    report["three_day_total"] = _overview_rows(all_rows)
    report["three_day_total"]["market_value_bet_count"]=sum(
        r.is_value_bet for r in all_rows)
    health = get_latest_live_operational_health(conn)
    report["last_persisted_operational_health"] = (
        {"status":health.status,"freshness":health.freshness_state,
         "checked_at_utc":health.checked_at.astimezone(UTC).isoformat(),
         "errors":health.error_count,"notes":health.notes}
        if health else None)
    missing = load_terminal_fixtures_without_result(conn,now=now)
    mids={r.match_id for r in all_rows}
    report["three_day_terminal_fixture_missing_result"]=[
        {"match_id":x.match_id,"fixture_status":x.match_status,
         "age_hours":x.age_hours}
        for x in missing.items if x.match_id in mids
    ]
    canonical=json.dumps(report,sort_keys=True,separators=(",",":"),default=str)
    return {**report,"record_sha256":sha256(canonical.encode()).hexdigest()}


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start-manila",required=True)
    p.add_argument("--days",type=int,default=3)
    args=p.parse_args(argv)
    dsn=os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL is required")
    with psycopg.connect(dsn,autocommit=False,connect_timeout=12) as conn:
        result=run(conn,first_day=date.fromisoformat(args.start_manila),days=args.days)
    print("TENNIS_LIVE_THREE_DAY_SCORECARD "+
          json.dumps(result,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
