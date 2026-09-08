"""Read-only upcoming fixture view for the client dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import psycopg

from sports_edge_tennis.dashboard import presentation as pres


@dataclass(frozen=True)
class UpcomingMatchView:
    match_id: int
    player_1: str
    player_2: str
    tournament: str
    competition_tier: str | None
    scheduled_start: datetime
    manager_status: str | None
    selected_side: int | None
    consensus_probability: Decimal | None

    @property
    def selected_player(self) -> str | None:
        return pres.side_name(self.selected_side, self.player_1, self.player_2)


def get_upcoming_matches(
    conn: psycopg.Connection,
    *,
    now: datetime,
    days: int = 3,
    limit: int = 24,
) -> list[UpcomingMatchView]:
    """Return the next scheduled canonical fixtures, with latest model state when present."""
    horizon = now + timedelta(days=days)
    rows = conn.execute(
        """
        SELECT
            m.match_id,
            p1.canonical_name,
            p2.canonical_name,
            t.canonical_name,
            mv.competition_tier::text,
            mv.match_start_at,
            mgr.manager_status,
            mgr.selected_side,
            mgr.consensus_probability
        FROM matches m
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN LATERAL (
            SELECT player_1_id, player_2_id, competition_tier, match_start_at, match_status
            FROM match_versions
            WHERE match_id = m.match_id
              AND as_of <= %s
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        LEFT JOIN LATERAL (
            SELECT
                mcr.manager_status::text AS manager_status,
                mcr.selected_side,
                mcr.consensus_probability
            FROM manager_consensus_results mcr
            JOIN prediction_runs pr ON pr.prediction_run_id = mcr.prediction_run_id
            WHERE mcr.match_id = m.match_id
              AND pr.run_status = 'COMPLETED'
              AND pr.as_of <= %s
            ORDER BY pr.as_of DESC, pr.prediction_run_id DESC
            LIMIT 1
        ) mgr ON TRUE
        WHERE mv.match_status = 'SCHEDULED'
          AND mv.match_start_at > %s
          AND mv.match_start_at <= %s
        ORDER BY mv.match_start_at ASC, m.match_id ASC
        LIMIT %s
        """,
        (now, now, now, horizon, limit),
    ).fetchall()

    return [
        UpcomingMatchView(
            match_id=row[0],
            player_1=row[1],
            player_2=row[2],
            tournament=row[3],
            competition_tier=row[4],
            scheduled_start=row[5],
            manager_status=row[6],
            selected_side=row[7],
            consensus_probability=row[8],
        )
        for row in rows
    ]
