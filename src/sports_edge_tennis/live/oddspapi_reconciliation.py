from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Sequence

import psycopg

from sports_edge_tennis.live.oddspapi import OddspapiTennisFixtureEnvelope
from sports_edge_tennis.live.sportradar_benchmark import deterministic_name_keys


SOURCE_SYSTEM = "ODDSPAPI"
SPORTRADAR_SOURCE_SYSTEM = "SPORTRADAR"
DEFAULT_MAX_START_DELTA_MINUTES = 120

ResolutionStatus = Literal["EXACT", "EXISTING", "UNMATCHED", "AMBIGUOUS", "CONFLICT"]


@dataclass(frozen=True)
class OddspapiCanonicalCandidate:
    match_id: int
    match_version_id: int
    canonical_player_1_id: int
    canonical_player_2_id: int
    canonical_player_1_name: str
    canonical_player_2_name: str
    provider_orientation: Literal["DIRECT", "REVERSED"]
    start_delta_minutes: int


@dataclass(frozen=True)
class OddspapiEventResolution:
    status: ResolutionStatus
    match_id: int | None
    candidate: OddspapiCanonicalCandidate | None
    candidate_count: int
    reason: str | None = None


class OddspapiReconciliationError(RuntimeError):
    pass


def reconcile_oddspapi_event(
    conn: psycopg.Connection,
    *,
    envelope: OddspapiTennisFixtureEnvelope,
    candidate_match_ids: Sequence[int],
    as_of: datetime,
    max_start_delta_minutes: int = DEFAULT_MAX_START_DELTA_MINUTES,
    persist: bool = False,
) -> OddspapiEventResolution:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    unique_ids = tuple(dict.fromkeys(int(value) for value in candidate_match_ids))

    existing = _existing_mapping(conn, envelope.fixture_id)
    if existing is not None:
        if unique_ids and existing not in unique_ids:
            return OddspapiEventResolution("CONFLICT", None, None, 1, "existing_mapping_outside_candidate_scope")
        candidate = _load_one_candidate(conn, match_id=existing, as_of=as_of, envelope=envelope, max_start_delta_minutes=max_start_delta_minutes)
        if candidate is None:
            return OddspapiEventResolution("CONFLICT", None, None, 1, "existing_mapping_evidence_conflict")
        resolution = OddspapiEventResolution("EXISTING", existing, candidate, 1)
        if persist:
            _apply_identity_bridge(conn, envelope=envelope, candidate=candidate)
        return resolution

    betradar_matches = _betradar_matches(conn, envelope.betradar_id)
    if len(betradar_matches) > 1:
        return OddspapiEventResolution("CONFLICT", None, None, len(betradar_matches), "multiple_betradar_matches")
    if len(betradar_matches) == 1:
        match_id = betradar_matches[0]
        if unique_ids and match_id not in unique_ids:
            return OddspapiEventResolution("CONFLICT", None, None, 1, "betradar_mapping_outside_candidate_scope")
        candidate = _load_one_candidate(conn, match_id=match_id, as_of=as_of, envelope=envelope, max_start_delta_minutes=max_start_delta_minutes)
        if candidate is None:
            return OddspapiEventResolution("CONFLICT", None, None, 1, "betradar_mapping_evidence_conflict")
        if persist:
            _persist_event_mapping(conn, envelope=envelope, candidate=candidate)
            _apply_identity_bridge(conn, envelope=envelope, candidate=candidate)
        return OddspapiEventResolution("EXACT", match_id, candidate, 1, "BETRADAR_ID")

    if not unique_ids:
        return OddspapiEventResolution("UNMATCHED", None, None, 0, "empty_candidate_scope")

    rows = _load_candidate_rows(conn, candidate_match_ids=unique_ids, as_of=as_of)
    matches = [
        candidate
        for row in rows
        if (candidate := _candidate_from_row(row, envelope=envelope, max_start_delta_minutes=max_start_delta_minutes)) is not None
    ]
    if len(matches) > 1:
        return OddspapiEventResolution("AMBIGUOUS", None, None, len(matches), "multiple_name_time_candidates")
    if not matches:
        return OddspapiEventResolution("UNMATCHED", None, None, 0, "name_time_unmatched")
    candidate = matches[0]
    if persist:
        _persist_event_mapping(conn, envelope=envelope, candidate=candidate)
        _apply_identity_bridge(conn, envelope=envelope, candidate=candidate)
    return OddspapiEventResolution("EXACT", candidate.match_id, candidate, 1, "NAME_TIME")


def _existing_mapping(conn: psycopg.Connection, fixture_id: str) -> int | None:
    rows = conn.execute(
        """
        SELECT match_id
        FROM match_source_ids
        WHERE source_system = %s AND source_match_id = %s
        ORDER BY match_source_id
        LIMIT 2
        """,
        (SOURCE_SYSTEM, fixture_id),
    ).fetchall()
    if len(rows) > 1:
        raise OddspapiReconciliationError("oddspapi_event_identity_conflict")
    return int(rows[0][0]) if rows else None


def _betradar_matches(conn: psycopg.Connection, betradar_id: str | None) -> tuple[int, ...]:
    if not betradar_id:
        return ()
    variants = (str(betradar_id), f"sr:sport_event:{betradar_id}")
    rows = conn.execute(
        """
        SELECT DISTINCT match_id
        FROM match_source_ids
        WHERE source_system = %s
          AND source_match_id = ANY(%s)
        ORDER BY match_id
        """,
        (SPORTRADAR_SOURCE_SYSTEM, list(variants)),
    ).fetchall()
    return tuple(int(row[0]) for row in rows)


def _load_one_candidate(conn, *, match_id: int, as_of: datetime, envelope, max_start_delta_minutes: int):
    rows = _load_candidate_rows(conn, candidate_match_ids=(match_id,), as_of=as_of)
    if len(rows) != 1:
        return None
    return _candidate_from_row(rows[0], envelope=envelope, max_start_delta_minutes=max_start_delta_minutes)


def _load_candidate_rows(conn, *, candidate_match_ids: Sequence[int], as_of: datetime):
    return conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, mv.match_version_id, mv.player_1_id, mv.player_2_id, mv.match_start_at
            FROM match_versions mv
            WHERE mv.match_id = ANY(%s)
              AND mv.source_observed_at <= %s
              AND mv.as_of <= %s
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        )
        SELECT latest.match_id, latest.match_version_id, latest.player_1_id, latest.player_2_id,
               latest.match_start_at, p1.canonical_name, p2.canonical_name
        FROM latest
        JOIN players p1 ON p1.player_id = latest.player_1_id
        JOIN players p2 ON p2.player_id = latest.player_2_id
        WHERE latest.match_start_at IS NOT NULL
        ORDER BY latest.match_id
        """,
        (list(candidate_match_ids), as_of, as_of),
    ).fetchall()


def _candidate_from_row(row, *, envelope: OddspapiTennisFixtureEnvelope, max_start_delta_minutes: int) -> OddspapiCanonicalCandidate | None:
    match_id, version_id, p1_id, p2_id, start_at, p1_name, p2_name = row
    provider_p1 = deterministic_name_keys(envelope.participant_1_name)
    provider_p2 = deterministic_name_keys(envelope.participant_2_name)
    canonical_p1 = deterministic_name_keys(str(p1_name))
    canonical_p2 = deterministic_name_keys(str(p2_name))
    direct = bool(provider_p1 & canonical_p1) and bool(provider_p2 & canonical_p2)
    reverse = bool(provider_p1 & canonical_p2) and bool(provider_p2 & canonical_p1)
    if direct == reverse:
        return None
    delta = int(abs((start_at - envelope.match_start_at).total_seconds()) // 60)
    if delta > max_start_delta_minutes:
        return None
    return OddspapiCanonicalCandidate(
        match_id=int(match_id),
        match_version_id=int(version_id),
        canonical_player_1_id=int(p1_id),
        canonical_player_2_id=int(p2_id),
        canonical_player_1_name=str(p1_name),
        canonical_player_2_name=str(p2_name),
        provider_orientation="DIRECT" if direct else "REVERSED",
        start_delta_minutes=delta,
    )


def _persist_event_mapping(conn, *, envelope: OddspapiTennisFixtureEnvelope, candidate: OddspapiCanonicalCandidate) -> None:
    current = _existing_mapping(conn, envelope.fixture_id)
    if current is not None:
        if current != candidate.match_id:
            raise OddspapiReconciliationError("oddspapi_event_identity_conflict")
        return
    conn.execute(
        """
        INSERT INTO match_source_ids (match_id, match_version_id, source_system, source_match_id, source_record_locator, source_file_name)
        VALUES (%s, %s, %s, %s, %s, 'oddspapi_v4')
        ON CONFLICT (source_system, source_match_id) WHERE source_match_id IS NOT NULL DO NOTHING
        """,
        (candidate.match_id, candidate.match_version_id, SOURCE_SYSTEM, envelope.fixture_id, f"oddspapi:fixture:{envelope.fixture_id}"),
    )
    if _existing_mapping(conn, envelope.fixture_id) != candidate.match_id:
        raise OddspapiReconciliationError("oddspapi_event_identity_conflict")


def _apply_identity_bridge(conn, *, envelope: OddspapiTennisFixtureEnvelope, candidate: OddspapiCanonicalCandidate) -> None:
    if candidate.provider_orientation == "DIRECT":
        bindings = ((envelope.participant_1_id, candidate.canonical_player_1_id), (envelope.participant_2_id, candidate.canonical_player_2_id))
    else:
        bindings = ((envelope.participant_1_id, candidate.canonical_player_2_id), (envelope.participant_2_id, candidate.canonical_player_1_id))
    for source_player_id, player_id in bindings:
        row = conn.execute(
            "SELECT player_id FROM player_source_ids WHERE source_system = %s AND source_player_id = %s",
            (SOURCE_SYSTEM, source_player_id),
        ).fetchone()
        if row is not None:
            if int(row[0]) != int(player_id):
                raise OddspapiReconciliationError("oddspapi_player_identity_conflict")
            continue
        conn.execute(
            "INSERT INTO player_source_ids (player_id, source_system, source_player_id) VALUES (%s, %s, %s)",
            (player_id, SOURCE_SYSTEM, source_player_id),
        )


__all__ = [
    "DEFAULT_MAX_START_DELTA_MINUTES",
    "OddspapiCanonicalCandidate",
    "OddspapiEventResolution",
    "OddspapiReconciliationError",
    "SOURCE_SYSTEM",
    "reconcile_oddspapi_event",
]
