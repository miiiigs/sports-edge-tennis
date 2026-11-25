"""Read-only original Manager C source adapter and reproducible research CLI.

No reconstruction of original player history/uncertainty. Version identifiers
are metadata, not proof of numeric feature values. No deployment or DB writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime, timedelta
import json
import os
import re

import psycopg

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import CODES, _utc, source_sql
from sports_edge_tennis.validation.manager_abc_validation_frozen_risk_coverage import (
    normalize, freeze_threshold, evaluate_selected,
)
from sports_edge_tennis.validation.manager_c_marginal_cohort import (
    POLICY, _original_agent_evidence, analyze,
)
from sports_edge_tennis.validation.manager_c_forward_feature_ablation import (
    GROUPS, _hash, evaluate, freeze, vector,
)

VERSION = "MANAGER_C_ORIGINAL_SOURCE_V1"
TRAIN_START = datetime(2026,10,15,tzinfo=UTC)
TRAIN_END = datetime(2026,11,4,tzinfo=UTC)
VALIDATION_END = datetime(2026,11,21,tzinfo=UTC)
HISTORICAL_AS_OF = VALIDATION_END + timedelta(hours=3,minutes=30)
BASELINE_SHA = "e7d70fb821d7c69339adfce51bbd7fe35161adda49c595124d1791fef1b56863"


def original_sql():
    # Preserve the exact established first-run selection, then retrieve only
    # metadata tied to that exact run/fixture and the same issue-time version.
    return """
    WITH original(mid,rid,issued,starts,tour,edition,agent,status,side,prob,
                  pred_at,manager_status,manager_reason,manager_side,manager_p,
                  outcome,winner,known) AS (
    """ + source_sql().strip().rstrip(";") + """
    )
    SELECT o.*,p.prediction_id,p.prediction_locked_at,p.model_version,
           p.feature_version,p.agent_config_version_id,
           v.match_version_id,v.surface::text,v.qualifying_flag,
           EXISTS (SELECT 1 FROM rating_snapshots rs WHERE rs.player_id=v.player_1_id
             AND rs.model_version=p.model_version AND rs.feature_version=p.feature_version
             AND rs.experiment_id IS NULL AND rs.as_of<=p.as_of AND rs.created_at<=p.as_of
             AND rs.sample_match_count IS NOT NULL) AS p1_rating_sample_visible,
           EXISTS (SELECT 1 FROM rating_snapshots rs WHERE rs.player_id=v.player_2_id
             AND rs.model_version=p.model_version AND rs.feature_version=p.feature_version
             AND rs.experiment_id IS NULL AND rs.as_of<=p.as_of AND rs.created_at<=p.as_of
             AND rs.sample_match_count IS NOT NULL) AS p2_rating_sample_visible
    FROM original o
    LEFT JOIN predictions p ON p.match_id=o.mid AND p.prediction_run_id=o.rid
      AND p.agent_code::text=o.agent AND p.experiment_id IS NULL
    LEFT JOIN LATERAL (
      SELECT match_version_id,surface,qualifying_flag,player_1_id,player_2_id
      FROM match_versions mv
      WHERE mv.match_id=o.mid AND mv.as_of<=o.issued
        AND mv.source_observed_at<=o.issued AND mv.ingested_at<=o.issued
      ORDER BY mv.as_of DESC,mv.source_observed_at DESC,mv.match_version_id DESC
      LIMIT 1
    ) v ON TRUE
    ORDER BY o.mid,o.agent
    """


def grading(record, known, *, as_of):
    """Research W/L/P/V; cancelled separately reported inside V."""
    if record["winner"] in (1,2):
        return "W" if record["winner"] == record["candidates"][POLICY][0] else "L"
    if (record["result_status"] in {"RETIRED","WALKOVER","DEFAULTED","CANCELLED"}
            and known is not None
            and _utc(record["issued_at"]) <= _utc(known) <= _utc(as_of)):
        return "V"
    return "P"


def decode(raw, *, start, end, as_of):
    legacy = [tuple(r[:18]) for r in raw]
    records, excluded, digest = normalize(legacy,start=start,end=end,as_of=as_of)
    ids = {r["match_id"] for r in records}
    agents = _original_agent_evidence(legacy)
    meta, known = {}, {}
    for r in raw:
        mid = int(r[0])
        if mid not in ids:
            continue
        known[mid] = r[17]
        m = meta.setdefault(mid, {"fixture_version_id":r[23],"surface":r[24],
                                  "qualifying":r[25],"predictions":{}})
        if (m["fixture_version_id"],m["surface"],m["qualifying"]) != tuple(r[23:26]):
            raise ValueError("inconsistent original fixture metadata")
        if r[6] is not None:
            if r[18] is None or r[19] is None or _utc(r[19]) > _utc(r[2]):
                raise ValueError("missing or late original prediction lock")
            m["predictions"][r[6]] = {"prediction_id":r[18],"locked_at":r[19],
                                      "model_version":r[20],"feature_version":r[21],
                                      "config_id":r[22],
                                      "both_rating_sample_counts_visible":bool(r[26] and r[27])}
    for r in records:
        m = meta[r["match_id"]]
        r["main_draw_verified"] = m["qualifying"] is False
        r["research_grade"] = (grading(r,known[r["match_id"]],as_of=as_of)
                               if r["candidates"][POLICY] is not None else None)
    return {"records":records,"agents":agents,"known":known,"meta":meta,
            "source_sha256":digest,"full_source_sha256":_hash(raw),
            "exclusions":excluded}


def read(conn, *, start, end, as_of):
    if not conn.autocommit:
        raise ValueError("dedicated autocommit connection required")
    start,end,as_of = map(_utc,(start,end,as_of))
    if not start < end <= as_of or end-start > timedelta(days=120):
        raise ValueError("invalid bounded source window")
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '120s'")
        conn.execute("SET LOCAL lock_timeout = '3s'")
        if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
            raise RuntimeError("read-only transaction not established")
        raw = conn.execute(original_sql(),dict(start=start,end=end,as_of=as_of)).fetchall()
    return decode(raw,start=start,end=end,as_of=as_of)


def summarize(rows, data):
    grades = Counter(r["research_grade"] for r in rows if r["research_grade"])
    fields = Counter()
    for r in rows:
        mid = r["match_id"]
        m = data["meta"][mid]
        p = m["predictions"]
        fields["surface_at_issue"] += int(m["surface"] not in (None,"UNKNOWN"))
        fields["main_draw_verified"] += int(r["main_draw_verified"])
        fields["original_agent_probability_pair"] += int(all(
            c in data["agents"].get(mid,{}) for c in CODES[:2]))
        fields["original_C_probability"] += int(r["candidates"][POLICY] is not None)
        fields["any_agent_both_rating_samples_visible_unlinked"] += int(any(
            x["both_rating_sample_counts_visible"] for x in p.values()))
        fields["all_original_agent_version_ids"] += int(all(
            c in p and all(p[c][k] is not None for k in
                           ("model_version","feature_version","config_id")) for c in CODES))
    return {"targets":len(rows),"wlpv":{k:grades[k] for k in "WLPV"},
            "cancelled_in_void":sum(r["research_grade"]=="V" and
                                    r["result_status"]=="CANCELLED" for r in rows),
            "available":{k:fields[k] for k in (
                "surface_at_issue","main_draw_verified","original_agent_probability_pair",
                "original_C_probability","all_original_agent_version_ids",
                "any_agent_both_rating_samples_visible_unlinked")},
            "rating_sample_census_note":"Same canonical players and model/feature versions, both clocks by original agent as_of; no original-run snapshot linkage or identity bridge proof.",
            "not_proven_original_feature_values":["min_player_prior_match_count",
                "max_player_rating_uncertainty","surface_elo_gap","serve_return_depth"]}


def historical(data):
    rows = data["records"]
    report = analyze(rows,validation_end=TRAIN_END,original_agents=data["agents"],
                     dataset_sha256=data["source_sha256"])
    early = [r for r in rows if r["start_at"]<TRAIN_END]
    later = [r for r in rows if r["start_at"]>=TRAIN_END]
    selected = {}
    for name,target in (("narrow",.2),("broad",.4)):
        cutoff = freeze_threshold(early,policy=POLICY,coverage_target=target)["threshold"]
        if cutoff is None:
            raise ValueError("cannot reproduce historical cutoff")
        _,selected[name] = evaluate_selected(later,policy=POLICY,cutoff=cutoff)
    n,b = ({r["match_id"] for r in selected[k]} for k in ("narrow","broad"))
    groups = {"narrow":selected["narrow"],"broad":selected["broad"],
              "marginal":[r for r in later if r["match_id"] in b-n],
              "rejected":[r for r in later if r["match_id"] not in b]}
    report["feature_census"] = {name:{"all":summarize(rs,data),
        **{tour:summarize([r for r in rs if r["tour"]==tour],data)
           for tour in ("ATP_MAIN","CHALLENGER")}} for name,rs in groups.items()}
    report["historical_source_hash_matches"] = data["source_sha256"] == BASELINE_SHA
    report["full_source_sha256"] = data["full_source_sha256"]
    report["exclusions"] = data["exclusions"]
    return report


def make_manifest(train,val,*,boundary,source_sha,now):
    boundary,now = map(_utc,(boundary,now))
    if now >= boundary or boundary < VALIDATION_END:
        raise ValueError("freeze boundary must be future")
    tr = [r for r in train["records"] if r["main_draw_verified"]]
    va = [dict(r,winner=None,research_grade=None) for r in val["records"]
          if r["main_draw_verified"] and TRAIN_END <= r["issued_at"] < boundary]
    m = freeze(tr,va,train_cutoff=TRAIN_END,freeze_at=boundary,
               known=train["known"],agents={**train["agents"],**val["agents"]},
               source_hashes={"train":train["full_source_sha256"],
                              "validation":val["full_source_sha256"]})
    # Independent attestation of this final hash is required before boundary.
    envelope = {"version":VERSION,"source_commit":source_sha,"created_at":now.isoformat(),
                "freeze_at":boundary.isoformat(),"evaluation_end":(boundary+timedelta(days=28)).isoformat(),
                "settlement_as_of":(boundary+timedelta(days=35)).isoformat(),
                "manifest":m,"external_attestation":None,
                "training_exclusions":train["exclusions"],"validation_exclusions":val["exclusions"],
                "history_uncertainty_status":"NO_VERIFIED_ORIGINAL_NUMERIC_SNAPSHOT"}
    envelope["envelope_sha256"] = _hash(envelope)
    return envelope


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",choices=("historical","freeze","evaluate"))
    p.add_argument("--source-sha",required=True)
    p.add_argument("--boundary-utc")
    p.add_argument("--manifest")
    a = p.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}",a.source_sha):
        p.error("exact Git commit SHA required")
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("authorized TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10,
                         options="-c default_transaction_read_only=on") as conn:
        if a.mode == "historical":
            out = historical(read(conn,start=TRAIN_START,end=VALIDATION_END,as_of=HISTORICAL_AS_OF))
        elif a.mode == "freeze":
            if not a.boundary_utc or not a.boundary_utc.endswith("Z"):
                p.error("future explicit UTC boundary required")
            boundary = datetime.fromisoformat(a.boundary_utc.replace("Z","+00:00"))
            if datetime.now(UTC)>=boundary:
                p.error("boundary passed; preregister a new future boundary")
            train = read(conn,start=TRAIN_START,end=TRAIN_END,as_of=TRAIN_END)
            val = read(conn,start=TRAIN_END,end=VALIDATION_END,as_of=VALIDATION_END)
            out = make_manifest(train,val,boundary=boundary,source_sha=a.source_sha,now=datetime.now(UTC))
        else:
            if not a.manifest:
                p.error("private frozen envelope required")
            with open(a.manifest,encoding="utf-8") as f:
                envelope = json.load(f)
            check = dict(envelope)
            digest = check.pop("envelope_sha256")
            if _hash(check)!=digest or envelope["source_commit"]!=a.source_sha:
                p.error("envelope hash or source commit mismatch")
            start,end,as_of = [datetime.fromisoformat(envelope[k]) for k in
                                ("freeze_at","evaluation_end","settlement_as_of")]
            if datetime.now(UTC)<as_of:
                p.error("predeclared settlement window has not matured")
            data = read(conn,start=start,end=end,as_of=as_of)
            rows = [r for r in data["records"] if r["main_draw_verified"] and r["issued_at"]>=start]
            out = evaluate(envelope["manifest"],rows,agents=data["agents"])
            out.update(source_sha256=data["full_source_sha256"],exclusions=data["exclusions"],
                       envelope_sha256=digest,independent_forward_validation=False,
                       note="external timestamp evidence must be verified outside this CLI")
    print(json.dumps({"source_commit":a.source_sha,"report":out},sort_keys=True,default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
