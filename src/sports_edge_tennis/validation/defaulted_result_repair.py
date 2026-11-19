"""Guarded one-case repair for a provider Awarded/in-match-default result.

This tool is intentionally scoped to canonical match 131712 / API-Tennis event
12168226. It verifies the existing canonical event, participant and tournament
source identities, fetches one bounded provider date, requires the distinct
DEFAULTED parser outcome and a declared winner, and defaults to dry-run.

No prediction, odds, winner-selection or historical timestamp is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Mapping

import psycopg

from sports_edge_tennis.live.credentials import MissingCredentialError, load_secret
from sports_edge_tennis.live.history_refresh import (
    fetch_api_tennis_fixture_window,
    parse_terminal_result,
)

MATCH_ID = 131712
EVENT_KEY = "12168226"
EVENT_DATE = date(2026, 10, 5)
SOURCE_SYSTEM = "API_TENNIS"
DATASET = "api_tennis_defaulted_result_repair"
SOURCE_VERSION = "DEFAULTED_REPAIR_V1"


class DefaultedRepairError(RuntimeError):
    pass


@dataclass(frozen=True)
class CanonicalIdentity:
    match_id: int
    event_key: str
    player_1_source_id: str
    player_2_source_id: str
    tournament_source_id: str


def _one_source_id(conn: psycopg.Connection, sql: str, params: tuple[Any, ...], label: str) -> str:
    rows = conn.execute(sql, params).fetchall()
    values = sorted({str(row[0]) for row in rows if row[0] is not None})
    if len(values) != 1:
        raise DefaultedRepairError(f"{label}_source_identity_not_unique")
    return values[0]


def load_canonical_identity(conn: psycopg.Connection) -> CanonicalIdentity:
    row = conn.execute(
        """
        SELECT mv.player_1_id, mv.player_2_id, te.tournament_id
        FROM matches m
        JOIN tournament_editions te
          ON te.tournament_edition_id = m.tournament_edition_id
        JOIN LATERAL (
            SELECT player_1_id, player_2_id
            FROM match_versions
            WHERE match_id = m.match_id
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        WHERE m.match_id = %s
        """,
        (MATCH_ID,),
    ).fetchone()
    if row is None:
        raise DefaultedRepairError("canonical_match_missing")
    p1, p2, tournament_id = map(int, row)

    event = _one_source_id(
        conn,
        """
        SELECT source_match_id FROM match_source_ids
        WHERE match_id=%s AND source_system=%s AND source_match_id IS NOT NULL
        """,
        (MATCH_ID, SOURCE_SYSTEM),
        "match",
    )
    if event != EVENT_KEY:
        raise DefaultedRepairError("canonical_event_key_mismatch")

    p1_source = _one_source_id(
        conn,
        """
        SELECT source_player_id FROM player_source_ids
        WHERE player_id=%s AND source_system=%s
        """,
        (p1, SOURCE_SYSTEM),
        "player_1",
    )
    p2_source = _one_source_id(
        conn,
        """
        SELECT source_player_id FROM player_source_ids
        WHERE player_id=%s AND source_system=%s
        """,
        (p2, SOURCE_SYSTEM),
        "player_2",
    )
    tournament_source = _one_source_id(
        conn,
        """
        SELECT source_tournament_id FROM tournament_source_ids
        WHERE tournament_id=%s AND source_system=%s
        """,
        (tournament_id, SOURCE_SYSTEM),
        "tournament",
    )
    return CanonicalIdentity(
        match_id=MATCH_ID,
        event_key=event,
        player_1_source_id=p1_source,
        player_2_source_id=p2_source,
        tournament_source_id=tournament_source,
    )


def verify_fixture(identity: CanonicalIdentity, fixture: Mapping[str, Any]) -> dict[str, Any]:
    if str(fixture.get("event_key") or "") != identity.event_key:
        raise DefaultedRepairError("provider_event_key_mismatch")
    if str(fixture.get("first_player_key") or "") != identity.player_1_source_id:
        raise DefaultedRepairError("provider_player_1_identity_mismatch")
    if str(fixture.get("second_player_key") or "") != identity.player_2_source_id:
        raise DefaultedRepairError("provider_player_2_identity_mismatch")
    if str(fixture.get("tournament_key") or "") != identity.tournament_source_id:
        raise DefaultedRepairError("provider_tournament_identity_mismatch")

    terminal = parse_terminal_result(fixture)
    if terminal is None or terminal.outcome_status != "DEFAULTED":
        raise DefaultedRepairError("provider_not_verified_defaulted")
    if terminal.winner_side not in (1, 2):
        raise DefaultedRepairError("provider_defaulted_winner_missing")

    proof = {
        "match_id": identity.match_id,
        "event_key": identity.event_key,
        "player_1_source_id": identity.player_1_source_id,
        "player_2_source_id": identity.player_2_source_id,
        "tournament_source_id": identity.tournament_source_id,
        "provider_status": str(fixture.get("event_status") or ""),
        "winner_side": terminal.winner_side,
    }
    proof_sha = hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"proof": proof, "proof_sha256": proof_sha, "terminal": terminal}


def persist_defaulted_result(
    conn: psycopg.Connection,
    *,
    identity: CanonicalIdentity,
    received_at: datetime,
    winner_side: int,
    proof_sha: str,
    expected_proof_sha: str,
) -> str:
    if proof_sha != expected_proof_sha:
        raise DefaultedRepairError("proof_sha_mismatch")
    existing = conn.execute(
        """
        SELECT outcome_status::text, winner_side
        FROM match_result_observations
        WHERE match_id=%s
          AND outcome_status IN ('COMPLETED','RETIRED','WALKOVER','DEFAULTED','CANCELLED')
        ORDER BY as_of DESC, source_observed_at DESC, match_result_observation_id DESC
        LIMIT 1
        """,
        (identity.match_id,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) == ("DEFAULTED", winner_side):
            return "ALREADY_APPLIED"
        raise DefaultedRepairError("conflicting_terminal_result_exists")

    with conn.transaction():
        ingest_run_id = int(
            conn.execute(
                """
                INSERT INTO ingest_runs (
                    source_system, source_dataset, started_at, finished_at, status,
                    source_version, rows_seen, rows_inserted
                ) VALUES (%s, %s, %s, clock_timestamp(), 'COMPLETED', %s, 1, 1)
                RETURNING ingest_run_id
                """,
                (SOURCE_SYSTEM, DATASET, received_at, SOURCE_VERSION),
            ).fetchone()[0]
        )
        conn.execute(
            """
            INSERT INTO match_result_observations (
                match_id, outcome_status, winner_side, retired_side,
                scoreline_text, source_observed_at, as_of, ingest_run_id
            ) VALUES (%s, 'DEFAULTED', %s, NULL, %s, %s, %s, %s)
            """,
            (
                identity.match_id,
                winner_side,
                "provider_status=Awarded;proof_sha256=" + proof_sha,
                received_at,
                received_at,
                ingest_run_id,
            ),
        )
    return "APPLIED"


def _dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=_dsn())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-proof-sha")
    args = parser.parse_args(argv)
    if not args.dsn:
        print("DEFAULTED_REPAIR_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    try:
        api_key = load_secret("API_TENNIS_API_KEY")
    except MissingCredentialError:
        api_key = None
    if not api_key:
        print("DEFAULTED_REPAIR_BLOCKED reason=MISSING_API_TENNIS_KEY", flush=True)
        return 2

    try:
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            identity = load_canonical_identity(conn)
        received_at, fixtures = fetch_api_tennis_fixture_window(
            api_key, EVENT_DATE, EVENT_DATE
        )
        matches = [f for f in fixtures if str(f.get("event_key") or "") == EVENT_KEY]
        if len(matches) != 1:
            raise DefaultedRepairError("provider_exact_event_not_unique")
        verified = verify_fixture(identity, matches[0])
        proof_sha = str(verified["proof_sha256"])
        print(
            "DEFAULTED_REPAIR_PREVIEW "
            f"match_id={MATCH_ID} event_key={EVENT_KEY} outcome=DEFAULTED "
            f"winner_side={verified['terminal'].winner_side} proof_sha256={proof_sha} "
            "writes=0",
            flush=True,
        )
        if not args.apply:
            return 0
        if not args.expected_proof_sha:
            raise DefaultedRepairError("apply_requires_expected_proof_sha")
        with psycopg.connect(args.dsn, autocommit=False) as conn:
            state = persist_defaulted_result(
                conn,
                identity=identity,
                received_at=received_at,
                winner_side=int(verified["terminal"].winner_side),
                proof_sha=proof_sha,
                expected_proof_sha=args.expected_proof_sha,
            )
        print(
            f"DEFAULTED_REPAIR_{state} match_id={MATCH_ID} proof_sha256={proof_sha}",
            flush=True,
        )
        return 0
    except Exception as exc:  # fail-closed CLI boundary
        print(f"DEFAULTED_REPAIR_FAILED kind={type(exc).__name__}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
