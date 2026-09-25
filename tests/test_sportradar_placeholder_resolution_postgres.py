from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.data.normalize import normalize_person_lookup
from sports_edge_tennis.live.current_slate_bootstrap import (
    is_placeholder_name,
    load_concrete_targets,
)
from sports_edge_tennis.live.sportradar_ingest import (
    ingest_sportradar_daily_summaries,
)

PLACEHOLDER_SEEN_AT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
RESOLVED_AT = datetime(2026, 9, 19, 18, 0, tzinfo=UTC)
MATCH_START = "2026-09-19T13:00:00Z"


def _summary(
    *,
    event_id: str = "sr:event:promo",
    p1_provider_id: str = "sr:ph:1",
    p1_name: str = "Qf1",
    p2_provider_id: str = "sr:ph:2",
    p2_name: str = "Qf2",
    status: str = "not_started",
    match_status: str = "not_started",
) -> dict:
    return {
        "sport_event": {
            "id": event_id,
            "start_time": MATCH_START,
            "sport_event_context": {
                "competition": {
                    "id": "sr:competition:promo",
                    "name": "ATP Challenger Promo Men Singles",
                    "level": "challenger",
                    "type": "singles",
                    "gender": "men",
                },
                "category": {"name": "ATP"},
                "season": {"id": "sr:season:promo"},
                "surface": "hard",
                "mode": {"best_of": "3"},
                "round": {"name": "Quarterfinal"},
                "stage": {"phase": "main"},
            },
            "competitors": [
                {"id": p1_provider_id, "name": p1_name, "qualifier": "home"},
                {"id": p2_provider_id, "name": p2_name, "qualifier": "away"},
            ],
        },
        "sport_event_status": {"status": status, "match_status": match_status},
    }


def _ingest(db: psycopg.Connection, summary: dict, received_at: datetime):
    return ingest_sportradar_daily_summaries(
        db,
        {"summaries": [summary]},
        received_at=received_at,
        allow_new_events=True,
    )


def _latest_names(
    db: psycopg.Connection, match_id: int, as_of: datetime
) -> tuple[str, str] | None:
    return db.execute(
        """
        SELECT p1.canonical_name, p2.canonical_name
        FROM match_versions mv
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        WHERE mv.match_id = %s
          AND mv.source_observed_at <= %s
          AND mv.as_of <= %s
        ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        LIMIT 1
        """,
        (match_id, as_of, as_of),
    ).fetchone()


def test_placeholder_event_promotes_to_real_participants_append_only(
    db: psycopg.Connection,
) -> None:
    # A. placeholder fixture initially ingested.
    first = _ingest(db, _summary(), PLACEHOLDER_SEEN_AT)
    assert first.rows_ingested == 1
    assert first.matches[0].overlap_status == "NEW_CANONICAL_EVENT"
    match_id = first.matches[0].match_id
    assert _latest_names(db, match_id, PLACEHOLDER_SEEN_AT) == ("Qf1", "Qf2")

    # B. same provider event later resolves participants.
    second = _ingest(
        db,
        _summary(
            p1_provider_id="sr:real:1",
            p1_name="Real One",
            p2_provider_id="sr:real:2",
            p2_name="Real Two",
        ),
        RESOLVED_AT,
    )
    assert second.rows_ingested == 1
    assert second.matches[0].overlap_status == "SOURCE_ID_RESOLVED"

    # C. canonical match remains the same.
    assert second.matches[0].match_id == match_id
    # G. no duplicate canonical match.
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
    assert (
        db.execute(
            "SELECT count(*) FROM match_source_ids WHERE source_system = 'SPORTRADAR'"
        ).fetchone()[0]
        == 1
    )

    # D. newest match version contains the real participants.
    assert _latest_names(db, match_id, RESOLVED_AT) == ("Real One", "Real Two")
    # E. previous version remains intact (append-only history).
    versions = [
        row[0]
        for row in db.execute(
            "SELECT match_version_id FROM match_versions "
            "WHERE match_id = %s ORDER BY match_version_id",
            (match_id,),
        ).fetchall()
    ]
    assert len(versions) == 2
    assert _latest_names(db, match_id, PLACEHOLDER_SEEN_AT) == ("Qf1", "Qf2")

    # J. PIT replay before the resolution timestamp still sees the placeholder.
    before = _latest_names(db, match_id, RESOLVED_AT - timedelta(seconds=1))
    after = _latest_names(db, match_id, RESOLVED_AT)
    assert before == ("Qf1", "Qf2")
    assert after == ("Real One", "Real Two")

    # F. the target resolver now returns the concrete match.
    run_id = db.execute(
        """
        INSERT INTO prediction_runs (
            run_status, as_of, started_at, prediction_contract_version
        )
        VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1')
        RETURNING prediction_run_id
        """,
        (RESOLVED_AT, RESOLVED_AT),
    ).fetchone()[0]
    db.execute(
        "INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s, %s)",
        (run_id, match_id),
    )
    targets = load_concrete_targets(db, run_id=run_id, cutoff=RESOLVED_AT)
    assert [target[0] for target in targets] == [match_id]
    assert not any(
        is_placeholder_name(name) for name in _latest_names(db, match_id, RESOLVED_AT)
    )


def test_ambiguous_provider_participant_update_fails_closed(
    db: psycopg.Connection,
) -> None:
    first = _ingest(db, _summary(), PLACEHOLDER_SEEN_AT)
    match_id = first.matches[0].match_id

    # Two canonical players share the normalized alias of the incoming name.
    normalized = normalize_person_lookup("A. Test")
    for name in ("Ambiguous Alpha", "Ambiguous Beta"):
        player_id = db.execute(
            "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
            (name,),
        ).fetchone()[0]
        db.execute(
            """
            INSERT INTO player_aliases (
                player_id, source_system, alias, normalized_alias,
                first_observed_at, last_observed_at
            )
            VALUES (%s, 'TEST', 'A. Test', %s, %s, %s)
            """,
            (player_id, normalized, PLACEHOLDER_SEEN_AT, PLACEHOLDER_SEEN_AT),
        )

    outcome = _ingest(
        db,
        _summary(
            p1_provider_id="sr:real:amb",
            p1_name="A. Test",
            p2_provider_id="sr:real:2",
            p2_name="Real Two",
        ),
        RESOLVED_AT,
    )

    assert outcome.rows_ingested == 0
    assert outcome.rows_rejected == 1
    assert "AMBIGUOUS_PLAYER" in str(outcome.rejected_rows)
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
    assert _latest_names(db, match_id, RESOLVED_AT) == ("Qf1", "Qf2")
    assert (
        db.execute(
            "SELECT count(*) FROM match_versions WHERE match_id = %s", (match_id,)
        ).fetchone()[0]
        == 1
    )


def test_real_participant_substitution_is_rejected_without_duplicate(
    db: psycopg.Connection,
) -> None:
    first = _ingest(
        db,
        _summary(
            p1_provider_id="sr:real:1",
            p1_name="Real One",
            p2_provider_id="sr:real:2",
            p2_name="Real Two",
        ),
        PLACEHOLDER_SEEN_AT,
    )
    match_id = first.matches[0].match_id

    outcome = _ingest(
        db,
        _summary(
            p1_provider_id="sr:real:1",
            p1_name="Real One",
            p2_provider_id="sr:real:3",
            p2_name="Real Three",
        ),
        RESOLVED_AT,
    )

    assert outcome.rows_ingested == 0
    assert outcome.rows_rejected == 1
    assert "existing_match_player_conflict" in str(outcome.rejected_rows)
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
    assert _latest_names(db, match_id, RESOLVED_AT) == ("Real One", "Real Two")


def test_cancellation_status_change_remains_append_only(
    db: psycopg.Connection,
) -> None:
    first = _ingest(db, _summary(), PLACEHOLDER_SEEN_AT)
    match_id = first.matches[0].match_id

    outcome = _ingest(
        db,
        _summary(status="cancelled", match_status="cancelled"),
        RESOLVED_AT,
    )

    assert outcome.rows_ingested == 1
    assert outcome.matches[0].match_id == match_id
    status = db.execute(
        """
        SELECT match_status::text
        FROM match_versions
        WHERE match_id = %s
        ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()[0]
    assert status == "CANCELLED"
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
