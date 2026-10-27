"""Exact Manager-history research query and heldout labeling checks."""
from datetime import date, timedelta

from ops.export_tennis_manager_73_ledger import export_sql, rows_for_regime
from research.tennis_manager_73_shadow_20261027.evaluator import evaluate, Pick
from sports_edge_tennis.performance.regimes import (
    first_production_model_picks_cte, is_complete_model_regime_key,
)


def test_export_is_identical_manager_first_population():
    sql = export_sql()
    assert first_production_model_picks_cte() in sql
    assert "mv0.as_of <= p.picked_at" in sql
    assert "mv0.source_observed_at <= p.picked_at" in sql
    assert "mro.as_of DESC" in sql
    assert "p.model_regime_key = %s" in sql
    assert "wager" not in sql.lower()


def test_sql_parses_on_real_postgres_schema(db):
    # The configured PostgreSQL fixture has the production schema migrations.
    # No writes, no arbitrary production connection and no provider calls.
    assert db.execute(export_sql(), ("PIT_MODEL_REGIME_UNKNOWN", date(2026, 10, 26))).fetchall() == []


def test_all_pending_validation_labels_block_candidate():
    first = date(2026, 1, 1)
    rows = []
    for i in range(80):
        for j in range(8):
            rows.append(Pick(
                match_id=i * 8 + j + 1,
                day=first + timedelta(days=i),
                confidence=0.84 if j < 6 else 0.65,
                result="PENDING" if (i == 70 and j == 0) else ("WON" if j < 6 else "LOST"),
                regime="PIT_MODEL_REGIME_UNIT",
            ))
    assert evaluate(rows)["status"] == "BLOCKED_UNSETTLED_VALIDATION_OR_HOLDOUT"
