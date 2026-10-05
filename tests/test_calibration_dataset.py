"""Offline calibration dataset loader + artifact integrity tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "ops" / "calibration_evaluation.py"
ARTIFACT = REPO / "artifacts" / "calibration" / "model_pick_calibration.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("calibration_evaluation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(**overrides):
    base = {
        "picked_at": "2026-09-30T10:00:00+00:00",
        "manila_date": "2026-09-30",
        "match_id": 1,
        "aligned_probability": "0.800000",
        "aligned_pick_type": "MANAGER_CONSENSUS",
        "tier": "ATP_TOUR",
        "surface": "HARD",
        "a1_prob": "0.82",
        "a2_prob": "0.78",
        "a3_status": "ABSTAINED",
        "result_status": "WON",
        "is_value_bet": False,
    }
    base.update(overrides)
    return base


def test_loader_keeps_only_terminal_rows(tmp_path: Path):
    ledger = {
        "data": [
            _row(match_id=1, result_status="WON"),
            _row(match_id=2, result_status="LOST"),
            _row(match_id=3, result_status="PENDING"),
            _row(match_id=4, result_status="VOID"),
            _row(match_id=5, aligned_probability=None, result_status="WON"),
        ]
    }
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")

    module = _load_module()
    rows = module._load(str(path))
    assert {r["match_id"] for r in rows} == {1, 2}
    assert all(r["raw"] is not None for r in rows)


def test_artifact_exists_and_is_research_only():
    assert ARTIFACT.exists(), "expected committed calibration research artifact"
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert set(artifact) >= {"git_sha", "dataset", "combined", "manager", "wide", "note"}
    assert artifact["dataset"]["hash"] and len(artifact["dataset"]["hash"]) == 64
    assert artifact["dataset"]["rows_scored"] == (
        artifact["dataset"]["manager_n"] + artifact["dataset"]["wide_n"]
    )
    assert "Not wired" in artifact["note"]
    for group in ("combined", "manager", "wide"):
        assert artifact[group]["best_method"] in {"raw", "platt", "isotonic"}
        assert set(artifact[group]["mapping"]) >= {"0.70", "0.80", "0.90"}
