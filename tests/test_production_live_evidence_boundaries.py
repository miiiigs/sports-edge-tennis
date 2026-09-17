from __future__ import annotations

from sports_edge_tennis.performance import coverage_evidence, evidence
from sports_edge_tennis.performance.a1_diagnostic import load_a1_live_challenger_diagnostic


class _Result:
    def __init__(self, *, one=None, rows=()):
        self._one = one
        self._rows = list(rows)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class _CaptureConnection:
    def __init__(self):
        self.queries: list[str] = []

    def execute(self, sql, params=None):
        self.queries.append(str(sql))
        return _Result()


def test_latest_live_run_excludes_experiments():
    conn = _CaptureConnection()
    assert evidence._latest_run_id(conn) is None
    assert "experiment_id IS NULL" in conn.queries[-1]


def test_agent_evidence_excludes_experiment_runs_and_predictions():
    conn = _CaptureConnection()
    assert evidence._agent_evidence(conn) == ()
    query = conn.queries[-1]
    assert "pr.experiment_id IS NULL" in query
    assert "p.experiment_id IS NULL" in query


def test_agent3_shadow_excludes_experiment_runs_and_predictions():
    conn = _CaptureConnection()
    assert evidence._agent3_shadow(conn) == ()
    query = conn.queries[-1]
    assert "pr.experiment_id IS NULL" in query
    assert "p.experiment_id IS NULL" in query


def test_coverage_slices_exclude_experiment_runs_and_predictions():
    conn = _CaptureConnection()
    assert coverage_evidence.load_agent_coverage_slices(conn) == ()
    query = conn.queries[-1]
    assert "pr.experiment_id IS NULL" in query
    assert "p.experiment_id IS NULL" in query


def test_a1_challenger_diagnostic_is_production_live_only():
    conn = _CaptureConnection()
    diagnostic = load_a1_live_challenger_diagnostic(conn)
    assert diagnostic.initialized is False
    query = conn.queries[-1]
    assert "pr.experiment_id IS NULL" in query
    assert "p.experiment_id IS NULL" in query
    assert "AGENT_1_SURFACE_ELO" in query
    assert "CHALLENGER" in query
