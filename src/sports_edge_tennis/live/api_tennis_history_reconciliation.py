"""Deterministic API-Tennis historical event reconciliation.

This lane reconciles terminal API-Tennis provider events that were never linked to
an existing canonical match. It is deliberately narrow and fail-closed:

* it only ever *attaches* an API-Tennis ``source_match_id`` to an already-existing
  canonical ``match_id``; it never creates matches, versions, players, results or
  statistics;
* a link is only proposed when both provider player keys already carry an exact
  ``player_source_ids`` mapping (source system ``API_TENNIS``) to the same two
  canonical players as one, and only one, existing canonical match;
* tournament evidence may only *veto* a candidate, never invent one;
* no fuzzy player-name matching, no canonical merges, no threshold changes.

Everything is read-only unless an operator explicitly authorizes apply with both a
preview hash and an authorization token. Apply is append-only (``match_source_ids``
is insert-only) and idempotent: a second run is a no-op.

Point-in-time safety: the link has no effect on historical prediction state because
Agent 2 statistics only become PIT-visible at their actual receive time. Nothing here
rewrites match, result or decision truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg

from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    DEFAULT_CONFIG as A2_CONFIG,
)
from sports_edge_tennis.data.agent2_history import (
    build_history_snapshot,
    load_agent2_history,
)
from sports_edge_tennis.data.agent2_history import (
    historical_match_is_eligible_for_update as a2_match_is_eligible,
)
from sports_edge_tennis.data.agent3_history import (
    historical_match_is_eligible_for_update as a3_match_is_eligible,
)
from sports_edge_tennis.data.agent3_history import (
    load_agent3_history,
)
from sports_edge_tennis.data.sources.api_tennis_live import (
    SOURCE_SYSTEM,
    ApiTennisFixtureRecord,
    ApiTennisSourceAError,
    _parse_fixture,
)
from sports_edge_tennis.live.credentials import MissingCredentialError, load_secret
from sports_edge_tennis.live.history_refresh import fetch_api_tennis_fixture_window

RECONCILIATION_DATASET = "api_tennis_history_reconciliation"
RECONCILIATION_ADAPTER_VERSION = "API_TENNIS_HISTORY_RECONCILIATION_V1"
APPLY_AUTHORIZATION_TOKEN = "API_TENNIS_EXACT_EVENT_LINK_V1"
DEFAULT_MAX_START_DELTA_MINUTES = 120
MAX_AUDIT_EVENTS = 20000

# Deterministic taxonomy. Materially different causes are never collapsed.
ALREADY_LINKED = "ALREADY_LINKED"
DETERMINISTIC_MATCH_AVAILABLE_BUT_UNLINKED = "DETERMINISTIC_MATCH_AVAILABLE_BUT_UNLINKED"
CANONICAL_EVENT_MISSING = "CANONICAL_EVENT_MISSING"
MULTIPLE_CANONICAL_CANDIDATES = "MULTIPLE_CANONICAL_CANDIDATES"
TOURNAMENT_MISMATCH = "TOURNAMENT_MISMATCH"
TOURNAMENT_EDITION_MISMATCH = "TOURNAMENT_EDITION_MISMATCH"
DATE_TIME_MISMATCH = "DATE_TIME_MISMATCH"
PLAYER_IDENTITY_MISSING = "PLAYER_IDENTITY_MISSING"
PLAYER_IDENTITY_AMBIGUOUS = "PLAYER_IDENTITY_AMBIGUOUS"
DUPLICATE_PROVIDER_EVENT = "DUPLICATE_PROVIDER_EVENT"
UNSUPPORTED_SCOPE = "UNSUPPORTED_SCOPE"
PROVIDER_DATA_INCOMPLETE = "PROVIDER_DATA_INCOMPLETE"

TAXONOMY = (
    ALREADY_LINKED,
    DETERMINISTIC_MATCH_AVAILABLE_BUT_UNLINKED,
    CANONICAL_EVENT_MISSING,
    MULTIPLE_CANONICAL_CANDIDATES,
    TOURNAMENT_MISMATCH,
    TOURNAMENT_EDITION_MISMATCH,
    DATE_TIME_MISMATCH,
    PLAYER_IDENTITY_MISSING,
    PLAYER_IDENTITY_AMBIGUOUS,
    DUPLICATE_PROVIDER_EVENT,
    UNSUPPORTED_SCOPE,
    PROVIDER_DATA_INCOMPLETE,
)

_ORIENTATION_DIRECT = "DIRECT"
_ORIENTATION_REVERSED = "REVERSED"

_SCOPE_ERROR_MESSAGES = {
    "out_of_scope_event_type",
    "unsupported_event_type",
    "unsupported_event_status",
    "unsupported_competition_tier",
    "unsupported_best_of_sets",
}
_INCOMPLETE_ERROR_PREFIXES = ("missing_required_field:", "invalid_")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class CandidateMatch:
    match_id: int
    match_version_id: int
    player_1_id: int
    player_2_id: int
    match_start_at: datetime | None
    surface: str | None
    tournament_id: int
    tournament_edition_id: int
    season_year: int
    start_delta_minutes: int | None
    orientation: str | None


@dataclass(frozen=True)
class AuditRow:
    event_key: str
    event_date: str | None
    event_time: str | None
    event_status: str | None
    tournament_key: str | None
    tournament_name: str | None
    first_player_key: str | None
    second_player_key: str | None
    first_player_name: str | None
    second_player_name: str | None
    candidate_match_ids: tuple[int, ...]
    candidate_count: int
    time_delta_minutes: int | None
    tournament_evidence: str
    player_identity_evidence: str
    orientation: str | None
    surface_evidence: str | None
    classification: str
    deterministic_action_eligible: bool
    reason: str
    reason_safe: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "event_date": self.event_date,
            "event_time": self.event_time,
            "event_status": self.event_status,
            "tournament_key": self.tournament_key,
            "tournament_name": self.tournament_name,
            "first_player_key": self.first_player_key,
            "second_player_key": self.second_player_key,
            "first_player_name": self.first_player_name,
            "second_player_name": self.second_player_name,
            "candidate_match_ids": list(self.candidate_match_ids),
            "candidate_count": self.candidate_count,
            "time_delta_minutes": self.time_delta_minutes,
            "tournament_evidence": self.tournament_evidence,
            "player_identity_evidence": self.player_identity_evidence,
            "orientation": self.orientation,
            "surface_evidence": self.surface_evidence,
            "classification": self.classification,
            "deterministic_action_eligible": self.deterministic_action_eligible,
            "reason": self.reason,
            "reason_safe": self.reason_safe,
        }


@dataclass(frozen=True)
class LinkCandidate:
    event_key: str
    match_id: int
    match_version_id: int
    provider_first_player_id: int
    provider_second_player_id: int
    canonical_player_1_id: int
    canonical_player_2_id: int
    orientation: str
    start_delta_minutes: int | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "match_id": self.match_id,
            "match_version_id": self.match_version_id,
            "provider_first_player_id": self.provider_first_player_id,
            "provider_second_player_id": self.provider_second_player_id,
            "canonical_player_1_id": self.canonical_player_1_id,
            "canonical_player_2_id": self.canonical_player_2_id,
            "orientation": self.orientation,
            "start_delta_minutes": self.start_delta_minutes,
        }


@dataclass(frozen=True)
class ReconciliationPlan:
    as_of: datetime
    max_start_delta_minutes: int
    rows: tuple[AuditRow, ...]
    link_candidates: tuple[LinkCandidate, ...]
    total_events: int
    already_linked: int
    unmatched: int
    preview_sha256: str = ""

    @property
    def taxonomy_counts(self) -> tuple[tuple[str, int], ...]:
        counts = Counter(row.classification for row in self.rows)
        return tuple(sorted((name, counts.get(name, 0)) for name in TAXONOMY))

    def with_hash(self) -> ReconciliationPlan:
        return ReconciliationPlan(
            as_of=self.as_of,
            max_start_delta_minutes=self.max_start_delta_minutes,
            rows=self.rows,
            link_candidates=self.link_candidates,
            total_events=self.total_events,
            already_linked=self.already_linked,
            unmatched=self.unmatched,
            preview_sha256=_plan_hash(self),
        )


@dataclass(frozen=True)
class ApplyOutcome:
    ingest_run_id: int
    candidates_considered: int
    links_inserted: int
    links_existing: int
    status: str


def _plan_hash(plan: ReconciliationPlan) -> str:
    return candidate_hash(plan.link_candidates)


def candidate_hash(candidates: Iterable[LinkCandidate]) -> str:
    payload = [
        {
            "event_key": candidate.event_key,
            "match_id": candidate.match_id,
            "match_version_id": candidate.match_version_id,
            "orientation": candidate.orientation,
        }
        for candidate in sorted(candidates, key=lambda candidate: candidate.event_key)
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def link_candidates_from_payload(payload: Mapping[str, Any]) -> tuple[LinkCandidate, ...]:
    rows = payload.get("link_candidates")
    if not isinstance(rows, list):
        raise TypeError("reconciliation artifact is missing link_candidates")
    candidates: list[LinkCandidate] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("reconciliation artifact link candidate is malformed")
        candidates.append(
            LinkCandidate(
                event_key=str(row["event_key"]),
                match_id=int(row["match_id"]),
                match_version_id=int(row["match_version_id"]),
                provider_first_player_id=int(row["provider_first_player_id"]),
                provider_second_player_id=int(row["provider_second_player_id"]),
                canonical_player_1_id=int(row["canonical_player_1_id"]),
                canonical_player_2_id=int(row["canonical_player_2_id"]),
                orientation=str(row["orientation"]),
                start_delta_minutes=(
                    int(row["start_delta_minutes"])
                    if row.get("start_delta_minutes") is not None
                    else None
                ),
            )
        )
    return tuple(candidates)


def _provider_player_map(
    conn: psycopg.Connection,
    source_player_ids: Iterable[str],
) -> tuple[dict[str, int], set[str]]:
    ids = tuple(sorted({value for value in source_player_ids if value}))
    if not ids:
        return {}, set()
    rows = conn.execute(
        """
        SELECT source_player_id, player_id
        FROM player_source_ids
        WHERE source_system = %s
          AND source_player_id = ANY(%s)
        ORDER BY source_player_id, player_id
        """,
        (SOURCE_SYSTEM, list(ids)),
    ).fetchall()
    mapping: dict[str, int] = {}
    ambiguous: set[str] = set()
    for source_player_id, player_id in rows:
        key = str(source_player_id)
        value = int(player_id)
        if key in mapping and mapping[key] != value:
            ambiguous.add(key)
        else:
            mapping[key] = value
    for key in ambiguous:
        mapping.pop(key, None)
    return mapping, ambiguous


def _provider_tournament_map(
    conn: psycopg.Connection,
    source_tournament_ids: Iterable[str],
) -> dict[str, int]:
    ids = tuple(sorted({value for value in source_tournament_ids if value}))
    if not ids:
        return {}
    rows = conn.execute(
        """
        SELECT source_tournament_id, tournament_id
        FROM tournament_source_ids
        WHERE source_system = %s
          AND source_tournament_id = ANY(%s)
        """,
        (SOURCE_SYSTEM, list(ids)),
    ).fetchall()
    return {str(source): int(tournament_id) for source, tournament_id in rows}


def _existing_event_map(
    conn: psycopg.Connection,
    source_match_ids: Iterable[str],
) -> dict[str, set[int]]:
    ids = tuple(sorted({value for value in source_match_ids if value}))
    if not ids:
        return {}
    rows = conn.execute(
        """
        SELECT source_match_id, match_id
        FROM match_source_ids
        WHERE source_system = %s
          AND source_match_id = ANY(%s)
        """,
        (SOURCE_SYSTEM, list(ids)),
    ).fetchall()
    result: dict[str, set[int]] = {}
    for source_match_id, match_id in rows:
        result.setdefault(str(source_match_id), set()).add(int(match_id))
    return result


def _canonical_pair_candidates(
    conn: psycopg.Connection,
    *,
    player_1_id: int,
    player_2_id: int,
) -> list[CandidateMatch]:
    """Latest canonical match identities for one exact player pair (both orientations)."""
    if player_1_id == player_2_id:
        return []
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id,
                mv.match_version_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.match_start_at,
                mv.surface::text AS surface
            FROM match_versions mv
            WHERE (mv.player_1_id = %(p1)s AND mv.player_2_id = %(p2)s)
               OR (mv.player_1_id = %(p2)s AND mv.player_2_id = %(p1)s)
            ORDER BY
                mv.match_id,
                mv.as_of DESC,
                mv.source_observed_at DESC,
                mv.match_version_id DESC
        )
        SELECT
            latest.match_id,
            latest.match_version_id,
            latest.player_1_id,
            latest.player_2_id,
            latest.match_start_at,
            latest.surface,
            m.tournament_edition_id,
            te.tournament_id,
            te.season_year,
            te.surface::text
        FROM latest
        JOIN matches m ON m.match_id = latest.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        ORDER BY latest.match_id
        """,
        {"p1": player_1_id, "p2": player_2_id},
    ).fetchall()
    candidates: list[CandidateMatch] = []
    for row in rows:
        (
            match_id,
            match_version_id,
            version_p1,
            version_p2,
            match_start_at,
            version_surface,
            edition_id,
            tournament_id,
            season_year,
            edition_surface,
        ) = row
        candidates.append(
            CandidateMatch(
                match_id=int(match_id),
                match_version_id=int(match_version_id),
                player_1_id=int(version_p1),
                player_2_id=int(version_p2),
                match_start_at=match_start_at,
                surface=version_surface or edition_surface,
                tournament_id=int(tournament_id),
                tournament_edition_id=int(edition_id),
                season_year=int(season_year),
                start_delta_minutes=None,
                orientation="PENDING",
            )
        )
    return candidates


def _with_time_delta(
    candidate: CandidateMatch,
    *,
    provider_start_at: datetime | None,
    provider_first_player_id: int,
) -> CandidateMatch:
    delta: int | None = None
    if candidate.match_start_at is not None and provider_start_at is not None:
        delta = int(
            abs((candidate.match_start_at - provider_start_at).total_seconds()) // 60
        )
    orientation = (
        _ORIENTATION_DIRECT
        if candidate.player_1_id == provider_first_player_id
        else _ORIENTATION_REVERSED
    )
    return CandidateMatch(
        match_id=candidate.match_id,
        match_version_id=candidate.match_version_id,
        player_1_id=candidate.player_1_id,
        player_2_id=candidate.player_2_id,
        match_start_at=candidate.match_start_at,
        surface=candidate.surface,
        tournament_id=candidate.tournament_id,
        tournament_edition_id=candidate.tournament_edition_id,
        season_year=candidate.season_year,
        start_delta_minutes=delta,
        orientation=orientation,
    )


def _parse_records(
    fixtures: list[dict[str, Any]],
    *,
    as_of: datetime,
) -> tuple[dict[str, ApiTennisFixtureRecord], dict[str, str], dict[str, set[str]]]:
    records: dict[str, ApiTennisFixtureRecord] = {}
    failures: dict[str, str] = {}
    key_hashes: dict[str, set[str]] = {}
    for row_number, fixture in enumerate(fixtures, start=1):
        event_key = _text(fixture.get("event_key")) or f"__row_{row_number}__"
        try:
            record = _parse_fixture(fixture, row_number=row_number, received_at=as_of)
        except ApiTennisSourceAError as exc:
            failures[event_key] = str(exc)
            continue
        if event_key not in records:
            records[event_key] = record
        key_hashes.setdefault(event_key, set()).add(record.source_record_hash)
    return records, failures, key_hashes


def _classify_parse_failure(message: str) -> tuple[str, str]:
    if message in _SCOPE_ERROR_MESSAGES:
        return UNSUPPORTED_SCOPE, f"provider_scope_rejected:{message}"
    if message.startswith("duplicate_player_source_id"):
        return PROVIDER_DATA_INCOMPLETE, "provider_reports_identical_participants"
    if message.startswith(_INCOMPLETE_ERROR_PREFIXES):
        return PROVIDER_DATA_INCOMPLETE, f"provider_row_incomplete:{message}"
    return PROVIDER_DATA_INCOMPLETE, f"provider_row_rejected:{message}"


def build_reconciliation_plan(
    conn: psycopg.Connection,
    fixtures: list[Mapping[str, Any]],
    *,
    as_of: datetime,
    max_start_delta_minutes: int = DEFAULT_MAX_START_DELTA_MINUTES,
) -> ReconciliationPlan:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if max_start_delta_minutes < 0:
        raise ValueError("max_start_delta_minutes must be non-negative")
    fixture_list = [dict(fixture) for fixture in fixtures]

    records, failures, key_hashes = _parse_records(fixture_list, as_of=as_of)
    duplicate_keys = {
        event_key for event_key, hashes in key_hashes.items() if len(hashes) > 1
    }

    event_ids = list(records.keys())
    existing_map = _existing_event_map(conn, event_ids)
    player_map, ambiguous_players = _provider_player_map(
        conn,
        (
            player_key
            for record in records.values()
            for player_key in (
                record.first_player_source_id,
                record.second_player_source_id,
            )
        ),
    )
    tournament_map = _provider_tournament_map(
        conn, (record.tournament_source_id for record in records.values())
    )

    rows: list[AuditRow] = []
    link_candidates: list[LinkCandidate] = []

    for event_key, record in records.items():
        if event_key in duplicate_keys:
            rows.append(
                _blocked_row(
                    record,
                    classification=DUPLICATE_PROVIDER_EVENT,
                    reason="same_event_key_present_with_conflicting_payload",
                    candidate_ids=(),
                    delta=None,
                    tournament_evidence="",
                    identity_evidence="",
                )
            )
            continue

        if event_key in existing_map:
            linked_ids = tuple(sorted(existing_map[event_key]))
            rows.append(
                _blocked_row(
                    record,
                    classification=ALREADY_LINKED,
                    reason="provider_event_already_linked",
                    candidate_ids=linked_ids,
                    delta=None,
                    tournament_evidence="",
                    identity_evidence="",
                    eligible=False,
                )
            )
            continue

        first_key = record.first_player_source_id
        second_key = record.second_player_source_id
        if first_key in ambiguous_players or second_key in ambiguous_players:
            rows.append(
                _blocked_row(
                    record,
                    classification=PLAYER_IDENTITY_AMBIGUOUS,
                    reason="provider_player_key_maps_to_multiple_canonical_players",
                    candidate_ids=(),
                    delta=None,
                    tournament_evidence="",
                    identity_evidence="ambiguous_provider_mapping",
                )
            )
            continue
        provider_p1 = player_map.get(first_key)
        provider_p2 = player_map.get(second_key)
        if provider_p1 is None or provider_p2 is None:
            missing = []
            if provider_p1 is None:
                missing.append(first_key)
            if provider_p2 is None:
                missing.append(second_key)
            rows.append(
                _blocked_row(
                    record,
                    classification=PLAYER_IDENTITY_MISSING,
                    reason=f"provider_player_keys_without_exact_mapping:{','.join(sorted(missing))}",
                    candidate_ids=(),
                    delta=None,
                    tournament_evidence="",
                    identity_evidence="no_exact_api_tennis_player_source_mapping",
                )
            )
            continue
        if provider_p1 == provider_p2:
            rows.append(
                _blocked_row(
                    record,
                    classification=PLAYER_IDENTITY_AMBIGUOUS,
                    reason="provider_player_keys_resolve_to_same_canonical_player",
                    candidate_ids=(),
                    delta=None,
                    tournament_evidence="",
                    identity_evidence="collapsed_provider_identity",
                )
            )
            continue

        candidates = _canonical_pair_candidates(
            conn, player_1_id=provider_p1, player_2_id=provider_p2
        )
        candidates = [
            _with_time_delta(
                candidate,
                provider_start_at=record.match_start_at,
                provider_first_player_id=provider_p1,
            )
            for candidate in candidates
        ]
        provider_tid = tournament_map.get(record.tournament_source_id)

        row, candidate = _classify_linked_event(
            record,
            candidates=candidates,
            provider_tournament_id=provider_tid,
            max_start_delta_minutes=max_start_delta_minutes,
            identity_evidence=(
                f"api_tennis:{record.first_player_source_id}->player:{provider_p1} "
                f"api_tennis:{record.second_player_source_id}->player:{provider_p2}"
            ),
        )
        rows.append(row)
        if candidate is not None:
            link_candidates.append(
                LinkCandidate(
                    event_key=event_key,
                    match_id=candidate.match_id,
                    match_version_id=candidate.match_version_id,
                    provider_first_player_id=provider_p1,
                    provider_second_player_id=provider_p2,
                    canonical_player_1_id=candidate.player_1_id,
                    canonical_player_2_id=candidate.player_2_id,
                    orientation=candidate.orientation or _ORIENTATION_DIRECT,
                    start_delta_minutes=candidate.start_delta_minutes,
                )
            )

    for event_key, message in failures.items():
        if event_key in records:
            continue
        classification, reason = _classify_parse_failure(message)
        rows.append(
            AuditRow(
                event_key=event_key,
                event_date=_text(_fixture_field(fixture_list, event_key, "event_date")),
                event_time=_text(_fixture_field(fixture_list, event_key, "event_time")),
                event_status=_text(_fixture_field(fixture_list, event_key, "event_status")),
                tournament_key=_text(_fixture_field(fixture_list, event_key, "tournament_key")),
                tournament_name=_text(_fixture_field(fixture_list, event_key, "tournament_name")),
                first_player_key=_text(
                    _fixture_field(fixture_list, event_key, "first_player_key")
                ),
                second_player_key=_text(
                    _fixture_field(fixture_list, event_key, "second_player_key")
                ),
                first_player_name=_text(
                    _fixture_field(fixture_list, event_key, "event_first_player")
                ),
                second_player_name=_text(
                    _fixture_field(fixture_list, event_key, "event_second_player")
                ),
                candidate_match_ids=(),
                candidate_count=0,
                time_delta_minutes=None,
                tournament_evidence="",
                player_identity_evidence="",
                orientation=None,
                surface_evidence=None,
                classification=classification,
                deterministic_action_eligible=False,
                reason=reason,
                reason_safe=False,
            )
        )

    rows.sort(key=lambda row: row.event_key)
    link_candidates.sort(key=lambda candidate: candidate.event_key)
    already_linked = sum(1 for row in rows if row.classification == ALREADY_LINKED)
    plan = ReconciliationPlan(
        as_of=as_of,
        max_start_delta_minutes=max_start_delta_minutes,
        rows=tuple(rows),
        link_candidates=tuple(link_candidates),
        total_events=len(rows),
        already_linked=already_linked,
        unmatched=len(rows) - already_linked,
    )
    return plan.with_hash()


def _fixture_field(
    fixtures: list[dict[str, Any]], event_key: str, field: str
) -> Any:
    for fixture in fixtures:
        if _text(fixture.get("event_key")) == event_key:
            return fixture.get(field)
    return None


def _classify_linked_event(
    record: ApiTennisFixtureRecord,
    *,
    candidates: list[CandidateMatch],
    provider_tournament_id: int | None,
    max_start_delta_minutes: int,
    identity_evidence: str,
) -> tuple[AuditRow, CandidateMatch | None]:
    within_time = [
        candidate
        for candidate in candidates
        if candidate.start_delta_minutes is not None
        and candidate.start_delta_minutes <= max_start_delta_minutes
    ]
    consistent = [
        candidate
        for candidate in within_time
        if _tournament_consistent(
            candidate,
            record=record,
            provider_tournament_id=provider_tournament_id,
        )
    ]
    candidate_ids = tuple(sorted({candidate.match_id for candidate in consistent}))
    tournament_evidence = _tournament_evidence_text(
        record,
        provider_tournament_id=provider_tournament_id,
        candidates=within_time,
    )

    if len(consistent) == 1:
        candidate = consistent[0]
        row = AuditRow(
            event_key=record.source_match_id,
            event_date=record.match_start_at.date().isoformat(),
            event_time=record.match_start_at.strftime("%H:%M"),
            event_status=record.match_status,
            tournament_key=record.tournament_source_id,
            tournament_name=record.tournament_name,
            first_player_key=record.first_player_source_id,
            second_player_key=record.second_player_source_id,
            first_player_name=record.first_player_name,
            second_player_name=record.second_player_name,
            candidate_match_ids=candidate_ids,
            candidate_count=1,
            time_delta_minutes=candidate.start_delta_minutes,
            tournament_evidence=tournament_evidence,
            player_identity_evidence=identity_evidence,
            orientation=candidate.orientation,
            surface_evidence=candidate.surface,
            classification=DETERMINISTIC_MATCH_AVAILABLE_BUT_UNLINKED,
            deterministic_action_eligible=True,
            reason="single_exact_player_pair_and_start_time_candidate",
            reason_safe=True,
        )
        return row, candidate

    if len(within_time) > 1 and not consistent:
        return (
            _blocked_row(
                record,
                classification=MULTIPLE_CANONICAL_CANDIDATES,
                reason="multiple_tournament_consistent_candidates"
                if len(consistent) > 1
                else "multiple_time_candidates",
                candidate_ids=tuple(
                    sorted({candidate.match_id for candidate in within_time})
                ),
                delta=min(
                    candidate.start_delta_minutes
                    for candidate in within_time
                    if candidate.start_delta_minutes is not None
                ),
                tournament_evidence=tournament_evidence,
                identity_evidence=identity_evidence,
            ),
            None,
        )

    if len(consistent) > 1:
        return (
            _blocked_row(
                record,
                classification=MULTIPLE_CANONICAL_CANDIDATES,
                reason="multiple_tournament_consistent_candidates",
                candidate_ids=candidate_ids,
                delta=min(
                    candidate.start_delta_minutes
                    for candidate in consistent
                    if candidate.start_delta_minutes is not None
                ),
                tournament_evidence=tournament_evidence,
                identity_evidence=identity_evidence,
            ),
            None,
        )

    if within_time:
        # Time matches but tournament evidence vetoes every candidate.
        edition_only = all(
            provider_tournament_id is not None
            and candidate.tournament_id == provider_tournament_id
            and candidate.season_year != record.match_start_at.year
            for candidate in within_time
        )
        classification = (
            TOURNAMENT_EDITION_MISMATCH if edition_only else TOURNAMENT_MISMATCH
        )
        return (
            _blocked_row(
                record,
                classification=classification,
                reason="tournament_evidence_conflicts_with_time_candidate",
                candidate_ids=tuple(
                    sorted({candidate.match_id for candidate in within_time})
                ),
                delta=min(
                    candidate.start_delta_minutes
                    for candidate in within_time
                    if candidate.start_delta_minutes is not None
                ),
                tournament_evidence=tournament_evidence,
                identity_evidence=identity_evidence,
            ),
            None,
        )

    if candidates:
        return (
            _blocked_row(
                record,
                classification=DATE_TIME_MISMATCH,
                reason="canonical_player_pair_exists_outside_start_time_tolerance",
                candidate_ids=tuple(
                    sorted({candidate.match_id for candidate in candidates})
                ),
                delta=min(
                    (
                        candidate.start_delta_minutes
                        for candidate in candidates
                        if candidate.start_delta_minutes is not None
                    ),
                    default=None,
                ),
                tournament_evidence=tournament_evidence,
                identity_evidence=identity_evidence,
            ),
            None,
        )

    return (
        _blocked_row(
            record,
            classification=CANONICAL_EVENT_MISSING,
            reason="no_canonical_match_for_exact_player_pair",
            candidate_ids=(),
            delta=None,
            tournament_evidence=tournament_evidence,
            identity_evidence=identity_evidence,
        ),
        None,
    )


def _tournament_consistent(
    candidate: CandidateMatch,
    *,
    record: ApiTennisFixtureRecord,
    provider_tournament_id: int | None,
) -> bool:
    if provider_tournament_id is None:
        return True
    if candidate.tournament_id != provider_tournament_id:
        return False
    return candidate.season_year == record.match_start_at.year


def _tournament_evidence_text(
    record: ApiTennisFixtureRecord,
    *,
    provider_tournament_id: int | None,
    candidates: list[CandidateMatch],
) -> str:
    if provider_tournament_id is None:
        return f"api_tennis:{record.tournament_source_id}->unmapped"
    candidate_editions = sorted(
        {(candidate.tournament_id, candidate.season_year) for candidate in candidates}
    )
    return (
        f"api_tennis:{record.tournament_source_id}->tournament:{provider_tournament_id} "
        f"season:{record.match_start_at.year} candidates={candidate_editions}"
    )


def _blocked_row(
    record: ApiTennisFixtureRecord,
    *,
    classification: str,
    reason: str,
    candidate_ids: tuple[int, ...],
    delta: int | None,
    tournament_evidence: str,
    identity_evidence: str,
    eligible: bool = False,
) -> AuditRow:
    return AuditRow(
        event_key=record.source_match_id,
        event_date=record.match_start_at.date().isoformat(),
        event_time=record.match_start_at.strftime("%H:%M"),
        event_status=record.match_status,
        tournament_key=record.tournament_source_id,
        tournament_name=record.tournament_name,
        first_player_key=record.first_player_source_id,
        second_player_key=record.second_player_source_id,
        first_player_name=record.first_player_name,
        second_player_name=record.second_player_name,
        candidate_match_ids=candidate_ids,
        candidate_count=len(candidate_ids),
        time_delta_minutes=delta,
        tournament_evidence=tournament_evidence,
        player_identity_evidence=identity_evidence,
        orientation=None,
        surface_evidence=None,
        classification=classification,
        deterministic_action_eligible=eligible,
        reason=reason,
        reason_safe=False,
    )


def build_audit_artifact(
    plan: ReconciliationPlan,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(UTC)
    if generated.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    return {
        "artifact": "API_TENNIS_HISTORY_RECONCILIATION_AUDIT",
        "adapter_version": RECONCILIATION_ADAPTER_VERSION,
        "generated_at": generated.astimezone(UTC).isoformat(),
        "as_of": plan.as_of.astimezone(UTC).isoformat(),
        "max_start_delta_minutes": plan.max_start_delta_minutes,
        "source_system": SOURCE_SYSTEM,
        "totals": {
            "events": plan.total_events,
            "already_linked": plan.already_linked,
            "unmatched": plan.unmatched,
            "deterministically_eligible": len(plan.link_candidates),
        },
        "taxonomy": {name: count for name, count in plan.taxonomy_counts},
        "preview_sha256": plan.preview_sha256,
        "link_candidates": [candidate.to_payload() for candidate in plan.link_candidates],
        "rows": [row.to_payload() for row in plan.rows],
    }


def write_audit_artifact(path: Path, artifact: Mapping[str, Any]) -> str:
    encoded = json.dumps(artifact, sort_keys=True, indent=2, default=str)
    payload_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded + "\n", encoding="utf-8")
    return payload_hash


def apply_reconciliation(
    conn: psycopg.Connection,
    *,
    plan: ReconciliationPlan,
    authorization: str,
    expected_sha256: str,
    applied_at: datetime,
) -> ApplyOutcome:
    """Append-only, idempotent linking of a plan's deterministic candidates."""
    return apply_candidate_links(
        conn,
        candidates=plan.link_candidates,
        authorization=authorization,
        expected_sha256=expected_sha256,
        applied_at=applied_at,
    )


def apply_candidate_links(
    conn: psycopg.Connection,
    *,
    candidates: Iterable[LinkCandidate],
    authorization: str,
    expected_sha256: str,
    applied_at: datetime,
) -> ApplyOutcome:
    """Append-only, idempotent linking of frozen deterministic candidates.

    The preview hash binds the apply to an exact captured candidate set. Re-running
    the same captured set is a no-op because every event is already linked.
    """
    if applied_at.tzinfo is None:
        raise ValueError("applied_at must be timezone-aware")
    if authorization != APPLY_AUTHORIZATION_TOKEN:
        raise ValueError("reconciliation apply authorization token mismatch")
    candidate_list = tuple(candidates)
    if expected_sha256 != candidate_hash(candidate_list):
        raise ValueError("reconciliation apply preview hash mismatch")

    ingest_run_id = conn.execute(
        """
        INSERT INTO ingest_runs (
            source_system, source_dataset, started_at, status, source_version
        ) VALUES (%s, %s, %s, 'RUNNING', %s)
        RETURNING ingest_run_id
        """,
        (
            SOURCE_SYSTEM,
            RECONCILIATION_DATASET,
            applied_at,
            RECONCILIATION_ADAPTER_VERSION,
        ),
    ).fetchone()[0]

    inserted = 0
    existing = 0
    for candidate in candidate_list:
        row = conn.execute(
            """
            SELECT match_id
            FROM match_source_ids
            WHERE source_system = %s
              AND source_match_id = %s
            FOR UPDATE
            """,
            (SOURCE_SYSTEM, candidate.event_key),
        ).fetchone()
        if row is not None:
            if int(row[0]) != candidate.match_id:
                raise ValueError(
                    "reconciliation event source id conflicts with canonical match"
                )
            existing += 1
            continue
        conn.execute(
            """
            INSERT INTO match_source_ids (
                match_id, match_version_id, source_system, source_match_id, ingest_run_id
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                candidate.match_id,
                candidate.match_version_id,
                SOURCE_SYSTEM,
                candidate.event_key,
                ingest_run_id,
            ),
        )
        inserted += 1

    status = "COMPLETED" if not existing else "PARTIAL"
    if inserted == 0 and existing == 0:
        status = "COMPLETED"
    conn.execute(
        """
        UPDATE ingest_runs
        SET finished_at = %s,
            status = %s,
            rows_seen = %s,
            rows_inserted = %s,
            rows_rejected = 0,
            error_summary = '[]'::jsonb
        WHERE ingest_run_id = %s
        """,
        (applied_at, status, len(candidate_list), inserted, ingest_run_id),
    )
    return ApplyOutcome(
        ingest_run_id=int(ingest_run_id),
        candidates_considered=len(candidate_list),
        links_inserted=inserted,
        links_existing=existing,
        status=status,
    )


def measure_history_coverage(
    conn: psycopg.Connection,
    *,
    cutoff: datetime,
) -> dict[str, Any]:
    """Read-only data-support measurement for A2/A3 (no scoring or gate changes)."""
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must be timezone-aware")

    canonical_matches = int(
        conn.execute("SELECT count(DISTINCT match_id) FROM match_versions").fetchone()[0]
    )
    linked_api_tennis = int(
        conn.execute(
            """
            SELECT count(DISTINCT match_id)
            FROM match_source_ids
            WHERE source_system = %s
            """,
            (SOURCE_SYSTEM,),
        ).fetchone()[0]
    )
    known_surface_versions = int(
        conn.execute(
            "SELECT count(*) FROM match_versions WHERE surface IS NOT NULL"
        ).fetchone()[0]
    )
    unknown_surface_versions = int(
        conn.execute(
            "SELECT count(*) FROM match_versions WHERE surface IS NULL"
        ).fetchone()[0]
    )

    a2_history = load_agent2_history(conn, cutoff=cutoff)
    a2_eligible = sum(1 for observation in a2_history if a2_match_is_eligible(observation))
    a2_unknown_surface = sum(1 for observation in a2_history if observation.surface is None)
    a2_snapshot = build_history_snapshot(a2_history, cutoff=cutoff)
    a2_players_meeting_thresholds = sum(
        1
        for snapshot in a2_snapshot.players.values()
        if snapshot.prior_usable_matches >= A2_CONFIG.minimum_prior_usable_matches
        and snapshot.overall_serve_points_played >= A2_CONFIG.minimum_prior_service_points
        and snapshot.overall_return_points_played >= A2_CONFIG.minimum_prior_return_points
    )

    a3_history = load_agent3_history(conn, cutoff=cutoff)
    a3_eligible = sum(1 for observation in a3_history if a3_match_is_eligible(observation))
    a3_pairs: set[tuple[int, int]] = set()
    a3_opponents: dict[int, set[int]] = {}
    for observation in a3_history:
        if not a3_match_is_eligible(observation):
            continue
        pair = tuple(sorted((observation.player_1_id, observation.player_2_id)))
        a3_pairs.add(pair)
        a3_opponents.setdefault(observation.player_1_id, set()).add(observation.player_2_id)
        a3_opponents.setdefault(observation.player_2_id, set()).add(observation.player_1_id)
    a3_players_with_direct_support = sum(1 for value in a3_opponents.values() if value)
    shared_opponent_pairs = 0
    opponent_sets = list(a3_opponents.items())
    for index, (player_a, opponents_a) in enumerate(opponent_sets):
        for player_b, opponents_b in opponent_sets[index + 1 :]:
            if opponents_a & opponents_b:
                shared_opponent_pairs += 1

    return {
        "canonical_matches": canonical_matches,
        "api_tennis_linked_matches": linked_api_tennis,
        "known_surface_versions": known_surface_versions,
        "unknown_surface_versions": unknown_surface_versions,
        "a2_history_rows": len(a2_history),
        "a2_eligible_matches": a2_eligible,
        "a2_unknown_surface_matches": a2_unknown_surface,
        "a2_players_meeting_thresholds": a2_players_meeting_thresholds,
        "a3_history_rows": len(a3_history),
        "a3_eligible_matches": a3_eligible,
        "a3_pairs_with_direct_support": len(a3_pairs),
        "a3_players_with_direct_support": a3_players_with_direct_support,
        "a3_pairs_with_shared_opponent_support": shared_opponent_pairs,
    }


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic API-Tennis historical event reconciliation audit"
    )
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--max-start-delta-minutes", type=int, default=DEFAULT_MAX_START_DELTA_MINUTES
    )
    parser.add_argument("--artifact", type=Path)
    parser.add_argument(
        "--fixtures-file",
        type=Path,
        help="Offline captured get_fixtures payload (list or {'result': [...]})",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--from-artifact", type=Path)
    parser.add_argument("--authorization")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--dsn", default=_db_dsn())
    args = parser.parse_args(argv)

    if args.end_date < args.start_date:
        print("API_TENNIS_RECONCILIATION_BLOCKED reason=INVALID_DATE_RANGE", flush=True)
        return 2
    if not args.dsn:
        print("API_TENNIS_RECONCILIATION_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    if args.apply and not args.from_artifact:
        print("API_TENNIS_RECONCILIATION_BLOCKED reason=APPLY_REQUIRES_ARTIFACT", flush=True)
        return 2

    as_of = datetime.now(UTC)
    if args.fixtures_file:
        try:
            payload = json.loads(args.fixtures_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(
                "API_TENNIS_RECONCILIATION_BLOCKED "
                f"reason=FIXTURES_FILE_{type(exc).__name__}",
                flush=True,
            )
            return 2
        if isinstance(payload, Mapping):
            payload = payload.get("result", [])
        if not isinstance(payload, list):
            print("API_TENNIS_RECONCILIATION_BLOCKED reason=FIXTURES_FILE_FORMAT", flush=True)
            return 2
        fixtures = list(payload)
        received_at = as_of
    else:
        try:
            api_key = load_secret("API_TENNIS_API_KEY")
        except MissingCredentialError:
            api_key = None
        if not api_key:
            print("API_TENNIS_RECONCILIATION_BLOCKED reason=NO_API_TENNIS_KEY", flush=True)
            return 2
        received_at, fixtures = fetch_api_tennis_fixture_window(
            api_key, args.start_date, args.end_date
        )
    if len(fixtures) > MAX_AUDIT_EVENTS:
        print(
            "API_TENNIS_RECONCILIATION_BLOCKED reason=EVENT_LIMIT_EXCEEDED",
            flush=True,
        )
        return 2

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        plan = build_reconciliation_plan(
            conn,
            fixtures,
            as_of=received_at or as_of,
            max_start_delta_minutes=args.max_start_delta_minutes,
        )
        artifact = build_audit_artifact(plan, generated_at=as_of)
        if args.artifact:
            payload_hash = write_audit_artifact(args.artifact, artifact)
            print(
                "API_TENNIS_RECONCILIATION_ARTIFACT "
                f"path={args.artifact} sha256={payload_hash}",
                flush=True,
            )
        if args.apply:
            try:
                frozen = json.loads(args.from_artifact.read_text(encoding="utf-8"))
                frozen_candidates = link_candidates_from_payload(frozen)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                print(
                    "API_TENNIS_RECONCILIATION_BLOCKED "
                    f"reason=ARTIFACT_{type(exc).__name__}",
                    flush=True,
                )
                return 2
            if frozen.get("preview_sha256") != candidate_hash(frozen_candidates):
                print(
                    "API_TENNIS_RECONCILIATION_BLOCKED reason=ARTIFACT_HASH_MISMATCH",
                    flush=True,
                )
                return 2
            outcome = apply_candidate_links(
                conn,
                candidates=frozen_candidates,
                authorization=args.authorization or "",
                expected_sha256=args.expected_sha256 or frozen.get("preview_sha256", ""),
                applied_at=as_of,
            )
            print(
                "API_TENNIS_RECONCILIATION_APPLY "
                f"status={outcome.status} inserted={outcome.links_inserted} "
                f"existing={outcome.links_existing}",
                flush=True,
            )

    print(
        "API_TENNIS_RECONCILIATION_AUDIT "
        f"events={plan.total_events} already_linked={plan.already_linked} "
        f"unmatched={plan.unmatched} eligible={len(plan.link_candidates)} "
        f"preview_sha256={plan.preview_sha256}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
