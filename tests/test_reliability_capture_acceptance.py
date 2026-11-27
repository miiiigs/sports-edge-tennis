import json
from hashlib import sha256

import pytest

from sports_edge_tennis.validation import a1_reliability_shadow as r
from sports_edge_tennis.validation.reliability_capture_acceptance import (
    settle_bound_reliability_capture, validate_bound_reliability_capture,
)
from tests.test_a1_reliability_shadow import features


def original():
    row = {"agent_code":"AGENT_1_SURFACE_ELO","prediction_id":1,
           "prediction_status":"PREDICTED","selection_side":1,
           "win_probability":0.67,"as_of":"2026-11-01T00:00:00+00:00"}
    payload = json.dumps({
        "schema":"forward_original_v1","canonical_match_id":2,
        "original_issued_at_utc":"2026-11-01T00:01:00+00:00",
        "scheduled_start_at_utc":"2026-11-01T02:00:00+00:00",
        "tour":"CHALLENGER","agents_json":[[json.dumps(row),"{}"]],
    }).encode()
    return {"original_bytes":payload,"original_sha256":sha256(payload).hexdigest(),
            "forecast_kind":"ORIGINAL_A1"}


def capture(at="2026-11-01T00:05:00+00:00"):
    return r.build_capture(features(), captured_at=at)


def test_binding_and_separate_settlement():
    record = capture()
    result = validate_bound_reliability_capture(record, **original())
    assert result["independent_receipt_verified"] is False
    assert result["eligible_for_prospective_evidence"] is False
    settlement = settle_bound_reliability_capture(
        record,winner_side=1,settled_at="2026-11-01T03:00:00+00:00",**original())
    assert settlement["a1_correct"] is True


def test_rehashed_but_changed_score_cannot_pass():
    record = capture()
    record["selection_score"] = 0.99
    record["capture_id"] = r.sha256_payload({k:v for k,v in record.items() if k!="capture_id"})
    with pytest.raises(r.ReliabilityValidationError,match="recomputed"):
        validate_bound_reliability_capture(record,**original())


@pytest.mark.parametrize("at",["2026-11-01T00:00:00+00:00","2026-11-01T01:31:00+00:00"])
def test_outside_original_receipt_deadline_rejected(at):
    with pytest.raises(r.ReliabilityValidationError,match="timing"):
        validate_bound_reliability_capture(capture(at),**original())


def test_no_corrected_relabelling_or_early_settlement():
    with pytest.raises(r.ReliabilityValidationError,match="binding unavailable"):
        validate_bound_reliability_capture(capture(),**{**original(),"forecast_kind":"CORRECTED_A1"})
    with pytest.raises(r.ReliabilityValidationError,match="predates"):
        settle_bound_reliability_capture(capture(),winner_side=1,
            settled_at="2026-11-01T01:00:00+00:00",**original())
