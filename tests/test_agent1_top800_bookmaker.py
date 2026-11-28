"""Pure tests for the Agent 1 top-800 commercial evidence adapter."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sports_edge_tennis.validation.agent1_top800_bookmaker import (
    _preissue_pair,
    _quote_state,
    _retrospective_pair,
    coverage,
    summarize,
)

LOCK = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def _quote(*, side="PLAYER_1", price=2.0, observed=None, ingested=None,
           status="OPEN", source="SHARP", bookmaker="PINNACLE") -> dict:
    observed = observed or LOCK - timedelta(hours=1)
    return {
        "source": source, "bookmaker": bookmaker, "side": side,
        "price": price, "status": status, "observed": observed,
        "as_of": observed, "ingested": ingested or observed,
        "locked_at": LOCK,
    }


def _row(match_id: int, side: int, confidence: float, winner: int) -> dict:
    p1 = confidence if side == 1 else 1 - confidence
    return {
        "match_id": match_id, "selected_side": side, "confidence": confidence,
        "p1": p1, "winner": winner, "settled": True, "tour": "CHALLENGER",
        "start_at": LOCK + timedelta(hours=1), "tournament_id": 1,
    }


def test_quote_state_separates_preissue_late_backfilled_and_stale():
    assert _quote_state(_quote()) == "PREISSUE"
    assert _quote_state(_quote(observed=LOCK + timedelta(minutes=1))) == "LATE"
    assert _quote_state(_quote(
        observed=LOCK - timedelta(hours=10),
        ingested=LOCK + timedelta(hours=1))) == "BACKFILLED"
    assert _quote_state(_quote(observed=LOCK - timedelta(hours=10))) == "STALE"


def test_preissue_pair_requires_both_sides_open_and_synchronised():
    pair = _preissue_pair([
        _quote(side="PLAYER_1", price=1.8),
        _quote(side="PLAYER_2", price=2.0),
    ])
    assert pair is not None
    assert pair["p1"] == (1 / 1.8) / (1 / 1.8 + 1 / 2.0)
    assert _preissue_pair([_quote(side="PLAYER_1")]) is None
    assert _preissue_pair([
        _quote(side="PLAYER_1", price=1.8),
        _quote(side="PLAYER_2", price=2.0, observed=LOCK - timedelta(minutes=10)),
    ]) is None


def test_coverage_marks_unmatched_and_late_only():
    assert coverage(_row(1, 1, .7, 1), [])["unmatched"] is True
    late = coverage(_row(1, 1, .7, 1),
                    [_quote(observed=LOCK + timedelta(hours=1))])
    assert late["late_only"] is True
    assert late["preissue_two_sided"] is False


def test_retrospective_pair_is_classified_and_never_marked_preissue():
    pair = _retrospective_pair(_row(1, 1, .7, 1), [
        _quote(side="PLAYER_1", price=1.8, observed=LOCK + timedelta(minutes=5)),
        _quote(side="PLAYER_2", price=2.0, observed=LOCK + timedelta(minutes=5)),
    ])
    assert pair is not None
    assert pair["classification"] == ["LATE"]
    assert _retrospective_pair(_row(1, 1, .7, 1), [
        _quote(side="PLAYER_1", price=1.8),
        _quote(side="PLAYER_2", price=2.0),
    ]) is None


def test_summarize_reports_retrospective_separately_from_original_time():
    rows = [_row(i, 1, .70, 1 if i % 2 == 0 else 2) for i in range(25)]
    quotes = {
        i: [_quote(side="PLAYER_1", price=1.8, observed=LOCK + timedelta(minutes=5)),
            _quote(side="PLAYER_2", price=2.0, observed=LOCK + timedelta(minutes=5))]
        for i in range(25)
    }
    report = summarize("test", "synthetic", rows, quotes)
    assert report["bookmaker"]["priced"] == 0
    assert report["retrospective_bookmaker"]["priced"] == 25
    assert report["retrospective_bookmaker"]["state_counts"] == {"LATE": 25}
    assert report["market_vs_model"]["n"] == 25


def test_summarize_does_not_extrapolate_the_priced_subset():
    rows = [_row(1, 1, .70, 1), _row(2, 1, .65, 2)]
    quotes = {
        1: [_quote(side="PLAYER_1", price=1.8), _quote(side="PLAYER_2", price=2.0)],
        2: [_quote(side="PLAYER_1", observed=LOCK + timedelta(hours=1))],
    }
    report = summarize("test", "synthetic", rows, quotes)
    assert report["settled"] == 2
    assert report["actual_wins"] == 1
    assert report["model_expected_wins"] == 1.35
    assert report["bookmaker"]["priced"] == 1
    assert report["bookmaker"]["coverage"] == 0.5
    assert report["coverage"]["late_only"] == 1
    assert report["coverage"]["unmatched"] == 0
    assert "not projected" in report["bookmaker"]["no_extrapolation"]
