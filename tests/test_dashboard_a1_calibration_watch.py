"""Dashboard must show real same-denominator A1 evidence, never research-as-live."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard
from sports_edge_tennis.dashboard.production_app import app
from sports_edge_tennis.performance.a1_diagnostic import (
    A1DiagnosticSlice, A1LiveChallengerDiagnostic,
)


def _slice(dimension, value, sample, scored, acc, all_conf, scored_conf):
    return A1DiagnosticSlice(
        dimension=dimension, value=value, sample=sample, scored=scored,
        correct=int(round(scored * acc)), accuracy=acc,
        brier_score=.22, mean_confidence=all_conf,
        scored_mean_confidence=scored_conf,
    )


def test_live_a1_summary_compares_confidence_on_exact_scored_denominator(monkeypatch):
    class Conn:
        closed = False
        def close(self):
            self.closed = True

    conn = Conn()
    report = A1LiveChallengerDiagnostic(
        initialized=True,
        slices=(
            _slice("overall", "ALL", 300, 240, .625, .80, .73),
            _slice("confidence_band", "80%+", 100, 40, .75, .94, .90),
        ),
    )
    monkeypatch.setattr(dashboard, "DEMO_MODE", False)
    monkeypatch.setattr(dashboard, "_connect", lambda: conn)
    import sports_edge_tennis.performance.a1_diagnostic as diag
    monkeypatch.setattr(diag, "load_a1_live_challenger_diagnostic", lambda conn: report)

    observed = dashboard._load_a1_challenger_performance()
    assert conn.closed
    assert observed["available"]
    assert observed["overall"]["scored"] == 240
    assert observed["overall"]["accuracy"] == "62.5%"
    assert observed["overall"]["scored_confidence"] == "73.0%"
    assert observed["high_confidence"]["scored"] == 40
    assert observed["high_confidence"]["accuracy"] == "75.0%"
    # Must not mistakenly compare 75.0% with all-issued 94.0%.
    assert observed["high_confidence"]["overconfidence_pp"] == "+15.0 pp"
    assert observed["research_only"]


def test_demo_mode_rejects_fake_live_calibration_numbers(monkeypatch):
    monkeypatch.setattr(dashboard, "DEMO_MODE", True)
    monkeypatch.setattr(
        dashboard, "_connect",
        lambda: (_ for _ in ()).throw(AssertionError("demo requested live DB")),
    )
    result = dashboard._load_a1_challenger_performance()
    assert result["available"] is False
    assert "Demo" in result["reason"]
    page = TestClient(app).get("/performance")
    assert page.status_code == 200
    assert "Agent 1 · Challenger calibration watch" in page.text
    assert "Live Agent 1 calibration evidence unavailable" in page.text
    assert "No historical research figures are substituted" in page.text


def test_live_performance_renders_explicit_agent_not_manager_scope(monkeypatch):
    monkeypatch.setattr(dashboard, "DEMO_MODE", True)
    monkeypatch.setattr(
        dashboard, "_load_a1_challenger_performance",
        lambda: {
            "available": True, "overall": {
                "sample": 100, "scored": 90, "accuracy": "60.0%",
                "scored_confidence": "72.0%", "overconfidence_pp": "+12.0 pp",
            },
            "high_confidence": {
                "sample": 50, "scored": 40, "accuracy": "70.0%",
                "scored_confidence": "87.0%", "overconfidence_pp": "+17.0 pp",
            },
        },
    )
    page = TestClient(app).get("/performance")
    assert page.status_code == 200
    assert "+17.0 pp" in page.text
    assert "40 graded" in page.text
    assert "not the current Manager" in page.text
    assert "Research tests do not change published picks" in page.text


def test_scored_only_confidence_is_calculated_by_query_not_inferred_posthoc():
    import inspect
    from sports_edge_tennis.performance.a1_diagnostic import (
        load_a1_live_challenger_diagnostic,
    )
    source = inspect.getsource(load_a1_live_challenger_diagnostic)
    assert "avg(win_probability) FILTER (WHERE winner_side IS NOT NULL)" in source
    assert "scored_mean_confidence=_as_float(row[8])" in source
