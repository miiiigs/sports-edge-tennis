from __future__ import annotations

import csv
import json
import os
from collections import Counter
from pathlib import Path

import psycopg


AUDIT = Path(
    "ops/validation/provider-cutover-rehearsal-20260922/"
    "sportradar-canonical-link-audit.csv"
)
EXPECTED_ROWS = 29


def player_state(conn, player_id: int, source_id: str) -> str:
    rows = conn.execute(
        """
        SELECT player_id, source_player_id
        FROM player_source_ids
        WHERE source_system='SPORTRADAR'
          AND (player_id=%s OR source_player_id=%s)
        """,
        (player_id, source_id),
    ).fetchall()
    exact = False
    for existing_player, existing_source in rows:
        if int(existing_player) == player_id and str(existing_source) == source_id:
            exact = True
        elif int(existing_player) == player_id or str(existing_source) == source_id:
            return "CONFLICT"
    return "EXISTING" if exact else "INSERT"


def match_state(conn, match_id: int, event_id: str) -> str:
    rows = conn.execute(
        """
        SELECT match_id, source_match_id
        FROM match_source_ids
        WHERE source_system='SPORTRADAR'
          AND (match_id=%s OR source_match_id=%s)
        """,
        (match_id, event_id),
    ).fetchall()
    exact = False
    for existing_match, existing_source in rows:
        if int(existing_match) == match_id and str(existing_source) == event_id:
            exact = True
        elif int(existing_match) == match_id or str(existing_source) == event_id:
            return "CONFLICT"
    return "EXISTING" if exact else "INSERT"


def latest_participants(conn, match_id: int) -> tuple[int, int] | None:
    row = conn.execute(
        """
        SELECT player_1_id, player_2_id
        FROM match_versions
        WHERE match_id=%s
        ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    return None if row is None else (int(row[0]), int(row[1]))


def main() -> int:
    with AUDIT.open(newline="", encoding="utf-8") as handle:
        rows = [
            row for row in csv.DictReader(handle)
            if row["link_status"] == "EXACT"
            and row["oddspapi_crosscheck"] == "SPORTRADAR_ONLY"
            and row["evidence_type"] == "EXACT_SPORTRADAR_NAME_TIME"
        ]

    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError(f"expected {EXPECTED_ROWS} Sportradar-only exact rows, got {len(rows)}")
    for row in rows:
        if row["orientation"] != "DIRECT" or int(row["candidate_count"]) != 1:
            raise RuntimeError(f"unexpected audit shape for match {row['canonical_match_id']}")

    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("database URL unavailable")

    match_states = Counter()
    player_states = Counter()
    cohorts = Counter()
    strong_rows: list[int] = []
    review_rows: list[dict[str, object]] = []

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        if str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower() != "on":
            raise RuntimeError("read-only transaction not confirmed")

        for row in rows:
            match_id = int(row["canonical_match_id"])
            p1 = int(row["canonical_player_1_id"])
            p2 = int(row["canonical_player_2_id"])
            sid1 = row["player_1_sportradar_id"]
            sid2 = row["player_2_sportradar_id"]
            event_id = row["sportradar_event_id"]
            delta = int(row["start_delta_minutes"])

            actual = latest_participants(conn, match_id)
            if actual != (p1, p2):
                raise RuntimeError(
                    f"participant drift match={match_id} expected={(p1,p2)} actual={actual}"
                )

            ms = match_state(conn, match_id, event_id)
            s1 = player_state(conn, p1, sid1)
            s2 = player_state(conn, p2, sid2)
            match_states[ms] += 1
            player_states[s1] += 1
            player_states[s2] += 1

            if "CONFLICT" in (ms, s1, s2):
                cohort = "CONFLICT"
            elif s1 == "EXISTING" and s2 == "EXISTING" and ms == "INSERT":
                cohort = "BOTH_PLAYERS_ESTABLISHED"
                strong_rows.append(match_id)
            elif (s1 == "EXISTING") ^ (s2 == "EXISTING"):
                cohort = "ONE_PLAYER_ESTABLISHED"
            else:
                cohort = "NO_PLAYERS_ESTABLISHED"
            cohorts[cohort] += 1

            if cohort != "BOTH_PLAYERS_ESTABLISHED" or delta > 30:
                review_rows.append(
                    {
                        "match_id": match_id,
                        "tour": row["tour_scope"],
                        "delta_minutes": delta,
                        "match_state": ms,
                        "p1_state": s1,
                        "p2_state": s2,
                        "cohort": cohort,
                    }
                )

        conn.rollback()

    result = {
        "status": "OK",
        "transaction_read_only": "on",
        "writes": 0,
        "input_rows": len(rows),
        "match_states": dict(match_states),
        "player_reference_states": dict(player_states),
        "cohorts": dict(cohorts),
        "strong_match_only_candidates": len(strong_rows),
        "strong_match_ids": strong_rows,
        "review_rows": review_rows,
    }
    print("SPORTRADAR_STAGE1B_PREVIEW " + json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
