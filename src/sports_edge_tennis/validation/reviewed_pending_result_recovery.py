"""Guarded recovery for four reviewed historical pending tennis results.

The evidence packet is immutable source material. This tool verifies the current
canonical identity before every write, hashes the exact packet bytes, defaults
to preview, and requires the operator to repeat that proof hash on --apply.

It appends result observations at the current review/apply time. It never
backdates result evidence and never changes predictions, decisions, odds,
match versions, player identities, or model rules.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg


DEFAULT_PACKET = (
    Path.cwd()
    / "research"
    / "pending_result_reconciliation"
    / "reviewed_public_recovery_20261119.json"
)
SOURCE_SYSTEM = "REVIEWED_PUBLIC_CORROBORATION"
SOURCE_DATASET = "pending_result_reconciliation"
SOURCE_VERSION = "REVIEWED_PUBLIC_PENDING_RECOVERY_V1"
TERMINAL = ("COMPLETED", "RETIRED", "WALKOVER", "DEFAULTED", "CANCELLED")


class ReviewedPendingRecoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class CanonicalCase:
    match_id: int
    player_1: str
    player_2: str
    match_start_at: datetime
    round_code: str | None
    qualifying_flag: bool
    source_ids: tuple[tuple[str, str], ...]


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ReviewedPendingRecoveryError("timestamp_must_be_timezone_aware")
    return parsed.astimezone(UTC)


def load_packet(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("packet_version") != SOURCE_VERSION:
        raise ReviewedPendingRecoveryError("unexpected_packet_version")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 4:
        raise ReviewedPendingRecoveryError("expected_exactly_four_cases")
    ids = [int(case["match_id"]) for case in cases]
    if len(ids) != len(set(ids)):
        raise ReviewedPendingRecoveryError("duplicate_match_id_in_packet")
    return payload, hashlib.sha256(raw).hexdigest()


def load_canonical_case(conn: psycopg.Connection, match_id: int) -> CanonicalCase:
    row = conn.execute(
        """
        SELECT
            mv.match_id,
            p1.canonical_name,
            p2.canonical_name,
            mv.match_start_at,
            mv.round_code,
            mv.qualifying_flag
        FROM (
            SELECT DISTINCT ON (match_id)
                match_id, player_1_id, player_2_id, match_start_at,
                round_code, qualifying_flag
            FROM match_versions
            WHERE match_id=%s
            ORDER BY match_id, as_of DESC, source_observed_at DESC, match_version_id DESC
        ) mv
        JOIN players p1 ON p1.player_id=mv.player_1_id
        JOIN players p2 ON p2.player_id=mv.player_2_id
        """,
        (match_id,),
    ).fetchone()
    if row is None:
        raise ReviewedPendingRecoveryError(f"canonical_match_missing:{match_id}")
    source_rows = conn.execute(
        """
        SELECT source_system, source_match_id
        FROM match_source_ids
        WHERE match_id=%s AND source_match_id IS NOT NULL
        ORDER BY source_system, source_match_id
        """,
        (match_id,),
    ).fetchall()
    return CanonicalCase(
        match_id=int(row[0]),
        player_1=str(row[1]),
        player_2=str(row[2]),
        match_start_at=row[3].astimezone(UTC),
        round_code=None if row[4] is None else str(row[4]),
        qualifying_flag=bool(row[5]),
        source_ids=tuple((str(r[0]), str(r[1])) for r in source_rows),
    )


def verify_case(conn: psycopg.Connection, spec: dict[str, Any]) -> CanonicalCase:
    match_id = int(spec["match_id"])
    expected = spec["expected"]
    actual = load_canonical_case(conn, match_id)
    if actual.player_1 != str(expected["player_1"]):
        raise ReviewedPendingRecoveryError(f"player_1_mismatch:{match_id}")
    if actual.player_2 != str(expected["player_2"]):
        raise ReviewedPendingRecoveryError(f"player_2_mismatch:{match_id}")
    if actual.match_start_at != utc(str(expected["match_start_at_utc"])):
        raise ReviewedPendingRecoveryError(f"start_time_mismatch:{match_id}")

    required_source = (
        str(expected["source_system"]),
        str(expected["source_match_id"]),
    )
    if required_source not in actual.source_ids:
        raise ReviewedPendingRecoveryError(f"source_identity_mismatch:{match_id}")

    if "round_code" in expected and actual.round_code != str(expected["round_code"]):
        raise ReviewedPendingRecoveryError(f"round_code_mismatch:{match_id}")
    if "qualifying_flag" in expected and actual.qualifying_flag is not bool(
        expected["qualifying_flag"]
    ):
        raise ReviewedPendingRecoveryError(f"qualifying_flag_mismatch:{match_id}")

    result = spec["result"]
    outcome = str(result["outcome_status"])
    winner = result.get("winner_side")
    if outcome == "COMPLETED" and winner not in (1, 2):
        raise ReviewedPendingRecoveryError(f"completed_winner_missing:{match_id}")
    if outcome == "CANCELLED" and winner is not None:
        raise ReviewedPendingRecoveryError(f"cancelled_winner_must_be_null:{match_id}")
    if outcome not in {"COMPLETED", "CANCELLED"}:
        raise ReviewedPendingRecoveryError(f"unsupported_reviewed_outcome:{match_id}")

    return actual


def latest_terminal(conn: psycopg.Connection, match_id: int) -> tuple[str, int | None] | None:
    row = conn.execute(
        """
        SELECT outcome_status::text, winner_side
        FROM match_result_observations
        WHERE match_id=%s
          AND outcome_status IN ('COMPLETED','RETIRED','WALKOVER','DEFAULTED','CANCELLED')
        ORDER BY as_of DESC, source_observed_at DESC, match_result_observation_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0]), None if row[1] is None else int(row[1])


def _expected_result(spec: dict[str, Any]) -> tuple[str, int | None]:
    result = spec["result"]
    return str(result["outcome_status"]), (
        None if result.get("winner_side") is None else int(result["winner_side"])
    )


def preview(conn: psycopg.Connection, payload: dict[str, Any], packet_sha: str) -> dict[str, Any]:
    reviewed = []
    for spec in payload["cases"]:
        actual = verify_case(conn, spec)
        current = latest_terminal(conn, actual.match_id)
        desired = _expected_result(spec)
        if current is not None and current != desired:
            raise ReviewedPendingRecoveryError(
                f"conflicting_terminal_result:{actual.match_id}:{current[0]}:{current[1]}"
            )
        reviewed.append(
            {
                "match_id": actual.match_id,
                "desired_outcome": desired[0],
                "desired_winner_side": desired[1],
                "current_terminal": current,
                "state": "ALREADY_APPLIED" if current == desired else "READY",
            }
        )
    return {
        "packet_sha256": packet_sha,
        "reviewed_count": len(reviewed),
        "cases": reviewed,
        "writes": 0,
    }


def apply(
    conn: psycopg.Connection,
    payload: dict[str, Any],
    *,
    packet_path: Path,
    packet_sha: str,
    expected_proof_sha: str,
) -> dict[str, Any]:
    if packet_sha != expected_proof_sha:
        raise ReviewedPendingRecoveryError("proof_sha_mismatch")

    checked = preview(conn, payload, packet_sha)
    pending_specs = [
        spec
        for spec, row in zip(payload["cases"], checked["cases"], strict=True)
        if row["state"] == "READY"
    ]
    if not pending_specs:
        return {
            "packet_sha256": packet_sha,
            "applied": 0,
            "already_applied": len(checked["cases"]),
        }

    applied_at = datetime.now(UTC)
    with conn.transaction():
        ingest_run_id = int(
            conn.execute(
                """
                INSERT INTO ingest_runs (
                    source_system, source_dataset, started_at, finished_at, status,
                    source_version, source_file_name, source_file_hash,
                    rows_seen, rows_inserted
                ) VALUES (
                    %s, %s, %s, %s, 'COMPLETED',
                    %s, %s, %s, %s, %s
                )
                RETURNING ingest_run_id
                """,
                (
                    SOURCE_SYSTEM,
                    SOURCE_DATASET,
                    applied_at,
                    applied_at,
                    SOURCE_VERSION,
                    packet_path.name,
                    packet_sha,
                    len(pending_specs),
                    len(pending_specs),
                ),
            ).fetchone()[0]
        )
        for spec in pending_specs:
            verify_case(conn, spec)
            if latest_terminal(conn, int(spec["match_id"])) is not None:
                raise ReviewedPendingRecoveryError(
                    f"terminal_result_changed_during_apply:{spec['match_id']}"
                )
            result = spec["result"]
            note = (
                str(result.get("scoreline_text") or "")
                + f"; reviewed_public_proof_sha256={packet_sha}"
            ).strip("; ")
            conn.execute(
                """
                INSERT INTO match_result_observations (
                    match_id, outcome_status, winner_side, retired_side,
                    sets_won_player_1, sets_won_player_2,
                    scoreline_text, source_observed_at, as_of, ingest_run_id
                ) VALUES (
                    %s, %s::result_status, %s, NULL,
                    %s, %s,
                    %s, %s, %s, %s
                )
                """,
                (
                    int(spec["match_id"]),
                    str(result["outcome_status"]),
                    result.get("winner_side"),
                    result.get("sets_won_player_1"),
                    result.get("sets_won_player_2"),
                    note,
                    applied_at,
                    applied_at,
                    ingest_run_id,
                ),
            )

    return {
        "packet_sha256": packet_sha,
        "applied": len(pending_specs),
        "already_applied": len(checked["cases"]) - len(pending_specs),
        "applied_at_utc": applied_at.isoformat().replace("+00:00", "Z"),
    }


def _dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--dsn", default=_dsn())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-proof-sha")
    args = parser.parse_args(argv)
    if not args.dsn:
        print("REVIEWED_PENDING_RECOVERY_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    try:
        payload, packet_sha = load_packet(args.packet)
        with psycopg.connect(args.dsn, autocommit=False, connect_timeout=10) as conn:
            report = preview(conn, payload, packet_sha)
            print(
                "REVIEWED_PENDING_RECOVERY_PREVIEW "
                f"proof_sha256={packet_sha} reviewed={report['reviewed_count']} "
                f"states={','.join(str(r['match_id'])+':'+r['state'] for r in report['cases'])} "
                "writes=0",
                flush=True,
            )
            if not args.apply:
                conn.rollback()
                return 0
            if not args.expected_proof_sha:
                raise ReviewedPendingRecoveryError("apply_requires_expected_proof_sha")
            result = apply(
                conn,
                payload,
                packet_path=args.packet,
                packet_sha=packet_sha,
                expected_proof_sha=args.expected_proof_sha,
            )
            conn.commit()
        print(
            "REVIEWED_PENDING_RECOVERY_APPLIED "
            f"proof_sha256={packet_sha} applied={result['applied']} "
            f"already_applied={result['already_applied']}",
            flush=True,
        )
        return 0
    except Exception as exc:
        print(
            f"REVIEWED_PENDING_RECOVERY_FAILED kind={type(exc).__name__} message={exc}",
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
