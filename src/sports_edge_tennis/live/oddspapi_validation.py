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
    build_oddspapi_tournament_odds_params,
    fetch_oddspapi_account,
    fetch_oddspapi_tennis_fixture_odds,
    fetch_oddspapi_tennis_fixtures,
    fetch_oddspapi_tennis_odds_by_tournaments,
    merge_oddspapi_fixture_odds,
    parse_oddspapi_tennis_fixture,
    sanitized_request_shape,
    wait_for_oddspapi_cooldown,
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
    parser.add_argument("--odds-only-tournament-ids")
    parser.add_argument("--minimal-bulk-control-tournament-id", type=int)
    parser.add_argument("--fallback-fixture-id")
    parser.add_argument("--cooldown-seconds", type=float, default=2.0)
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

    if args.minimal_bulk_control_tournament_id is not None:
        return _run_minimal_bulk_control(
            api_key=api_key,
            account_before=account_before,
            budget=budget,
            tournament_id=args.minimal_bulk_control_tournament_id,
            fallback_fixture_id=args.fallback_fixture_id,
            output=args.output,
        )

    fixtures: list[dict[str, Any]] = []
    valid_fixtures = []
    fixture_received_at = datetime.now(UTC)
    if not args.odds_only_tournament_ids:
        fixture_received_at, fixtures = fetch_oddspapi_tennis_fixtures(
            api_key,
            from_at=window_start,
            to_at=window_end,
            bookmakers=(ODDSPAPI_SHARP_BOOKMAKER,),
            budget=budget,
        )

    parse_failures = 0
    tournament_ids: list[int] = _parse_tournament_ids(args.odds_only_tournament_ids)
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
    odds_error_details: dict[str, Any] = {}
    if tournament_ids and not args.fixtures_only:
        try:
            if fixtures:
                wait_for_oddspapi_cooldown(args.cooldown_seconds)
            odds_received_at, board_rows = fetch_oddspapi_tennis_odds_by_tournaments(
                api_key,
                tournament_ids=tournament_ids,
                bookmakers=ODDSPAPI_PLAN_BOOKMAKERS,
                budget=budget,
            )
            odds_rows = (
                merge_oddspapi_fixture_odds(fixtures, board_rows)
                if fixtures
                else [dict(row) for row in board_rows]
            )
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
            odds_error_details = dict(exc.details)
    else:
        odds_received_at = fixture_received_at

    account_after = fetch_oddspapi_account(api_key)
    odds_http_status = odds_error_details.get("status_code")
    if odds_http_status is None and odds_error_kind and odds_error_kind.startswith("HTTP_"):
        odds_http_status = _status_from_kind(odds_error_kind)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "account_request_limit": account_before.request_limit,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "fixture_received_at": fixture_received_at.isoformat(),
        "odds_received_at": odds_received_at.isoformat(),
        "session_billable_requests": budget.session_used,
        "billable_requests_attempted": budget.session_used,
        "account_request_limit_before": account_before.request_limit,
        "account_request_count_before": account_before.request_count,
        "account_request_count_after": account_after.request_count,
        "fixture_rows": len(fixtures),
        "valid_fixture_rows": len(valid_fixtures),
        "fixture_parse_failures": parse_failures,
        "unique_tournament_ids": len(set(tournament_ids)),
        "odds_http_status": odds_http_status,
        "odds_error_code": odds_error_details.get("provider_code"),
        "odds_error_message": odds_error_details.get("provider_message"),
        "odds_error_details": odds_error_details.get("provider_details"),
        "odds_retry_ms": odds_error_details.get("retry_ms"),
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
        "reconciliation_summary": None,
        "pinnacle_comparison_summary": None,
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


def _run_minimal_bulk_control(
    *,
    api_key: str,
    account_before,
    budget: OddspapiRequestBudget,
    tournament_id: int,
    fallback_fixture_id: str | None,
    output: Path | None,
) -> int:
    current_params = build_oddspapi_tournament_odds_params(
        api_key=api_key,
        tournament_ids=(tournament_id,),
    )
    minimal_params = build_oddspapi_tournament_odds_params(
        api_key=api_key,
        tournament_ids=(tournament_id,),
        include_bookmakers=False,
        include_optional_params=False,
    )
    current_request_shape = sanitized_request_shape(
        endpoint="/v4/odds-by-tournaments",
        params=current_params,
        base_url="https://api.oddspapi.io",
    )
    minimal_request_shape = sanitized_request_shape(
        endpoint="/v4/odds-by-tournaments",
        params=minimal_params,
        base_url="https://api.oddspapi.io",
    )
    print(
        "ODDSPAPI_REQUEST_SHAPE "
        + json.dumps(current_request_shape, sort_keys=True),
        flush=True,
    )

    bulk_metadata = {}
    bulk_error_kind = None
    bulk_error_details: dict[str, Any] = {}
    bulk_rows: list[dict[str, Any]] = []
    try:
        _received_at, bulk_rows = fetch_oddspapi_tennis_odds_by_tournaments(
            api_key,
            tournament_ids=(tournament_id,),
            include_bookmakers=False,
            include_optional_params=False,
            budget=budget,
            metadata_sink=lambda metadata: bulk_metadata.update(_metadata_dict(metadata)),
        )
    except TransportError as exc:
        bulk_error_kind = exc.kind
        bulk_error_details = dict(exc.details)

    fixture_payload = None
    fixture_metadata = {}
    fixture_error_kind = None
    fixture_error_details: dict[str, Any] = {}
    if bulk_error_kind is not None and fallback_fixture_id:
        try:
            _fixture_received_at, fixture_payload = fetch_oddspapi_tennis_fixture_odds(
                api_key,
                fixture_id=fallback_fixture_id,
                budget=budget,
                metadata_sink=lambda metadata: fixture_metadata.update(_metadata_dict(metadata)),
            )
        except TransportError as exc:
            fixture_error_kind = exc.kind
            fixture_error_details = dict(exc.details)

    odds_rows = bulk_rows or ([fixture_payload] if isinstance(fixture_payload, Mapping) else [])
    returned_bookmakers, observed_market_ids, observed_outcome_ids = _shape_counts(odds_rows)
    candidate = _candidate_match_winner(odds_rows)
    account_after = fetch_oddspapi_account(api_key)
    endpoint_tested = "/v4/odds-by-tournaments"
    if fixture_payload is not None or fixture_error_kind is not None:
        endpoint_tested = "/v4/odds"
    artifact = {
        "generated_at": datetime.now(UTC).isoformat(),
        "endpoint_tested": endpoint_tested,
        "sanitized_params": minimal_request_shape["params"],
        "current_sanitized_request_shape": current_request_shape,
        "minimal_sanitized_request_shape": minimal_request_shape,
        "account_request_limit": account_before.request_limit,
        "account_request_count_before": account_before.request_count,
        "account_request_count_after": account_after.request_count,
        "session_billable_requests": budget.session_used,
        "http_status": fixture_metadata.get("status_code") or fixture_error_details.get("status_code") or bulk_metadata.get("status_code") or bulk_error_details.get("status_code"),
        "content_type": fixture_metadata.get("content_type") or fixture_error_details.get("content_type") or bulk_metadata.get("content_type") or bulk_error_details.get("content_type"),
        "response_size_bytes": fixture_metadata.get("response_size_bytes") or fixture_error_details.get("response_size_bytes") or bulk_metadata.get("response_size_bytes") or bulk_error_details.get("response_size_bytes"),
        "bulk_http_status": bulk_metadata.get("status_code") or bulk_error_details.get("status_code"),
        "bulk_error_summary": _error_summary(bulk_error_kind, bulk_error_details),
        "fixture_http_status": fixture_metadata.get("status_code") or fixture_error_details.get("status_code"),
        "fixture_error_summary": _error_summary(fixture_error_kind, fixture_error_details),
        "fixture_id": (fixture_payload or {}).get("fixtureId") if isinstance(fixture_payload, Mapping) else fallback_fixture_id,
        "tournament_id": tournament_id,
        "returned_bookmakers": returned_bookmakers,
        "observed_market_ids": observed_market_ids,
        "observed_outcome_ids": observed_outcome_ids,
        "candidate_match_winner_market": candidate,
        "timestamp_fields_observed": _timestamp_fields(odds_rows),
        "sanitized_odds_sample": _odds_sample(odds_rows[0]) if odds_rows else None,
        "error_summary": _error_summary(fixture_error_kind or bulk_error_kind, fixture_error_details or bulk_error_details),
    }
    text = json.dumps(artifact, sort_keys=True)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print("ODDSPAPI_VALIDATION " + text, flush=True)
    return 0


def _metadata_dict(metadata) -> dict[str, Any]:
    return {
        "status_code": metadata.status_code,
        "content_type": metadata.content_type,
        "response_size_bytes": metadata.response_size_bytes,
    }


def _error_summary(kind: str | None, details: Mapping[str, Any]) -> dict[str, Any] | None:
    if kind is None and not details:
        return None
    return {
        "kind": kind,
        "status_code": details.get("status_code"),
        "code": details.get("provider_code"),
        "message": details.get("provider_message"),
        "details": details.get("provider_details"),
        "retry_ms": details.get("retry_ms"),
        "content_type": details.get("content_type"),
        "response_size_bytes": details.get("response_size_bytes"),
    }


def _shape_counts(rows: list[Mapping[str, Any]]) -> tuple[list[str], dict[str, int], dict[str, int]]:
    bookmakers: set[str] = set()
    markets: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    for payload in rows:
        books = payload.get("bookmakerOdds")
        if not isinstance(books, Mapping):
            continue
        for slug, raw_book in books.items():
            bookmakers.add(str(slug))
            raw_markets = raw_book.get("markets") if isinstance(raw_book, Mapping) else None
            if not isinstance(raw_markets, Mapping):
                continue
            for market_id, raw_market in raw_markets.items():
                market_key = str(market_id)
                markets[market_key] = markets.get(market_key, 0) + 1
                raw_outcomes = raw_market.get("outcomes") if isinstance(raw_market, Mapping) else None
                if isinstance(raw_outcomes, Mapping):
                    for outcome_id in raw_outcomes:
                        outcome_key = str(outcome_id)
                        outcomes[outcome_key] = outcomes.get(outcome_key, 0) + 1
    return sorted(bookmakers), markets, outcomes


def _candidate_match_winner(rows: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    for payload in rows:
        books = payload.get("bookmakerOdds")
        if not isinstance(books, Mapping):
            continue
        p1 = str(payload.get("participant1Id") or "")
        p2 = str(payload.get("participant2Id") or "")
        for slug, raw_book in books.items():
            markets = raw_book.get("markets") if isinstance(raw_book, Mapping) else None
            if not isinstance(markets, Mapping):
                continue
            if "121" in markets and _market_has_outcomes(markets["121"], ("121", "122")):
                return {
                    "bookmaker": str(slug),
                    "market_id": "121",
                    "outcome_ids": ["121", "122"],
                    "participant_1_id": p1,
                    "participant_2_id": p2,
                    "participant_1_maps_to": "121",
                    "participant_2_maps_to": "122",
                    "status": "CURRENT_ASSUMPTION_VERIFIED_BY_STRUCTURE",
                }
    return None


def _market_has_outcomes(raw_market: Any, outcome_ids: tuple[str, ...]) -> bool:
    outcomes = raw_market.get("outcomes") if isinstance(raw_market, Mapping) else None
    if not isinstance(outcomes, Mapping):
        return False
    for outcome_id in outcome_ids:
        outcome = outcomes.get(outcome_id)
        players = outcome.get("players") if isinstance(outcome, Mapping) else None
        if not isinstance(players, Mapping) or not isinstance(players.get("0"), Mapping):
            return False
    return True


def _timestamp_fields(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"changedAt": 0, "bookmakerChangedAt": 0}
    for payload in rows:
        books = payload.get("bookmakerOdds")
        if not isinstance(books, Mapping):
            continue
        for raw_book in books.values():
            markets = raw_book.get("markets") if isinstance(raw_book, Mapping) else None
            if not isinstance(markets, Mapping):
                continue
            for raw_market in markets.values():
                outcomes = raw_market.get("outcomes") if isinstance(raw_market, Mapping) else None
                if not isinstance(outcomes, Mapping):
                    continue
                for outcome in outcomes.values():
                    players = outcome.get("players") if isinstance(outcome, Mapping) else None
                    player = players.get("0") if isinstance(players, Mapping) else None
                    if not isinstance(player, Mapping):
                        continue
                    for key in counts:
                        if player.get(key) is not None:
                            counts[key] += 1
    return counts


def _parse_tournament_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(value.strip()) for value in raw.split(",") if value.strip()]


def _status_from_kind(kind: str) -> int | None:
    try:
        return int(kind.removeprefix("HTTP_"))
    except ValueError:
        return None


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
