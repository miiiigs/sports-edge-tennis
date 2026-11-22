"""Reconcile 14 ungraded among validation-frozen 194 A1/A2 original forecasts.

Reports actual stored status at a fixed report cutoff. Never silently scores
retirements, defaults, voids, walkovers, cancellations or missing results as
wins or losses. The 74.44% historical result is an inspected research holdout,
not a forward-validated production strategy or all-fixture coverage metric.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.validation.a1_a2_blend_shadow import normalize, source_sql
from sports_edge_tennis.validation.a1_a2_validation_frozen_selection import (
    _confidence, _freeze_cutoff, _score_selected,
)
from sports_edge_tennis.validation.a1_a2_weight_selection_shadow import ObservedPair

VERSION = "A1A2_VALIDATION_FROZEN_194_STATUS_RECONCILIATION_V1"
MODEL = "equal_log_odds_blend"
VALIDATION_FRACTION = .40
EXPECTED_ISSUED = 194
EXPECTED_SETTLED = 180
EXPECTED_UNGRADED = 14


def _utc(value:datetime)->datetime:
    if value is None or value.tzinfo is None:
        raise ValueError("expected timezone-aware timestamp")
    return value.astimezone(UTC)


def classify(*, status:str|None, winner_side:int|None,
             known_at:datetime|None, start_at:datetime,
             as_of:datetime)->str:
    """Explicit reason for an ungraded pick under the COMPLETED-only metric."""
    start,cutoff=_utc(start_at),_utc(as_of)
    if start>cutoff:
        return "FUTURE_SCHEDULED_AT_REPORT_CUTOFF"
    if status is None:
        return "PAST_START_NO_TERMINAL_RESULT_OBSERVED"
    if status=="CANCELLED":
        return "CANCELLED_NO_WINNER"
    if status=="WALKOVER":
        return ("WALKOVER_WINNER_PRESENT_EXCLUDED_BY_COMPLETED_ONLY_RULE"
                if winner_side in (1,2)
                else "WINNERLESS_WALKOVER_EXCLUDED")
    if status=="RETIRED":
        return ("RETIRED_WITH_WINNER_EXCLUDED_BY_COMPLETED_ONLY_RULE"
                if winner_side in (1,2)
                else "RETIRED_WINNER_NOT_USABLE")
    if status=="COMPLETED":
        if winner_side not in (1,2):
            return "COMPLETED_WITHOUT_VALID_WINNER_DATA_ERROR"
        if known_at is None or _utc(known_at)>cutoff or _utc(known_at)<start:
            return "COMPLETED_BUT_RESULT_TIMESTAMP_UNSAFE"
        return "ALREADY_SETTLED_NOT_UNGRADED"
    return "OTHER_UNGRADED_RESULT_STATUS_"+str(status)


def audit(rows:list[tuple], *,as_of:datetime,
          validation_start:datetime,holdout_start:datetime,
          require_original_counts:bool=True)->dict[str,Any]:
    as_of,validation_start,holdout_start=map(
        _utc,(as_of,validation_start,holdout_start))
    if not validation_start<holdout_start<as_of:
        raise ValueError("invalid validation and holdout cutoff order")
    if validation_start.time()!=datetime.min.time() or holdout_start.time()!=datetime.min.time():
        raise ValueError("split boundaries must be fixed UTC midnights")

    normalized,rejected=normalize(rows,as_of=as_of)
    by_mid={}
    for r in rows:
        mid=int(r[0])
        if mid in by_mid:
            raise ValueError("duplicate first-pair source rows")
        by_mid[mid]=r
    originals=[]
    for p in normalized:
        raw=by_mid[p.match_id]
        result_known_at=raw[14] if raw[12]=="COMPLETED" else None
        if result_known_at is not None and (
            _utc(result_known_at)<_utc(p.start_at) or _utc(result_known_at)>as_of
        ):
            raise ValueError("unsafe original result observation")
        originals.append(ObservedPair(pair=p,result_known_at=result_known_at))
    validation=[
        x for x in originals
        if validation_start.date()<=x.pair.start_at.astimezone(UTC).date()
           <holdout_start.date()
        and x.pair.winner_p1 in (0,1)
        and x.result_known_at is not None
        and _utc(x.result_known_at)<holdout_start
    ]
    holdout=[
        x for x in originals
        if x.pair.start_at.astimezone(UTC).date()>=holdout_start.date()
    ]
    cutoff=_freeze_cutoff(validation,MODEL,VALIDATION_FRACTION)
    if cutoff is None:
        raise ValueError("no prior validation confidence data to freeze threshold")
    score,selected=_score_selected(holdout,MODEL,cutoff)
    ungraded=[]
    by_status=Counter()
    for x in selected:
        if x.pair.winner_p1 in (0,1):
            continue
        p=x.pair
        raw=by_mid[p.match_id]
        status=str(raw[12]) if raw[12] is not None else None
        winner_side=int(raw[13]) if raw[13] is not None else None
        known_at=raw[14]
        reason=classify(
            status=status,winner_side=winner_side,
            known_at=known_at,start_at=p.start_at,as_of=as_of,
        )
        if reason=="ALREADY_SETTLED_NOT_UNGRADED":
            raise ValueError("selected ungraded completed row could be scored")
        by_status[reason]+=1
        ungraded.append({
            "canonical_match_id":p.match_id,
            "tour":p.tour,
            "surface":p.surface,
            "original_issued_at":_utc(p.issued_at).isoformat(),
            "match_start_at":_utc(p.start_at).isoformat(),
            "stored_result_status_at_report_cutoff":status or "NO_RESULT_OBSERVED",
            "stored_winner_side":winner_side,
            "result_known_at":_utc(known_at).isoformat() if known_at else None,
            "ungraded_classification":reason,
            "original_selected_confidence":round(
                _confidence(p,MODEL) or 0.,8),
        })
    ungraded.sort(key=lambda r:r["canonical_match_id"])
    if len(ungraded)!=score["ungraded_selected"]:
        raise ValueError("ungraded per-fixture reconciliation mismatched scorecard")
    if len(selected)!=score["issued_selected"] or (
        score["wins"]+score["losses"]!=score["settled_selected"]
    ):
        raise ValueError("scorecard internal consistency failure")
    reproduced=(
        score["issued_selected"]==EXPECTED_ISSUED and
        score["settled_selected"]==EXPECTED_SETTLED and
        score["ungraded_selected"]==EXPECTED_UNGRADED and
        score["wins"]==134 and score["losses"]==46 and
        len(holdout)==519 and len(validation)==664
    )
    if require_original_counts and not reproduced:
        raise ValueError(
            "original 194/519 134W46L cohort not reproduced at supplied fixed cutoffs"
        )
    digest=hashlib.sha256()
    for x in selected:
        r=x.pair
        digest.update((
            f"{r.match_id}|{r.issued_at.isoformat()}|{r.start_at.isoformat()}|"
            f"{r.winner_p1}|{_confidence(r,MODEL):.12f}\n"
        ).encode())
    return {
        "version":VERSION,"as_of_utc":as_of.isoformat(),
        "validation_start":validation_start.isoformat(),
        "holdout_start":holdout_start.isoformat(),
        "model":MODEL,"validation_target_fraction":VALIDATION_FRACTION,
        "threshold_fit_from_earlier_settled_validation_only":cutoff,
        "prior_validation_settled":len(validation),
        "holdout_issued_original_paired":len(holdout),
        "source_first_pair_rows":len(rows),
        "normalized_original_paired":len(normalized),
        "source_rejections":rejected,
        "selected_issued":score["issued_selected"],
        "selected_settled":score["settled_selected"],
        "selected_wins":score["wins"],"selected_losses":score["losses"],
        "selected_ungraded":score["ungraded_selected"],
        "selected_settled_accuracy":score["wins"]/score["settled_selected"] if score["settled_selected"] else None,
        "status_reason_counts":dict(sorted(by_status.items())),
        "ungraded_original_match_records":ungraded,
        "original_194_cohort_reproduced":reproduced,
        "selection_sha256":digest.hexdigest(),
        "all_eligible_fixture_coverage":None,
        "historical_holdout_previously_inspected":True,
        "not_forward_validated":True,
        "research_only":True,"production_modified":False,"database_writes":False,
        "classification_notice":(
            "Per-match labels reflect stored original outcome observations by "
            "the fixed report cutoff; no future observed results are imported, "
            "no void or retired outcome is silently treated as a loss, "
            "no retrospective missing live selection is created."
        ),
    }


def run(conn:psycopg.Connection,*,as_of:datetime,
        validation_start:datetime,holdout_start:datetime)->dict[str,Any]:
    conn.execute("SET default_transaction_read_only=on")
    rows=conn.execute(source_sql(),{"as_of":_utc(as_of)}).fetchall()
    return audit(rows,as_of=as_of,validation_start=validation_start,
                 holdout_start=holdout_start)


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--as-of-utc",required=True)
    p.add_argument("--validation-start-utc",required=True)
    p.add_argument("--holdout-start-utc",required=True)
    x=p.parse_args(argv)
    vals=[x.as_of_utc,x.validation_start_utc,x.holdout_start_utc]
    if any(not z.endswith("Z") for z in vals):
        p.error("all timestamps must use explicit UTC Z")
    parsed=[datetime.fromisoformat(z.replace("Z","+00:00")) for z in vals]
    dsn=os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        out=run(conn,as_of=parsed[0],validation_start=parsed[1],holdout_start=parsed[2])
    print("A1A2_FROZEN_194_UNGRADED_STATUS "+json.dumps(out,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
