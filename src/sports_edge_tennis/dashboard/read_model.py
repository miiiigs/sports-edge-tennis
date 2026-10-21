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
from sports_edge_tennis.dashboard.aligned_model_picks import (
    AlignedPick,
    load_aligned_day_picks,
)
from sports_edge_tennis.dashboard.market_overlay import MarketDecisionView


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


@dataclass(frozen=True)
class LiveHealthView:
    status: str
    freshness_state: str
    checked_at: datetime
    latest_observation_at: datetime | None
    error_count: int
    notes: str | None


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


def get_latest_live_operational_health(
    conn: psycopg.Connection,
) -> LiveHealthView | None:
    row = conn.execute(
        """
        SELECT
            status::text,
            freshness_state::text,
            checked_at,
            latest_observation_at,
            error_count,
            notes
        FROM data_health_checks
        WHERE source_system = 'SPORTS_EDGE_TENNIS'
          AND dataset_name = 'LIVE_BATCH_RUNTIME'
        ORDER BY checked_at DESC, data_health_check_id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return LiveHealthView(
        status=str(row[0]),
        freshness_state=str(row[1]),
        checked_at=row[2],
        latest_observation_at=row[3],
        error_count=int(row[4]),
        notes=row[5],
    )


def get_run_for_date(
    conn: psycopg.Connection, run_date: date, timezone_name: str = "UTC"
) -> RunState:
    """Return the newest completed prediction run for a slate date.

    In-progress runs are intentionally excluded from the client read model. A
    RUNNING row may already contain targets while predictions or Manager rows
    are still being persisted; exposing it would make previously published
    picks temporarily disappear during an hourly refresh.
    """
    run_row = conn.execute(
        """
        SELECT pr.prediction_run_id, pr.run_status, pr.as_of
        FROM prediction_runs pr
        WHERE pr.run_status = 'COMPLETED'
          AND EXISTS (
              SELECT 1
              FROM prediction_run_targets prt
              JOIN LATERAL (
                  SELECT mv.match_start_at
                  FROM match_versions mv
                  WHERE mv.match_id = prt.match_id
                    AND mv.as_of <= pr.as_of
                    AND mv.source_observed_at <= pr.as_of
                  ORDER BY mv.as_of DESC,
                           mv.source_observed_at DESC,
                           mv.match_version_id DESC
                  LIMIT 1
              ) target_mv ON TRUE
              WHERE prt.prediction_run_id = pr.prediction_run_id
                AND (target_mv.match_start_at AT TIME ZONE %s::text)::date = %s
          )
        ORDER BY pr.as_of DESC, pr.prediction_run_id DESC
        LIMIT 1
        """,
        (timezone_name, run_date),
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

    # Backward-compatible fallback for legacy/synthetic completed runs that
    # predate target persistence. Restricting this to zero-target runs prevents
    # a future target-bearing slate created today from hijacking today's page.
    run_row = conn.execute(
        """
        SELECT pr.prediction_run_id, pr.run_status, pr.as_of
        FROM prediction_runs pr
        WHERE (pr.as_of AT TIME ZONE %s::text)::date = %s
          AND pr.run_status = 'COMPLETED'
          AND NOT EXISTS (
              SELECT 1
              FROM prediction_run_targets prt
              WHERE prt.prediction_run_id = pr.prediction_run_id
          )
        ORDER BY pr.as_of DESC, pr.prediction_run_id DESC
        LIMIT 1
        """,
        (timezone_name, run_date),
    ).fetchone()
    if run_row is not None:
        prediction_run_id, run_status, as_of = run_row
        return RunState(
            kind="POPULATED",
            run_date=run_date,
            as_of=as_of,
            prediction_run_id=prediction_run_id,
            run_status=run_status,
            matches=[],
        )

    ingest_row = conn.execute(
        """
        SELECT started_at, status
        FROM ingest_runs
        WHERE source_system = 'API_TENNIS'
          AND (started_at AT TIME ZONE %s::text)::date = %s
        ORDER BY started_at DESC, ingest_run_id DESC
        LIMIT 1
        """,
        (timezone_name, run_date),
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


# ---------------------------------------------------------------------------
# Issued daily pick ledger.
#
# The client page must show what was *issued* during the sporting day, not the
# newest re-evaluation. A later degraded run (e.g. the run-616 A2 regression)
# must never erase a Qualified Model Pick or a Value Bet that was already
# issued earlier the same day.
#
# The primary Model Pick population now comes from the single shared aligned
# ledger (``dashboard.aligned_model_picks``) so Today and Model Picks Results
# select the same sporting matches for the same local day. This is a read-model
# selection rule; no prediction, Manager, decision, or settlement row is ever
# mutated.
# ---------------------------------------------------------------------------

_DAY_RUNS_SQL = """
    SELECT pr.prediction_run_id, pr.as_of
    FROM prediction_runs pr
    WHERE pr.run_status = 'COMPLETED'
      AND EXISTS (
          SELECT 1
          FROM prediction_run_targets prt
          JOIN LATERAL (
              SELECT mv.match_start_at
              FROM match_versions mv
              WHERE mv.match_id = prt.match_id
                AND mv.as_of <= pr.as_of
                AND mv.source_observed_at <= pr.as_of
              ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
              LIMIT 1
          ) target_mv ON TRUE
          WHERE prt.prediction_run_id = pr.prediction_run_id
            AND (target_mv.match_start_at AT TIME ZONE %s::text)::date = %s
      )
    ORDER BY pr.as_of ASC, pr.prediction_run_id ASC
"""


def completed_day_run_rows(
    conn: psycopg.Connection, run_date: date, timezone_name: str = "UTC"
) -> list[tuple[int, datetime]]:
    """Completed prediction runs that target ``run_date``, earliest first."""
    return [
        (int(row[0]), row[1])
        for row in conn.execute(
            _DAY_RUNS_SQL, (timezone_name, run_date)
        ).fetchall()
    ]


def _load_aligned_match_views(
    conn: psycopg.Connection, picks: tuple[AlignedPick, ...]
) -> list[MatchView]:
    """Reconstruct the Today card matches for the aligned Model Pick population.

    Each qualifying pick is rendered from the exact prediction run / match pair
    that produced it, so Today's cards carry the issued Manager/Agent evidence
    instead of the newest re-evaluation.
    """
    by_run: dict[int, list[AlignedPick]] = {}
    for pick in picks:
        by_run.setdefault(pick.prediction_run_id, []).append(pick)

    views: dict[int, MatchView] = {}
    for run_id, run_picks in by_run.items():
        match_ids = [pick.match_id for pick in run_picks]
        base = _load_issued_match_base(conn, run_picks)
        predictions = _load_predictions(conn, run_id)
        manager = _load_manager(conn, run_id)
        sharp = _load_sharp(conn, match_ids)
        for pick in run_picks:
            if pick.match_id in views:
                continue
            info = base.get(pick.match_id, {})
            mgr = manager.get(pick.match_id, {})
            sharp_info = sharp.get(pick.match_id, {})
            views[pick.match_id] = MatchView(
                match_id=pick.match_id,
                player_1=info.get("player_1", "—"),
                player_2=info.get("player_2", "—"),
                tournament=info.get("tournament", "—"),
                competition_tier=info.get("competition_tier"),
                scheduled_start=info.get("scheduled_start"),
                agents=predictions.get(pick.match_id, {}),
                manager_status=mgr.get("manager_status"),
                selected_side=(
                    mgr.get("selected_side")
                    if mgr.get("selected_side") is not None
                    else pick.selected_side
                ),
                consensus_probability=(
                    mgr.get("consensus_probability")
                    if mgr.get("consensus_probability") is not None
                    else pick.aligned_probability
                ),
                reason_code=mgr.get("reason_code"),
                active_agent_count=mgr.get("active_agent_count", 0),
                abstaining_agent_count=mgr.get("abstaining_agent_count", 0),
                player_1_price=sharp_info.get("player_1_price"),
                player_2_price=sharp_info.get("player_2_price"),
                sharp_source_observed_at=sharp_info.get("source_observed_at"),
            )
    return list(views.values())


def get_issued_day_state(
    conn: psycopg.Connection, run_date: date, timezone_name: str = "UTC"
) -> RunState:
    """Return the issued daily Model Pick ledger for ``run_date``.

    The primary population is the *shared* aligned Model Picks ledger
    (``aligned_model_picks``): the earliest qualifying current-rule evaluation
    per sporting match whose local match day is ``run_date``. This is exactly the
    population the Model Picks Results page shows, so Today and that page can
    never disagree about the day's Model Picks. Wide-Spread cases remain
    tracking-only here and are never promoted into a Value Bet.
    """
    picks = load_aligned_day_picks(
        conn, decision_date=run_date, timezone_name=timezone_name
    )
    runs = completed_day_run_rows(conn, run_date, timezone_name)
    if not runs:
        # No completed production run covers this local day. Fall back for
        # context only; it cannot introduce non-qualifying picks.
        return get_run_for_date(conn, run_date, timezone_name)

    latest_run_id, latest_as_of = runs[-1]
    matches = _load_aligned_match_views(conn, picks) if picks else []
    return RunState(
        kind="POPULATED" if matches else "EMPTY_SLATE",
        run_date=run_date,
        as_of=latest_as_of,
        prediction_run_id=latest_run_id,
        run_status="COMPLETED",
        matches=matches,
    )


def get_issued_market_decisions(
    conn: psycopg.Connection,
    run_date: date,
    timezone_name: str = "UTC",
) -> dict[int, MarketDecisionView]:
    """First actually-issued actionable-or-blocked market decision per match.

    Uses the earliest published BET or VETOED decision per match across the
    day's completed runs so:

    * a Value Bet keeps its issued bookmaker, price, edge, and stake even after
      a later re-evaluation or market refresh;
    * a real VETOED row reaches the client as a blocked (never actionable) card;
    * a later BET can never override an earlier safety veto.

    NO_BET rows remain evidence only and are intentionally excluded. Never
    recalculates old value bets or converts a blocked row into a stake.
    """
    runs = completed_day_run_rows(conn, run_date, timezone_name)
    if not runs:
        return {}
    rows = conn.execute(
        """
        SELECT DISTINCT ON (d.match_id)
            d.match_id,
            d.decision_status::text,
            d.reason_code::text,
            d.selection_side,
            d.market_decimal_price,
            d.sharp_reference_price,
            d.price_bookmaker_code,
            d.edge_pct,
            d.min_acceptable_odds,
            d.suggested_stake_fraction,
            d.published_at
        FROM decisions d
        WHERE d.prediction_run_id = ANY(%s)
          -- BET is actionable; VETOED is Manager/Market-qualified but blocked.
          -- Both must be visible so the read model and presentation agree.
          AND d.decision_status IN ('BET', 'VETOED')
          AND d.manager_consensus_result_id IS NOT NULL
        ORDER BY d.match_id, COALESCE(d.published_at, d.as_of) ASC, d.decision_id ASC
        """,
        ([run_id for run_id, _ in runs],),
    ).fetchall()
    return {
        int(row[0]): MarketDecisionView(
            match_id=int(row[0]),
            decision_status=str(row[1]),
            reason_code=str(row[2]),
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


def get_history(
    conn: psycopg.Connection, limit: int = 30, timezone_name: str = "UTC"
) -> list[RunSummary]:
    run_rows = conn.execute(
        """
        WITH run_dates AS (
            SELECT
                pr.prediction_run_id,
                pr.run_status,
                pr.as_of,
                min((target_mv.match_start_at AT TIME ZONE %s::text)::date) AS run_date
            FROM prediction_runs pr
            JOIN prediction_run_targets prt
              ON prt.prediction_run_id = pr.prediction_run_id
            JOIN LATERAL (
                SELECT mv.match_start_at
                FROM match_versions mv
                WHERE mv.match_id = prt.match_id
                  AND mv.as_of <= pr.as_of
                  AND mv.source_observed_at <= pr.as_of
                ORDER BY mv.as_of DESC,
                         mv.source_observed_at DESC,
                         mv.match_version_id DESC
                LIMIT 1
            ) target_mv ON TRUE
            WHERE pr.run_status = 'COMPLETED'
            GROUP BY pr.prediction_run_id, pr.run_status, pr.as_of
        ),
        ranked AS (
            SELECT
                prediction_run_id,
                run_status,
                run_date,
                as_of,
                row_number() OVER (
                    PARTITION BY run_date
                    ORDER BY as_of DESC, prediction_run_id DESC
                ) AS recency_rank
            FROM run_dates
            WHERE run_date IS NOT NULL
        )
        SELECT prediction_run_id, run_status, run_date, as_of
        FROM ranked
        WHERE recency_rank = 1
        ORDER BY run_date DESC, as_of DESC, prediction_run_id DESC
        LIMIT %s
        """,
        (timezone_name, limit),
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

    populated_dates = {summary.run_date for summary in summaries}
    ingest_rows = conn.execute(
        """
        WITH localized AS (
            SELECT
                ingest_run_id,
                (started_at AT TIME ZONE %s::text)::date AS run_date,
                started_at,
                status
            FROM ingest_runs
            WHERE source_system = 'API_TENNIS'
        )
        SELECT DISTINCT ON (run_date)
            ingest_run_id,
            run_date,
            started_at,
            status
        FROM localized
        ORDER BY run_date DESC,
                 started_at DESC,
                 ingest_run_id DESC
        LIMIT %s
        """,
        (timezone_name, limit),
    ).fetchall()

    for _ingest_id, run_date, as_of, status in ingest_rows:
        if run_date in populated_dates:
            continue
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

    summaries.sort(key=lambda s: (s.run_date, s.as_of), reverse=True)
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


def _load_issued_match_base(
    conn: psycopg.Connection, picks: list[AlignedPick]
) -> dict[int, dict]:
    """Read the player orientation, tournament and start visible *at issuance*.

    The existing generic fixture reader intentionally uses the newest match
    version; it is not safe for a historical Model Pick because provider
    identity corrections can swap sides after the model has selected PLAYER_1
    or PLAYER_2. Render the original issued side and scheduled start instead.
    """
    if not picks:
        return {}
    rows = conn.execute(
        """
        SELECT
            selected.match_id,
            t.canonical_name,
            mv.competition_tier::text,
            mv.match_start_at,
            p1.canonical_name,
            p2.canonical_name
        FROM unnest(%s::bigint[], %s::timestamptz[])
             AS selected(match_id, picked_at)
        JOIN matches m ON m.match_id = selected.match_id
        JOIN tournament_editions te
          ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN LATERAL (
            SELECT
                mv0.player_1_id, mv0.player_2_id,
                mv0.competition_tier, mv0.match_start_at
            FROM match_versions mv0
            WHERE mv0.match_id = selected.match_id
              AND mv0.as_of <= selected.picked_at
              AND mv0.source_observed_at <= selected.picked_at
            ORDER BY
                mv0.as_of DESC, mv0.source_observed_at DESC,
                mv0.match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        """,
        ([p.match_id for p in picks], [p.picked_at for p in picks]),
    ).fetchall()
    return {
        int(row[0]): {
            "tournament": row[1],
            "competition_tier": row[2],
            "scheduled_start": row[3],
            "player_1": row[4],
            "player_2": row[5],
        }
        for row in rows
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
