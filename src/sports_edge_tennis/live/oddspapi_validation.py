from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.oddspapi import (
    ODDSPAPI_PLAN_BOOKMAKERS,
    ODDSPAPI_SHARP_BOOKMAKER,
    OddspapiRequestBudget,
    fetch_oddspapi_account,
    fetch_oddspapi_tennis_fixtures,
    fetch_oddspapi_tennis_odds_by_tournaments,
    merge_oddspapi_fixture_odds,
    parse_oddspapi_tennis_fixture,
)
from sports_edge_tennis.live.transport import TransportError
from sports_edge_tennis.odds.providers.oddspapi_tennis import (
    ODDSPAPI_MATCH_WINNER_MARKET_ID,
    ODDSPAPI_PLAYER_1_OUTCOME_ID,
    ODDSPAPI_PLAYER_2_OUTCOME_ID,
    extract_oddspapi_match_winner_prices,
)


def _today_utc() -> date:
    return datetime.now(UTC).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only OddsPapi account validation probe")
    parser.add_argument("--start-date", type=date.fromisoformat, default=_today_utc())
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--account-only", action="store_true")
    parser.add_argument("--fixtures-only", action="store_true")
    args = parser.parse_args(argv)

    if args.days < 1 or args.days > 2:
        print("ODDSPAPI_VALIDATION_BLOCKED invalid_days", flush=True)
        return 2

    api_key = load_secret("ODDSPAPI_API_KEY")
    if not api_key:
        print("ODDSPAPI_VALIDATION_BLOCKED missing_api_key", flush=True)
        return 2

    account_before = fetch_oddspapi_account(api_key)
    if args.account_only:
        print(
            "ODDSPAPI_ACCOUNT "
            + json.dumps(
                {
                    "account_request_limit": account_before.request_limit,
                    "account_request_count": account_before.request_count,
                    "account_remaining": account_before.remaining,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    budget = _budget_from_account(account_before, reserve=int(os.environ.get("ODDSPAPI_MONTHLY_RESERVE", "500")))
    window_start = datetime.combine(args.start_date, datetime.min.time(), tzinfo=UTC)
    window_end = window_start + timedelta(days=args.days)

    billable_requests = 0
    fixture_received_at, fixtures = fetch_oddspapi_tennis_fixtures(
        api_key,
        from_at=window_start,
        to_at=window_end,
        bookmakers=(ODDSPAPI_SHARP_BOOKMAKER,),
        budget=budget,
    )
    billable_requests += 1

    valid_fixtures = []
    parse_failures = 0
    tournament_ids: list[int] = []
    bookmaker_fixture_hits = {slug: 0 for slug in ODDSPAPI_PLAN_BOOKMAKERS}
    for payload in fixtures:
        try:
            envelope = parse_oddspapi_tennis_fixture(payload)
        except ValueError:
            parse_failures += 1
            continue
        valid_fixtures.append(payload)
        tournament_ids.append(envelope.tournament_id)
        books = payload.get("bookmakerOdds")
        if isinstance(books, Mapping):
            for slug in ODDSPAPI_PLAN_BOOKMAKERS:
                if slug in books:
                    bookmaker_fixture_hits[slug] += 1

    odds_rows: list[dict[str, Any]] = []
    match_winner_books = {slug: 0 for slug in ODDSPAPI_PLAN_BOOKMAKERS}
    observed_market_ids: dict[str, int] = {}
    observed_outcome_ids: dict[str, int] = {}
    odds_error_kind = None
    if tournament_ids and not args.fixtures_only:
        try:
            odds_received_at, board_rows = fetch_oddspapi_tennis_odds_by_tournaments(
                api_key,
                tournament_ids=tournament_ids,
                bookmakers=ODDSPAPI_PLAN_BOOKMAKERS,
                budget=budget,
            )
            billable_requests += 1
            odds_rows = merge_oddspapi_fixture_odds(fixtures, board_rows)
            for payload in odds_rows:
                _count_odds_shape(
                    payload,
                    match_winner_books=match_winner_books,
                    observed_market_ids=observed_market_ids,
                    observed_outcome_ids=observed_outcome_ids,
                )
        except TransportError as exc:
            odds_received_at = datetime.now(UTC)
            odds_error_kind = exc.kind
            if exc.kind.startswith("HTTP_"):
                billable_requests += 1
    else:
        odds_received_at = fixture_received_at

    account_after = fetch_oddspapi_account(api_key)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "fixture_received_at": fixture_received_at.isoformat(),
        "odds_received_at": odds_received_at.isoformat(),
        "billable_requests_attempted": billable_requests,
        "account_request_limit_before": account_before.request_limit,
        "account_request_count_before": account_before.request_count,
        "account_request_count_after": account_after.request_count,
        "fixture_rows": len(fixtures),
        "valid_fixture_rows": len(valid_fixtures),
        "fixture_parse_failures": parse_failures,
        "unique_tournament_ids": len(set(tournament_ids)),
        "odds_rows": len(odds_rows),
        "odds_error_kind": odds_error_kind,
        "bookmaker_fixture_hits": bookmaker_fixture_hits,
        "match_winner_market_id_expected": ODDSPAPI_MATCH_WINNER_MARKET_ID,
        "match_winner_outcome_ids_expected": [
            ODDSPAPI_PLAYER_1_OUTCOME_ID,
            ODDSPAPI_PLAYER_2_OUTCOME_ID,
        ],
        "match_winner_books": match_winner_books,
        "observed_market_ids": observed_market_ids,
        "observed_outcome_ids": observed_outcome_ids,
        "sanitized_fixture_sample": _fixture_sample(valid_fixtures[0]) if valid_fixtures else None,
        "sanitized_odds_sample": _odds_sample(odds_rows[0]) if odds_rows else None,
    }
    text = json.dumps(report, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print("ODDSPAPI_VALIDATION " + text, flush=True)
    return 0


def _budget_from_account(account, *, reserve: int) -> OddspapiRequestBudget:
    return OddspapiRequestBudget(
        monthly_limit=account.request_limit or 5000,
        monthly_used=account.request_count or 0,
        monthly_reserve=max(0, reserve),
    )


def _count_odds_shape(
    payload: Mapping[str, Any],
    *,
    match_winner_books: dict[str, int],
    observed_market_ids: dict[str, int],
    observed_outcome_ids: dict[str, int],
) -> None:
    books = payload.get("bookmakerOdds")
    if not isinstance(books, Mapping):
        return
    for slug, raw_book in books.items():
        if not isinstance(raw_book, Mapping):
            continue
        markets = raw_book.get("markets")
        if not isinstance(markets, Mapping):
            continue
        for market_id, raw_market in markets.items():
            observed_market_ids[str(market_id)] = observed_market_ids.get(str(market_id), 0) + 1
            if not isinstance(raw_market, Mapping):
                continue
            outcomes = raw_market.get("outcomes")
            if isinstance(outcomes, Mapping):
                for outcome_id in outcomes:
                    observed_outcome_ids[str(outcome_id)] = observed_outcome_ids.get(str(outcome_id), 0) + 1
        if str(slug) in match_winner_books and extract_oddspapi_match_winner_prices(payload, bookmaker=str(slug)) is not None:
            match_winner_books[str(slug)] += 1


def _fixture_sample(payload: Mapping[str, Any]) -> dict[str, Any]:
    external = payload.get("externalProviders")
    external = external if isinstance(external, Mapping) else {}
    return {
        "fixtureId": payload.get("fixtureId"),
        "sportId": payload.get("sportId"),
        "tournamentId": payload.get("tournamentId"),
        "tournamentName": payload.get("tournamentName"),
        "participant1Id": payload.get("participant1Id"),
        "participant1Name": payload.get("participant1Name"),
        "participant2Id": payload.get("participant2Id"),
        "participant2Name": payload.get("participant2Name"),
        "startTime": payload.get("startTime"),
        "statusId": payload.get("statusId"),
        "statusName": payload.get("statusName"),
        "hasOdds": payload.get("hasOdds"),
        "externalProviders": {
            "betradarId": external.get("betradarId"),
            "pinnacleId": external.get("pinnacleId"),
        },
    }


def _odds_sample(payload: Mapping[str, Any]) -> dict[str, Any]:
    sample = _fixture_sample(payload)
    books = payload.get("bookmakerOdds")
    book_sample: dict[str, Any] = {}
    if isinstance(books, Mapping):
        for slug, raw_book in books.items():
            if not isinstance(raw_book, Mapping):
                continue
            markets = raw_book.get("markets")
            market_ids = list(markets.keys())[:10] if isinstance(markets, Mapping) else []
            match_winner = markets.get(ODDSPAPI_MATCH_WINNER_MARKET_ID) if isinstance(markets, Mapping) else None
            book_sample[str(slug)] = {
                "bookmakerIsActive": raw_book.get("bookmakerIsActive"),
                "suspended": raw_book.get("suspended"),
                "bookmakerFixtureId_present": bool(raw_book.get("bookmakerFixtureId")),
                "market_ids_sample": market_ids,
                "match_winner": _market_sample(match_winner) if isinstance(match_winner, Mapping) else None,
            }
    sample["bookmakerOdds"] = book_sample
    return sample


def _market_sample(raw_market: Mapping[str, Any]) -> dict[str, Any]:
    outcomes = raw_market.get("outcomes")
    outcome_sample: dict[str, Any] = {}
    if isinstance(outcomes, Mapping):
        for outcome_id, outcome in outcomes.items():
            players = outcome.get("players") if isinstance(outcome, Mapping) else None
            player_zero = players.get("0") if isinstance(players, Mapping) else None
            outcome_sample[str(outcome_id)] = {
                "has_player_zero": isinstance(player_zero, Mapping),
                "active": player_zero.get("active") if isinstance(player_zero, Mapping) else None,
                "price": player_zero.get("price") if isinstance(player_zero, Mapping) else None,
                "changedAt": player_zero.get("changedAt") if isinstance(player_zero, Mapping) else None,
                "bookmakerChangedAt": player_zero.get("bookmakerChangedAt") if isinstance(player_zero, Mapping) else None,
                "bookmakerOutcomeId": player_zero.get("bookmakerOutcomeId") if isinstance(player_zero, Mapping) else None,
            }
    return {
        "marketActive": raw_market.get("marketActive"),
        "bookmakerMarketId_present": bool(raw_market.get("bookmakerMarketId")),
        "outcomes": outcome_sample,
    }


if __name__ == "__main__":
    sys.exit(main())
