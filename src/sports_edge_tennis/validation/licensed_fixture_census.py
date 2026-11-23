"""Authorised independent ATP/Challenger fixture census, no scraping.

Consumes a *separately licensed and provably complete* fixture CSV.
Original source publication time and fixed source fixture identity are required.
Cross-provider mismatches are UNKNOWN rather than falsely labelled missed.
Does not contact ATP/TDI/ProTennisLive, import data, update picks or alter
the live selection population. No complete official census exists here until
a licensed reference export and verified source completeness are supplied.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC,datetime,timedelta
from pathlib import Path
from typing import Any

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import (
    aligned_production_model_picks_cte,
)

VERSION="LICENSED_REFERENCE_FIXTURE_CENSUS_V1"
REQUIRED_CSV_COLUMNS=(
    "source_match_id","tour_scope","draw_stage","match_type",
    "scheduled_start_utc","first_published_utc",
)
PREMATCH_MINUTES=30


def _utc(value:datetime|None, name:str)->datetime:
    if value is None or value.tzinfo is None:
        raise ValueError(name+" requires explicit timezone")
    return value.astimezone(UTC)


def _parse(s:str, name:str)->datetime:
    if not s or not s.endswith("Z"):
        raise ValueError(name+" must be an explicit UTC ISO-8601 Z timestamp")
    return _utc(datetime.fromisoformat(s.replace("Z","+00:00")),name)


@dataclass(frozen=True)
class Manifest:
    source_system:str
    export_version:str
    licence_reference:str
    owner_approved_commercial_validation:bool
    provider_declares_complete_window:bool
    requested_window_start:datetime
    requested_window_end:datetime
    report_as_of:datetime
    file_sha256:str

    def validate(self)->None:
        if not self.source_system or not self.export_version or not self.licence_reference:
            raise ValueError("source, export version and licence reference required")
        if not self.owner_approved_commercial_validation:
            raise ValueError("commercial data rights not approved; no import allowed")
        start,end,asof=map(lambda q:_utc(q[1],q[0]),[
            ("requested_window_start",self.requested_window_start),
            ("requested_window_end",self.requested_window_end),
            ("report_as_of",self.report_as_of),
        ])
        if not start<end<=asof or (end-start).days>62:
            raise ValueError("invalid source census window")
        if len(self.file_sha256)!=64:
            raise ValueError("CSV source SHA256 required")


@dataclass(frozen=True)
class Reference:
    source_match_id:str
    tour_scope:str
    draw_stage:str
    match_type:str
    scheduled_start:datetime
    first_published:datetime|None

    def validate(self)->None:
        if not self.source_match_id.strip():
            raise ValueError("empty official fixture ID")
        if self.tour_scope not in ("ATP_MAIN","CHALLENGER"):
            raise ValueError("out-of-scope tour")
        if self.draw_stage not in ("MAIN","QUALIFYING"):
            raise ValueError("unsupported draw stage")
        if self.match_type!="SINGLES":
            raise ValueError("unsupported match type")
        start=_utc(self.scheduled_start,"scheduled_start")
        if self.first_published:
            published=_utc(self.first_published,"first_published")
            if published>start:
                raise ValueError("first official publication after scheduled start; audit source")
        if start.year<2000 or start.year>2100:
            raise ValueError("implausible scheduled year")


def load_authorised_csv(csv_path:Path,manifest_path:Path
                        )->tuple[Manifest,list[Reference]]:
    raw=csv_path.read_bytes()
    meta=json.loads(manifest_path.read_text())
    expected_hash=meta.get("file_sha256")
    actual_hash=hashlib.sha256(raw).hexdigest()
    if actual_hash!=expected_hash:
        raise ValueError("authorised export SHA256 mismatch")
    manifest=Manifest(
        source_system=meta["source_system"],
        export_version=meta["export_version"],
        licence_reference=meta["licence_reference"],
        owner_approved_commercial_validation=meta["owner_approved_commercial_validation"],
        provider_declares_complete_window=meta["provider_declares_complete_window"],
        requested_window_start=_parse(meta["requested_window_start_utc"],"window_start"),
        requested_window_end=_parse(meta["requested_window_end_utc"],"window_end"),
        report_as_of=_parse(meta["report_as_of_utc"],"as_of"),
        file_sha256=actual_hash,
    )
    manifest.validate()
    rows=list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    if not rows:
        raise ValueError("empty reference: cannot infer official coverage")
    columns=set(rows[0])
    if not set(REQUIRED_CSV_COLUMNS).issubset(columns):
        raise ValueError("missing required source fixture fields")
    seen=set()
    refs=[]
    for row in rows:
        fid=row["source_match_id"].strip()
        if fid in seen:
            raise ValueError("duplicate official fixture identity")
        seen.add(fid)
        ref=Reference(
            source_match_id=fid,
            tour_scope=row["tour_scope"].strip(),
            draw_stage=row["draw_stage"].strip(),
            match_type=row["match_type"].strip(),
            scheduled_start=_parse(row["scheduled_start_utc"],"scheduled_start"),
            first_published=(
                _parse(row["first_published_utc"],"first_published")
                if row["first_published_utc"].strip() else None
            ),
        )
        ref.validate()
        if not manifest.requested_window_start<=ref.scheduled_start<manifest.requested_window_end:
            raise ValueError("out-of-window fixture record")
        refs.append(ref)
    return manifest,refs


def source_matching(conn:psycopg.Connection, *,manifest:Manifest,
                    ref:Reference)->dict[str,Any]:
    """Query strictly exact source ID; never fuzzy-map unknown players."""
    rows=conn.execute(
        """
        SELECT DISTINCT match_id
        FROM match_source_ids
        WHERE source_system=%s
          AND source_match_id=%s
          AND created_at<=%s
        ORDER BY match_id
        """,
        (manifest.source_system,ref.source_match_id,manifest.report_as_of),
    ).fetchall()
    if not rows:
        return {"identity":"NO_EXACT_PROVIDER_MAPPING"}
    if len(rows)!=1:
        return {"identity":"AMBIGUOUS_MULTIPLE_CANONICAL_MAPPINGS"}
    mid=int(rows[0][0])
    cutoff=ref.scheduled_start-timedelta(minutes=PREMATCH_MINUTES)
    v=conn.execute(
        """
        SELECT match_version_id, match_start_at,tour_scope::text,
               match_status::text
        FROM match_versions
        WHERE match_id=%s AND as_of<=%s
          AND source_observed_at<=%s AND ingested_at<=%s
        ORDER BY as_of DESC,source_observed_at DESC,match_version_id DESC
        LIMIT 1
        """,
        (mid,cutoff,cutoff,cutoff),
    ).fetchone()
    if v is None:
        return {"identity":"EXACT_CANONICAL",
                "canonical_match_id":mid,
                "internal_discovered_prematch":False,
                "reason":"NO_PIT_MATCH_VERSION_AT_ORIGINAL_30M_DEADLINE"}
    if str(v[2])!=ref.tour_scope:
        return {"identity":"EXACT_ID_BUT_TOUR_MISMATCH",
                "canonical_match_id":mid}
    if abs((_utc(v[1],"internal_scheduled_start")-ref.scheduled_start).total_seconds())>86400:
        return {"identity":"EXACT_ID_BUT_SCHEDULE_MISMATCH",
                "canonical_match_id":mid}
    run=conn.execute(
        """
        SELECT pr.prediction_run_id,pr.prediction_finalized_at
        FROM prediction_run_targets t
        JOIN prediction_runs pr ON pr.prediction_run_id=t.prediction_run_id
        WHERE t.match_id=%s AND pr.run_status='COMPLETED'
          AND pr.experiment_id IS NULL
          AND pr.prediction_contract_version='predictive_agent_result_v1'
          AND pr.prediction_finalized_at<=%s
        ORDER BY pr.prediction_finalized_at,pr.prediction_run_id
        LIMIT 1
        """,
        (mid,cutoff),
    ).fetchone()
    pred_counts={}
    manager_status=None
    if run:
        data=conn.execute(
            """
            SELECT agent_code::text,prediction_status::text
            FROM predictions
            WHERE match_id=%s AND prediction_run_id=%s
              AND experiment_id IS NULL
            """,
            (mid,int(run[0])),
        ).fetchall()
        if len(data)!=len({str(a[0]) for a in data}):
            raise ValueError("duplicate original agent rows")
        pred_counts={str(a[0]):str(a[1]) for a in data}
        manager=conn.execute(
            """
            SELECT manager_status::text FROM manager_consensus_results
            WHERE match_id=%s AND prediction_run_id=%s
            """,
            (mid,int(run[0])),
        ).fetchone()
        manager_status=str(manager[0]) if manager else "MISSING"
    return {
        "identity":"EXACT_CANONICAL","canonical_match_id":mid,
        "internal_discovered_prematch":True,
        "original_run_on_time":run is not None,
        "first_run_id":int(run[0]) if run else None,
        "original_agent_statuses":pred_counts,
        "original_manager_status":manager_status,
        "internal_pit_match_status":str(v[3]),
    }


def audit(manifest:Manifest,refs:list[Reference],
          internal:dict[str,dict[str,Any]], *,
          aligned_picks:dict[int,tuple[datetime,str]]|None=None
          )->dict[str,Any]:
    manifest.validate()
    seen=set()
    counts=Counter()
    details=[]
    aligned_picks=aligned_picks or {}
    for ref in sorted(refs,key=lambda v:v.source_match_id):
        ref.validate()
        if ref.source_match_id in seen:
            raise ValueError("duplicate reference fixture ID")
        seen.add(ref.source_match_id)
        if not manifest.requested_window_start<=ref.scheduled_start<manifest.requested_window_end:
            raise ValueError("reference fixture outside licence and completeness period")
        counts["reference_fixture_rows"]+=1
        if ref.draw_stage!="MAIN" or ref.match_type!="SINGLES":
            counts["out_of_scope_qualifiers_or_non_singles"]+=1
            continue
        counts["main_draw_singles_in_export"]+=1
        cutoff=ref.scheduled_start-timedelta(minutes=PREMATCH_MINUTES)
        if ref.first_published is None:
            counts["unknown_original_source_publication_time"]+=1
            continue
        if ref.first_published>cutoff:
            counts["officially_first_announced_after_30m_cutoff"]+=1
            continue
        counts["officially_knowable_by_30m_cutoff"]+=1
        match=internal.get(ref.source_match_id,{"identity":"NO_EXACT_PROVIDER_MAPPING"})
        reason=match.get("identity","NO_EXACT_PROVIDER_MAPPING")
        if reason!="EXACT_CANONICAL":
            counts["mapping_"+reason]+=1
            details.append({
                "source_fixture_id":ref.source_match_id,
                "reason":reason,
                "not_proof_internal_fixture_absent":True,
            })
            continue
        counts["exact_provider_id_mapped"]+=1
        if not match.get("internal_discovered_prematch"):
            counts["exact_mapped_but_not_discovered_by_30m"]+=1
            details.append({
                "source_fixture_id":ref.source_match_id,
                "canonical_match_id":match["canonical_match_id"],
                "reason":"MAPPED_BUT_NO_ORIGINAL_PREMATCH_VERSION",
            })
            continue
        counts["discovered_prematch_exact_verified"]+=1
        if match.get("original_run_on_time"):
            counts["original_run_targeted_on_time"]+=1
        statuses=match.get("original_agent_statuses",{})
        for code in (
            "AGENT_1_SURFACE_ELO","AGENT_2_HIERARCHICAL_MARKOV",
            "AGENT_3_INTRANSITIVITY"
        ):
            if statuses.get(code)=="PREDICTED":
                counts[code+"_PREDICTED"]+=1
        if all(statuses.get(code)=="PREDICTED" for code in (
                "AGENT_1_SURFACE_ELO","AGENT_2_HIERARCHICAL_MARKOV",
                "AGENT_3_INTRANSITIVITY")):
            counts["all_three_agents_predicted"]+=1
        if match.get("original_manager_status")=="CONSENSUS":
            counts["original_manager_consensus"]+=1
        picked=aligned_picks.get(match["canonical_match_id"])
        if picked and _utc(picked[0],"aligned_pick")<=cutoff:
            counts["aligned_dashboard_model_pick_published_before_30m"]+=1
            if picked[1]=="A1_A2_WIDE_SPREAD":
                counts["aligned_widespread_tracking_only"]+=1
            elif picked[1]=="MANAGER_CONSENSUS":
                counts["published_original_manager_consensus"]+=1
    denom=counts["officially_knowable_by_30m_cutoff"]
    # Distinguish exact source-mapping lower bound from true discovery recall.
    exact_lower_bound=(
        counts["discovered_prematch_exact_verified"]/denom if denom else None
    )
    report={
        "version":VERSION,
        "source_system":manifest.source_system,
        "export_version":manifest.export_version,
        "input_file_sha256":manifest.file_sha256,
        "commercial_rights_owner_approved":True,
        "provider_declares_complete_window":manifest.provider_declares_complete_window,
        "counts":dict(sorted(counts.items())),
        "per_fixture_exceptions":details,
        "official_eligible_singles_total_if_source_complete":(
            denom if manifest.provider_declares_complete_window else None),
        "exact_provider_match_coverage_lower_bound_conditional_on_export":(
            exact_lower_bound if manifest.provider_declares_complete_window else None),
        "true_official_match_discovery_rate_verified":False,
        "unmatched_provider_ids_may_exist_in_internal_db_under_other_ids":True,
        "original_feature_data_qualified_threshold_explicit":False,
        "source_license_or_completeness_cannot_be_inferred_from_website":True,
        "no_web_scraping":True,"no_api_calls":True,
        "no_database_writes":True,"no_prediction_policy_changes":True,
    }
    return report


def run_db(conn:psycopg.Connection,manifest:Manifest,
           refs:list[Reference])->dict[str,Any]:
    manifest.validate()
    conn.execute("SET default_transaction_read_only=on")
    internal={
        ref.source_match_id:source_matching(conn,manifest=manifest,ref=ref)
        for ref in refs if ref.draw_stage=="MAIN"
        and ref.first_published is not None
        and ref.first_published<=ref.scheduled_start-timedelta(minutes=PREMATCH_MINUTES)
    }
    rows=conn.execute(
        f"""WITH {aligned_production_model_picks_cte()}
            SELECT match_id,picked_at,aligned_pick_type
            FROM aligned_model_picks WHERE picked_at<=%s""",
        (manifest.report_as_of,),
    ).fetchall()
    picks={}
    for mid,issued,kind in rows:
        mid=int(mid)
        if mid in picks:
            raise ValueError("duplicate original aligned Model Pick")
        picks[mid]=(_utc(issued,"aligned_issue"),str(kind))
    return audit(manifest,refs,internal,aligned_picks=picks)


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--licensed-csv",required=True)
    p.add_argument("--manifest-json",required=True)
    args=p.parse_args(argv)
    manifest,refs=load_authorised_csv(Path(args.licensed_csv),Path(args.manifest_json))
    dsn=os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        result=run_db(conn,manifest,refs)
    print("TENNIS_LICENSED_OFFICIAL_FIXTURE_CENSUS "+json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
