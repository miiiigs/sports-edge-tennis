"""Controlled API-Tennis + OddsPapi live readiness probe, hard-capped at 5 requests.

Purpose: produce the live evidence ``docs/API_TENNIS_ODDSPAPI_CUTOVER.md`` asks for
before the provider variables change, while spending at most five billable
OddsPapi HTTP attempts, retries included.

Plan (at most):
  1 x GET /v4/fixtures                 discovery, sharp book only
  4 x GET /v4/odds-by-tournaments      one chunk of <=5 tournaments x 4 books

Guarantees:
* Without ``--execute`` it makes **no** HTTP request; it prints the plan and the
  upcoming consensus scope only.
* Every attempt draws from one ``OddspapiAttemptCap`` (limit <= 5, enforced in code)
  *before* the ledger row is reserved and before a socket is opened. 429/5xx
  retries consume the same cap. Multi-id 404 fallbacks are disabled.
* With zero upcoming Manager-consensus matches it stops before discovery and
  reports zero qualified requests (Rule A).
* It never persists event mappings, identity-cache rows, odds observations or
  snapshots. Reconciliation runs with ``persist=False``. The only writes are the
  normal ``oddspapi_request_ledger`` accounting rows (classification DIAGNOSTIC).
* The API key is read from the environment and never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Sequence

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.oddspapi import (
    MAX_ODDSPAPI_TOURNAMENTS_PER_REQUEST,
    ODDSPAPI_PLAN_BOOKMAKERS,
    ODDSPAPI_SHARP_BOOKMAKER,
    fetch_oddspapi_tennis_fixtures,
    fetch_oddspapi_tennis_odds_for_books,
    merge_oddspapi_odds_books,
    parse_oddspapi_tennis_fixture,
)
from sports_edge_tennis.live.oddspapi_ledger import (
    OddspapiAttemptCap,
    OddspapiBudgetExceeded,
    OddspapiLedgerBinding,
    OddspapiLedgerConfig,
)
from sports_edge_tennis.live.oddspapi_reconciliation import (
    OddspapiReconciliationError,
    reconcile_oddspapi_event,
)
from sports_edge_tennis.live.transport import TransportError
from sports_edge_tennis.odds.providers.oddspapi_tennis import (
    build_oddspapi_tennis_observation,
)
from sports_edge_tennis.performance.regimes import PRODUCTION_RUN_PREDICATE

HARD_MAX_REQUESTS = 5
PROBE_MAX_START_DELTA_MINUTES = 60
EXPECTED_MAPPINGS = {
    "pinnacle+30": ("SHARP", "PINNACLE"),
    "1xbet": ("OFFERED", None),
    "unibet": ("OFFERED", None),
    "bet365": ("OFFERED", None),
}
PASS, FAIL, NOT_EVALUATED = "PASS", "FAIL", "NOT_EVALUATED"


@dataclass
class ProbeReport:
    correlation_id: str
    max_requests: int
    executed: bool
    consensus_upcoming: int = 0
    attempts_used: int = 0
    discovery_rows: int = 0
    discovery_valid_tennis: int = 0
    resolutions: dict[str, int] = field(default_factory=dict)
    contested_matches: int = 0
    matched: list[dict[str, Any]] = field(default_factory=list)
    tournaments_qualified: list[int] = field(default_factory=list)
    mappings: dict[str, Any] = field(default_factory=dict)
    quotes: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None
    acceptance: dict[str, str] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "probe": "API_TENNIS_ODDSPAPI_LIVE_READINESS",
            "correlation_id": self.correlation_id,
            "executed": self.executed,
            "max_requests": self.max_requests,
            "attempts_used": self.attempts_used,
            "consensus_upcoming": self.consensus_upcoming,
            "discovery_rows": self.discovery_rows,
            "discovery_valid_tennis": self.discovery_valid_tennis,
            "resolutions": self.resolutions,
            "contested_matches": self.contested_matches,
            "matched": self.matched,
            "tournaments_qualified": self.tournaments_qualified,
            "mappings": self.mappings,
            "quotes": self.quotes,
            "stop_reason": self.stop_reason,
            "acceptance": self.acceptance,
        }


def load_upcoming_consensus_scope(
    conn: psycopg.Connection, *, now: datetime, horizon_hours: int = 48
) -> list[dict[str, Any]]:
    """Consensus matches from the latest finalized production run, not yet started."""
    rows = conn.execute(
        f"""
        WITH latest_run AS (
            SELECT pr.prediction_run_id
            FROM prediction_runs pr
            WHERE {PRODUCTION_RUN_PREDICATE}
              AND pr.prediction_finalized_at <= %(now)s
            ORDER BY pr.prediction_finalized_at DESC, pr.prediction_run_id DESC
            LIMIT 1
        )
        SELECT DISTINCT mcr.match_id, lv.match_start_at
        FROM manager_consensus_results mcr
        JOIN latest_run lr ON lr.prediction_run_id = mcr.prediction_run_id
        JOIN LATERAL (
            SELECT match_start_at
            FROM match_versions
            WHERE match_id = mcr.match_id
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) lv ON TRUE
        WHERE mcr.manager_status = 'CONSENSUS'
          AND lv.match_start_at > %(now)s
          AND lv.match_start_at <= %(horizon)s
        ORDER BY lv.match_start_at, mcr.match_id
        """,
        {"now": now, "horizon": now + timedelta(hours=horizon_hours)},
    ).fetchall()
    return [{"match_id": int(r[0]), "scheduled_start": r[1]} for r in rows]


def load_mapping_state(conn: psycopg.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT DISTINCT ON (provider_bookmaker_key)
            provider_bookmaker_key, normalization_class::text, bookmaker_code
        FROM provider_bookmaker_mappings
        WHERE provider_code = 'ODDSPAPI'
          AND provider_bookmaker_key = ANY(%s)
        ORDER BY provider_bookmaker_key, effective_from DESC,
                 mapping_version DESC, provider_bookmaker_mapping_id DESC
        """,
        (list(EXPECTED_MAPPINGS),),
    ).fetchall()
    found = {str(r[0]): (str(r[1]), str(r[2]) if r[2] is not None else None) for r in rows}
    out: dict[str, Any] = {}
    for key, (cls, code) in EXPECTED_MAPPINGS.items():
        actual = found.get(key)
        ok = actual is not None and actual[0] == cls and (code is None or actual[1] == code)
        out[key] = {
            "expected_class": cls,
            "actual_class": actual[0] if actual else None,
            "bookmaker_code": actual[1] if actual else None,
            "ok": ok,
        }
    return out


def _verdict(condition: bool | None) -> str:
    if condition is None:
        return NOT_EVALUATED
    return PASS if condition else FAIL


def run_probe(
    conn: psycopg.Connection,
    *,
    api_key: str | None,
    now: datetime,
    max_requests: int = HARD_MAX_REQUESTS,
    execute: bool = False,
    ledger_config: OddspapiLedgerConfig | None = None,
    fetch_fixtures: Callable[..., Any] = fetch_oddspapi_tennis_fixtures,
    fetch_odds: Callable[..., Any] = fetch_oddspapi_tennis_odds_for_books,
) -> ProbeReport:
    if not 1 <= int(max_requests) <= HARD_MAX_REQUESTS:
        raise ValueError(f"max_requests must be between 1 and {HARD_MAX_REQUESTS}")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    report = ProbeReport(
        correlation_id=f"probe:{now.strftime('%Y%m%dT%H%MZ')}:{uuid.uuid4().hex[:8]}",
        max_requests=int(max_requests),
        executed=execute,
    )
    report.mappings = load_mapping_state(conn)
    scope = load_upcoming_consensus_scope(conn, now=now)
    report.consensus_upcoming = len(scope)

    sharp_ok = report.mappings["pinnacle+30"]["ok"]
    offered_ok = all(v["ok"] for k, v in report.mappings.items() if k != "pinnacle+30")

    if not scope:
        report.stop_reason = "ZERO_UPCOMING_CONSENSUS"
    elif not execute:
        report.stop_reason = "DRY_RUN_NO_REQUESTS"
    elif not api_key:
        report.stop_reason = "MISSING_API_KEY"

    if report.stop_reason is not None:
        report.acceptance = _acceptance(report, sharp_ok=sharp_ok, offered_ok=offered_ok, auth=None)
        return report

    cap = OddspapiAttemptCap(int(max_requests))
    config = ledger_config or OddspapiLedgerConfig(monthly_limit=5000, reserve=500)

    def binding(purpose: str) -> OddspapiLedgerBinding:
        return OddspapiLedgerBinding(
            conn=conn,
            config=config,
            request_purpose=purpose,
            classification="DIAGNOSTIC",
            correlation_id=report.correlation_id,
            attempt_cap=cap,
        )

    auth: bool | None = None
    try:
        start = min(item["scheduled_start"] for item in scope)
        end = max(item["scheduled_start"] for item in scope)
        window_start = start - timedelta(hours=2)
        window_end = max(end + timedelta(hours=2), window_start + timedelta(hours=6))
        discovery_at, discovery_rows = fetch_fixtures(
            api_key,
            from_at=window_start,
            to_at=window_end,
            bookmakers=(ODDSPAPI_SHARP_BOOKMAKER,),
            ledger=binding("DISCOVERY"),
        )
        auth = True
        report.discovery_rows = len(discovery_rows)

        scope_ids = tuple(item["match_id"] for item in scope)
        claims: dict[int, list[tuple[Any, Any]]] = {}
        for payload in discovery_rows:
            try:
                envelope = parse_oddspapi_tennis_fixture(payload)
            except ValueError:
                continue
            report.discovery_valid_tennis += 1
            try:
                resolution = reconcile_oddspapi_event(
                    conn,
                    envelope=envelope,
                    candidate_match_ids=scope_ids,
                    as_of=discovery_at,
                    max_start_delta_minutes=PROBE_MAX_START_DELTA_MINUTES,
                    persist=False,
                )
            except OddspapiReconciliationError:
                report.resolutions["ERROR"] = report.resolutions.get("ERROR", 0) + 1
                continue
            report.resolutions[resolution.status] = report.resolutions.get(resolution.status, 0) + 1
            if resolution.status in ("EXACT", "EXISTING") and resolution.candidate is not None:
                claims.setdefault(int(resolution.match_id), []).append((envelope, resolution))

        accepted: list[tuple[Any, Any]] = []
        for match_id, found in sorted(claims.items()):
            if len({env.fixture_id for env, _ in found}) != 1:
                report.contested_matches += 1
                continue
            envelope, resolution = found[0]
            accepted.append((envelope, resolution))
            cand = resolution.candidate
            report.matched.append(
                {
                    "match_id": match_id,
                    "oddspapi_fixture_id": envelope.fixture_id,
                    "tournament_id": envelope.tournament_id,
                    "provider_players": [envelope.participant_1_name, envelope.participant_2_name],
                    "canonical_players": [cand.canonical_player_1_name, cand.canonical_player_2_name],
                    "orientation": cand.provider_orientation,
                    "start_delta_minutes": cand.start_delta_minutes,
                    "resolution": resolution.status,
                }
            )

        tournaments = list(dict.fromkeys(env.tournament_id for env, _ in accepted))
        tournaments = tournaments[:MAX_ODDSPAPI_TOURNAMENTS_PER_REQUEST]
        report.tournaments_qualified = tournaments
        if not tournaments:
            report.stop_reason = "NO_ACCEPTED_FIXTURE_TO_QUALIFY"
        else:
            odds_at, board_rows, _ = fetch_odds(
                api_key,
                tournament_ids=tournaments,
                bookmakers=ODDSPAPI_PLAN_BOOKMAKERS,
                ledger=binding("QUALIFICATION"),
                fallback_budget=0,
            )
            rows = merge_oddspapi_odds_books(
                discovery_rows,
                board_rows,
                trusted_fixture_ids=[env.fixture_id for env, _ in accepted],
            )
            by_fixture = {str(r.get("fixtureId")): r for r in rows}
            for envelope, resolution in accepted:
                payload = by_fixture.get(envelope.fixture_id)
                if payload is None:
                    continue
                observation = build_oddspapi_tennis_observation(
                    event_payload=payload,
                    received_at=odds_at,
                    response_id=f"{report.correlation_id}:{envelope.fixture_id}",
                )
                for market in observation.markets:
                    report.quotes.append(
                        {
                            "match_id": resolution.match_id,
                            "bookmaker_key": market.provider_bookmaker_key,
                            "market_status": market.market_status,
                            "selections": len(market.selections),
                            "received_at": odds_at.isoformat(),
                            "prematch": envelope.match_start_at > odds_at,
                        }
                    )
            report.stop_reason = "COMPLETED"
    except OddspapiBudgetExceeded as exc:
        report.stop_reason = f"CAP_REACHED {exc}"
    except TransportError as exc:
        if exc.kind in ("HTTP_401", "HTTP_403"):
            auth = False
        report.stop_reason = f"TRANSPORT_{exc.kind}"
    finally:
        report.attempts_used = cap.used

    report.acceptance = _acceptance(report, sharp_ok=sharp_ok, offered_ok=offered_ok, auth=auth)
    return report


def _acceptance(
    report: ProbeReport, *, sharp_ok: bool, offered_ok: bool, auth: bool | None
) -> dict[str, str]:
    discovered = report.discovery_rows > 0 or auth is True
    matched = bool(report.matched)
    quotes = report.quotes
    sharp_quotes = [q for q in quotes if q["bookmaker_key"] == "pinnacle+30"]
    offered_quotes = [q for q in quotes if q["bookmaker_key"] != "pinnacle+30"]
    fresh = [q for q in quotes if q["market_status"] == "OPEN" and q["prematch"]]
    qualified_pair = {
        q["match_id"] for q in sharp_quotes if q["market_status"] == "OPEN" and q["prematch"]
    } & {q["match_id"] for q in offered_quotes if q["market_status"] == "OPEN" and q["prematch"]}
    return {
        "1_auth": _verdict(auth),
        "2_discovery_valid_tennis": _verdict(
            None if not discovered else report.discovery_valid_tennis > 0
        ),
        "3_canonical_match": _verdict(None if not discovered else matched),
        "4_orientation_recorded": _verdict(
            None if not matched else all(m["orientation"] in ("DIRECT", "REVERSED") for m in report.matched)
        ),
        "5_no_ambiguous_or_duplicate_accepted": _verdict(
            None if not discovered else True  # ambiguous/contested are counted, never accepted
        ),
        "6_pinnacle_sharp_mapping": _verdict(sharp_ok),
        "7_offered_mappings": _verdict(offered_ok),
        "8_quotes_open_and_prematch": _verdict(None if not quotes else len(fresh) == len(quotes)),
        "9_within_request_cap": _verdict(report.attempts_used <= report.max_requests),
        "10_qualified_evidence_without_pinnapi": _verdict(
            None if not report.tournaments_qualified else bool(qualified_pair)
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-requests", type=int, default=HARD_MAX_REQUESTS)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually call OddsPapi (billable). Without it, no request is made.",
    )
    parser.add_argument(
        "--dsn", default=os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    )
    args = parser.parse_args(argv)
    if not args.dsn:
        print("ODDSPAPI_PROBE_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    if not 1 <= args.max_requests <= HARD_MAX_REQUESTS:
        print(f"ODDSPAPI_PROBE_BLOCKED reason=MAX_REQUESTS_OVER_{HARD_MAX_REQUESTS}", flush=True)
        return 2
    api_key = load_secret("ODDSPAPI_API_KEY") if args.execute else None
    with psycopg.connect(args.dsn, autocommit=True) as conn:
        report = run_probe(
            conn,
            api_key=api_key,
            now=datetime.now(UTC),
            max_requests=args.max_requests,
            execute=args.execute,
        )
    print("ODDSPAPI_PROBE " + json.dumps(report.as_json(), default=str, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
