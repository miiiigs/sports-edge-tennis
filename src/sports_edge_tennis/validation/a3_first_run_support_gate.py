"""Original-issue Agent 3 graph support: evidence, not hypothetical predictions.

Use the first completed original run of each canonical match, its original A3
abstention and the contemporaneous append-only support observation. Measure
whether graph evidence is absent vs filtered. Never infer an A3 forecast from
a support diagnostic or retrospectively change an actual Model Pick.
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

from sports_edge_tennis.agents.agent3_intransitivity import DEFAULT_CONFIG

VERSION="A3_FIRST_RUN_PIT_SUPPORT_GATE_DIAGNOSTICS_V1"
PREMATCH_MINUTES=30


def query()->str:
    return """
        WITH first_run AS (
          SELECT DISTINCT ON(t.match_id)
              t.match_id, pr.prediction_run_id,pr.as_of AS run_as_of,
              pr.prediction_finalized_at AS issued_at
          FROM prediction_run_targets t
          JOIN prediction_runs pr ON pr.prediction_run_id=t.prediction_run_id
          WHERE pr.run_status='COMPLETED'
            AND pr.prediction_finalized_at IS NOT NULL
            AND pr.prediction_finalized_at<=%(as_of)s
            AND pr.experiment_id IS NULL
            AND pr.prediction_contract_version='predictive_agent_result_v1'
          ORDER BY t.match_id,pr.prediction_finalized_at,pr.prediction_run_id
        )
        SELECT fr.match_id,fr.prediction_run_id,fr.run_as_of,fr.issued_at,
               v.match_start_at,v.tour_scope::text AS tour,
               v.surface::text AS surface,
               p.prediction_status::text AS agent_status,p.abstain_reason_code,
               d.evaluated_at,d.identity_status,d.direct_raw_support,
               d.direct_effective_support,d.raw_shared_opponents,
               d.eligible_shared_opponents,d.q_count,d.support_path,
               d.gate_failed,d.final_status,
               d.graph_degree_player1,d.graph_degree_player2,
               d.history_count_player1,d.history_count_player2,
               d.created_at
        FROM first_run fr
        LEFT JOIN LATERAL (
            SELECT match_start_at,tour_scope,surface
            FROM match_versions mv
            WHERE mv.match_id=fr.match_id
              AND mv.as_of<=fr.issued_at
              AND mv.source_observed_at<=fr.issued_at
              AND mv.ingested_at<=fr.issued_at
            ORDER BY mv.as_of DESC,mv.source_observed_at DESC,
                     mv.match_version_id DESC LIMIT 1
        ) v ON TRUE
        LEFT JOIN predictions p
          ON p.prediction_run_id=fr.prediction_run_id
         AND p.match_id=fr.match_id
         AND p.agent_code='AGENT_3_INTRANSITIVITY'
         AND p.experiment_id IS NULL
        LEFT JOIN LATERAL (
          SELECT evaluated_at,identity_status,direct_raw_support,
                 direct_effective_support,raw_shared_opponents,
                 eligible_shared_opponents,q_count,support_path,
                 gate_failed,final_status,graph_degree_player1,
                 graph_degree_player2,history_count_player1,
                 history_count_player2,created_at
          FROM a3_support_diagnostics d
          WHERE d.match_id=fr.match_id
            AND d.evaluated_at>=fr.run_as_of
            AND d.evaluated_at<=fr.issued_at
            AND d.created_at<=%(as_of)s
          ORDER BY d.evaluated_at DESC,d.a3_support_diagnostic_id DESC
          LIMIT 1
        ) d ON TRUE
        WHERE v.match_start_at >= %(start)s
          AND v.match_start_at < %(end)s
        ORDER BY fr.match_id
    """


def _as_utc(t:datetime)->datetime:
    if t is None or t.tzinfo is None:
        raise ValueError("required timezone aware timestamp")
    return t.astimezone(UTC)


def _support_stratum(raw_direct:int,eff_direct:float,
                     raw_shared:int,eligible_shared:int,q:float)->str:
    """Telemetry classifications only; not validated readiness to predict."""
    if raw_direct==0 and raw_shared==0:
        return "NO_DIRECT_OR_SHARED_OPPONENT_HISTORY"
    if raw_direct>=2 and eff_direct<2.5:
        return "DIRECT_MATCHES_BUT_LOW_EFFECTIVE_SAMPLE"
    if raw_direct>=2 and eff_direct>=2.5:
        return "DIRECT_THRESHOLD_POSSIBLE_REQUIRES_SIGNAL_CHECK"
    if raw_shared>0 and eligible_shared==0:
        return "RAW_COMMON_OPPONENTS_ALL_FILTERED_OUT"
    if eligible_shared>0 and eligible_shared<5:
        return "COMMON_OPPONENTS_BELOW_MINIMUM_FIVE"
    if eligible_shared>=5 and q<8:
        return "FIVE_COMMON_OPPONENTS_BUT_Q_BELOW_EIGHT"
    if eligible_shared>=5 and q>=8:
        return "NETWORK_THRESHOLD_POSSIBLE_REQUIRES_SIGNAL_CHECK"
    return "OTHER_INSUFFICIENT_OR_UNCLASSIFIED"


def summarize(rows:list[tuple],*,start:datetime,end:datetime,
              as_of:datetime)->dict[str,Any]:
    start,end,as_of=map(_as_utc,(start,end,as_of))
    if not start<end<=as_of or (end-start).days>120:
        raise ValueError("invalid audited window")
    prior=set()
    overall=Counter()
    by_tour=defaultdict(Counter)
    gaps=Counter()
    classifications=Counter()
    identity=Counter()
    digest=hashlib.sha256()
    samples=[]
    for row in rows:
        (mid,run_id,run_asof,issued,match_start,tour,surface,
         a3_status,abstain_reason,diag_at,identity_status,d_raw,d_eff,
         shared_raw,shared_ok,q_count,support_path,diag_failure,
         diag_status,deg1,deg2,hist1,hist2,diag_created)=row
        mid=int(mid)
        if mid in prior:
            raise ValueError("duplicate first-run fixture")
        prior.add(mid)
        if (match_start is None or issued is None or
            tour not in ("ATP_MAIN","CHALLENGER") or
            _as_utc(issued)>_as_utc(match_start)-timedelta(minutes=PREMATCH_MINUTES)):
            overall["unsafe_first_run_excluded"]+=1
            continue
        if not start<=_as_utc(match_start)<end or _as_utc(issued)>as_of:
            raise ValueError("fixture outside report cutoff")
        overall["valid_first_run_targets"]+=1
        c=by_tour[tour]
        c["valid_first_run_targets"]+=1
        status=str(a3_status or "MISSING_AGENT_ROW")
        c["A3_"+status]+=1
        overall["A3_"+status]+=1
        reason=str(abstain_reason or "NONE")
        c["A3_reason_"+reason]+=1
        overall["A3_reason_"+reason]+=1
        if diag_at is None:
            c["NO_SAME_RUN_DIAGNOSTIC"]+=1
            overall["NO_SAME_RUN_DIAGNOSTIC"]+=1
            digest.update(f"{mid}|{run_id}|{status}|NO_DIAGNOSTIC\n".encode())
            continue
        if (run_asof is None or diag_created is None or
            _as_utc(diag_at)<_as_utc(run_asof) or
            _as_utc(diag_at)>_as_utc(issued) or
            _as_utc(diag_created)>as_of):
            raise ValueError("future or ambiguous diagnostic")
        if diag_status!=status:
            # A single match can run repeated diagnostic observations within
            # one run. Ignore a mismatched record rather than infer support.
            gaps["A3_DIAGNOSTIC_STATUS_MISMATCH"]+=1
            c["STATUS_MISMATCH_CANNOT_USE"]+=1
            continue
        c["MATCHED_DIAGNOSTIC"]+=1
        overall["MATCHED_DIAGNOSTIC"]+=1
        raw=int(d_raw or 0)
        eff=float(d_eff or 0)
        shared=int(shared_raw or 0)
        qualifying=int(shared_ok or 0)
        q=float(q_count or 0)
        if min(raw,eff,shared,qualifying,q)<0 or qualifying>shared:
            raise ValueError("impossible graph support diagnostic")
        classification=_support_stratum(raw,eff,shared,qualifying,q)
        classifications[classification]+=1
        c[classification]+=1
        identity[str(identity_status or "UNKNOWN")]+=1
        if status=="ABSTAINED":
            if reason in ("UNKNOWN_SURFACE","UNKNOWN_SURFACE_INFORMATION") or surface is None:
                gaps["UNKNOWN_SURFACE_AS_OF_ISSUE"]+=1
            if shared>0 and qualifying<shared:
                gaps["SOME_RAW_SHARED_OPPONENTS_EXCLUDED_BY_EDGE_OR_DECAY"]+=1
            if eff<2.5 and raw>=2:
                gaps["RAW_DIRECT_EVIDENCE_BUT_INSUFFICIENT_DECAYED_EFFECTIVE"]+=1
            if qualifying>=5 and q<8:
                gaps["QUALIFIED_NETWORK_COUNT_BUT_LOW_Q"]+=1
        if len(samples)<15:
            samples.append({
                "match_id":mid,"tour":tour,"a3_status":status,
                "reason":reason,"support_stratum":classification,
                "raw_shared":shared,"eligible_shared":qualifying,
                "raw_direct":raw,"effective_direct":eff,
            })
        digest.update((
            f"{mid}|{run_id}|{status}|{reason}|{classification}|"
            f"{raw}|{eff}|{shared}|{qualifying}|{q}|{diag_at.isoformat()}\n"
        ).encode())
    return {
        "version":VERSION,"dataset_sha256":digest.hexdigest(),
        "window_start_utc":start.isoformat(),"window_end_utc":end.isoformat(),
        "report_as_of_utc":as_of.isoformat(),
        "overall":dict(sorted(overall.items())),
        "tour":{k:dict(sorted(v.items())) for k,v in sorted(by_tour.items())},
        "matched_diagnostic_classification":dict(sorted(classifications.items())),
        "matched_diagnostic_identity":dict(sorted(identity.items())),
        "support_gap_counters":dict(sorted(gaps.items())),
        "sample_first_15_without_player_pii":samples,
        "production_a3_lookback_days":DEFAULT_CONFIG.lookback_days,
        "production_a3_half_life_days":DEFAULT_CONFIG.half_life_days,
        "production_network_edge_raw_threshold":DEFAULT_CONFIG.network_edge_raw_support,
        "production_network_edge_effective_threshold":
            str(DEFAULT_CONFIG.network_edge_effective_support),
        "selection_denom":"ORIGINAL_ON_TIME_FIRST_RUN_TARGETS_NOT_FULL_ELIGIBLE_SLATE",
        "no_new_predictions":True,"no_data_writes":True,
        "not_fitted_or_out_of_sample_experiment":True,
        "notes":[
            "Only A3 diagnostics recorded during the very first completed nonexperimental original issue run are attributable; later evaluations are NOT backfilled.",
            "Raw common-opponent edges are not eligible edges and do not certify that an alternative Agent3 model would issue a correct forecast.",
            "An earlier match has decayed temporal weight <1 for a single history event; current per-edge effective >=1 prevents that single event from qualifying by itself.",
            "Unknown surfaces require independently time-valid official metadata, never a guess.",
            "Missing original diagnostics are kept explicit and cannot support any claim of sample-wide mechanism prevalence.",
            "No model threshold changed, no revised probability generated or published, and no commercial research dataset imported.",
        ],
    }


def run(conn:psycopg.Connection, *,start:datetime,
        end:datetime,as_of:datetime)->dict[str,Any]:
    conn.execute("SET default_transaction_read_only=on")
    rows=conn.execute(query(),{"start":_as_utc(start),
        "end":_as_utc(end),"as_of":_as_utc(as_of)}).fetchall()
    return summarize(rows,start=start,end=end,as_of=as_of)


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    for field in ("start","end","as_of"):
        p.add_argument("--"+field.replace("_","-")+"-utc",required=True)
    args=p.parse_args(argv)
    stamps=[getattr(args,k+"_utc") for k in ("start","end","as_of")]
    if any(not x.endswith("Z") for x in stamps):
        p.error("timestamps must be UTC ISO-8601 Z")
    dsn=os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL required")
    start,end,as_of=[
        datetime.fromisoformat(x.replace("Z","+00:00")) for x in stamps
    ]
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        out=run(conn,start=start,end=end,as_of=as_of)
    print("TENNIS_A3_FIRST_RUN_SUPPORT_GATE "+json.dumps(out,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
