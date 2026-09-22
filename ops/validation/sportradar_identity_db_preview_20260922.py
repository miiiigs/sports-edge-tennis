from __future__ import annotations

import json
import os

import psycopg

from sports_edge_tennis.validation.sportradar_identity_apply import (
    DEFAULT_ARTIFACT,
    EXPECTED_ARTIFACT_SHA256,
    _latest_match_version,
    _match_source_state,
    _player_source_state,
    build_plan,
    load_candidates,
    sha256_file,
)


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("TENNIS_DATABASE_URL/DATABASE_URL unavailable")

    actual_hash = sha256_file(DEFAULT_ARTIFACT)
    if actual_hash != EXPECTED_ARTIFACT_SHA256:
        raise RuntimeError(
            f"artifact hash mismatch expected={EXPECTED_ARTIFACT_SHA256} actual={actual_hash}"
        )

    plan = build_plan(load_candidates(DEFAULT_ARTIFACT))
    unique_players: dict[tuple[int, str], None] = {}
    match_insert = match_existing = match_conflict = 0
    player_insert = player_existing = player_conflict = 0

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower()
        if read_only != "on":
            raise RuntimeError(f"transaction_read_only={read_only!r}")

        for row in plan.eligible:
            state = _match_source_state(conn, match_id=row.match_id, event_id=row.event_id)
            if state == "CONFLICT":
                match_conflict += 1
            elif state == "EXISTING":
                match_existing += 1
            else:
                match_insert += 1

            _latest_match_version(conn, row)
            unique_players[(row.player_1_id, row.player_1_source_id)] = None
            unique_players[(row.player_2_id, row.player_2_source_id)] = None

        for player_id, source_id in unique_players:
            state = _player_source_state(conn, player_id=player_id, source_id=source_id)
            if state == "CONFLICT":
                player_conflict += 1
            elif state == "EXISTING":
                player_existing += 1
            else:
                player_insert += 1

        conn.rollback()

    result = {
        "status": "OK",
        "transaction_read_only": "on",
        "artifact_sha256": actual_hash,
        "eligible": len(plan.eligible),
        "blocked": len(plan.blocked),
        "unique_players": len(unique_players),
        "match_insert": match_insert,
        "match_existing": match_existing,
        "match_conflict": match_conflict,
        "player_insert": player_insert,
        "player_existing": player_existing,
        "player_conflict": player_conflict,
        "writes": 0,
    }
    print("SPORTRADAR_IDENTITY_DB_PREVIEW " + json.dumps(result, sort_keys=True), flush=True)

    if match_conflict or player_conflict:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
