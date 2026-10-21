"""Read-only, player-side-specific guide prices for issued Model Picks.

Guide odds are observed OFFERED bookmaker quotes, never the reciprocal of the
model's probability or an odds feed for a different player/market. This query
uses the *same first-qualified production population* as the Model Picks ledger.
The selected snapshot ID and source timestamps are surfaced to the client. This
read-only view is NOT an immutable publication ledger: late-arriving historical
snapshots can change the chosen guide. Never use it to retroactively price a
bet, backtest ROI or claim that a quote was available at issuance.

A quote can arrive after the prediction (model decisions are made before the
OddsPapi fetch). Such quotes are display-only; they are not model inputs and
must not be treated as odds available at the moment the pick was issued.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import (
    ALIGNED_PICK_DATE_FILTER,
    aligned_production_model_picks_cte,
)

# Same issued-at point-in-time fixture version as the aligned Model Picks
# ledger, with the player orientation retained for a fail-closed drift check.
ISSUED_MATCH_VERSION_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT mv0.match_start_at, mv0.player_1_id, mv0.player_2_id
        FROM match_versions mv0
        WHERE mv0.match_id = p.match_id
          AND mv0.as_of <= p.picked_at
          AND mv0.source_observed_at <= p.picked_at
        ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC,
                 mv0.match_version_id DESC
        LIMIT 1
    ) mv ON TRUE
"""


@dataclass(frozen=True)
class GuideOddsQuote:
    match_id: int
    selected_side: int
    decimal_price: Decimal
    bookmaker_code: str
    observed_at: datetime
    odds_snapshot_id: int
    issued_before_quote: bool
    available_at: datetime | None = None


def get_issued_guide_odds(
    conn: psycopg.Connection,
    *,
    decision_date: date,
    timezone_name: str = "UTC",
) -> dict[int, GuideOddsQuote]:
    """Map first-qualified Model Picks to valid, time-bounded offered quotes.

    No synthetic/sharp/unknown-origin record is accepted. For each player, the
    latest qualifying quote available in the 12 hours *before* issue takes
    priority. If none existed, the earliest quote observed before match start
    may be a guide; long-horizon picks must not lose a price merely because the
    bookmaker listed the match more than 24 hours after the pick was issued.
    "Match start" is the schedule version in effect when the quote arrived, so
    a later reschedule does not retroactively make an on-time quote late.
    Prices displayed after issue are never passed off as publication-time bets.

    Exact canonical match_id and PLAYER_1/PLAYER_2 orientation are mandatory;
    missing evidence yields an unavailable price rather than a fallback.
    """
    rows = conn.execute(
        f"""
        WITH {aligned_production_model_picks_cte()}
        SELECT
            p.match_id,
            p.selected_side,
            g.decimal_price,
            g.bookmaker_code,
            g.source_observed_at,
            g.odds_snapshot_id,
            GREATEST(g.as_of, g.ingested_at, g.source_observed_at)
                AS available_at,
            GREATEST(g.as_of, g.ingested_at, g.source_observed_at)
                > p.picked_at AS quote_after_issue
        FROM aligned_model_picks p
        {ISSUED_MATCH_VERSION_LATERAL}
        LEFT JOIN LATERAL (
            SELECT lv.player_1_id, lv.player_2_id
            FROM match_versions lv
            WHERE lv.match_id = p.match_id
            ORDER BY lv.as_of DESC, lv.source_observed_at DESC,
                     lv.match_version_id DESC
            LIMIT 1
        ) latest_mv ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                o.decimal_price, o.bookmaker_code,
                o.source_observed_at, o.odds_snapshot_id,
                o.as_of, o.ingested_at
            FROM odds_snapshots o
            -- A match can be rescheduled after the pick was issued. Judge
            -- "pre-match" against the schedule known when the quote arrived,
            -- not the stale start captured at issue time. A quote that was
            -- already late against the then-current schedule is still rejected
            -- even if a later correction moves the start further out.
            JOIN LATERAL (
                SELECT mvq.match_start_at
                FROM match_versions mvq
                WHERE mvq.match_id = p.match_id
                  AND mvq.as_of <= GREATEST(
                      o.as_of, o.ingested_at, o.source_observed_at)
                  AND mvq.source_observed_at <= GREATEST(
                      o.as_of, o.ingested_at, o.source_observed_at)
                  AND mvq.ingested_at <= GREATEST(
                      o.as_of, o.ingested_at, o.source_observed_at)
                ORDER BY mvq.as_of DESC, mvq.source_observed_at DESC,
                         mvq.match_version_id DESC
                LIMIT 1
            ) quote_mv ON TRUE
            WHERE o.match_id = p.match_id
              AND o.source_system = 'OFFERED'
              -- Only the three documented OddsPapi offered bookmakers.
              AND o.bookmaker_code IN ('BET365', 'UNIBET', '1XBET')
              AND o.odds_origin = 'LIVE_PROVIDER'
              AND o.market_type = 'MATCH_WINNER'
              AND o.market_status = 'OPEN'
              AND o.selection_type::text =
                  CASE WHEN p.selected_side = 1 THEN 'PLAYER_1'
                       WHEN p.selected_side = 2 THEN 'PLAYER_2'
                       ELSE 'INVALID' END
              AND p.picked_at < mv.match_start_at
              -- Refuse to attach an old PLAYER_1/PLAYER_2 quote to a
              -- currently-rendered player if provider orientation changed.
              AND mv.player_1_id = latest_mv.player_1_id
              AND mv.player_2_id = latest_mv.player_2_id
              AND o.source_observed_at <= CURRENT_TIMESTAMP
              AND o.as_of <= CURRENT_TIMESTAMP
              AND o.ingested_at <= CURRENT_TIMESTAMP
              -- The quote cannot be treated as valid before the application
              -- received it, even if the provider timestamps it earlier.
              -- Use the reschedule-aware start captured above.
              AND GREATEST(o.as_of, o.ingested_at, o.source_observed_at)
                  <= quote_mv.match_start_at
              AND o.source_observed_at >= p.picked_at - INTERVAL '12 hours'
            ORDER BY
              CASE WHEN GREATEST(o.as_of, o.ingested_at, o.source_observed_at)
                        <= p.picked_at THEN 0 ELSE 1 END,
              CASE WHEN GREATEST(o.as_of, o.ingested_at, o.source_observed_at)
                        <= p.picked_at
                   THEN GREATEST(o.as_of, o.ingested_at, o.source_observed_at)
                   END DESC NULLS LAST,
              CASE WHEN GREATEST(o.as_of, o.ingested_at, o.source_observed_at)
                        > p.picked_at
                   THEN GREATEST(o.as_of, o.ingested_at, o.source_observed_at)
                   END ASC NULLS LAST,
              CASE o.bookmaker_code
                  WHEN 'BET365' THEN 0 WHEN 'UNIBET' THEN 1
                  WHEN '1XBET' THEN 2 ELSE 3 END,
              o.odds_snapshot_id ASC
            LIMIT 1
        ) g ON TRUE
        WHERE {ALIGNED_PICK_DATE_FILTER}
        ORDER BY p.match_id
        """,
        (timezone_name, timezone_name, decision_date),
    ).fetchall()

    guide: dict[int, GuideOddsQuote] = {}
    for row in rows:
        match_id, selected_side, price, book, observed, snapshot_id, available_at, after = row
        if (
            selected_side not in (1, 2)
            or price is None
            or Decimal(price) <= 1
            or not book
            or observed is None
            or snapshot_id is None
        ):
            continue
        guide[int(match_id)] = GuideOddsQuote(
            match_id=int(match_id),
            selected_side=int(selected_side),
            decimal_price=Decimal(price),
            bookmaker_code=str(book),
            observed_at=observed,
            odds_snapshot_id=int(snapshot_id),
            issued_before_quote=bool(after),
            available_at=available_at,
        )
    return guide


__all__ = ["GuideOddsQuote", "get_issued_guide_odds"]
