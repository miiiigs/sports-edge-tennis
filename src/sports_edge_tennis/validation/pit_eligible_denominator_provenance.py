"""Read-only canonical PIT fixture coverage plus legacy historical cutoff audit.

Separate four populations:
1. latest stored calendar fixtures (hindsight as-of census);
2. fixtures whose scheduled start and player identity were actually visible
   before their match by a hard lead time;
3. originally targeted completed live production runs;
4. dashboard-aligned first-issued Model Pick read-model selections.

The PIT-visible fixture subset is NOT necessarily *all* eligible ATP/Challenger
fixtures: unobserved provider slates and provider failures are unknowable
without an independent fixture-source census. Closing odds or hindsight fixture
data never become eligible PIT facts.

No DB writes, prediction policy changes, external requests or subscriptions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import aligned_production_model_picks_cte

VERSION = "PIT_CANONICAL_FIXTURE_DENOMINATOR_AND_HISTORICAL_CUTOFF_AUDIT_V1"
PREMATCH_MINUTES = 30
TOURS = ("ATP_MAIN", "CHALLENGER")


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("must supply a timezone-aware datetime")
    return value.astimezone(UTC)


def _cutoffs(*, start: datetime, end: datetime, as_of: datetime) -> dict[str, datetime]:
    start, end, as_of = map(_utc, (start, end, as_of))
    if not (start < end <= as_of):
        raise ValueError("require start < end <= as-of")
    if (end-start).days > 120:
        raise ValueError("read-only fixture window limited to 120 days")
    return {"start": start, "end": end, "as_of": as_of}


def fixture_source_sql() -> str:
    """Use latest known snapshot only for hindsight window membership.

    PIT pre-issue eligibility comes independently from the preissue LATERAL,
    which requires all three timestamps (source, as_of and actual ingestion)
    before match_start_at - 30 minutes. An old rescheduled match may be in
    another month in the PIT version; such a mismatch is excluded explicitly.
    """
    return f"""
        WITH latest_visible AS (
          SELECT DISTINCT ON (v.match_id)
                 v.match_id,v.match_start_at,v.tour_scope::text AS tour,
                 v.qualifying_flag,v.match_status::text AS status,
                 v.source_observed_at,v.as_of,v.ingested_at,
                 v.match_version_id
          FROM match_versions v
          WHERE v.source_observed_at <= %(as_of)s
            AND v.as_of <= %(as_of)s
            AND v.ingested_at <= %(as_of)s
          ORDER BY v.match_id,v.as_of DESC,v.source_observed_at DESC,
                   v.ingested_at DESC,v.match_version_id DESC
        ),
        current_window AS (
          SELECT * FROM latest_visible
          WHERE match_start_at >= %(start)s
            AND match_start_at < %(end)s
            AND tour IN ('ATP_MAIN','CHALLENGER')
        ),
        first_target AS (
          SELECT DISTINCT ON(t.match_id)
                 t.match_id,pr.prediction_run_id,pr.prediction_finalized_at
          FROM prediction_run_targets t
          JOIN prediction_runs pr ON pr.prediction_run_id=t.prediction_run_id
          WHERE pr.run_status='COMPLETED'
            AND pr.experiment_id IS NULL
            AND pr.prediction_finalized_at IS NOT NULL
            AND pr.prediction_finalized_at <= %(as_of)s
          ORDER BY t.match_id,pr.prediction_finalized_at,pr.prediction_run_id
        ),
        {aligned_production_model_picks_cte()}
        SELECT c.match_id,c.match_start_at,c.tour,c.qualifying_flag,
               c.status,
               GREATEST(c.as_of,c.source_observed_at,c.ingested_at)
                 AS hindsight_version_known_at,
               p.match_start_at AS pit_start_at,p.tour AS pit_tour,
               p.qualifying_flag AS pit_qualifying,
               p.status AS pit_status,
               p.version_known_at AS pit_version_known_at,
               fr.prediction_run_id,fr.prediction_finalized_at,
               ps.agent1_predicted,ps.agent2_predicted,ps.agent3_predicted,
               ps.agent1_abstained,ps.agent2_abstained,ps.agent3_abstained,
               mc.manager_status,mc.reason_code,
               pick.picked_at,pick.aligned_pick_type,
               lr.outcome_status,lr.winner_side,lr.result_known_at,
               sources.system_names
        FROM current_window c
        LEFT JOIN LATERAL (
          SELECT v.match_start_at,v.tour_scope::text AS tour,
                 v.qualifying_flag,v.match_status::text AS status,
                 GREATEST(v.as_of,v.source_observed_at,v.ingested_at)
                   AS version_known_at
          FROM match_versions v
          WHERE v.match_id=c.match_id
            AND v.as_of <= %(as_of)s
            AND v.source_observed_at <= %(as_of)s
            AND v.ingested_at <= %(as_of)s
            AND v.as_of <= v.match_start_at-INTERVAL '30 minutes'
            AND v.source_observed_at <= v.match_start_at-INTERVAL '30 minutes'
            AND v.ingested_at <= v.match_start_at-INTERVAL '30 minutes'
          ORDER BY v.as_of DESC,v.source_observed_at DESC,v.ingested_at DESC,
                   v.match_version_id DESC LIMIT 1
        ) p ON TRUE
        LEFT JOIN first_target fr ON fr.match_id=c.match_id
        LEFT JOIN LATERAL (
          SELECT count(*) FILTER(WHERE agent_code='AGENT_1_SURFACE_ELO'
                                    AND prediction_status='PREDICTED') AS agent1_predicted,
                 count(*) FILTER(WHERE agent_code='AGENT_2_HIERARCHICAL_MARKOV'
                                    AND prediction_status='PREDICTED') AS agent2_predicted,
                 count(*) FILTER(WHERE agent_code='AGENT_3_INTRANSITIVITY'
                                    AND prediction_status='PREDICTED') AS agent3_predicted,
                 count(*) FILTER(WHERE agent_code='AGENT_1_SURFACE_ELO'
                                    AND prediction_status='ABSTAINED') AS agent1_abstained,
                 count(*) FILTER(WHERE agent_code='AGENT_2_HIERARCHICAL_MARKOV'
                                    AND prediction_status='ABSTAINED') AS agent2_abstained,
                 count(*) FILTER(WHERE agent_code='AGENT_3_INTRANSITIVITY'
                                    AND prediction_status='ABSTAINED') AS agent3_abstained
          FROM predictions pred
          WHERE pred.prediction_run_id=fr.prediction_run_id
            AND pred.match_id=c.match_id
        ) ps ON TRUE
        LEFT JOIN manager_consensus_results mc
          ON mc.prediction_run_id=fr.prediction_run_id
         AND mc.match_id=c.match_id
        LEFT JOIN aligned_model_picks pick
          ON pick.match_id=c.match_id AND pick.picked_at<=%(as_of)s
        LEFT JOIN LATERAL (
          SELECT outcome_status::text AS outcome_status,winner_side,
                 GREATEST(as_of,source_observed_at,ingested_at) AS result_known_at
          FROM match_result_observations r
          WHERE r.match_id=c.match_id
            AND r.source_observed_at<=%(as_of)s
            AND r.as_of<=%(as_of)s
            AND r.ingested_at<=%(as_of)s
          ORDER BY r.as_of DESC,r.source_observed_at DESC,
                   r.ingested_at DESC,r.match_result_observation_id DESC
          LIMIT 1
        ) lr ON TRUE
        LEFT JOIN LATERAL (
          SELECT string_agg(DISTINCT ms.source_system,',' ORDER BY ms.source_system)
                   AS system_names
          FROM match_source_ids ms WHERE ms.match_id=c.match_id
            AND ms.created_at<=%(as_of)s
        ) sources ON TRUE
        ORDER BY c.match_id
    """


def _classify(row: tuple, *, start: datetime, end: datetime,
              as_of: datetime) -> dict[str,Any]:
    (match_id,latest_start,tour,qualifying,current_status,hindsight_known,
     pit_start,pit_tour,pit_qualifying,pit_status,pit_known,
     run_id,run_at,a1,a2,a3,a1_abs,a2_abs,a3_abs,
     manager_status,manager_reason,pick_at,pick_kind,
     result_status,winner_side,result_known,systems) = row
    if latest_start.tzinfo is None or not start<=_utc(latest_start)<end:
        raise ValueError("latest fixture out of requested as-of window")
    if tour not in TOURS:
        raise ValueError("non ATP/Ch fixture passed SQL boundary")
    pit_visible = (
        pit_start is not None
        and start<=_utc(pit_start)<end
        and pit_tour==tour
        and pit_known is not None
        and _utc(pit_known)<=_utc(pit_start)-timedelta(minutes=PREMATCH_MINUTES)
    )
    pit_status_eligible = pit_visible and pit_status=="SCHEDULED"
    # Keep all qualifying matches as a distinct cohort and never imply that
    # they are excluded from the official fixture universe.
    base_eligible = pit_status_eligible and not pit_qualifying
    first_run_on_time = (
        pit_visible and run_at is not None
        and _utc(run_at)<=_utc(pit_start)-timedelta(minutes=PREMATCH_MINUTES)
        and _utc(run_at)<=as_of
    )
    pick_on_time = (
        pit_visible and pick_at is not None
        and _utc(pick_at)<=_utc(pit_start)-timedelta(minutes=PREMATCH_MINUTES)
        and _utc(pick_at)<=as_of
    )
    if not pit_visible:
        issue="NO_TRUSTED_IN_WINDOW_PIT_FIXTURE"
    elif pit_status!="SCHEDULED":
        issue="PIT_NOT_SCHEDULED"
    elif pit_qualifying:
        issue="QUALIFYING_REPORTED_SEPARATELY"
    elif run_at is None:
        issue="NO_COMPLETED_FIRST_PRODUCTION_RUN"
    elif not first_run_on_time:
        issue="FIRST_PRODUCTION_RUN_AFTER_PREMATCH_CUTOFF"
    elif not pick_on_time:
        issue="NO_ON_TIME_ALIGNED_MODEL_PICK"
    else:
        issue="ON_TIME_ALIGNED_MODEL_PICK"
    valid_result = (
        result_status=="COMPLETED"
        and winner_side in (1,2)
        and result_known is not None
        and _utc(result_known)>=_utc(latest_start)
        and _utc(result_known)<=as_of
    )
    return {
        "match_id":int(match_id),"tour":str(tour),"qualifying":bool(qualifying),
        "pit_visible":bool(pit_visible),
        "pit_qualifying":bool(pit_qualifying) if pit_visible else None,
        "pit_status_eligible":bool(pit_status_eligible),
        "main_draw_pit_proxy":bool(base_eligible),
        "first_run_targeted":run_id is not None,
        "first_run_on_time":bool(first_run_on_time),
        "first_run_id":int(run_id) if run_id is not None else None,
        "a1_predicted":int(a1 or 0)>0 if first_run_on_time else False,
        "a2_predicted":int(a2 or 0)>0 if first_run_on_time else False,
        "a3_predicted":int(a3 or 0)>0 if first_run_on_time else False,
        "a1_abstained":int(a1_abs or 0)>0 if first_run_on_time else False,
        "a2_abstained":int(a2_abs or 0)>0 if first_run_on_time else False,
        "a3_abstained":int(a3_abs or 0)>0 if first_run_on_time else False,
        "manager_status":str(manager_status) if first_run_on_time and manager_status else None,
        "manager_reason":str(manager_reason) if first_run_on_time and manager_reason else None,
        "on_time_aligned_pick":bool(pick_on_time),
        "aligned_kind":str(pick_kind) if pick_on_time else None,
        "result_status":str(result_status) if result_status is not None else "NO_RESULT",
        "valid_completed_result":bool(valid_result),
        "issue":issue,
        "source_systems":tuple(str(systems).split(",")) if systems else (),
    }


def summarize(rows: list[tuple], *, start: datetime, end: datetime,
              as_of: datetime) -> dict[str,Any]:
    cut=_cutoffs(start=start,end=end,as_of=as_of)
    start,end,as_of=cut["start"],cut["end"],cut["as_of"]
    fixtures=[]
    seen=set()
    for raw in rows:
        record=_classify(raw,start=start,end=end,as_of=as_of)
        if record["match_id"] in seen:
            raise ValueError("duplicate canonical match rows in coverage audit")
        seen.add(record["match_id"])
        fixtures.append(record)
    digest=hashlib.sha256()
    for f in sorted(fixtures,key=lambda x:x["match_id"]):
        digest.update(
            (f"{f['match_id']}|{f['tour']}|{f['qualifying']}|"
             f"{f['pit_visible']}|{f['pit_status_eligible']}|"
             f"{f['first_run_id']}|{f['first_run_on_time']}|"
             f"{f['on_time_aligned_pick']}|{f['issue']}|"
             f"{f['result_status']}\n").encode()
        )
    counters=Counter(f["issue"] for f in fixtures)
    by_tour={}
    for tour in ("ATP_MAIN","CHALLENGER"):
        group=[f for f in fixtures if f["tour"]==tour]
        denom=[f for f in group if f["main_draw_pit_proxy"]]
        issued=[f for f in denom if f["on_time_aligned_pick"]]
        targeted=[f for f in denom if f["first_run_on_time"]]
        a1a2=[f for f in denom if f["a1_predicted"] and f["a2_predicted"]]
        a3=[f for f in denom if f["a3_predicted"]]
        by_tour[tour]={
            "stored_hindsight_calendar_fixtures":len(group),
            "visible_at_least_30m_before_start":sum(f["pit_visible"] for f in group),
            "pit_scheduled_including_qualifying":sum(f["pit_status_eligible"] for f in group),
            "pit_scheduled_qualifying":sum(
                f["pit_status_eligible"] and f["pit_qualifying"] for f in group
            ),
            "main_draw_pit_visible_denominator_proxy":len(denom),
            "first_production_completed_run_on_time":len(targeted),
            "on_time_agent1_agent2_original_predictions":len(a1a2),
            "on_time_agent3_predicted":len(a3),
            "on_time_aligned_model_picks":len(issued),
            "target_coverage_of_pit_proxy":len(targeted)/len(denom) if denom else None,
            "aligned_pick_coverage_of_pit_proxy":len(issued)/len(denom) if denom else None,
            "on_time_aligned_picks_completed_results":sum(f["valid_completed_result"] for f in issued),
            "on_time_aligned_picks_ungraded":sum(not f["valid_completed_result"] for f in issued),
            "manager_consensus_on_first_run":sum(f["manager_status"]=="CONSENSUS" for f in denom),
            "manager_first_run_reasons":dict(sorted(Counter(
                str(f["manager_reason"] or f["manager_status"] or "NO_MANAGER_ROW")
                for f in denom if f["first_run_on_time"]
            ).items())),
            "issue_counts":dict(sorted(Counter(f["issue"] for f in group).items())),
        }
    source=Counter()
    for f in fixtures:
        for system in f["source_systems"]:
            source[system]+=1
    return {
        "version":VERSION,"status":"READ_ONLY_PIT_FIXTURE_CENSUS",
        "window_start_utc":start.isoformat(),"window_end_utc":end.isoformat(),
        "report_as_of_utc":as_of.isoformat(),
        "dataset_sha256":digest.hexdigest(),
        "db_writes":False,"production_policy_changed":False,
        "stored_canonical_fixtures":len(fixtures),
        "pit_visible_at_least_30m":sum(f["pit_visible"] for f in fixtures),
        "main_draw_pit_proxy":sum(f["main_draw_pit_proxy"] for f in fixtures),
        "pit_proxy_coverage_not_all_official_eligible_matches":True,
        "cannot_establish_unobserved_provider_fixtures":True,
        "qualifying_separate_not_silently_excluded":True,
        "on_time_first_run_targeted":sum(f["first_run_on_time"] for f in fixtures),
        "on_time_dashboard_aligned_model_picks":sum(f["on_time_aligned_pick"] for f in fixtures),
        "source_systems_stored_hindsight_only":dict(sorted(source.items())),
        "issue_counts":dict(sorted(counters.items())),
        "by_tour":by_tour,
        "first_run_only_not_all_run_results":True,
        "publication":("aligned Model Picks presentation/read model, not a proof of "
                       "external alert delivery or independent market qualification"),
        "limitations":[
            "No provider-independent fixture slate means this is PIT-visible proxy coverage, not all official eligible fixtures.",
            "Calendar window derives from latest as-of stored match version; reschedule migrations are excluded from PIT window when start differs.",
            "Only main-draw scheduled fixtures enter main proxy; qualifying/PIT postponed fixtures separately counted.",
            "First completed run is not a replacement for a complete per-run funnel or Manager A-E backtest.",
            "Stored sources are provenance labels, not proven commercially licensed data.",
        ],
    }


def legacy_historical_sql() -> str:
    """Census pre-match *reconstructibility* of the old walkforward label.

    Legacy runner predicts at max(result.as_of, result.source_observed_at)
    before updating the *same outcome*. That is not the pre-match cutoff, even
    when its own match winner is held out. Count outcome visibility times
    relative to canonical stored match start. This SQL does not estimate the
    historical model's own accuracy or prove every feature leaked.
    """
    return """
        WITH results AS (
          SELECT DISTINCT ON(r.match_id)
            r.match_id,r.outcome_status::text AS status,r.winner_side,
            r.as_of,r.source_observed_at,
            GREATEST(r.as_of,r.source_observed_at) AS legacy_boundary
          FROM match_result_observations r
          WHERE r.as_of<=%(as_of)s
            AND r.source_observed_at<=%(as_of)s
            AND r.ingested_at<=%(as_of)s
          ORDER BY r.match_id,r.as_of DESC,r.source_observed_at DESC,
                   r.match_result_observation_id DESC
        )
        SELECT te.season_year,rs.match_id,rs.status,rs.winner_side,
               rs.legacy_boundary,mv.match_start_at,
               mv.player_1_id,mv.player_2_id,
               mv.source_observed_at AS fixture_source_time,
               mv.as_of AS fixture_as_of,
               mv.ingested_at AS fixture_ingested
        FROM results rs
        JOIN matches m ON m.match_id=rs.match_id
        JOIN tournament_editions te ON te.tournament_edition_id=m.tournament_edition_id
        LEFT JOIN LATERAL (
          SELECT player_1_id,player_2_id,match_start_at,
                 source_observed_at,as_of,ingested_at
          FROM match_versions v
          WHERE v.match_id=rs.match_id
            AND v.as_of<=rs.as_of
            AND v.source_observed_at<=rs.as_of
          ORDER BY v.as_of DESC,v.source_observed_at DESC,v.match_version_id DESC
          LIMIT 1
        ) mv ON TRUE
        WHERE te.season_year BETWEEN %(year_start)s AND %(year_end)s
        ORDER BY te.season_year,rs.match_id
    """


def summarize_legacy(rows:list[tuple], *, year_start:int=2016,
                     year_end:int=2025)->dict[str,Any]:
    counters=defaultdict(Counter)
    seen=set()
    digest=hashlib.sha256()
    for year,match_id,status,winner,boundary,start,p1,p2,source,version_asof,ingested in rows:
        match_id=int(match_id)
        if match_id in seen:
            raise ValueError("duplicate historical match from latest-result query")
        seen.add(match_id)
        if not year_start<=int(year)<=year_end:
            raise ValueError("historical year outside requested window")
        c=counters[str(year)]
        c["stored_result_matches"]+=1
        c[f"status_{status}"]+=1
        if winner==1:
            c["winner_player_1_stored"]+=1
        elif winner==2:
            c["winner_player_2_stored"]+=1
        if start is None or boundary is None:
            c["missing_original_match_start_or_result_visibility"]+=1
            label="UNKNOWN"
        elif _utc(boundary)>_utc(start)-timedelta(minutes=PREMATCH_MINUTES):
            c["legacy_prediction_boundary_after_preissue_cutoff"]+=1
            if _utc(boundary)>=_utc(start):
                c["legacy_prediction_boundary_at_or_after_start"]+=1
            label="NOT_PREMATCH"
        else:
            c["legacy_boundary_preissue"]=c["legacy_boundary_preissue"]+1
            label="PREISSUE"
        if source is not None and start is not None and (
            _utc(source)>_utc(start)-timedelta(minutes=PREMATCH_MINUTES)
            or _utc(version_asof)>_utc(start)-timedelta(minutes=PREMATCH_MINUTES)
            or _utc(ingested)>_utc(start)-timedelta(minutes=PREMATCH_MINUTES)
        ):
            c["latest_legacy_joined_fixture_version_not_pit"]+=1
        digest.update(f"{year}|{match_id}|{status}|{winner}|{label}\n".encode())
    by_year={k:dict(sorted(v.items())) for k,v in sorted(counters.items())}
    totals=Counter()
    for counter in counters.values():
        totals.update(counter)
    return {
        "status":"LEGACY_RESULT_VISIBILITY_IS_NOT_PREMATCH_EVIDENCE",
        "legacy_runner":"src/sports_edge_tennis/validation/agent1_walk_forward.py",
        "runner_prediction_boundary":"observation.visibility_at = max(outcome as_of, outcome source_observed_at)",
        "runner_label_handling":"predict before applying same visibility-cohort outcome; this does not prove the rating state is as of match start",
        "historical_years":[year_start,year_end],
        "dataset_sha256":digest.hexdigest(),
        "totals":dict(sorted(totals.items())),"per_year":by_year,
        "db_writes":False,
        "cannot_validate_claimed_14000_at_75pct":True,
        "interpretation":(
            "This is a point-in-time *methodology and provenance audit*, NOT "
            "a recalculation of a claimed 14k/75% win rate. Result visibility "
            "time must not be confused with a real pre-match forecast cutoff. "
            "Winner/loser ordering remains a separate potential leakage "
            "vector. Historical evaluation must be reissued chronologically "
            "with feature snapshots visible before the match and license "
            "permissions checked."
        ),
    }


def run(conn:psycopg.Connection,*,start:datetime,end:datetime,
        as_of:datetime,include_legacy:bool=True)->dict[str,Any]:
    cut=_cutoffs(start=start,end=end,as_of=as_of)
    conn.execute("SET default_transaction_read_only=on")
    out=summarize(conn.execute(fixture_source_sql(),cut).fetchall(),**cut)
    if include_legacy:
        hist=conn.execute(legacy_historical_sql(),{
            "as_of":cut["as_of"],"year_start":2016,"year_end":2025,
        }).fetchall()
        out["legacy_historical_provenance"]=summarize_legacy(hist)
    else:
        out["legacy_historical_provenance"]={"status":"NOT_EXECUTED"}
    return out


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window-start-utc",required=True)
    p.add_argument("--window-end-utc",required=True)
    p.add_argument("--as-of-utc",required=True)
    p.add_argument("--skip-legacy",action="store_true")
    args=p.parse_args(argv)
    if any(not x.endswith("Z") for x in (
        args.window_start_utc,args.window_end_utc,args.as_of_utc
    )):
        p.error("all timestamp flags must be UTC and end with Z")
    dsn=os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL is required")
    times=[datetime.fromisoformat(t.replace("Z","+00:00")) for t in
           (args.window_start_utc,args.window_end_utc,args.as_of_utc)]
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        out=run(conn,start=times[0],end=times[1],as_of=times[2],
                include_legacy=not args.skip_legacy)
    print("TENNIS_PIT_ELIGIBLE_DENOMINATOR "+json.dumps(out,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
