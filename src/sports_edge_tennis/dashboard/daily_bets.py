"""Read-only daily ledger for real Type A Tennis bets.

This module never mutates decisions, settlements, CLV, model state, or provider data.
A match enters client performance once, at its first persisted Type A BET. Hourly
refreshes may create newer decision evidence for the same match, but they must not
manufacture duplicate historical wagers or inflate win/loss sample size.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable

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
class PeriodBetSummary:
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


def aggregate_bet_summaries(
    summaries: Iterable[DailyBetSummary],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> PeriodBetSummary:
    """Roll daily summaries into one read-only period summary.

    ROI intentionally uses the same denominator as the daily ledger: one unit per
    settled bet. Pending bets do not contribute P/L or stake until they settle.
    """
    selected = [
        row
        for row in summaries
        if (start_date is None or row.decision_date >= start_date)
        and (end_date is None or row.decision_date <= end_date)
    ]
    bets = sum(row.bets for row in selected)
    settled = sum(row.settled for row in selected)
    pending = sum(row.pending for row in selected)
    wins = sum(row.wins for row in selected)
    losses = sum(row.losses for row in selected)
    voids = sum(row.voids for row in selected)
    cancelled = sum(row.cancelled for row in selected)
    profit = sum((row.one_unit_profit for row in selected), Decimal("0"))
    return PeriodBetSummary(
        bets=bets,
        settled=settled,
        pending=pending,
        wins=wins,
        losses=losses,
        voids=voids,
        cancelled=cancelled,
        one_unit_profit=profit,
        roi=(profit / Decimal(settled)) if settled else None,
        hit_rate=(wins / (wins + losses)) if (wins + losses) else None,
    )


def build_performance_windows(
    summaries: Iterable[DailyBetSummary],
    *,
    as_of_date: date,
) -> dict[str, PeriodBetSummary]:
    """Return UTC performance windows by scheduled match day from the persisted ledger."""
    rows = tuple(summaries)
    return {
        "today": aggregate_bet_summaries(
            rows,
            start_date=as_of_date,
            end_date=as_of_date,
        ),
        "last_7_days": aggregate_bet_summaries(
            rows,
            start_date=as_of_date - timedelta(days=6),
            end_date=as_of_date,
        ),
        "last_30_days": aggregate_bet_summaries(
            rows,
            start_date=as_of_date - timedelta(days=29),
            end_date=as_of_date,
        ),
        "all_time": aggregate_bet_summaries(rows, end_date=as_of_date),
    }


def load_daily_bet_summaries(
    conn: psycopg.Connection,
    *,
    limit: int = 5000,
) -> tuple[DailyBetSummary, ...]:
    """Return one aggregate row per UTC scheduled match day, newest first.

    Client results count one publishable Type A BET per sporting match. Legacy
    bets from before the publication timestamp boundary are retained, while
    newer BET decisions that never crossed publication are excluded. The day is
    the point-in-time scheduled match day, not the earlier publication day.
    """
    if limit < 1 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")

    rows = conn.execute(
        """
        WITH publication_boundary AS (
            SELECT min(published_at) AS first_published_at
            FROM decisions
            WHERE published_at IS NOT NULL
        ),
        first_bets AS (
            SELECT DISTINCT ON (d.match_id)
                d.decision_id,
                d.match_id,
                d.as_of,
                COALESCE(d.published_at, d.as_of) AS published_at,
                d.market_decimal_price
            FROM decisions d
            CROSS JOIN publication_boundary pb
            WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
              AND d.decision_status = 'BET'
              AND d.market_type = 'MATCH_WINNER'
              AND (
                  d.published_at IS NOT NULL
                  OR pb.first_published_at IS NULL
                  OR d.as_of < pb.first_published_at
              )
            ORDER BY d.match_id, COALESCE(d.published_at, d.as_of), d.decision_id
        ),
        dated_bets AS (
            SELECT
                d.*,
                COALESCE(
                    (mv.match_start_at AT TIME ZONE 'UTC')::date,
                    (d.published_at AT TIME ZONE 'UTC')::date
                ) AS match_date
            FROM first_bets d
            LEFT JOIN LATERAL (
                SELECT mv0.match_start_at
                FROM match_versions mv0
                WHERE mv0.match_id = d.match_id
                  AND mv0.as_of <= d.published_at
                  AND mv0.source_observed_at <= d.published_at
                ORDER BY mv0.as_of DESC,
                         mv0.source_observed_at DESC,
                         mv0.match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
        )
        SELECT
            d.match_date,
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
            COUNT(*) FILTER (WHERE ds.settlement_status IN ('VOID', 'PUSH'))::int AS voids,
            COUNT(*) FILTER (WHERE ds.settlement_status = 'CANCELLED')::int AS cancelled,
            COALESCE(
                SUM(
                    CASE ds.settlement_status
                        WHEN 'WON' THEN d.market_decimal_price - 1
                        WHEN 'LOST' THEN -1
                        WHEN 'VOID' THEN 0
                        WHEN 'PUSH' THEN 0
                        WHEN 'CANCELLED' THEN 0
                        ELSE 0
                    END
                ),
                0
            ) AS one_unit_profit
        FROM dated_bets d
        LEFT JOIN decision_settlements ds ON ds.decision_id = d.decision_id
        GROUP BY d.match_date
        ORDER BY d.match_date DESC
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
    if status in {"VOID", "PUSH", "CANCELLED"}:
        return Decimal("0")
    if status == "PENDING":
        return None
    raise ValueError(f"unsupported settlement_status: {status}")


def load_daily_bets(
    conn: psycopg.Connection,
    *,
    decision_date: date,
) -> tuple[DailyBetRow, ...]:
    """Return the first publishable Type A BET per match for one UTC match day."""
    rows = conn.execute(
        """
        WITH publication_boundary AS (
            SELECT min(published_at) AS first_published_at
            FROM decisions
            WHERE published_at IS NOT NULL
        ),
        first_bets AS (
            SELECT DISTINCT ON (d.match_id)
                d.decision_id,
                d.match_id,
                d.as_of,
                COALESCE(d.published_at, d.as_of) AS published_at,
                d.selection_side,
                d.market_decimal_price,
                d.price_bookmaker_code,
                d.consensus_probability
            FROM decisions d
            CROSS JOIN publication_boundary pb
            WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
              AND d.decision_status = 'BET'
              AND d.market_type = 'MATCH_WINNER'
              AND (
                  d.published_at IS NOT NULL
                  OR pb.first_published_at IS NULL
                  OR d.as_of < pb.first_published_at
              )
            ORDER BY d.match_id, COALESCE(d.published_at, d.as_of), d.decision_id
        )
        SELECT
            d.decision_id,
            d.published_at,
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
        FROM first_bets d
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
              AND mv0.as_of <= d.published_at
              AND mv0.source_observed_at <= d.published_at
            ORDER BY mv0.as_of DESC,
                     mv0.source_observed_at DESC,
                     mv0.match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        LEFT JOIN players p1 ON p1.player_id = mv.player_1_id
        LEFT JOIN players p2 ON p2.player_id = mv.player_2_id
        LEFT JOIN decision_settlements ds ON ds.decision_id = d.decision_id
        WHERE COALESCE(
            (mv.match_start_at AT TIME ZONE 'UTC')::date,
            (d.published_at AT TIME ZONE 'UTC')::date
        ) = %s
        ORDER BY d.published_at DESC, d.decision_id DESC
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

