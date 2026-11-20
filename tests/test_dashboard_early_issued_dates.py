"""Brian's live dashboard must not hide already issued future-day Model Picks.

Regression for historical original match day issued on an earlier date:
the original aligned ledger owns eligibility even before an hourly runner
targets that day. No stored pick, original probability, result or Manager rule
may be changed by this dashboard read-only correction.
"""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import sports_edge_tennis.dashboard.read_model as read_model
from sports_edge_tennis.dashboard.aligned_model_picks import AlignedPick


MATCH_DAY = date(2026, 11, 7)
PICK_TIME = datetime(2026, 11, 6, 13, 0, tzinfo=UTC)


def _aligned(match_id: int = 114, run_id: int = 77) -> AlignedPick:
    return AlignedPick(
        manager_consensus_result_id=1000 + match_id,
        prediction_run_id=run_id,
        match_id=match_id,
        picked_at=PICK_TIME,
        selected_side=1,
        aligned_probability=Decimal("0.74"),
        aligned_pick_type="MANAGER_CONSENSUS",
        match_start_at=datetime(2026, 11, 7, 8, 0, tzinfo=UTC),
    )


def test_early_issued_pick_visible_even_when_no_completed_run_targets_its_match_day(monkeypatch):
    pick = _aligned()
    sentinel = object()
    monkeypatch.setattr(read_model, "load_aligned_day_picks", lambda *args, **kw: (pick,))
    monkeypatch.setattr(read_model, "completed_day_run_rows", lambda *args, **kw: [])
    monkeypatch.setattr(read_model, "_load_aligned_match_views",
                        lambda conn, picks: [sentinel] if picks == (pick,) else [])
    def no_fallback(*args):
        raise AssertionError("original issued pick must never be replaced by empty-slate fallback")
    monkeypatch.setattr(read_model, "get_run_for_date", no_fallback)

    state = read_model.get_issued_day_state(object(), MATCH_DAY, timezone_name="Asia/Manila")
    assert state.kind == "POPULATED"
    assert state.run_date == MATCH_DAY
    assert state.prediction_run_id == 77
    assert state.as_of == PICK_TIME
    assert state.matches == [sentinel]


def test_no_original_picks_preserves_existing_empty_date_fallback(monkeypatch):
    expected = read_model.RunState(
        kind="NO_RUN", run_date=MATCH_DAY, as_of=None,
        prediction_run_id=None, run_status=None, matches=[],
    )
    monkeypatch.setattr(read_model, "load_aligned_day_picks", lambda *args, **kw: ())
    monkeypatch.setattr(read_model, "completed_day_run_rows", lambda *args, **kw: [])
    monkeypatch.setattr(read_model, "get_run_for_date", lambda *args: expected)

    assert read_model.get_issued_day_state(object(), MATCH_DAY) is expected


def test_existing_current_day_completed_run_still_owns_freshness_when_picks_present(monkeypatch):
    pick = _aligned()
    later = PICK_TIME + timedelta(hours=3)
    monkeypatch.setattr(read_model, "load_aligned_day_picks", lambda *args, **kw: (pick,))
    monkeypatch.setattr(read_model, "completed_day_run_rows",
                        lambda *args, **kw: [(77, PICK_TIME), (79, later)])
    monkeypatch.setattr(read_model, "_load_aligned_match_views", lambda conn, picks: [pick])

    state = read_model.get_issued_day_state(object(), MATCH_DAY, timezone_name="Asia/Manila")
    assert state.kind == "POPULATED"
    assert state.prediction_run_id == 79
    assert state.as_of == later
    assert state.matches == [pick]
