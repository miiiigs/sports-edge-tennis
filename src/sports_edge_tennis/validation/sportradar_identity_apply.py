from __future__ import annotations

import csv
import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import psycopg


SOURCE_SYSTEM = "SPORTRADAR"
EXPECTED_ARTIFACT_SHA256 = "bae6a36a25d1bc1ba64d24eb4da2d9a4ba18adb6d175b0a2d0946cb96f0e3d18"
EXPECTED_INPUT_ROWS = 480
EXPECTED_APPLY_ROWS = 477
EXPECTED_BLOCKED_ROWS = 3
AUTH_TOKEN = "SPORTRADAR_TRIPLE_EXACT_BACKFILL_V1"
ENABLED_ENV = "SPORTRADAR_TRIPLE_EXACT_APPLY_ENABLED"
AUTH_ENV = "SPORTRADAR_TRIPLE_EXACT_APPLY_AUTHORIZATION"
HASH_ENV = "SPORTRADAR_TRIPLE_EXACT_ARTIFACT_SHA256"
DEFAULT_ARTIFACT = Path(
    "ops/validation/provider-cutover-rehearsal-20260922/"
    "sportradar-triple-exact-candidates.csv"
)


@dataclass(frozen=True)
class CandidateRow:
    match_id: int
    player_1_id: int
    player_2_id: int
    event_id: str
    player_1_source_id: str
    player_2_source_id: str
    oddspapi_betradar_id: str
    evidence_type: str


@dataclass(frozen=True)
class ApplyPlan:
    eligible: tuple[CandidateRow, ...]
    blocked: tuple[CandidateRow, ...]
    source_to_players: Mapping[str, frozenset[int]]


def _enabled(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def event_key(value: str | None) -> str:
    text = str(value or "").strip()
    prefix = "sr:sport_event:"
    if text.startswith(prefix):
        return text[len(prefix):]
    return text


def sha256_file(path: Path) -> str:
    """Hash the reviewed CSV in a checkout-independent canonical form.

    Git may materialize the same reviewed CSV with LF or CRLF line endings
    depending on the runner. Normalize only line endings and the terminal
    newline before hashing so the content lock remains stable across checkouts.
    """
    raw = path.read_bytes()
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    normalized = normalized.rstrip(b"\n") + b"\n"
    canonical = normalized.replace(b"\n", b"\r\n")
    return hashlib.sha256(canonical).hexdigest()


def load_candidates(path: Path) -> tuple[CandidateRow, ...]:
    rows: list[CandidateRow] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                CandidateRow(
                    match_id=int(raw["canonical_match_id"]),
                    player_1_id=int(raw["canonical_player_1_id"]),
                    player_2_id=int(raw["canonical_player_2_id"]),
                    event_id=str(raw["sportradar_event_id"]).strip(),
                    player_1_source_id=str(raw["p1_sportradar_id"]).strip(),
                    player_2_source_id=str(raw["p2_sportradar_id"]).strip(),
                    oddspapi_betradar_id=str(raw["oddspapi_betradar_id"]).strip(),
                    evidence_type=str(raw["evidence_type"]).strip(),
                )
            )
    return tuple(rows)


def build_plan(rows: Sequence[CandidateRow]) -> ApplyPlan:
    if len(rows) != EXPECTED_INPUT_ROWS:
        raise ValueError(f"candidate row count changed: expected={EXPECTED_INPUT_ROWS} actual={len(rows)}")

    event_to_matches: dict[str, set[int]] = defaultdict(set)
    match_to_events: dict[int, set[str]] = defaultdict(set)
    source_to_players: dict[str, set[int]] = defaultdict(set)
    player_to_sources: dict[int, set[str]] = defaultdict(set)

    for row in rows:
        if row.evidence_type != "TRIPLE_EXACT_BETRADAR":
            raise ValueError(f"unexpected evidence type for match {row.match_id}: {row.evidence_type}")
        if not row.event_id or not row.player_1_source_id or not row.player_2_source_id:
            raise ValueError(f"missing provider identity for match {row.match_id}")
        if row.player_1_id == row.player_2_id:
            raise ValueError(f"canonical players collide for match {row.match_id}")
        if row.player_1_source_id == row.player_2_source_id:
            raise ValueError(f"provider players collide for match {row.match_id}")
        if event_key(row.event_id) != event_key(row.oddspapi_betradar_id):
            raise ValueError(f"betradar mismatch for match {row.match_id}")

        key = event_key(row.event_id)
        event_to_matches[key].add(row.match_id)
        match_to_events[row.match_id].add(key)
        source_to_players[row.player_1_source_id].add(row.player_1_id)
        source_to_players[row.player_2_source_id].add(row.player_2_id)
        player_to_sources[row.player_1_id].add(row.player_1_source_id)
        player_to_sources[row.player_2_id].add(row.player_2_source_id)

    bad_events = {k: v for k, v in event_to_matches.items() if len(v) != 1}
    bad_matches = {k: v for k, v in match_to_events.items() if len(v) != 1}
    bad_players = {k: v for k, v in player_to_sources.items() if len(v) != 1}
    if bad_events or bad_matches or bad_players:
        raise ValueError(
            "candidate artifact has unexpected event/match/player conflicts "
            f"events={len(bad_events)} matches={len(bad_matches)} players={len(bad_players)}"
        )

    blocked_sources = {source_id for source_id, ids in source_to_players.items() if len(ids) != 1}
    eligible = tuple(
        row
        for row in rows
        if row.player_1_source_id not in blocked_sources
        and row.player_2_source_id not in blocked_sources
    )
    blocked = tuple(row for row in rows if row not in eligible)

    if len(eligible) != EXPECTED_APPLY_ROWS or len(blocked) != EXPECTED_BLOCKED_ROWS:
        raise ValueError(
            "candidate eligibility changed "
            f"eligible={len(eligible)} blocked={len(blocked)}"
        )
    return ApplyPlan(
        eligible=eligible,
        blocked=blocked,
        source_to_players={k: frozenset(v) for k, v in source_to_players.items()},
    )


def _match_source_state(conn: psycopg.Connection, *, match_id: int, event_id: str) -> str:
    variants = [event_id, event_key(event_id)]
    bare = event_key(event_id)
    if bare:
        variants.append(f"sr:sport_event:{bare}")
    rows = conn.execute(
        """
        SELECT DISTINCT match_id, source_match_id
        FROM match_source_ids
        WHERE source_system = %s
          AND (source_match_id = ANY(%s) OR match_id = %s)
        ORDER BY match_id, source_match_id
        """,
        (SOURCE_SYSTEM, list(dict.fromkeys(variants)), match_id),
    ).fetchall()
    for existing_match_id, existing_source_id in rows:
        if event_key(str(existing_source_id or "")) == bare and int(existing_match_id) != match_id:
            return "CONFLICT"
        if int(existing_match_id) == match_id and event_key(str(existing_source_id or "")) != bare:
            return "CONFLICT"
    if any(int(existing_match_id) == match_id and event_key(str(existing_source_id or "")) == bare for existing_match_id, existing_source_id in rows):
        return "EXISTING"
    return "INSERT"


def _player_source_state(conn: psycopg.Connection, *, player_id: int, source_id: str) -> str:
    rows = conn.execute(
        """
        SELECT DISTINCT player_id, source_player_id
        FROM player_source_ids
        WHERE source_system = %s
          AND (source_player_id = %s OR player_id = %s)
        ORDER BY player_id, source_player_id
        """,
        (SOURCE_SYSTEM, source_id, player_id),
    ).fetchall()
    for existing_player_id, existing_source_id in rows:
        if str(existing_source_id) == source_id and int(existing_player_id) != player_id:
            return "CONFLICT"
        if int(existing_player_id) == player_id and str(existing_source_id) != source_id:
            return "CONFLICT"
    if any(int(existing_player_id) == player_id and str(existing_source_id) == source_id for existing_player_id, existing_source_id in rows):
        return "EXISTING"
    return "INSERT"


def _latest_match_version(conn: psycopg.Connection, row: CandidateRow) -> int:
    value = conn.execute(
        """
        SELECT match_version_id, player_1_id, player_2_id
        FROM match_versions
        WHERE match_id = %s
        ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
        LIMIT 1
        """,
        (row.match_id,),
    ).fetchone()
    if value is None:
        raise RuntimeError(f"missing canonical match version match={row.match_id}")
    version_id, p1, p2 = value
    if int(p1) != row.player_1_id or int(p2) != row.player_2_id:
        raise RuntimeError(
            f"canonical participant drift match={row.match_id} "
            f"expected={row.player_1_id}/{row.player_2_id} actual={p1}/{p2}"
        )
    return int(version_id)


def _row_hash(artifact_hash: str, kind: str, canonical_id: int, source_id: str) -> str:
    payload = f"{artifact_hash}:{kind}:{canonical_id}:{source_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def apply_triple_exact_backfill(
    *,
    dsn: str,
    artifact_path: Path = DEFAULT_ARTIFACT,
    env: Mapping[str, str] | None = None,
) -> dict[str, int | str]:
    values = os.environ if env is None else env
    if not _enabled(values.get(ENABLED_ENV)):
        raise RuntimeError(f"{ENABLED_ENV} is not enabled")
    if str(values.get(AUTH_ENV) or "").strip() != AUTH_TOKEN:
        raise RuntimeError(f"{AUTH_ENV} authorization mismatch")

    actual_hash = sha256_file(artifact_path)
    supplied_hash = str(values.get(HASH_ENV) or "").strip()
    if actual_hash != EXPECTED_ARTIFACT_SHA256:
        raise RuntimeError(
            f"artifact hash mismatch expected={EXPECTED_ARTIFACT_SHA256} actual={actual_hash}"
        )
    if supplied_hash != EXPECTED_ARTIFACT_SHA256:
        raise RuntimeError(f"{HASH_ENV} must equal reviewed artifact hash")

    plan = build_plan(load_candidates(artifact_path))
    match_inserted = match_existing = 0
    player_inserted = player_existing = 0

    unique_players: dict[tuple[int, str], None] = {}
    for row in plan.eligible:
        unique_players[(row.player_1_id, row.player_1_source_id)] = None
        unique_players[(row.player_2_id, row.player_2_source_id)] = None

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET LOCAL lock_timeout = '10s'")
        conn.execute("SET LOCAL statement_timeout = '120s'")
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('sportradar_triple_exact_backfill_v1'))")

        # Revalidate the complete write set before the first INSERT. Any drift
        # aborts the whole transaction rather than producing a partial backfill.
        match_states: dict[int, tuple[str, int]] = {}
        for row in plan.eligible:
            state = _match_source_state(conn, match_id=row.match_id, event_id=row.event_id)
            if state == "CONFLICT":
                raise RuntimeError(f"match source conflict match={row.match_id} event={row.event_id}")
            match_states[row.match_id] = (state, _latest_match_version(conn, row))

        player_states: dict[tuple[int, str], str] = {}
        for player_id, source_id in unique_players:
            state = _player_source_state(conn, player_id=player_id, source_id=source_id)
            if state == "CONFLICT":
                raise RuntimeError(f"player source conflict player={player_id} source={source_id}")
            player_states[(player_id, source_id)] = state

        for row in plan.eligible:
            state, version_id = match_states[row.match_id]
            if state == "EXISTING":
                match_existing += 1
                continue
            conn.execute(
                """
                INSERT INTO match_source_ids (
                    match_id,
                    match_version_id,
                    source_system,
                    source_match_id,
                    source_record_hash,
                    source_record_locator,
                    source_file_name,
                    source_version_hash
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row.match_id,
                    version_id,
                    SOURCE_SYSTEM,
                    row.event_id,
                    _row_hash(actual_hash, "match", row.match_id, row.event_id),
                    f"sportradar:sport_event:{event_key(row.event_id)}",
                    artifact_path.name,
                    actual_hash,
                ),
            )
            match_inserted += 1

        for player_id, source_id in unique_players:
            state = player_states[(player_id, source_id)]
            if state == "EXISTING":
                player_existing += 1
                continue
            conn.execute(
                """
                INSERT INTO player_source_ids (
                    player_id,
                    source_system,
                    source_player_id,
                    source_record_hash
                )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    player_id,
                    SOURCE_SYSTEM,
                    source_id,
                    _row_hash(actual_hash, "player", player_id, source_id),
                ),
            )
            player_inserted += 1

        conn.commit()

    return {
        "artifact_sha256": actual_hash,
        "eligible_rows": len(plan.eligible),
        "blocked_rows": len(plan.blocked),
        "match_inserted": match_inserted,
        "match_existing": match_existing,
        "player_inserted": player_inserted,
        "player_existing": player_existing,
    }


__all__ = [
    "AUTH_ENV",
    "AUTH_TOKEN",
    "DEFAULT_ARTIFACT",
    "ENABLED_ENV",
    "EXPECTED_APPLY_ROWS",
    "EXPECTED_ARTIFACT_SHA256",
    "HASH_ENV",
    "CandidateRow",
    "ApplyPlan",
    "apply_triple_exact_backfill",
    "build_plan",
    "event_key",
    "load_candidates",
    "sha256_file",
]