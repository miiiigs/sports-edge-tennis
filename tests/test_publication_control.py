from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from sports_edge_tennis.publication_control import (
    enable_publication,
    halt_publication,
    load_publication_control_state,
)


def test_publication_control_defaults_enabled(db: psycopg.Connection):
    state = load_publication_control_state(db)

    assert state.enabled is True
    assert state.action is None


def test_publication_control_halt_then_manual_enable(db: psycopg.Connection):
    halted = halt_publication(
        db,
        source="SYSTEM",
        reason="operator drill",
        created_at=datetime(2026, 9, 18, 17, 0, tzinfo=UTC),
    )

    assert halted.enabled is False
    assert halted.action == "HALT"
    assert halted.source == "SYSTEM"

    enabled = enable_publication(
        db,
        actor="qa-operator",
        reason="drill complete",
        created_at=datetime(2026, 9, 18, 17, 5, tzinfo=UTC),
    )

    assert enabled.enabled is True
    assert enabled.action == "ENABLE"
    assert enabled.source == "OPERATOR"
    assert enabled.actor == "qa-operator"


def test_operator_halt_requires_actor(db: psycopg.Connection):
    with pytest.raises(ValueError, match="requires actor"):
        halt_publication(
            db,
            source="OPERATOR",
            reason="manual pause",
        )


def test_publication_control_events_are_append_only(db: psycopg.Connection):
    halt_publication(
        db,
        source="SYSTEM",
        reason="append-only test",
    )
    event_id = db.execute(
        "SELECT publication_control_event_id FROM publication_control_events"
    ).fetchone()[0]

    with pytest.raises(psycopg.Error):
        db.execute(
            "UPDATE publication_control_events SET reason = 'rewritten' "
            "WHERE publication_control_event_id = %s",
            (event_id,),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            "DELETE FROM publication_control_events "
            "WHERE publication_control_event_id = %s",
            (event_id,),
        )
