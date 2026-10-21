"""P0 operator-only, read-only Guide Odds diagnosis for first-qualified Manager picks.

Run inside a pre-authorized environment with a SELECT-only production DSN:
  python -m ops.tennis_p0_zero_guide_odds_audit --start 2026-10-19 --end 2026-10-21

No provider requests, login scraping, migrations, or betting actions. The script
never prints the DSN. Historical quotes are DISPLAY-only, never issue-time bets.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from sports_edge_tennis.dashboard.aligned_model_picks import (
    MANAGER_CONSENSUS_PICK,
    load_aligned_day_picks,
)
from sports_edge_tennis.dashboard.guide_odds import get_issued_guide_odds

BOOKMAKERS = {"BET365", "UNIBET", "1XBET"}
ODDS_SQL = """
    SELECT o.bookmaker_code, o.source_system::text AS source_system,
           o.odds_origin::text AS odds_origin, o.market_type::text AS market_type,
           o.market_status::text AS market_status,
           o.selection_type::text AS selection_type, o.decimal_price,
           o.source_observed_at, o.as_of, o.ingested_at
    FROM odds_snapshots o
    WHERE o.match_id = %s AND o.source_system = 'OFFERED'
    ORDER BY o.source_observed_at DESC, o.odds_snapshot_id DESC
    LIMIT 3000
"""
IDENTITY_SQL = """
    SELECT (pit.player_1_id = latest.player_1_id
            AND pit.player_2_id = latest.player_2_id) AS same_orientation
    FROM (SELECT player_1_id, player_2_id FROM match_versions
          WHERE match_id = %s AND as_of <= %s AND source_observed_at <= %s
          ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC LIMIT 1) pit
    CROSS JOIN (SELECT player_1_id, player_2_id FROM match_versions
                WHERE match_id = %s ORDER BY as_of DESC, source_observed_at DESC,
                match_version_id DESC LIMIT 1) latest
"""


def classify_missing(
    quotes: list[dict],
    *,
    side: int,
    picked_at: datetime,
    match_start_at: datetime | None,
    same_orientation: bool | None,
) -> str:
    """Return one evidence-based exclusion, not a guess about the provider.

    The first missing cause is explanatory only. Other constraints may also fail.
    """
    if not quotes:
        return "NO_STORED_OFFERED_QUOTE_FOR_MATCH"
    if match_start_at is None or picked_at >= match_start_at:
        return "NO_VALID_PREMATCH_ISSUANCE"
    if same_orientation is not True:
        return "PLAYER_ORIENTATION_NOT_VERIFIED"
    supported = [q for q in quotes if q["bookmaker_code"] in BOOKMAKERS]
    if not supported:
        return "NO_SUPPORTED_BOOKMAKER_QUOTE"
    winner = [q for q in supported if q["odds_origin"] == "LIVE_PROVIDER"
              and q["market_type"] == "MATCH_WINNER"
              and q["selection_type"] == f"PLAYER_{side}"]
    if not winner:
        return "NO_LIVE_MATCH_WINNER_QUOTE_ON_PICKED_SIDE"
    prices = [q for q in winner if q["decimal_price"] is not None
              and Decimal(q["decimal_price"]) > 1
              and q["market_status"] == "OPEN"]
    if not prices:
        return "NO_OPEN_VALID_PRICE"
    clock_ok = [q for q in prices if all(
        isinstance(q.get(key), datetime)
        for key in ("source_observed_at", "as_of", "ingested_at")
    )]
    if not clock_ok:
        return "MISSING_SOURCE_AVAILABILITY_TIMESTAMPS"
    before_start = [q for q in clock_ok if max(
        q["source_observed_at"], q["as_of"], q["ingested_at"]
    ) <= match_start_at]
    if not before_start:
        return "PRICE_FIRST_AVAILABLE_AFTER_MATCH_START"
    in_window = [q for q in before_start
                 if q["source_observed_at"] >= picked_at - timedelta(hours=12)]
    if not in_window:
        return "QUOTE_OBSERVED_BEFORE_12H_PRE_ISSUE_WINDOW"
    return "ELIGIBLE_QUOTE_BUT_DASHBOARD_READER_MISSED"


RESULT_SQL = """
    SELECT outcome_status::text AS outcome_status, winner_side,
           source_observed_at, as_of
    FROM match_result_observations
    WHERE match_id = %s
      AND outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
    ORDER BY as_of DESC, source_observed_at DESC,
             match_result_observation_id DESC
"""


def completion_result(observations: list[dict], side: int) -> str:
    """Score only authoritative, unambiguous COMPLETED sporting results."""
    if not observations:
        return "PENDING_OR_UNVERIFIED"
    unique = {(o["outcome_status"], o["winner_side"]) for o in observations}
    if len(unique) != 1:
        return "CONFLICTING_TERMINAL_OBSERVATIONS"
    status, winner = next(iter(unique))
    if status != "COMPLETED" or winner not in (1, 2):
        return "NOT_COMPARABLE_OR_VOID"
    return "WON" if winner == side else "LOST"




def _identity(conn, match_id: int, picked_at: datetime) -> bool | None:
    row = conn.execute(
        IDENTITY_SQL, (match_id, picked_at, picked_at, match_id)
    ).fetchone()
    return None if row is None else row["same_orientation"]


def day_report(conn, day: date, timezone: str = "Asia/Manila") -> dict:
    # Reuse the dashboard's official FIRST-QUALIFIED query, not PR247's
    # first-CONSENSUS definition. Never include A1/A2 tracking as Manager.
    picks = [
        p for p in load_aligned_day_picks(
            conn, decision_date=day, timezone_name=timezone
        ) if p.aligned_pick_type == MANAGER_CONSENSUS_PICK
    ]
    priced = get_issued_guide_odds(
        conn, decision_date=day, timezone_name=timezone
    )
    causes: Counter[str] = Counter()
    results: Counter[str] = Counter()
    details: list[dict] = []
    for pick in picks:
        price = priced.get(pick.match_id)
        if price is not None and price.selected_side == pick.selected_side:
            cause = "PRICED"
        else:
            quotes = conn.execute(ODDS_SQL, (pick.match_id,)).fetchall()
            cause = classify_missing(
                quotes, side=pick.selected_side, picked_at=pick.picked_at,
                match_start_at=pick.match_start_at,
                same_orientation=_identity(conn, pick.match_id, pick.picked_at),
            )
        causes[cause] += 1
        # PR244 removes production's hard 24-hour post-issue ceiling.
        # Flag when the candidate's chosen quote itself exceeds that ceiling.
        # It does not prove no other earlier eligible quote could exist.
        selected_quote_exceeds_deployed_24h = (
            cause == "PRICED"
            and (
                price.observed_at > pick.picked_at + timedelta(hours=24)
                or (
                    price.available_at is not None
                    and price.available_at > pick.picked_at + timedelta(hours=24)
                )
            )
        )
        if selected_quote_exceeds_deployed_24h:
            causes["CANDIDATE_SELECTED_QUOTE_EXCEEDS_DEPLOYED_24H_GATE"] += 1
        result = completion_result(
            conn.execute(RESULT_SQL, (pick.match_id,)).fetchall(),
            pick.selected_side,
        )
        results[result] += 1
        details.append({
            "result": result,
            "candidate_selected_quote_exceeds_deployed_24h_gate": selected_quote_exceeds_deployed_24h,
            "match_id": pick.match_id,
            "first_qualified_at": pick.picked_at.isoformat(),
            "scheduled_start_at": (pick.match_start_at.isoformat()
                                   if pick.match_start_at else None),
            "selected_side": pick.selected_side,
            "model_probability": str(pick.aligned_probability),
            "cause": cause,
            "bookmaker": price.bookmaker_code if cause == "PRICED" else None,
            "decimal_price": str(price.decimal_price) if cause == "PRICED" else None,
            "quote_observed_at": (price.observed_at.isoformat()
                                  if cause == "PRICED" else None),
            "quote_available_at": (price.available_at.isoformat()
                                   if cause == "PRICED" and price.available_at else None),
            "guide_after_issue": price.issued_before_quote if cause == "PRICED" else None,
        })
    return {
        "date_ph": day.isoformat(),
        "genuine_manager_first_qualified": len(picks),
        "priced": causes["PRICED"],
        "unpriced": len(picks) - causes["PRICED"],
        "reason_counts": dict(sorted(causes.items())),
        "scored_completed": results["WON"] + results["LOST"],
        "wins": results["WON"],
        "losses": results["LOST"],
        "result_states": dict(sorted(results.items())),
        "observed_accuracy_completed_only": (
            results["WON"] / (results["WON"] + results["LOST"])
            if results["WON"] + results["LOST"] else None
        ),
        "picks": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    if args.end < args.start or (args.end - args.start).days > 14:
        parser.error("choose an inclusive window no longer than 15 days")
    dsn = os.getenv("TENNIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        parser.error("run in an authorized environment with a SELECT-only DSN")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            day = args.start
            results = []
            while day <= args.end:
                results.append(day_report(conn, day))
                day += timedelta(days=1)
            print(json.dumps({
                "method": "canonical-first-qualified-Manager-only",
                "timezone": "Asia/Manila",
                "source": "read-only production persisted quotes; no provider calls",
                "accuracy_scope": "first-qualified Manager matching local match date; COMPLETED only; excludes tracking/void/pending; not independently verified publication",
                "days": results,
            }, indent=2))
            # SELECT-only by transaction contract even if caller supplied a
            # broader credential. No write or provider code paths exist.


if __name__ == "__main__":
    main()
