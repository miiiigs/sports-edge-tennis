import json
from pathlib import Path
from sports_edge_tennis.ops.production_log_acceptance import assess_slot, summarize

EVIDENCE = Path(__file__).resolve().parents[1]/"docs/research/evidence/production_log_audit_20261128.json"


def test_actual_log_snapshot_does_not_treat_partial_as_healthy():
    report = json.loads(EVIDENCE.read_text())
    out = summarize(report["slots"])
    assert out["expected_slots"] == 85
    assert out["execution_states"] == {"PARTIAL_SLATES":65,"COMPLETION_LOG_ONLY":20}
    assert out["archive_states"] == {
        "CAPTURE_LOGGED_PERSISTENCE_UNVERIFIED":37,
        "NO_CAPTURE_LOGGED_PERSISTENCE_UNVERIFIED":48}
    assert out["activation_healthy_slots"] == 0


def test_missing_or_duplicate_start_never_healthy():
    row={"slot":"2026-11-28T12:00:00Z","starts":[],"completions":[],
         "heartbeats":[],"unavailable":[]}
    assert assess_slot(row)["state"]=="MISSING_START"
    row["starts"]=["2026-11-28T12:01:00Z"]
    assert assess_slot(row)["state"]=="MISSING_COMPLETION"
    row["starts"]*=2
    assert assess_slot(row)["state"]=="AMBIGUOUS_LOG_CARDINALITY"


def test_claimed_persistence_cannot_become_independent_evidence():
    row={"slot":"2026-11-28T12:00:00Z","starts":["2026-11-28T12:01:00Z"],
         "completions":[{"at":"2026-11-28T12:02:00Z","slates":2,"failed_slates":0}],
         "heartbeats":[{"persistent_storage_verified":True,"records_logged":1}],
         "unavailable":[]}
    result=assess_slot(row)
    assert result["state"]=="COMPLETION_LOG_ONLY"
    assert result["archive_state"]=="PERSISTENCE_CLAIM_NOT_INDEPENDENT"
    assert result["healthy_for_activation"] is False
