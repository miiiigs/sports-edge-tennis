"""Brian production dashboard evidence: five-day parity, pending, zero hindsight.

Synthetic source-bound tests. Real database/owner browser checks remain a
separate release gate and must never be represented as passed by these tests.
"""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from pathlib import Path

import pytest
from jinja2 import Environment

from sports_edge_tennis.dashboard.qualified_history import ComparisonDay
from sports_edge_tennis.dashboard.recent_quality_summary import (
    latest_five_day_evidence,
)
from sports_edge_tennis.live.pending_result_diagnostics import (
    PendingPickEvidence, classify_pending_pick,
    REASON_TERMINAL_FIXTURE_WITHOUT_RESULT,
    REASON_FIXTURE_POSTPONED,
)


RAW = (
    ("2026-11-05", (19, 12, 7, 0, 0), (6, 4, 2, 0, 0), (10, 3, 7, 0, 0)),
    ("2026-11-06", (8, 6, 2, 0, 0), (2, 1, 1, 0, 0), (6, 4, 2, 0, 0)),
    ("2026-11-07", (10, 5, 4, 1, 0), (5, 4, 1, 0, 0), (7, 4, 2, 1, 0)),
    ("2026-11-08", (26, 14, 11, 0, 1), (15, 9, 5, 0, 1), (17, 11, 6, 0, 0)),
    ("2026-11-09", (61, 27, 17, 3, 14), (28, 16, 5, 2, 5), (29, 13, 10, 1, 5)),
)


def _data():
    days, checks, manager = [], [], []
    for day, old, qualified, mgr in RAW:
        d = date.fromisoformat(day)
        days.append(ComparisonDay(
            day=d,
            original=old[0], original_won=old[1], original_lost=old[2],
            original_void=old[3], original_pending=old[4],
            qualified=qualified[0], qualified_won=qualified[1],
            qualified_lost=qualified[2], qualified_void=qualified[3],
            qualified_pending=qualified[4],
            avoided_losses=old[2]-qualified[2],
            missed_winners=old[1]-qualified[1], complete=True,
        ))
        checks.append(SimpleNamespace(
            decision_date=d, picks=old[0], wins=old[1],
            losses=old[2], voids=old[3], pending=old[4],
        ))
        manager.append({
            "date": day, "picks": mgr[0], "wins": mgr[1],
            "losses": mgr[2], "voids": mgr[3], "pending": mgr[4],
        })
    return days, checks, {"days": list(reversed(manager))}


def test_five_day_live_db_contract_counts_and_separate_cohorts():
    days, checks, manager = _data()
    report = latest_five_day_evidence(
        days, as_of_date=date(2026, 11, 10),
        manager=manager, independent_original_days=checks,
    )
    assert report["ledger_reconciled"]
    assert report["period_start"] == "2026-11-05"
    assert report["period_end"] == "2026-11-09"
    assert report["original"] == {
        "picks": 124, "won": 64, "lost": 41,
        "void": 4, "pending": 15, "graded": 105,
        "win_rate": pytest.approx(64/105), "provisional": True,
    }
    assert report["qualified"]["picks"] == 56
    assert report["qualified"]["won"] == 34
    assert report["qualified"]["lost"] == 14
    assert report["qualified"]["void"] == 2
    assert report["qualified"]["pending"] == 6
    assert report["qualified"]["win_rate"] == pytest.approx(34/48)
    assert report["qualified"]["selection_coverage"] == pytest.approx(56/124)
    assert report["rejected"]["won"] == 30
    assert report["rejected"]["lost"] == 27
    assert report["rejected"]["win_rate"] == pytest.approx(30/57)
    assert report["manager_only"]["picks"] == 69
    assert report["manager_only"]["won"] == 35
    assert report["manager_only"]["lost"] == 27
    assert report["manager_only"]["win_rate"] == pytest.approx(35/62)
    assert report["manager_only"]["independent_cohort"]
    assert report["is_final"] is False
    assert report["a3_a4"]["win_rate"] is None
    assert report["a3_a4"]["status"] == "NOT_VALIDATED_NO_LIVE_RESULT_SERIES"
    nov9 = report["daily"][-1]
    assert nov9["qualified_win_rate"] == pytest.approx(16/21)
    assert nov9["qualified_best_if_pending_win"] == pytest.approx(21/26)
    assert nov9["qualified_worst_if_pending_lose"] == pytest.approx(16/26)
    assert nov9["status"] == "PROVISIONAL"


def test_five_day_report_fails_visible_on_missing_or_mismatched_originals():
    days, checks, manager = _data()
    changed = list(checks)
    changed[0] = SimpleNamespace(
        **{**vars(changed[0]), "wins": 13}
    )
    report = latest_five_day_evidence(
        days, as_of_date=date(2026, 11, 10),
        manager=manager, independent_original_days=changed,
    )
    assert report["ledger_reconciled"] is False
    assert report["reconciliation_mismatch_dates"] == ["2026-11-05"]
    assert report["daily"][0]["status"] == "RECONCILIATION_REQUIRED"
    assert not report["is_final"]
    with pytest.raises(ValueError, match="missing"):
        latest_five_day_evidence(
            days[:-1], as_of_date=date(2026, 11, 10),
            manager=manager, independent_original_days=checks,
        )
    bad = [*days]
    bad[1] = ComparisonDay(**{
        **vars(bad[1]), "qualified": 100,
    })
    with pytest.raises(ValueError, match="inconsistent"):
        latest_five_day_evidence(
            bad, as_of_date=date(2026, 11, 10),
            manager=manager, independent_original_days=checks,
        )


def test_terminal_provider_status_beats_stale_future_schedule():
    now = datetime(2026, 11, 10, 14, tzinfo=UTC)
    base = {
        "match_id": 88, "pick_source": "MANAGER_CONSENSUS",
        "picked_at": now - timedelta(days=1), "selected_side": 1,
        "has_type_a_bet": False,
        "pit_match_start_at": now + timedelta(hours=2),
        "latest_match_start_at": now + timedelta(hours=2),
        "last_fixture_observed_at": now - timedelta(minutes=20),
        "fixture_versions": 1, "source_systems": ("API_TENNIS",),
        "api_tennis_event_key": "123456",
        "tournament": "Test", "competition_tier": "CHALLENGER",
        "player_1": "Player1", "player_2": "Player2",
    }
    for state, reason in [
        ("COMPLETED", REASON_TERMINAL_FIXTURE_WITHOUT_RESULT),
        ("WALKOVER", REASON_TERMINAL_FIXTURE_WITHOUT_RESULT),
        ("CANCELLED", REASON_TERMINAL_FIXTURE_WITHOUT_RESULT),
        ("POSTPONED", REASON_FIXTURE_POSTPONED),
    ]:
        item = PendingPickEvidence(
            **{**base, "latest_match_status": state}
        )
        classified = classify_pending_pick(
            item, now=now, recovery_window_start=date(2026, 11, 8),
        )
        assert classified.reason == reason


def test_live_dashboard_html_compiles_and_keeps_unaudited_a4_blank():
    template = (
        Path(__file__).parents[1] /
        "src/sports_edge_tennis/dashboard/templates/model_pick_results.html"
    ).read_text(encoding="utf-8")
    Environment().parse(template)
    assert "recent_five_day_evidence" in template
    assert "pending_case_audit" in template
    assert "Not verified" in template
    assert "A3 / A4 challenger" in template
    assert "PENDING" not in template or "PROVISIONAL" in template
