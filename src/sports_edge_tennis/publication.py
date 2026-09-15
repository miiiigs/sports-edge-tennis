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

    Publication is intentionally narrower than decision persistence. Market has
    already validated the offered price and source freshness at ``decision.as_of``.
    This boundary therefore checks only new observations that appeared after
    generation and before publication. If the selected offered market closes or
    the price drifts below the stored ``min_acceptable_odds`` in that interval,
    publication fails closed.

    This preserves point-in-time historical replay semantics: a replay executed
    later in wall-clock time is not rejected merely because its already-validated
    historical quote is old. A genuinely newer observation in the replay/live
    publication interval is still authoritative.

    This helper never converts NO_BET/VETOED rows, never rewrites an existing
    publication timestamp, and never backfills historical rows implicitly.
    """
    if published_at.tzinfo is None:
        raise PublicationError("published_at must be timezone-aware")

    state = conn.execute(
        """
        SELECT
            decision_status::text,
            published_at,
            as_of,
            match_id,
            selection_type::text,
            min_acceptable_odds,
            price_source_system,
            price_bookmaker_code
        FROM decisions
        WHERE decision_id = %s
        """,
        (decision_id,),
    ).fetchone()
    if state is None:
        raise PublicationError("decision does not exist")

    (
        status,
        existing_published_at,
        decision_as_of,
        match_id,
        selection_type,
        min_acceptable_odds,
        price_source_system,
        price_bookmaker_code,
    ) = state

    if status != "BET":
        raise PublicationError("only final BET decisions can be published")
    if existing_published_at is not None:
        # Safe no-op. The row already crossed the one-way publication boundary;
        # later market movement must not rewrite historical publication truth.
        return existing_published_at
    if decision_as_of > published_at:
        raise PublicationError("published_at cannot precede decision as_of")
    if (
        selection_type is None
        or min_acceptable_odds is None
        or price_source_system is None
        or price_bookmaker_code is None
    ):
        raise PublicationError("BET decision is missing publication price lineage")

    # Market already validated all snapshots visible at decision_as_of. Only a
    # later observation can change publication eligibility. Using this interval
    # rather than wall-clock freshness keeps historical PIT replays deterministic
    # while still killing live/replay bets whose price moves before publication.
    newer_offered = conn.execute(
        """
        SELECT decimal_price, market_status::text
        FROM odds_snapshots
        WHERE match_id = %s
          AND market_type = 'MATCH_WINNER'
          AND selection_type::text = %s
          AND source_system = %s
          AND bookmaker_code = %s
          AND source_observed_at > %s
          AND source_observed_at <= %s
          AND as_of > %s
          AND as_of <= %s
        ORDER BY source_observed_at DESC, as_of DESC, odds_snapshot_id DESC
        LIMIT 1
        """,
        (
            match_id,
            selection_type,
            price_source_system,
            price_bookmaker_code,
            decision_as_of,
            published_at,
            decision_as_of,
            published_at,
        ),
    ).fetchone()

    if newer_offered is not None:
        decimal_price, market_status = newer_offered
        if market_status != "OPEN":
            raise PublicationError("offered market closed before publication")
        if decimal_price < min_acceptable_odds:
            raise PublicationError("offered price is below min_acceptable_odds at publication")

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

    # The row was validated above, so reaching this point means the publication
    # transition was lost or blocked unexpectedly. Fail closed rather than
    # reporting a published BET without durable evidence.
    raise PublicationError("BET decision could not be published")


__all__ = ["PublicationError", "publish_new_bet_decision"]
