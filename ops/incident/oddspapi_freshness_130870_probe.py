from __future__ import annotations

"""Read-only incident probe for the OddsPapi STALE_MARKET case on match 130870.

Prints sanitized structured diagnostics only. Never prints the database URL,
passwords, API keys, raw provider payloads, or any other secret. Writes nothing.
"""

import json
import os
import sys
from datetime import UTC, datetime

import psycopg

MATCH_ID = int(os.environ.get("FRESHNESS_PROBE_MATCH_ID") or (sys.argv[1] if len(sys.argv) > 1 else 130870))
PLAN_BOOKMAKERS = ("pinnacle+30", "1xbet", "unibet", "bet365")
SHARP_BOOKMAKERS = ("pinnacle+30",)
MATCH_WINNER_MARKET_ID = "121"


def _dsn() -> str:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("FRESHNESS_PROBE_BLOCKED missing_dsn")
    return dsn


def _emit(label: str, payload: dict) -> None:
    print(f"{label} {json.dumps(payload, default=str, sort_keys=True)}", flush=True)


def _parse_ts(value):
    if value in (None, ""):
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _age_seconds(decision_as_of, observed_at):
    if decision_as_of is None or observed_at is None:
        return None
    return int((decision_as_of - observed_at).total_seconds())


def _sanitize_payload(payload: dict) -> dict:
    """Extract only match-winner price timing fields; drop everything else."""
    out: dict = {
        "fixtureId": payload.get("fixtureId"),
        "sportId": payload.get("sportId"),
        "tournamentId": payload.get("tournamentId"),
        "participant1Id": payload.get("participant1Id"),
        "participant2Id": payload.get("participant2Id"),
        "startTime": payload.get("startTime"),
        "updatedAt": payload.get("updatedAt"),
        "books": {},
    }
    books = payload.get("bookmakerOdds")
    if not isinstance(books, dict):
        return out
    for slug in PLAN_BOOKMAKERS:
        raw_book = books.get(slug)
        if not isinstance(raw_book, dict):
            continue
        book_out: dict = {"suspended": raw_book.get("suspended"), "bookmakerChangedAt": raw_book.get("changedAt")}
        markets = raw_book.get("markets")
        market = markets.get(MATCH_WINNER_MARKET_ID) if isinstance(markets, dict) else None
        if not isinstance(market, dict):
            book_out["market_present"] = False
            out["books"][slug] = book_out
            continue
        book_out["marketActive"] = market.get("marketActive")
        book_out["marketChangedAt"] = market.get("changedAt")
        outcomes = market.get("outcomes")
        sides = {}
        if isinstance(outcomes, dict):
            for outcome_id in ("121", "122"):
                outcome = outcomes.get(outcome_id)
                if not isinstance(outcome, dict):
                    continue
                players = outcome.get("players")
                entry = players.get("0") if isinstance(players, dict) else None
                if not isinstance(entry, dict):
                    continue
                sides[outcome_id] = {
                    "active": entry.get("active"),
                    "price": entry.get("price"),
                    "changedAt": entry.get("changedAt"),
                    "bookmakerChangedAt": entry.get("bookmakerChangedAt"),
                }
        book_out["outcomes"] = sides
        out["books"][slug] = book_out
    return out


def main() -> int:
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")

        decisions = conn.execute(
            """
            SELECT d.decision_id, d.prediction_run_id, d.match_id,
                   d.decision_status::text, d.reason_code::text, d.selection_side,
                   d.consensus_probability, d.market_decimal_price,
                   d.sharp_reference_price, d.price_bookmaker_code,
                   d.price_source_system, d.as_of, d.market_policy_version_id,
                   d.snapshot_lineage_hash
            FROM decisions d
            WHERE d.match_id = %s
            ORDER BY d.decision_id
            """,
            (MATCH_ID,),
        ).fetchall()
        _emit("FRESHNESS_130870_DECISION_DETAIL", {"count": len(decisions)})

        managers = conn.execute(
            """
            SELECT manager_consensus_result_id, prediction_run_id,
                   manager_status::text, selected_side, consensus_probability,
                   active_agent_count, abstaining_agent_count, probability_spread,
                   reason_code::text, as_of
            FROM manager_consensus_results
            WHERE match_id = %s
            ORDER BY manager_consensus_result_id
            """,
            (MATCH_ID,),
        ).fetchall()
        for m in managers:
            _emit(
                "FRESHNESS_130870_MANAGER",
                {
                    "manager_consensus_result_id": m[0],
                    "prediction_run_id": m[1],
                    "manager_status": m[2],
                    "selected_side": m[3],
                    "consensus_probability": m[4],
                    "active_agent_count": m[5],
                    "abstaining_agent_count": m[6],
                    "probability_spread": m[7],
                    "reason_code": m[8],
                    "as_of": m[9],
                },
            )

        identity = conn.execute(
            """
            SELECT m.match_id, mv.match_version_id, mv.player_1_id, p1.canonical_name,
                   mv.player_2_id, p2.canonical_name, mv.match_start_at,
                   mv.match_status::text, mv.tour_scope::text,
                   mv.competition_tier::text, mv.as_of, mv.source_observed_at
            FROM matches m
            JOIN LATERAL (
                SELECT * FROM match_versions
                WHERE match_id = m.match_id
                ORDER BY as_of DESC, match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
            JOIN players p1 ON p1.player_id = mv.player_1_id
            JOIN players p2 ON p2.player_id = mv.player_2_id
            WHERE m.match_id = %s
            """,
            (MATCH_ID,),
        ).fetchall()
        for r in identity:
            _emit(
                "FRESHNESS_130870_MATCH",
                {
                    "match_id": r[0],
                    "match_version_id": r[1],
                    "player_1_id": r[2],
                    "player_1": r[3],
                    "player_2_id": r[4],
                    "player_2": r[5],
                    "match_start_at": r[6],
                    "match_status": r[7],
                    "tour_scope": r[8],
                    "competition_tier": r[9],
                    "version_as_of": r[10],
                    "version_source_observed_at": r[11],
                },
            )

        sources = conn.execute(
            """
            SELECT source_system, source_match_id
            FROM match_source_ids
            WHERE match_id = %s
            ORDER BY source_system, match_source_id
            """,
            (MATCH_ID,),
        ).fetchall()
        for s in sources:
            _emit("FRESHNESS_130870_SOURCE_ID", {"source_system": s[0], "source_match_id": s[1]})

        cache = conn.execute(
            """
            SELECT canonical_match_id, sportradar_event_id, oddspapi_event_id,
                   betradar_id, oddspapi_tournament_id, player_orientation,
                   resolution_status, scheduled_start, resolved_at, last_verified_at
            FROM oddspapi_event_identity_cache
            WHERE canonical_match_id = %s
            """,
            (MATCH_ID,),
        ).fetchall()
        for c in cache:
            _emit(
                "FRESHNESS_130870_IDENTITY_CACHE",
                {
                    "canonical_match_id": c[0],
                    "sportradar_event_id": c[1],
                    "oddspapi_event_id": c[2],
                    "betradar_id": c[3],
                    "oddspapi_tournament_id": c[4],
                    "player_orientation": c[5],
                    "resolution_status": c[6],
                    "scheduled_start": c[7],
                    "resolved_at": c[8],
                    "last_verified_at": c[9],
                },
            )

        # Per-decision freshness.
        for d in decisions:
            as_of = d[11]
            policy_ttl = None
            ttl_row = conn.execute(
                "SELECT odds_max_staleness_minutes FROM market_policy_versions WHERE market_policy_version_id = %s",
                (d[12],),
            ).fetchone()
            if ttl_row is not None:
                policy_ttl = int(ttl_row[0])
            _emit(
                "FRESHNESS_130870_DECISION",
                {
                    "decision_id": d[0],
                    "prediction_run_id": d[1],
                    "decision_status": d[3],
                    "reason_code": d[4],
                    "selection_side": d[5],
                    "consensus_probability": d[6],
                    "market_decimal_price": d[7],
                    "sharp_reference_price": d[8],
                    "price_bookmaker_code": d[9],
                    "price_source_system": d[10],
                    "decision_as_of": as_of,
                    "market_policy_version_id": d[12],
                    "freshness_limit_minutes": policy_ttl,
                    "snapshot_lineage_hash": d[13],
                },
            )

            snaps = conn.execute(
                """
                SELECT os.odds_snapshot_id, os.source_system, os.bookmaker_code,
                       os.selection_type::text, os.decimal_price, os.market_status::text,
                       os.source_observed_at, os.as_of, os.ingested_at,
                       os.raw_odds_observation_id, os.source_snapshot_id
                FROM decision_odds_snapshots dos
                JOIN odds_snapshots os ON os.odds_snapshot_id = dos.odds_snapshot_id
                WHERE dos.decision_id = %s
                ORDER BY dos.snapshot_role, os.odds_snapshot_id
                """,
                (d[0],),
            ).fetchall()
            for s in snaps:
                age = _age_seconds(as_of, s[6])
                _emit(
                    "FRESHNESS_130870_LINEAGE_SNAPSHOT",
                    {
                        "decision_id": d[0],
                        "odds_snapshot_id": s[0],
                        "source_system": s[1],
                        "bookmaker_code": s[2],
                        "selection_type": s[3],
                        "decimal_price": s[4],
                        "market_status": s[5],
                        "source_observed_at": s[6],
                        "as_of": s[7],
                        "ingested_at": s[8],
                        "raw_odds_observation_id": s[9],
                        "source_snapshot_id": s[10],
                        "market_age_seconds": age,
                        "fresh": (age is not None and policy_ttl is not None and age <= policy_ttl * 60),
                    },
                )

        # All odds snapshots for the match in the decision cycle window.
        all_snaps = conn.execute(
            """
            SELECT odds_snapshot_id, source_system, bookmaker_code, selection_type::text,
                   decimal_price, market_status::text, source_observed_at, as_of,
                   ingested_at, raw_odds_observation_id, source_snapshot_id
            FROM odds_snapshots
            WHERE match_id = %s
            ORDER BY odds_snapshot_id
            """,
            (MATCH_ID,),
        ).fetchall()
        for s in all_snaps:
            _emit(
                "FRESHNESS_130870_ODDS_SNAPSHOT",
                {
                    "odds_snapshot_id": s[0],
                    "source_system": s[1],
                    "bookmaker_code": s[2],
                    "selection_type": s[3],
                    "decimal_price": s[4],
                    "market_status": s[5],
                    "source_observed_at": s[6],
                    "as_of": s[7],
                    "ingested_at": s[8],
                    "raw_odds_observation_id": s[9],
                    "source_snapshot_id": s[10],
                },
            )

        # Bookmaker mappings for OddsPapi.
        mappings = conn.execute(
            """
            SELECT provider_bookmaker_key, mapping_version, bookmaker_code,
                   normalization_class::text, effective_from
            FROM provider_bookmaker_mappings
            WHERE provider_code = 'ODDSPAPI'
            ORDER BY provider_bookmaker_key, mapping_version
            """,
        ).fetchall()
        for m in mappings:
            _emit(
                "FRESHNESS_130870_BOOKMAKER_MAPPING",
                {
                    "provider_bookmaker_key": m[0],
                    "mapping_version": m[1],
                    "bookmaker_code": m[2],
                    "normalization_class": m[3],
                    "effective_from": m[4],
                },
            )

        # Raw OddsPapi observations linked to this match's snapshots.
        raw_rows = conn.execute(
            """
            SELECT DISTINCT r.raw_odds_observation_id, r.provider_code, r.provider_event_id,
                   r.provider_observation_key, r.provider_response_id, r.provider_observed_at,
                   r.received_at, r.observation_time, r.observation_time_basis::text,
                   r.adapter_version
            FROM raw_odds_observations r
            JOIN odds_snapshots os ON os.raw_odds_observation_id = r.raw_odds_observation_id
            WHERE os.match_id = %s
            ORDER BY r.raw_odds_observation_id
            """,
            (MATCH_ID,),
        ).fetchall()
        for r in raw_rows:
            _emit(
                "FRESHNESS_130870_RAW_OBSERVATION",
                {
                    "raw_odds_observation_id": r[0],
                    "provider_code": r[1],
                    "provider_event_id": r[2],
                    "provider_observation_key": r[3],
                    "provider_response_id": r[4],
                    "provider_observed_at": r[5],
                    "received_at": r[6],
                    "observation_time": r[7],
                    "observation_time_basis": r[8],
                    "adapter_version": r[9],
                },
            )
            payload_row = conn.execute(
                "SELECT payload_json FROM raw_odds_observations WHERE raw_odds_observation_id = %s",
                (r[0],),
            ).fetchone()
            if payload_row is None:
                continue
            payload = payload_row[0]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    continue
            if not isinstance(payload, dict):
                continue
            sanitized = _sanitize_payload(payload)
            _emit(
                "FRESHNESS_130870_RAW_PAYLOAD",
                {
                    "raw_odds_observation_id": r[0],
                    "received_at": r[6],
                    "provider_observed_at": r[5],
                    "payload": sanitized,
                },
            )
            decision_for_run = conn.execute(
                """
                SELECT decision_as_of FROM (
                    SELECT d.as_of AS decision_as_of
                    FROM decisions d
                    WHERE d.match_id = %s
                    UNION ALL
                    SELECT now()
                ) x
                ORDER BY decision_as_of DESC
                LIMIT 1
                """,
                (MATCH_ID,),
            ).fetchone()
            reference_time = decision_for_run[0] if decision_for_run else None
            for slug in PLAN_BOOKMAKERS:
                book = sanitized["books"].get(slug)
                if not book:
                    continue
                for outcome_id, side in sorted(book.get("outcomes", {}).items()):
                    changed = _parse_ts(side.get("changedAt"))
                    book_changed = _parse_ts(side.get("bookmakerChangedAt"))
                    _emit(
                        "FRESHNESS_130870_BOOK_TIMING",
                        {
                            "raw_odds_observation_id": r[0],
                            "bookmaker": slug,
                            "outcome_id": outcome_id,
                            "price": side.get("price"),
                            "active": side.get("active"),
                            "changed_at": side.get("changedAt"),
                            "bookmaker_changed_at": side.get("bookmakerChangedAt"),
                            "changed_age_seconds": _age_seconds(r[6], changed),
                            "bookmaker_changed_age_seconds": _age_seconds(r[6], book_changed),
                            "changed_age_vs_decision_seconds": _age_seconds(reference_time, changed),
                        },
                    )

        # Request ledger around the decision cycle.
        ledger = conn.execute(
            """
            SELECT oddspapi_request_ledger_id, requested_at, billing_month, endpoint,
                   bookmaker, tournament_chunk_size, request_purpose, classification,
                   attempt_status, http_status, retry_sequence, correlation_id
            FROM oddspapi_request_ledger
            ORDER BY oddspapi_request_ledger_id DESC
            LIMIT 25
            """,
        ).fetchall()
        for row in ledger:
            _emit(
                "FRESHNESS_130870_REQUEST_LEDGER",
                {
                    "oddspapi_request_ledger_id": row[0],
                    "requested_at": row[1],
                    "billing_month": row[2],
                    "endpoint": row[3],
                    "bookmaker": row[4],
                    "tournament_chunk_size": row[5],
                    "request_purpose": row[6],
                    "classification": row[7],
                    "attempt_status": row[8],
                    "http_status": row[9],
                    "retry_sequence": row[10],
                    "correlation_id": row[11],
                },
            )

    _emit("FRESHNESS_130870_DONE", {"writes": 0})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
