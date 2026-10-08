"""Agent 3 support observability tests (append-only, no threshold changes)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from sports_edge_tennis.agents.agent3_intransitivity import (
    DEFAULT_CONFIG as A3_CONFIG,
    Agent3HistorySnapshot,
    OutcomeObservation,
    PlayerHistorySnapshot,
    PredictionRequest,
    predict,
)
from sports_edge_tennis.data.a3_support_diagnostics import (
    build_a3_support_diagnostic,
    record_a3_support_diagnostic,
)

REPO = Path(__file__).resolve().parents[1]
SNAP = REPO / "db" / "schema" / "0020_a3_support_diagnostics.sql"
MANIFEST = REPO / "db" / "migrations" / "manifest.json"

AS_OF = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _target(match_id: int = 99, p1: int = 1, p2: int = 2):
    return SimpleNamespace(match_id=match_id, player_1_id=p1, player_2_id=p2)


def test_a3_locked_thresholds_unchanged():
    assert A3_CONFIG.direct_raw_support == 2
    assert A3_CONFIG.direct_effective_support == Decimal("2.5")
    assert A3_CONFIG.network_edge_raw_support == 1
    assert A3_CONFIG.network_edge_effective_support == Decimal("1.0")
    assert A3_CONFIG.normal_network_shared_support == 5
    assert A3_CONFIG.network_only_shared_support == 8
    assert A3_CONFIG.signal_floor == Decimal("0.12")
    assert A3_CONFIG.activation_probability_floor == Decimal("0.530000")
    assert A3_CONFIG.config_version == "agent3_intransitivity_config_v1"


def test_build_diagnostic_unresolved_identity_empty_graph():
    snapshot = Agent3HistorySnapshot()
    result = predict(
        PredictionRequest(
            player_1_id=1, player_2_id=2, surface="HARD", history_snapshot=snapshot, as_of=AS_OF
        )
    )
    diagnostic = build_a3_support_diagnostic(
        target=_target(), snapshot=snapshot, result=result, as_of=AS_OF
    )
    assert diagnostic.final_status == "ABSTAINED"
    assert diagnostic.identity_status == "LIVE_UNRESOLVED"
    assert diagnostic.raw_shared_opponents == 0
    assert diagnostic.eligible_shared_opponents == 0
    assert diagnostic.gate_failed == "INSUFFICIENT_MATCHUP_SUPPORT"
    assert diagnostic.history_count_player1 == 0


def test_build_diagnostic_resolved_identity_with_history():
    snapshot = Agent3HistorySnapshot(
        players={
            1: PlayerHistorySnapshot(
                player_id=1, all_observations=(OutcomeObservation(observed_at=AS_OF, value=1),)
            ),
            2: PlayerHistorySnapshot(
                player_id=2, all_observations=(OutcomeObservation(observed_at=AS_OF, value=-1),)
            ),
        }
    )
    result = predict(
        PredictionRequest(
            player_1_id=1, player_2_id=2, surface="HARD", history_snapshot=snapshot, as_of=AS_OF
        )
    )
    diagnostic = build_a3_support_diagnostic(
        target=_target(), snapshot=snapshot, result=result, as_of=AS_OF
    )
    assert diagnostic.identity_status == "RESOLVED"
    assert diagnostic.history_count_player1 == 1
    assert diagnostic.history_count_player2 == 1


def test_record_diagnostic_is_deduped_and_append_only(db: psycopg.Connection):
    snapshot = Agent3HistorySnapshot()
    result = predict(
        PredictionRequest(
            player_1_id=1, player_2_id=2, surface="HARD", history_snapshot=snapshot, as_of=AS_OF
        )
    )
    diagnostic = build_a3_support_diagnostic(
        target=_target(), snapshot=snapshot, result=result, as_of=AS_OF
    )

    assert record_a3_support_diagnostic(db, diagnostic) is True
    assert record_a3_support_diagnostic(db, diagnostic) is False
    count = db.execute("SELECT count(*) FROM a3_support_diagnostics").fetchone()[0]
    assert count == 1

    row = db.execute(
        "SELECT match_id, identity_status, final_status, gate_failed, "
        "raw_shared_opponents, eligible_shared_opponents FROM a3_support_diagnostics"
    ).fetchone()
    assert row == (99, "LIVE_UNRESOLVED", "ABSTAINED", "INSUFFICIENT_MATCHUP_SUPPORT", 0, 0)

    with pytest.raises(psycopg.Error):
        db.execute("UPDATE a3_support_diagnostics SET gate_failed = 'x'")
    with pytest.raises(psycopg.Error):
        db.execute("DELETE FROM a3_support_diagnostics")


def test_diagnostics_store_no_secrets(db: psycopg.Connection):
    snapshot = Agent3HistorySnapshot()
    result = predict(
        PredictionRequest(
            player_1_id=1, player_2_id=2, surface="HARD", history_snapshot=snapshot, as_of=AS_OF
        )
    )
    record_a3_support_diagnostic(
        db,
        build_a3_support_diagnostic(
            target=_target(), snapshot=snapshot, result=result, as_of=AS_OF
        ),
    )
    blob = db.execute("SELECT to_jsonb(a3_support_diagnostics)::text FROM a3_support_diagnostics").fetchone()[0]
    assert "APIkey" not in blob
    assert "api_key" not in blob.lower()


def test_0020_snapshot_and_manifest():
    def normalise(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"--.*", "", text)).strip().lower()

    ups = sorted((REPO / "db" / "migrations").glob("*.up.sql"))
    concat = "\n".join(p.read_text(encoding="utf-8") for p in ups if p.name[:4] <= "0020")
    assert normalise(concat) == normalise(SNAP.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = next(e for e in manifest["migrations"] if e["version"] == "0020")
    assert entry == {
        "version": "0020",
        "name": "a3_support_diagnostics",
        "up": "db/migrations/0020_a3_support_diagnostics.up.sql",
        "down": "db/migrations/0020_a3_support_diagnostics.down.sql",
        "schema_snapshot": "db/schema/0020_a3_support_diagnostics.sql",
    }
