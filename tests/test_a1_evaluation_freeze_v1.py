"""Protect committed research inputs and measurement source files.

This is a byte-integrity tripwire, not an independent signature. A legitimate
methodology revision needs separate versioned evidence and review; never
silently rewrite the V1 manifest to turn CI green.
"""
from hashlib import sha1
import json
from pathlib import Path

from sports_edge_tennis.validation.prospective_policy_registry import POLICIES
from sports_edge_tennis.agents.agent1_surface_elo import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/research/evidence/a1_evaluation_freeze_v1.json"


def test_frozen_evaluation_source_inventory():
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["version"] == "A1_EVALUATION_FREEZE_V1"
    assert manifest["anchor_commit"] == "d930faac1e070b758a5c8c552347c4282eb8371b"
    for path, expected in manifest["git_blob_sha1"].items():
        payload = (ROOT / path).read_bytes()
        actual = sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
        assert actual == expected, f"Frozen V1 source changed: {path}"
    assert manifest["can_promote"] is False


def test_primary_secondary_frozen_rules_match_registered_policy():
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["candidate_thresholds"] == {"B": "0.668060", "D": "0.648708"}
    assert {c: POLICIES[c].threshold for c in ("B","D")} == manifest["candidate_thresholds"]
    assert DEFAULT_CONFIG.model_version == manifest["model_version"]
    assert DEFAULT_CONFIG.feature_version == manifest["feature_version"]
    assert DEFAULT_CONFIG.config_version == manifest["config_version"]
    assert DEFAULT_CONFIG.probability_decimal_places == 6
    assert DEFAULT_CONFIG.tie_selection_policy == "PLAYER_1"
