"""Read-only lifecycle view for Tennis model picks shown during a slate day.

The client dashboard refreshes throughout the day, while prediction runs are
append-only.  This module bridges those two truths: once a match has reached
Manager CONSENSUS, the first observed consensus remains part of the day's
client-visible history even if a later run changes model eligibility or market
value.

No rows are inserted, updated, or deleted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import psycopg


@dataclass(frozen=True)
class PickLifecycleView:
    match_id: int
    player_1: str
    player_2: str
    tournament: str
    competition_tier: str | None
    scheduled_start: datetime | None
    first_pick_at: datetime
    first_selected_side: int
    first_probability: Decimal | None
    latest_model_at: datetime
    latest_manager_status: str
    latest_selected_side: int | None
    latest_probability: Decimal | None
    latest_active_agent_count: int
    latest_abstaining_agent_count: int
    latest_market_status: str | None
    latest_market_reason: str | None
    latest_market_price: Decimal | None
    latest_bookmaker: str | None
    latest_edge_pct: Decimal | None

    @property
    def first_selected_player(self) -> str:
        return self.player_1 if self.first_selected_side == 1 else self.player_2

    @property
    def latest_selected_player(self) -> str | None:
        if self.latest_selected_side == 1:
            return self.player_1
        if self.latest_selected_side == 2:
            return self.player_2
        return None

    @property
    def model_pick_still_active(self) -> bool:
        return (
            self.latest_manager_status == "CONSENSUS"
            and self.latest_selected_side == self.first_selected_side
        )

    @property
    def model_pick_changed(self) -> bool:
        return (
            self.latest_manager_status == "CONSENSUS"
            and self.latest_selected_side is not None
            and self.latest_selected_side != self.first_selected_side
        )

    @property
    def latest_lifecycle_state(self) -> str:
        """Plain state for the client layer; keeps prediction and price separate."""
        if self.model_pick_changed:
            return "MODEL_CHANGED"
        if not self.model_pick_still_active:
            return "MODEL_NO_LONGER_QUALIFIES"
        if self.latest_market_status == "BET":
            return "VALUE_BET_NOW"
        if self.latest_market_status == "VETOED":
            return "SAFETY_VETOED"
        if self.latest_market_status == "NO_BET":
            return "PICK_ONLY_NO_VALUE_NOW"
        return "PICK_ACTIVE_PRICE_PENDING"


def load_pick_lifecycle(
    conn: psycopg.Connection,
    *,
    slate_date: date,
) -> tuple[PickLifecycleView, ...]:
    """Return every match that reached consensus at least once on ``slate_date``.

    The first CONSENSUS row represents the first model pick visible that day.
    The newest Manager row represents current model state.  The newest market
    decision for the same day represents current price/value state.  Keeping
    these independently prevents a later market refresh from erasing the
    earlier model pick from client history.
    """
    manager_rows = conn.execute(
        """
        SELECT
            mcr.match_id,
            mcr.manager_status::text,
            mcr.selected_side,
            mcr.consensus_probability,
            mcr.active_agent_count,
            mcr.abstaining_agent_count,
            mcr.as_of,
            mcr.manager_consensus_result_id
        FROM manager_consensus_results mcr
        JOIN prediction_runs pr
          ON pr.prediction_run_id = mcr.prediction_run_id
        WHERE pr.as_of::date = %s
          AND pr.run_status = 'COMPLETED'
        ORDER BY mcr.match_id, mcr.as_of, mcr.manager_consensus_result_id
        """,
        (slate_date,),
    ).fetchall()

    if not manager_rows:
        return ()

    grouped: dict[int, list[tuple]] = {}
    for row in manager_rows:
        grouped.setdefault(int(row[0]), []).append(row)

    first_and_latest: dict[int, tuple[tuple, tuple]] = {}
    for match_id, rows in grouped.items():
        first_consensus = next((row for row in rows if row[1] == "CONSENSUS"), None)
        if first_consensus is None or first_consensus[2] not in (1, 2):
            continue
        first_and_latest[match_id] = (first_consensus, rows[-1])

    if not first_and_latest:
        return ()

    match_ids = list(first_and_latest)
    match_base = _load_match_base(conn, match_ids)
    market_latest = _load_latest_market_decisions(conn, match_ids, slate_date)

    result: list[PickLifecycleView] = []
    for match_id, (first_row, latest_row) in first_and_latest.items():
        info = match_base.get(match_id)
        if info is None:
            continue
        market = market_latest.get(match_id, {})
        result.append(
            PickLifecycleView(
                match_id=match_id,
                player_1=info["player_1"],
                player_2=info["player_2"],
                tournament=info["tournament"],
                competition_tier=info["competition_tier"],
                scheduled_start=info["scheduled_start"],
                first_pick_at=first_row[6],
                first_selected_side=int(first_row[2]),
                first_probability=first_row[3],
                latest_model_at=latest_row[6],
                latest_manager_status=str(latest_row[1]),
                latest_selected_side=latest_row[2],
                latest_probability=latest_row[3],
                latest_active_agent_count=int(latest_row[4]),
                latest_abstaining_agent_count=int(latest_row[5]),
                latest_market_status=market.get("decision_status"),
                latest_market_reason=market.get("reason_code"),
                latest_market_price=market.get("market_decimal_price"),
                latest_bookmaker=market.get("price_bookmaker_code"),
                latest_edge_pct=market.get("edge_pct"),
            )
        )

    result.sort(
        key=lambda row: (
            row.scheduled_start is None,
            row.scheduled_start or row.first_pick_at,
            row.match_id,
        )
    )
    return tuple(result)


def _load_match_base(conn: psycopg.Connection, match_ids: list[int]) -> dict[int, dict]:
    rows = conn.execute(
        """
        SELECT
            m.match_id,
            t.canonical_name,
            mv.competition_tier,
            mv.match_start_at,
            p1.canonical_name,
            p2.canonical_name
        FROM matches m
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN LATERAL (
            SELECT player_1_id, player_2_id, competition_tier, match_start_at
            FROM match_versions
            WHERE match_id = m.match_id
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        WHERE m.match_id = ANY(%s)
        """,
        (match_ids,),
    ).fetchall()
    return {
        int(row[0]): {
            "tournament": str(row[1]),
            "competition_tier": row[2],
            "scheduled_start": row[3],
            "player_1": str(row[4]),
            "player_2": str(row[5]),
        }
        for row in rows
    }


def _load_latest_market_decisions(
    conn: psycopg.Connection,
    match_ids: list[int],
    slate_date: date,
) -> dict[int, dict]:
    rows = conn.execute(
        """
        SELECT DISTINCT ON (match_id)
            match_id,
            decision_status::text,
            reason_code::text,
            market_decimal_price,
            price_bookmaker_code,
            edge_pct
        FROM decisions
        WHERE match_id = ANY(%s)
          AND as_of::date = %s
          AND manager_consensus_result_id IS NOT NULL
        ORDER BY match_id, as_of DESC, decision_id DESC
        """,
        (match_ids, slate_date),
    ).fetchall()
    return {
        int(row[0]): {
            "decision_status": str(row[1]),
            "reason_code": str(row[2]),
            "market_decimal_price": row[3],
            "price_bookmaker_code": row[4],
            "edge_pct": row[5],
        }
        for row in rows
    }
