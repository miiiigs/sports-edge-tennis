"""Guarded one-shot model identity bridge from exact Sportradar evidence.

OFF by default. This does not merge canonical players or rewrite source identity.
It appends one PIT-aware model bridge event after re-validating exact provider linkage
and one unique historical candidate.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.data.model_identity import has_non_api_source_identity
from sports_edge_tennis.data.model_identity_bridge import (
    append_bridge,
    ensure_schema,
    latest_bridge_event,
)
from sports_edge_tennis.live.sportradar_benchmark import (
    CanonicalGapTarget,
    SportradarLinkCandidate,
    provider_name_identity_evidence,
)


ENABLED_ENV = "SPORTRADAR_IDENTITY_BRIDGE_APPLY_ENABLED"
AUTH_ENV = "SPORTRADAR_IDENTITY_BRIDGE_APPLY_AUTHORIZATION"
AUTH_TOKEN = "SPORTRADAR_EXACT_MODEL_IDENTITY_BRIDGE_V1"
MATCH_ID_ENV = "SPORTRADAR_IDENTITY_BRIDGE_APPLY_MATCH_ID"
LIVE_PLAYER_ID_ENV = "SPORTRADAR_IDENTITY_BRIDGE_APPLY_LIVE_PLAYER_ID"
HISTORICAL_PLAYER_ID_ENV = "SPORTRADAR_IDENTITY_BRIDGE_APPLY_HISTORICAL_PLAYER_ID"
EVENT_ID_ENV = "SPORTRADAR_IDENTITY_BRIDGE_APPLY_EVENT_ID"
PROVIDER_PLAYER_ID_ENV = "SPORTRADAR_IDENTITY_BRIDGE_APPLY_PROVIDER_PLAYER_ID"
ACTOR_ENV = "SPORTRADAR_IDENTITY_BRIDGE_APPLY_ACTOR"
REASON_ENV = "SPORTRADAR_IDENTITY_BRIDGE_APPLY_REASON"


def _text(value: object) -> str:
    return str(value or "").strip()


def _flag_enabled(value: object) -> bool:
    return _text(value).casefold() in {"1", "true", "yes", "on"}


def _required_int(values: Mapping[str, str], key: str) -> int:
    raw = _text(values.get(key))
    if not raw:
        raise ValueError(f"{key} is required")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _select_exact_link(
    exact_links: Sequence[tuple[CanonicalGapTarget, SportradarLinkCandidate]],
    *,
    match_id: int,
    event_id: str,
) -> tuple[CanonicalGapTarget, SportradarLinkCandidate]:
    matches = [
        (target, candidate)
        for target, candidate in exact_links
        if target.match_id == match_id and candidate.event_id == event_id
    ]
    if len(matches) != 1:
        raise ValueError(
            "Sportradar identity bridge requires exactly one current exact link "
            f"for match={match_id} event={event_id}; found={len(matches)}"
        )
    return matches[0]


def run_optional_identity_bridge(
    *,
    dsn: str | None,
    exact_links: Sequence[tuple[CanonicalGapTarget, SportradarLinkCandidate]],
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> int:
    values = os.environ if env is None else env
    if not _flag_enabled(values.get(ENABLED_ENV)):
        return 0
    if not dsn:
        raise ValueError("Sportradar identity bridge requires database connection")
    if _text(values.get(AUTH_ENV)) != AUTH_TOKEN:
        raise ValueError("Sportradar identity bridge authorization token mismatch")

    match_id = _required_int(values, MATCH_ID_ENV)
    live_player_id = _required_int(values, LIVE_PLAYER_ID_ENV)
    historical_player_id = _required_int(values, HISTORICAL_PLAYER_ID_ENV)
    event_id = _text(values.get(EVENT_ID_ENV))
    provider_player_id = _text(values.get(PROVIDER_PLAYER_ID_ENV))
    actor = _text(values.get(ACTOR_ENV))
    reason = _text(values.get(REASON_ENV))
    if not event_id or not provider_player_id:
        raise ValueError("Sportradar identity bridge event/player IDs are required")
    if not actor or not reason:
        raise ValueError("Sportradar identity bridge actor and reason are required")

    target, candidate = _select_exact_link(
        exact_links,
        match_id=match_id,
        event_id=event_id,
    )

    if target.player_1_id == live_player_id:
        live_status = target.player_1_identity_status
        candidate_provider_id = candidate.player_1_sportradar_id
        provider_name = candidate.player_1_sportradar_name
    elif target.player_2_id == live_player_id:
        live_status = target.player_2_identity_status
        candidate_provider_id = candidate.player_2_sportradar_id
        provider_name = candidate.player_2_sportradar_name
    else:
        raise ValueError(
            f"Sportradar identity bridge live player {live_player_id} "
            f"is not a participant in match {match_id}"
        )

    if live_status != "LIVE_UNRESOLVED":
        raise ValueError(
            "Sportradar identity bridge only accepts LIVE_UNRESOLVED players "
            f"status={live_status}"
        )
    if candidate_provider_id != provider_player_id:
        raise ValueError(
            "Sportradar identity bridge provider competitor mismatch "
            f"expected={provider_player_id} current={candidate_provider_id}"
        )
    if not provider_name:
        raise ValueError("Sportradar identity bridge provider name is missing")

    activated_at = now or datetime.now(UTC)
    with psycopg.connect(dsn, autocommit=False) as conn:
        schema_status = ensure_schema(conn)

        if has_non_api_source_identity(conn, live_player_id):
            raise ValueError(
                "Sportradar identity bridge refuses a live player that already has "
                "non-API historical identity"
            )
        if not has_non_api_source_identity(conn, historical_player_id):
            raise ValueError(
                "Sportradar identity bridge target lacks non-API historical identity"
            )

        evidence = provider_name_identity_evidence(
            conn,
            live_player_id=live_player_id,
            provider_name=provider_name,
            as_of=target.prediction_as_of,
        )
        if (
            evidence.status != "UNIQUE_PIT_CANDIDATE"
            or evidence.pit_candidates != (historical_player_id,)
        ):
            raise ValueError(
                "Sportradar identity bridge PIT evidence mismatch "
                f"status={evidence.status} "
                f"candidates={','.join(str(v) for v in evidence.pit_candidates) or '-'}"
            )

        latest = latest_bridge_event(conn, live_player_id=live_player_id)
        expected_latest = (
            "BRIDGE",
            historical_player_id,
            event_id,
            provider_player_id,
        )
        if latest == expected_latest:
            conn.rollback()
            print(
                "SPORTRADAR_IDENTITY_BRIDGE_APPLY "
                f"status=EXISTING live_player_id={live_player_id} "
                f"historical_player_id={historical_player_id} match_id={match_id} "
                f"event_id={event_id} provider_player_id={provider_player_id} "
                f"schema={schema_status} writes=0",
                flush=True,
            )
            return 0
        if latest is not None:
            raise ValueError(
                "Sportradar identity bridge refuses conflicting existing bridge event "
                f"latest={latest}"
            )

        event_pk = append_bridge(
            conn,
            live_player_id=live_player_id,
            historical_player_id=historical_player_id,
            evidence_match_id=match_id,
            evidence_provider_event_id=event_id,
            evidence_provider_player_id=provider_player_id,
            evidence_provider_name=provider_name,
            evidence_as_of=target.prediction_as_of,
            activated_at=activated_at,
            actor=actor,
            reason=reason,
        )
        conn.commit()

    print(
        "SPORTRADAR_IDENTITY_BRIDGE_APPLY "
        f"status=INSERTED bridge_event_id={event_pk} "
        f"live_player_id={live_player_id} historical_player_id={historical_player_id} "
        f"match_id={match_id} event_id={event_id} "
        f"provider_player_id={provider_player_id} "
        f"activated_at={activated_at.isoformat()} schema={schema_status} writes=1",
        flush=True,
    )
    return 0
