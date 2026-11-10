"""Regression tests for strictly prior A4 and non-selective Guide Odds."""
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from sports_edge_tennis.performance.a4_quality_shadow import (
    FEATURES, MIN_TRAIN, MODEL_VERSION, GuideQuote, a3_risk, features, fit_a4,
    prioritize, report_day, usable_quote,
)
from sports_edge_tennis.performance.v2_quality_research import FrozenPick
from ops.a4_quality_shadow import build_report


def _utc(month: int, day: int, hour: int = 0) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


def _pick(mid: int, *, day: date = date(2026, 10, 27),
          picked_at: datetime = _utc(10, 26, 8), result: str = "WON",
          observed: datetime = _utc(10, 28, 10), ingested: datetime | None = None,
          raw: float = .78, a3_status: str = "ABSTAINED",
          a3_side: int | None = None, a3_reason: str | None = "INSUFFICIENT_MATCHUP_SUPPORT") -> FrozenPick:
    return FrozenPick(
        match_id=mid, day=day, picked_at=picked_at,
        kind="MANAGER_CONSENSUS", side=1, raw=raw,
        surface="HARD", tier="ATP_TOUR",
        a1_side=1, a2_side=1, a1=.78, a2=.76,
        a3_status=a3_status, a3_side=a3_side,
        result=result, result_as_of=observed,
        result_source_observed_at=observed,
        result_ingested_at=ingested or observed,
        a3_reason=a3_reason,
    )


def _training() -> tuple[FrozenPick, ...]:
    return tuple(
        _pick(1000 + i, result="WON" if i % 3 else "LOST",
              raw=.61 + (i % 20) / 100.0)
        for i in range(MIN_TRAIN + 10)
    )


def _quote(p: FrozenPick, *, seen: datetime | None = None, ingested: datetime | None = None,
           selected_side: int = 1, market_status: str = "OPEN",
           selected_decimal: float = 1.50, opposite_decimal: float = 2.80,
           source: str = "ODDSPAPI_TENNIS") -> GuideQuote:
    seen = seen or (p.picked_at - timedelta(minutes=15))
    return GuideQuote(
        match_id=p.match_id, selected_side=selected_side,
        selected_decimal=selected_decimal, opposite_decimal=opposite_decimal,
        source=source, market_status=market_status,
        source_observed_at=seen, ingested_at=ingested or seen,
        bookmaker_code="TEST_BOOK", source_snapshot_id="test-received-observation",
    )


def test_a3_absence_is_not_predicted_upset_or_forced_pick_rejection():
    p = _pick(1)
    assert a3_risk(p) == "NO_EVIDENCE"
    assert a3_risk(replace(p, a3_status="PREDICTED", a3_side=2)) == "OPPOSES_PICK"
    assert a3_risk(replace(p, a3_status="PREDICTED", a3_side=1)) == "CONFIRMS_PICK"
    assert a3_risk(replace(p, a3_status=None)) == "UNKNOWN"
    assert len(features(p)) == len(FEATURES)
    assert features(p) == features(replace(p, result="LOST"))


def test_a4_fit_uses_only_pre_cutoff_visible_outcomes():
    history = _training()
    cutoff = datetime(2026, 11, 1, tzinfo=UTC)
    fitted = fit_a4(history, cutoff)
    assert fitted is not None
    assert fitted.version == MODEL_VERSION
    assert fitted.trained_rows == len(history)
    assert (fitted.observed_wins + fitted.observed_losses) == len(history)
    assert 0 < fitted.predict(history[0]) < 1
    future = replace(history[0], result_ingested_at=_utc(11, 3))
    patched = fit_a4((future,) + history[1:], cutoff)
    assert patched is not None and patched.trained_rows == len(history) - 1
    # Swapping a holdout WIN -> LOSS must not affect an earlier-fitted model.
    holdout = _pick(9000, day=date(2026, 11, 2),
                    picked_at=_utc(11, 1), observed=_utc(11, 3))
    baseline = fit_a4(history + (holdout,), cutoff)
    changed = fit_a4(history + (replace(holdout, result="LOST"),), cutoff)
    assert baseline == changed


def test_a4_has_no_fit_on_small_or_one_sided_history():
    cutoff = _utc(11, 1)
    assert fit_a4(_training()[:MIN_TRAIN - 1], cutoff) is None
    all_wins = tuple(replace(p, result="WON") for p in _training())
    assert fit_a4(all_wins, cutoff) is None
    with pytest.raises(ValueError, match="timezone"):
        fit_a4(_training(), datetime(2026, 11, 1))


def test_guide_odds_no_vig_and_pre_issuance_guard():
    p = _pick(1)
    q = _quote(p)
    assert usable_quote(p, [q]) == (q, "AVAILABLE")
    assert q.no_vig_selected() == pytest.approx(
        (1/1.5) / ((1/1.5) + (1/2.8)),
    )
    assert usable_quote(p, [_quote(p, seen=p.picked_at + timedelta(seconds=1))])[1] == "POST_PICK_QUOTE"
    assert usable_quote(p, [_quote(p, ingested=p.picked_at + timedelta(seconds=1))])[1] == "POST_PICK_QUOTE"
    assert usable_quote(p, [_quote(p, seen=p.picked_at - timedelta(hours=7))])[1] == "STALE"
    assert usable_quote(p, [_quote(p, selected_side=2)])[1] == "WRONG_SELECTED_SIDE"
    assert usable_quote(p, [_quote(p, market_status="CLOSED")])[1] == "UNSUPPORTED_MARKET"
    assert usable_quote(p, [_quote(p, source="UNVERIFIED")])[1] == "UNSUPPORTED_MARKET"
    assert usable_quote(p, [_quote(p, selected_decimal=1.20, opposite_decimal=1.40)])[1] == "BAD_OVERROUND"
    with pytest.raises(ValueError, match="decimal odds"):
        _quote(p, selected_decimal=1.0)
    with pytest.raises(ValueError, match="timezone"):
        _quote(p, seen=datetime(2026, 10, 26, 7))


def test_market_context_never_modifies_original_selected_side_or_predicted_probability():
    p = _pick(1)
    supported = prioritize(p, reliability=.78, quotes=[_quote(p)])
    assert supported["priority"] == "GUIDE_SUPPORTS_HIGH_PRIORITY"
    assert supported["guide_odds_status"] == "AVAILABLE"
    assert supported["unchanged_selected_side"] == 1
    assert supported["original_probability"] == .78
    assert supported["changes_pick"] is False
    # No subscription / no guide quote must leave same pick.
    missing = prioritize(p, reliability=.78, quotes=())
    assert missing["priority"] == "MODEL_PRIORITY_ONLY"
    assert missing["unchanged_selected_side"] == 1
    assert missing["no_vig_market_probability"] is None
    assert not missing["changes_pick"]
    risk = prioritize(replace(p, a3_status="PREDICTED", a3_side=2),
                      reliability=.78, quotes=[_quote(p)])
    assert risk["priority"] == "REVIEW_A3_CONFLICT"
    assert risk["changes_pick"] is False
    untrained = prioritize(p, reliability=None, quotes=[_quote(p)])
    assert untrained["priority"] == "UNRANKED"
    assert untrained["no_vig_market_probability"] is not None


def test_day_report_is_prior_trained_and_only_scores_results_known_as_of():
    train = _training()
    pick = _pick(
        9000, day=date(2026, 11, 2), picked_at=_utc(11, 1),
        observed=_utc(11, 3), result="WON",
    )
    cutoff = datetime(2026, 11, 2, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Manila"))
    result = report_day(
        (pick,), history=train + (pick,), cutoff=cutoff,
        evaluated_at=_utc(11, 4), quotes=[_quote(pick)],
    )
    assert result["a4_train_rows"] == len(train)
    assert result["a4_fit_available"] is True
    assert result["graded"] == 1
    assert result["original_won"] == 1
    assert result["original_pending"] == 0
    assert result["selected_sides_changed"] == 0
    assert result["guide_odds_available"] == 1
    late = replace(pick, result_ingested_at=_utc(11, 7))
    pending = report_day(
        (late,), history=train + (late,), cutoff=cutoff,
        evaluated_at=_utc(11, 4),
    )
    assert pending["graded"] == 0
    assert pending["original_pending"] == 1
    assert pending["a4_brier"] is None
    with pytest.raises(ValueError, match="wrong day"):
        report_day((pick,), history=train, cutoff=_utc(10, 29),
                   evaluated_at=_utc(11, 4))


def test_report_reconciles_original_ledger_without_exposing_match_ids():
    pick = _pick(800, result="LOST")
    original = {"2026-10-27": {
        "picks": 1, "won": 0, "lost": 1, "void": 0, "pending": 0,
    }}
    output = build_report(
        (pick,), start=date(2026, 10, 27), end=date(2026, 10, 27),
        evaluated_at=_utc(11, 1), expected=original,
    )
    assert output["summary"]["original_picks"] == 1
    assert output["summary"]["selected_sides_changed"] == 0
    assert output["daily"][0]["original_lost"] == 1
    assert "line_items" not in output["daily"][0]
    assert output["original_reconciliation"]["status"] == "RECONCILED_AGAINST_FROZEN_ORIGINAL_LEDGER"
    bad = {**original, "2026-10-27": {**original["2026-10-27"], "won": 1}}
    with pytest.raises(ValueError, match="cohort mismatch"):
        build_report((pick,), start=date(2026, 10, 27), end=date(2026, 10, 27),
                     evaluated_at=_utc(11, 1), expected=bad)
