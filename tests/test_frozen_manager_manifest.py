"""The registered Manager C freeze must remain byte-identical.

This integration is not a model activation. These assertions pin the archived
envelope attestation, the RFC 3161 token and the frozen policy constants so an
unrelated change cannot silently alter the prospective experiment.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from sports_edge_tennis.validation.manager_c_forward_feature_ablation import (
    GROUPS,
    TARGETS,
    VERSION as ABLATION_VERSION,
)
from sports_edge_tennis.validation.manager_c_original_source import (
    VERSION as MANIFEST_VERSION,
)

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "docs/research"
ATTESTATION = json.loads(
    (RESEARCH / "manager_c_frozen_envelope_20261125_attestation.json").read_text()
)
VERIFICATION = json.loads(
    (RESEARCH / "manager_c_frozen_envelope_20261125_verification.json").read_text()
)


def test_archived_timestamp_token_is_unchanged():
    token = RESEARCH / ATTESTATION["token_path"]
    assert hashlib.sha256(token.read_bytes()).hexdigest() == (
        "1d27027d824ee1c0ec9513c08019730bb737dfe67ec949cd9189d5c8572278ab"
    )
    assert (ATTESTATION["token_sha256"] == VERIFICATION["tsr_sha256"]
            == hashlib.sha256(token.read_bytes()).hexdigest())


def test_envelope_attestation_matches_verification_record():
    assert ATTESTATION["envelope_file_sha256"] == (
        "bb54892acf5b661f72eab47c7abdcd5d78e6868520071476f308364283c8c74c"
    )
    assert ATTESTATION["envelope_file_sha256"] == VERIFICATION["envelope_file_sha256"]
    assert ATTESTATION["envelope_sha256_field"] == (
        VERIFICATION["declared_envelope_sha256"]
    )
    assert ATTESTATION["message_imprint_hex"] == VERIFICATION["envelope_file_sha256"]
    assert ATTESTATION["gen_time_utc"] == "2026-11-24T09:11:36Z"
    assert VERIFICATION["token_signature_verified"] is True
    assert VERIFICATION["imprint_equals_envelope_file_sha256"] is True
    assert VERIFICATION["timestamp_precedes_freeze"] is True


def test_frozen_policy_constants_match_code():
    policy = VERIFICATION["frozen_policy"]
    assert MANIFEST_VERSION == policy["manifest_version"]
    assert ABLATION_VERSION == policy["ablation_version"]
    assert tuple(policy["groups"]) == GROUPS
    assert tuple(policy["targets"]) == TARGETS
    assert policy["policy_matches_code"] is True
    assert policy["train_targets"] == 822
    assert policy["validation_targets"] == 535


def test_timestamp_precedes_registered_boundaries():
    gen_time = datetime.fromisoformat(
        ATTESTATION["gen_time_utc"].replace("Z", "+00:00")
    )
    freeze = datetime.fromisoformat(VERIFICATION["freeze_at"])
    assert gen_time.astimezone(UTC) < freeze.astimezone(UTC)
    evaluation_end = datetime.fromisoformat(VERIFICATION["evaluation_end"])
    settlement = datetime.fromisoformat(VERIFICATION["settlement_as_of"])
    assert evaluation_end < settlement
