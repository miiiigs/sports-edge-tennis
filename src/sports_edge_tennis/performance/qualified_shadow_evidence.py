"""Read-only qualified Model Picks shadow: prospectively test the preselected 70% gate.

This does NOT change the canonical Results ledger, published percentages,
Manager abstentions, market decisions, betting rules or Brian's 73% acceptance.
The >=70% candidate was selected exclusively on results observed before
2026-10-27 00:00 Asia/Manila (see PR #273 chronological research).
Do not infer performance from an unsettled or low-N day.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg

from sports_edge_tennis.performance.aligned_historical_replay import (
    ReplayPick,
    _counts,
    aligned_replay_sql,
    VALID_RESULTS,
    VALID_PICKS,
)

MANILA = ZoneInfo("Asia/Manila")
START_DAY = date(2026, 10, 27)
POLICY_ID = "PIT_CONFIDENCE_GE70_V1"
MIN_CONFIDENCE = Decimal("0.70")
MIN_DAILY_GRADED = 5
WINDOW_DAYS = 5


@dataclass(frozen=True)
class QualifiedDay:
    day: date
    original: int
    retained: int
    won: int
    lost: int
    void: int
    pending: int
    avoided_losses: int
    missed_winners: int
    complete_day: bool

    @property
    def graded(self) -> int:
        return self.won + self.lost

    @property
    def accuracy_pct(self) -> float | None:
        return 100.0 * self.won / self.graded if self.graded else None

    @property
    def status(self) -> str:
        if not self.complete_day:
            return "IN_PROGRESS"
        if self.original < 1:
            return "NO_ORIGINAL"
        if self.retained == 0:
            return "NO_QUALIFIED"
        if self.pending:
            return "UNSETTLED"
        if self.graded < MIN_DAILY_GRADED:
            return "TOO_FEW_GRADED"
        return "RESEARCH_MET73" if 100 * self.won >= 73 * self.graded else "RESEARCH_BELOW73"


def _parse(rows: list[tuple]) -> tuple[ReplayPick, ...]:
    picks: list[ReplayPick] = []
    seen: set[int] = set()
    for row in rows:
        (match_id, day, kind, probability, side, surface, a1side,
         a2side, a1p, a2p, outcome) = row
        mid = int(match_id)
        if mid in seen:
            raise ValueError("duplicate aligned sporting match in shadow")
        seen.add(mid)
        if kind not in VALID_PICKS or outcome not in VALID_RESULTS or side not in (1, 2):
            raise ValueError("invalid canonical aligned match")
        prob = Decimal(probability)
        if not prob.is_finite() or not 0 <= prob <= 1:
            raise ValueError("invalid original point-in-time probability")
        for x in (a1p, a2p):
            if x is not None and (not Decimal(x).is_finite() or not 0 <= Decimal(x) <= 1):
                raise ValueError("invalid agent probability")
        picks.append(ReplayPick(
            mid, day, str(kind), prob, int(side),
            str(surface or "UNKNOWN").upper(), str(outcome),
            int(a1side) if a1side in (1, 2) else None,
            int(a2side) if a2side in (1, 2) else None,
            Decimal(a1p) if a1p is not None else None,
            Decimal(a2p) if a2p is not None else None,
        ))
    return tuple(picks)


def window(as_of: datetime) -> tuple[date, date]:
    if as_of.tzinfo is None:
        raise ValueError("timezone-aware as_of required")
    today = as_of.astimezone(MANILA).date()
    first = max(START_DAY, today - timedelta(days=WINDOW_DAYS))
    return first, first + timedelta(days=WINDOW_DAYS - 1)


def evaluate(rows: tuple[ReplayPick, ...], *, as_of: datetime) -> tuple[QualifiedDay, ...]:
    first, last = window(as_of)
    today = as_of.astimezone(MANILA).date()
    out = []
    for i in range(WINDOW_DAYS):
        day = first + timedelta(days=i)
        picks = tuple(p for p in rows if p.day == day)
        c = _counts(picks, "confidence70")
        out.append(QualifiedDay(
            day, c["original"], c["retained"], c["wins"], c["losses"],
            c["void"], c["pending"], c["avoided_losses"], c["missed_winners"],
            day < today,
        ))
    return tuple(out)


def load(conn: psycopg.Connection, *, as_of: datetime) -> tuple[QualifiedDay, ...]:
    first, last = window(as_of)
    raw = conn.execute(aligned_replay_sql(), (first, last)).fetchall()
    return evaluate(_parse(raw), as_of=as_of)


def render(days: tuple[QualifiedDay, ...]) -> tuple[str, ...]:
    if len(days) != WINDOW_DAYS:
        raise ValueError("shadow requires exactly five calendar days")
    lines = []
    for d in days:
        pct = f"{d.accuracy_pct:.1f}" if d.accuracy_pct is not None else "-"
        coverage = f"{100 * d.retained / d.original:.1f}" if d.original else "-"
        lines.append(
            "QUALIFIED_SHADOW_DAY "
            f"date={d.day.isoformat()} policy={POLICY_ID} research_only=true "
            f"original={d.original} retained={d.retained} coverage_pct={coverage} "
            f"won={d.won} lost={d.lost} void={d.void} pending={d.pending} "
            f"graded={d.graded} win_rate_pct={pct} "
            f"avoided_losses={d.avoided_losses} missed_winners={d.missed_winners} "
            f"status={d.status}"
        )
    won = sum(d.won for d in days)
    lost = sum(d.lost for d in days)
    original = sum(d.original for d in days)
    retained = sum(d.retained for d in days)
    void = sum(d.void for d in days)
    pending = sum(d.pending for d in days)
    pct = f"{100 * won / (won + lost):.1f}" if won + lost else "-"
    passed = sum(d.status == "RESEARCH_MET73" for d in days)
    lines.append(
        "QUALIFIED_SHADOW_TOTAL "
        f"start={days[0].day.isoformat()} end={days[-1].day.isoformat()} "
        f"policy={POLICY_ID} research_only=true live_selection_changed=false "
        f"original={original} retained={retained} won={won} lost={lost} "
        f"void={void} pending={pending} graded={won+lost} "
        f"pooled_win_rate_pct={pct} daily_passed={passed}/5 "
        "acceptance=UNCHANGED_ORIGINAL_ALIGNED_ONLY"
    )
    return tuple(lines)
