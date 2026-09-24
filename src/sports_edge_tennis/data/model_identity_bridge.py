"""Append-only, PIT-aware model identity bridge support.

These bridge events never rewrite canonical/live source identity. They only allow the
predictive layer to substitute one reviewed historical player after activation.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import psycopg


MIGRATION_PATH = Path("db/migrations/0013_model_identity_bridge_v1.up.sql")
APPEND_ONLY_TRIGGER = "trg_model_identity_bridge_events_append_only_mutation"

# Source systems that only ever identify live fixtures. Sportradar joined this set
# during the live-provider cutover: it attaches provider IDs to live canonical
# players but is not the deep historical corpus identity. A player carrying only
# these identities is still eligible for the live-to-history model bridge.
LIVE_FIXTURE_SOURCE_SYSTEMS = ("API_TENNIS", "SPORTRADAR")


def schema_state(conn: psycopg.Connection) -> tuple[bool, bool]:
    table_present = bool(
        conn.execute(
            "SELECT to_regclass('public.model_identity_bridge_events') IS NOT NULL"
        ).fetchone()[0]
    )
    if not table_present:
        return False, False
    trigger_present = bool(
        conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_trigger
                WHERE tgrelid = 'public.model_identity_bridge_events'::regclass
                  AND tgname = %s
                  AND NOT tgisinternal
            )
            """,
            (APPEND_ONLY_TRIGGER,),
        ).fetchone()[0]
    )
    return True, trigger_present


def ensure_schema(
    conn: psycopg.Connection,
    *,
    migration_path: Path = MIGRATION_PATH,
) -> str:
    table_present, trigger_present = schema_state(conn)
    if table_present and trigger_present:
        return "EXISTING"
    if table_present or trigger_present:
        raise RuntimeError(
            "model-identity bridge schema is partially applied "
            f"table_present={table_present} trigger_present={trigger_present}"
        )
    if not migration_path.is_file():
        raise RuntimeError(f"required migration file missing: {migration_path}")

    conn.execute(migration_path.read_text(encoding="utf-8"))
    table_present, trigger_present = schema_state(conn)
    if not table_present or not trigger_present:
        raise RuntimeError(
            "model-identity bridge schema verification failed "
            f"table_present={table_present} trigger_present={trigger_present}"
        )
    return "APPLIED"


def resolve_active_bridge(
    conn: psycopg.Connection,
    *,
    live_player_id: int,
    as_of: datetime,
) -> int | None:
    """Return the latest active historical model player visible at the cutoff."""
    row = conn.execute(
        """
        SELECT
            e.action,
            e.historical_player_id,
            EXISTS (
                SELECT 1
                FROM player_source_ids psi
                WHERE psi.player_id = e.historical_player_id
                  AND psi.source_system <> ALL(%s)
            ) AS target_has_historical_identity
        FROM model_identity_bridge_events e
        WHERE e.live_player_id = %s
          AND e.activated_at <= %s
        ORDER BY e.activated_at DESC, e.model_identity_bridge_event_id DESC
        LIMIT 1
        """,
        (list(LIVE_FIXTURE_SOURCE_SYSTEMS), live_player_id, as_of),
    ).fetchone()
    if row is None or str(row[0]) != "BRIDGE" or row[1] is None or not bool(row[2]):
        return None
    historical_player_id = int(row[1])
    if historical_player_id == live_player_id:
        return None
    return historical_player_id


def latest_bridge_event(
    conn: psycopg.Connection,
    *,
    live_player_id: int,
) -> tuple[str, int | None, str | None, str | None] | None:
    row = conn.execute(
        """
        SELECT
            action,
            historical_player_id,
            evidence_provider_event_id,
            evidence_provider_player_id
        FROM model_identity_bridge_events
        WHERE live_player_id = %s
        ORDER BY activated_at DESC, model_identity_bridge_event_id DESC
        LIMIT 1
        """,
        (live_player_id,),
    ).fetchone()
    if row is None:
        return None
    return (
        str(row[0]),
        int(row[1]) if row[1] is not None else None,
        str(row[2]) if row[2] is not None else None,
        str(row[3]) if row[3] is not None else None,
    )


def append_bridge(
    conn: psycopg.Connection,
    *,
    live_player_id: int,
    historical_player_id: int,
    evidence_match_id: int,
    evidence_provider_event_id: str,
    evidence_provider_player_id: str,
    evidence_provider_name: str,
    evidence_as_of: datetime,
    activated_at: datetime,
    actor: str,
    reason: str,
) -> int:
    row = conn.execute(
        """
        INSERT INTO model_identity_bridge_events (
            live_player_id,
            action,
            historical_player_id,
            evidence_source_system,
            evidence_match_id,
            evidence_provider_event_id,
            evidence_provider_player_id,
            evidence_provider_name,
            evidence_status,
            evidence_as_of,
            activated_at,
            actor,
            reason
        )
        VALUES (
            %s, 'BRIDGE', %s, 'SPORTRADAR', %s, %s, %s, %s,
            'UNIQUE_PIT_CANDIDATE', %s, %s, %s, %s
        )
        RETURNING model_identity_bridge_event_id
        """,
        (
            live_player_id,
            historical_player_id,
            evidence_match_id,
            evidence_provider_event_id,
            evidence_provider_player_id,
            evidence_provider_name,
            evidence_as_of,
            activated_at,
            actor,
            reason,
        ),
    ).fetchone()
    assert row is not None
    return int(row[0])
