"""Strict, inactive acceptance wrapper for the frozen reliability research.

Does not change the V1 research score, original forecasts or selections.
An exact hash is integrity evidence, not an authenticated independent receipt.
"""
import json
from datetime import timedelta
from hashlib import sha256

from sports_edge_tennis.validation import a1_reliability_shadow as r
from sports_edge_tennis.validation.forward_receipt_store import _instant


def validate_bound_reliability_capture(capture, *, original_bytes,
                                       original_sha256, forecast_kind):
    """Recompute V1 and bind to an exact immutable original forecast envelope.

    Only the local research envelope contract is supported; no formal
    prospective activation or claimed feature lineage from an external caller.
    """
    r.validate_capture(capture)
    try:
        feature = r.ReliabilityFeatures(**capture["feature_snapshot"])
        rebuilt = r.build_capture(feature, captured_at=capture["captured_at"])
    except (TypeError, ValueError, KeyError) as exc:
        raise r.ReliabilityValidationError("invalid feature snapshot") from exc
    if r.canonical_json(rebuilt) != r.canonical_json(capture):
        raise r.ReliabilityValidationError("capture differs from recomputed frozen score")
    if feature.qualifying:
        raise r.ReliabilityValidationError("qualifying outside research scope")
    if not isinstance(original_bytes, bytes) or sha256(original_bytes).hexdigest() != original_sha256:
        raise r.ReliabilityValidationError("original payload digest mismatch")
    original = json.loads(original_bytes)
    if original.get("schema") != "forward_original_v1":
        raise r.ReliabilityValidationError("unsupported original schema")
    captured = _instant(capture["captured_at"])
    issued = _instant(original["original_issued_at_utc"])
    start = _instant(original["scheduled_start_at_utc"])
    if (feature.match_id != original.get("canonical_match_id")
            or _instant(feature.match_start_at) != start
            or feature.tour != original.get("tour")
            or not _instant(feature.prediction_as_of) <= issued <= captured
            or captured > start - timedelta(minutes=30)):
        raise r.ReliabilityValidationError("original identity or capture timing mismatch")
    if forecast_kind == "ORIGINAL_A1":
        if "candidate_decision" in original:
            raise r.ReliabilityValidationError("corrected evidence cannot masquerade as original")
        predictions = [json.loads(row[0]) for row in original["agents_json"]]
        rows = [row for row in predictions if row["agent_code"] == "AGENT_1_SURFACE_ELO"]
        if len(rows) != 1 or rows[0]["prediction_status"] != "PREDICTED":
            raise r.ReliabilityValidationError("missing original A1 prediction")
        row = rows[0]
        side = row["selection_side"]
        probability = float(row["win_probability"])
        p1 = probability if side == 1 else 1 - probability
        if (side not in (1, 2) or feature.prediction_id != row["prediction_id"]
                or _instant(feature.prediction_as_of) != _instant(row["as_of"])
                or side != feature.a1_selected_side
                or abs(p1 - feature.a1_probability_p1) > 1e-12):
            raise r.ReliabilityValidationError("original forecast probability or side mismatch")
    else:
        # The synthetic corrected envelope does not carry an independent
        # corrected prediction row identifier. Do not invent one.
        raise r.ReliabilityValidationError("corrected runtime forecast binding unavailable")
    return {
        "classification": "LOCAL_RESEARCH_CAPTURE_ONLY",
        "capture_id": capture["capture_id"],
        "original_sha256": original_sha256,
        "forecast_kind": forecast_kind,
        "capture_deadline": (start - timedelta(minutes=30)).isoformat(),
        "independent_receipt_verified": False,
        "eligible_for_prospective_evidence": False,
        "can_promote": False,
    }


def settle_bound_reliability_capture(capture, *, winner_side, settled_at, **binding):
    validate_bound_reliability_capture(capture, **binding)
    if _instant(settled_at) < _instant(capture["feature_snapshot"]["match_start_at"]):
        raise r.ReliabilityValidationError("settlement predates scheduled start")
    if type(winner_side) is not int:
        raise r.ReliabilityValidationError("winner_side must be integer")
    return r.settle_capture(capture, winner_side=winner_side, settled_at=settled_at)
