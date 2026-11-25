"""Research-only Manager C chronological ablation; no publication or DB writes.

Freeze using separately time-truncated training labels and validation scores.
Evaluate a frozen manifest on subsequent first-issue records. Exact-K is a
descriptive comparator, never an operational threshold. Optional enrichment
requires immutable source evidence for the exact original prediction run.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import (
    CODES, PREMATCH_MINUTES, _utc, _wilson, source_sql,
)
from sports_edge_tennis.validation.manager_abc_validation_frozen_risk_coverage import normalize
from sports_edge_tennis.validation.manager_c_marginal_cohort import POLICY, _original_agent_evidence

VERSION = "MANAGER_C_FORWARD_ABLATION_V1"
TARGETS = (.10, .20, .25, .40, .50, .75)
GROUPS = ("calibration", "disagreement", "history", "uncertainty")
MIN_TRAIN = 30
L2 = 8.0
ITERATIONS = 350
LR = .12


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def read_original(conn, *, start, end, as_of):
    """Use an autocommit connection. Training must use its own earlier as-of."""
    conn.execute("SET default_transaction_read_only=on")
    raw = conn.execute(source_sql(), dict(start=_utc(start), end=_utc(end),
                                         as_of=_utc(as_of))).fetchall()
    records, exclusions, digest = normalize(raw, start=start, end=end, as_of=as_of)
    known = {int(r[0]): r[17] for r in raw}
    return records, _original_agent_evidence(raw), known, exclusions, digest


def confidence(row):
    p = float(row["candidates"][POLICY][1])
    if not math.isfinite(p) or not 0 < p < 1:
        raise ValueError("invalid original probability")
    if (row["candidates"][POLICY][0] == 1) != (p > .5):
        raise ValueError("original side orientation mismatch")
    return max(p, 1-p)


def _check(rows):
    if len({r["match_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate canonical match")
    for r in rows:
        if _utc(r["issued_at"]) > _utc(r["start_at"])-timedelta(minutes=PREMATCH_MINUTES):
            raise ValueError("unsafe original issue")
        if r["tour"] not in ("ATP_MAIN", "CHALLENGER"):
            raise ValueError("unsupported tour")


def _enrichment(row, snapshots, field):
    # Snapshots must be independently verified exports of an immutable
    # original-run feature source. A current player profile is NOT admissible.
    s = snapshots.get(row["match_id"])
    if s is None or field not in s or s[field] is None:
        return None
    if s.get("run_id") != row["run_id"] or not s.get("source_ref"):
        raise ValueError("enrichment lacks original-run provenance")
    for clock in ("as_of", "source_observed_at", "ingested_at"):
        t = s.get(clock)
        if isinstance(t, str):
            t = datetime.fromisoformat(t)
        if t is None or _utc(t) > _utc(row["issued_at"]):
            raise ValueError("missing or late enrichment timestamp")
    value = float(s[field])
    if not math.isfinite(value) or value < 0:
        raise ValueError("invalid enrichment")
    return value


def vector(row, group, agents, snapshots):
    if row["candidates"][POLICY] is None:
        return None
    p = confidence(row)
    x = (1.0, math.log(p/(1-p)))
    if group == "calibration":
        return x
    if group == "disagreement":
        a = agents.get(row["match_id"], {})
        a1, a2 = a.get(CODES[0]), a.get(CODES[1])
        if a1 is None or a2 is None:
            return None
        if any(not math.isfinite(float(v[1])) or not 0 < float(v[1]) < 1
               for v in (a1, a2)):
            raise ValueError("invalid original agent probability")
        return x + (abs(float(a1[1])-float(a2[1])), float(a1[0] != a2[0]))
    if group == "history":
        v = _enrichment(row, snapshots, "min_player_prior_match_count")
        return None if v is None else x + (math.log1p(v),)
    if group == "uncertainty":
        v = _enrichment(row, snapshots, "max_player_rating_uncertainty")
        return None if v is None else x + (math.log1p(v),)
    raise ValueError("unregistered ablation")


def _sigmoid(z):
    return 1/(1+math.exp(-max(-35., min(35., z))))


def _fit(data):
    if len(data) < MIN_TRAIN or len({y for _, y in data}) != 2:
        return None
    w = [0.] * len(data[0][0])
    for _ in range(ITERATIONS):
        g = [0.] * len(w)
        for x, y in data:
            e = _sigmoid(sum(a*b for a,b in zip(w,x)))-y
            for i,v in enumerate(x):
                g[i] += e*v
        for i in range(len(w)):
            w[i] -= LR*(g[i]+(L2*w[i] if i else 0))/len(data)
    return w


def _score(w, x):
    return _sigmoid(sum(a*b for a,b in zip(w,x)))


def _label(r):
    return int(r["winner"] == r["candidates"][POLICY][0])


def _cutoff(values, denominator, target):
    k = math.ceil(denominator*target)
    return sorted(values, reverse=True)[k-1] if 0 < k <= len(values) else None


def freeze(train, validation, *, train_cutoff, freeze_at, known, agents,
           snapshots=None, source_hashes=None):
    """No validation outcomes read. Use a separate train-cutoff SQL snapshot."""
    _check(train)
    _check(validation)
    train_cutoff, freeze_at = _utc(train_cutoff), _utc(freeze_at)
    if train_cutoff >= freeze_at or not train or not validation:
        raise ValueError("invalid temporal partitions")
    if any(_utc(r["start_at"]) >= train_cutoff for r in train):
        raise ValueError("training match outside earlier partition")
    if any(not train_cutoff <= _utc(r["issued_at"]) < freeze_at for r in validation):
        raise ValueError("validation issue outside frozen partition")
    if {r["match_id"] for r in train} & {r["match_id"] for r in validation}:
        raise ValueError("training/validation fixture overlap")
    snapshots = snapshots or {}
    result = {"version": VERSION, "train_cutoff": train_cutoff.isoformat(),
              "freeze_at": freeze_at.isoformat(), "source_hashes": source_hashes,
              "train_targets": len(train), "validation_targets": len(validation),
              "train_ids": sorted(r["match_id"] for r in train),
              "validation_ids": sorted(r["match_id"] for r in validation),
              "models": {}, "targets": TARGETS}
    for group in GROUPS:
        data, evidence = [], []
        for r in train:
            x = vector(r, group, agents, snapshots)
            t = known.get(r["match_id"])
            if (x is not None and r["winner"] in (1,2) and t is not None
                    and _utc(r["start_at"]) <= _utc(t) <= train_cutoff):
                data.append((x,_label(r)))
                evidence.append((r["match_id"],r["run_id"],x,_label(r),_utc(t)))
        # Train a confidence-only calibrator on the SAME labelled rows to
        # isolate information added by each feature, not sample composition.
        w = _fit(data)
        matched_cal = _fit([(x[:2],y) for x,y in data])
        available = [(r, vector(r,group,agents,snapshots)) for r in validation]
        available = [(r,x) for r,x in available if x is not None]
        model = {"training_n": len(data), "training_sha256": _hash(evidence),
                 "validation_feature_n": len(available),
                 "validation_feature_sha256": _hash([(r["match_id"],x) for r,x in available]),
                 "weights": w, "matched_calibration_weights": matched_cal}
        if w is not None:
            scores = {"raw": [confidence(r) for r,x in available],
                      "calibration": [_score(matched_cal,x[:2]) for r,x in available],
                      "feature": [_score(w,x) for r,x in available]}
            model["cutoffs"] = {str(t): {n:_cutoff(v,len(validation),t)
                                      for n,v in scores.items()} for t in TARGETS}
        result["models"][group] = model
    result["manifest_sha256"] = _hash(result)
    return result


def _stats(rows, scores, denominator):
    graded = [r for r in rows if r["winner"] in (1,2)]
    wins = sum(_label(r) for r in graded)
    pairs = [(max(1e-12,min(1-1e-12,scores[r["match_id"]])),_label(r)) for r in graded]
    n = len(graded)
    pending = sum(r.get("research_grade", "P") != "V" for r in rows if r["winner"] not in (1,2))
    void = len(rows)-n-pending
    return {"issued":len(rows),"settled":n,"ungraded":len(rows)-n,
            "wins":wins,"losses":n-wins,"pending":pending,"void":void,
            "cancelled_in_void":sum(r.get("research_grade")=="V" and r.get("result_status")=="CANCELLED" for r in rows),
            "accuracy":wins/n if n else None,
            "wilson95":_wilson(wins,n),"coverage":len(rows)/denominator,
            "brier":sum((p-y)**2 for p,y in pairs)/n if n else None,
            "log_loss":-sum(y*math.log(p)+(1-y)*math.log1p(-p) for p,y in pairs)/n if n else None,
            "ids_sha256":_hash(sorted(r["match_id"] for r in rows))}


def _compare(rows, raw, alternative, name, denominator, targets, cutoffs, tour_denominators):
    by_id = {r["match_id"]:r for r in rows}
    def pair(a,b):
        ai,bi = set(a),set(b)
        return {"raw":_stats([by_id[i] for i in sorted(ai)],raw,denominator),
                "alternative":_stats([by_id[i] for i in sorted(bi)],alternative,denominator),
                "removed":_stats([by_id[i] for i in sorted(ai-bi)],raw,denominator),
                "added":_stats([by_id[i] for i in sorted(bi-ai)],alternative,denominator),
                "overlap":len(ai&bi),
                "common_probability_raw":_stats([by_id[i] for i in sorted(ai&bi)],raw,denominator),
                "common_probability_alternative":_stats([by_id[i] for i in sorted(ai&bi)],alternative,denominator),
                "by_tour": {tour:{
                    "raw":_stats([by_id[i] for i in sorted(ai) if by_id[i]["tour"]==tour],raw,tour_denominators[tour]),
                    "alternative":_stats([by_id[i] for i in sorted(bi) if by_id[i]["tour"]==tour],alternative,tour_denominators[tour]),
                    "removed":_stats([by_id[i] for i in sorted(ai-bi) if by_id[i]["tour"]==tour],raw,tour_denominators[tour]),
                    "added":_stats([by_id[i] for i in sorted(bi-ai) if by_id[i]["tour"]==tour],alternative,tour_denominators[tour])}
                    for tour in tour_denominators if tour_denominators[tour]}}
    report = {"all_common_candidates":pair(raw,alternative),"targets":{}}
    for target in targets:
        k = math.ceil(denominator*target)
        locked = cutoffs[str(target)]
        a,b = locked["raw"],locked[name]
        entry = {"frozen_thresholds":None,"exact_k_descriptive_only":None}
        if a is not None and b is not None:
            entry["frozen_thresholds"] = pair(
                [i for i,p in raw.items() if p>=a],
                [i for i,p in alternative.items() if p>=b])
        if 0<k<=len(rows):
            entry["exact_k_descriptive_only"] = pair(
                sorted(raw,key=lambda i:(-raw[i],i))[:k],
                sorted(alternative,key=lambda i:(-alternative[i],i))[:k])
        report["targets"][str(target)] = entry
    return report


def evaluate(manifest, records, *, agents, snapshots=None):
    """Manifest must be externally time-attested BEFORE evaluation issuance."""
    _check(records)
    m = dict(manifest)
    expected = m.pop("manifest_sha256")
    if _hash(m) != expected or m["version"] != VERSION:
        raise ValueError("invalid frozen manifest")
    freeze_at = _utc(datetime.fromisoformat(m["freeze_at"]))
    if not records or any(_utc(r["issued_at"]) < freeze_at for r in records):
        raise ValueError("evaluation predates freeze")
    if (set(m["train_ids"]) | set(m["validation_ids"])) & {r["match_id"] for r in records}:
        raise ValueError("evaluation fixture overlaps earlier partition")
    snapshots = snapshots or {}
    tour_denominators = {tour:sum(r["tour"]==tour for r in records) for tour in ("ATP_MAIN","CHALLENGER")}
    out = {"manifest_sha256":expected,"first_run_targets":len(records),
           "official_eligible_denominator":None,"external_freeze_attestation_required":True,
           "groups":{}}
    for group, model in m["models"].items():
        available = [(r,vector(r,group,agents,snapshots)) for r in records]
        available = [(r,x) for r,x in available if x is not None]
        candidates = sum(r["candidates"][POLICY] is not None for r in records)
        row = {"candidate_n":candidates,"feature_available_n":len(available),
               "feature_missing_n":candidates-len(available)}
        if model["weights"] is not None and available:
            rows = [r for r,x in available]
            raw = {r["match_id"]:confidence(r) for r in rows}
            cal = {r["match_id"]:_score(model["matched_calibration_weights"],x[:2]) for r,x in available}
            learned = {r["match_id"]:_score(model["weights"],x) for r,x in available}
            row["raw_vs_calibration"] = _compare(rows,raw,cal,"calibration",len(records),m["targets"],model["cutoffs"],tour_denominators)
            row["raw_vs_feature"] = _compare(rows,raw,learned,"feature",len(records),m["targets"],model["cutoffs"],tour_denominators)
            row["same_candidate_calibration_vs_feature"] = {
                "calibration":_stats(rows,cal,len(records)),
                "feature":_stats(rows,learned,len(records))}
            row["by_tour_common_candidates"] = {
                tour: {name:_stats([r for r in rows if r["tour"]==tour], scores,
                                    sum(r["tour"]==tour for r in records))
                       for name,scores in (("raw",raw),("calibration",cal),("feature",learned))}
                for tour in ("ATP_MAIN","CHALLENGER") if any(r["tour"]==tour for r in records)}
        else:
            row["status"] = "UNAVAILABLE_FEATURES_OR_INSUFFICIENT_TRAINING"
        out["groups"][group] = row
    return out
