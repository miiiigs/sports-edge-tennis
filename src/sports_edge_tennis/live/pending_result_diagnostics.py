"""Read-only, match-level reasons for every production pick still awaiting a result.

Aggregate recovery counts (``PENDING_RESULT_REFRESH checked=… terminal_found=…``)
cannot tell an operator *why* a specific pick is still ``PENDING``. On 2026-10-13
six picks shown on the 2026-10-12 dashboard day stayed pending for ~30 hours. They
were Jinan Challenger matches first listed at a 02:00Z placeholder start, then
rescheduled to 2026-10-13; the dashboard dates a pick by its point-in-time start,
so nothing showed they had been postponed. Older picks were stranded because the
provider event stopped being re-observed before any terminal status, which is
invisible once the bounded recovery window has moved on.

This module classifies each pending pick with exactly one primary reason plus
informational flags. It uses the same pick population as
``pending_result_refresh`` and never writes, repairs, voids or forces a result.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo

import psycopg

from sports_edge_tennis.live.pending_result_refresh import (
    DEFAULT_WINDOW_DAYS,
    pending_pick_matches_ctes,
    window_bounds,
)

REASON_NOT_STARTED = "NOT_STARTED"
REASON_AWAITING_RESULT = "AWAITING_RESULT"
REASON_RESULT_DELAYED = "RESULT_DELAYED"
REASON_TERMINAL_FIXTURE_WITHOUT_RESULT = "TERMINAL_FIXTURE_WITHOUT_RESULT"
REASON_FIXTURE_NOT_REOBSERVED = "PROVIDER_FIXTURE_NOT_REOBSERVED"
REASON_NO_API_TENNIS_LINK = "NO_API_TENNIS_LINK"
REASON_FIXTURE_POSTPONED = "FIXTURE_POSTPONED"

FLAG_RESCHEDULED = "RESCHEDULED"
FLAG_OUTSIDE_RECOVERY_WINDOW = "OUTSIDE_RECOVERY_WINDOW"
FLAG_TYPE_A_BET = "TYPE_A_BET"

AGE_NOT_STARTED = "NOT_STARTED"
AGE_UNDER_6H = "LT_6H"
AGE_6_TO_24H = "6H_24H"
AGE_24_TO_72H = "24H_72H"
AGE_OVER_72H = "GT_72H"

DEFAULT_GRACE_HOURS = 6
DEFAULT_NOT_REOBSERVED_HOURS = 6
# Tennis starts are order-of-play estimates: a match listed for 11:30 often
# starts 1-4 h later because earlier matches ran long. Production on 2026-10-12
# had 15/28 picks drift >=60 min, all same-day. A real schedule change is a
# placeholder or postponement moving the match by hours or to another day.
DEFAULT_RESCHEDULE_MINUTES = 360
DAY_CHANGE_MIN_MINUTES = 60
DEFAULT_LIMIT = 500
MAX_LIMIT = 2000

_TERMINAL = frozenset({"COMPLETED", "RETIRED", "WALKOVER", "CANCELLED"})
_POSTPONED = "POSTPONED"
API_TENNIS_SOURCE = "API_TENNIS"


@dataclass(frozen=True)
class PendingPickEvidence:
    """Everything persisted about one pending pick; no provider calls involved."""

    match_id: int
    pick_source: str
    picked_at: datetime
    selected_side: int | None
    has_type_a_bet: bool
    pit_match_start_at: datetime | None
    latest_match_start_at: datetime | None
    latest_match_status: str | None
    last_fixture_observed_at: datetime | None
    fixture_versions: int
    source_systems: tuple[str, ...]
    api_tennis_event_key: str | None
    tournament: str | None
    competition_tier: str | None
    player_1: str | None
    player_2: str | None


@dataclass(frozen=True)
class PendingPickDiagnosis:
    evidence: PendingPickEvidence
    reason: str
    flags: tuple[str, ...]
    age_bucket: str
    hours_since_start: float | None
    hours_since_last_observed: float | None

    @property
    def match_id(self) -> int:
        return self.evidence.match_id

    def log_line(self) -> str:
        ev = self.evidence
        return (
            "PENDING_RESULT_ITEM "
            f"match_id={ev.match_id} reason={self.reason} "
            f"flags={','.join(self.flags) or '-'} age={self.age_bucket} "
            f"pick_source={ev.pick_source} "
            f"pit_start={_iso(ev.pit_match_start_at)} "
            f"latest_start={_iso(ev.latest_match_start_at)} "
            f"fixture_status={ev.latest_match_status or '-'} "
            f"last_observed={_iso(ev.last_fixture_observed_at)} "
            f"api_tennis_event={ev.api_tennis_event_key or '-'}"
        )


@dataclass(frozen=True)
class PendingResultDiagnosticsReport:
    generated_at: datetime
    recovery_window_start: date
    recovery_window_end: date
    items: tuple[PendingPickDiagnosis, ...]

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def by_reason(self) -> dict[str, int]:
        return dict(sorted(Counter(item.reason for item in self.items).items()))

    @property
    def by_age(self) -> dict[str, int]:
        return dict(sorted(Counter(item.age_bucket for item in self.items).items()))

    @property
    def by_flag(self) -> dict[str, int]:
        return dict(
            sorted(Counter(flag for item in self.items for flag in item.flags).items())
        )

    def summary_line(self) -> str:
        def fmt(counts: dict[str, int]) -> str:
            return ",".join(f"{key}:{value}" for key, value in counts.items()) or "-"

        return (
            "PENDING_RESULT_DIAGNOSTICS "
            f"total={self.total} reasons={fmt(self.by_reason)} "
            f"ages={fmt(self.by_age)} flags={fmt(self.by_flag)} "
            f"recovery_window={self.recovery_window_start.isoformat()}"
            f"..{self.recovery_window_end.isoformat()}"
        )


def _iso(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


def _hours(later: datetime, earlier: datetime | None) -> float | None:
    if earlier is None:
        return None
    return (later - earlier).total_seconds() / 3600.0


def age_bucket(hours_since_start: float | None) -> str:
    if hours_since_start is None or hours_since_start < 0:
        return AGE_NOT_STARTED
    if hours_since_start < 6:
        return AGE_UNDER_6H
    if hours_since_start < 24:
        return AGE_6_TO_24H
    if hours_since_start < 72:
        return AGE_24_TO_72H
    return AGE_OVER_72H


def is_schedule_change(
    pit_match_start_at: datetime | None,
    latest_match_start_at: datetime | None,
    *,
    reschedule_minutes: int = DEFAULT_RESCHEDULE_MINUTES,
    local_timezone: tzinfo | None = None,
) -> bool:
    """True only when persisted fixture versions show the start really moved.

    Evidence is the point-in-time start the pick was issued against versus the
    latest provider start for the same canonical match. A move counts when it is
    at least ``reschedule_minutes``, or, given ``local_timezone``, when it moves
    the match to another local day by at least ``DAY_CHANGE_MIN_MINUTES``. How
    long a pick has been pending is never used: a slow result feed is not a
    reschedule, and ordinary order-of-play drift is not either.
    """
    if pit_match_start_at is None or latest_match_start_at is None:
        return False
    delta = abs((latest_match_start_at - pit_match_start_at).total_seconds())
    if delta >= reschedule_minutes * 60:
        return True
    return (
        local_timezone is not None
        and delta >= DAY_CHANGE_MIN_MINUTES * 60
        and pit_match_start_at.astimezone(local_timezone).date()
        != latest_match_start_at.astimezone(local_timezone).date()
    )


def classify_pending_pick(
    evidence: PendingPickEvidence,
    *,
    now: datetime,
    recovery_window_start: date,
    grace_hours: int = DEFAULT_GRACE_HOURS,
    not_reobserved_hours: int = DEFAULT_NOT_REOBSERVED_HOURS,
    reschedule_minutes: int = DEFAULT_RESCHEDULE_MINUTES,
) -> PendingPickDiagnosis:
    """Exactly one primary reason per pending pick, in a fixed precedence order."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    start = evidence.latest_match_start_at
    since_start = _hours(now, start)
    since_observed = _hours(now, evidence.last_fixture_observed_at)

    flags: list[str] = []
    if is_schedule_change(
        evidence.pit_match_start_at, start, reschedule_minutes=reschedule_minutes
    ):
        flags.append(FLAG_RESCHEDULED)
    if start is not None and start.astimezone(UTC).date() < recovery_window_start:
        flags.append(FLAG_OUTSIDE_RECOVERY_WINDOW)
    if evidence.has_type_a_bet:
        flags.append(FLAG_TYPE_A_BET)

    if start is not None and start > now:
        reason = REASON_NOT_STARTED
    elif evidence.latest_match_status == _POSTPONED:
        # Provider marked the fixture postponed and has not yet published a new
        # future start. Not a result-ingestion failure, and never graded.
        reason = REASON_FIXTURE_POSTPONED
    elif (evidence.latest_match_status or "") in _TERMINAL:
        # Fixture feed says the match ended but no parseable terminal result was
        # persisted (e.g. no winner reported). Never inferred into a result here.
        reason = REASON_TERMINAL_FIXTURE_WITHOUT_RESULT
    elif evidence.api_tennis_event_key is None:
        reason = REASON_NO_API_TENNIS_LINK
    elif since_observed is not None and since_observed >= not_reobserved_hours and (
        since_start is None or since_start >= grace_hours
    ):
        reason = REASON_FIXTURE_NOT_REOBSERVED
    elif since_start is not None and since_start < grace_hours:
        reason = REASON_AWAITING_RESULT
    else:
        reason = REASON_RESULT_DELAYED

    return PendingPickDiagnosis(
        evidence=evidence,
        reason=reason,
        flags=tuple(flags),
        age_bucket=age_bucket(since_start),
        hours_since_start=since_start,
        hours_since_last_observed=since_observed,
    )


def _evidence_sql() -> str:
    return f"""
        WITH {pending_pick_matches_ctes()}
        SELECT
            pm.match_id,
            pm.pick_source,
            pm.picked_at,
            pm.selected_side,
            EXISTS (
                SELECT 1
                FROM decisions d
                WHERE d.match_id = pm.match_id
                  AND d.signal_type = 'TYPE_A_MODEL_EDGE'
                  AND d.decision_status = 'BET'
                  AND d.market_type = 'MATCH_WINNER'
                  AND d.selection_side IN (1, 2)
            ) AS has_type_a_bet,
            pit.match_start_at AS pit_match_start_at,
            lv.match_start_at AS latest_match_start_at,
            lv.match_status AS latest_match_status,
            lv.last_observed_at,
            lv.versions,
            src.source_systems,
            src.api_tennis_event_key,
            t.canonical_name AS tournament,
            t.competition_tier::text AS competition_tier,
            p1.canonical_name AS player_1,
            p2.canonical_name AS player_2
        FROM pick_matches pm
        JOIN matches m ON m.match_id = pm.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        LEFT JOIN LATERAL (
            SELECT mv0.match_start_at
            FROM match_versions mv0
            WHERE mv0.match_id = pm.match_id
              AND mv0.as_of <= pm.picked_at
              AND mv0.source_observed_at <= pm.picked_at
            ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC, mv0.match_version_id DESC
            LIMIT 1
        ) pit ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                latest.match_start_at,
                latest.match_status,
                latest.player_1_id,
                latest.player_2_id,
                agg.last_observed_at,
                agg.versions
            FROM (
                SELECT
                    mv1.match_start_at,
                    mv1.match_status::text AS match_status,
                    mv1.player_1_id,
                    mv1.player_2_id
                FROM match_versions mv1
                WHERE mv1.match_id = pm.match_id
                ORDER BY mv1.as_of DESC, mv1.source_observed_at DESC, mv1.match_version_id DESC
                LIMIT 1
            ) latest
            CROSS JOIN (
                SELECT max(mv2.as_of) AS last_observed_at, count(*)::int AS versions
                FROM match_versions mv2
                WHERE mv2.match_id = pm.match_id
            ) agg
        ) lv ON TRUE
        LEFT JOIN players p1 ON p1.player_id = lv.player_1_id
        LEFT JOIN players p2 ON p2.player_id = lv.player_2_id
        LEFT JOIN LATERAL (
            SELECT
                array_agg(DISTINCT s.source_system ORDER BY s.source_system) AS source_systems,
                min(s.source_match_id) FILTER (
                    WHERE s.source_system = '{API_TENNIS_SOURCE}'
                ) AS api_tennis_event_key
            FROM match_source_ids s
            WHERE s.match_id = pm.match_id
        ) src ON TRUE
        WHERE NOT EXISTS (
            SELECT 1
            FROM match_result_observations mro
            WHERE mro.match_id = pm.match_id
              AND mro.outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
        )
          AND pm.picked_at >= %(since)s
        ORDER BY lv.match_start_at NULLS LAST, pm.match_id
        LIMIT %(limit)s
    """


def load_pending_pick_evidence(
    conn: psycopg.Connection,
    *,
    since: datetime,
    limit: int = DEFAULT_LIMIT,
) -> tuple[PendingPickEvidence, ...]:
    if since.tzinfo is None or since.utcoffset() is None:
        raise ValueError("since must be timezone-aware")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    rows = conn.execute(_evidence_sql(), {"since": since, "limit": limit}).fetchall()
    return tuple(
        PendingPickEvidence(
            match_id=int(row[0]),
            pick_source=str(row[1]),
            picked_at=row[2],
            selected_side=int(row[3]) if row[3] is not None else None,
            has_type_a_bet=bool(row[4]),
            pit_match_start_at=row[5],
            latest_match_start_at=row[6],
            latest_match_status=str(row[7]) if row[7] is not None else None,
            last_fixture_observed_at=row[8],
            fixture_versions=int(row[9] or 0),
            source_systems=tuple(str(value) for value in (row[10] or ())),
            api_tennis_event_key=str(row[11]) if row[11] is not None else None,
            tournament=str(row[12]) if row[12] is not None else None,
            competition_tier=str(row[13]) if row[13] is not None else None,
            player_1=str(row[14]) if row[14] is not None else None,
            player_2=str(row[15]) if row[15] is not None else None,
        )
        for row in rows
    )


def load_pending_result_diagnostics(
    conn: psycopg.Connection,
    *,
    now: datetime | None = None,
    lookback_days: int = 60,
    limit: int = DEFAULT_LIMIT,
    recovery_window_days: int = DEFAULT_WINDOW_DAYS,
) -> PendingResultDiagnosticsReport:
    reference = now or datetime.now(UTC)
    if not 1 <= lookback_days <= 400:
        raise ValueError("lookback_days must be between 1 and 400")
    window_start, window_end = window_bounds(reference, recovery_window_days)
    evidence = load_pending_pick_evidence(
        conn, since=reference - timedelta(days=lookback_days), limit=limit
    )
    return PendingResultDiagnosticsReport(
        generated_at=reference,
        recovery_window_start=window_start,
        recovery_window_end=window_end,
        items=tuple(
            classify_pending_pick(
                item, now=reference, recovery_window_start=window_start
            )
            for item in evidence
        ),
    )


def diagnosis_json(item: PendingPickDiagnosis) -> dict[str, object]:
    ev = item.evidence

    def iso(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "match_id": ev.match_id,
        "reason": item.reason,
        "flags": list(item.flags),
        "age_bucket": item.age_bucket,
        "hours_since_start": item.hours_since_start,
        "hours_since_last_observed": item.hours_since_last_observed,
        "pick_source": ev.pick_source,
        "picked_at": iso(ev.picked_at),
        "selected_side": ev.selected_side,
        "has_type_a_bet": ev.has_type_a_bet,
        "pit_match_start_at": iso(ev.pit_match_start_at),
        "latest_match_start_at": iso(ev.latest_match_start_at),
        "latest_fixture_status": ev.latest_match_status,
        "last_fixture_observed_at": iso(ev.last_fixture_observed_at),
        "fixture_versions": ev.fixture_versions,
        "source_systems": list(ev.source_systems),
        "api_tennis_event_key": ev.api_tennis_event_key,
        "tournament": ev.tournament,
        "competition_tier": ev.competition_tier,
        "player_1": ev.player_1,
        "player_2": ev.player_2,
    }


def report_json(report: PendingResultDiagnosticsReport) -> dict[str, object]:
    return {
        "generated_at": report.generated_at.isoformat(),
        "recovery_window": {
            "start": report.recovery_window_start.isoformat(),
            "end": report.recovery_window_end.isoformat(),
        },
        "total": report.total,
        "by_reason": report.by_reason,
        "by_age": report.by_age,
        "by_flag": report.by_flag,
        "items": [diagnosis_json(item) for item in report.items],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only match-level reasons for pending production picks"
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL"),
    )
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)
    if not args.dsn:
        print("PENDING_RESULT_DIAGNOSTICS_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    with psycopg.connect(
        args.dsn, options="-c default_transaction_read_only=on"
    ) as conn:
        report = load_pending_result_diagnostics(
            conn, lookback_days=args.lookback_days, limit=args.limit
        )
    print(report.summary_line(), flush=True)
    for item in report.items:
        print(item.log_line(), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
