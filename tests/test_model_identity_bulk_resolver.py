"""Semantic parity and query-scaling tests for the bulk model identity resolver.

The bulk resolver must be exactly equivalent to calling
``resolve_model_player_id`` once per player, while issuing a bounded number of SQL
statements instead of growing with the player count (no N+1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.data.model_identity import (
    resolve_model_player_id,
    resolve_model_player_ids,
    resolve_model_player_ids_with_provenance,
)
from sports_edge_tennis.data.normalize import normalize_person_lookup


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _player(conn: psycopg.Connection, name: str) -> int:
    # PIT-consistent fixture: the player row must be visible at the 2025 cutoffs used
    # by these tests. ``players.updated_at`` is the reference-visibility gate.
    return conn.execute(
        """
        INSERT INTO players (canonical_name, status, created_at, updated_at)
        VALUES (%s, 'ACTIVE', %s, %s) RETURNING player_id
        """,
        (name, _utc(2024, 1, 1), _utc(2024, 1, 1)),
    ).fetchone()[0]


def _source(
    conn: psycopg.Connection,
    *,
    player_id: int,
    system: str,
    source_id: str,
    created_at: datetime | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO player_source_ids
            (player_id, source_system, source_player_id, created_at)
        VALUES (%s, %s, %s, %s)
        """,
        (player_id, system, source_id, created_at or _utc(2024, 1, 1)),
    )


def _alias(
    conn: psycopg.Connection,
    *,
    player_id: int,
    alias: str,
    system: str = "TENNIS_DATA",
    created_at: datetime | None = None,
    first_observed_at: datetime | None = None,
) -> None:
    ingested = created_at or _utc(2024, 1, 1)
    conn.execute(
        """
        INSERT INTO player_aliases (
            player_id, source_system, alias, normalized_alias,
            first_observed_at, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            player_id,
            system,
            alias,
            normalize_person_lookup(alias),
            first_observed_at or ingested,
            ingested,
            ingested,
        ),
    )


def _dummy_match(conn: psycopg.Connection, name: str) -> int:
    tournament_id = conn.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, 'ATP_MAIN', 'ATP_250')
        RETURNING tournament_id
        """,
        (name,),
    ).fetchone()[0]
    edition_id = conn.execute(
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name)
        VALUES (%s, 2025, %s)
        RETURNING tournament_edition_id
        """,
        (tournament_id, name),
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]


def _seed_candidate(
    conn: psycopg.Connection,
    *,
    name: str,
    alias: str,
    observed_at: datetime,
    with_completed_history: bool = True,
    live_only: bool = False,
    source_created_at: datetime | None = None,
    alias_created_at: datetime | None = None,
    alias_first_observed_at: datetime | None = None,
    history_ingested_at: datetime | None = None,
) -> int:
    """Seed a historical candidate with an alias, optionally PIT-visible results."""
    player_id = _player(conn, name)
    if live_only:
        _source(
            conn,
            player_id=player_id,
            system="API_TENNIS",
            source_id=f"live-cand-{player_id}",
            created_at=source_created_at,
        )
    else:
        _source(
            conn,
            player_id=player_id,
            system="SACKMANN",
            source_id=f"hist-cand-{player_id}",
            created_at=source_created_at,
        )
    _alias(
        conn,
        player_id=player_id,
        alias=alias,
        created_at=alias_created_at,
        first_observed_at=alias_first_observed_at,
    )
    if not with_completed_history:
        return player_id

    opponent = _player(conn, f"{name} Opponent")
    _source(conn, player_id=opponent, system="SACKMANN", source_id=f"hist-opp-{player_id}")
    match_id = _dummy_match(conn, f"{name} Open")
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets, qualifying_flag,
            match_status, source_observed_at, as_of, ingested_at
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE,
                'COMPLETED', %s, %s, %s)
        """,
        (match_id, player_id, opponent, observed_at, observed_at, observed_at,
         history_ingested_at or observed_at),
    )
    conn.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of, ingested_at
        )
        VALUES (%s, 'COMPLETED', 1, %s, %s, %s)
        """,
        (match_id, observed_at, observed_at, history_ingested_at or observed_at),
    )
    return player_id


def _seed_bridge(
    conn: psycopg.Connection,
    *,
    live_player_id: int,
    historical_player_id: int,
    activated_at: datetime,
    created_at: datetime | None = None,
) -> None:
    match_id = _dummy_match(conn, f"Bridge Evidence {live_player_id}")
    conn.execute(
        """
        INSERT INTO model_identity_bridge_events (
            live_player_id, action, historical_player_id, evidence_source_system,
            evidence_match_id, evidence_provider_event_id, evidence_provider_player_id,
            evidence_provider_name, evidence_status, evidence_as_of, activated_at,
            actor, reason, created_at
        )
        VALUES (%s, 'BRIDGE', %s, 'SPORTRADAR', %s, 'evt-1', 'prov-1', 'Prov Name',
                'UNIQUE_PIT_CANDIDATE', %s, %s, 'test', 'parity', %s)
        """,
        (
            live_player_id,
            historical_player_id,
            match_id,
            activated_at,
            activated_at,
            created_at or activated_at,
        ),
    )


def _seed_disable(conn: psycopg.Connection, *, live_player_id: int, activated_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO model_identity_bridge_events (
            live_player_id, action, historical_player_id, evidence_status,
            evidence_as_of, activated_at, actor, reason, created_at
        )
        VALUES (%s, 'DISABLE', NULL, 'DISABLE', %s, %s, 'test', 'parity', %s)
        """,
        (live_player_id, activated_at, activated_at, activated_at),
    )


def _scalar_map(conn: psycopg.Connection, player_ids, as_of: datetime) -> dict[int, int]:
    return {
        player_id: resolve_model_player_id(conn, player_id=player_id, as_of=as_of)
        for player_id in player_ids
    }


def test_bulk_resolver_matches_scalar_for_representative_cases(db: psycopg.Connection):
    cutoff = _utc(2025, 6, 16)
    observed = _utc(2025, 1, 1, 12)

    historical = _player(db, "Bulk Historical")
    _source(db, player_id=historical, system="SACKMANN", source_id="hist-bulk")

    bridge_live = _player(db, "Bulk Bridge Live")
    _source(db, player_id=bridge_live, system="API_TENNIS", source_id="api-bridge-live")
    bridge_target = _seed_candidate(db, name="Bulk Bridge Target", alias="Bulk Bridge Target", observed_at=observed)
    _seed_bridge(
        db,
        live_player_id=bridge_live,
        historical_player_id=bridge_target,
        activated_at=_utc(2025, 5, 1),
    )

    alias_live = _player(db, "Bulk Alias")
    _source(db, player_id=alias_live, system="SPORTRADAR", source_id="sr:bulk-alias")
    alias_target = _seed_candidate(db, name="Bulk Alias", alias="Bulk Alias", observed_at=observed)

    no_candidate = _player(db, "Bulk Nobody")
    _source(db, player_id=no_candidate, system="API_TENNIS", source_id="api-nobody")

    ambiguous_live = _player(db, "Bulk Ambiguous")
    _source(db, player_id=ambiguous_live, system="API_TENNIS", source_id="api-ambiguous")
    _seed_candidate(db, name="Bulk Ambiguous One", alias="Bulk Ambiguous", observed_at=observed)
    _seed_candidate(db, name="Bulk Ambiguous Two", alias="Bulk Ambiguous", observed_at=observed)

    no_history_live = _player(db, "Bulk No History")
    _source(db, player_id=no_history_live, system="API_TENNIS", source_id="api-no-history")
    _seed_candidate(
        db,
        name="Bulk No History",
        alias="Bulk No History",
        observed_at=observed,
        with_completed_history=False,
    )

    future_bridge_live = _player(db, "Bulk Future Bridge")
    _source(db, player_id=future_bridge_live, system="API_TENNIS", source_id="api-future-bridge")
    future_target = _seed_candidate(db, name="Bulk Future Target", alias="Bulk Future Target", observed_at=observed)
    _seed_bridge(
        db,
        live_player_id=future_bridge_live,
        historical_player_id=future_target,
        activated_at=_utc(2025, 7, 1),
    )

    live_only_candidate_live = _player(db, "Bulk Live Candidate")
    _source(db, player_id=live_only_candidate_live, system="API_TENNIS", source_id="api-live-candidate")
    _seed_candidate(
        db,
        name="Bulk Live Candidate Target",
        alias="Bulk Live Candidate",
        observed_at=observed,
        live_only=True,
    )

    disabled_live = _player(db, "Bulk Disabled")
    _source(db, player_id=disabled_live, system="API_TENNIS", source_id="api-disabled")
    disabled_target = _seed_candidate(db, name="Bulk Disabled Target", alias="Bulk Disabled Target", observed_at=observed)
    _seed_bridge(
        db,
        live_player_id=disabled_live,
        historical_player_id=disabled_target,
        activated_at=_utc(2025, 4, 1),
    )
    _seed_disable(db, live_player_id=disabled_live, activated_at=_utc(2025, 5, 1))

    player_ids = [
        historical,
        bridge_live,
        alias_live,
        no_candidate,
        ambiguous_live,
        no_history_live,
        future_bridge_live,
        live_only_candidate_live,
        disabled_live,
    ]

    bulk = resolve_model_player_ids(db, player_ids=player_ids, as_of=cutoff)
    scalar = _scalar_map(db, player_ids, cutoff)

    assert bulk == scalar
    assert bulk[historical] == historical
    assert bulk[bridge_live] == bridge_target
    assert bulk[alias_live] == alias_target
    assert bulk[no_candidate] == no_candidate
    assert bulk[ambiguous_live] == ambiguous_live
    assert bulk[no_history_live] == no_history_live
    assert bulk[future_bridge_live] == future_bridge_live
    assert bulk[live_only_candidate_live] == live_only_candidate_live
    assert bulk[disabled_live] == disabled_live


def test_bulk_resolver_returns_entry_for_every_input_and_dedupes(db: psycopg.Connection):
    cutoff = _utc(2025, 6, 16)
    historical = _player(db, "Bulk Dedupe Historical")
    _source(db, player_id=historical, system="SACKMANN", source_id="hist-dedupe")
    live = _player(db, "Bulk Dedupe Live")
    _source(db, player_id=live, system="API_TENNIS", source_id="api-dedupe")

    bulk = resolve_model_player_ids(
        db,
        player_ids=[historical, live, historical, live, historical],
        as_of=cutoff,
    )

    assert bulk == {historical: historical, live: live}
    assert resolve_model_player_ids(db, player_ids=[], as_of=cutoff) == {}


class _CountingConnection:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn
        self.queries = 0

    def execute(self, *args, **kwargs):
        self.queries += 1
        return self._conn.execute(*args, **kwargs)


def test_bulk_resolver_query_count_is_not_proportional_to_player_count(db: psycopg.Connection):
    cutoff = _utc(2025, 6, 16)
    player_ids: list[int] = []
    for index in range(150):
        player_id = _player(db, f"Bulk Scaling Player {index:03d}")
        _source(db, player_id=player_id, system="API_TENNIS", source_id=f"api-scaling-{index:03d}")
        player_ids.append(player_id)

    scalar_conn = _CountingConnection(db)
    scalar = _scalar_map(scalar_conn, player_ids, cutoff)

    bulk_conn = _CountingConnection(db)
    bulk = resolve_model_player_ids(bulk_conn, player_ids=player_ids, as_of=cutoff)

    assert bulk == scalar
    assert all(value == player_id for player_id, value in bulk.items())

    # Bulk resolution stays bounded; scalar resolution grows with the player count.
    assert bulk_conn.queries <= 6
    assert scalar_conn.queries >= 10 * bulk_conn.queries
    assert scalar_conn.queries >= len(player_ids)


def test_future_ingested_alias_is_invisible_at_original_cutoff(db: psycopg.Connection):
    cutoff = _utc(2025, 6, 16)
    observed = _utc(2025, 1, 1, 12)
    live = _player(db, "Future Alias Live")
    _source(db, player_id=live, system="API_TENNIS", source_id="api-future-alias")
    target = _seed_candidate(
        db,
        name="Future Alias Live",
        alias="Future Alias Live",
        observed_at=observed,
        source_created_at=observed,
        alias_first_observed_at=observed,
        alias_created_at=cutoff.replace(day=17),
    )

    at_cutoff = resolve_model_player_ids_with_provenance(
        db, player_ids=[live], as_of=cutoff
    )[live]
    assert at_cutoff.resolved_player_id == live
    assert at_cutoff.method == "NO_HISTORICAL_CANDIDATE"

    later = resolve_model_player_ids_with_provenance(
        db, player_ids=[live], as_of=cutoff.replace(day=18)
    )[live]
    assert later.resolved_player_id == target
    assert later.method == "ALIAS"
    assert later.visible_from == cutoff.replace(day=17)


def test_ambiguous_alias_fails_closed_with_provenance(db: psycopg.Connection):
    cutoff = _utc(2025, 6, 16)
    observed = _utc(2025, 1, 1, 12)
    live = _player(db, "Ambiguous Orientation")
    _source(db, player_id=live, system="API_TENNIS", source_id="api-ambiguous-orientation")
    _seed_candidate(
        db,
        name="Ambiguous One",
        alias="Ambiguous Orientation",
        observed_at=observed,
        source_created_at=observed,
        alias_first_observed_at=observed,
        alias_created_at=observed,
    )
    _seed_candidate(
        db,
        name="Ambiguous Two",
        alias="Ambiguous Orientation",
        observed_at=observed,
        source_created_at=observed,
        alias_first_observed_at=observed,
        alias_created_at=observed,
    )

    resolution = resolve_model_player_ids_with_provenance(
        db, player_ids=[live], as_of=cutoff
    )[live]
    assert resolution.resolved_player_id == live
    assert resolution.method == "AMBIGUOUS"
    assert resolution.candidate_count == 2


def test_forward_shadow_targets_preserve_live_and_model_orientation(
    db: psycopg.Connection,
):
    from decimal import Decimal

    from sports_edge_tennis.validation.a1_identity_bridge_forward_shadow import (
        load_targets,
    )

    as_of = _utc(2025, 6, 16, 8)
    start = _utc(2025, 6, 16, 12)
    live_one = _player(db, "Orientation One")
    live_two = _player(db, "Orientation Two")
    _source(db, player_id=live_one, system="API_TENNIS", source_id="api-orient-one")
    _source(db, player_id=live_two, system="API_TENNIS", source_id="api-orient-two")
    hist_one = _seed_candidate(
        db,
        name="Orientation One",
        alias="Orientation One",
        observed_at=_utc(2025, 1, 1, 12),
        source_created_at=_utc(2025, 1, 1, 12),
        alias_first_observed_at=_utc(2025, 1, 1, 12),
        alias_created_at=_utc(2025, 1, 1, 12),
    )
    hist_two = _seed_candidate(
        db,
        name="Orientation Two",
        alias="Orientation Two",
        observed_at=_utc(2025, 1, 1, 12),
        source_created_at=_utc(2025, 1, 1, 12),
        alias_first_observed_at=_utc(2025, 1, 1, 12),
        alias_created_at=_utc(2025, 1, 1, 12),
    )
    match_id = _dummy_match(db, "Orientation Open")
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets, qualifying_flag,
            match_status, source_observed_at, as_of, ingested_at
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE,
                'SCHEDULED', %s, %s, %s)
        """,
        (match_id, live_one, live_two, start, as_of, as_of, as_of),
    )
    from abstention_helpers import (
        A1,
        A2,
        A3,
        finalize_matrix,
        insert_run,
        insert_target,
        make_result,
        seed_agent_configs,
    )

    run_id = insert_run(db, as_of=as_of)
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
    finalize_matrix(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        results=[
            make_result(
                A1,
                as_of,
                status="PREDICTED",
                side=1,
                probability=Decimal("0.640000"),
            ),
            make_result(
                A2,
                as_of,
                status="ABSTAINED",
                reason="INSUFFICIENT_HISTORY",
            ),
            make_result(
                A3,
                as_of,
                status="ABSTAINED",
                reason="WEAK_SIGNAL",
            ),
        ],
        finalized_at=as_of,
    )

    targets = load_targets(db, run_ids=[run_id], capture_at=_utc(2025, 6, 16, 9))
    assert len(targets) == 1
    target = targets[0]
    assert target.live_player_1_id == live_one
    assert target.live_player_2_id == live_two
    assert target.model_player_1_id == hist_one
    assert target.model_player_2_id == hist_two
    assert target.model_player_1_id != target.model_player_2_id


def test_backdated_bridge_event_inserted_after_cutoff_is_invisible(
    db: psycopg.Connection,
):
    """``activated_at`` is caller-supplied; the insertion clock is the real gate."""
    cutoff = _utc(2025, 6, 16)
    observed = _utc(2025, 1, 1, 12)
    live = _player(db, "Backdated Bridge Live")
    _source(db, player_id=live, system="API_TENNIS", source_id="api-backdated")
    target = _seed_candidate(
        db, name="Backdated Bridge Target", alias="Backdated Bridge Target",
        observed_at=observed,
    )
    _seed_bridge(
        db,
        live_player_id=live,
        historical_player_id=target,
        activated_at=_utc(2025, 4, 1),
        # Activated in April, but only inserted in July: a mapping decision made with
        # hindsight must not be visible to an April replay.
        created_at=_utc(2025, 7, 1),
    )

    at_cutoff = resolve_model_player_ids_with_provenance(
        db, player_ids=[live], as_of=cutoff
    )[live]
    assert at_cutoff.resolved_player_id == live
    assert at_cutoff.method == "NO_HISTORICAL_CANDIDATE"
    assert at_cutoff.bridge_event_id is None

    later = resolve_model_player_ids_with_provenance(
        db, player_ids=[live], as_of=_utc(2025, 7, 2)
    )[live]
    assert later.resolved_player_id == target
    assert later.method == "PERSISTED_BRIDGE"


def test_mutated_canonical_name_is_not_reused_for_earlier_replay(
    db: psycopg.Connection,
):
    """A rename after the cutoff must not generate lookup keys for an earlier replay."""
    cutoff = _utc(2025, 6, 16)
    observed = _utc(2025, 1, 1, 12)
    live = _player(db, "Old Name Player")
    _source(db, player_id=live, system="API_TENNIS", source_id="api-renamed")
    # The historical candidate only carries the post-rename name.
    target = _seed_candidate(
        db, name="Historical Candidate", alias="New Name Player", observed_at=observed
    )
    db.execute(
        "UPDATE players SET canonical_name = 'New Name Player', updated_at = %s "
        "WHERE player_id = %s",
        (_utc(2025, 5, 1), live),
    )

    before_rename = resolve_model_player_ids_with_provenance(
        db, player_ids=[live], as_of=_utc(2025, 4, 1)
    )[live]
    assert before_rename.resolved_player_id == live
    # Migration 0025 preserves the pre-rename name, so the PIT lookup is still made
    # but cannot match an alias that only ever existed under the later name.
    assert before_rename.name_source == "PIT_CANONICAL_NAME"
    assert before_rename.method == "NO_HISTORICAL_CANDIDATE"

    after_rename = resolve_model_player_ids_with_provenance(
        db, player_ids=[live], as_of=cutoff
    )[live]
    assert after_rename.resolved_player_id == target
    assert after_rename.name_source == "PIT_CANONICAL_NAME"


def test_alias_refresh_is_legitimate_but_refresh_gate_is_measurable(
    db: psycopg.Connection,
):
    """``updated_at`` tracks re-observation, not value history.

    The default strict policy keeps legitimate PIT evidence. The optional refresh gate
    is available only to size the residual out-of-band mutation channel.
    """
    cutoff = _utc(2025, 6, 16)
    observed = _utc(2025, 1, 1, 12)
    live = _player(db, "Refresh Evidence Live")
    _source(db, player_id=live, system="API_TENNIS", source_id="api-refresh")
    target = _seed_candidate(
        db,
        name="Refresh Evidence Target",
        alias="Refresh Evidence Live",
        observed_at=observed,
    )
    # The candidate alias was re-observed after the cutoff; only last_observed_at and
    # updated_at move, the linkage is unchanged.
    db.execute(
        "UPDATE player_aliases SET last_observed_at = %s, updated_at = %s "
        "WHERE player_id = %s",
        (_utc(2025, 8, 1), _utc(2025, 8, 1), target),
    )

    default = resolve_model_player_ids_with_provenance(
        db, player_ids=[live], as_of=cutoff
    )[live]
    assert default.resolved_player_id == target

    gated = resolve_model_player_ids_with_provenance(
        db, player_ids=[live], as_of=cutoff, alias_refresh_gate=True
    )[live]
    assert gated.resolved_player_id == live
    assert gated.method == "NO_HISTORICAL_CANDIDATE"


def test_resolution_provenance_freezes_reference_digest(db: psycopg.Connection):
    from sports_edge_tennis.data.identity_provenance import (
        reference_drift_for,
        resolve_with_provenance,
    )

    cutoff = _utc(2025, 6, 16)
    observed = _utc(2025, 1, 1, 12)
    live = _player(db, "Provenance Live")
    _source(db, player_id=live, system="API_TENNIS", source_id="api-provenance")
    target = _seed_candidate(
        db, name="Provenance Target", alias="Provenance Live", observed_at=observed
    )

    resolutions, reference = resolve_with_provenance(
        db, player_ids=[live, target], as_of=cutoff
    )
    record = resolutions[live].as_record()
    assert record["player_id"] == live
    assert record["resolved_player_id"] == target
    assert record["method"] == "ALIAS"
    assert record["evidence"] == "tennis_data_alias_pit"
    assert record["observed_at"] == cutoff.isoformat()
    assert record["reference_sha256"] == reference.sha256
    assert reference.row_counts["players"] == 2  # live and target

    drift = reference_drift_for(
        db, player_ids=[live, target], recorded=reference.as_record()
    )
    assert drift["drift"] is False

    # Migration 0025 makes alias linkage immutable for normal traffic. An out-of-band
    # re-point is rejected outright, which is stronger than merely detecting it.
    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        db.execute(
            "UPDATE player_aliases SET player_id = %s WHERE player_id = %s",
            (live, target),
        )

    # A name mutation is still an allowed operation, and the frozen digest detects it.
    db.execute(
        "UPDATE players SET canonical_name = 'Renamed Provenance Target', updated_at = %s "
        "WHERE player_id = %s",
        (_utc(2026, 1, 1), target),
    )
    moved = reference_drift_for(
        db, player_ids=[live, target], recorded=reference.as_record()
    )
    assert moved["drift"] is True


def test_strict_policy_is_a_noop_for_live_now_resolution(db: psycopg.Connection):
    """Live production resolves at ``as_of ~= now``, where every PIT gate is a no-op.

    This guards the production contract: the stricter historical policy must not change
    which identity a live target resolves to.
    """
    now = datetime.now(UTC)
    live = _player(db, "Live Now Player")
    db.execute(
        "INSERT INTO player_source_ids (player_id, source_system, source_player_id, created_at) "
        "VALUES (%s, 'API_TENNIS', 'api-live-now', %s)",
        (live, now),
    )
    target = _seed_candidate(
        db,
        name="Live Now Player",
        alias="Live Now Player",
        observed_at=now - timedelta(days=30),
    )
    strict = resolve_model_player_ids_with_provenance(
        db, player_ids=[live], as_of=now, reference_policy="strict"
    )[live]
    permissive = resolve_model_player_ids_with_provenance(
        db, player_ids=[live], as_of=now, reference_policy="permissive"
    )[live]
    assert strict.resolved_player_id == target
    assert permissive.resolved_player_id == target
    assert strict.method == permissive.method == "ALIAS"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
