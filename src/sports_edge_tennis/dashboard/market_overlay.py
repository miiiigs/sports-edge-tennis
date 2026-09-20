"""Read-only persisted Market V1 decision overlay for dashboard runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import psycopg


@dataclass(frozen=True)
class MarketDecisionView:
    match_id: int
    decision_status: str
    reason_code: str
    selected_side: int | None
    market_decimal_price: Decimal | None
    sharp_reference_price: Decimal | None
    price_bookmaker_code: str | None
    edge_pct: Decimal | None
    min_acceptable_odds: Decimal | None
    suggested_stake_fraction: Decimal | None = None
    published_at: datetime | None = None


def get_market_decisions_for_run(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int | None,
) -> dict[int, MarketDecisionView]:
    if prediction_run_id is None:
        return {}
    rows = conn.execute(
        """
        SELECT DISTINCT ON (match_id)
            match_id,
            decision_status::text,
            reason_code::text,
            selection_side,
            market_decimal_price,
            sharp_reference_price,
            price_bookmaker_code,
            edge_pct,
            min_acceptable_odds,
            suggested_stake_fraction,
            published_at
        FROM decisions
        WHERE prediction_run_id = %s
          AND manager_consensus_result_id IS NOT NULL
        ORDER BY match_id, as_of DESC, decision_id DESC
        """,
        (prediction_run_id,),
    ).fetchall()
    return {
        row[0]: MarketDecisionView(
            match_id=row[0],
            decision_status=row[1],
            reason_code=row[2],
            selected_side=row[3],
            market_decimal_price=row[4],
            sharp_reference_price=row[5],
            price_bookmaker_code=row[6],
            edge_pct=row[7],
            min_acceptable_odds=row[8],
            suggested_stake_fraction=row[9],
            published_at=row[10],
        )
        for row in rows
    }
