"""Read-only model-pick results for the Sports Edge Tennis dashboard.

A Model Pick exists when the Manager reaches CONSENSUS on a winner. It is
scored independently of price/value. Value Bets are a subset of Model Picks, so
those matches remain in this ledger but still count only once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sports_edge_tennis.dashboard import presentation as pres
from sports_edge_tennis.dashboard.aligned_model_picks import (
    ALIGNED_PICK_DATE_FILTER,
    aligned_production_model_picks_cte,
)
from sports_edge_tennis.dashboard.app import (
    DEMO_MODE,
    _connect,
    _latest_activity,
    _request_timezone,
    templates,
)
from sports_edge_tennis.dashboard.history_alignment import (
    ALIGNED_LIVE_HISTORY_START,
    ALIGNED_MIN_MODEL_PROBABILITY,
    since_aligned_start,
)
from sports_edge_tennis.dashboard.pagination import (
    DEFAULT_PAGE_SIZE,
    build_page_url,
    paginate,
)
from sports_edge_tennis.dashboard.timezone import (
    DEFAULT_TIMEZONE,
    DashboardTimezone,
    dashboard_today,
)
from sports_edge_tennis.dashboard.manager_five_day_target import manager_five_day_target
from sports_edge_tennis.dashboard.aligned_five_day_target import aligned_five_day_target
from sports_edge_tennis.live.pending_result_diagnostics import is_schedule_change
from sports_edge_tennis.performance.regimes import (
    ModelRegimeCohort,
    ModelRegimeIdentity,
    current_model_regime_started_at,
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
    # Classifications are display-only; raw pending remains unchanged in the
    # append-only audit ledger and Brian's five-day acceptance calculation.
    awaiting_result: int = 0
    not_started: int = 0
    rescheduled: int = 0
    postponed: int = 0
    needs_review: int = 0


@dataclass(frozen=True)
class PeriodModelPickSummary:
    picks: int
    scored: int
    pending: int
    voids: int
    wins: int
    losses: int
    hit_rate: float | None
    # Classifications are display-only; raw pending remains unchanged in the
    # append-only audit ledger and Brian's five-day acceptance calculation.
    awaiting_result: int = 0
    not_started: int = 0
    rescheduled: int = 0
    postponed: int = 0
    needs_review: int = 0


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
    pick_type: str | None = None
    match_id: int | None = None
    # Latest persisted provider fixture state for the same canonical match. The
    # pick stays dated by ``match_start_at`` (the point-in-time start it was
    # issued against); these only explain a later schedule change.
    latest_match_start_at: datetime | None = None
    latest_fixture_status: str | None = None
    # Set by the loader with the shared ``is_schedule_change`` rule evaluated
    # in the dashboard timezone, so a move across local midnight is caught.
    schedule_changed: bool = False

    @property
    def lifecycle_status(self) -> str:
        """Customer-facing state, never a settlement or W/L modification."""
        if self.result_status != "PENDING":
            return self.result_status
        if self.is_postponed:
            return "POSTPONED"
        if self.latest_fixture_status in ("COMPLETED", "RETIRED", "WALKOVER", "CANCELLED"):
            return "NEEDS_RESULT_REVIEW"
        if self.is_rescheduled:
            return "RESCHEDULED"
        if self.latest_fixture_status in ("NOT_STARTED", "SCHEDULED"):
            return "NOT_STARTED"
        if self.latest_fixture_status is None:
            return "NEEDS_RESULT_REVIEW"
        return "AWAITING_RESULT"

    @property
    def pending_explanation(self) -> str | None:
        """Explain the provider's *fixture* state, not an inferred result.

        These are read-only clues. A terminal fixture without a persisted
        official result stays PENDING, never silently wins/loses/voids.
        """
        if self.result_status != "PENDING":
            return None
        status = self.latest_fixture_status
        if status == "WALKOVER":
            return "Walkover reported · result verification pending"
        if status == "RETIRED":
            return "Retirement reported · result verification pending"
        if status == "COMPLETED":
            return "Match ended · final result not verified"
        if status == "CANCELLED":
            return "Cancellation reported · result verification pending"
        if self.is_postponed:
            return "Postponed by provider · not graded"
        if self.is_rescheduled:
            return "Awaiting rescheduled match"
        return "Awaiting confirmed match result"

    @property
    def is_rescheduled(self) -> bool:
        """Provider moved the start after the pick was issued."""
        return self.schedule_changed and self.latest_match_start_at is not None

    @property
    def is_postponed(self) -> bool:
        """Provider currently marks the fixture postponed; it is never graded."""
        return self.result_status == "PENDING" and self.latest_fixture_status == "POSTPONED"

    @property
    def is_wide_spread(self) -> bool:
        return self.pick_type == "A1_A2_WIDE_SPREAD"

    @property
    def type_label(self) -> str:
        if self.is_wide_spread:
            return "MODEL PICK · WIDE SPREAD"
        if self.is_value_bet:
            return "VALUE BET"
        return "MODEL PICK"

    @property
    def probability_source_label(self) -> str:
        """Confidence label that never reads as a calibrated win probability."""
        if self.is_wide_spread:
            return "Wide-Spread Tracking Confidence · A1/A2 Tracking Average"
        return "Model Probability · Uncalibrated"

    @property
    def probability_display_text(self) -> str:
        """Whole-percent display only; the stored probability is unchanged."""
        return pres.whole_percent(self.consensus_probability)


@dataclass(frozen=True)
class ModelPickResultsView:
    all_summaries: tuple[DailyModelPickSummary, ...] = ()
    aligned_summaries: tuple[DailyModelPickSummary, ...] = ()
    # Brian's acceptance always uses Manila dates, regardless of UI timezone.
    aligned_acceptance_summaries: tuple[DailyModelPickSummary, ...] = ()
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
        awaiting_result=int(row[7]) if len(row) >= 12 else 0,
        not_started=int(row[8]) if len(row) >= 12 else 0,
        rescheduled=int(row[9]) if len(row) >= 12 else 0,
        postponed=int(row[10]) if len(row) >= 12 else 0,
        needs_review=int(row[11]) if len(row) >= 12 else 0,
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
        awaiting_result=sum(row.awaiting_result for row in selected),
        not_started=sum(row.not_started for row in selected),
        rescheduled=sum(row.rescheduled for row in selected),
        postponed=sum(row.postponed for row in selected),
        needs_review=sum(row.needs_review for row in selected),
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


def load_aligned_daily_model_pick_summaries(
    conn: psycopg.Connection,
    *,
    limit: int = 5000,
    timezone_name: str = "UTC",
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
                    (mv.match_start_at AT TIME ZONE %s::text)::date,
                    (p.picked_at AT TIME ZONE %s::text)::date
                ) AS match_date,
                mv.match_start_at AS original_match_start_at
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
                r.winner_side,
                lv.match_start_at AS latest_match_start_at,
                lv.latest_status
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
            LEFT JOIN LATERAL (
                SELECT mv1.match_start_at,
                       mv1.match_status::text AS latest_status
                FROM match_versions mv1
                WHERE mv1.match_id = d.match_id
                ORDER BY mv1.as_of DESC, mv1.source_observed_at DESC,
                         mv1.match_version_id DESC
                LIMIT 1
            ) lv ON TRUE
        ),
        classified AS (
            SELECT *,
                CASE
                    WHEN outcome_status = 'COMPLETED'
                         AND (winner_side IS NULL OR winner_side NOT IN (1, 2))
                         THEN 'NEEDS_REVIEW'
                    WHEN outcome_status IS NOT NULL THEN 'SETTLED'
                    WHEN latest_status = 'POSTPONED' THEN 'POSTPONED'
                    WHEN latest_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
                         THEN 'NEEDS_REVIEW'
                    WHEN original_match_start_at IS NOT NULL
                         AND latest_match_start_at IS NOT NULL
                         AND (
                             abs(extract(epoch FROM (latest_match_start_at - original_match_start_at))) >= 21600
                             OR (
                                 abs(extract(epoch FROM (latest_match_start_at - original_match_start_at))) >= 3600
                                 AND (latest_match_start_at AT TIME ZONE %s::text)::date
                                   <> (original_match_start_at AT TIME ZONE %s::text)::date
                             )
                         ) THEN 'RESCHEDULED'
                    WHEN latest_status IN ('NOT_STARTED', 'SCHEDULED')
                         OR latest_match_start_at > now() THEN 'NOT_STARTED'
                    WHEN latest_status IS NULL THEN 'NEEDS_REVIEW'
                    ELSE 'AWAITING_RESULT'
                END AS result_lifecycle
            FROM with_results
        )
        SELECT
            match_date,
            count(*)::int AS picks,
            count(*) FILTER (
                WHERE outcome_status = 'COMPLETED' AND winner_side IN (1, 2)
            )::int AS scored,
            count(*) FILTER (
                WHERE outcome_status IS NULL OR (
                    outcome_status = 'COMPLETED'
                    AND (winner_side IS NULL OR winner_side NOT IN (1, 2))
                )
            )::int AS pending,
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
            )::int AS losses,
            count(*) FILTER (WHERE result_lifecycle = 'AWAITING_RESULT')::int AS awaiting_result,
            count(*) FILTER (WHERE result_lifecycle = 'NOT_STARTED')::int AS not_started,
            count(*) FILTER (WHERE result_lifecycle = 'RESCHEDULED')::int AS rescheduled,
            count(*) FILTER (WHERE result_lifecycle = 'POSTPONED')::int AS postponed,
            count(*) FILTER (WHERE result_lifecycle = 'NEEDS_REVIEW')::int AS needs_review
        FROM classified
        WHERE match_date >= %s
        GROUP BY match_date
        ORDER BY match_date DESC
        LIMIT %s
        """,
        (
            timezone_name, timezone_name,
            timezone_name, timezone_name,
            ALIGNED_LIVE_HISTORY_START, limit
        ),
    ).fetchall()
    return tuple(_summary_from_row(row) for row in rows)


def load_daily_model_pick_summaries(
    conn: psycopg.Connection,
    *,
    limit: int = 5000,
    model_regime_key: str | None = None,
    timezone_name: str = "UTC",
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
                    (mv.match_start_at AT TIME ZONE %s::text)::date,
                    (p.picked_at AT TIME ZONE %s::text)::date
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
            count(*) FILTER (
                WHERE outcome_status IS NULL OR (
                    outcome_status = 'COMPLETED'
                    AND (winner_side IS NULL OR winner_side NOT IN (1, 2))
                )
            )::int AS pending,
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
        (timezone_name, timezone_name, model_regime_key, model_regime_key, limit),
    ).fetchall()
    return tuple(_summary_from_row(row) for row in rows)


def load_daily_model_picks(
    conn: psycopg.Connection,
    *,
    decision_date: date,
    timezone_name: str = "UTC",
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
            (mv.match_start_at AT TIME ZONE %s::text)::date,
            (p.picked_at AT TIME ZONE %s::text)::date
        ) = %s
        ORDER BY p.picked_at DESC, p.manager_consensus_result_id DESC
        """,
        (timezone_name, timezone_name, decision_date),
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


def load_aligned_daily_model_picks(
    conn: psycopg.Connection,
    *,
    decision_date: date,
    timezone_name: str = "UTC",
) -> tuple[ModelPickRow, ...]:
    """Detail rows for the aligned Model Picks population on one local day.

    This uses the *same* ``aligned_production_model_picks_cte()`` as the daily
    summary, so the selected-day detail count always equals the aligned
    summary's pick count for that day. Wide-spread rows are tracking-only and
    are labelled through ``pick_type``.
    """
    local_zone = ZoneInfo(timezone_name)
    veto_available = veto_audits_available(conn)
    rows = conn.execute(
        f"""
        WITH {aligned_production_model_picks_cte()},
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
            p.aligned_probability,
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
            mpv.policy_version AS manager_policy_version,
            p.aligned_pick_type,
            p.match_id,
            lv.match_start_at AS latest_match_start_at,
            lv.match_status AS latest_fixture_status
        FROM aligned_model_picks p
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
        LEFT JOIN LATERAL (
            SELECT
                mv1.match_start_at,
                mv1.match_status::text AS match_status
            FROM match_versions mv1
            WHERE mv1.match_id = p.match_id
            ORDER BY mv1.as_of DESC,
                     mv1.source_observed_at DESC,
                     mv1.match_version_id DESC
            LIMIT 1
        ) lv ON TRUE
        LEFT JOIN first_bets fb ON fb.match_id = p.match_id
        LEFT JOIN manager_consensus_results mcr2
          ON mcr2.manager_consensus_result_id = p.manager_consensus_result_id
        LEFT JOIN manager_policy_versions mpv
          ON mpv.manager_policy_version_id = mcr2.manager_policy_version_id
        WHERE {ALIGNED_PICK_DATE_FILTER}
        ORDER BY p.picked_at DESC, p.manager_consensus_result_id DESC
        """,
        (timezone_name, timezone_name, decision_date),
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
            model_regime_key=None,
            agent_config_versions=(None, None, None),
            pick_type=str(row[11]) if row[11] is not None else None,
            match_id=int(row[12]) if row[12] is not None else None,
            latest_match_start_at=row[13],
            latest_fixture_status=str(row[14]) if row[14] is not None else None,
            schedule_changed=is_schedule_change(
                row[2], row[13], local_timezone=local_zone
            ),
        )
        for row in rows
    )


def _load_history(
    requested_date: date | None, tz: DashboardTimezone | None = None
) -> ModelPickResultsView:
    tz = tz or DEFAULT_TIMEZONE
    if DEMO_MODE:
        return ModelPickResultsView(selected_date=requested_date)
    conn = _connect()
    if conn is None:
        return ModelPickResultsView(selected_date=requested_date, failed=True)
    try:
        # Aligned current-rule population is the primary client ledger.
        aligned_summaries = load_aligned_daily_model_pick_summaries(
            conn, timezone_name=tz.iana
        )
        aligned_acceptance_summaries = (
            aligned_summaries if tz.iana == "Asia/Manila" else
            load_aligned_daily_model_pick_summaries(
                conn, limit=100, timezone_name="Asia/Manila"
            )
        )
        # Original first-production Manager ledger is retained for auditing.
        summaries = load_daily_model_pick_summaries(conn, timezone_name=tz.iana)
        current_regime = resolve_current_model_regime(conn)
        current_summaries: tuple[DailyModelPickSummary, ...] = ()
        started_at = None
        if (
            current_regime is not None
            and current_regime.has_complete_lineage
            and not current_regime.is_inconsistent
        ):
            current_summaries = load_daily_model_pick_summaries(
                conn,
                model_regime_key=current_regime.regime_key,
                timezone_name=tz.iana,
            )
            started_at = current_model_regime_started_at(
                conn, current_regime.regime_key
            )
        cohorts = load_model_regime_cohorts(conn, timezone_name=tz.iana)
        # Default the selected detail day to *today* in the chosen dashboard zone,
        # exactly like /today. A future slate with earlier picks must never hijack
        # the default; the daily history list still shows future/past days.
        selected_date = requested_date or dashboard_today(tz)
        # The selected-day summary and detail both come from the aligned
        # population, so they can never disagree about the day's pick count.
        selected_summary = next(
            (
                row
                for row in aligned_summaries
                if selected_date is not None and row.decision_date == selected_date
            ),
            None,
        )
        picks = (
            load_aligned_daily_model_picks(
                conn, decision_date=selected_date, timezone_name=tz.iana
            )
            if selected_date is not None
            else ()
        )
        return ModelPickResultsView(
            all_summaries=summaries,
            aligned_summaries=aligned_summaries,
            aligned_acceptance_summaries=aligned_acceptance_summaries,
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


def _aligned_acceptance_today() -> date:
    """Independent from viewer-selected UTC/London timezone."""
    return datetime.now(ZoneInfo("Asia/Manila")).date()


def _period_json(summary: PeriodModelPickSummary) -> dict[str, object]:
    return {
        "picks": summary.picks,
        "scored": summary.scored,
        "pending": summary.pending,
        "voids": summary.voids,
        "wins": summary.wins,
        "losses": summary.losses,
        "hit_rate": summary.hit_rate,
        "awaiting_result": summary.awaiting_result,
        "not_started": summary.not_started,
        "rescheduled": summary.rescheduled,
        "postponed": summary.postponed,
        "needs_review": summary.needs_review,
    }


@router.get("/model-pick-results", response_class=HTMLResponse)
def model_pick_results(
    request: Request,
    date: str | None = Query(default=None),
    history_page: int = Query(default=1, ge=1),
    detail_page: int = Query(default=1, ge=1),
    audit_page: int = Query(default=1, ge=1),
):
    try:
        requested_date = _parse_optional_date(date)
    except ValueError:
        return HTMLResponse("Invalid date", status_code=400)

    tz = _request_timezone(request)
    view = _load_history(requested_date, tz)
    as_of_date = dashboard_today(tz)
    aligned_summaries = since_aligned_start(view.aligned_summaries)

    history_rows, history_pagination = paginate(
        aligned_summaries, page=history_page, page_size=DEFAULT_PAGE_SIZE
    )
    detail_rows, detail_pagination = paginate(
        view.picks, page=detail_page, page_size=DEFAULT_PAGE_SIZE
    )
    audit_rows, audit_pagination = paginate(
        view.all_summaries, page=audit_page, page_size=DEFAULT_PAGE_SIZE
    )

    return templates.TemplateResponse(
        request,
        "model_pick_results.html",
        {
            "pres": pres,
            "demo_mode": DEMO_MODE,
            "latest_activity": _latest_activity(),
            # Primary aligned ledger drives the daily table and detail rows.
            "daily_summaries": history_rows,
            "history_pagination": history_pagination,
            # Original first-production Manager ledger, kept for auditing.
            "original_daily_summaries": audit_rows,
            "audit_pagination": audit_pagination,
            "original_picks": sum(row.picks for row in view.all_summaries),
            "selected_date": view.selected_date,
            "selected_summary": view.selected_summary,
            "daily_picks": detail_rows,
            "detail_pagination": detail_pagination,
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
            "manager_accuracy_target": manager_five_day_target(
                view.all_summaries, as_of_date=as_of_date
            ),
            "aligned_accuracy_target": aligned_five_day_target(
                view.aligned_acceptance_summaries,
                as_of_date=_aligned_acceptance_today(),
            ),
            "performance_as_of": as_of_date,
            "model_pick_history_error": view.failed,
            "page_url": lambda param, page: build_page_url(request, param, page),
            "pct": _pct,
        },
    )


@router.get("/api/dashboard/model-pick-results")
def api_model_pick_results(request: Request, date: str | None = Query(default=None)):
    try:
        requested_date = _parse_optional_date(date)
    except ValueError:
        return JSONResponse({"error": "invalid date"}, status_code=400)

    tz = _request_timezone(request)
    view = _load_history(requested_date, tz)
    if view.failed:
        return JSONResponse({"error": "model pick history unavailable"}, status_code=503)

    as_of_date = dashboard_today(tz)
    all_windows = build_model_pick_windows(view.all_summaries, as_of_date=as_of_date)
    # The aligned block is built from the aligned current-rule population, the
    # same population the page headlines and daily table use.
    aligned_summaries = since_aligned_start(view.aligned_summaries)
    aligned_windows = build_model_pick_windows(
        aligned_summaries, as_of_date=as_of_date
    )
    current_windows = build_model_pick_windows(
        view.current_summaries, as_of_date=as_of_date
    )

    return JSONResponse(
        {
            "manager_accuracy_target": manager_five_day_target(
                view.all_summaries, as_of_date=as_of_date
            ),
            "aligned_accuracy_target": aligned_five_day_target(
                view.aligned_acceptance_summaries,
                as_of_date=_aligned_acceptance_today(),
            ),
            "as_of_date": as_of_date.isoformat(),
            "display_timezone": {
                "key": tz.key,
                "iana": tz.iana,
                "label": tz.display_label,
            },
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
            # The primary day counts must describe the same aligned
            # population as the selected-day detail records. Previously this
            # used the Manager-only historical ledger while the details also
            # included A1/A2 wide-spread research tracking.
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
                    "awaiting_result": row.awaiting_result,
                    "not_started": row.not_started,
                    "rescheduled": row.rescheduled,
                    "postponed": row.postponed,
                    "needs_review": row.needs_review,
                }
                for row in aligned_summaries
            ],
            # Preserve the original Manager-only historical population as a
            # separately named audit series. Never blend it into aligned
            # pick counts or silently discard it when correcting the API.
            "manager_only_days": [
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
                    "lifecycle_status": row.lifecycle_status,
                    "pending_explanation": row.pending_explanation,
                    "latest_fixture_status": row.latest_fixture_status,
                    "is_value_bet": row.is_value_bet,
                    # This read-only classification is essential to distinguish
                    # actionable Manager history from research-only tracking.
                    "pick_type": row.pick_type,
                    "is_tracking_only": row.pick_type == "A1_A2_WIDE_SPREAD",
                    "is_manager_consensus": row.pick_type == "MANAGER_CONSENSUS",
                    "manager_policy_version": row.manager_policy_version,
                    "model_regime_key": row.model_regime_key,
                    "agent_config_versions": list(row.agent_config_versions),
                    "match_id": row.match_id,
                    "latest_match_start_at": row.latest_match_start_at.isoformat()
                    if row.latest_match_start_at
                    else None,
                    "latest_fixture_status": row.latest_fixture_status,
                    "rescheduled": row.is_rescheduled,
                    "postponed": row.is_postponed,
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
    "aligned_production_model_picks_cte",
    "api_model_pick_results",
    "build_model_pick_windows",
    "load_aligned_daily_model_pick_summaries",
    "load_daily_model_pick_summaries",
    "load_daily_model_picks",
    "model_pick_results",
]
