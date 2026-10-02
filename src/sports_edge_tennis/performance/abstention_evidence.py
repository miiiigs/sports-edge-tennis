"""Read-only abstention-reason diagnostics for predictive agents.

Migration ``0017_prediction_abstention_reasons`` begins persisting
``predictions.abstain_reason_code``. This module aggregates participation and
abstention reasons over rolling windows so the unresolved A2/A3 coverage
questions can be measured prospectively.

Rows written before 0017 have ``abstain_reason_code IS NULL``; they are reported
under the explicit ``UNKNOWN_NOT_PERSISTED`` bucket. No historical reason is
inferred. This module only reads; it never changes prediction side/probability
or any model, Manager, Market, Veto, or staking behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg

#: Explicit bucket for pre-0017 abstained rows whose reason was not persisted.
UNKNOWN_REASON_BUCKET = "UNKNOWN_NOT_PERSISTED"

_PRODUCTION_PREDICATE = """
    pr.prediction_contract_version = 'predictive_agent_result_v1'
    AND pr.run_status = 'COMPLETED'
    AND pr.prediction_finalized_at IS NOT NULL
    AND pr.experiment_id IS NULL
    AND p.experiment_id IS NULL
"""

_PARTICIPATION_SQL = f"""
    SELECT
        p.agent_code::text AS agent_code,
        count(*)::int AS evaluations,
        count(*) FILTER (WHERE p.prediction_status = 'PREDICTED')::int AS predicted,
        count(*) FILTER (WHERE p.prediction_status = 'ABSTAINED')::int AS abstained
    FROM predictions p
    JOIN prediction_runs pr ON pr.prediction_run_id = p.prediction_run_id
    WHERE {_PRODUCTION_PREDICATE}
      AND p.as_of >= %s AND p.as_of < %s
    GROUP BY 1
    ORDER BY 1
"""

_REASON_SQL = f"""
    SELECT
        p.agent_code::text AS agent_code,
        COALESCE(p.abstain_reason_code, '{UNKNOWN_REASON_BUCKET}') AS reason_code,
        count(*)::int AS n
    FROM predictions p
    JOIN prediction_runs pr ON pr.prediction_run_id = p.prediction_run_id
    WHERE {_PRODUCTION_PREDICATE}
      AND p.prediction_status = 'ABSTAINED'
      AND p.as_of >= %s AND p.as_of < %s
    GROUP BY 1, 2
    ORDER BY 1, 3 DESC, 2
"""


@dataclass(frozen=True)
class AbstainReasonShare:
    reason_code: str
    count: int
    share: float | None


@dataclass(frozen=True)
class AgentAbstentionSummary:
    agent_code: str
    evaluations: int
    predicted: int
    abstained: int
    predicted_rate: float | None
    abstained_rate: float | None
    unknown_reason_count: int
    reasons: tuple[AbstainReasonShare, ...]


@dataclass(frozen=True)
class AbstentionWindow:
    window: str
    window_start: datetime
    window_end: datetime
    agents: tuple[AgentAbstentionSummary, ...]


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _default_windows(now: datetime) -> tuple[tuple[str, datetime, datetime], ...]:
    start_of_day = datetime(now.year, now.month, now.day, tzinfo=UTC)
    return (
        ("TODAY", start_of_day, now),
        ("LAST_7_DAYS", now - timedelta(days=7), now),
        ("LAST_30_DAYS", now - timedelta(days=30), now),
    )


def load_abstention_window(
    conn: psycopg.Connection,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[AgentAbstentionSummary, ...]:
    """Participation and reason shares for one half-open as-of window."""
    participation = {
        str(row[0]): (int(row[1]), int(row[2]), int(row[3]))
        for row in conn.execute(_PARTICIPATION_SQL, (window_start, window_end)).fetchall()
    }
    reasons: dict[str, list[tuple[str, int]]] = {}
    unknown: dict[str, int] = {}
    for row in conn.execute(_REASON_SQL, (window_start, window_end)).fetchall():
        agent_code = str(row[0])
        reason_code = str(row[1])
        count = int(row[2])
        if reason_code == UNKNOWN_REASON_BUCKET:
            unknown[agent_code] = unknown.get(agent_code, 0) + count
        reasons.setdefault(agent_code, []).append((reason_code, count))

    agents: list[AgentAbstentionSummary] = []
    for agent_code in sorted(participation):
        evaluations, predicted, abstained = participation[agent_code]
        reason_shares = tuple(
            AbstainReasonShare(
                reason_code=reason_code,
                count=count,
                share=_ratio(count, abstained),
            )
            for reason_code, count in reasons.get(agent_code, ())
        )
        agents.append(
            AgentAbstentionSummary(
                agent_code=agent_code,
                evaluations=evaluations,
                predicted=predicted,
                abstained=abstained,
                predicted_rate=_ratio(predicted, evaluations),
                abstained_rate=_ratio(abstained, evaluations),
                unknown_reason_count=unknown.get(agent_code, 0),
                reasons=reason_shares,
            )
        )
    return tuple(agents)


def load_abstention_windows(
    conn: psycopg.Connection,
    *,
    now: datetime | None = None,
) -> tuple[AbstentionWindow, ...]:
    """Today / last-7-day / last-30-day abstention diagnostics in UTC."""
    reference = now or datetime.now(tz=UTC)
    return tuple(
        AbstentionWindow(
            window=label,
            window_start=start,
            window_end=end,
            agents=load_abstention_window(conn, window_start=start, window_end=end),
        )
        for label, start, end in _default_windows(reference)
    )


__all__ = [
    "AgentAbstentionSummary",
    "AbstainReasonShare",
    "AbstentionWindow",
    "UNKNOWN_REASON_BUCKET",
    "load_abstention_window",
    "load_abstention_windows",
]
