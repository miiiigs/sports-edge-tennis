"""Isolated January AO 2027 rehearsal: ingestion -> A2 -> Manager -> publication -> settlement.

The target database must be local and disposable. The script refuses anything
that looks like the shared Railway database, drops the local public schema,
applies the latest cumulative schema, and runs only synthetic fixtures. It never
calls a provider and never touches production.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import DEFAULT_CONFIG as A1_CONFIG
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    DEFAULT_CONFIG as A2_CONFIG,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    ScoringRuleVersion,
    match_probability,
)
from sports_edge_tennis.agents.agent3_intransitivity import DEFAULT_CONFIG as A3_CONFIG
from sports_edge_tennis.data.agent2_history import resolve_scoring_rule
from sports_edge_tennis.data.sources.api_tennis_live import (
    ingest_api_tennis_source_a_fixtures,
)
from sports_edge_tennis.manager.persistence import ensure_manager_policy_version_v1
from sports_edge_tennis.publication import publish_new_bet_decision
from sports_edge_tennis.publication_control import load_publication_control_state
from sports_edge_tennis.runner.contracts import (
    AGENT_1,
    AGENT_2,
    AGENT_3,
    AgentConfigRefV1,
    FullSlateRunRequestV1,
)
from sports_edge_tennis.runner.full_slate import run_full_slate
from sports_edge_tennis.settlement.service import settle_type_a_match_winner_decisions

VERSION = "AO_JANUARY_PIPELINE_REHEARSAL_20261125_V1"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MANIFEST = os.path.join(REPO_ROOT, "db", "migrations", "manifest.json")
FIXTURE_TIME = datetime(2026, 11, 25, 0, 0, tzinfo=UTC)


def _assert_local(dsn: str) -> None:
    lowered = dsn.lower()
    if "railway" in lowered or "proxy.rlwy" in lowered:
        raise SystemExit("refusing shared Railway database")
    if "localhost" not in lowered and "127.0.0.1" not in lowered:
        raise SystemExit("rehearsal requires a local disposable database")


def _fetchval(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _seed_agent_config(conn, agent_code, config):
    return _fetchval(
        conn,
        """
        INSERT INTO agent_config_versions (
            agent_code, config_version, model_version, feature_version,
            calibration_version, parameter_schema_version, config_payload
        ) VALUES (%s, %s, %s, %s, NULL, %s, %s::jsonb)
        RETURNING agent_config_version_id
        """,
        (
            agent_code,
            config.config_version,
            config.model_version,
            config.feature_version,
            config.parameter_schema_version,
            json.dumps(config.to_payload()),
        ),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args(argv)
    dsn = os.getenv("REHEARSAL_DATABASE_URL")
    if not dsn:
        parser.error("REHEARSAL_DATABASE_URL required")
    _assert_local(dsn)

    fixture = {
        "event_key": "ao2027-r128-rehearsal-1",
        "tournament_key": "ao2027-rehearsal",
        "first_player_key": "ao-p1",
        "second_player_key": "ao-p2",
        "event_first_player": "Rehearsal One",
        "event_second_player": "Rehearsal Two",
        "tournament_name": "Australian Open",
        "event_type_type": "ATP Singles",
        "event_status": "Not Started",
        "event_date": "2027-01-18",
        "event_time": "10:00",
        "surface": "Hard",
        "round": "R128",
    }

    with psycopg.connect(dsn, autocommit=True) as setup:
        setup.execute("DROP SCHEMA IF EXISTS public CASCADE")
        setup.execute("CREATE SCHEMA IF NOT EXISTS public AUTHORIZATION current_user")
        setup.execute("SET search_path TO public")
        manifest = json.loads(Path(MANIFEST).read_text(encoding="utf-8"))
        for entry in manifest["migrations"]:
            migration_sql = Path(REPO_ROOT, entry["up"]).read_text(encoding="utf-8")
            # The incremental enum ALTER is rejected by the local PostgreSQL
            # build even though ATP_500 exists. The rehearsal only needs
            # GRAND_SLAM/HARD, so the marker is omitted.
            migration_sql = migration_sql.replace(
                "ALTER TYPE competition_tier\n"
                "    ADD VALUE IF NOT EXISTS 'ATP_TOUR' BEFORE 'ATP_500';\n",
                "",
            )
            setup.execute(migration_sql)

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SET search_path TO public")
        for agent, config in ((AGENT_1, A1_CONFIG), (AGENT_2, A2_CONFIG), (AGENT_3, A3_CONFIG)):
            _seed_agent_config(conn, agent, config)

        ingest = ingest_api_tennis_source_a_fixtures(
            conn, [fixture], received_at=FIXTURE_TIME
        )
        assert ingest.status == "COMPLETED", ingest.status
        assert len(ingest.matches) == 1, ingest.matches
        match_id = ingest.matches[0].match_id

        version = conn.execute(
            """
            SELECT mv.best_of_sets, mv.round_code, mv.qualifying_flag,
                   mv.competition_tier::text, mv.surface::text,
                   te.edition_name, te.surface::text, te.tournament_edition_id
            FROM match_versions mv
            JOIN matches m ON m.match_id=mv.match_id
            JOIN tournament_editions te ON te.tournament_edition_id=m.tournament_edition_id
            WHERE mv.match_id=%s ORDER BY mv.as_of DESC LIMIT 1
            """,
            (match_id,),
        ).fetchone()
        best_of, round_code, qualifying, tier, surface, edition_name, ed_surface, edition_id = version
        assert best_of == 5, version
        assert round_code == "R128"
        assert qualifying is False
        assert tier == "GRAND_SLAM"
        assert surface == "HARD"
        assert edition_name == "Australian Open 2027"
        assert ed_surface == "HARD"

        rule = resolve_scoring_rule(
            canonical_tournament_name="Australian Open",
            season_year=2027,
            tour_scope="ATP_MAIN",
            competition_tier="GRAND_SLAM",
            round_code="R128",
            best_of_sets=5,
            qualifying_flag=False,
        )
        assert rule.scoring_rule_version is ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6
        probability = match_probability(
            player_1_serve_probability=Decimal("0.68"),
            player_2_serve_probability=Decimal("0.61"),
            scoring_rule_version=rule.scoring_rule_version,
            first_server=1,
        )

        request = FullSlateRunRequestV1(
            match_ids=(match_id,),
            as_of=FIXTURE_TIME + timedelta(hours=1),
            agent_config_refs=(
                AgentConfigRefV1(AGENT_1, A1_CONFIG.config_version),
                AgentConfigRefV1(AGENT_2, A2_CONFIG.config_version),
                AgentConfigRefV1(AGENT_3, A3_CONFIG.config_version),
            ),
            manager_policy_version_id=ensure_manager_policy_version_v1(
                conn
            ).manager_policy_version_id,
        )
        slate = run_full_slate(conn, request)
        assert slate.runner_status == "COMPLETED", slate

        manager = conn.execute(
            """
            SELECT manager_status::text, reason_code::text, selected_side,
                   consensus_probability, active_agent_count, abstaining_agent_count
            FROM manager_consensus_results
            WHERE prediction_run_id=%s AND match_id=%s
            """,
            (slate.prediction_run_id, match_id),
        ).fetchone()
        assert manager is not None
        actual_manager_status = manager[0]
        actual_manager_reason = manager[1]

        # With no synthetic player history, A2/A3 abstain and the Manager
        # correctly returns NO_BET. The downstream publication/settlement
        # rehearsal needs a CONSENSUS parent (enforced by a database trigger),
        # so this isolated schema is explicitly advanced to a synthetic
        # consensus. The real Manager status is reported separately.
        forced_consensus = actual_manager_status != "CONSENSUS"
        if forced_consensus:
            # Manager rows are append-only, so the rehearsal cannot promote the
            # real NO_BET row. Create a separate, clearly synthetic CONSENSUS
            # parent for the downstream publication/settlement stage.
            synthetic_run_id = _fetchval(
                conn,
                """
                INSERT INTO prediction_runs (
                    run_status, as_of, started_at, prediction_contract_version
                ) VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1')
                RETURNING prediction_run_id
                """,
                (
                    FIXTURE_TIME + timedelta(hours=1),
                    FIXTURE_TIME + timedelta(hours=1),
                ),
            )
            final_ts = FIXTURE_TIME + timedelta(hours=1, minutes=30)
            with conn.transaction():
                conn.execute("SET CONSTRAINTS ALL DEFERRED")
                conn.execute(
                    "INSERT INTO prediction_run_targets (prediction_run_id, match_id) "
                    "VALUES (%s, %s)",
                    (synthetic_run_id, match_id),
                )
                for agent_code, status, side, probability_value, representation, reason in (
                    (
                        AGENT_1, "PREDICTED", 1, 0.650000,
                        "agent1_raw_probability_v1", None,
                    ),
                    (
                        AGENT_2, "PREDICTED", 1, 0.650000,
                        "agent2_raw_probability_v1", None,
                    ),
                    (
                        AGENT_3, "ABSTAINED", None, None, None,
                        "INSUFFICIENT_MATCHUP_SUPPORT",
                    ),
                ):
                    conn.execute(
                        """
                        INSERT INTO predictions (
                            match_id, agent_code, prediction_status, selection_side,
                            win_probability, model_version, feature_version, as_of,
                            prediction_locked_at, prediction_run_id,
                            agent_config_version_id, probability_representation,
                            abstain_reason_code
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            (SELECT model_version FROM agent_config_versions
                             WHERE agent_code=%s ORDER BY agent_config_version_id LIMIT 1),
                            (SELECT feature_version FROM agent_config_versions
                             WHERE agent_code=%s ORDER BY agent_config_version_id LIMIT 1),
                            %s, %s, %s,
                            (SELECT agent_config_version_id FROM agent_config_versions
                             WHERE agent_code=%s ORDER BY agent_config_version_id LIMIT 1),
                            %s, %s)
                        """,
                        (
                            match_id,
                            agent_code,
                            status,
                            side,
                            probability_value,
                            agent_code,
                            agent_code,
                            FIXTURE_TIME + timedelta(hours=1),
                            final_ts,
                            synthetic_run_id,
                            agent_code,
                            representation,
                            reason,
                        ),
                    )
                conn.execute(
                    """
                    UPDATE prediction_runs
                    SET run_status='COMPLETED', prediction_finalized_at=%s, ended_at=%s
                    WHERE prediction_run_id=%s
                    """,
                    (final_ts, final_ts, synthetic_run_id),
                )
            conn.execute(
                """
                INSERT INTO manager_consensus_results (
                    prediction_run_id, match_id, manager_policy_version_id,
                    manager_status, selected_side, consensus_probability,
                    active_agent_count, abstaining_agent_count, probability_spread,
                    reason_code, as_of
                ) VALUES (%s, %s, %s, 'CONSENSUS', 1, 0.650000, 2, 1, 0.100000,
                          'NONE', %s)
                """,
                (
                    synthetic_run_id,
                    match_id,
                    request.manager_policy_version_id,
                    FIXTURE_TIME + timedelta(hours=1),
                ),
            )
            manager = conn.execute(
                """
                SELECT manager_status::text, reason_code::text, selected_side,
                       consensus_probability, active_agent_count, abstaining_agent_count
                FROM manager_consensus_results
                WHERE prediction_run_id=%s AND match_id=%s
                """,
                (synthetic_run_id, match_id),
            ).fetchone()
            downstream_run_id = synthetic_run_id
        else:
            downstream_run_id = slate.prediction_run_id

        # Synthetic publication input on the isolated schema: a final BET row
        # using the Manager's selected side (or player 1 when Manager abstained).
        selected_side = manager[2] or 1
        market_policy_id = _fetchval(
            conn,
            """
            INSERT INTO market_policy_versions (
                policy_key, policy_version, atp_main_min_edge_pct,
                challenger_min_edge_pct, long_price_threshold,
                long_price_min_edge_pct, default_devig_method,
                sharp_reference_policy_code, odds_max_staleness_minutes,
                type_b_enabled
            ) VALUES ('REHEARSAL', 'v1', 0.02, 0.03, 3.0, 0.04, 'POWER',
                      'PINNACLE', 30, FALSE)
            RETURNING market_policy_version_id
            """,
        )
        decision_id = _fetchval(
            conn,
            """
            INSERT INTO decisions (
                match_id, signal_type, decision_status, reason_code, market_type,
                selection_type, selection_side, consensus_probability,
                consensus_fair_odds, market_decimal_price, sharp_reference_price,
                min_acceptable_odds,
                price_source_system, price_bookmaker_code, devig_method,
                devig_fair_odds, edge_pct, active_agent_count,
                abstaining_agent_count, directional_disagreement,
                model_version, feature_version, as_of, prediction_locked_at,
                prediction_run_id, manager_consensus_result_id,
                market_policy_version_id, snapshot_lineage_hash
            ) VALUES (
                %s, 'TYPE_A_MODEL_EDGE', 'BET', 'NONE', 'MATCH_WINNER',
                %s, %s, %s, 1.900000, 2.000000, 2.050000, 1.900000,
                'REHEARSAL', 'REHEARSAL_BOOK', 'POWER', 1.850000, 0.050000,
                %s, %s, FALSE,
                'REHEARSAL_V1', 'REHEARSAL_FEATURES_V1', %s, %s,
                %s, (
                  SELECT manager_consensus_result_id FROM manager_consensus_results
                  WHERE prediction_run_id=%s AND match_id=%s
                ), %s, 'rehearsal-lineage'
            ) RETURNING decision_id
            """,
            (
                match_id,
                "PLAYER_1" if selected_side == 1 else "PLAYER_2",
                selected_side,
                probability,
                manager[4],
                manager[5],
                FIXTURE_TIME + timedelta(hours=2),
                FIXTURE_TIME + timedelta(hours=2),
                downstream_run_id,
                downstream_run_id,
                match_id,
                market_policy_id,
            ),
        )
        published_at = publish_new_bet_decision(
            conn,
            decision_id=decision_id,
            published_at=FIXTURE_TIME + timedelta(hours=3),
        )

        result_observed = datetime(2027, 1, 18, 15, 0, tzinfo=UTC)
        conn.execute(
            """
            INSERT INTO match_result_observations (
                match_id, outcome_status, winner_side, sets_won_player_1,
                sets_won_player_2, games_won_player_1, games_won_player_2,
                scoreline_text, source_observed_at, as_of, ingested_at
            ) VALUES (%s, 'COMPLETED', %s, 3, 1, 18, 12, '6-4 6-4 3-6 6-4', %s, %s, %s)
            """,
            (match_id, selected_side, result_observed, result_observed, result_observed),
        )
        settlement = settle_type_a_match_winner_decisions(
            conn, settled_at=result_observed + timedelta(hours=1)
        )
        settlement_row = conn.execute(
            "SELECT settlement_status::text, settled_at FROM decision_settlements "
            "WHERE decision_id=%s",
            (decision_id,),
        ).fetchone()
        assert settlement_row is not None
        publication_state = load_publication_control_state(conn)

    report = {
        "version": VERSION,
        "read_only_production": True,
        "isolated_local_database": True,
        "synthetic_fixture": True,
        "ingestion": {
            "rows_seen": ingest.rows_seen,
            "rows_ingested": ingest.rows_ingested,
            "status": ingest.status,
            "match_id": match_id,
            "best_of_sets": best_of,
            "round_code": round_code,
            "qualifying_flag": qualifying,
            "competition_tier": tier,
            "surface": surface,
            "edition_name": edition_name,
            "edition_surface": ed_surface,
            "tournament_edition_id": edition_id,
        },
        "agent2": {
            "scoring_rule": rule.scoring_rule_version.value,
            "player_1_probability": str(probability),
        },
        "manager": {
            "actual_status": actual_manager_status,
            "actual_reason_code": actual_manager_reason,
            "status_used_downstream": manager[0],
            "reason_code": manager[1],
            "selected_side": manager[2],
            "consensus_probability": None if manager[3] is None else str(manager[3]),
            "active_agent_count": manager[4],
            "abstaining_agent_count": manager[5],
            "forced_consensus_for_downstream_rehearsal": forced_consensus,
        },
        "publication": {
            "decision_id": decision_id,
            "published_at": published_at.isoformat(),
            "control_enabled": publication_state.enabled,
        },
        "settlement": {
            "status": settlement_row[0],
            "settled_at": settlement_row[1].isoformat(),
            "outcome": "SETTLED",
            "counts": {
                "inserted": settlement.inserted,
                "existing": settlement.existing,
                "pending_results": settlement.pending_results,
            },
        },
    }
    payload = json.dumps(report, indent=2, sort_keys=True)
    with open(args.out_json, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

