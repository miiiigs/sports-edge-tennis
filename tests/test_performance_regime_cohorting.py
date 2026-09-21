"""Performance must be attributable to the regime that produced it.

Pooling a Manager v1 bet with a Manager v2 bet answers neither "how is the
current system doing" nor "how did the previous one do". These tests pin the
cohorting so the two questions stay separately answerable, and so historical
truth is never rewritten to make the current number look different.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.dashboard.daily_bets import (
    build_performance_windows,
    load_daily_bet_summaries,
    load_daily_bets,
)
from sports_edge_tennis.manager import (
    ensure_manager_policy_version_v1,
    evaluate_and_persist_manager_consensus,
)
from sports_edge_tennis.market import ensure_market_policy_version_v1
from sports_edge_tennis.performance.regimes import (
    AGENT_SLOTS,
    COMPLETE_REGIME_PREFIX,
    INCONSISTENT_CURRENT_REGIME_KEY,
    DATA_PROVIDER_UNAVAILABLE,
    LEGACY_REGIME_PREFIX,
    Dimension,
    RegimeCohort,
    RegimeFingerprint,
    current_regime_started_at,
    is_complete_regime_key,
    load_regime_cohorts,
    regime_label_for,
    resolve_current_regime,
)

AGENTS = (
    "AGENT_1_SURFACE_ELO",
    "AGENT_2_HIERARCHICAL_MARKOV",
    "AGENT_3_INTRANSITIVITY",
)
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

#: The canonical Agent configuration set. Every run shares it unless a test is
#: specifically about an Agent changing, so "same system" fixtures really are
#: the same system all the way down to Agent config identity.
DEFAULT_AGENT_CONFIGS = ("a1-base", "a2-base", "a3-base")


def _val(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _policy_id(conn: psycopg.Connection, version: str) -> int:
    """Resolve a Manager policy version, seeding reference data if truncated.

    The v1 row comes from its production helper and the v2 row mirrors
    migration 0014 exactly, so the fixture cannot drift from the real policy
    semantics the Manager enforces.
    """
    if version == "v1":
        ensure_manager_policy_version_v1(conn)
    else:
        conn.execute(
            """
            INSERT INTO manager_policy_versions (
                policy_key, policy_version, consensus_mode,
                allow_agent3_abstention, three_agent_max_probability_spread,
                agent3_abstention_max_probability_spread, min_active_agent_probability
            )
            VALUES ('tennis_manager_consensus', %s, 'UNANIMOUS_ACTIVE_AGENTS',
                    FALSE, 0.080000, 0.000000, 0.500000)
            ON CONFLICT (policy_key, policy_version) DO NOTHING
            """,
            (version,),
        )
    return _val(
        conn,
        """
        SELECT manager_policy_version_id
        FROM manager_policy_versions
        WHERE policy_key = 'tennis_manager_consensus' AND policy_version = %s
        """,
        (version,),
    )


def _decision_policy_id(conn: psycopg.Connection) -> int:
    """Reference-data row required by the decisions FK. Values are immaterial to
    regime cohorting; only its existence is."""
    existing = conn.execute(
        "SELECT decision_policy_version_id FROM decision_policy_versions LIMIT 1"
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    return _val(
        conn,
        """
        INSERT INTO decision_policy_versions (
            policy_key, policy_version, consensus_mode, allow_agent3_abstention,
            max_probability_spread, min_active_agent_probability,
            requires_data_sufficiency, min_recent_matches_with_serve_stats,
            odds_feed_max_staleness_minutes, match_data_max_staleness_hours,
            ratings_max_staleness_hours, fixture_feed_max_staleness_hours,
            atp_main_all_agents_min_edge_pct, atp_main_agent3_abstain_min_edge_pct,
            challenger_min_edge_pct, default_devig_method,
            sharp_reference_policy_code, veto_policy_code,
            publication_requires_min_acceptable_odds, publish_retractions_required
        )
        VALUES (
            'tennis_decision_policy', 'regime-fixture-v1', 'UNANIMOUS_ACTIVE_AGENTS',
            TRUE, 0.080000, 0.500000, TRUE, 10, 30, 12, 12, 12,
            0.030000, 0.040000, 0.050000, 'POWER',
            'PINNACLE_ONLY', 'VETO_V1', TRUE, FALSE
        )
        RETURNING decision_policy_version_id
        """,
    )


def _market_policy_id(conn: psycopg.Connection, version: str) -> int:
    """Resolve a Market policy version, mirroring migration 0008's seed row.

    Only ``policy_version`` matters to regime identity; the thresholds are
    copied from the real v1 row so the fixture cannot drift into a shape the
    Market layer would reject.
    """
    if version == "v1":
        return int(ensure_market_policy_version_v1(conn).market_policy_version_id)
    conn.execute(
        """
        INSERT INTO market_policy_versions (
            policy_key, policy_version, atp_main_min_edge_pct,
            challenger_min_edge_pct, long_price_threshold,
            long_price_min_edge_pct, default_devig_method,
            sharp_reference_policy_code, odds_max_staleness_minutes,
            type_b_enabled
        )
        VALUES ('tennis_market_value', %s, 0.070000, 0.090000, 4.000000,
                0.090000, 'POWER', 'PINNACLE_SHARP', 15, FALSE)
        ON CONFLICT (policy_key, policy_version) DO NOTHING
        """,
        (version,),
    )
    return _val(
        conn,
        """
        SELECT market_policy_version_id FROM market_policy_versions
        WHERE policy_key = 'tennis_market_value' AND policy_version = %s
        """,
        (version,),
    )


def _agent_config_id(conn: psycopg.Connection, agent: str, config_version: str) -> int:
    """Reuse an Agent config row across runs, or create it the first time.

    Reuse is the point: a new ``agent_config_versions`` row per run would make
    every run its own regime and hide exactly the pooling these tests check.
    """
    conn.execute(
        """
        INSERT INTO agent_config_versions (
            agent_code, config_version, model_version, feature_version,
            parameter_schema_version, config_payload
        )
        VALUES (%s, %s, 'regime-fixture', 'regime-feature', 'params-v1',
                '{"regime_fixture": true}'::jsonb)
        ON CONFLICT (agent_code, config_version) DO NOTHING
        """,
        (agent, config_version),
    )
    return _val(
        conn,
        """
        SELECT agent_config_version_id FROM agent_config_versions
        WHERE agent_code = %s AND config_version = %s
        """,
        (agent, config_version),
    )


def _seed_match(conn: psycopg.Connection, *, label: str, start: datetime) -> int:
    p1 = _val(
        conn,
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (f"{label} One",),
    )
    p2 = _val(
        conn,
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (f"{label} Two",),
    )
    tid = _val(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, 'ATP_MAIN', 'ATP_250') RETURNING tournament_id
        """,
        (f"{label} Open",),
    )
    eid = _val(
        conn,
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name)
        VALUES (%s, 2026, %s) RETURNING tournament_edition_id
        """,
        (tid, f"{label} Open 2026"),
    )
    mid = _val(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (eid,),
    )
    conn.execute(
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
        (mid, p1, p2, start, BASE, BASE, BASE),
    )
    return int(mid)


def _seed_run(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
    experiment_id: str | None = None,
    agent_config_versions: tuple[str, str, str] = DEFAULT_AGENT_CONFIGS,
    extra_matches: tuple[tuple[int, tuple[str, str, str]], ...] = (),
) -> int:
    """One production run.

    ``extra_matches`` lets a single run cover more matches, each with its own
    Agent configuration. Targets can only be attached while the run is still
    RUNNING (``validate_prediction_run_target_insert``), so a run that
    straddles two Agent sets has to be built in one pass rather than extended
    afterwards.
    """
    finalized = as_of + timedelta(minutes=10)
    with conn.transaction():
        conn.execute(
            "SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED"
        )
        run_id = _val(
            conn,
            """
            INSERT INTO prediction_runs (
                run_status, as_of, started_at, prediction_contract_version, experiment_id
            )
            VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1', %s)
            RETURNING prediction_run_id
            """,
            (as_of, as_of, experiment_id),
        )
        targets = ((match_id, agent_config_versions), *extra_matches)
        for target_match_id, target_configs in targets:
            conn.execute(
                "INSERT INTO prediction_run_targets (prediction_run_id, match_id) "
                "VALUES (%s, %s)",
                (run_id, target_match_id),
            )
            _seed_predictions(
                conn,
                match_id=target_match_id,
                run_id=run_id,
                agent_config_versions=target_configs,
                as_of=as_of,
                finalized=finalized,
                experiment_id=experiment_id,
            )
        conn.execute(
            """
            UPDATE prediction_runs
            SET run_status = 'COMPLETED', prediction_finalized_at = %s, ended_at = %s
            WHERE prediction_run_id = %s
            """,
            (finalized, finalized + timedelta(minutes=1), run_id),
        )
    return int(run_id)


def _seed_predictions(
    conn: psycopg.Connection,
    *,
    match_id: int,
    run_id: int,
    agent_config_versions: tuple[str, str, str],
    as_of: datetime,
    finalized: datetime,
    experiment_id: str | None = None,
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
                VALUES (%s, %s, %s, %s, 'PREDICTED', 1, 0.60,
                        'regime-fixture', 'regime-feature', %s, %s, %s, %s)
                """,
                (
                    match_id, run_id, cfg, agent, as_of, finalized,
                    AGENT_PROBABILITY_REPRESENTATIONS[agent], experiment_id,
                ),
            )


def _seed_bet(
    conn: psycopg.Connection,
    *,
    label: str,
    policy_version: str,
    match_day: date,
    price: str,
    settlement: str | None,
    published: bool = True,
    experiment_id: str | None = None,
    price_source_system: str | None = "OFFERED",
    agent_config_versions: tuple[str, str, str] = DEFAULT_AGENT_CONFIGS,
    market_policy_version: str = "v1",
    with_staking: bool = False,
) -> int:
    """One production Type A BET produced under a named system configuration."""
    start = datetime(
        match_day.year, match_day.month, match_day.day, 12, 0, tzinfo=timezone.utc
    )
    as_of = start - timedelta(hours=4)
    match_id = _seed_match(conn, label=label, start=start)
    run_id = _seed_run(
        conn,
        match_id=match_id,
        as_of=as_of,
        experiment_id=experiment_id,
        agent_config_versions=agent_config_versions,
    )

    outcome = evaluate_and_persist_manager_consensus(
        conn,
        prediction_run_id=run_id,
        manager_policy_version_id=_policy_id(conn, policy_version),
    )
    assert outcome.status == "PERSISTED"
    manager_id = _val(
        conn,
        """
        SELECT manager_consensus_result_id FROM manager_consensus_results
        WHERE prediction_run_id = %s AND match_id = %s
        """,
        (run_id, match_id),
    )
    market_policy_id = _market_policy_id(conn, market_policy_version)
    decision_policy = _decision_policy_id(conn)
    decision_id = _val(
        conn,
        """
        INSERT INTO decisions (
            match_id, prediction_run_id, decision_policy_version_id,
            manager_consensus_result_id, market_policy_version_id,
            snapshot_lineage_hash, signal_type, decision_status, reason_code,
            market_type, selection_type, selection_side,
            consensus_probability, consensus_fair_odds, market_decimal_price,
            sharp_reference_price, price_source_system, price_bookmaker_code,
            devig_method, devig_fair_odds, edge_pct, min_acceptable_odds,
            active_agent_count, abstaining_agent_count,
            model_version, feature_version, experiment_id,
            staking_rule_version, staking_probability, staking_decimal_price,
            full_kelly_fraction, quarter_kelly_fraction, suggested_stake_fraction,
            as_of, prediction_locked_at, published_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            'TYPE_A_MODEL_EDGE', 'BET', 'NONE',
            'MATCH_WINNER', 'PLAYER_1', 1,
            0.60, 1.666667, %s, 1.60, %s, 'BOOKIE',
            'MULTIPLICATIVE', 1.60, 0.05, 1.50,
            3, 0, 'regime-fixture', 'regime-feature', %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s
        )
        RETURNING decision_id
        """,
        (
            match_id, run_id, decision_policy, manager_id,
            market_policy_id,
            f"lineage-{label}", price, price_source_system, experiment_id,
            # The schema permits exactly one staking rule version, or none at
            # all: chk_decisions_staking_rule_coherent pins the whole group.
            *(
                ("QUARTER_KELLY_CAP_2_V1", "0.60", price, "0.20", "0.05", "0.02")
                if with_staking
                else (None, None, None, None, None, None)
            ),
            as_of, as_of + timedelta(minutes=10),
            (as_of + timedelta(minutes=20)) if published else None,
        ),
    )
    if settlement is not None:
        conn.execute(
            """
            INSERT INTO decision_settlements (
                decision_id, settlement_status, settled_at
            )
            VALUES (%s, %s, %s)
            """,
            (decision_id, settlement, start + timedelta(hours=3)),
        )
    return int(decision_id)


class _KeepOpen:
    """The dashboard routes close the connection they are handed.

    The test fixture's connection is shared across assertions, so hand the
    routes a proxy whose close() is a no-op instead of monkeypatching the
    route's own lifecycle.
    """

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        return None


def _cohort(cohorts: tuple[RegimeCohort, ...], key: str) -> RegimeCohort:
    found = [c for c in cohorts if c.regime_key == key]
    assert found, f"no cohort {key} in {[c.regime_key for c in cohorts]}"
    return found[0]


def _describe(cohorts: tuple[RegimeCohort, ...]) -> list[tuple]:
    return [
        (c.regime_key, c.manager_policy_version, c.agent_config_versions,
         c.market_policy_version)
        for c in cohorts
    ]


def _cohort_for(
    cohorts: tuple[RegimeCohort, ...],
    *,
    manager: str | None = None,
    agents: tuple[str, str, str] = DEFAULT_AGENT_CONFIGS,
    market: str = "v1",
) -> RegimeCohort:
    """Find a cohort by the lineage it was seeded with, not by a literal key.

    The key is a fingerprint hash computed in SQL, so a test that hard-coded it
    would pin the hash rather than the behaviour. Describing the system and
    letting the lookup find it keeps the assertions about systems.
    """
    found = [
        c
        for c in cohorts
        if c.manager_policy_version == manager
        and c.agent_config_versions == agents
        and c.market_policy_version == market
    ]
    assert found, (
        f"no cohort for manager={manager} agents={agents} market={market} "
        f"in {_describe(cohorts)}"
    )
    assert len(found) == 1, f"ambiguous cohort: {_describe(cohorts)}"
    return found[0]


def _legacy_cohorts(cohorts: tuple[RegimeCohort, ...]) -> list[RegimeCohort]:
    return [c for c in cohorts if not c.has_complete_lineage]


# ----------------------------------------------------------- pure helpers


def _fingerprint(
    *,
    manager: str | None = "v1",
    agents: tuple[str, str, str] = DEFAULT_AGENT_CONFIGS,
    market: str | None = "v1",
    veto: str | None = None,
    conflicted_agent: str | None = None,
) -> RegimeFingerprint:
    slots = []
    for (tag, _), value in zip(AGENT_SLOTS, agents):
        slots.append(
            Dimension.conflicted(tag) if tag == conflicted_agent
            else Dimension.of(tag, value)
        )
    return RegimeFingerprint(
        manager=Dimension.of("MGR", manager),
        agents=tuple(slots),
        market=Dimension.of("MKT", market),
        veto=Dimension.of("VETO", veto),
    )


def test_regime_keys_and_labels_never_expose_database_ids() -> None:
    key = _fingerprint().regime_key
    assert key.startswith(COMPLETE_REGIME_PREFIX)
    # A key is a fingerprint digest: no policy id, config id or row id in it.
    assert all(part not in key for part in ("a1-base", "v1", "tennis"))
    assert regime_label_for("v1", complete=True) == "Manager v1"
    assert (
        regime_label_for("v1", complete=True, sequence=2)
        == "Manager v1 · configuration 2"
    )
    assert regime_label_for(None, complete=False) == "Legacy production regime"
    # An unproven regime is never given an invented Manager version.
    assert regime_label_for("v1", complete=False) == "Legacy production regime"


def test_every_fingerprint_dimension_changes_the_regime_key() -> None:
    """Each persisted dimension is load-bearing, not decoration."""
    base = _fingerprint().regime_key
    variants = {
        "manager": _fingerprint(manager="v2"),
        "agent_1": _fingerprint(agents=("a1-NEW", "a2-base", "a3-base")),
        "agent_2": _fingerprint(agents=("a1-base", "a2-NEW", "a3-base")),
        "agent_3": _fingerprint(agents=("a1-base", "a2-base", "a3-NEW")),
        "market": _fingerprint(market="v2"),
        "veto": _fingerprint(veto="tennis_veto_v2"),
    }
    keys = {name: fp.regime_key for name, fp in variants.items()}
    assert base not in keys.values(), keys
    assert len(set(keys.values())) == len(keys), keys


def test_incomplete_lineage_can_never_match_a_complete_regime() -> None:
    """A missing or conflicting dimension is quarantined, never assumed."""
    complete = _fingerprint()
    assert complete.is_complete

    for broken in (
        _fingerprint(manager=None),
        _fingerprint(market=None),
        _fingerprint(agents=("a1-base", None, "a3-base")),
        _fingerprint(conflicted_agent="A2"),
    ):
        assert not broken.is_complete
        assert broken.regime_key.startswith(LEGACY_REGIME_PREFIX)
        assert not is_complete_regime_key(broken.regime_key)
        assert broken.regime_key != complete.regime_key

    # Missing and conflicting are different facts and stay different regimes.
    assert (
        _fingerprint(agents=("a1-base", None, "a3-base")).regime_key
        != _fingerprint(conflicted_agent="A2").regime_key
    )


def test_veto_is_fingerprinted_but_absence_does_not_strand_a_system() -> None:
    """A Veto change splits cohorts; a Veto table that was never written does not.

    ``decision_veto_audits`` is created by the Veto service, not a migration, so
    requiring it would push an otherwise fully-identified system into legacy.
    """
    without = _fingerprint(veto=None)
    v1 = _fingerprint(veto="tennis_veto_v1")
    v2 = _fingerprint(veto="tennis_veto_v2")

    assert without.is_complete and v1.is_complete and v2.is_complete
    assert len({without.regime_key, v1.regime_key, v2.regime_key}) == 3


def test_unproven_provider_is_reported_unavailable_not_guessed() -> None:
    cohort = RegimeCohort(
        regime_key="REGIME_abc123", manager_policy_version="v1", bets=1, settled=1,
        pending=0, wins=1, losses=0, voids=0, cancelled=0,
        one_unit_profit=Decimal("0.8"), first_bet_date=None, last_bet_date=None,
        odds_sources=(),
    )
    assert cohort.odds_provider_label == DATA_PROVIDER_UNAVAILABLE
    for banned in ("Sportradar", "Goalserve", "API-Tennis", "Pinnacle"):
        assert banned not in cohort.odds_provider_label


# ------------------------------------------------------- cohorting truth


def test_v1_and_v2_bets_form_separate_cohorts(db: psycopg.Connection) -> None:
    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")

    cohorts = load_regime_cohorts(db)

    assert len(cohorts) == 2, _describe(cohorts)
    v1, v2 = _cohort_for(cohorts, manager="v1"), _cohort_for(cohorts, manager="v2")
    assert (v1.bets, v1.wins, v1.losses) == (1, 1, 0)
    assert (v2.bets, v2.wins, v2.losses) == (0 + 1, 0, 1)
    # Original persisted price, not a recomputed one: 2.00 -> +1.00 on a win.
    assert v1.one_unit_profit == Decimal("1.00")
    assert v2.one_unit_profit == Decimal("-1")
    assert v1.label == "Manager v1 · configuration 1"
    assert v2.label == "Manager v2 · configuration 2"


def test_current_system_excludes_previous_regime_bets(db: psycopg.Connection) -> None:
    """The central claim: Current System contains only the active regime."""
    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")

    current = resolve_current_regime(db)
    assert current is not None
    assert current.regime_key == _cohort_for(load_regime_cohorts(db), manager="v2").regime_key

    current_only = load_daily_bet_summaries(db, regime_key=current.regime_key)
    all_history = load_daily_bet_summaries(db)

    assert sum(r.bets for r in current_only) == 1
    assert sum(r.wins for r in current_only) == 0
    assert sum(r.losses for r in current_only) == 1
    # The v1 win is absent from Current System but present in history.
    assert sum(r.bets for r in all_history) == 2
    assert sum(r.wins for r in all_history) == 1


def test_previous_regime_is_preserved_after_a_new_regime_appears(
    db: psycopg.Connection,
) -> None:
    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    before = load_regime_cohorts(db)
    v1_before = _cohort_for(before, manager="v1")

    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")
    after = load_regime_cohorts(db)
    v1_after = _cohort_for(after, manager="v1")

    # Adding a newer regime must not touch the older record in any way. The
    # display ordinal is the one thing that may move, because it describes the
    # cohort's position in a sequence that just grew; the record does not.
    assert v1_after.regime_key == v1_before.regime_key
    assert (v1_after.bets, v1_after.wins, v1_after.losses) == (
        v1_before.bets, v1_before.wins, v1_before.losses
    )
    assert v1_after.one_unit_profit == v1_before.one_unit_profit
    assert (v1_after.first_bet_date, v1_after.last_bet_date) == (
        v1_before.first_bet_date, v1_before.last_bet_date
    )


def test_current_regime_resolves_from_production_lineage_not_environment(
    db: psycopg.Connection, monkeypatch
) -> None:
    _seed_bet(db, label="Only", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    # Environment claiming the target stack must not relabel persisted history.
    monkeypatch.setenv("TENNIS_MANAGER_POLICY_VERSION", "v2")
    monkeypatch.setenv("TENNIS_DATA_PROVIDER_MODE", "SPORTRADAR_V3")

    current = resolve_current_regime(db)

    assert current is not None
    assert current.manager_policy_version == "v1"
    assert current.regime_key == _cohort_for(load_regime_cohorts(db), manager="v1").regime_key
    assert current.label == "Manager v1 · current model configuration"


def test_no_production_history_yields_no_current_regime(db: psycopg.Connection) -> None:
    assert resolve_current_regime(db) is None
    assert load_regime_cohorts(db) == ()


# ------------------------------------------------ contamination boundaries


def test_experiment_bets_never_contaminate_production_performance(
    db: psycopg.Connection,
) -> None:
    _seed_bet(db, label="Real", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="Lab", policy_version="v1", match_day=date(2026, 9, 3),
              price="5.00", settlement="WON", experiment_id="BACKTEST_A")

    cohorts = load_regime_cohorts(db)
    summaries = load_daily_bet_summaries(db)

    assert _cohort_for(cohorts, manager="v1").bets == 1
    assert sum(r.bets for r in summaries) == 1
    assert sum(r.one_unit_profit for r in summaries) == Decimal("1.00")


def test_unpublished_modern_bet_rows_remain_excluded(db: psycopg.Connection) -> None:
    _seed_bet(db, label="Published", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="Unpublished", policy_version="v1", match_day=date(2026, 9, 4),
              price="3.00", settlement="WON", published=False)

    summaries = load_daily_bet_summaries(db)

    assert sum(r.bets for r in summaries) == 1
    assert _cohort_for(load_regime_cohorts(db), manager="v1").bets == 1


def test_repeat_hourly_bets_on_one_match_count_once(db: psycopg.Connection) -> None:
    """Cohorting must not let a later duplicate become a match's first bet."""
    _seed_bet(db, label="First", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    match_id = _val(db, "SELECT max(match_id) FROM matches")
    run_id = _val(db, "SELECT max(prediction_run_id) FROM prediction_runs")
    original = _val(
        db,
        "SELECT decision_id FROM decisions WHERE match_id = %s",
        (match_id,),
    )
    # A second, later BET decision for the very same sporting match.
    db.execute(
        """
        INSERT INTO decisions (
            match_id, prediction_run_id, decision_policy_version_id,
            manager_consensus_result_id, market_policy_version_id,
            snapshot_lineage_hash, signal_type, decision_status, reason_code,
            market_type, selection_type, selection_side,
            consensus_probability, consensus_fair_odds, market_decimal_price,
            sharp_reference_price, price_source_system, price_bookmaker_code,
            devig_method, devig_fair_odds, edge_pct, min_acceptable_odds,
            active_agent_count, abstaining_agent_count,
            model_version, feature_version, as_of, prediction_locked_at, published_at
        )
        SELECT
            match_id, prediction_run_id, decision_policy_version_id,
            manager_consensus_result_id, market_policy_version_id,
            'lineage-dup', signal_type, decision_status, reason_code,
            market_type, selection_type, selection_side,
            consensus_probability, consensus_fair_odds, 9.00,
            sharp_reference_price, price_source_system, price_bookmaker_code,
            devig_method, devig_fair_odds, edge_pct, min_acceptable_odds,
            active_agent_count, abstaining_agent_count,
            model_version, feature_version,
            as_of + interval '1 hour',
            prediction_locked_at + interval '1 hour',
            published_at + interval '1 hour'
        FROM decisions WHERE decision_id = %s
        """,
        (original,),
    )

    summaries = load_daily_bet_summaries(db)
    cohort = _cohort_for(load_regime_cohorts(db), manager="v1")

    assert sum(r.bets for r in summaries) == 1
    assert cohort.bets == 1
    # The original 2.00 price is used, never the later 9.00 duplicate.
    assert cohort.one_unit_profit == Decimal("1.00")

    cohort_filtered = load_daily_bet_summaries(db, regime_key=cohort.regime_key)
    assert sum(r.bets for r in cohort_filtered) == 1


# --------------------------------------------------- original bet identity


def test_historical_rows_carry_their_own_regime_and_original_terms(
    db: psycopg.Connection,
) -> None:
    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")

    (row,) = load_daily_bets(db, decision_date=date(2026, 9, 2))

    assert row.manager_policy_version == "v1"
    assert row.regime_key == _cohort_for(load_regime_cohorts(db), manager="v1").regime_key
    assert row.market_policy_version == "v1"
    assert row.agent_config_versions == DEFAULT_AGENT_CONFIGS
    assert row.market_decimal_price == Decimal("2.000000")
    assert row.selection == "Old One"
    assert row.settlement_status == "WON"
    assert row.profit_units == Decimal("1.000000")


def test_regime_windows_use_the_same_first_bet_ledger(db: psycopg.Connection) -> None:
    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")

    current = resolve_current_regime(db)
    windows = build_performance_windows(
        load_daily_bet_summaries(db, regime_key=current.regime_key),
        as_of_date=date(2026, 9, 10),
    )

    assert windows["all_time"].bets == 1
    assert windows["all_time"].losses == 1
    assert windows["all_time"].wins == 0


def test_regime_start_is_the_first_production_bet_of_that_regime(
    db: psycopg.Connection,
) -> None:
    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")

    cohorts = load_regime_cohorts(db)
    started = current_regime_started_at(db, _cohort_for(cohorts, manager="v2").regime_key)

    assert started is not None
    assert started.date() == date(2026, 9, 10)
    assert current_regime_started_at(
        db, _cohort_for(cohorts, manager="v1").regime_key
    ).date() == date(2026, 9, 2)


# -------------------------------------------------- client surface contract


def test_results_api_separates_current_regime_from_all_history(
    db: psycopg.Connection, monkeypatch
) -> None:
    """The API must expose the two questions as two different numbers."""
    from fastapi.testclient import TestClient

    import sports_edge_tennis.dashboard.app as dashboard_app
    import sports_edge_tennis.dashboard.daily_history as daily_history
    from sports_edge_tennis.dashboard.production_app import app

    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")

    monkeypatch.setattr(dashboard_app, "DEMO_MODE", False)
    monkeypatch.setattr(daily_history, "DEMO_MODE", False)
    monkeypatch.setattr(daily_history, "_connect", lambda: _KeepOpen(db))
    monkeypatch.setattr(daily_history, "_latest_activity", lambda: None)

    body = TestClient(app).get("/api/dashboard/bet-history").json()

    cohorts = load_regime_cohorts(db)
    v1_key = _cohort_for(cohorts, manager="v1").regime_key
    v2_key = _cohort_for(cohorts, manager="v2").regime_key

    assert body["current_regime"]["regime_key"] == v2_key
    assert body["current_regime"]["label"] == "Manager v2 · current model configuration"
    # Structured lineage travels with the key so a consumer never parses it.
    assert body["current_regime"]["manager_policy_version"] == "v2"
    assert body["current_regime"]["market_policy_version"] == "v1"
    assert body["current_regime"]["agent_config_versions"] == list(DEFAULT_AGENT_CONFIGS)
    # Current regime: the single v2 loss only.
    assert body["current_regime_performance"]["all_time"]["bets"] == 1
    assert body["current_regime_performance"]["all_time"]["losses"] == 1
    assert body["current_regime_performance"]["all_time"]["wins"] == 0
    # All history: both regimes preserved.
    assert body["all_historical_performance"]["all_time"]["bets"] == 2
    assert body["all_historical_performance"]["all_time"]["wins"] == 1
    # Backwards-compatible key keeps its original all-regimes meaning.
    assert body["performance_windows"]["all_time"]["bets"] == 2
    assert {r["regime_key"] for r in body["regimes"]} == {v1_key, v2_key}
    assert [r["is_current"] for r in body["regimes"] if r["regime_key"] == v2_key] == [True]


def test_results_page_shows_current_system_before_history(
    db: psycopg.Connection, monkeypatch
) -> None:
    from fastapi.testclient import TestClient

    import sports_edge_tennis.dashboard.app as dashboard_app
    import sports_edge_tennis.dashboard.daily_history as daily_history
    from sports_edge_tennis.dashboard.production_app import app

    _seed_bet(db, label="Old", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")
    _seed_bet(db, label="New", policy_version="v2", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST")

    monkeypatch.setattr(dashboard_app, "DEMO_MODE", False)
    monkeypatch.setattr(daily_history, "DEMO_MODE", False)
    monkeypatch.setattr(daily_history, "_connect", lambda: _KeepOpen(db))
    monkeypatch.setattr(daily_history, "_latest_activity", lambda: None)

    body = TestClient(app).get("/bet-history").text

    assert "Current system" in body
    assert "All historical performance" in body
    assert body.index("Current system") < body.index("All historical performance")
    assert "Manager v2" in body
    # History must say plainly that it combines system versions.
    assert "combining" in body and "more than one system version" in body
    # And must promise no retroactive re-scoring.
    assert "nothing is re-scored against today" in body


def test_performance_page_marks_static_metrics_as_historical_validation(
    monkeypatch,
) -> None:
    from fastapi.testclient import TestClient

    import sports_edge_tennis.dashboard.app as dashboard_app
    from sports_edge_tennis.dashboard.production_app import app

    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    body = TestClient(app).get("/performance").text

    assert "Historical model validation" in body
    assert "historical validation figures, not the live betting record" in body
    assert "not recomputed when the production system changes" in body
    # The forbidden framings must not appear anywhere on the page.
    for banned in ("Current accuracy", "current live accuracy", "Current system performance"):
        assert banned not in body


def test_agent_evidence_declares_it_spans_all_regimes() -> None:
    """Agent accuracy is all-production evidence and must say so."""
    import inspect

    from sports_edge_tennis.dashboard import model_evidence_routes
    from sports_edge_tennis.performance import evidence

    source = inspect.getsource(model_evidence_routes)
    assert '"agents_scope": "ALL_PRODUCTION_REGIMES"' in source
    assert "across all " in source
    assert "not current-system evidence" in (evidence.__doc__ or "")


def test_decisions_without_manager_lineage_become_legacy_not_a_guess(
    db: psycopg.Connection,
) -> None:
    """Pre-versioning rows are labelled legacy, never given an invented Manager.

    The schema guarantees a Type A BET records ``price_source_system``
    (decisions_check4), so the offered-price provider is always proven for a
    real bet. What is never recorded anywhere is the *data* provider, which is
    why no data-provider claim is made for any historical row.
    """
    _seed_bet(db, label="Versioned", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")

    # A decision that predates Manager lineage: permitted by the schema
    # (chk_decisions_market_links_coherent allows a NULL manager link).
    match_id = _seed_match(
        db, label="Ancient", start=datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    )
    run_id = _seed_run(
        db, match_id=match_id, as_of=datetime(2026, 9, 3, 8, tzinfo=timezone.utc)
    )
    legacy_id = _val(
        db,
        """
        INSERT INTO decisions (
            match_id, prediction_run_id, decision_policy_version_id,
            signal_type, decision_status, reason_code, market_type,
            selection_type, selection_side, consensus_probability,
            consensus_fair_odds, market_decimal_price, sharp_reference_price,
            price_source_system, price_bookmaker_code, devig_method,
            devig_fair_odds, edge_pct, min_acceptable_odds,
            active_agent_count, abstaining_agent_count,
            model_version, feature_version, as_of, prediction_locked_at, published_at
        )
        VALUES (
            %s, %s, %s, 'TYPE_A_MODEL_EDGE', 'BET', 'NONE', 'MATCH_WINNER',
            'PLAYER_1', 1, 0.60, 1.666667, 3.00, 1.60,
            'OFFERED', 'BOOKIE', 'MULTIPLICATIVE', 1.60, 0.05, 1.50, 3, 0,
            'legacy', 'legacy', %s, %s, %s
        )
        RETURNING decision_id
        """,
        (
            match_id, run_id, _decision_policy_id(db),
            datetime(2026, 9, 3, 8, tzinfo=timezone.utc),
            datetime(2026, 9, 3, 8, 10, tzinfo=timezone.utc),
            datetime(2026, 9, 3, 8, 20, tzinfo=timezone.utc),
        ),
    )
    db.execute(
        "INSERT INTO decision_settlements (decision_id, settlement_status, settled_at) "
        "VALUES (%s, 'WON', %s)",
        (legacy_id, datetime(2026, 9, 3, 15, tzinfo=timezone.utc)),
    )

    cohorts = load_regime_cohorts(db)
    (legacy,) = _legacy_cohorts(cohorts)

    assert not legacy.has_complete_lineage
    assert legacy.regime_key.startswith(LEGACY_REGIME_PREFIX)
    assert legacy.bets == 1
    assert legacy.label == "Legacy production regime"
    assert legacy.manager_policy_version is None
    # The offered-price provider is reported from what was actually recorded.
    assert legacy.odds_sources == ("OFFERED",)
    # No unproven provider is ever attributed to a historical row.
    for banned in ("Sportradar", "Goalserve", "API-Tennis", "Manager v"):
        assert banned not in legacy.label
        assert banned not in legacy.odds_provider_label

    # The legacy bet is preserved in history but excluded from Current System.
    current = resolve_current_regime(db)
    versioned_key = _cohort_for(cohorts, manager="v1").regime_key
    assert current.regime_key == versioned_key
    assert sum(r.bets for r in load_daily_bet_summaries(db)) == 2
    assert sum(r.bets for r in load_daily_bet_summaries(db, regime_key=versioned_key)) == 1


# ------------------------------------------- system identity beyond Manager
#
# Manager policy version alone cannot support the claim the dashboard makes.
# These are the acceptance tests for that claim: if any of them can be made to
# pass while pooling two different configurations, the claim is false.


def test_same_manager_different_agent_config_are_distinct_systems(
    db: psycopg.Connection,
) -> None:
    """A2 changes, Manager stays v1: that is a new system, not more of the old.

    This is the case Manager-only cohorting got wrong. Retraining one Agent
    changes which matches reach consensus and at what probability, so its bets
    belong to a different record.
    """
    old_agents = ("a1-base", "a2-base", "a3-base")
    new_agents = ("a1-base", "a2-retrained", "a3-base")

    _seed_bet(db, label="AgentsOld", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON", agent_config_versions=old_agents)
    before = load_regime_cohorts(db)
    old_before = _cohort_for(before, manager="v1", agents=old_agents)

    _seed_bet(db, label="AgentsNew", policy_version="v1", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST", agent_config_versions=new_agents)

    cohorts = load_regime_cohorts(db)
    old = _cohort_for(cohorts, manager="v1", agents=old_agents)
    new = _cohort_for(cohorts, manager="v1", agents=new_agents)

    # Two systems, both reporting Manager v1, with different keys.
    assert len(cohorts) == 2, _describe(cohorts)
    assert old.regime_key != new.regime_key
    assert old.manager_policy_version == new.manager_policy_version == "v1"

    # Current System is the new Agent set only. The old win does not flatter it.
    current = resolve_current_regime(db)
    assert current is not None
    assert current.regime_key == new.regime_key
    assert current.agent_config_versions == new_agents

    current_only = load_daily_bet_summaries(db, regime_key=current.regime_key)
    assert sum(r.bets for r in current_only) == 1
    assert sum(r.wins for r in current_only) == 0
    assert sum(r.losses for r in current_only) == 1

    # All Historical still contains both.
    all_history = load_daily_bet_summaries(db)
    assert sum(r.bets for r in all_history) == 2
    assert sum(r.wins for r in all_history) == 1

    # The previous Agent set's record is preserved exactly as it was.
    assert old.regime_key == old_before.regime_key
    assert (old.bets, old.wins, old.losses) == (1, 1, 0)
    assert old.one_unit_profit == old_before.one_unit_profit == Decimal("1.00")


def test_same_manager_and_agents_different_market_policy_are_distinct_systems(
    db: psycopg.Connection,
) -> None:
    """Market v1 and Market v2 bets are different systems under one Manager.

    The Market policy sets the edge threshold a price must clear, so it decides
    which bets exist at all. Two thresholds are two systems.
    """
    _seed_bet(db, label="MarketOld", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON", market_policy_version="v1")
    _seed_bet(db, label="MarketNew", policy_version="v1", match_day=date(2026, 9, 10),
              price="1.50", settlement="LOST", market_policy_version="v2")

    cohorts = load_regime_cohorts(db)
    old = _cohort_for(cohorts, manager="v1", market="v1")
    new = _cohort_for(cohorts, manager="v1", market="v2")

    assert len(cohorts) == 2, _describe(cohorts)
    assert old.regime_key != new.regime_key
    assert old.manager_policy_version == new.manager_policy_version == "v1"
    assert old.agent_config_versions == new.agent_config_versions

    current = resolve_current_regime(db)
    assert current is not None
    assert current.regime_key == new.regime_key
    assert current.market_policy_version == "v2"

    assert sum(
        r.bets for r in load_daily_bet_summaries(db, regime_key=current.regime_key)
    ) == 1
    assert sum(r.bets for r in load_daily_bet_summaries(db)) == 2
    assert (old.bets, old.wins) == (1, 1)


def test_veto_policy_change_splits_cohorts(db: psycopg.Connection) -> None:
    """A Veto version change is a system change: it alters which bets survive."""
    from sports_edge_tennis.veto.service import ensure_veto_audit_ledger

    ensure_veto_audit_ledger(db)

    old_id = _seed_bet(db, label="VetoOld", policy_version="v1",
                       match_day=date(2026, 9, 2), price="2.00", settlement="WON")
    new_id = _seed_bet(db, label="VetoNew", policy_version="v1",
                       match_day=date(2026, 9, 10), price="1.50", settlement="LOST")
    for decision_id, version in ((old_id, "tennis_veto_v1"), (new_id, "tennis_veto_v2")):
        db.execute(
            """
            INSERT INTO decision_veto_audits (
                decision_id, veto_policy_version, evaluation_as_of, vetoed,
                triggered_codes, unavailable_codes, evidence
            )
            VALUES (%s, %s, %s, FALSE, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb)
            """,
            (decision_id, version, BASE),
        )

    cohorts = load_regime_cohorts(db)
    by_veto = {c.veto_policy_version: c for c in cohorts}

    assert set(by_veto) == {"tennis_veto_v1", "tennis_veto_v2"}, _describe(cohorts)
    assert by_veto["tennis_veto_v1"].regime_key != by_veto["tennis_veto_v2"].regime_key
    # Everything else about the two systems is identical.
    assert by_veto["tennis_veto_v1"].manager_policy_version == "v1"
    assert by_veto["tennis_veto_v2"].manager_policy_version == "v1"

    current = resolve_current_regime(db)
    assert current.veto_policy_version == "tennis_veto_v2"
    assert current.regime_key == by_veto["tennis_veto_v2"].regime_key


def test_inconsistent_agent_lineage_in_one_run_is_reported_not_collapsed(
    db: psycopg.Connection,
) -> None:
    """A run that disagrees with itself yields no Current System claim.

    ``uq_predictions_run_match_agent`` guarantees one Agent config per agent
    per match, so a *decision* always has a canonical Agent set. A run can
    still straddle two Agent sets across different matches, and when it does
    the honest answer is that the current configuration is undetermined --
    not whichever of the two happened to sort first.
    """
    base_agents = ("a1-base", "a2-base", "a3-base")
    other_agents = ("a1-base", "a2-DIFFERENT", "a3-base")
    as_of = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)

    first = _seed_match(
        db, label="RunSplitA", start=datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    )
    second = _seed_match(
        db, label="RunSplitB", start=datetime(2026, 9, 10, 18, tzinfo=timezone.utc)
    )
    run_id = _seed_run(
        db,
        match_id=first,
        as_of=as_of,
        agent_config_versions=base_agents,
        extra_matches=((second, other_agents),),
    )
    outcome = evaluate_and_persist_manager_consensus(
        db, prediction_run_id=run_id, manager_policy_version_id=_policy_id(db, "v1")
    )
    assert outcome.status == "PERSISTED"

    for match_id, label in ((first, "a"), (second, "b")):
        manager_id = _val(
            db,
            "SELECT manager_consensus_result_id FROM manager_consensus_results "
            "WHERE prediction_run_id = %s AND match_id = %s",
            (run_id, match_id),
        )
        db.execute(
            """
            INSERT INTO decisions (
                match_id, prediction_run_id, decision_policy_version_id,
                manager_consensus_result_id, market_policy_version_id,
                snapshot_lineage_hash, signal_type, decision_status, reason_code,
                market_type, selection_type, selection_side,
                consensus_probability, consensus_fair_odds, market_decimal_price,
                sharp_reference_price, price_source_system, price_bookmaker_code,
                devig_method, devig_fair_odds, edge_pct, min_acceptable_odds,
                active_agent_count, abstaining_agent_count,
                model_version, feature_version, as_of, prediction_locked_at,
                published_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                'TYPE_A_MODEL_EDGE', 'BET', 'NONE', 'MATCH_WINNER', 'PLAYER_1', 1,
                0.60, 1.666667, 2.00, 1.60, 'OFFERED', 'BOOKIE',
                'MULTIPLICATIVE', 1.60, 0.05, 1.50, 3, 0,
                'regime-fixture', 'regime-feature', %s, %s, %s
            )
            """,
            (
                match_id, run_id, _decision_policy_id(db), manager_id,
                _market_policy_id(db, "v1"), f"lineage-runsplit-{label}",
                as_of, as_of + timedelta(minutes=10), as_of + timedelta(minutes=20),
            ),
        )

    # Each decision still gets the exact Agent set that produced it: the run's
    # ambiguity is never smeared across its own bets.
    cohorts = load_regime_cohorts(db)
    assert len(cohorts) == 2, _describe(cohorts)
    assert _cohort_for(cohorts, manager="v1", agents=base_agents).bets == 1
    assert _cohort_for(cohorts, manager="v1", agents=other_agents).bets == 1

    # But the run cannot name one current configuration, and says so.
    current = resolve_current_regime(db)
    assert current is not None
    assert current.is_inconsistent
    assert current.regime_key == INCONSISTENT_CURRENT_REGIME_KEY
    assert not current.has_complete_lineage
    assert current.label == "Current configuration could not be determined"
    # A quarantined identity can never collect another system's bets.
    assert current.regime_key not in {c.regime_key for c in cohorts}
    assert load_daily_bet_summaries(db, regime_key=current.regime_key) == ()


def test_sql_and_python_fingerprints_agree(db: psycopg.Connection) -> None:
    """The two fingerprint implementations must never drift apart.

    Cohorting is computed in SQL because the grouping happens in the database;
    ``resolve_current_regime`` reports in Python. If the two ever disagree a
    regime would silently split in half, so this pins them together.
    """
    _seed_bet(db, label="Mirror", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON")

    (cohort,) = load_regime_cohorts(db)
    expected = RegimeFingerprint(
        manager=Dimension.of("MGR", "v1"),
        agents=tuple(
            Dimension.of(tag, value)
            for (tag, _), value in zip(AGENT_SLOTS, DEFAULT_AGENT_CONFIGS)
        ),
        market=Dimension.of("MKT", "v1"),
        veto=Dimension.of("VETO", None),
    )

    assert cohort.regime_key == expected.regime_key
    assert resolve_current_regime(db).regime_key == expected.regime_key


def test_fingerprint_separators_inside_a_version_cannot_forge_a_regime(
    db: psycopg.Connection,
) -> None:
    """A config version containing the fingerprint's own separators is escaped.

    The fingerprint joins dimensions with ``|`` and ``:``. Without escaping, a
    config version containing those could make two different systems produce
    the same canonical string and silently pool. It is also the part of the
    SQL/Python mirror most likely to drift, so it is pinned on both sides.
    """
    awkward = ("a1-base", "a2|x:y", "a3-base")
    _seed_bet(db, label="Awkward", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON", agent_config_versions=awkward)
    _seed_bet(db, label="Plain", policy_version="v1", match_day=date(2026, 9, 3),
              price="2.00", settlement="WON",
              agent_config_versions=("a1-base", "a2-x", "a3-base"))

    cohorts = load_regime_cohorts(db)
    awkward_cohort = _cohort_for(cohorts, manager="v1", agents=awkward)

    # Two systems, not one: the separators did not collapse them together.
    assert len(cohorts) == 2, _describe(cohorts)

    # SQL and Python still agree once escaping is involved.
    expected = RegimeFingerprint(
        manager=Dimension.of("MGR", "v1"),
        agents=tuple(
            Dimension.of(tag, value)
            for (tag, _), value in zip(AGENT_SLOTS, awkward)
        ),
        market=Dimension.of("MKT", "v1"),
        veto=Dimension.of("VETO", None),
    )
    assert awkward_cohort.regime_key == expected.regime_key


def test_staking_version_does_not_split_one_unit_roi_cohorts(
    db: psycopg.Connection,
) -> None:
    """Staking is persisted but deliberately outside the fingerprint.

    ``decisions.staking_rule_version`` is recorded, but staking is computed
    after a decision is already a BET. It cannot change whether the bet enters
    the ledger or the price it was taken at, and Results ROI is a flat one-unit
    number, so a staking change cannot move it. Splitting on it would halve the
    sample and answer nothing. Bankroll performance is a separate question and
    would need it; one-unit ROI does not.

    The schema reinforces this: ``chk_decisions_staking_rule_coherent`` admits
    exactly one staking rule version or none, so a second one cannot appear
    without a migration that would be free to revisit this decision.
    """
    _seed_bet(db, label="StakeA", policy_version="v1", match_day=date(2026, 9, 2),
              price="2.00", settlement="WON", with_staking=False)
    _seed_bet(db, label="StakeB", policy_version="v1", match_day=date(2026, 9, 3),
              price="2.00", settlement="WON", with_staking=True)

    staking_versions = {
        row[0]
        for row in db.execute(
            "SELECT staking_rule_version FROM decisions ORDER BY decision_id"
        ).fetchall()
    }
    assert staking_versions == {None, "QUARTER_KELLY_CAP_2_V1"}

    cohorts = load_regime_cohorts(db)

    # One system, one record: a staked and an unstaked bet are the same system.
    assert len(cohorts) == 1, _describe(cohorts)
    assert cohorts[0].bets == 2
    # Both one-unit wins land in the same ROI, which is what staking cannot move.
    assert cohorts[0].one_unit_profit == Decimal("2.00")
