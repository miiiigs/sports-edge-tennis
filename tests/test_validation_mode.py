from __future__ import annotations

from types import SimpleNamespace

from sports_edge_tennis.live import production_batch
from sports_edge_tennis.live.provider_authority import (
    DATA_MODE_ENV,
    ODDS_MODE_ENV,
    TARGET_DATA_MODE,
    TARGET_ODDS_MODE,
    load_provider_authority,
)
from sports_edge_tennis.live.validation import (
    VALIDATION_MODE_ENV,
    validation_mode_enabled,
)
from sports_edge_tennis.veto import integration as veto_integration


def test_validation_mode_defaults_off():
    assert validation_mode_enabled({}) is False
    assert validation_mode_enabled({VALIDATION_MODE_ENV: ""}) is False
    assert validation_mode_enabled({VALIDATION_MODE_ENV: "0"}) is False
    assert validation_mode_enabled({VALIDATION_MODE_ENV: "false"}) is False


def test_validation_mode_truthy_values():
    for value in ("1", "true", "TRUE", "yes", "on"):
        assert validation_mode_enabled({VALIDATION_MODE_ENV: value}) is True


def test_provider_configuration_is_loaded_unchanged_in_validation_mode():
    base = {DATA_MODE_ENV: TARGET_DATA_MODE, ODDS_MODE_ENV: TARGET_ODDS_MODE}
    with_flag = {**base, VALIDATION_MODE_ENV: "1"}
    assert load_provider_authority(base) == load_provider_authority(with_flag)
    assert load_provider_authority(with_flag).data_mode == TARGET_DATA_MODE
    assert load_provider_authority(with_flag).odds_mode == TARGET_ODDS_MODE


def test_bet_publication_is_blocked_in_validation_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(veto_integration, "validation_mode_enabled", lambda: True)
    monkeypatch.setattr(
        veto_integration,
        "publish_new_bet_decision",
        lambda *a, **k: calls.append((a, k)) or "should-not-happen",
    )
    result = veto_integration.publish_bet_decision_if_allowed(
        None, decision_id=1, published_at=None
    )
    assert result is None
    assert calls == []


def test_bet_publication_runs_when_validation_mode_off(monkeypatch):
    calls = []
    monkeypatch.setattr(veto_integration, "validation_mode_enabled", lambda: False)
    monkeypatch.setattr(
        veto_integration,
        "publish_new_bet_decision",
        lambda conn, *, decision_id, published_at: calls.append(decision_id) or "stamped",
    )
    result = veto_integration.publish_bet_decision_if_allowed(
        None, decision_id=7, published_at=None
    )
    assert result == "stamped"
    assert calls == [7]


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Connect:
    def __call__(self, *args, **kwargs):
        return _Conn()


def _primary_decision():
    return SimpleNamespace(
        authority=SimpleNamespace(
            data_mode=TARGET_DATA_MODE, odds_mode=TARGET_ODDS_MODE
        ),
        state="CLOSED",
        reason="PRIMARY_ACTIVE",
    )


def _wire_validation_main(monkeypatch):
    monkeypatch.setattr(production_batch, "_db_dsn", lambda: "postgresql://example")
    monkeypatch.setattr(
        production_batch,
        "load_provider_authority",
        lambda env: SimpleNamespace(data_mode=TARGET_DATA_MODE, odds_mode=TARGET_ODDS_MODE),
    )
    monkeypatch.setattr(
        production_batch,
        "_resolve_provider_decision",
        lambda conn, configured, *, env: _primary_decision(),
    )
    monkeypatch.setattr(production_batch.psycopg, "connect", _Connect())
    monkeypatch.setattr(production_batch, "_ensure_quarter_kelly_schema", lambda conn: "EXISTING")
    monkeypatch.setattr(production_batch, "_ensure_publication_control_schema", lambda conn: "EXISTING")
    monkeypatch.setattr(
        production_batch, "ensure_model_identity_bridge_schema", lambda conn: "EXISTING"
    )
    monkeypatch.setattr(production_batch, "_ensure_strict_manager_v2_schema", lambda conn: "EXISTING")
    monkeypatch.setattr(
        production_batch, "_ensure_prediction_abstention_reason_schema", lambda conn: "EXISTING"
    )
    monkeypatch.setattr(
        production_batch,
        "_ensure_tournament_identity_corrections_schema",
        lambda conn: "EXISTING",
    )
    monkeypatch.setattr(
        production_batch,
        "_ensure_provider_fixture_metadata_schema",
        lambda conn: "EXISTING",
    )
    monkeypatch.setattr(production_batch, "_ensure_oddspapi_ledger_schema", lambda conn: "READY")
    monkeypatch.setattr(production_batch, "_live_batch_gap_cycle", lambda *a, **k: None)
    monkeypatch.setattr(production_batch, "_live_operational_health_cycle", lambda *a, **k: None)
    monkeypatch.setattr(
        production_batch,
        "live_batch_main",
        lambda argv, summary_sink=None, provider_decision=None: 0,
    )
    monkeypatch.setattr(production_batch, "validation_mode_enabled", lambda: True)


def test_validation_mode_blocks_settlement_shadow_and_notifications(monkeypatch):
    _wire_validation_main(monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("side effect executed in validation mode")

    monkeypatch.setattr(production_batch, "settle_type_a_match_winner_decisions", forbidden)
    monkeypatch.setattr(production_batch, "_shadow_cycle", forbidden)
    monkeypatch.setattr(production_batch, "_a1_evidence_cycle", forbidden)
    monkeypatch.setattr(production_batch, "_pncl_concordance_cycle", forbidden)

    assert production_batch.main([]) == 0


def test_validation_mode_disabled_runs_settlement(monkeypatch):
    _wire_validation_main(monkeypatch)
    monkeypatch.setattr(production_batch, "validation_mode_enabled", lambda: False)

    called = {"settlement": False, "shadow": False}

    def settlement(conn, *, settled_at):
        called["settlement"] = True
        return SimpleNamespace(
            eligible_decisions=0,
            pending_results=0,
            inserted=0,
            existing=0,
            reconciled=0,
            won=0,
            lost=0,
            void=0,
            cancelled=0,
        )

    monkeypatch.setattr(production_batch, "settle_type_a_match_winner_decisions", settlement)
    monkeypatch.setattr(
        production_batch,
        "_shadow_cycle",
        lambda *a, **k: called.__setitem__("shadow", True),
    )
    monkeypatch.setattr(production_batch, "_a1_evidence_cycle", lambda *a, **k: None)
    monkeypatch.setattr(production_batch, "_pncl_concordance_cycle", lambda *a, **k: None)

    assert production_batch.main([]) == 0
    assert called["settlement"] is True
    assert called["shadow"] is True


def test_genuine_settlement_conflict_fails_closed_and_skips_shadow(monkeypatch, capsys):
    from sports_edge_tennis.settlement import SettlementEvidenceConflict

    _wire_validation_main(monkeypatch)
    monkeypatch.setattr(production_batch, "validation_mode_enabled", lambda: False)

    def conflict(conn, *, settled_at):
        raise SettlementEvidenceConflict(
            "immutable settlement conflicts with latest canonical result truth"
        )

    shadow_called = []
    monkeypatch.setattr(production_batch, "settle_type_a_match_winner_decisions", conflict)
    monkeypatch.setattr(
        production_batch, "_shadow_cycle", lambda *a, **k: shadow_called.append(1)
    )

    assert production_batch.main([]) == 2
    out = capsys.readouterr().out
    assert "SETTLEMENT_CONFLICT" in out
    assert shadow_called == []
