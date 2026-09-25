from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from sports_edge_tennis.performance import shadow_strategy as shadow


class _Result:
    def __init__(self, *, one=None, all_rows=()):
        self._one = one
        self._all = list(all_rows)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class _CandidateConn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        if "FROM prediction_runs" in sql:
            return _Result(
                one=(
                    "COMPLETED",
                    "predictive_agent_result_v1",
                    datetime(2026, 9, 17, 0, 0, tzinfo=timezone.utc),
                )
            )
        if "FROM predictions" in sql:
            return _Result(all_rows=self.rows)
        raise AssertionError(f"unexpected SQL: {sql}")


def _rows(*, a1_side=1, a2_side=1, a3_status="ABSTAINED", a1_prob="0.61", a2_prob="0.57"):
    return [
        (11, 101, "AGENT_1_SURFACE_ELO", "PREDICTED", a1_side, Decimal(a1_prob)),
        (12, 101, "AGENT_2_HIERARCHICAL_MARKOV", "PREDICTED", a2_side, Decimal(a2_prob)),
        (
            13,
            101,
            "AGENT_3_INTRANSITIVITY",
            a3_status,
            None if a3_status == "ABSTAINED" else 1,
            None if a3_status == "ABSTAINED" else Decimal("0.55"),
        ),
    ]


def test_shadow_candidate_is_exact_a1_a2_agree_a3_abstain_contract():
    candidates = shadow._load_candidate_rows(
        _CandidateConn(_rows()),
        prediction_run_id=77,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.match_id == 101
    assert candidate.selected_side == 1
    assert candidate.a1_probability == Decimal("0.61")
    assert candidate.a2_probability == Decimal("0.57")
    assert candidate.consensus_probability == Decimal(
        "0.59000000000000000000000000000000000000000000000000"
    )
    assert candidate.probability_spread == Decimal("0.04")


def test_shadow_candidate_rejects_directional_disagreement_and_active_a3():
    disagree = shadow._load_candidate_rows(
        _CandidateConn(_rows(a2_side=2)),
        prediction_run_id=77,
    )
    active_a3 = shadow._load_candidate_rows(
        _CandidateConn(_rows(a3_status="PREDICTED")),
        prediction_run_id=77,
    )

    assert disagree == ()
    assert active_a3 == ()


def test_shadow_candidate_requires_both_active_probabilities_above_half():
    candidates = shadow._load_candidate_rows(
        _CandidateConn(_rows(a1_prob="0.50")),
        prediction_run_id=77,
    )
    assert candidates == ()


def test_shadow_clv_matches_production_formula_shape():
    margin, clv = shadow._calculate_shadow_clv(
        price_taken=Decimal("2.20"),
        closing_price=Decimal("2.00"),
        closing_booksum=Decimal("1.05"),
    )

    assert margin == Decimal("0.05000000")
    assert clv == Decimal("0.04761905")


def test_shadow_tables_are_separate_from_production_decisions():
    sql = shadow._SCHEMA_DDL.lower()
    assert "create table if not exists shadow_strategy_observations" in sql
    assert "create table if not exists shadow_strategy_positions" in sql
    assert "create table if not exists shadow_strategy_settlements" in sql
    assert "create table if not exists shadow_strategy_clv" in sql
    assert "alter table decisions" not in sql
    assert "insert into decisions" not in sql


class _SettlementConn:
    """Minimal fake for the shadow settlement query sequence."""

    def __init__(self, *, positions, results, priors):
        self.positions = positions
        self.results = results
        self.priors = priors
        self.inserted = []

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "CREATE TABLE" in normalized or "DO $$" in normalized:
            return _Result()
        if "FROM shadow_strategy_settlements" in normalized:
            return _Result(one=self.priors.get(params[0]))
        if "FROM match_result_observations" in normalized:
            return _Result(one=self.results.get(params[0]))
        if "FROM shadow_strategy_positions" in normalized:
            return _Result(all_rows=self.positions)
        if "INSERT INTO shadow_strategy_settlements" in normalized:
            self.inserted.append(params)
            return _Result(one=(params[0],))
        raise AssertionError(f"unexpected SQL: {normalized}")


def _settled_at():
    return datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def test_shadow_settlement_reconciles_equivalent_new_result_row():
    conn = _SettlementConn(
        positions=[(1, 55, 1)],
        results={55: (900, "COMPLETED", 1)},
        priors={1: (700, "WON", 55)},
    )

    outcome = shadow.settle_shadow_positions(conn, settled_at=_settled_at())

    assert outcome.reconciled == 1
    assert outcome.existing == 0
    assert outcome.inserted == 0
    assert outcome.won == 1
    assert conn.inserted == []


def test_shadow_settlement_actual_contradiction_fails_closed():
    conn = _SettlementConn(
        positions=[(1, 55, 1)],
        results={55: (900, "COMPLETED", 2)},
        priors={1: (700, "WON", 55)},
    )

    with pytest.raises(shadow.ShadowSettlementConflict):
        shadow.settle_shadow_positions(conn, settled_at=_settled_at())


def test_shadow_settlement_different_match_lineage_fails_closed():
    conn = _SettlementConn(
        positions=[(1, 55, 1)],
        results={55: (900, "COMPLETED", 1)},
        priors={1: (700, "WON", 99)},
    )

    with pytest.raises(shadow.ShadowSettlementConflict):
        shadow.settle_shadow_positions(conn, settled_at=_settled_at())


def test_shadow_schema_bootstraps_on_postgres(db):
    shadow.ensure_shadow_strategy_schema(db)

    for table_name in (
        "shadow_strategy_registry",
        "shadow_strategy_runs",
        "shadow_strategy_observations",
        "shadow_strategy_observation_odds",
        "shadow_strategy_positions",
        "shadow_strategy_settlements",
        "shadow_strategy_clv",
    ):
        row = db.execute("SELECT to_regclass(%s)", (f"public.{table_name}",)).fetchone()
        assert row is not None
        assert row[0] == table_name
