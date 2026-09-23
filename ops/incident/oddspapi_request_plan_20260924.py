from __future__ import annotations

"""Read-only OddsPapi request-plan / economics proof.

Reads the current production Manager state, computes the exact OddsPapi request
plan for Manager-eligible matches, and reports projections. Makes NO OddsPapi or
other provider calls and writes nothing.
"""

import json
import os

import psycopg

from sports_edge_tennis.live.oddspapi import ODDSPAPI_PLAN_BOOKMAKERS, ODDSPAPI_SHARP_BOOKMAKER
from sports_edge_tennis.live.oddspapi_budget import (
    OddspapiBudgetPolicy,
    enforce_oddspapi_budget,
    plan_oddspapi_qualification,
)

RUN_IDS = (481, 482, 483)


def _load_manager_state(conn) -> tuple[int, int, list[int]]:
    rows = conn.execute(
        """
        SELECT
            count(*) FILTER (WHERE manager_status = 'CONSENSUS') AS consensus,
            count(*) FILTER (WHERE manager_status = 'NO_BET') AS no_bet
        FROM manager_consensus_results
        WHERE prediction_run_id = ANY(%s)
        """,
        (list(RUN_IDS),),
    ).fetchone()
    consensus = int(rows[0] or 0)
    no_bet = int(rows[1] or 0)
    tournament_ids: list[int] = []
    if consensus:
        tournament_ids = [
            int(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT te.tournament_id
                FROM manager_consensus_results mcr
                JOIN matches m ON m.match_id = mcr.match_id
                JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
                WHERE mcr.prediction_run_id = ANY(%s)
                  AND mcr.manager_status = 'CONSENSUS'
                """,
                (list(RUN_IDS),),
            ).fetchall()
        ]
    return consensus, no_bet, tournament_ids


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("ODDSPAPI_PLAN_BLOCKED missing_dsn")
        return 2
    policy = OddspapiBudgetPolicy()
    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower()
        consensus, no_bet, tournament_ids = _load_manager_state(conn)
        conn.rollback()

    scenarios = {}
    for label, tids in (
        ("zero_consensus", ()),
        ("one_match_one_tournament", tournament_ids[:1]),
        ("moderate_five_tournaments", tournament_ids[:5]),
    ):
        plan = plan_oddspapi_qualification(
            tournament_ids=tids,
            books=ODDSPAPI_PLAN_BOOKMAKERS,
            discovery_book=ODDSPAPI_SHARP_BOOKMAKER,
            policy=policy,
        )
        budget_ok = True
        try:
            enforce_oddspapi_budget(plan, policy=policy)
        except Exception:
            budget_ok = False
        scenarios[label] = {
            "tournaments": len(plan.tournament_ids),
            "discovery_requests": plan.discovery_requests,
            "odds_requests": plan.odds_requests,
            "total_requests": plan.total_requests,
            "projected_monthly": policy.projected_monthly(plan.total_requests),
            "within_cycle_limit": budget_ok,
        }

    print(
        "ODDSPAPI_REQUEST_PLAN "
        + json.dumps(
            {
                "transaction_read_only": read_only,
                "writes": 0,
                "provider_calls": 0,
                "manager_consensus": consensus,
                "manager_no_bet": no_bet,
                "consensus_tournaments": len(tournament_ids),
                "policy": {
                    "monthly_limit": policy.monthly_limit,
                    "reserve": policy.reserve,
                    "usable_monthly": policy.usable_monthly,
                    "per_cycle_limit": policy.per_cycle_limit,
                    "tournaments_per_call": policy.tournaments_per_call,
                },
                "scenarios": scenarios,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
