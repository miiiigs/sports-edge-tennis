"""Read-only daily ledger for real Type A Tennis bets.

This module never mutates decisions, settlements, CLV, model state, or provider data.
Daily performance is grouped by the UTC date on which the persisted BET decision
was made so a wager remains attached to its original decision day even if it
settles later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import psycopg


@dataclass(frozen=True)
class DailyBetSummary:
    decision_date: date
    bets: int
    settled: int
    pending: int
    wins: int
    losses: int
    voids: int
    cancelled: int
    one_unit_profit: Decimal
    roi: Decimal | None
    hit_rate: float | None


@dataclass(frozen=True)
class DailyBetRow:
    decision_id: int
    decision_at: datetime
    match_start_at: datetime | None
    tournament: str | None
    player_1: str | None
    player_2: str | None
    selection: str | None
    market_decimal_price: Decimal
    bookmaker_code: str | None
    consensus_probability: Decimal | None
    settlement_status: str
    settled_at: datetime | None
    profit_units: Decimal | None


def _summary_from_row(row) -> DailyBetSummary:
    settled = int(row[2])
    wins = int(row[4])
    losses = int(row[5])
    profit = Decimal(row[8]) if row[8] is not None else Decimal("0")
    return DailyBetSummary(
        decision_date=row[0],
        bets=int(row[1]),
        settled=settled,
        pending=int(row[3]),
        wins=wins,
        losses=losses,
        voids=int(row[6]),
        cancelled=int(row[7]),
        one_unit_profit=profit,
        roi=(profit / Decimal(settled)) if settled else None,
        hit_rate=(wins / (wins + losses)) if (wins + losses) else None,
    )


def load_daily_bet_summaries(
    conn: psycopg.Connection,
    *,
    limit: int = 366,
) -> tuple[DailyBetSummary, ...]:
    """Return one aggregate row per UTC decision day, newest first."""
    if limit < 1 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")

    rows = conn.execute(
        """
        SELECT
            (d.as_of AT TIME ZONE 'UTC')::date AS decision_date,
            COUNT(*)::int AS bets,
            COUNT(*) FILTER (
                WHERE ds.decision_id IS NOT NULL
                  AND ds.settlement_status <> 'PENDING'
            )::int AS settled,
            COUNT(*) FILTER (
                WHERE ds.decision_id IS NULL
                   OR ds.settlement_status = 'PENDING'
            )::int AS pending,
            COUNT(*) FILTER (WHERE ds.settlement_status = 'WON')::int AS wins,
            COUNT(*) FILTER (WHERE ds.settlement_status = 'LOST')::int AS losses,
            COUNT(*) FILTER (WHERE ds.settlement_status = 'VOID')::int AS voids,
            COUNT(*) FILTER (WHERE ds.settlement_status = 'CANCELLED')::int AS cancelled,
            COALESCE(
                SUM(
                    CASE ds.settlement_status
                        WHEN 'WON' THEN d.market_decimal_price - 1
                        WHEN 'LOST' THEN -1
                        WHEN 'VOID' THEN 0
                        WHEN 'CANCELLED' THEN 0
                        ELSE 0
                    END
                ),
                0
            ) AS one_unit_profit
        FROM decisions d
        LEFT JOIN decision_settlements ds ON ds.decision_id = d.decision_id
        WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
          AND d.decision_status = 'BET'
          AND d.market_type = 'MATCH_WINNER'
        GROUP BY (d.as_of AT TIME ZONE 'UTC')::date
        ORDER BY decision_date DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return tuple(_summary_from_row(row) for row in rows)


def _profit_for_status(status: str, price: Decimal) -> Decimal | None:
    if status == "WON":
        return price - Decimal("1")
    if status == "LOST":
        return Decimal("-1")
    if status in {"VOID", "CANCELLED"}:
        return Decimal("0")
    if status == "PENDING":
        return None
    raise ValueError(f"unsupported settlement_status: {status}")


def load_daily_bets(
    conn: psycopg.Connection,
    *,
    decision_date: date,
) -> tuple[DailyBetRow, ...]:
    """Return real persisted Type A BET decisions for one UTC decision day."""
    rows = conn.execute(
        """
        SELECT
            d.decision_id,
            d.as_of,
            mv.match_start_at,
            t.canonical_name AS tournament,
            p1.canonical_name AS player_1,
            p2.canonical_name AS player_2,
            CASE d.selection_side
                WHEN 1 THEN p1.canonical_name
                WHEN 2 THEN p2.canonical_name
                ELSE NULL
            END AS selection,
            d.market_decimal_price,
            d.price_bookmaker_code,
            d.consensus_probability,
            COALESCE(ds.settlement_status::text, 'PENDING') AS settlement_status,
            ds.settled_at
        FROM decisions d
        JOIN matches m ON m.match_id = d.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        LEFT JOIN LATERAL (
            SELECT
                mv0.player_1_id,
                mv0.player_2_id,
                mv0.match_start_at
            FROM match_versions mv0
            WHERE mv0.match_id = d.match_id
              AND mv0.as_of <= d.as_of
            ORDER BY mv0.as_of DESC,
                     mv0.source_observed_at DESC,
                     mv0.match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        LEFT JOIN players p1 ON p1.player_id = mv.player_1_id
        LEFT JOIN players p2 ON p2.player_id = mv.player_2_id
        LEFT JOIN decision_settlements ds ON ds.decision_id = d.decision_id
        WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
          AND d.decision_status = 'BET'
          AND d.market_type = 'MATCH_WINNER'
          AND (d.as_of AT TIME ZONE 'UTC')::date = %s
        ORDER BY d.as_of DESC, d.decision_id DESC
        """,
        (decision_date,),
    ).fetchall()

    result: list[DailyBetRow] = []
    for row in rows:
        price = Decimal(row[7])
        status = str(row[10])
        result.append(
            DailyBetRow(
                decision_id=int(row[0]),
                decision_at=row[1],
                match_start_at=row[2],
                tournament=str(row[3]) if row[3] is not None else None,
                player_1=str(row[4]) if row[4] is not None else None,
                player_2=str(row[5]) if row[5] is not None else None,
                selection=str(row[6]) if row[6] is not None else None,
                market_decimal_price=price,
                bookmaker_code=str(row[8]) if row[8] is not None else None,
                consensus_probability=Decimal(row[9]) if row[9] is not None else None,
                settlement_status=status,
                settled_at=row[11],
                profit_units=_profit_for_status(status, price),
            )
        )
    return tuple(result)
