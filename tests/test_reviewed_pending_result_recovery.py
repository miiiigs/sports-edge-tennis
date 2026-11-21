from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sports_edge_tennis.validation.reviewed_pending_result_recovery import (
    ReviewedPendingRecoveryError,
    load_packet,
    utc,
)


def test_packet_is_exactly_four_unique_reviewed_cases():
    root = Path(__file__).resolve().parents[1]
    payload, proof = load_packet(
        root
        / "research"
        / "pending_result_reconciliation"
        / "reviewed_public_recovery_20261119.json"
    )
    assert len(payload["cases"]) == 4
    assert len({int(c["match_id"]) for c in payload["cases"]}) == 4
    assert len(proof) == 64
    assert {c["result"]["outcome_status"] for c in payload["cases"]} == {
        "COMPLETED",
        "CANCELLED",
    }


def test_packet_completed_and_cancelled_semantics_are_fail_closed():
    root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (
            root
            / "research"
            / "pending_result_reconciliation"
            / "reviewed_public_recovery_20261119.json"
        ).read_text(encoding="utf-8")
    )
    by_id = {int(c["match_id"]): c for c in payload["cases"]}
    assert by_id[130903]["result"]["winner_side"] == 2
    assert by_id[130903]["expected"]["round_code"] == "semifinal"
    assert by_id[130903]["expected"]["qualifying_flag"] is False
    for match_id in (133361, 133347, 133978):
        assert by_id[match_id]["result"]["winner_side"] is None
        assert by_id[match_id]["result"]["outcome_status"] == "CANCELLED"


def test_utc_rejects_naive_timestamp():
    with pytest.raises(ReviewedPendingRecoveryError, match="timezone_aware"):
        utc("2026-11-19T06:42:00")
    assert utc("2026-11-19T06:42:00Z") == datetime(
        2026, 11, 19, 6, 42, tzinfo=UTC
    )
