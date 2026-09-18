from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg


HALT_SOURCES = frozenset({"OPERATOR", "CLV_STOP", "DRAWDOWN_STOP", "SYSTEM"})


@dataclass(frozen=True)
class PublicationControlState:
    enabled: bool
    action: str | None
    source: str | None
    reason: str | None
    actor: str | None
    created_at: datetime | None


def load_publication_control_state(
    conn: psycopg.Connection,
) -> PublicationControlState:
    row = conn.execute(
        """
        SELECT action::text, source::text, reason, actor, created_at
        FROM publication_control_events
        ORDER BY created_at DESC, publication_control_event_id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return PublicationControlState(
            enabled=True,
            action=None,
            source=None,
            reason=None,
            actor=None,
            created_at=None,
        )
    action, source, reason, actor, created_at = row
    return PublicationControlState(
        enabled=action == "ENABLE",
        action=str(action),
        source=str(source),
        reason=str(reason),
        actor=str(actor) if actor is not None else None,
        created_at=created_at,
    )


def halt_publication(
    conn: psycopg.Connection,
    *,
    source: str,
    reason: str,
    actor: str | None = None,
    created_at: datetime | None = None,
) -> PublicationControlState:
    normalized_source = source.strip().upper()
    normalized_reason = reason.strip()
    normalized_actor = actor.strip() if actor is not None else None
    if normalized_source not in HALT_SOURCES:
        raise ValueError(f"unsupported publication halt source: {source!r}")
    if not normalized_reason:
        raise ValueError("publication halt reason is required")
    if normalized_source == "OPERATOR" and not normalized_actor:
        raise ValueError("operator publication halt requires actor")
    event_at = created_at or datetime.now(timezone.utc)
    if event_at.tzinfo is None:
        raise ValueError("publication control created_at must be timezone-aware")

    conn.execute(
        """
        INSERT INTO publication_control_events (
            action, source, reason, actor, created_at
        )
        VALUES ('HALT', %s, %s, %s, %s)
        """,
        (normalized_source, normalized_reason, normalized_actor, event_at),
    )
    return load_publication_control_state(conn)


def enable_publication(
    conn: psycopg.Connection,
    *,
    actor: str,
    reason: str,
    created_at: datetime | None = None,
) -> PublicationControlState:
    normalized_actor = actor.strip()
    normalized_reason = reason.strip()
    if not normalized_actor:
        raise ValueError("publication enable requires actor")
    if not normalized_reason:
        raise ValueError("publication enable reason is required")
    event_at = created_at or datetime.now(timezone.utc)
    if event_at.tzinfo is None:
        raise ValueError("publication control created_at must be timezone-aware")

    conn.execute(
        """
        INSERT INTO publication_control_events (
            action, source, reason, actor, created_at
        )
        VALUES ('ENABLE', 'OPERATOR', %s, %s, %s)
        """,
        (normalized_reason, normalized_actor, event_at),
    )
    return load_publication_control_state(conn)


__all__ = [
    "HALT_SOURCES",
    "PublicationControlState",
    "enable_publication",
    "halt_publication",
    "load_publication_control_state",
]
