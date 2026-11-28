"""Build the candidate-freeze manifest for PR #388.

Read-only with respect to the database. This manifest records what was verified at
the committed head; it never activates, migrates or deploys anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "research" / "evidence"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default=str(EVIDENCE / "a1_candidate_freeze_manifest_20261126.json"),
    )
    parser.add_argument("--source-commit", default=None)
    args = parser.parse_args(argv)

    manifest = json.loads((ROOT / "db/migrations/manifest.json").read_text(encoding="utf-8"))
    versions = [m["version"] for m in manifest["migrations"]]
    tail = [m for m in manifest["migrations"] if m["version"] in {"0023", "0024", "0025"}]
    tail_ok = [m["version"] for m in tail] == ["0023", "0024", "0025"] and versions == sorted(versions)

    readiness = json.loads(
        (EVIDENCE / "a1_identity_bridge_readiness_20261126.json").read_text(encoding="utf-8")
    )
    quarantine_path = EVIDENCE / "a1_forward_quarantine_manifest_20261126.json"
    quarantine = (
        json.loads(quarantine_path.read_text(encoding="utf-8"))
        if quarantine_path.exists()
        else None
    )
    reconciliation_path = EVIDENCE / "a1_historical_reconciliation_20261126.json"
    reconciliation = (
        json.loads(reconciliation_path.read_text(encoding="utf-8"))
        if reconciliation_path.exists()
        else None
    )
    top800_path = EVIDENCE / "a1_top800_sets_20261126.json"

    evidence_files = [
        "a1_identity_bridge_four_way_20261125.json",
        "a1_identity_bridge_equal_coverage_20261124.json",
        "a1_identity_bridge_replay_fidelity_20261124.json",
        "a1_identity_reference_policy_20261125.json",
        "a1_identity_replay_mismatch_20261125.json",
        "a1_identity_replay_mismatch_cases_20261126.json",
        "a1_identity_manager_interaction_20261125.json",
        "a1_identity_bridge_readiness_20261126.json",
        "legacy_75pct_claim_audit_20261125.json",
        "a1_forward_quarantine_manifest_20261126.json",
        "a1_historical_reconciliation_20261126.json",
        "a1_top800_sets_20261126.json",
    ]
    evidence_hashes = {
        name: sha256(EVIDENCE / name) for name in evidence_files if (EVIDENCE / name).exists()
    }

    source_commit = args.source_commit or git("rev-parse", "HEAD")
    merge_commit = git("rev-list", "--merges", "--max-count=1", "HEAD")
    merge_parents = git("show", "-s", "--format=%P", merge_commit).split()

    payload = {
        "version": "A1_CANDIDATE_FREEZE_MANIFEST_V1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_commit": source_commit,
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "pr": 388,
        "pr392_reconciled_head": "af6e09bef019ef3a5cc67273ad08094ee0b8a8a0",
        "merge_commit": merge_commit,
        "merge_parents": merge_parents,
        "can_promote": False,
        "production_changed": False,
        "production_migration_applied": False,
        "deployment_performed": False,
        "migration_chain": [
            {
                "version": entry["version"],
                "name": entry["name"],
                "up_sha256": sha256(ROOT / entry["up"]),
                "down_sha256": sha256(ROOT / entry["down"]),
                "schema_snapshot_sha256": sha256(ROOT / entry["schema_snapshot"]),
            }
            for entry in tail
        ],
        "migration_chain_contiguous_through_0025": tail_ok,
        "runtime_identity_persistence": {
            "writer": "sports_edge_tennis.data.identity_runtime.persist_identity_resolutions",
            "transactional_with_predictions": True,
            "opt_in_field": "FullSlateRunRequestV1.persist_identity_resolutions",
            "default_enabled": False,
            "tests": [
                "tests/test_identity_resolution_transactional_persistence.py",
                "tests/test_full_slate_runner.py::test_opt_in_identity_resolution_persistence_matches_predictions",
                "tests/test_full_slate_runner.py::test_default_full_slate_writes_no_identity_resolution_rows",
            ],
        },
        "historical_reconciliation": {
            "status": "COMPLETE" if reconciliation else "MISSING",
            "classification": (
                reconciliation or {}
            ).get("classification"),
            "freeze": (reconciliation or {}).get("freeze"),
            "top800_accounting": (
                reconciliation or {}
            ).get("phase2_top800_decomposition", {}).get("identity"),
        },
        "forward_quarantine": {
            "manifest": "docs/research/evidence/a1_forward_quarantine_manifest_20261126.json",
            "sha256": evidence_hashes.get("a1_forward_quarantine_manifest_20261126.json"),
            "quarantined_reissued_targets": (quarantine or {}).get(
                "quarantined_reissued_targets"
            ),
            "pre_outcome_records_not_independently_receipted": (quarantine or {}).get(
                "pre_outcome_records_not_independently_receipted"
            ),
            "admissible_independent_receipts": (quarantine or {}).get(
                "admissible_independent_receipts"
            ),
        },
        "acceptance": {
            "activation_state": readiness.get("activation_state"),
            "gates": readiness.get("activation_gates"),
            "release_requirements": readiness.get("release_requirements"),
        },
        "legacy_claim": readiness.get("workstreams", {}).get("legacy_75pct_claim"),
        "evidence_sha256": evidence_hashes,
        "scientific_freeze_recommendation": (
            "NOT_READY_FOR_ACTIVATION_FREEZE; READY_FOR_REVIEW_OF_HISTORICAL_EXPLANATION_ONLY"
        ),
        "blockers": [
            "At least 200 settled first-issued selections over at least 30 calendar days are not present.",
            "No independent wall-clock collector receipt; hand-committed captures are not sufficient.",
            "Migration 0025 is implemented but not applied to production, so reference-policy v3 is not re-measured on production.",
            "No forward ATP sample; ATP remains unresolved rather than pooled away.",
            "No new Agent 1 configuration version and no owner approval.",
            "The historical population is development-reused, not untouched validation.",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str)[:5000])
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
