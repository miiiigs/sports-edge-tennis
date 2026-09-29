"""Today's Model Pick population must equal the aligned Model Picks ledger.

These tests pin the count invariant:

    Today.total_model_pick_count == aligned selected-day summary picks
                                 == aligned selected-day detail length

for the same timezone and local match date, and prove that Today no longer
leaks sub-60% Manager consensus or adjacent-day matches from a multi-slate run.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
from test_performance_regime_cohorting import (
    AGENTS,
    DEFAULT_AGENT_CONFIGS,
    _agent_config_id,
    _policy_id,
    _seed_match,
    _seed_run,
    _val,
)

from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.dashboard import model_pick_history
from sports_edge_tennis.dashboard.market_overlay import MarketDecisionView
from sports_edge_tennis.dashboard.model_pick_history import (
    load_aligned_daily_model_pick_summaries,
    load_aligned_daily_model_picks,
)
from sports_edge_tennis.dashboard.picks import (
    MODEL_PICK,
    VALUE_BET,
    build_pick_card,
    build_today_picks,
)
from sports_edge_tennis.dashboard.read_model import (
    AgentView,
    MatchView,
    get_issued_day_state,
)
from sports_edge_tennis.dashboard.timezone import TIMEZONE_BY_KEY

A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"


# --------------------------------------------------------------- seeding


def _insert_prediction(
    conn, *, match_id, run_id, agent_code, status, side, prob, as_of, finalized
) -> None:
    cfg = _agent_config_id(
        conn, agent_code, DEFAULT_AGENT_CONFIGS[AGENTS.index(agent_code)]
    )
    representation = (
        AGENT_PROBABILITY_REPRESENTATIONS[agent_code]
        if status == "PREDICTED"
        else None
    )
    conn.execute(
        """
        INSERT INTO predictions (
            match_id, prediction_run_id, agent_config_version_id, agent_code,
            prediction_status, selection_side, win_probability, model_version,
            feature_version, as_of, prediction_locked_at, probability_representation,
            experiment_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'regime-fixture', 'regime-feature',
                %s, %s, %s, NULL)
        """,
        (
            match_id,
            run_id,
            cfg,
            agent_code,
            status,
            side,
            prob,
            as_of,
            finalized,
            representation,
        ),
    )


def _seed_pick(
    conn,
    *,
    label: str,
    start: datetime,
    manager_status: str,
    selected_side: int | None,
    consensus_probability: Decimal | None,
    reason: str,
    agents: tuple[tuple[str, str, int | None, Decimal | None], ...],
) -> int:
    """One production run + manager row with full control over the aligned inputs."""
    match_id = _seed_match(conn, label=label, start=start)
    as_of = start - timedelta(hours=4)
    finalized = as_of + timedelta(minutes=10)
    with conn.transaction():
        conn.execute(
            "SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED"
        )
        run_id = _val(
            conn,
            """
            INSERT INTO prediction_runs (
                run_status, as_of, started_at, prediction_contract_version
            ) VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1')
            RETURNING prediction_run_id
            """,
            (as_of, as_of),
        )
        conn.execute(
            "INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s, %s)",
            (run_id, match_id),
        )
        for agent_code, status, side, prob in agents:
            _insert_prediction(
                conn,
                match_id=match_id,
                run_id=run_id,
                agent_code=agent_code,
                status=status,
                side=side,
                prob=prob,
                as_of=as_of,
                finalized=finalized,
            )
        conn.execute(
            "UPDATE prediction_runs SET run_status='COMPLETED', prediction_finalized_at=%s, "
            "ended_at=%s WHERE prediction_run_id=%s",
            (finalized, finalized + timedelta(minutes=1), run_id),
        )
    _insert_manager(
        conn,
        run_id=run_id,
        match_id=match_id,
        as_of=as_of,
        manager_status=manager_status,
        selected_side=selected_side,
        consensus_probability=consensus_probability,
        reason=reason,
    )
    return match_id


def _insert_manager(
    conn,
    *,
    run_id: int,
    match_id: int,
    as_of: datetime,
    manager_status: str,
    selected_side: int | None,
    consensus_probability: Decimal | None,
    reason: str,
) -> None:
    conn.execute(
        """
        INSERT INTO manager_consensus_results (
            prediction_run_id, match_id, manager_policy_version_id, manager_status,
            selected_side, consensus_probability, active_agent_count,
            abstaining_agent_count, probability_spread, reason_code, as_of
        ) VALUES (%s, %s, %s, %s, %s, %s, 2, 1, %s, %s, %s)
        """,
        (
            run_id,
            match_id,
            _policy_id(conn, "v1"),
            manager_status,
            selected_side,
            consensus_probability,
            Decimal("0.02"),
            reason,
            as_of,
        ),
    )


def _manager_agents(prob: Decimal) -> tuple:
    return (
        (A1, "PREDICTED", 1, prob),
        (A2, "PREDICTED", 1, prob),
        (A3, "PREDICTED", 1, prob),
    )


def _wide_spread_agents(a1: Decimal, a2: Decimal) -> tuple:
    return (
        (A1, "PREDICTED", 1, a1),
        (A2, "PREDICTED", 1, a2),
        (A3, "ABSTAINED", None, None),
    )


def _seed_manager_pick(conn, *, label, start, probability: Decimal) -> int:
    return _seed_pick(
        conn,
        label=label,
        start=start,
        manager_status="CONSENSUS",
        selected_side=1,
        consensus_probability=probability,
        reason="NONE",
        agents=_manager_agents(Decimal("0.60")),
    )


def _seed_wide_pick(conn, *, label, start, a1: Decimal, a2: Decimal) -> int:
    return _seed_pick(
        conn,
        label=label,
        start=start,
        manager_status="NO_BET",
        selected_side=None,
        consensus_probability=None,
        reason="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
        agents=_wide_spread_agents(a1, a2),
    )


# --------------------------------------------------------------- helpers


def _aligned_day_picks(db, day: date, tz: str = "UTC") -> int:
    summaries = load_aligned_daily_model_pick_summaries(db, timezone_name=tz)
    row = next((r for r in summaries if r.decision_date == day), None)
    return 0 if row is None else row.picks


def _today_match_ids(db, day: date, tz: str = "UTC") -> list[int]:
    state = get_issued_day_state(db, day, tz)
    return sorted(m.match_id for m in state.matches)


def _aligned_detail_match_ids(db, day: date, tz: str = "UTC") -> list[int]:
    rows = load_aligned_daily_model_picks(db, decision_date=day, timezone_name=tz)
    return sorted(int(r.match_id) for r in rows if r.match_id is not None)


def _assert_invariant(db, day: date, tz: str = "UTC") -> None:
    state = get_issued_day_state(db, day, tz)
    today_ids = sorted(m.match_id for m in state.matches)
    detail_ids = _aligned_detail_match_ids(db, day, tz)
    assert today_ids == detail_ids

    picks = build_today_picks(state.matches, {})
    summary = _aligned_day_picks(db, day, tz)
    assert picks.total_model_pick_count == summary == len(detail_ids)


# --------------------------------------------------- manager 60% boundary


def _day(y: int, m: int, d: int) -> date:
    return date(y, m, d)


def _noon(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)


def test_manager_59_99_excluded_everywhere(db: psycopg.Connection) -> None:
    day = _day(2026, 9, 12)
    _seed_manager_pick(db, label="M5999", start=_noon(day), probability=Decimal("0.599900"))

    assert _aligned_day_picks(db, day) == 0
    assert _today_match_ids(db, day) == []


def test_manager_exactly_60_included_everywhere(db: psycopg.Connection) -> None:
    day = _day(2026, 9, 13)
    match_id = _seed_manager_pick(
        db, label="M60", start=_noon(day), probability=Decimal("0.600000")
    )

    assert _aligned_day_picks(db, day) == 1
    assert _today_match_ids(db, day) == [match_id]
    _assert_invariant(db, day)


def test_manager_over_60_included_everywhere(db: psycopg.Connection) -> None:
    day = _day(2026, 9, 14)
    match_id = _seed_manager_pick(
        db, label="M65", start=_noon(day), probability=Decimal("0.650000")
    )

    assert _aligned_day_picks(db, day) == 1
    assert _today_match_ids(db, day) == [match_id]
    _assert_invariant(db, day)


# --------------------------------------------------- wide-spread boundary


def test_wide_spread_59_99_excluded_everywhere(db: psycopg.Connection) -> None:
    day = _day(2026, 9, 15)
    _seed_wide_pick(
        db,
        label="W5999",
        start=_noon(day),
        a1=Decimal("0.599900"),
        a2=Decimal("0.599900"),
    )

    assert _aligned_day_picks(db, day) == 0
    assert _today_match_ids(db, day) == []


def test_wide_spread_exactly_60_included_everywhere(db: psycopg.Connection) -> None:
    day = _day(2026, 9, 16)
    match_id = _seed_wide_pick(
        db,
        label="W60",
        start=_noon(day),
        a1=Decimal("0.600000"),
        a2=Decimal("0.600000"),
    )

    assert _aligned_day_picks(db, day) == 1
    assert _today_match_ids(db, day) == [match_id]
    _assert_invariant(db, day)


def test_wide_spread_over_60_included_everywhere(db: psycopg.Connection) -> None:
    day = _day(2026, 9, 17)
    match_id = _seed_wide_pick(
        db,
        label="W70",
        start=_noon(day),
        a1=Decimal("0.700000"),
        a2=Decimal("0.550000"),
    )

    assert _aligned_day_picks(db, day) == 1
    assert _today_match_ids(db, day) == [match_id]
    _assert_invariant(db, day)


# --------------------------------------------------- multi-slate bucket


def test_multi_slate_run_does_not_leak_adjacent_day(db: psycopg.Connection) -> None:
    day_a = _day(2026, 9, 24)
    day_b = _day(2026, 9, 25)
    start_a = _noon(day_a)
    start_b = _noon(day_b)

    match_a = _seed_match(db, label="SlateA", start=start_a)
    match_b = _seed_match(db, label="SlateB", start=start_b)
    as_of = start_a - timedelta(hours=4)
    run_id = _seed_run(
        conn=db,
        match_id=match_a,
        as_of=as_of,
        extra_matches=((match_b, DEFAULT_AGENT_CONFIGS),),
    )
    _insert_manager(
        db, run_id=run_id, match_id=match_a, as_of=as_of,
        manager_status="CONSENSUS", selected_side=1,
        consensus_probability=Decimal("0.650000"), reason="NONE",
    )
    _insert_manager(
        db, run_id=run_id, match_id=match_b, as_of=as_of,
        manager_status="CONSENSUS", selected_side=1,
        consensus_probability=Decimal("0.650000"), reason="NONE",
    )

    # One run straddling two Manila match days; each day must contain only its
    # own match, never the other slate.
    assert _today_match_ids(db, day_a, "Asia/Manila") == [match_a]
    assert _today_match_ids(db, day_b, "Asia/Manila") == [match_b]
    _assert_invariant(db, day_a, "Asia/Manila")
    _assert_invariant(db, day_b, "Asia/Manila")


def test_same_utc_instant_buckets_differ_by_timezone(db: psycopg.Connection) -> None:
    start = datetime(2026, 9, 27, 20, 0, tzinfo=UTC)  # 04:00 PHT Sep 28
    match_id = _seed_manager_pick(
        db, label="Rollover", start=start, probability=Decimal("0.650000")
    )

    assert _today_match_ids(db, date(2026, 9, 27), "UTC") == [match_id]
    assert _today_match_ids(db, date(2026, 9, 28), "UTC") == []
    assert _today_match_ids(db, date(2026, 9, 28), "Asia/Manila") == [match_id]

    _assert_invariant(db, date(2026, 9, 27), "UTC")
    _assert_invariant(db, date(2026, 9, 28), "Asia/Manila")


def test_london_bst_boundary_bucket(db: psycopg.Connection) -> None:
    # BST (summer): 2026-09-27 23:30 UTC is 2026-09-28 00:30 BST.
    bst_match = _seed_manager_pick(
        db,
        label="LondonBST",
        start=datetime(2026, 9, 27, 23, 30, tzinfo=UTC),
        probability=Decimal("0.650000"),
    )
    assert _today_match_ids(db, date(2026, 9, 27), "UTC") == [bst_match]
    assert _today_match_ids(db, date(2026, 9, 28), "Europe/London") == [bst_match]
    assert _today_match_ids(db, date(2026, 9, 28), "UTC") == []
    _assert_invariant(db, date(2026, 9, 28), "Europe/London")

    # London is DST-aware from the real zone, never a fixed offset: GMT in
    # winter and BST in summer.
    london = TIMEZONE_BY_KEY["london"]
    assert london.abbreviation(datetime(2026, 1, 15, 12, 0, tzinfo=UTC)) == "GMT"
    assert london.abbreviation(datetime(2026, 7, 15, 12, 0, tzinfo=UTC)) == "BST"


# --------------------------------------------------- per-timezone invariant


def test_invariant_holds_for_all_three_timezones(db: psycopg.Connection) -> None:
    day = _day(2026, 9, 20)
    match_id = _seed_manager_pick(
        db, label="AllTz", start=_noon(day), probability=Decimal("0.650000")
    )
    for tz in ("UTC", "Asia/Manila", "Europe/London"):
        assert _today_match_ids(db, day, tz) == [match_id]
        _assert_invariant(db, day, tz)


# --------------------------------------------------- classification (pure)


def _manager_match() -> MatchView:
    return MatchView(
        match_id=1,
        player_1="Player A",
        player_2="Player B",
        tournament="Test Open",
        competition_tier="ATP_250",
        scheduled_start=datetime(2026, 9, 28, 14, 0, tzinfo=UTC),
        agents={},
        manager_status="CONSENSUS",
        selected_side=1,
        consensus_probability=Decimal("0.650000"),
        reason_code="NONE",
        active_agent_count=3,
        abstaining_agent_count=0,
        player_1_price=None,
        player_2_price=None,
        sharp_source_observed_at=None,
    )


def _wide_match() -> MatchView:
    return MatchView(
        match_id=2,
        player_1="Player A",
        player_2="Player B",
        tournament="Test Open",
        competition_tier="ATP_250",
        scheduled_start=datetime(2026, 9, 28, 14, 0, tzinfo=UTC),
        agents={
            A1: AgentView(A1, "PREDICTED", 1, Decimal("0.700000")),
            A2: AgentView(A2, "PREDICTED", 1, Decimal("0.550000")),
            A3: AgentView(A3, "ABSTAINED", None, None),
        },
        manager_status="NO_BET",
        selected_side=None,
        consensus_probability=None,
        reason_code="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
        active_agent_count=2,
        abstaining_agent_count=1,
        player_1_price=None,
        player_2_price=None,
        sharp_source_observed_at=None,
    )


def _bet_decision(match_id: int, side: int) -> MarketDecisionView:
    return MarketDecisionView(
        match_id=match_id,
        decision_status="BET",
        reason_code="VALUE",
        selected_side=side,
        market_decimal_price=Decimal("2.00"),
        sharp_reference_price=Decimal("2.05"),
        price_bookmaker_code="UNIBET",
        edge_pct=Decimal("0.05"),
        min_acceptable_odds=Decimal("1.90"),
        suggested_stake_fraction=Decimal("0.01"),
        published_at=datetime(2026, 9, 28, 9, 0, tzinfo=UTC),
    )


def test_eligible_manager_pick_with_bet_decision_is_value_bet() -> None:
    card = build_pick_card(_manager_match(), _bet_decision(1, 1))
    assert card is not None
    assert card.kind == VALUE_BET


def test_wide_spread_never_becomes_value_bet() -> None:
    card = build_pick_card(_wide_match(), _bet_decision(2, 1))
    assert card is not None
    assert card.kind == MODEL_PICK
    assert card.is_wide_spread is True


# --------------------------------------------------- default selected date


def test_model_picks_default_to_today_not_newest_future_slate(monkeypatch):
    today = date(2026, 9, 20)
    future = date(2026, 9, 22)
    aligned = (
        _summary(future, picks=5),
        _summary(today, picks=2),
    )
    monkeypatch.setattr(model_pick_history, "_connect", lambda: _DummyConn())
    monkeypatch.setattr(
        model_pick_history,
        "load_aligned_daily_model_pick_summaries",
        lambda conn, timezone_name="UTC": aligned,
    )
    monkeypatch.setattr(
        model_pick_history,
        "load_daily_model_pick_summaries",
        lambda conn, **kwargs: aligned,
    )
    monkeypatch.setattr(
        model_pick_history,
        "load_aligned_daily_model_picks",
        lambda conn, decision_date, timezone_name="UTC": (),
    )
    monkeypatch.setattr(
        model_pick_history, "resolve_current_model_regime", lambda conn: None
    )
    monkeypatch.setattr(
        model_pick_history, "load_model_regime_cohorts", lambda conn, **kwargs: ()
    )
    monkeypatch.setattr(model_pick_history, "dashboard_today", lambda tz: today)

    view = model_pick_history._load_history(None, TIMEZONE_BY_KEY["utc"])

    assert view.selected_date == today
    assert view.selected_summary is not None
    assert view.selected_summary.decision_date == today


def _summary(day: date, *, picks: int):
    from sports_edge_tennis.dashboard.model_pick_history import DailyModelPickSummary

    return DailyModelPickSummary(day, picks, 0, picks, 0, 0, 0, None)


class _DummyConn:
    def close(self):
        return None
