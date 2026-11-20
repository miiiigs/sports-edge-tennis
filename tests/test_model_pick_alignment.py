"""Aligned Model Picks: one population for summary and detail.

The page headline, the daily rows and the selected-day detail must all come
from the same ``aligned_production_model_picks_cte()``. These tests pin that
contract and the wide-spread / Manager distinction, without changing any
stored decision.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import test_performance_regime_cohorting as harness
from test_performance_regime_cohorting import (
    AGENTS,
    _agent_config_id,
    _policy_id,
    _seed_match,
    _seed_model_pick_only,
    _seed_run,
)

from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.dashboard import model_pick_history
from sports_edge_tennis.dashboard.read_model import get_issued_day_state
from sports_edge_tennis.dashboard.model_pick_history import (
    DailyModelPickSummary,
    ModelPickResultsView,
    ModelPickRow,
    aligned_production_model_picks_cte,
    load_aligned_daily_model_pick_summaries,
    load_aligned_daily_model_picks,
    load_daily_model_pick_summaries,
    load_daily_model_picks,
)
from sports_edge_tennis.manager import evaluate_and_persist_manager_consensus


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _CaptureConn:
    """Scripted connection that records SQL and parameters."""

    def __init__(self, rows=()):
        self._rows = rows
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        if "to_regclass" in sql:
            return _Result([(None,)])
        self.calls.append((sql, params))
        return _Result(self._rows)

    @property
    def sql(self) -> str:
        return self.calls[-1][0]

    @property
    def params(self) -> tuple:
        return self.calls[-1][1]


# ----------------------------------------------------------- CTE structure


def test_aligned_cte_is_the_60_percent_current_rule_population() -> None:
    sql = aligned_production_model_picks_cte()

    assert "mcr.manager_status = 'CONSENSUS'" in sql
    assert "mcr.consensus_probability >= 0.600000" in sql
    assert "AGENT3_ABSTENTION_SPREAD_EXCEEDED" in sql
    assert "a1.prediction_status = 'PREDICTED'" in sql
    assert "a2.prediction_status = 'PREDICTED'" in sql
    assert "a3.prediction_status = 'ABSTAINED'" in sql
    assert "a2.selection_side = a1.selection_side" in sql
    assert "((a1.win_probability + a2.win_probability) / 2.0) >= 0.600000" in sql
    assert "DISTINCT ON (match_id)" in sql


def test_summary_and_detail_both_use_the_aligned_cte() -> None:
    summary_conn = _CaptureConn()
    detail_conn = _CaptureConn()

    load_aligned_daily_model_pick_summaries(summary_conn)
    load_aligned_daily_model_picks(detail_conn, decision_date=date(2026, 9, 28))

    for conn in (summary_conn, detail_conn):
        assert "aligned_model_candidates AS" in conn.sql
        assert "aligned_model_picks AS" in conn.sql
        assert "first_model_picks" not in conn.sql


def test_aligned_detail_uses_validated_timezone_and_date_params() -> None:
    conn = _CaptureConn()

    load_aligned_daily_model_picks(
        conn,
        decision_date=date(2026, 9, 28),
        timezone_name="Asia/Manila",
    )

    assert conn.params == ("Asia/Manila", "Asia/Manila", date(2026, 9, 28))
    assert "AT TIME ZONE %s::text" in conn.sql
    assert "match_start_at" in conn.sql


def test_aligned_summary_uses_the_same_timezone_bucket_expression() -> None:
    conn = _CaptureConn()

    load_aligned_daily_model_pick_summaries(conn, timezone_name="Europe/London")

    assert conn.params[0] == "Europe/London"
    assert conn.params[1] == "Europe/London"
    assert "AT TIME ZONE %s::text" in conn.sql


def test_original_ledger_does_not_use_the_aligned_population() -> None:
    conn = _CaptureConn()

    load_daily_model_picks(conn, decision_date=date(2026, 9, 28))

    assert "first_model_picks AS" in conn.sql
    assert "aligned_model_picks" not in conn.sql
    assert "a1_a2_wide_spread" not in conn.sql.lower()


# ------------------------------------------------------- pick type labels


def _row(**overrides) -> ModelPickRow:
    base = {
        "manager_consensus_result_id": 1,
        "picked_at": datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
        "match_start_at": datetime(2026, 9, 28, 14, 0, tzinfo=UTC),
        "tournament": "Test Open",
        "player_1": "Player A",
        "player_2": "Player B",
        "selection": "Player A",
        "consensus_probability": Decimal("0.650000"),
        "result_status": "PENDING",
        "is_value_bet": False,
        "manager_policy_version": "v1",
        "model_regime_key": None,
    }
    base.update(overrides)
    return ModelPickRow(**base)


def test_manager_aligned_row_is_labelled_as_manager_consensus() -> None:
    row = _row(pick_type="MANAGER_CONSENSUS")

    assert row.is_wide_spread is False
    assert row.type_label == "MODEL PICK"
    assert row.probability_source_label == "Model Probability · Uncalibrated"


def test_wide_spread_row_is_labelled_and_tracking_only() -> None:
    row = _row(pick_type="A1_A2_WIDE_SPREAD", is_value_bet=False)

    assert row.is_wide_spread is True
    assert row.type_label == "MODEL PICK · WIDE SPREAD"
    assert row.probability_source_label == (
        "Wide-Spread Tracking Confidence · A1/A2 Tracking Average"
    )
    # A wide-spread row is never an actionable Value Bet.
    assert row.is_value_bet is False


def test_value_bet_row_keeps_its_own_label() -> None:
    row = _row(pick_type="MANAGER_CONSENSUS", is_value_bet=True)

    assert row.type_label == "VALUE BET"


# ----------------------------------------------------- history wiring


class _DummyConn:
    def close(self):
        return None


def test_load_history_uses_aligned_population_for_summary_and_detail(monkeypatch):
    day = date(2026, 9, 28)
    aligned_rows = (
        DailyModelPickSummary(day, 3, 2, 1, 0, 1, 1, 0.5),
    )
    original_rows = (
        DailyModelPickSummary(day, 99, 0, 99, 0, 0, 0, None),
    )
    detail = (
        _row(pick_type="MANAGER_CONSENSUS", manager_consensus_result_id=1),
        _row(pick_type="A1_A2_WIDE_SPREAD", manager_consensus_result_id=2),
        _row(pick_type="MANAGER_CONSENSUS", manager_consensus_result_id=3),
    )

    monkeypatch.setattr(model_pick_history, "_connect", lambda: _DummyConn())
    monkeypatch.setattr(
        model_pick_history,
        "load_aligned_daily_model_pick_summaries",
        lambda conn, timezone_name="UTC", **kwargs: aligned_rows,
    )
    monkeypatch.setattr(
        model_pick_history,
        "load_daily_model_pick_summaries",
        lambda conn, **kwargs: original_rows,
    )
    monkeypatch.setattr(
        model_pick_history,
        "load_aligned_daily_model_picks",
        lambda conn, decision_date, timezone_name="UTC": detail,
    )
    monkeypatch.setattr(
        model_pick_history, "resolve_current_model_regime", lambda conn: None
    )
    monkeypatch.setattr(
        model_pick_history, "load_model_regime_cohorts", lambda conn, **kwargs: ()
    )

    view = model_pick_history._load_history(day)

    # The selected summary is the aligned one, not the original ledger's 99.
    assert view.selected_summary is not None
    assert view.selected_summary.picks == 3
    assert len(view.picks) == 3
    assert view.selected_summary.picks == len(view.picks)
    # The original ledger is still carried for the audit section.
    assert view.all_summaries == original_rows
    assert view.aligned_summaries == aligned_rows


def test_api_aligned_history_uses_aligned_population(monkeypatch):
    from fastapi.testclient import TestClient

    from sports_edge_tennis.dashboard.production_app import app

    day = date(2026, 9, 28)
    aligned_rows = (
        DailyModelPickSummary(day, 3, 2, 1, 0, 1, 1, 0.5),
    )
    original_rows = (
        DailyModelPickSummary(day, 99, 0, 99, 0, 0, 0, None),
    )
    monkeypatch.setattr(
        model_pick_history,
        "_load_history",
        lambda requested, tz=None: ModelPickResultsView(
            all_summaries=original_rows,
            aligned_summaries=aligned_rows,
        ),
    )

    body = TestClient(app).get("/api/dashboard/model-pick-results").json()

    assert body["aligned_live_history"]["performance"]["all_time"]["picks"] == 3
    assert body["all_historical_model_performance"]["all_time"]["picks"] == 99


# --------------------------------------------------------------- postgres


def test_aligned_daily_summary_count_equals_detail_count(db: psycopg.Connection) -> None:
    day = date(2026, 9, 11)
    _seed_model_pick_only(db, label="AlignA", match_day=day)
    _seed_model_pick_only(db, label="AlignB", match_day=day)

    summaries = load_aligned_daily_model_pick_summaries(db)
    selected = next(row for row in summaries if row.decision_date == day)
    detail = load_aligned_daily_model_picks(db, decision_date=day)

    assert selected.picks == len(detail) == 2
    assert len({row.manager_consensus_result_id for row in detail}) == 2


def _seed_pick_at_start(conn, *, label: str, start: datetime) -> int:
    """A production consensus pick whose match starts at an explicit instant.

    ``_seed_model_pick_only`` fixes the start at 12:00 UTC per match day; this
    lets a test place a match across a time-zone midnight without mutating the
    append-only ``match_versions`` row after the fact.
    """
    as_of = start - timedelta(hours=4)
    match_id = _seed_match(conn, label=label, start=start)
    run_id = _seed_run(conn, match_id=match_id, as_of=as_of)
    outcome = evaluate_and_persist_manager_consensus(
        conn,
        prediction_run_id=run_id,
        manager_policy_version_id=_policy_id(conn, "v1"),
    )
    assert outcome.status == "PERSISTED"
    return match_id


def test_aligned_bucketing_respects_selected_timezone(db: psycopg.Connection) -> None:
    # 20:00 UTC on Sep 27 is 04:00 PHT on Sep 28.
    start = datetime(2026, 9, 27, 20, 0, tzinfo=UTC)
    _seed_pick_at_start(db, label="Midnight", start=start)

    utc_summaries = load_aligned_daily_model_pick_summaries(db, timezone_name="UTC")
    ph_summaries = load_aligned_daily_model_pick_summaries(
        db, timezone_name="Asia/Manila"
    )

    assert any(row.decision_date == date(2026, 9, 27) for row in utc_summaries)
    assert not any(row.decision_date == date(2026, 9, 27) for row in ph_summaries)
    assert any(row.decision_date == date(2026, 9, 28) for row in ph_summaries)

    ph_detail = load_aligned_daily_model_picks(
        db, decision_date=date(2026, 9, 28), timezone_name="Asia/Manila"
    )
    assert len(ph_detail) == 1

    # The invariant holds under a non-UTC bucket too.
    ph_selected = next(
        row for row in ph_summaries if row.decision_date == date(2026, 9, 28)
    )
    assert ph_selected.picks == len(ph_detail)


def test_aligned_excludes_sub_60_manager_consensus(
    db: psycopg.Connection, monkeypatch
) -> None:
    day = date(2026, 9, 12)

    def seed_at_055(
        conn, *, match_id, run_id, agent_config_versions, as_of, finalized, experiment_id=None
    ) -> None:
        for agent, config_version in zip(AGENTS, agent_config_versions):
            cfg = _agent_config_id(conn, agent, config_version)
            conn.execute(
                """
                INSERT INTO predictions (
                    match_id, prediction_run_id, agent_config_version_id, agent_code,
                    prediction_status, selection_side, win_probability,
                    model_version, feature_version, as_of, prediction_locked_at,
                    probability_representation, experiment_id
                )
                VALUES (%s, %s, %s, %s, 'PREDICTED', 1, 0.55,
                        'regime-fixture', 'regime-feature', %s, %s, %s, %s)
                """,
                (
                    match_id,
                    run_id,
                    cfg,
                    agent,
                    as_of,
                    finalized,
                    AGENT_PROBABILITY_REPRESENTATIONS[agent],
                    experiment_id,
                ),
            )

    # Predictions are append-only, so seed a 0.55 consensus from the start.
    monkeypatch.setattr(harness, "_seed_predictions", seed_at_055)
    _seed_model_pick_only(db, label="Sub60", match_day=day)

    aligned = load_aligned_daily_model_pick_summaries(db)
    assert all(row.decision_date != day for row in aligned)

    # The original first-production ledger still records the pick unchanged.
    original = load_daily_model_pick_summaries(db)
    assert any(row.decision_date == day for row in original)


def test_today_uses_issued_player_side_and_start_after_provider_reschedule_or_swap(
    db: psycopg.Connection,
) -> None:
    """Current provider corrections cannot silently rewrite a historic pick."""
    # Historical synthetic fixture: all event times must precede real CI time
    # because append-only predictions validate ingested_at >= as_of.
    start = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)
    match_id = _seed_pick_at_start(db, label="IssuedParity", start=start)
    old = db.execute(
        """
        SELECT player_1_id, player_2_id
        FROM match_versions WHERE match_id = %s
        ORDER BY match_version_id ASC LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    assert old is not None
    p1, p2 = old
    correction_at = start - timedelta(hours=1)  # after original pick
    rescheduled_start = start + timedelta(hours=2)
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at,
            tour_scope, competition_tier, surface, round_code,
            best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of, ingested_at
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32',
                3, FALSE, 'SCHEDULED', %s, %s, %s)
        """,
        (match_id, p2, p1, rescheduled_start,
         correction_at, correction_at, correction_at),
    )

    state = get_issued_day_state(db, date(2026, 10, 18))
    assert state.kind == "POPULATED"
    assert len(state.matches) == 1
    issued = state.matches[0]
    assert issued.match_id == match_id
    assert issued.player_1 == "IssuedParity One"
    assert issued.player_2 == "IssuedParity Two"
    assert issued.selected_side == 1
    assert issued.selected_player == "IssuedParity One"
    assert issued.scheduled_start == start
    assert issued.consensus_probability is not None
    assert issued.consensus_probability >= Decimal("0.60")
