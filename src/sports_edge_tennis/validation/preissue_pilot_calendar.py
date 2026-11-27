"""Offline scheduling of pre-issue dual-provider collection windows.

A plan is not a request, a reservation, or evidence of market availability.
The live cron and the consensus-only spending rule are not changed.
"""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sports_edge_tennis.validation.api_tennis_acquisition_plan import (
    FixtureCandidate, plan_simultaneous_acquisition,
)


@dataclass(frozen=True)
class UpcomingFixture:
    fixture: FixtureCandidate
    discovered_at: datetime
    planned_issue_at: datetime
    oddspapi_tournament_id: int | None


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(UTC)


def plan_preissue_pilot(*, fixtures, now, scheduler_slots, entitlements=(),
                        authorization_reference=None, oddspapi_remaining=None,
                        api_tennis_remaining=None, completion_buffer=timedelta(minutes=5),
                        oddspapi_per_cycle=5, api_tennis_per_cycle=5):
    """Use actual configured future slots, never hypothetical past captures.

    Only independently mapped dual-provider fixtures are planned here.
    Each fixture is assigned at most one first-attempt collection slot.
    A slot must finish before BOTH planned issue and T-60. Missing fixture
    identity, budget, approval and deadline failures stay in the denominator.
    Existing planner performs provider-specific request batching and approval
    gates. Worst-case retry allowance and atomic reservation must still be
    enforced by the eventual transport integration.
    """
    now = _utc(now)
    if completion_buffer <= timedelta(0):
        raise ValueError("positive completion buffer required")
    if type(oddspapi_per_cycle) is not int or oddspapi_per_cycle <= 0:
        raise ValueError("positive OddsPapi cycle cap required")
    if type(api_tennis_per_cycle) is not int or api_tennis_per_cycle <= 0:
        raise ValueError("positive API-Tennis cycle cap required")
    fixtures = list(fixtures)
    if len({r.fixture.match_id for r in fixtures}) != len(fixtures):
        raise ValueError("duplicate canonical fixture")
    slots = sorted({_utc(t) for t in scheduler_slots if _utc(t) >= now})
    work = []
    results = {}
    for item in fixtures:
        f = item.fixture
        discovered = _utc(item.discovered_at)
        issue = _utc(item.planned_issue_at)
        start = _utc(f.scheduled_start_at)
        deadline = min(issue, start-timedelta(hours=1))
        record = {"match_id":f.match_id, "tour":f.tour,
                  "deadline":deadline.isoformat(), "slot":None}
        results[f.match_id] = record
        if discovered > now:
            record["status"] = "DISCOVERY_NOT_YET_OBSERVED"
        elif not (f.mapped and f.api_tennis_match_key and f.oddspapi_fixture_id
                  and type(item.oddspapi_tournament_id) is int
                  and item.oddspapi_tournament_id > 0):
            record["status"] = "DUAL_MAPPING_UNAVAILABLE"
        elif now+completion_buffer > deadline:
            record["status"] = "DEADLINE_MISSED"
        else:
            work.append((deadline, f.match_id, item))

    work.sort(key=lambda row:(row[0],row[1]))
    cycles = []
    remaining = {"ODDSPAPI":oddspapi_remaining,"API_TENNIS":api_tennis_remaining}
    for slot in slots:
        # No assumption that prices exist just because a request is scheduled.
        ready = [r for r in work if "status" not in results[r[1]]
                 and slot+completion_buffer <= r[0]]
        if not ready:
            continue
        chosen = []
        current_plan = None
        for row in ready:
            attempted = chosen+[row]
            members = [x[2] for x in attempted]
            # Date-range API-Tennis request covers only the chosen fixture dates.
            start_day = min(_utc(x.fixture.scheduled_start_at) for x in members)
            end_day = max(_utc(x.fixture.scheduled_start_at) for x in members)
            window_start = start_day.replace(hour=0,minute=0,second=0,microsecond=0)
            window_end = end_day.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
            if window_end-window_start > timedelta(days=10):
                continue
            plan = plan_simultaneous_acquisition(
                fixtures=[x.fixture for x in members],
                oddspapi_tournament_ids=[x.oddspapi_tournament_id for x in members],
                oddspapi_bookmakers=("pinnacle+30",),
                coalesce_discovery=False,
                api_tennis_date_start=window_start,api_tennis_date_stop=window_end,
                entitlements=entitlements,
                oddspapi_monthly_remaining=remaining["ODDSPAPI"],
                api_tennis_remaining=remaining["API_TENNIS"],
                authorization_reference=authorization_reference)
            if (plan["by_provider"].get("ODDSPAPI",0)>oddspapi_per_cycle
                    or plan["by_provider"].get("API_TENNIS",0)>api_tennis_per_cycle):
                continue
            if not plan["execution_authorised"]:
                results[row[1]]["status"] = "AUTHORIZATION_OR_ALLOWANCE_BLOCKED"
                continue
            chosen = attempted
            current_plan = plan
        if chosen:
            for provider,count in current_plan["by_provider"].items():
                remaining[provider] -= count
            ids = [row[1] for row in chosen]
            cycles.append({"slot":slot.isoformat(),"match_ids":ids,
                           "planned_requests":current_plan["by_provider"],
                           "request_plan":current_plan["requests"]})
            for mid in ids:
                results[mid].update(status="PLANNED_NOT_EXECUTED",slot=slot.isoformat())
    for record in results.values():
        record.setdefault("status","NO_FEASIBLE_SLOT_OR_CYCLE_CAP")
    return {"fixtures":len(fixtures),"planned_fixtures":sum(
                r["status"]=="PLANNED_NOT_EXECUTED" for r in results.values()),
            "cycles":cycles,"fixture_results":[results[k] for k in sorted(results)],
            "remaining_estimated_allowance":remaining,
            "provider_calls":0,"original_time_prices":0,
            "actual_request_reservations":0,"can_promote":False}
