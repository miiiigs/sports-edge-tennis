from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.validation.a1_identity_bridge_forward_shadow import (
    CAPTURE_SCHEMA_VERSION,
    ShadowTarget,
    ShadowValidationError,
    build_record,
    run,
)


def _utc(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def _target(**overrides) -> ShadowTarget:
    values = {
        "prediction_run_id": 11,
        "match_id": 22,
        "prediction_id": 33,
        "prediction_as_of": _utc(20, 8),
        "match_start_at": _utc(20, 12),
        "live_player_1_id": 1001,
        "live_player_2_id": 1002,
        "model_player_1_id": 2001,
        "model_player_2_id": 1002,
        "surface": "HARD",
        "tour_scope": "CHALLENGER",
        "competition_tier": "CHALLENGER",
        "best_of_sets": 3,
        "qualifying_flag": False,
        "original_selection_side": 1,
        "original_win_probability": 0.72,
    }
    values.update(overrides)
    return ShadowTarget(**values)


def _bridged(probability: float, side: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        prediction_status="PREDICTED",
        selection_side=side,
        player_1_probability=probability,
        win_probability=probability if side == 1 else 1 - probability,
        model_version="agent1_surface_elo_v1",
        feature_version="agent1_surface_elo_inputs_v1",
    )


def test_target_requires_thirty_minute_forward_lead() -> None:
    with pytest.raises(ShadowValidationError):
        _target(match_start_at=_utc(20, 8) + timedelta(minutes=20))


def test_record_is_outcome_free_and_deterministic() -> None:
    target = _target()
    record = build_record(
        target=target,
        bridged=_bridged(0.66),
        history_rows=134_000,
        eligible_history_matches=130_000,
        capture_at=_utc(20, 8, 5),
        source_revision="abc123",
    )
    assert record["schema_version"] == CAPTURE_SCHEMA_VERSION
    assert record["outcome_included"] is False
    assert record["production_changed"] is False
    assert record["can_promote"] is False
    assert record["player_1_bridged"] is True
    assert record["player_2_bridged"] is False
    assert record["selection_changed"] is False
    assert len(record["record_sha256"]) == 64

    again = build_record(
        target=target,
        bridged=_bridged(0.66),
        history_rows=134_000,
        eligible_history_matches=130_000,
        capture_at=_utc(20, 8, 5),
        source_revision="abc123",
    )
    assert again == record


def test_record_marks_selection_change_without_rewriting_original() -> None:
    record = build_record(
        target=_target(original_selection_side=1),
        bridged=_bridged(0.34, side=2),
        history_rows=10,
        eligible_history_matches=9,
        capture_at=_utc(20, 8, 5),
        source_revision=None,
    )
    assert record["original_selection_side"] == 1
    assert record["bridged_selection_side"] == 2
    assert record["selection_changed"] is True
    assert record["bridged_win_probability"] == pytest.approx(0.66)


def test_ingested_guard_is_opt_in_and_appended_to_both_queries() -> None:
    class Cursor:
        def fetchall(self):
            return []

    class FakeConn:
        def __init__(self) -> None:
            self.sql = ""
            self.params: dict[str, object] = {}

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params or {}
            return Cursor()

    fake = FakeConn()
    load_agent1_history(fake, cutoff=_utc(20), ingested_by=_utc(21))
    assert "mro.ingested_at <= %(ingested_by)s" in fake.sql
    assert "mv.ingested_at <= %(ingested_by)s" in fake.sql
    assert fake.params["ingested_by"] == _utc(21)

    plain = FakeConn()
    load_agent1_history(plain, cutoff=_utc(20))
    assert "ingested_by" not in plain.sql
    assert "ingested_by" not in plain.params


def test_empty_forward_capture_is_explicit_and_does_not_claim_promotion() -> None:
    class Cursor:
        def fetchall(self):
            return []

    class FakeConn:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, sql, params=None):
            self.statements.append(sql)
            return Cursor()

    conn = FakeConn()
    logs: list[str] = []
    summary = run(
        conn,
        run_ids=[999],
        capture_at=_utc(20, 8),
        log=logs.append,
    )
    assert summary["targets"] == 0
    assert summary["captured"] == 0
    assert summary["failed"] == 0
    assert summary["modified_production"] is False
    assert summary["outcome_included"] is False
    assert summary["can_promote"] is False
    assert any("TENNIS_A1_BRIDGE_FORWARD_SUMMARY" in line for line in logs)
    assert conn.statements[0] == "SET default_transaction_read_only=on"
