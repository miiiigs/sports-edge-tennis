from __future__ import annotations

from datetime import datetime

import psycopg

from sports_edge_tennis.market.contracts import (
    MARKET_POLICY_CURRENT_VERSION,
    MARKET_POLICY_KEY,
    MARKET_POLICY_VERSION,
    MARKET_POLICY_VERSION_V2,
    MarketPolicyError,
    MarketPolicyV1,
    OddsSnapshotV1,
    PersistedManagerConsensusRow,
    SharpReferenceSpec,
)


def load_market_policy_version(
    conn: psycopg.Connection,
    *,
    market_policy_version_id: int,
) -> MarketPolicyV1:
    row = conn.execute(
        """
        SELECT
            market_policy_version_id,
            policy_key,
            policy_version,
            atp_main_min_edge_pct,
            challenger_min_edge_pct,
            long_price_threshold,
            long_price_min_edge_pct,
            default_devig_method,
            sharp_reference_policy_code,
            odds_max_staleness_minutes,
            type_b_enabled
        FROM market_policy_versions
        WHERE market_policy_version_id = %s
        """,
        (market_policy_version_id,),
    ).fetchone()
    if row is None:
        raise MarketPolicyError(
            "market_policy_version_id must reference an existing Market policy version"
        )
    return MarketPolicyV1(*row)


def ensure_market_policy_version_v1(conn: psycopg.Connection) -> MarketPolicyV1:
    conn.execute(
        """
        INSERT INTO market_policy_versions (
            policy_key,
            policy_version,
            atp_main_min_edge_pct,
            challenger_min_edge_pct,
            long_price_threshold,
            long_price_min_edge_pct,
            default_devig_method,
            sharp_reference_policy_code,
            odds_max_staleness_minutes,
            type_b_enabled
        )
        VALUES (%s, %s, 0.050000, 0.080000, 4.000000, 0.080000, 'POWER', 'PINNACLE_SHARP', 15, FALSE)
        ON CONFLICT (policy_key, policy_version) DO NOTHING
        """,
        (MARKET_POLICY_KEY, MARKET_POLICY_VERSION),
    )
    row = conn.execute(
        """
        SELECT
            market_policy_version_id,
            policy_key,
            policy_version,
            atp_main_min_edge_pct,
            challenger_min_edge_pct,
            long_price_threshold,
            long_price_min_edge_pct,
            default_devig_method,
            sharp_reference_policy_code,
            odds_max_staleness_minutes,
            type_b_enabled
        FROM market_policy_versions
        WHERE policy_key = %s AND policy_version = %s
        """,
        (MARKET_POLICY_KEY, MARKET_POLICY_VERSION),
    ).fetchone()
    if row is None:
        raise MarketPolicyError("failed to resolve the v1 Market policy version")
    return MarketPolicyV1(*row)


def ensure_market_policy_version_v2(conn: psycopg.Connection) -> MarketPolicyV1:
    """Resolve the current Market v2 policy with the 60% betting floor.

    The database row intentionally retains the same price/edge parameters as
    v1. The policy-version identity records the behavioral cutover so production
    performance cannot mix pre-floor and post-floor bets into one regime.
    """
    conn.execute(
        """
        INSERT INTO market_policy_versions (
            policy_key,
            policy_version,
            atp_main_min_edge_pct,
            challenger_min_edge_pct,
            long_price_threshold,
            long_price_min_edge_pct,
            default_devig_method,
            sharp_reference_policy_code,
            odds_max_staleness_minutes,
            type_b_enabled
        )
        VALUES (%s, %s, 0.050000, 0.080000, 4.000000, 0.080000, 'POWER', 'PINNACLE_SHARP', 15, FALSE)
        ON CONFLICT (policy_key, policy_version) DO NOTHING
        """,
        (MARKET_POLICY_KEY, MARKET_POLICY_VERSION_V2),
    )
    row = conn.execute(
        """
        SELECT
            market_policy_version_id,
            policy_key,
            policy_version,
            atp_main_min_edge_pct,
            challenger_min_edge_pct,
            long_price_threshold,
            long_price_min_edge_pct,
            default_devig_method,
            sharp_reference_policy_code,
            odds_max_staleness_minutes,
            type_b_enabled
        FROM market_policy_versions
        WHERE policy_key = %s AND policy_version = %s
        """,
        (MARKET_POLICY_KEY, MARKET_POLICY_CURRENT_VERSION),
    ).fetchone()
    if row is None:
        raise MarketPolicyError("failed to resolve the current v2 Market policy version")
    return MarketPolicyV1(*row)


def load_manager_slate(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
) -> list[PersistedManagerConsensusRow]:
    rows = conn.execute(
        """
        SELECT
            manager_consensus_result_id,
            prediction_run_id,
            match_id,
            manager_policy_version_id,
            manager_status::text,
            selected_side,
            consensus_probability,
            active_agent_count,
            abstaining_agent_count,
            probability_spread,
            reason_code::text,
            as_of
        FROM manager_consensus_results
        WHERE prediction_run_id = %s
        ORDER BY match_id
        """,
        (prediction_run_id,),
    ).fetchall()
    return [PersistedManagerConsensusRow(*row) for row in rows]


def load_competition_tier(
    conn: psycopg.Connection,
    *,
    match_id: int,
    decision_as_of: datetime,
) -> str | None:
    row = conn.execute(
        """
        SELECT competition_tier::text
        FROM match_versions
        WHERE match_id = %s
          AND as_of <= %s
        ORDER BY as_of DESC, match_version_id DESC
        LIMIT 1
        """,
        (match_id, decision_as_of),
    ).fetchone()
    return row[0] if row is not None else None


def load_match_winner_odds(
    conn: psycopg.Connection,
    *,
    match_id: int,
    decision_as_of: datetime,
) -> list[OddsSnapshotV1]:
    rows = conn.execute(
        """
        SELECT
            odds_snapshot_id,
            match_id,
            source_system,
            bookmaker_code,
            market_type::text,
            selection_type::text,
            decimal_price,
            market_status::text,
            booksum,
            source_snapshot_id,
            source_observed_at,
            as_of
        FROM odds_snapshots
        WHERE match_id = %s
          AND market_type = 'MATCH_WINNER'
          AND source_observed_at <= %s
          AND as_of <= %s
        ORDER BY odds_snapshot_id
        """,
        (match_id, decision_as_of, decision_as_of),
    ).fetchall()
    return [OddsSnapshotV1(*row) for row in rows]


def load_run_parent(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
) -> tuple[str, datetime, datetime]:
    row = conn.execute(
        """
        SELECT run_status, prediction_contract_version, as_of, prediction_finalized_at
        FROM prediction_runs
        WHERE prediction_run_id = %s
        FOR UPDATE
        """,
        (prediction_run_id,),
    ).fetchone()
    if row is None:
        raise MarketPolicyError("prediction_run_id must reference an existing run")
    run_status, contract_version, as_of, finalized_at = row
    return run_status, contract_version, as_of, finalized_at
