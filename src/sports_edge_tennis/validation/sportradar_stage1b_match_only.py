from __future__ import annotations

import csv
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import psycopg

from sports_edge_tennis.validation.sportradar_identity_apply import (
    SOURCE_SYSTEM,
    _latest_match_version,
    _match_source_state,
    _player_source_state,
    event_key,
    sha256_file,
)


EXPECTED_ARTIFACT_SHA256 = "31bd931e0c0e81f3b7ded752ec2f702d3637360fea22203d641bd2e0fb69084e"
EXPECTED_ROWS = 22
AUTH_TOKEN = "SPORTRADAR_STAGE1B_MATCH_ONLY_V1"
ENABLED_ENV = "SPORTRADAR_STAGE1B_APPLY_ENABLED"
AUTH_ENV = "SPORTRADAR_STAGE1B_APPLY_AUTHORIZATION"
HASH_ENV = "SPORTRADAR_STAGE1B_ARTIFACT_SHA256"
DEFAULT_ARTIFACT = Path(
    "ops/validation/provider-cutover-rehearsal-20260922/"
    "sportradar-stage1b-match-only-candidates.csv"
)


@dataclass(frozen=True)
class CandidateRow:
    match_id: int
    tour_scope: str
    player_1_id: int
    player_1_source_id: str
    player_2_id: int
    player_2_source_id: str
    event_id: str
    start_delta_minutes: int
    evidence_type: str
    orientation: str
    candidate_count: int


def _enabled(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _row_hash(artifact_hash: str, match_id: int, source_id: str) -> str:
    payload = f"{artifact_hash}:stage1b_match:{match_id}:{source_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_candidates(path: Path = DEFAULT_ARTIFACT) -> tuple[CandidateRow, ...]:
    rows: list[CandidateRow] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                CandidateRow(
                    match_id=int(raw["canonical_match_id"]),
                    tour_scope=str(raw["tour_scope"]).strip(),
                    player_1_id=int(raw["canonical_player_1_id"]),
                    player_1_source_id=str(raw["player_1_sportradar_id"]).strip(),
                    player_2_id=int(raw["canonical_player_2_id"]),
                    player_2_source_id=str(raw["player_2_sportradar_id"]).strip(),
                    event_id=str(raw["sportradar_event_id"]).strip(),
                    start_delta_minutes=int(raw["start_delta_minutes"]),
                    evidence_type=str(raw["evidence_type"]).strip(),
                    orientation=str(raw["orientation"]).strip(),
                    candidate_count=int(raw["candidate_count"]),
                )
            )
    return tuple(rows)


def validate_candidates(rows: Sequence[CandidateRow]) -> tuple[CandidateRow, ...]:
    if len(rows) != EXPECTED_ROWS:
        raise ValueError(f"candidate row count changed: expected={EXPECTED_ROWS} actual={len(rows)}")

    match_to_event: dict[int, str] = {}
    event_to_match: dict[str, int] = {}
    player_to_source: dict[int, str] = {}
    source_to_player: dict[str, int] = {}

    for row in rows:
        if row.evidence_type != "EXACT_SPORTRADAR_NAME_TIME":
            raise ValueError(f"unexpected evidence match={row.match_id}: {row.evidence_type}")
        if row.orientation != "DIRECT":
            raise ValueError(f"unexpected orientation match={row.match_id}: {row.orientation}")
        if row.candidate_count != 1:
            raise ValueError(f"candidate_count changed match={row.match_id}: {row.candidate_count}")
        if row.start_delta_minutes < 0 or row.start_delta_minutes > 30:
            raise ValueError(
                f"start delta outside Stage 1B gate match={row.match_id}: {row.start_delta_minutes}"
            )
        if not row.event_id or not row.player_1_source_id or not row.player_2_source_id:
            raise ValueError(f"missing provider identity match={row.match_id}")
        if row.player_1_id == row.player_2_id:
            raise ValueError(f"canonical player collision match={row.match_id}")
        if row.player_1_source_id == row.player_2_source_id:
            raise ValueError(f"provider player collision match={row.match_id}")

        key = event_key(row.event_id)
        existing_event = match_to_event.get(row.match_id)
        if existing_event is not None and existing_event != key:
            raise ValueError(f"canonical match maps to multiple events match={row.match_id}")
        match_to_event[row.match_id] = key

        existing_match = event_to_match.get(key)
        if existing_match is not None and existing_match != row.match_id:
            raise ValueError(f"Sportradar event maps to multiple matches event={key}")
        event_to_match[key] = row.match_id

        for player_id, source_id in (
            (row.player_1_id, row.player_1_source_id),
            (row.player_2_id, row.player_2_source_id),
        ):
            existing_source = player_to_source.get(player_id)
            if existing_source is not None and existing_source != source_id:
                raise ValueError(f"canonical player maps to multiple Sportradar IDs player={player_id}")
            player_to_source[player_id] = source_id

            existing_player = source_to_player.get(source_id)
            if existing_player is not None and existing_player != player_id:
                raise ValueError(f"Sportradar player maps to multiple canonical IDs source={source_id}")
            source_to_player[source_id] = player_id

    return tuple(rows)


def _revalidate(conn: psycopg.Connection, rows: Sequence[CandidateRow]) -> dict[int, tuple[str, int]]:
    states: dict[int, tuple[str, int]] = {}
    for row in rows:
        match_state = _match_source_state(conn, match_id=row.match_id, event_id=row.event_id)
        if match_state == "CONFLICT":
            raise RuntimeError(f"match source conflict match={row.match_id} event={row.event_id}")

        version_id = _latest_match_version(conn, row)

        p1_state = _player_source_state(
            conn, player_id=row.player_1_id, source_id=row.player_1_source_id
        )
        p2_state = _player_source_state(
            conn, player_id=row.player_2_id, source_id=row.player_2_source_id
        )
        if p1_state != "EXISTING" or p2_state != "EXISTING":
            raise RuntimeError(
                "Stage 1B requires pre-existing player identities "
                f"match={row.match_id} p1={p1_state} p2={p2_state}"
            )

        states[row.match_id] = (match_state, version_id)
    return states


def preview_stage1b_match_only(
    *,
    dsn: str,
    artifact_path: Path = DEFAULT_ARTIFACT,
) -> dict[str, int | str]:
    actual_hash = sha256_file(artifact_path)
    if actual_hash != EXPECTED_ARTIFACT_SHA256:
        raise RuntimeError(
            f"artifact hash mismatch expected={EXPECTED_ARTIFACT_SHA256} actual={actual_hash}"
        )
    rows = validate_candidates(load_candidates(artifact_path))

    match_insert = match_existing = 0
    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        if str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower() != "on":
            raise RuntimeError("transaction_read_only is not on")

        states = _revalidate(conn, rows)
        for state, _version_id in states.values():
            if state == "EXISTING":
                match_existing += 1
            else:
                match_insert += 1
        conn.rollback()

    return {
        "artifact_sha256": actual_hash,
        "candidate_rows": len(rows),
        "match_insert": match_insert,
        "match_existing": match_existing,
        "match_conflict": 0,
        "player_inserts_allowed": 0,
        "writes": 0,
        "transaction_read_only": "on",
    }


def apply_stage1b_match_only(
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

    rows = validate_candidates(load_candidates(artifact_path))
    inserted = existing = 0

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET LOCAL lock_timeout = '10s'")
        conn.execute("SET LOCAL statement_timeout = '120s'")
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('sportradar_stage1b_match_only_v1'))")

        states = _revalidate(conn, rows)

        for row in rows:
            state, version_id = states[row.match_id]
            if state == "EXISTING":
                existing += 1
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
                    _row_hash(actual_hash, row.match_id, row.event_id),
                    f"sportradar:sport_event:{event_key(row.event_id)}",
                    artifact_path.name,
                    actual_hash,
                ),
            )
            inserted += 1

        conn.commit()

    return {
        "artifact_sha256": actual_hash,
        "candidate_rows": len(rows),
        "match_inserted": inserted,
        "match_existing": existing,
        "player_inserted": 0,
    }


__all__ = [
    "AUTH_ENV",
    "AUTH_TOKEN",
    "DEFAULT_ARTIFACT",
    "ENABLED_ENV",
    "EXPECTED_ARTIFACT_SHA256",
    "EXPECTED_ROWS",
    "HASH_ENV",
    "CandidateRow",
    "apply_stage1b_match_only",
    "load_candidates",
    "preview_stage1b_match_only",
    "validate_candidates",
]
