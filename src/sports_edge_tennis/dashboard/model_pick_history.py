"""Read-only model-pick results for the Sports Edge Tennis dashboard.

A Model Pick exists when the Manager reaches CONSENSUS on a winner. It is
scored independently of price/value. Value Bets are a subset of Model Picks, so
those matches remain in this ledger but still count only once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Iterable

import psycopg
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sports_edge_tennis.dashboard import presentation as pres
from sports_edge_tennis.dashboard.app import DEMO_MODE, _connect, _latest_activity, templates
from sports_edge_tennis.dashboard.history_alignment import (
    ALIGNED_LIVE_HISTORY_START,
    ALIGNED_MIN_MODEL_PROBABILITY,
    since_aligned_start,
)
from sports_edge_tennis.performance.regimes import (
    ModelRegimeCohort,
    ModelRegimeIdentity,
    current_model_regime_started_at,
    PRODUCTION_RUN_PREDICATE,
    first_production_bets_cte,
    first_production_model_picks_cte,
    load_model_regime_cohorts,
    resolve_current_model_regime,
    veto_audits_available,
)

router = APIRouter()


@dataclass(frozen=True)
class DailyModelPickSummary:
    decision_date: date
    picks: int
    scored: int
    pending: int
    voids: int
    wins: int
    losses: int
    hit_rate: float | None


@dataclass(frozen=True)
class PeriodModelPickSummary:
    picks: int
    scored: int
    pending: int
    voids: int
    wins: int
    losses: int
    hit_rate: float | None


@dataclass(frozen=True)
class ModelPickRow:
    manager_consensus_result_id: int
    picked_at: datetime
    match_start_at: datetime | None
    tournament: str | None
    player_1: str | None
    player_2: str | None
    selection: str | None
    consensus_probability: Decimal | None
    result_status: str
    is_value_bet: bool
    manager_policy_version: str | None
    model_regime_key: str | None
    agent_config_versions: tuple[str | None, ...] = (None, None, None)


@dataclass(frozen=True)
class ModelPickResultsView:
    all_summaries: tuple[DailyModelPickSummary, ...] = ()
    aligned_summaries: tuple[DailyModelPickSummary, ...] = ()
    current_summaries: tuple[DailyModelPickSummary, ...] = ()
    current_regime: ModelRegimeIdentity | None = None
    current_regime_started_at: datetime | None = None
    cohorts: tuple[ModelRegimeCohort, ...] = ()
    selected_date: date | None = None
    selected_summary: DailyModelPickSummary | None = None
    picks: tuple[ModelPickRow, ...] = ()
    failed: bool = False

    @property
    def has_multiple_regimes(self) -> bool:
        return len(self.cohorts) > 1


def _parse_optional_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("invalid date") from exc


def _pct(value: float | Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.1f}%"


def _summary_from_row(row) -> DailyModelPickSummary:
    scored = int(row[2])
    wins = int(row[5])
    losses = int(row[6])
    return DailyModelPickSummary(
        decision_date=row[0],
        picks=int(row[1]),
        scored=scored,
        pending=int(row[3]),
        voids=int(row[4]),
        wins=wins,
        losses=losses,
        hit_rate=(wins / scored) if scored else None,
    )


def aggregate_model_pick_summaries(
    summaries: Iterable[DailyModelPickSummary],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> PeriodModelPickSummary:
    selected = [
        row
        for row in summaries
        if (start_date is None or row.decision_date >= start_date)
        and (end_date is None or row.decision_date <= end_date)
    ]
    picks = sum(row.picks for row in selected)
    scored = sum(row.scored for row in selected)
    pending = sum(row.pending for row in selected)
    voids = sum(row.voids for row in selected)
    wins = sum(row.wins for row in selected)
    losses = sum(row.losses for row in selected)
    return PeriodModelPickSummary(
        picks=picks,
        scored=scored,
        pending=pending,
        voids=voids,
        wins=wins,
        losses=losses,
        hit_rate=(wins / scored) if scored else None,
    )


def build_model_pick_windows(
    summaries: Iterable[DailyModelPickSummary],
    *,
    as_of_date: date,
) -> dict[str, PeriodModelPickSummary]:
    rows = tuple(summaries)
    return {
        "today": aggregate_model_pick_summaries(
            rows, start_date=as_of_date, end_date=as_of_date
        ),
        "last_7_days": aggregate_model_pick_summaries(
            rows,
            start_date=as_of_date - timedelta(days=6),
            end_date=as_of_date,
        ),
        "last_30_days": aggregate_model_pick_summaries(
            rows,
            start_date=as_of_date - timedelta(days=29),
            end_date=as_of_date,
        ),
        "all_time": aggregate_model_pick_summaries(rows, end_date=as_of_date),
    }


def aligned_production_model_picks_cte() -> str:
    """Current-rule Sep-10 model-pick ledger over immutable live evidence.

    A qualifying aligned Model Pick is the earliest production evaluation per
    sporting match that satisfies either:
    1) Manager CONSENSUS with probability >= 60%; or
    2) A1+A2 same-side, A3 abstained, Manager rejected only because the A1/A2
       spread exceeded the abstention gate, with A1/A2 arithmetic mean >= 60%.

    The second case is tracking-only and never becomes a Value Bet.
    """
    floor = str(ALIGNED_MIN_MODEL_PROBABILITY)
    return f"""
        aligned_model_candidates AS (
            SELECT
                mcr.manager_consensus_result_id,
                mcr.prediction_run_id,
                mcr.match_id,
                mcr.as_of AS picked_at,
                mcr.selected_side,
                mcr.consensus_probability AS aligned_probability,
                'MANAGER_CONSENSUS'::text AS aligned_pick_type
            FROM manager_consensus_results mcr
            JOIN prediction_runs pr
              ON pr.prediction_run_id = mcr.prediction_run_id
            WHERE mcr.manager_status = 'CONSENSUS'
              AND mcr.selected_side IN (1, 2)
              AND mcr.consensus_probability >= {floor}
              AND {PRODUCTION_RUN_PREDICATE}

            UNION ALL

            SELECT
                mcr.manager_consensus_result_id,
                mcr.prediction_run_id,
                mcr.match_id,
                mcr.as_of AS picked_at,
                a1.selection_side AS selected_side,
                ((a1.win_probability + a2.win_probability) / 2.0) AS aligned_probability,
                'A1_A2_WIDE_SPREAD'::text AS aligned_pick_type
            FROM manager_consensus_results mcr
            JOIN prediction_runs pr
              ON pr.prediction_run_id = mcr.prediction_run_id
            JOIN predictions a1
              ON a1.prediction_run_id = mcr.prediction_run_id
             AND a1.match_id = mcr.match_id
             AND a1.agent_code = 'AGENT_1_SURFACE_ELO'
            JOIN predictions a2
              ON a2.prediction_run_id = mcr.prediction_run_id
             AND a2.match_id = mcr.match_id
             AND a2.agent_code = 'AGENT_2_HIERARCHICAL_MARKOV'
            JOIN predictions a3
              ON a3.prediction_run_id = mcr.prediction_run_id
             AND a3.match_id = mcr.match_id
             AND a3.agent_code = 'AGENT_3_INTRANSITIVITY'
            WHERE mcr.manager_status = 'NO_BET'
              AND mcr.reason_code = 'AGENT3_ABSTENTION_SPREAD_EXCEEDED'
              AND a1.prediction_status = 'PREDICTED'
              AND a2.prediction_status = 'PREDICTED'
              AND a3.prediction_status = 'ABSTAINED'
              AND a1.selection_side IN (1, 2)
              AND a2.selection_side = a1.selection_side
              AND a1.win_probability IS NOT NULL
              AND a2.win_probability IS NOT NULL
              AND ((a1.win_probability + a2.win_probability) / 2.0) >= {floor}
              AND {PRODUCTION_RUN_PREDICATE}
        ),
        aligned_model_picks AS (
            SELECT DISTINCT ON (match_id)
                manager_consensus_result_id,
                prediction_run_id,
                match_id,
                picked_at,
                selected_side,
                aligned_probability,
                aligned_pick_type
            FROM aligned_model_candidates
            ORDER BY
                match_id,
                picked_at,
                manager_consensus_result_id,
                aligned_pick_type
        )
    """


def load_aligned_daily_model_pick_summaries(
    conn: psycopg.Connection,
    *,
    limit: int = 5000,
) -> tuple[DailyModelPickSummary, ...]:
    """Daily current-rule aligned picks from Sep 10 live production evidence."""
    if limit < 1 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")

    rows = conn.execute(
        f"""
        WITH {aligned_production_model_picks_cte()},
        dated AS (
            SELECT
                p.*,
                COALESCE(
                    (mv.match_start_at AT TIME ZONE 'UTC')::date,
                    (p.picked_at AT TIME ZONE 'UTC')::date
                ) AS match_date
            FROM aligned_model_picks p
            LEFT JOIN LATERAL (
                SELECT mv0.match_start_at
                FROM match_versions mv0
                WHERE mv0.match_id = p.match_id
                  AND mv0.as_of <= p.picked_at
                  AND mv0.source_observed_at <= p.picked_at
                ORDER BY mv0.as_of DESC,
                         mv0.source_observed_at DESC,
                         mv0.match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
        ),
        with_results AS (
            SELECT
                d.*,
                r.outcome_status,
                r.winner_side
            FROM dated d
            LEFT JOIN LATERAL (
                SELECT
                    mro.outcome_status::text AS outcome_status,
                    mro.winner_side
                FROM match_result_observations mro
                WHERE mro.match_id = d.match_id
                  AND mro.outcome_status IN (
                      'COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED'
                  )
                ORDER BY mro.as_of DESC,
                         mro.source_observed_at DESC,
                         mro.match_result_observation_id DESC
                LIMIT 1
            ) r ON TRUE
        )
        SELECT
            match_date,
            count(*)::int AS picks,
            count(*) FILTER (
                WHERE outcome_status = 'COMPLETED' AND winner_side IN (1, 2)
            )::int AS scored,
            count(*) FILTER (WHERE outcome_status IS NULL)::int AS pending,
            count(*) FILTER (
                WHERE outcome_status IN ('RETIRED', 'WALKOVER', 'CANCELLED')
            )::int AS voids,
            count(*) FILTER (
                WHERE outcome_status = 'COMPLETED'
                  AND winner_side = selected_side
            )::int AS wins,
            count(*) FILTER (
                WHERE outcome_status = 'COMPLETED'
                  AND winner_side IN (1, 2)
                  AND winner_side <> selected_side
            )::int AS losses
        FROM with_results
        WHERE match_date >= %s
        GROUP BY match_date
        ORDER BY match_date DESC
        LIMIT %s
        """,
        (ALIGNED_LIVE_HISTORY_START, limit),
    ).fetchall()
    return tuple(_summary_from_row(row) for row in rows)


def load_daily_model_pick_summaries(
    conn: psycopg.Connection,
    *,
    limit: int = 5000,
    model_regime_key: str | None = None,
) -> tuple[DailyModelPickSummary, ...]:
    if limit < 1 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")

    rows = conn.execute(
        f"""
        WITH {first_production_model_picks_cte()},
        dated AS (
            SELECT
                p.*,
                COALESCE(
                    (mv.match_start_at AT TIME ZONE 'UTC')::date,
                    (p.picked_at AT TIME ZONE 'UTC')::date
                ) AS match_date
            FROM first_model_picks p
            LEFT JOIN LATERAL (
                SELECT mv0.match_start_at
                FROM match_versions mv0
                WHERE mv0.match_id = p.match_id
                  AND mv0.as_of <= p.picked_at
                  AND mv0.source_observed_at <= p.picked_at
                ORDER BY mv0.as_of DESC,
                         mv0.source_observed_at DESC,
                         mv0.match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
        ),
        with_results AS (
            SELECT
                d.*,
                r.outcome_status,
                r.winner_side
            FROM dated d
            LEFT JOIN LATERAL (
                SELECT
                    mro.outcome_status::text AS outcome_status,
                    mro.winner_side
                FROM match_result_observations mro
                WHERE mro.match_id = d.match_id
                  AND mro.outcome_status IN (
                      'COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED'
                  )
                ORDER BY mro.as_of DESC,
                         mro.source_observed_at DESC,
                         mro.match_result_observation_id DESC
                LIMIT 1
            ) r ON TRUE
        )
        SELECT
            match_date,
            count(*)::int AS picks,
            count(*) FILTER (
                WHERE outcome_status = 'COMPLETED' AND winner_side IN (1, 2)
            )::int AS scored,
            count(*) FILTER (WHERE outcome_status IS NULL)::int AS pending,
            count(*) FILTER (
                WHERE outcome_status IN ('RETIRED', 'WALKOVER', 'CANCELLED')
            )::int AS voids,
            count(*) FILTER (
                WHERE outcome_status = 'COMPLETED'
                  AND winner_side = selected_side
            )::int AS wins,
            count(*) FILTER (
                WHERE outcome_status = 'COMPLETED'
                  AND winner_side IN (1, 2)
                  AND winner_side <> selected_side
            )::int AS losses
        FROM with_results
        WHERE %s::text IS NULL OR model_regime_key = %s::text
        GROUP BY match_date
        ORDER BY match_date DESC
        LIMIT %s
        """,
        (model_regime_key, model_regime_key, limit),
    ).fetchall()
    return tuple(_summary_from_row(row) for row in rows)


def load_daily_model_picks(
    conn: psycopg.Connection,
    *,
    decision_date: date,
) -> tuple[ModelPickRow, ...]:
    veto_available = veto_audits_available(conn)
    rows = conn.execute(
        f"""
        WITH {first_production_model_picks_cte()},
        {first_production_bets_cte(veto_available=veto_available)}
        SELECT
            p.manager_consensus_result_id,
            p.picked_at,
            mv.match_start_at,
            t.canonical_name AS tournament,
            p1.canonical_name AS player_1,
            p2.canonical_name AS player_2,
            CASE p.selected_side
                WHEN 1 THEN p1.canonical_name
                WHEN 2 THEN p2.canonical_name
                ELSE NULL
            END AS selection,
            p.consensus_probability,
            CASE
                WHEN r.outcome_status = 'COMPLETED'
                     AND r.winner_side = p.selected_side THEN 'WON'
                WHEN r.outcome_status = 'COMPLETED'
                     AND r.winner_side IN (1, 2)
                     AND r.winner_side <> p.selected_side THEN 'LOST'
                WHEN r.outcome_status IN ('RETIRED', 'WALKOVER', 'CANCELLED') THEN 'VOID'
                ELSE 'PENDING'
            END AS result_status,
            (
                fb.decision_id IS NOT NULL
                AND fb.selection_side = p.selected_side
            ) AS is_value_bet,
            p.manager_policy_version,
            p.model_regime_key,
            p.agent_a1_config_version,
            p.agent_a2_config_version,
            p.agent_a3_config_version
        FROM first_model_picks p
        JOIN matches m ON m.match_id = p.match_id
        JOIN tournament_editions te
          ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        LEFT JOIN LATERAL (
            SELECT
                mv0.player_1_id,
                mv0.player_2_id,
                mv0.match_start_at
            FROM match_versions mv0
            WHERE mv0.match_id = p.match_id
              AND mv0.as_of <= p.picked_at
              AND mv0.source_observed_at <= p.picked_at
            ORDER BY mv0.as_of DESC,
                     mv0.source_observed_at DESC,
                     mv0.match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        LEFT JOIN players p1 ON p1.player_id = mv.player_1_id
        LEFT JOIN players p2 ON p2.player_id = mv.player_2_id
        LEFT JOIN LATERAL (
            SELECT
                mro.outcome_status::text AS outcome_status,
                mro.winner_side
            FROM match_result_observations mro
            WHERE mro.match_id = p.match_id
              AND mro.outcome_status IN (
                  'COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED'
              )
            ORDER BY mro.as_of DESC,
                     mro.source_observed_at DESC,
                     mro.match_result_observation_id DESC
            LIMIT 1
        ) r ON TRUE
        LEFT JOIN first_bets fb ON fb.match_id = p.match_id
        WHERE COALESCE(
            (mv.match_start_at AT TIME ZONE 'UTC')::date,
            (p.picked_at AT TIME ZONE 'UTC')::date
        ) = %s
        ORDER BY p.picked_at DESC, p.manager_consensus_result_id DESC
        """,
        (decision_date,),
    ).fetchall()

    return tuple(
        ModelPickRow(
            manager_consensus_result_id=int(row[0]),
            picked_at=row[1],
            match_start_at=row[2],
            tournament=str(row[3]) if row[3] is not None else None,
            player_1=str(row[4]) if row[4] is not None else None,
            player_2=str(row[5]) if row[5] is not None else None,
            selection=str(row[6]) if row[6] is not None else None,
            consensus_probability=Decimal(row[7]) if row[7] is not None else None,
            result_status=str(row[8]),
            is_value_bet=bool(row[9]),
            manager_policy_version=str(row[10]) if row[10] is not None else None,
            model_regime_key=str(row[11]) if row[11] is not None else None,
            agent_config_versions=tuple(
                str(row[12 + index]) if row[12 + index] is not None else None
                for index in range(3)
            ),
        )
        for row in rows
    )


def _load_history(requested_date: date | None) -> ModelPickResultsView:
    if DEMO_MODE:
        return ModelPickResultsView(selected_date=requested_date)
    conn = _connect()
    if conn is None:
        return ModelPickResultsView(selected_date=requested_date, failed=True)
    try:
        summaries = load_daily_model_pick_summaries(conn)
        aligned_summaries = load_aligned_daily_model_pick_summaries(conn)
        current_regime = resolve_current_model_regime(conn)
        current_summaries: tuple[DailyModelPickSummary, ...] = ()
        started_at = None
        if (
            current_regime is not None
            and current_regime.has_complete_lineage
            and not current_regime.is_inconsistent
        ):
            current_summaries = load_daily_model_pick_summaries(
                conn, model_regime_key=current_regime.regime_key
            )
            started_at = current_model_regime_started_at(
                conn, current_regime.regime_key
            )
        cohorts = load_model_regime_cohorts(conn)
        selected_date = requested_date or (
            summaries[0].decision_date if summaries else None
        )
        selected_summary = next(
            (
                row
                for row in summaries
                if selected_date is not None and row.decision_date == selected_date
            ),
            None,
        )
        picks = (
            load_daily_model_picks(conn, decision_date=selected_date)
            if selected_date is not None
            else ()
        )
        return ModelPickResultsView(
            all_summaries=summaries,
            aligned_summaries=aligned_summaries,
            current_summaries=current_summaries,
            current_regime=current_regime,
            current_regime_started_at=started_at,
            cohorts=cohorts,
            selected_date=selected_date,
            selected_summary=selected_summary,
            picks=picks,
        )
    except Exception:
        return ModelPickResultsView(selected_date=requested_date, failed=True)
    finally:
        conn.close()


def _period_json(summary: PeriodModelPickSummary) -> dict[str, object]:
    return {
        "picks": summary.picks,
        "scored": summary.scored,
        "pending": summary.pending,
        "voids": summary.voids,
        "wins": summary.wins,
        "losses": summary.losses,
        "hit_rate": summary.hit_rate,
    }


@router.get("/model-pick-results", response_class=HTMLResponse)
def model_pick_results(request: Request, date: str | None = Query(default=None)):
    try:
        requested_date = _parse_optional_date(date)
    except ValueError:
        return HTMLResponse("Invalid date", status_code=400)

    view = _load_history(requested_date)
    as_of_date = datetime.now(UTC).date()
    aligned_summaries = since_aligned_start(view.aligned_summaries)
    return templates.TemplateResponse(
        request,
        "model_pick_results.html",
        {
            "pres": pres,
            "demo_mode": DEMO_MODE,
            "latest_activity": _latest_activity(),
            "daily_summaries": view.all_summaries,
            "selected_date": view.selected_date,
            "selected_summary": view.selected_summary,
            "daily_picks": view.picks,
            "aligned_live_start": ALIGNED_LIVE_HISTORY_START,
            "aligned_live_windows": build_model_pick_windows(
                aligned_summaries, as_of_date=as_of_date
            ),
            "aligned_live_picks": sum(row.picks for row in aligned_summaries),
            "current_windows": build_model_pick_windows(
                view.current_summaries, as_of_date=as_of_date
            ),
            "current_regime": view.current_regime,
            "current_regime_started_at": view.current_regime_started_at,
            "current_picks": sum(row.picks for row in view.current_summaries),
            "performance_windows": build_model_pick_windows(
                view.all_summaries, as_of_date=as_of_date
            ),
            "cohorts": view.cohorts,
            "has_multiple_regimes": view.has_multiple_regimes,
            "performance_as_of": as_of_date,
            "model_pick_history_error": view.failed,
            "pct": _pct,
        },
    )


@router.get("/api/dashboard/model-pick-results")
def api_model_pick_results(date: str | None = Query(default=None)):
    try:
        requested_date = _parse_optional_date(date)
    except ValueError:
        return JSONResponse({"error": "invalid date"}, status_code=400)

    view = _load_history(requested_date)
    if view.failed:
        return JSONResponse({"error": "model pick history unavailable"}, status_code=503)

    as_of_date = datetime.now(UTC).date()
    all_windows = build_model_pick_windows(view.all_summaries, as_of_date=as_of_date)
    aligned_summaries = since_aligned_start(view.all_summaries)
    aligned_windows = build_model_pick_windows(
        aligned_summaries, as_of_date=as_of_date
    )
    current_windows = build_model_pick_windows(
        view.current_summaries, as_of_date=as_of_date
    )

    return JSONResponse(
        {
            "as_of_date": as_of_date.isoformat(),
            "aligned_live_history": {
                "start_date": ALIGNED_LIVE_HISTORY_START.isoformat(),
                "scope": "SEP10_LIVE_MODEL_PICKS_ALIGNED_TO_CURRENT_60_PERCENT_RULE",
                "min_model_probability": str(ALIGNED_MIN_MODEL_PROBABILITY),
                "includes_a1_a2_wide_spread_tracking": True,
                "is_replay": False,
                "performance": {
                    key: _period_json(value) for key, value in aligned_windows.items()
                },
            },
            "current_model_regime": None
            if view.current_regime is None
            else {
                "regime_key": view.current_regime.regime_key,
                "label": view.current_regime.label,
                "manager_policy_version": view.current_regime.manager_policy_version,
                "agent_config_versions": list(view.current_regime.agent_config_versions),
                "has_complete_lineage": view.current_regime.has_complete_lineage,
                "lineage_inconsistent": view.current_regime.is_inconsistent,
                "started_at": view.current_regime_started_at.isoformat()
                if view.current_regime_started_at
                else None,
            },
            "current_model_performance": {
                key: _period_json(value) for key, value in current_windows.items()
            },
            "all_historical_model_performance": {
                key: _period_json(value) for key, value in all_windows.items()
            },
            "model_regimes": [
                {
                    "regime_key": cohort.regime_key,
                    "label": cohort.label,
                    "manager_policy_version": cohort.manager_policy_version,
                    "agent_config_versions": list(cohort.agent_config_versions),
                    "picks": cohort.picks,
                    "scored": cohort.scored,
                    "pending": cohort.pending,
                    "voids": cohort.voids,
                    "wins": cohort.wins,
                    "losses": cohort.losses,
                    "hit_rate": cohort.hit_rate,
                    "first_pick_date": cohort.first_pick_date.isoformat()
                    if cohort.first_pick_date
                    else None,
                    "last_pick_date": cohort.last_pick_date.isoformat()
                    if cohort.last_pick_date
                    else None,
                    "is_current": view.current_regime is not None
                    and cohort.regime_key == view.current_regime.regime_key,
                }
                for cohort in view.cohorts
            ],
            "selected_date": view.selected_date.isoformat()
            if view.selected_date
            else None,
            "days": [
                {
                    "date": row.decision_date.isoformat(),
                    "picks": row.picks,
                    "scored": row.scored,
                    "pending": row.pending,
                    "voids": row.voids,
                    "wins": row.wins,
                    "losses": row.losses,
                    "hit_rate": row.hit_rate,
                }
                for row in view.all_summaries
            ],
            "picks": [
                {
                    "manager_consensus_result_id": row.manager_consensus_result_id,
                    "picked_at": row.picked_at.isoformat(),
                    "match_start_at": row.match_start_at.isoformat()
                    if row.match_start_at
                    else None,
                    "tournament": row.tournament,
                    "player_1": row.player_1,
                    "player_2": row.player_2,
                    "selection": row.selection,
                    "consensus_probability": str(row.consensus_probability)
                    if row.consensus_probability is not None
                    else None,
                    "result": row.result_status,
                    "is_value_bet": row.is_value_bet,
                    "manager_policy_version": row.manager_policy_version,
                    "model_regime_key": row.model_regime_key,
                    "agent_config_versions": list(row.agent_config_versions),
                }
                for row in view.picks
            ],
        }
    )


__all__ = [
    "DailyModelPickSummary",
    "ModelPickResultsView",
    "ModelPickRow",
    "PeriodModelPickSummary",
    "aggregate_model_pick_summaries",
    "api_model_pick_results",
    "build_model_pick_windows",
    "aligned_production_model_picks_cte",
    "load_aligned_daily_model_pick_summaries",
    "load_daily_model_pick_summaries",
    "load_daily_model_picks",
    "model_pick_results",
]
