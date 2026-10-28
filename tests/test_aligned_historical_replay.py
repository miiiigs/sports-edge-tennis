"""Production read-only historical replay: preserve original aligned W/L."""
from datetime import date
from decimal import Decimal

from sports_edge_tennis.performance.aligned_historical_replay import (
    DAYS, RULES, ReplayPick, _counts, aligned_replay_sql,
    load_aligned_replay_picks, render_aligned_replay,
)


def row(mid, day, p, result, *, side=1, surface="CLAY",
        a1=1, a2=1, a1p=".80", a2p=".81", ptype="MANAGER_CONSENSUS"):
    return ReplayPick(
        mid, date(2026, 10, day), ptype, Decimal(str(p)), side,
        surface, result, a1, a2,
        Decimal(a1p) if a1p is not None else None,
        Decimal(a2p) if a2p is not None else None,
    )


def test_replay_filters_report_skipped_winners_and_losses_without_rewriting():
    p = (
        row(1, 24, .82, "WON"),
        row(2, 24, .65, "LOST"),
        row(3, 24, .84, "LOST"),
        row(4, 24, .66, "WON"),
        row(5, 24, .78, "VOID"),
        row(6, 24, .64, "PENDING"),
    )
    original = _counts(p, "original")
    challenger = _counts(p, "confidence80")
    assert (original["original"], original["wins"], original["losses"],
            original["void"], original["pending"]) == (6, 2, 2, 1, 1)
    assert (challenger["retained"], challenger["wins"], challenger["losses"]) == (2, 1, 1)
    assert (challenger["avoided_losses"], challenger["missed_winners"]) == (1, 1)
    assert all(x.outcome in ("WON","LOST","VOID","PENDING") for x in p)
    line = next(
        x for x in render_aligned_replay(p)
        if "day=2026-10-24 rule=confidence80" in x
    )
    assert "missed_winners=1" in line and "status=RETROSPECTIVE_BELOW73" in line


def test_agent_disagreement_requires_correct_same_side():
    p = (
        row(1,24,.80,"WON",a1=1,a2=1,a1p=".79",a2p=".80"),
        row(2,24,.80,"LOST",a1=1,a2=2,a1p=".79",a2p=".80"),
        row(3,24,.80,"WON",a1=1,a2=1,a1p=".79",a2p=None),
        row(4,24,.80,"LOST",a1=1,a2=1,a1p=".71",a2p=".81"),
    )
    x = _counts(p, "a1a2_same_side_gap_lt05")
    assert x["retained"] == 1
    assert x["wins"] == 1
    assert x["avoided_losses"] == 2
    assert x["missed_winners"] == 1


def test_full_postgres_schema_sql_is_read_only_and_original_pit(db):
    query = aligned_replay_sql()
    assert "aligned_production_model_picks_cte" not in query
    assert "aligned_model_candidates AS (" in query
    assert "mv0.as_of <= p.picked_at" in query
    assert "mv0.source_observed_at <= p.picked_at" in query
    assert "a1.prediction_run_id = p.prediction_run_id" in query
    assert "mro.as_of DESC" in query
    assert "INSERT" not in query and "UPDATE " not in query
    assert db.execute(query, (DAYS[0], DAYS[-1])).fetchall() == []


def test_no_picks_never_claims_73():
    x = render_aligned_replay(tuple())
    assert len(x) == 1 + 4*len(RULES)
    assert all("status=NO_ORIGINAL_PICKS" in line for line in x[1:])
