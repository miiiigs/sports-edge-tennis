"""Point-in-time daily comparison of original and frozen qualified Model Picks.

Read only. Shows every Manila date since the 2026-10-27 forward research start;
does not modify original published picks, the 73% acceptance scorecard,
Manager/Market/Veto, grading, odds or any historic outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import aligned_production_model_picks_cte
from sports_edge_tennis.dashboard.qualified_parallel import CONFIDENCE_FLOOR, POLICY_ID

START_DAY = date(2026, 10, 27)
VALID_OUTCOMES = frozenset(("WON", "LOST", "VOID", "PENDING"))


@dataclass(frozen=True)
class ComparisonDay:
    day: date
    original: int
    original_won: int
    original_lost: int
    original_void: int
    original_pending: int
    qualified: int
    qualified_won: int
    qualified_lost: int
    qualified_void: int
    qualified_pending: int
    avoided_losses: int
    missed_winners: int
    complete: bool

    @property
    def original_accuracy(self) -> float | None:
        graded = self.original_won + self.original_lost
        return self.original_won / graded if graded else None

    @property
    def qualified_accuracy(self) -> float | None:
        graded = self.qualified_won + self.qualified_lost
        return self.qualified_won / graded if graded else None

    @property
    def qualified_passes_daily_goal(self) -> bool:
        graded = self.qualified_won + self.qualified_lost
        return (
            self.complete and self.qualified_pending == 0 and graded >= 5
            and 100 * self.qualified_won >= 73 * graded
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "date": self.day.isoformat(), "original": self.original,
            "original_won": self.original_won, "original_lost": self.original_lost,
            "original_void": self.original_void, "original_pending": self.original_pending,
            "original_accuracy": self.original_accuracy,
            "qualified": self.qualified, "qualified_won": self.qualified_won,
            "qualified_lost": self.qualified_lost, "qualified_void": self.qualified_void,
            "qualified_pending": self.qualified_pending,
            "qualified_accuracy": self.qualified_accuracy,
            "avoided_losses": self.avoided_losses, "missed_winners": self.missed_winners,
            "complete": self.complete,
            "qualified_passes_daily_goal": self.qualified_passes_daily_goal,
        }


def summarize_rows(
    rows: list[tuple],
    *,
    as_of_date: date,
) -> tuple[ComparisonDay, ...]:
    """Summarize original first-issued PIT records, no hindsight selection."""
    if as_of_date < START_DAY:
        return ()
    by_date: dict[date, list[tuple[Decimal, str]]] = {}
    seen: set[int] = set()
    for day, match_id, probability, outcome in rows:
        if not START_DAY <= day <= as_of_date:
            raise ValueError("result outside requested research interval")
        mid = int(match_id)
        if mid in seen:
            raise ValueError("duplicate original sporting match in qualified history")
        seen.add(mid)
        try:
            prob = Decimal(probability)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("missing or invalid original confidence") from exc
        if not prob.is_finite() or not Decimal("0") <= prob <= Decimal("1"):
            raise ValueError("non-finite or out-of-range original confidence")
        if outcome not in VALID_OUTCOMES:
            raise ValueError("invalid persisted outcome")
        by_date.setdefault(day, []).append((prob, outcome))

    days: list[ComparisonDay] = []
    day = START_DAY
    while day <= as_of_date:
        original = by_date.get(day, [])
        retained = [(p, o) for p, o in original if p >= CONFIDENCE_FLOOR]
        skipped = [(p, o) for p, o in original if p < CONFIDENCE_FLOOR]
        cnt = lambda pairs, status: sum(o == status for _, o in pairs)
        days.append(ComparisonDay(
            day=day,
            original=len(original),
            original_won=cnt(original, "WON"),
            original_lost=cnt(original, "LOST"),
            original_void=cnt(original, "VOID"),
            original_pending=cnt(original, "PENDING"),
            qualified=len(retained),
            qualified_won=cnt(retained, "WON"),
            qualified_lost=cnt(retained, "LOST"),
            qualified_void=cnt(retained, "VOID"),
            qualified_pending=cnt(retained, "PENDING"),
            avoided_losses=cnt(skipped, "LOST"),
            missed_winners=cnt(skipped, "WON"),
            complete=day < as_of_date,
        ))
        day += timedelta(days=1)
    return tuple(reversed(days))


def qualified_history_sql() -> str:
    """Mirror dashboard's same canonical first-issued original aligned population."""
    return f"""
        WITH {aligned_production_model_picks_cte()},
        original AS (
            SELECT
                p.match_id, p.aligned_probability, p.selected_side,
                COALESCE((mv.match_start_at AT TIME ZONE 'Asia/Manila')::date,
                         (p.picked_at AT TIME ZONE 'Asia/Manila')::date) AS manila_date,
                result.outcome_status, result.winner_side
            FROM aligned_model_picks p
            LEFT JOIN LATERAL (
                SELECT mv0.match_start_at FROM match_versions mv0
                WHERE mv0.match_id = p.match_id
                  AND mv0.as_of <= p.picked_at
                  AND mv0.source_observed_at <= p.picked_at
                ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC,
                         mv0.match_version_id DESC LIMIT 1
            ) mv ON TRUE
            LEFT JOIN LATERAL (
                SELECT mro.outcome_status::text AS outcome_status, mro.winner_side
                FROM match_result_observations mro
                WHERE mro.match_id = p.match_id
                  AND mro.outcome_status IN ('COMPLETED','RETIRED','WALKOVER','CANCELLED')
                ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
                         mro.match_result_observation_id DESC LIMIT 1
            ) result ON TRUE
        )
        SELECT manila_date, match_id, aligned_probability,
               CASE
                 WHEN outcome_status = 'COMPLETED'
                  AND winner_side = selected_side THEN 'WON'
                 WHEN outcome_status = 'COMPLETED'
                  AND winner_side IN (1,2)
                  AND winner_side <> selected_side THEN 'LOST'
                 WHEN outcome_status IN ('RETIRED','WALKOVER','CANCELLED')
                   THEN 'VOID'
                 ELSE 'PENDING'
               END AS original_result
        FROM original
        WHERE manila_date BETWEEN %s::date AND %s::date
        ORDER BY manila_date, match_id
    """


def load_qualified_history(conn: psycopg.Connection, *, as_of_date: date) -> tuple[ComparisonDay, ...]:
    if as_of_date < START_DAY:
        return ()
    rows = conn.execute(qualified_history_sql(), (START_DAY, as_of_date)).fetchall()
    return summarize_rows(rows, as_of_date=as_of_date)


def daily_history_payload(days: tuple[ComparisonDay, ...]) -> dict[str, object]:
    return {
        "policy": POLICY_ID,
        "research_only": True,
        "live_selection_changed": False,
        "start_date": START_DAY.isoformat(),
        "timezone": "Asia/Manila",
        "days": [day.as_dict() for day in days],
    }
