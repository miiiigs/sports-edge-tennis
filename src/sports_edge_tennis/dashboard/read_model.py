"""Read-only dashboard read model over persisted prediction/odds truth.

This module performs SELECT queries only. It never creates runs, runs agents,
runs the Manager, or mutates any table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import psycopg

from sports_edge_tennis.dashboard import presentation as pres


@dataclass(frozen=True)
class AgentView:
    code: str
    status: str  # PREDICTED / ABSTAINED
    selection: int | None
    probability: Decimal | None


@dataclass(frozen=True)
class MatchView:
    match_id: int
    player_1: str
    player_2: str
    tournament: str
    competition_tier: str
    scheduled_start: datetime | None
    agents: dict[str, AgentView]
    manager_status: str | None
    selected_side: int | None
    consensus_probability: Decimal | None
    reason_code: str | None
    active_agent_count: int
    abstaining_agent_count: int
    player_1_price: Decimal | None
    player_2_price: Decimal | None
    sharp_source_observed_at: datetime | None

    @property
    def sharp_available(self) -> bool:
        return self.player_1_price is not None and self.player_2_price is not None

    @property
    def selected_player(self) -> str | None:
        return pres.side_name(self.selected_side, self.player_1, self.player_2)

    @property
    def client_state(self) -> str:
        return pres.client_state(self.manager_status, self.sharp_available)


@dataclass(frozen=True)
class RunState:
    kind: str  # "POPULATED" | "EMPTY_SLATE" | "NO_RUN"
    run_date: date | None
    as_of: datetime | None
    prediction_run_id: int | None
    run_status: str | None
    matches: list[MatchView] = field(default_factory=list)

    @property
    def is_populated(self) -> bool:
        return self.kind == "POPULATED"


@dataclass(frozen=True)
class RunSummary:
    run_date: date
    kind: str  # "POPULATED" | "EMPTY_SLATE"
    as_of: datetime
    prediction_run_id: int | None
    run_status: str
    match_count: int
    consensus_count: int
    no_bet_count: int
    sharp_count: int


def _connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True)


def get_latest_activity(conn: psycopg.Connection) -> datetime | None:
    """Most recent persisted activity timestamp (for header 'last updated')."""
    run_row = conn.execute(
        "SELECT max(started_at) FROM prediction_runs"
    ).fetchone()[0]
    ingest_row = conn.execute(
        "SELECT max(started_at) FROM ingest_runs WHERE source_system = 'API_TENNIS'"
    ).fetchone()[0]
    candidates = [t for t in (run_row, ingest_row) if t is not None]
    return max(candidates) if candidates else None


def get_run_for_date(conn: psycopg.Connection, run_date: date) -> RunState:
    run_row = conn.execute(
        """
        SELECT prediction_run_id, run_status, as_of
        FROM prediction_runs
        WHERE as_of::date = %s
        ORDER BY as_of DESC, prediction_run_id DESC
        LIMIT 1
        """,
        (run_date,),
    ).fetchone()
    if run_row is not None:
        prediction_run_id, run_status, as_of = run_row
        matches = load_run_matches(conn, prediction_run_id)
        return RunState(
            kind="POPULATED",
            run_date=run_date,
            as_of=as_of,
            prediction_run_id=prediction_run_id,
            run_status=run_status,
            matches=matches,
        )

    ingest_row = conn.execute(
        """
        SELECT started_at, status
        FROM ingest_runs
        WHERE source_system = 'API_TENNIS' AND started_at::date = %s
        ORDER BY started_at DESC, ingest_run_id DESC
        LIMIT 1
        """,
        (run_date,),
    ).fetchone()
    if ingest_row is not None:
        started_at, status = ingest_row
        return RunState(
            kind="EMPTY_SLATE",
            run_date=run_date,
            as_of=started_at,
            prediction_run_id=None,
            run_status=status,
            matches=[],
        )

    return RunState(
        kind="NO_RUN",
        run_date=run_date,
        as_of=None,
        prediction_run_id=None,
        run_status=None,
        matches=[],
    )


def load_run_matches(conn: psycopg.Connection, prediction_run_id: int) -> list[MatchView]:
    target_rows = conn.execute(
        """
        SELECT match_id
        FROM prediction_run_targets
        WHERE prediction_run_id = %s
        ORDER BY match_id
        """,
        (prediction_run_id,),
    ).fetchall()
    match_ids = [row[0] for row in target_rows]
    if not match_ids:
        return []

    base = _load_match_base(conn, match_ids)
    predictions = _load_predictions(conn, prediction_run_id)
    manager = _load_manager(conn, prediction_run_id)
    sharp = _load_sharp(conn, match_ids)

    matches: list[MatchView] = []
    for match_id in match_ids:
        info = base.get(match_id, {})
        preds = predictions.get(match_id, {})
        mgr = manager.get(match_id, {})
        sharp_info = sharp.get(match_id, {})

        matches.append(
            MatchView(
                match_id=match_id,
                player_1=info.get("player_1", "—"),
                player_2=info.get("player_2", "—"),
                tournament=info.get("tournament", "—"),
                competition_tier=info.get("competition_tier"),
                scheduled_start=info.get("scheduled_start"),
                agents=preds,
                manager_status=mgr.get("manager_status"),
                selected_side=mgr.get("selected_side"),
                consensus_probability=mgr.get("consensus_probability"),
                reason_code=mgr.get("reason_code"),
                active_agent_count=mgr.get("active_agent_count", 0),
                abstaining_agent_count=mgr.get("abstaining_agent_count", 0),
                player_1_price=sharp_info.get("player_1_price"),
                player_2_price=sharp_info.get("player_2_price"),
                sharp_source_observed_at=sharp_info.get("source_observed_at"),
            )
        )
    return matches


def get_history(conn: psycopg.Connection, limit: int = 30) -> list[RunSummary]:
    run_rows = conn.execute(
        """
        SELECT prediction_run_id, run_status, as_of::date AS run_date, as_of
        FROM prediction_runs
        WHERE run_status = 'COMPLETED'
        ORDER BY as_of DESC, prediction_run_id DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()

    summaries: list[RunSummary] = []
    for prediction_run_id, run_status, run_date, as_of in run_rows:
        counts = _run_counts(conn, prediction_run_id)
        summaries.append(
            RunSummary(
                run_date=run_date,
                kind="POPULATED",
                as_of=as_of,
                prediction_run_id=prediction_run_id,
                run_status=run_status,
                match_count=counts["match_count"],
                consensus_count=counts["consensus_count"],
                no_bet_count=counts["no_bet_count"],
                sharp_count=counts["sharp_count"],
            )
        )

    ingest_rows = conn.execute(
        """
        SELECT ingest_run_id, started_at::date AS run_date, started_at, status
        FROM ingest_runs
        WHERE source_system = 'API_TENNIS'
          AND NOT EXISTS (
              SELECT 1 FROM prediction_runs pr
              WHERE pr.as_of::date = ingest_runs.started_at::date
          )
        ORDER BY started_at DESC, ingest_run_id DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()

    for _ingest_id, run_date, as_of, status in ingest_rows:
        summaries.append(
            RunSummary(
                run_date=run_date,
                kind="EMPTY_SLATE",
                as_of=as_of,
                prediction_run_id=None,
                run_status=status,
                match_count=0,
                consensus_count=0,
                no_bet_count=0,
                sharp_count=0,
            )
        )

    summaries.sort(key=lambda s: s.as_of, reverse=True)
    return summaries[:limit]


def _run_counts(conn: psycopg.Connection, prediction_run_id: int) -> dict[str, int]:
    match_count = conn.execute(
        "SELECT count(*) FROM prediction_run_targets WHERE prediction_run_id = %s",
        (prediction_run_id,),
    ).fetchone()[0]
    manager_rows = conn.execute(
        """
        SELECT manager_status, count(*)
        FROM manager_consensus_results
        WHERE prediction_run_id = %s
        GROUP BY manager_status
        """,
        (prediction_run_id,),
    ).fetchall()
    consensus_count = 0
    no_bet_count = 0
    for status, count in manager_rows:
        if status == "CONSENSUS":
            consensus_count += count
        else:
            no_bet_count += count

    sharp_count = conn.execute(
        """
        SELECT count(DISTINCT match_id)
        FROM odds_snapshots
        WHERE source_system = 'SHARP'
          AND match_id IN (SELECT match_id FROM prediction_run_targets WHERE prediction_run_id = %s)
        """,
        (prediction_run_id,),
    ).fetchone()[0]

    return {
        "match_count": match_count,
        "consensus_count": consensus_count,
        "no_bet_count": no_bet_count,
        "sharp_count": sharp_count,
    }


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
    out: dict[int, dict] = {}
    for row in rows:
        out[row[0]] = {
            "tournament": row[1],
            "competition_tier": row[2],
            "scheduled_start": row[3],
            "player_1": row[4],
            "player_2": row[5],
        }
    return out


def _load_predictions(conn: psycopg.Connection, prediction_run_id: int) -> dict[int, dict[str, AgentView]]:
    rows = conn.execute(
        """
        SELECT match_id, agent_code::text, prediction_status::text, selection_side, win_probability
        FROM predictions
        WHERE prediction_run_id = %s
        """,
        (prediction_run_id,),
    ).fetchall()
    out: dict[int, dict[str, AgentView]] = {}
    for match_id, agent_code, status, side, prob in rows:
        out.setdefault(match_id, {})[agent_code] = AgentView(
            code=agent_code,
            status=status,
            selection=side,
            probability=prob,
        )
    return out


def _load_manager(conn: psycopg.Connection, prediction_run_id: int) -> dict[int, dict]:
    rows = conn.execute(
        """
        SELECT match_id, manager_status::text, selected_side, consensus_probability,
               active_agent_count, abstaining_agent_count, reason_code::text
        FROM manager_consensus_results
        WHERE prediction_run_id = %s
        """,
        (prediction_run_id,),
    ).fetchall()
    out: dict[int, dict] = {}
    for row in rows:
        out[row[0]] = {
            "manager_status": row[1],
            "selected_side": row[2],
            "consensus_probability": row[3],
            "active_agent_count": row[4],
            "abstaining_agent_count": row[5],
            "reason_code": row[6],
        }
    return out


def _load_sharp(conn: psycopg.Connection, match_ids: list[int]) -> dict[int, dict]:
    rows = conn.execute(
        """
        SELECT DISTINCT ON (match_id, selection_type)
            match_id, selection_type::text, decimal_price, source_observed_at
        FROM odds_snapshots
        WHERE source_system = 'SHARP'
          AND market_type = 'MATCH_WINNER'
          AND match_id = ANY(%s)
        ORDER BY match_id, selection_type, as_of DESC, source_observed_at DESC
        """,
        (match_ids,),
    ).fetchall()
    out: dict[int, dict] = {}
    for match_id, selection_type, price, observed_at in rows:
        bucket = out.setdefault(match_id, {"source_observed_at": observed_at})
        if selection_type == "PLAYER_1":
            bucket["player_1_price"] = price
        elif selection_type == "PLAYER_2":
            bucket["player_2_price"] = price
    return out
