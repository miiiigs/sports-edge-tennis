"""Offline, permission-gated competitor receipt and scoring extension.

No web requests, production writes or automatic settlement. The external
operator supplies authorized observations and the independently frozen canonical
fixture census. Receipt clock and rights are assertions, not independent proof.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import sqlite3
from copy import deepcopy
from contextlib import closing
from itertools import groupby
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fractions import Fraction
from urllib.parse import urlsplit

from sports_edge_tennis.validation.competitor_forward_audit import utc, _mean, _logloss, _cluster_ci

SOURCES = ("pronocast", "tennispredictions_ai", "bananapoint", "tennisbrain",
           "courtcruncher", "tennisforesight", "sports_edge_original", "bookmaker")
CLASSES = ("market_independent", "market_assisted", "bookmaker", "unknown")
CUTOFF = timedelta(minutes=30)
SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  seq INTEGER PRIMARY KEY, source TEXT NOT NULL, match_id TEXT NOT NULL,
  digest TEXT NOT NULL, received TEXT NOT NULL, payload BLOB NOT NULL,
  fixture BLOB NOT NULL, grant_ref TEXT NOT NULL,
  UNIQUE(source, match_id, digest)
);
CREATE TRIGGER IF NOT EXISTS observations_no_update BEFORE UPDATE ON observations
BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS observations_no_delete BEFORE DELETE ON observations
BEGIN SELECT RAISE(ABORT,'append only'); END;
"""


def _now():
    return datetime.now(timezone.utc)


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _decode(raw):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError("duplicate JSON key")
            result[k] = v
        return result

    def bad(value):
        raise ValueError("nonfinite number: " + value)

    if not isinstance(raw, bytes) or not 0 < len(raw) <= 1024 * 1024:
        raise ValueError("invalid payload")
    record = json.loads(raw, object_pairs_hook=unique, parse_constant=bad)
    if not isinstance(record, dict):
        raise ValueError("expected object")
    return record


def _url(value):
    u = urlsplit(value)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise ValueError("invalid source URL")
    return u.hostname.lower()


def _fixture(f):
    if (f.get("tour") not in ("ATP_TOUR", "CHALLENGER")
            or f.get("sex") != "M" or f.get("discipline") != "SINGLES"
            or f.get("draw") not in ("MAIN_DRAW", "QUALIFYING")
            or f.get("eligible") is not True):
        raise ValueError("outside canonical eligible population")
    for key in ("match_id", "player1_id", "player2_id", "tournament_id", "census_ref"):
        if not _nonempty(f.get(key)):
            raise ValueError("missing canonical " + key)
    if f["player1_id"] == f["player2_id"]:
        raise ValueError("identical participants")
    return utc(f["frozen_start_utc"])


def _validate(record, f, grant, received):
    start = _fixture(f)
    source = record.get("source")
    if source not in SOURCES or record.get("match_id") != f["match_id"]:
        raise ValueError("unknown source or canonical match")
    if (grant.get("source") != source or grant.get("commercial_research") is not True
            or grant.get("prospective_capture") is not True
            or not _nonempty(grant.get("reference"))
            or not utc(grant["valid_from_utc"]) <= received < utc(grant["valid_until_utc"])):
        raise ValueError("no active permission")
    if _url(record["source_url"]) not in grant.get("allowed_hosts", []):
        raise ValueError("source host outside permission")
    if record.get("market") != "MATCH_WINNER" or record.get("market_class") not in CLASSES:
        raise ValueError("invalid market")
    if (source == "bookmaker") != (record["market_class"] == "bookmaker"):
        raise ValueError("bookmaker class mismatch")
    a, b = record.get("player1_id"), record.get("player2_id")
    if (a, b) not in ((f["player1_id"], f["player2_id"]), (f["player2_id"], f["player1_id"])):
        raise ValueError("participant mismatch")
    if record.get("predicted_winner_id") not in (a, b):
        raise ValueError("invalid winner")
    p = record.get("p_player1")
    if p is not None:
        if type(p) not in (int, float) or not math.isfinite(p) or not 0 < p < 1:
            raise ValueError("invalid probability")
        if p != .5 and record["predicted_winner_id"] != (a if p > .5 else b):
            raise ValueError("winner probability disagreement")
    if record.get("probability_kind") != ("win_probability" if p is not None else "unavailable"):
        raise ValueError("confidence is not a probability")
    pub = record.get("published_at_utc")
    if pub is not None and utc(pub) > received:
        raise ValueError("publication after receipt")
    if received > start - CUTOFF:
        raise ValueError("late observation")
    if utc(f["census_observed_at_utc"]) > received:
        raise ValueError("fixture not independently visible at receipt")
    digest = record.get("evidence_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("missing permitted evidence digest")
    if not _nonempty(record.get("evidence_ref")):
        raise ValueError("missing evidence reference")
    if record.get("model_version") is not None and not _nonempty(record["model_version"]):
        raise ValueError("invalid model version")
    if (record.get("selected_at_publication") is not None
            and type(record["selected_at_publication"]) is not bool):
        raise ValueError("invalid publication selection flag")
    if record.get("outcomes_available") is not False:
        raise ValueError("outcome contamination")


class ObservationStore:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError("absolute local storage path required")
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA recursive_triggers=ON")
        return conn

    def receive(self, raw, fixture, grant):
        received = _now()
        record = _decode(raw)
        digest = hashlib.sha256(raw).hexdigest()
        source, mid = record["source"], record["match_id"]
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT seq,received,payload FROM observations WHERE source=? AND match_id=? AND digest=?",
                (source, mid, digest)).fetchone()
            if prior:
                if prior[2] != raw:
                    raise ValueError("digest conflict")
                return {"seq": prior[0], "received_at_utc": prior[1], "duplicate": True}
            _validate(record, fixture, grant, received)
            previous = conn.execute(
                "SELECT received,fixture FROM observations WHERE source=? AND match_id=? ORDER BY seq DESC LIMIT 1",
                (source, mid)).fetchone()
            frozen = json.dumps(fixture, sort_keys=True).encode()
            if previous and (received < utc(previous[0]) or previous[1] != frozen):
                raise ValueError("clock regression or frozen fixture drift")
            cur = conn.execute(
                "INSERT INTO observations(source,match_id,digest,received,payload,fixture,grant_ref) VALUES(?,?,?,?,?,?,?)",
                (source, mid, digest, received.isoformat(), raw, frozen, grant["reference"]))
            seq = cur.lastrowid
        return {"seq": seq, "received_at_utc": received.isoformat(), "duplicate": False,
                "revision": previous is not None, "payload_sha256": digest,
                "independent_clock_verified": False}

    def publication_inventory(self, *, as_of):
        """Inventory only; never promote a revision to first publication."""
        now = utc(as_of)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT source,match_id,received,payload,digest FROM observations ORDER BY seq"
            ).fetchall()
        result = {}
        seen = set()
        for source, mid, received, raw, digest in rows:
            if utc(received) > now:
                continue
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("stored payload digest mismatch")
            record = _decode(raw)
            key = (source, mid)
            row = result.setdefault(source, {
                "first_observations": 0, "subsequent_distinct_payloads": 0,
                "first_with_declared_publication_time": 0,
                "first_without_declared_publication_time": 0,
                "original_publication_independently_verified": False,
            })
            if key in seen:
                row["subsequent_distinct_payloads"] += 1
            else:
                seen.add(key)
                row["first_observations"] += 1
                field = ("first_with_declared_publication_time" if
                         record.get("published_at_utc") is not None else
                         "first_without_declared_publication_time")
                row[field] += 1
        return result

    def first(self):
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT source,match_id,received,payload,fixture,digest,grant_ref FROM observations "
                "WHERE seq IN (SELECT min(seq) FROM observations GROUP BY source,match_id) ORDER BY seq"
            ).fetchall()
        result = []
        for source, mid, received, raw, fixture, digest, grant_ref in rows:
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("stored payload digest mismatch")
            result.append({"source": source, "match_id": mid, "received_at_utc": received,
                           "forecast": _decode(raw), "fixture": json.loads(fixture),
                           "grant_ref": grant_ref})
        return result


class ConfiguredCompetitorCollector:
    """Local receiver boundary for a separate operator, aligned with PR #394.

    Grants and census are supplied by the collector operator, not the producer.
    Neither an operator assertion nor a checksum independently proves rights,
    source transformation fidelity, or clock independence.
    """

    def __init__(self, store, grants, fixtures):
        self.store = store
        self.grants = deepcopy(grants)
        self.fixtures = deepcopy(fixtures)

    def receive(self, raw, supplied_sha, evidence_bytes):
        record = _decode(raw)
        if not isinstance(supplied_sha, str) or not hmac.compare_digest(
                hashlib.sha256(raw).hexdigest(), supplied_sha):
            raise ValueError("transport digest mismatch")
        if not isinstance(evidence_bytes, bytes) or not 0 < len(evidence_bytes) <= 1024 * 1024:
            raise ValueError("missing permitted source evidence")
        if hashlib.sha256(evidence_bytes).hexdigest() != record.get("evidence_sha256"):
            raise ValueError("source evidence digest mismatch")
        grant = self.grants.get(record.get("source"))
        fixture = self.fixtures.get(record.get("match_id"))
        if grant is None or fixture is None:
            raise ValueError("not in operator-approved grant and census registry")
        if (grant.get("digest_retention") is not True
                or grant.get("normalized_forecast_retention") is not True
                or grant.get("internal_comparison") is not True):
            raise ValueError("retention or comparison permission missing")
        if record.get("published_at_utc") is not None and (
                grant.get("publication_timestamp_semantics") != "original_forecast_publication"):
            raise ValueError("unverified publication timestamp semantics")
        if record.get("origin") != "observed_pre_match":
            raise ValueError("historical or unknown-origin record")
        result = self.store.receive(raw, fixture, grant)
        return dict(result, source_evidence_digest_verified=True,
                    independent_clock_verified=False,
                    source_transformation_verified=False,
                    eligible_for_activation_evidence=False)


def _metrics(points):
    probs = [(p, y) for _, p, y in points if p is not None]
    calibration = []
    for i in range(10):
        band = [(p, y) for p, y in probs if i / 10 <= p < (i + 1) / 10]
        calibration.append({"lower": i / 10, "n": len(band), "mean_p": _mean([p for p,y in band]),
                            "observed": _mean([y for p,y in band])})
    return {"settled": len(points), "correct": sum(hit for hit,_,_ in points),
            "accuracy": _mean([hit for hit,_,_ in points]), "probability_n": len(probs),
            "brier": _mean([(p-y)**2 for p,y in probs]),
            "log_loss": _mean([_logloss(p,y) for p,y in probs]), "calibration": calibration}


def risk_coverage(points, *, denominator):
    """0/1 loss, confidence=max(p,1-p); complete denominator for full AUGRC.

    Points represent distinct, settled, timely probability forecasts from
    the declared cohort. Equal-confidence rows enter together. Trapezoids on
    generalized risk implement uniform fractional acceptance within a tie.
    Missing rows are not imputed as correct, wrong or low-confidence.
    """
    if type(denominator) is not int or denominator < 0:
        raise ValueError("invalid risk denominator")
    ranked = []
    for hit, p, y in points:
        if p is None:
            continue
        if (type(p) not in (int, float) or not math.isfinite(p)
                or not 0 < p < 1 or hit not in (0, 1) or y not in (0, 1)):
            raise ValueError("invalid risk point")
        ranked.append((max(p, 1-p), 1-hit))
    if len(ranked) > denominator:
        raise ValueError("risk observations exceed denominator")
    ranked.sort(reverse=True)
    curve = []
    accepted = errors = 0
    augrc = 0.0
    previous_c = previous_g = 0.0
    if denominator:
        for confidence, group in groupby(ranked, key=lambda item: item[0]):
            block = list(group)
            accepted += len(block)
            errors += sum(error for _, error in block)
            coverage = accepted / denominator
            generalized = errors / denominator
            augrc += (coverage - previous_c) * (generalized + previous_g) / 2
            curve.append({"confidence_threshold": confidence, "accepted": accepted,
                          "errors": errors, "coverage": coverage,
                          "selective_risk": errors / accepted,
                          "generalized_risk": generalized})
            previous_c, previous_g = coverage, generalized
    complete = denominator > 0 and len(ranked) == denominator
    return {"denominator": denominator, "probability_n": len(ranked),
            "missing_probability_or_unavailable_n": denominator-len(ranked),
            "max_coverage": len(ranked)/denominator if denominator else None,
            "curve": curve, "full_coverage_available": complete,
            "augrc": augrc if complete else None,
            "confidence": "maximum_class_probability",
            "tie_policy": "grouped_uniform_fractional_acceptance",
            "loss": "zero_one", "descriptive_only": True}


def score(fixtures, first, settlements, *, as_of, sources=SOURCES, baseline="sports_edge_original"):
    """Descriptive evaluation of first observations only; never select revisions.

    Settlement input must come from the existing official result contract.
    This function validates shape/timing, not provider authenticity. Missing
    actual start, result evidence or completed outcome prevents grading.
    """
    now = utc(as_of)
    universe = {}
    for f in fixtures:
        _fixture(f)
        mid = f["match_id"]
        if mid in universe:
            raise ValueError("duplicate census identity")
        if utc(f["census_observed_at_utc"]) > now:
            raise ValueError("future census")
        universe[mid] = f
    observed = {}
    for row in first:
        mid, source = row["match_id"], row["source"]
        key = source, mid
        if key in observed or mid not in universe or source not in sources:
            raise ValueError("duplicate or unknown observation")
        if row["fixture"] != universe[mid]:
            raise ValueError("census drift")
        received = utc(row["received_at_utc"])
        if received > now or received > _fixture(universe[mid]) - CUTOFF:
            raise ValueError("future or late observation")
        observed[key] = row
    settled = {}
    for mid, s in settlements.items():
        if mid not in universe:
            raise ValueError("unknown settlement identity")
        if s.get("status") != "COMPLETED":
            continue
        f = universe[mid]
        if (s.get("winner_id") not in (f["player1_id"], f["player2_id"])
                or not _nonempty(s.get("official_evidence_ref")) or not s.get("actual_start_utc")):
            continue
        actual = utc(s["actual_start_utc"])
        if not actual < utc(s["result_observed_at_utc"]) <= now:
            continue
        settled[mid] = (s["winner_id"], actual)

    def point(row, mid):
        winner, actual = settled[mid]
        if utc(row["received_at_utc"]) > actual - CUTOFF:
            return None
        rec = row["forecast"]
        p = rec.get("p_player1")
        if p is not None and rec["player1_id"] != universe[mid]["player1_id"]:
            p = 1-p
        return (int(rec["predicted_winner_id"] == winner), p,
                int(winner == universe[mid]["player1_id"]))

    reports = []
    equal_coverage = []
    strata = [(tier, draw) for tier in ("ALL", "ATP_TOUR", "CHALLENGER")
              for draw in ("ALL", "MAIN_DRAW", "QUALIFYING")]
    for tier, draw in strata:
        ids = {mid for mid, f in universe.items()
               if (tier == "ALL" or f["tour"] == tier)
               and (draw == "ALL" or f["draw"] == draw)}
        for source in sources:
            rows = {mid:r for (src,mid),r in observed.items() if src == source and mid in ids}
            for cls in CLASSES:
                selected = {mid:r for mid,r in rows.items() if r["forecast"]["market_class"] == cls}
                values = {}
                for mid,r in selected.items():
                    if mid in settled:
                        p = point(r,mid)
                        if p is not None:
                            values[mid] = p
                common, base_values, probability_common, probability_base = [], [], [], []
                paired_classes = {}
                paired_rows = {}
                for mid,p in sorted(values.items()):
                    other = observed.get((baseline,mid))
                    if source != baseline and other:
                        bp = point(other,mid)
                        if bp is not None:
                            common.append(p)
                            base_values.append(bp)
                            bc = other["forecast"]["market_class"]
                            paired_classes[bc] = paired_classes.get(bc, 0) + 1
                            paired_rows.setdefault(bc, []).append((mid, p, bp))
                            if p[1] is not None and bp[1] is not None:
                                probability_common.append(p)
                                probability_base.append(bp)
                uncertainty = {}
                paired_curves = {}
                for bc, pairs in paired_rows.items():
                    common_probs = [(p, bp) for _, p, bp in pairs
                                    if p[1] is not None and bp[1] is not None]
                    n = len(common_probs)
                    paired_curves[bc] = {
                        "population": "identical_settled_timely_probability_fixtures",
                        "matched_n": n, "settled_census_n": len(ids & settled.keys()),
                        "source": risk_coverage([p for p, _ in common_probs], denominator=n),
                        "baseline": risk_coverage([bp for _, bp in common_probs], denominator=n),
                    }
                    differences = {"accuracy": [], "brier": [], "log_loss": []}
                    for mid, p, bp in pairs:
                        cluster = (universe[mid]["tournament_id"] + ":" +
                                   _fixture(universe[mid]).date().isoformat()[:7])
                        differences["accuracy"].append((cluster, p[0] - bp[0]))
                        if p[1] is not None and bp[1] is not None:
                            differences["brier"].append(
                                (cluster, (p[1]-p[2])**2 - (bp[1]-bp[2])**2))
                            differences["log_loss"].append(
                                (cluster, _logloss(p[1], p[2]) - _logloss(bp[1], bp[2])))
                    uncertainty[bc] = {
                        name: {"n": len(values), "delta": _mean([v for _, v in values]),
                               "ci95": _cluster_ci(values),
                               "clusters": len({c for c, _ in values})}
                        for name, values in differences.items()
                    }
                explicitly_selected = [mid for mid, r in selected.items()
                                       if r["forecast"].get("selected_at_publication") is True]
                selection_known = sum(type(r["forecast"].get("selected_at_publication")) is bool
                                      for r in selected.values())
                reports.append({"tour":tier,"draw":draw,"source":source,"market_class":cls,
                                "eligible":len(ids),"captured":len(selected),
                                "missing":len(ids)-len(selected),
                                "coverage":len(selected)/len(ids) if ids else None,
                                "own":_metrics(list(values.values())),
                                "published_selection": {
                                    "known_flags": selection_known,
                                    "unknown_flags": len(selected)-selection_known,
                                    "explicitly_selected": len(explicitly_selected),
                                    "metrics": _metrics([values[mid] for mid in explicitly_selected
                                                         if mid in values]),
                                },
                                "missing_evidence": {
                                    "no_source_capture": len(ids)-len(rows),
                                    "other_market_class": len(rows)-len(selected),
                                    "captured_without_probability": sum(
                                        r["forecast"].get("p_player1") is None
                                        for r in selected.values()),
                                    "without_valid_completed_settlement": len(ids-settled.keys()),
                                    "first_without_declared_publication": sum(
                                        r["forecast"].get("published_at_utc") is None
                                        for r in selected.values()),
                                },
                                "settled_census_risk_coverage": risk_coverage(
                                    list(values.values()), denominator=len(ids & settled.keys())),
                                "paired_probability_risk_coverage": paired_curves,
                                "actual_start_late":sum(1 for mid,r in selected.items() if mid in settled and point(r,mid) is None),
                                "common_source":_metrics(common),"common_baseline":_metrics(base_values),
                                "common_probability_source":_metrics(probability_common),
                                "common_probability_baseline":_metrics(probability_base),
                                "paired_baseline_classes":paired_classes,
                                "paired_uncertainty_by_baseline_class": uncertainty,
                                "average_verified_odds":None})
                if source == baseline:
                    continue
                # Equal absolute volumes on the same pre-result census. Rank only
                # genuine probabilities, never outcomes or trust scores. The
                # top-k exercise is descriptive, not a live threshold claim.
                candidates = {mid:r for mid,r in selected.items()
                              if r["forecast"].get("p_player1") is not None}
                for base_class in CLASSES:
                    b_candidates = {mid:r for (src,mid),r in observed.items()
                                    if src == baseline and mid in ids
                                    and r["forecast"]["market_class"] == base_class
                                    and r["forecast"].get("p_player1") is not None}
                    def rank(rows):
                        return sorted(rows, key=lambda mid: (-max(rows[mid]["forecast"]["p_player1"],
                                                                 1-rows[mid]["forecast"]["p_player1"]),mid))
                    for fraction in (0.25, 0.4, 0.5, 1.0):
                        k = int(len(ids)*fraction)
                        feasible = k > 0 and len(candidates) >= k and len(b_candidates) >= k
                        a_ids = rank(candidates)[:k] if feasible else []
                        b_ids = rank(b_candidates)[:k] if feasible else []
                        def points(chosen, rows):
                            return [p for mid in chosen if mid in settled
                                    for p in [point(rows[mid],mid)] if p is not None]
                        equal_coverage.append({"tour":tier,"draw":draw,"source":source,"market_class":cls,
                                               "baseline_class":base_class,"target_fraction":fraction,
                                               "eligible":len(ids),"requested_k":k,"feasible":feasible,
                                               "source_selected":len(a_ids),"baseline_selected":len(b_ids),
                                               "selection_overlap":len(set(a_ids)&set(b_ids)),
                                               "source_own":_metrics(points(a_ids,candidates)),
                                               "baseline_own":_metrics(points(b_ids,b_candidates)),
                                               "descriptive_rank_only":True})
    return {"reports":reports,"equal_coverage":equal_coverage,"independent_clock_verified":False,
            "official_settlement_authenticity_verified":False,
            "activation_eligible":False}


def reconcile_bands(bands, threshold_index):
    """Arithmetic only: input rates are displayed percentages, rounded to .1."""
    if not 0 <= threshold_index < len(bands):
        raise ValueError("invalid band index")
    total = sum(n for n, rate in bands)
    selected = bands[threshold_index:]
    high = sum(n for n, rate in selected)
    if any(type(n) is not int or n <= 0 or not 0 <= rate <= 100 for n, rate in bands):
        raise ValueError("invalid band")
    counts = []
    for n, rate in selected:
        center = Fraction(str(rate))
        lower, upper = center - Fraction(1,20), center + Fraction(1,20)
        possible = [k for k in range(n+1) if lower <= Fraction(100*k,n) <= upper]
        if not possible:
            raise ValueError("displayed percentage impossible")
        counts.append((min(possible),max(possible)))
    return {"total":total,"selected":high,"share":high/total,
            "weighted_rate_pct":sum(n*rate for n,rate in selected)/high,
            "possible_rate_min_pct":100*sum(x[0] for x in counts)/high,
            "possible_rate_max_pct":100*sum(x[1] for x in counts)/high}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    capture = actions.add_parser("receive", help="Authorized local original observation only")
    capture.add_argument("--db", type=Path, required=True)
    for name in ("record","fixture","grant"):
        capture.add_argument("--"+name,type=Path,required=True)
    report = actions.add_parser("score")
    report.add_argument("--db",type=Path,required=True)
    report.add_argument("--fixtures",type=Path,required=True)
    report.add_argument("--settlements",type=Path,required=True)
    report.add_argument("--as-of",required=True)
    actions.add_parser("pronocast-aggregate")
    args = parser.parse_args(argv)
    if args.action == "pronocast-aggregate":
        result = reconcile_bands([(2242,54.4),(1844,64.3),(1378,73.9),(1199,89.7)],2)
    elif args.action == "receive":
        result = ObservationStore(args.db).receive(args.record.read_bytes(),
                                                   _decode(args.fixture.read_bytes()),
                                                   _decode(args.grant.read_bytes()))
    else:
        store = ObservationStore(args.db)
        result = score(json.loads(args.fixtures.read_bytes()), store.first(),
                       _decode(args.settlements.read_bytes()), as_of=args.as_of)
        result["publication_inventory"] = store.publication_inventory(as_of=args.as_of)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
