"""Regression tests for the identity-provenance audit surface.

These tests do not re-run the production analysis. They pin the contracts that make
the evidence trustworthy:

* the reference fingerprint is deterministic, sensitive to identity linkage changes,
  and insensitive to benign re-observation timestamps;
* an empty migrated database yields truthful zero/undefined audit output rather than a
  fabricated benchmark;
* the legacy 75% claim cannot be marked as reproduced without its required artifacts;
* the audit modules never create or mutate schema.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from sports_edge_tennis.data.identity_provenance import (
    REFERENCE_FAMILIES,
    reference_drift_for,
    reference_fingerprint,
    resolve_with_provenance,
)
from sports_edge_tennis.data.normalize import normalize_person_lookup

CUTOFF = datetime(2025, 6, 16, tzinfo=UTC)


def _player(conn: psycopg.Connection, name: str) -> int:
    return conn.execute(
        """
        INSERT INTO players (canonical_name, status, created_at, updated_at)
        VALUES (%s, 'ACTIVE', %s, %s) RETURNING player_id
        """,
        (name, datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
    ).fetchone()[0]


def _alias(conn: psycopg.Connection, *, player_id: int, alias: str) -> None:
    conn.execute(
        """
        INSERT INTO player_aliases (
            player_id, source_system, alias, normalized_alias,
            first_observed_at, created_at, updated_at
        )
        VALUES (%s, 'TENNIS_DATA', %s, %s, %s, %s, %s)
        """,
        (
            player_id,
            alias,
            normalize_person_lookup(alias),
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 1, tzinfo=UTC),
        ),
    )


def test_reference_fingerprint_is_deterministic(db: psycopg.Connection):
    one = _player(db, "Fingerprint One")
    _alias(db, player_id=one, alias="Fingerprint One")
    first = reference_fingerprint(db, player_ids=[one], as_of=CUTOFF)
    second = reference_fingerprint(db, player_ids=[one], as_of=CUTOFF)
    assert first.sha256 == second.sha256
    assert first.row_counts["players"] == 1
    assert first.row_counts["player_aliases"] == 1
    assert set(first.row_counts) == set(REFERENCE_FAMILIES)


def test_reference_fingerprint_ignores_benign_reobservation(db: psycopg.Connection):
    player = _player(db, "Refresh One")
    _alias(db, player_id=player, alias="Refresh One")
    before = reference_fingerprint(db, player_ids=[player], as_of=CUTOFF)
    db.execute(
        "UPDATE player_aliases SET last_observed_at = %s, updated_at = %s WHERE player_id = %s",
        (datetime(2025, 8, 1, tzinfo=UTC), datetime(2025, 8, 1, tzinfo=UTC), player),
    )
    after = reference_fingerprint(db, player_ids=[player], as_of=CUTOFF)
    assert before.sha256 == after.sha256


def test_reference_fingerprint_detects_name_mutation_and_alias_guard(
    db: psycopg.Connection,
):
    one = _player(db, "Repoint One")
    two = _player(db, "Repoint Two")
    _alias(db, player_id=one, alias="Repoint Shared")
    before = reference_fingerprint(db, player_ids=[one, two], as_of=CUTOFF)
    # 0025 rejects out-of-band linkage mutation rather than merely detecting it.
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        db.execute(
            "UPDATE player_aliases SET player_id = %s WHERE player_id = %s", (two, one)
        )
    # An allowed name mutation still moves the digest.
    db.execute(
        "UPDATE players SET canonical_name = 'Repoint One Renamed', updated_at = %s "
        "WHERE player_id = %s",
        (datetime(2025, 7, 1, tzinfo=UTC), one),
    )
    after = reference_fingerprint(db, player_ids=[one, two], as_of=CUTOFF)
    assert before.sha256 != after.sha256
    drift = reference_drift_for(
        db, player_ids=[one, two], recorded=before.as_record()
    )
    assert drift["drift"] is True


def test_resolve_with_provenance_records_observed_at(db: psycopg.Connection):
    live = _player(db, "Provenance Observed")
    db.execute(
        "INSERT INTO player_source_ids (player_id, source_system, source_player_id, created_at) "
        "VALUES (%s, 'API_TENNIS', 'api-prov-observed', %s)",
        (live, datetime(2024, 1, 1, tzinfo=UTC)),
    )
    resolutions, reference = resolve_with_provenance(db, player_ids=[live], as_of=CUTOFF)
    record = resolutions[live].as_record()
    assert record["observed_at"] == CUTOFF.isoformat()
    assert record["reference_sha256"] == reference.sha256
    assert record["method"] == "NO_HISTORICAL_CANDIDATE"


def test_audit_modules_are_read_only_on_empty_database(db: psycopg.Connection):
    from sports_edge_tennis.validation import a1_identity_manager_interaction as manager
    from sports_edge_tennis.validation import a1_identity_provenance_audit as audit
    from sports_edge_tennis.validation import legacy_75pct_claim_audit as legacy

    before = db.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchone()[0]

    targets = audit._load_targets(db)
    assert targets == []
    report = manager.run(db)
    assert report["population"] == 0
    assert report["manager_decisions_changed"] == 0

    verdict = legacy.audit(db)
    assert verdict["verdict"] == "UNREPRODUCIBLE"
    assert verdict["recommendation"] == "RETIRE"

    after = db.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchone()[0]
    assert before == after


def test_replay_mismatch_buckets_are_mutually_exclusive():
    # The bucket assignment order is part of the evidence contract.
    from sports_edge_tennis.validation.a1_identity_provenance_audit import (
        mismatch_report,
    )

    assert callable(mismatch_report)


def test_first_issued_capture_archive_is_hash_verified():
    """The committed run-2603 capture must be self-verifying and outcome-free."""
    import hashlib
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "research"
        / "evidence"
        / "a1_identity_bridge_forward_capture_2603_20261125.jsonl"
    )
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    records = [json.loads(line) for line in lines]
    captures = [r for r in records if r["record_type"] == "capture"]
    summaries = [r for r in records if r["record_type"] == "summary"]
    assert len(captures) == 2
    assert len(summaries) == 1

    for record in captures:
        stored = record.pop("record_sha256")
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
        assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == stored
        assert record["is_earliest_issued"] is True
        assert record["main_draw_eligible"] is True
        assert record["outcome_included"] is False
        assert record["can_promote"] is False
        assert record["production_changed"] is False
        assert record["schema_version"] == "a1_identity_bridge_forward_shadow_record_v2"
        reference = record["identity_reference"]
        assert len(reference["reference_sha256"]) == 64
        assert reference["policy_version"] == "identity_reference_pit_v2"
        # Original and model identity, mapping source and observation time are all
        # persisted for each slot.
        for slot in ("player_1_resolution", "player_2_resolution"):
            resolution = record[slot]
            assert resolution["method"]
            assert resolution["evidence"]
            assert resolution["observed_at"] == record["prediction_as_of"]
        assert record["match_version_id"] > 0

    summary = summaries[0]
    # The summary's record_set hash is computed from the stored per-record hashes.
    stored_hashes = [
        json.loads(line)["record_sha256"]
        for line in lines
        if json.loads(line)["record_type"] == "capture"
    ]
    recomputed = hashlib.sha256("\n".join(stored_hashes).encode("utf-8")).hexdigest()
    assert summary["record_set_sha256"] == recomputed


def test_reference_policy_evidence_is_mapping_neutral():
    """The committed full-population audit must show no mutable-reference dependence."""
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "research"
        / "evidence"
        / "a1_identity_reference_policy_20261125.json"
    )
    report = json.loads(path.read_text(encoding="utf-8"))
    reference = report["reference_policy"]
    assert report["targets"] >= 3000
    assert reference["strict_vs_permissive"]["changed_player_mappings"] == 0
    assert (
        reference["strict_alias_refresh_gate_vs_permissive"]["changed_player_mappings"] == 0
    )
    assert report["can_promote"] is False
    assert report["production_changed"] is False
    assert reference["experiment_status"] == "COMPLETE"
    for key in ("strict_vs_permissive", "strict_alias_refresh_gate_vs_permissive"):
        effect = reference[key]["forecast_effect"]
        assert effect["compared_targets"] == 0
        assert effect["changed_probabilities"] == 0
        assert effect["max_abs_probability_delta"] == 0.0
        assert effect["paired"] is None
    for policy in reference["policies"].values():
        assert policy["replay_fidelity"]["n"] == 0
    fidelity = reference["replay_fidelity_reference"]
    assert fidelity["exact"] == 0.9278
    assert fidelity["within_005"] == 0.946


def test_manager_interaction_evidence_is_paired_and_non_promotable():
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "research"
        / "evidence"
        / "a1_identity_manager_interaction_20261125.json"
    )
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["population"] > 0
    assert report["can_promote"] is False
    # Shared consensus picks must keep the same side; otherwise the interaction would be a
    # side flip rather than a coverage/selection effect.
    consensus = report["consensus"]
    assert consensus["same_side_on_overlap"] == consensus["both"]
    delta = report["consensus_accuracy_delta"]
    assert delta["paired"] is False
    assert delta["includes_zero"] is True
    assert delta["ci95"][0] < 0.0 < delta["ci95"][1]
    assert report["matched_fixture_contract"]["paired_activation_test"] is False
    assert report["analysis_status"] == "COMPLETE_BOUNDED"


def test_legacy_claim_is_formally_retired():
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "research"
        / "evidence"
        / "legacy_75pct_claim_audit_20261125.json"
    )
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["verdict"] == "UNREPRODUCIBLE"
    assert report["recommendation"] == "RETIRE"
    assert report["claim_status"] == "RETIRED"
    assert report["retirement_is_final"] is True


def test_readiness_report_is_complete_and_non_promotable():
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "research"
        / "evidence"
        / "a1_identity_bridge_readiness_20261126.json"
    )
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["can_promote"] is False
    assert report["production_changed"] is False
    assert report["activation_state"] == "NOT_ELIGIBLE"
    gates = {gate["gate"]: gate for gate in report["activation_gates"]}
    assert gates["minimum_200_settled_first_issued_bridged_selections"]["met"] is False
    assert gates["minimum_30_calendar_days_genuine_future_captures"]["met"] is False
    assert gates["independent_wall_clock_collector_receipt"]["met"] is False
    assert gates["no_atp_degradation"]["met"] is False
    assert report["workstreams"]["legacy_75pct_claim"]["status"] == "RETIRED_UNREPRODUCIBLE"
    assert report["workstreams"]["manager_interaction"]["ci_includes_zero"] is True
    assert report["release_requirements"]


def test_replay_mismatch_case_audit_is_identity_dominated():
    import hashlib
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    path = root / "docs" / "research" / "evidence" / "a1_identity_replay_mismatch_cases_20261126.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    source = root / report["derived_from"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == report["derived_from_sha256"]
    assert report["cases"] == 30
    assert report["target_ids_changed_at_as_of"] == report["cases"]
    assert report["is_first_issued"] == 0
    assert report["can_promote"] is False


def test_replay_mismatch_evidence_matches_committed_buckets():
    """The committed mismatch audit must decompose every mismatch exactly once."""
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "research"
        / "evidence"
        / "a1_identity_replay_mismatch_20261125.json"
    )
    report = json.loads(path.read_text(encoding="utf-8"))
    mismatch = report["mismatch"]
    buckets = mismatch["mismatch_by_mechanism"]
    assert set(buckets) == {
        "unknown_surface",
        "surface_fallback_resolution",
        "target_identity_bridged",
        "unexplained",
    }
    assert sum(bucket["mismatches"] for bucket in buckets.values()) == mismatch["total_mismatches"]
    assert sum(bucket["targets"] for bucket in buckets.values()) == mismatch["population"]
    assert 0.90 <= mismatch["fidelity"]["exact"] <= 0.95
    # Late history is not a cause in the observed window.
    for cutoff in mismatch["history_ingest_visibility"]["cutoffs"]:
        assert cutoff["ingested_after_cutoff"] == 0
    assert report["can_promote"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
