from __future__ import annotations

"""Offline replay of the match 130870 warm-cache pattern.

No database and no provider calls. Uses the persisted 130870 bookmaker prices
and the real OddsPapi observation adapter to show that qualified board odds are
retained when discovery is skipped for an already-cached identity. Prints
sanitized diagnostics only.
"""

import json
from datetime import UTC, datetime

from sports_edge_tennis.live.oddspapi import (
    ODDSPAPI_PLAN_BOOKMAKERS,
    merge_oddspapi_odds_books,
)
from sports_edge_tennis.market.policy import is_fresh
from sports_edge_tennis.odds.providers.oddspapi_tennis import (
    build_oddspapi_tennis_observation,
)

FIXTURE_ID = "id1203477074753146"
CYCLE_1_RECEIVED_AT = datetime(2026, 9, 24, 8, 7, 40, 587438, tzinfo=UTC)
CYCLE_2_RECEIVED_AT = datetime(2026, 9, 24, 9, 6, 56, 984671, tzinfo=UTC)
TTL_MINUTES = 15
PRICES = {
    "pinnacle+30": (2.02, 1.854),
    "1xbet": (1.89, 1.79),
    "unibet": (1.90, 1.86),
    "bet365": (1.87, 1.77),
}


def _book(price_1: float, price_2: float) -> dict:
    return {
        "suspended": False,
        "markets": {
            "121": {
                "marketActive": True,
                "outcomes": {
                    "121": {"players": {"0": {"active": True, "price": price_1}}},
                    "122": {"players": {"0": {"active": True, "price": price_2}}},
                },
            }
        },
    }


def _row() -> dict:
    return {
        "fixtureId": FIXTURE_ID,
        "sportId": 12,
        "tournamentId": 34770,
        "participant1Id": 145122,
        "participant1Name": "M. Martineau",
        "participant2Id": 53483,
        "participant2Name": "D. Masur",
        "startTime": "2026-09-24T09:40:00.000Z",
        "externalProviders": {"betradarId": 74753146},
        "bookmakerOdds": {slug: _book(*prices) for slug, prices in PRICES.items()},
    }


def _describe(label: str, rows: list[dict], received_at: datetime) -> None:
    for row in rows:
        for bookmaker in ODDSPAPI_PLAN_BOOKMAKERS:
            observation = build_oddspapi_tennis_observation(
                event_payload=row,
                received_at=received_at,
                response_id=f"replay:{label}:BOOK:{bookmaker}",
                allowed_bookmakers=(bookmaker,),
            )
            if not observation.markets:
                continue
            age = int((received_at - observation.observation_time).total_seconds())
            print(
                "WARM_CACHE_REPLAY "
                + json.dumps(
                    {
                        "cycle": label,
                        "bookmaker": bookmaker,
                        "fixture_id": observation.provider_event_id,
                        "observation_time": observation.observation_time.isoformat(),
                        "received_at": received_at.isoformat(),
                        "market_age_seconds": age,
                        "fresh_within_ttl": is_fresh(
                            observation.observation_time, received_at, TTL_MINUTES
                        ),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )


def main() -> int:
    row = _row()

    cycle_1 = merge_oddspapi_odds_books([row], [row])
    print(
        "WARM_CACHE_REPLAY "
        + json.dumps({"cycle": "1_cold_discovery", "merged_rows": len(cycle_1)}, sort_keys=True)
    )
    _describe("1_cold_discovery", cycle_1, CYCLE_1_RECEIVED_AT)

    cycle_2_before = merge_oddspapi_odds_books([], [row])
    print(
        "WARM_CACHE_REPLAY "
        + json.dumps(
            {
                "cycle": "2_warm_before_fix",
                "merged_rows": len(cycle_2_before),
                "note": "discovery skipped; board rows discarded",
            },
            sort_keys=True,
        )
    )
    _describe("2_warm_before_fix", cycle_2_before, CYCLE_2_RECEIVED_AT)

    cycle_2_after = merge_oddspapi_odds_books(
        [], [row], trusted_fixture_ids=(FIXTURE_ID,)
    )
    print(
        "WARM_CACHE_REPLAY "
        + json.dumps(
            {
                "cycle": "2_warm_after_fix",
                "merged_rows": len(cycle_2_after),
                "note": "discovery skipped; trusted cached identity retains board rows",
            },
            sort_keys=True,
        )
    )
    _describe("2_warm_after_fix", cycle_2_after, CYCLE_2_RECEIVED_AT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
