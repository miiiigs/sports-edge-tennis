from __future__ import annotations

import pytest

import sports_edge_tennis.performance.evidence as evidence


def test_empty_database_has_no_live_evidence(db):
    summary = evidence.load_live_evidence_summary(db)
    assert summary.initialized is False
    assert summary.latest_prediction_run_id is None


def test_live_evidence_summary_uses_unique_first_bet_hit_rate(monkeypatch):
    monkeypatch.setattr(evidence, "_latest_run_id", lambda conn: 42)
    monkeypatch.setattr(evidence, "_latest_funnel", lambda conn, run_id: (46, 6, 40, 1))
    monkeypatch.setattr(
        evidence,
        "_unique_first_bet_metrics",
        lambda conn: (20, 16, 10, 6, 0),
    )
    monkeypatch.setattr(
        evidence,
        "_reason_counts",
        lambda conn, prediction_run_id, source: (
            evidence.ReasonCount("NO_BET", source.upper(), 1),
        ),
    )
    monkeypatch.setattr(
        evidence,
        "_agent_evidence",
        lambda conn: (
            evidence.AgentEvidence("AGENT_1_SURFACE_ELO", 100, 100, 0, 80, 52, 0.65),
        ),
    )
    monkeypatch.setattr(
        evidence,
        "_agent3_shadow",
        lambda conn: (
            evidence.Agent3ShadowCohort("A3_ABSTAINS", 50, 40, 24, 0.60),
        ),
    )

    summary = evidence.load_live_evidence_summary(object())

    assert summary.initialized is True
    assert summary.latest_prediction_run_id == 42
    assert summary.latest_targets == 46
    assert summary.latest_manager_consensus == 6
    assert summary.latest_manager_no_bet == 40
    assert summary.latest_type_a_bets == 1
    assert summary.unique_first_bets == 20
    assert summary.unique_settled_bets == 16
    assert summary.unique_hit_rate == pytest.approx(10 / 16)
    assert summary.agents[0].accuracy == pytest.approx(0.65)
    assert summary.agent3_shadow[0].cohort == "A3_ABSTAINS"


def test_safe_ratio_is_none_for_empty_denominator():
    assert evidence._safe_ratio(0, 0) is None
    assert evidence._safe_ratio(3, 4) == pytest.approx(0.75)
