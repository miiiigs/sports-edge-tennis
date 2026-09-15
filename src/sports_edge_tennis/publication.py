from __future__ import annotations

from datetime import datetime

import psycopg


class PublicationError(RuntimeError):
    """Raised when a decision cannot cross the client publication boundary safely."""


def publish_new_bet_decision(
    conn: psycopg.Connection,
    *,
    decision_id: int,
    published_at: datetime,
) -> datetime:
    """Stamp a newly persisted final BET as client-published exactly once.

    Publication is intentionally narrower than decision persistence. Only a
    decision that has already survived Market and the kill-only Veto may cross
    this boundary. The database's append-only guard permits only the existing
    NULL -> timestamp transition for ``published_at``.

    This helper never converts NO_BET/VETOED rows, never rewrites an existing
    publication timestamp, and never backfills historical rows implicitly.
    """
    if published_at.tzinfo is None:
        raise PublicationError("published_at must be timezone-aware")

    row = conn.execute(
        """
        UPDATE decisions
        SET published_at = %s
        WHERE decision_id = %s
          AND decision_status = 'BET'
          AND published_at IS NULL
          AND as_of <= %s
        RETURNING published_at
        """,
        (published_at, decision_id, published_at),
    ).fetchone()
    if row is not None:
        return row[0]

    state = conn.execute(
        """
        SELECT decision_status::text, published_at, as_of
        FROM decisions
        WHERE decision_id = %s
        """,
        (decision_id,),
    ).fetchone()
    if state is None:
        raise PublicationError("decision does not exist")

    status, existing_published_at, decision_as_of = state
    if status != "BET":
        raise PublicationError("only final BET decisions can be published")
    if existing_published_at is not None:
        # Safe no-op. The row already crossed the one-way publication boundary;
        # do not rewrite its historical timestamp.
        return existing_published_at
    if decision_as_of > published_at:
        raise PublicationError("published_at cannot precede decision as_of")
    raise PublicationError("BET decision could not be published")


__all__ = ["PublicationError", "publish_new_bet_decision"]
