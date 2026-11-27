"""Build the reproducible manifest for the Agent 1 selective-performance study."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "research" / "evidence"
OUT = EVIDENCE / "a1_selective_performance_manifest_20261126.json"

FILES = [
    "docs/research/A1_SELECTIVE_PERFORMANCE_VALIDATION_20261126.md",
    "docs/research/evidence/a1_selective_population_20261126.csv",
    "docs/research/evidence/a1_target_census_20261126.csv",
    "docs/research/evidence/a1_selective_ranked_populations_20261126.csv",
    "docs/research/evidence/a1_selective_risk_coverage_20261126.csv",
    "docs/research/evidence/a1_selective_population_meta_20261126.json",
    "docs/research/evidence/a1_selective_performance_20261126.json",
    "docs/research/evidence/a1_identity_integrity_20261126.json",
    "docs/research/evidence/a1_migration_0025_isolated_20261126.json",
    "ops/validation/a1_selective_population_export_20261126.py",
    "ops/validation/a1_selective_performance_experiment_20261126.py",
    "ops/validation/a1_identity_integrity_audit_20261126.py",
    "src/sports_edge_tennis/validation/a1_selective_performance.py",
    "tests/test_a1_selective_performance.py",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    performance = json.loads(
        (EVIDENCE / "a1_selective_performance_20261126.json").read_text(encoding="utf-8")
    )
    integrity = json.loads(
        (EVIDENCE / "a1_identity_integrity_20261126.json").read_text(encoding="utf-8")
    )
    files = []
    for relative in FILES:
        path = ROOT / relative
        if not path.exists():
            raise SystemExit(f"missing artifact: {relative}")
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    selected = {
        "40": performance["coverage_experiment"]["corrected"]["points"]["0.40"],
        "45": performance["coverage_experiment"]["corrected"]["points"]["0.45"],
        "50": performance["coverage_experiment"]["corrected"]["points"]["0.50"],
    }
    manifest = {
        "version": "A1_SELECTIVE_PERFORMANCE_MANIFEST_V1",
        "generated_at": datetime.now(UTC).isoformat(),
        "base_commit": "607ea74a859af473d1295ddf72aee3d7c22fcb11",
        "model_version": "agent1_surface_elo_v1",
        "feature_version": "agent1_surface_elo_inputs_v1",
        "config_version": "agent1_surface_elo_config_v1",
        "reference_policy": "pre-v2 permissive-equivalent resolver for this experiment; identity_reference_pit_v3 implemented but not applied",
        "evaluation_cutoff": "2026-11-25T07:20:00Z",
        "population": {
            "targets": 3776,
            "settled": 3223,
            "atp": 577,
            "challenger": 2646,
            "development_end": "2026-10-31",
            "holdout_rows": 1025,
        },
        "headline_corrected": {
            key: {
                "selection_count": value["selection_count"],
                "wins": value["wins"],
                "losses": value["losses"],
                "accuracy": value["accuracy"],
                "wilson95": value["wilson95"],
                "brier": value["brier"],
                "log_loss": value["log_loss"],
            }
            for key, value in selected.items()
        },
        "four_way_top800": performance["four_way_top800"]["wins"],
        "risk_coverage": {
            key: {
                "aurc": value["aurc"],
                "augrc_paper": value["augrc_paper"],
            }
            for key, value in performance["risk_coverage"].items()
        },
        "population_reconciliation": {
            "target_churn_0720_to_1100": integrity["target_universe_reconciliation"]["churn_0720_to_1100"][
                "targets_added"
            ],
            "settlement_added_3223_to_3271": len(
                integrity["settlement_snapshots"]["added_3223_to_3271"]
            ),
            "settlement_added_3271_to_3284": len(
                integrity["settlement_snapshots"]["added_3271_to_3284"]
            ),
            "five_record_discrepancy": integrity["resolution_census"]["five_record_discrepancy"],
        },
        "files": files,
        "validation": performance["validation"],
        "can_promote": False,
        "production_changed": False,
        "migration_applied": False,
        "database_writes": False,
        "provider_calls": False,
        "note": (
            "This manifest binds the exact population, code, evidence and documentation used "
            "for the retrospective selective-performance study. It is not a production freeze."
        ),
    }
    OUT.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
