"""Fail-closed bridge from live-only provider players to historical model identity.

This does not rewrite append-only source identity. It is a read-time model bridge used
when historical observations from API-Tennis need to connect to the existing corpus.

PIT reference-data policy
-------------------------
The bridge is only sound if every *mutable* reference row it reads can be shown to
have existed, with the value it has today, at the original cutoff. The repository
has three mutable reference surfaces that feed player identity:

``players.canonical_name``
    Versioned by ``player_name_versions`` (migration 0025). The resolver reads the
    name whose ``valid_from <= as_of``; a rename can no longer rewrite an earlier
    replay. When the version table is absent (pre-0025 deployment) the legacy gate
    ``updated_at <= as_of`` or ``updated_at = created_at`` is retained.
``player_aliases``
    Linkage columns are immutable under the migration 0025 guard trigger.
    ``player_alias_versions`` records every linkage insertion, and the resolver
    reads the version visible at ``as_of``. ``ensure_player_alias`` still only
    refreshes ``last_observed_at``/``updated_at`` on conflict.
    ``alias_refresh_gate=True`` deliberately forces the legacy current-table path
    and applies the over-conservative ``updated_at`` gate; it is a measurement
    instrument for the residual mutable channel, not a deployable policy.
``model_identity_bridge_events``
    Append-only, but ``activated_at`` is caller-supplied and can be backdated.
    Gated by ``created_at <= as_of`` and ``evidence_as_of <= as_of`` in addition to
    ``activated_at``.

``player_source_ids`` and ``match_versions`` are append-only with ``created_at`` /
``ingested_at`` visibility, and are already gated by ingest-run visibility.

``reference_policy="strict"`` (the default) applies the gates above. Because live
production always resolves at ``as_of ~= now`` the gates are no-ops there; they only
constrain historical replay. ``reference_policy="permissive"`` drops the
``players.updated_at`` value-mutation gate and the bridge ``created_at`` gate; it
reproduces the pre-2026-11-25 behaviour and exists only so the effect of each gate can
be measured. It must never be used to make an evidence claim.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import psycopg

from sports_edge_tennis.data.model_identity_bridge import (
    LIVE_FIXTURE_SOURCE_SYSTEMS,
)
from sports_edge_tennis.data.normalize import (
    normalize_person_lookup,
    tennis_data_aliases,
)

IDENTITY_REFERENCE_POLICY_VERSION = "identity_reference_pit_v3"

REFERENCE_POLICY_STRICT = "strict"
REFERENCE_POLICY_PERMISSIVE = "permissive"
REFERENCE_POLICIES = (REFERENCE_POLICY_STRICT, REFERENCE_POLICY_PERMISSIVE)

# Resolution methods.
METHOD_HISTORICAL_ID = "HISTORICAL_ID"
METHOD_PERSISTED_BRIDGE = "PERSISTED_BRIDGE"
METHOD_ALIAS = "ALIAS"
METHOD_AMBIGUOUS = "AMBIGUOUS"
METHOD_NO_CANDIDATE = "NO_CANDIDATE"
METHOD_NO_HISTORICAL_CANDIDATE = "NO_HISTORICAL_CANDIDATE"
METHOD_NO_PLAYER = "NO_PLAYER"

# Name-key provenance: which PIT-visible reference text produced the lookup keys.
NAME_SOURCE_CANONICAL = "PIT_CANONICAL_NAME"
NAME_SOURCE_ALIAS = "PIT_ALIAS"
NAME_SOURCE_UNAVAILABLE = "PIT_NAME_UNAVAILABLE"
NAME_SOURCE_NONE = "NONE"


def _check_policy(policy: str) -> str:
    if policy not in REFERENCE_POLICIES:
        raise ValueError(
            f"unknown reference_policy {policy!r}; expected one of {REFERENCE_POLICIES}"
        )
    return policy


def identity_version_tables_available(conn: psycopg.Connection) -> bool:
    """Return True when migration 0025 version tables are present.

    Resolvers must remain able to run against a pre-0025 schema (for example during a
    rolling deployment). In that case the legacy current-table gates are used. The
    versioned path is authoritative as soon as the tables exist.
    """
    row = conn.execute(
        """
        SELECT to_regclass('public.player_name_versions') IS NOT NULL
           AND to_regclass('public.player_alias_versions') IS NOT NULL
        """
    ).fetchone()
    return bool(row and row[0])



def has_historical_model_identity(conn: psycopg.Connection, player_id: int) -> bool:
    """Return True when the player carries a non-live historical source identity.

    Sportradar is a live fixture provider: its source IDs are attached to live
    canonical players and do not represent the deep historical corpus. A player
    that only carries live-provider identities must remain bridgeable.

    Player source ids and aliases are reference identity data. This helper is a
    current-state predicate; the PIT resolver below applies ``created_at`` gates.
    """
    return (
        conn.execute(
            """
            SELECT 1
            FROM player_source_ids
            WHERE player_id = %s
              AND source_system <> ALL(%s)
            LIMIT 1
            """,
            (player_id, list(LIVE_FIXTURE_SOURCE_SYSTEMS)),
        ).fetchone()
        is not None
    )


@dataclass(frozen=True)
class IdentityResolution:
    """One player's fail-closed PIT identity resolution.

    ``method`` is one of ``HISTORICAL_ID``, ``PERSISTED_BRIDGE``, ``ALIAS``,
    ``AMBIGUOUS``, ``NO_CANDIDATE``, ``NO_HISTORICAL_CANDIDATE`` or ``NO_PLAYER``.
    ``matched_alias``, ``bridge_event_id``, ``evidence``, ``name_source`` and
    ``visible_from`` are audit provenance, never model inputs.
    """

    player_id: int
    resolved_player_id: int
    method: str
    candidate_count: int = 0
    matched_alias: str | None = None
    bridge_event_id: int | None = None
    visible_from: datetime | None = None
    evidence: str = "none"
    name_source: str = NAME_SOURCE_NONE
    reference_policy: str = REFERENCE_POLICY_STRICT
    observed_at: datetime | None = None

    @property
    def bridged(self) -> bool:
        return self.resolved_player_id != self.player_id


# Retained name for the guarded Sportradar identity-bridge apply tool. The predicate
# now excludes every live fixture provider, including Sportradar.
has_non_api_source_identity = has_historical_model_identity


def resolve_model_player_id(
    conn: psycopg.Connection,
    *,
    player_id: int,
    as_of: datetime,
    reference_policy: str = REFERENCE_POLICY_STRICT,
    allow_provider_alias_keys: bool = False,
    alias_refresh_gate: bool = False,
) -> int:
    """Map one live-only player to one unambiguous PIT-visible historical identity.

    Zero or multiple candidates retain the supplied player ID. No source mapping is
    mutated, and a player that already has a non-live historical source identity is
    returned as-is.
    """
    return resolve_model_player_ids_with_provenance(
        conn,
        player_ids=[player_id],
        as_of=as_of,
        reference_policy=reference_policy,
        allow_provider_alias_keys=allow_provider_alias_keys,
        alias_refresh_gate=alias_refresh_gate,
    )[player_id].resolved_player_id


def resolve_model_player_ids_with_provenance(
    conn: psycopg.Connection,
    *,
    player_ids: Iterable[int],
    as_of: datetime,
    reference_policy: str = REFERENCE_POLICY_STRICT,
    allow_provider_alias_keys: bool = False,
    alias_refresh_gate: bool = False,
) -> dict[int, IdentityResolution]:
    """Bulk, set-based PIT identity resolution with audit provenance.

    Every source/alias/history join is bounded by the original cutoff:
    ``player_source_ids.created_at <= as_of``, ``player_aliases.created_at <= as_of``,
    ``player_aliases.first_observed_at <= as_of``,
    ``players.updated_at <= as_of`` (or the row was never updated),
    ``model_identity_bridge_events.created_at <= as_of`` and
    ``match_result_observations.ingested_at <= as_of``. A backdated alias or bridge
    event with a later ingestion timestamp therefore cannot leak into a historical
    replay.

    Lookup keys are derived from PIT-visible reference text only: the canonical name
    when the player row has not been mutated after the cutoff. When
    ``allow_provider_alias_keys`` is enabled, a canonical tier with no candidates may
    fall back to the live player's own PIT-visible provider aliases. Canonical-name
    candidates always outrank provider-alias candidates.
    """
    policy = _check_policy(reference_policy)
    strict = policy == REFERENCE_POLICY_STRICT
    unique_ids = sorted({int(pid) for pid in player_ids})
    if not unique_ids:
        return {}
    live_sources = list(LIVE_FIXTURE_SOURCE_SYSTEMS)
    resolutions = {
        player_id: IdentityResolution(
            player_id=player_id,
            resolved_player_id=player_id,
            method=METHOD_NO_CANDIDATE,
            reference_policy=policy,
            observed_at=as_of,
        )
        for player_id in unique_ids
    }

    # Stage B: IDs that already carry a non-live historical identity map to themselves.
    historical_rows = conn.execute(
        """
        SELECT DISTINCT psi.player_id
        FROM player_source_ids psi
        LEFT JOIN ingest_runs ing ON ing.ingest_run_id = psi.ingest_run_id
        WHERE psi.player_id = ANY(%s)
          AND psi.source_system <> ALL(%s)
          AND COALESCE(ing.finished_at, ing.started_at, psi.created_at) <= %s
        """,
        (unique_ids, live_sources, as_of),
    ).fetchall()
    historical_ids = {int(row[0]) for row in historical_rows}
    for player_id in historical_ids:
        resolutions[player_id] = IdentityResolution(
            player_id=player_id,
            resolved_player_id=player_id,
            method=METHOD_HISTORICAL_ID,
            evidence="player_source_ids",
            reference_policy=policy,
            observed_at=as_of,
        )

    unresolved = [player_id for player_id in unique_ids if player_id not in historical_ids]
    if not unresolved:
        return resolutions

    # Stage C: latest persisted bridge event that was *inserted* and *activated* by
    # the cutoff. ``created_at`` defeats a backdated ``activated_at``.
    bridge_where = [
        "e.live_player_id = ANY(%s)",
        "e.activated_at <= %s",
        "e.evidence_as_of <= %s",
    ]
    bridge_params: list[object] = []
    if strict:
        bridge_where.append("e.created_at <= %s")
        bridge_params.append(as_of)
    bridge_rows = conn.execute(
        f"""
        SELECT DISTINCT ON (e.live_player_id)
            e.live_player_id,
            e.action,
            e.historical_player_id,
            e.model_identity_bridge_event_id,
            e.activated_at,
            EXISTS (
                SELECT 1
                FROM player_source_ids psi
                LEFT JOIN ingest_runs ing ON ing.ingest_run_id = psi.ingest_run_id
                WHERE psi.player_id = e.historical_player_id
                  AND psi.source_system <> ALL(%s)
                  AND COALESCE(ing.finished_at, ing.started_at, psi.created_at) <= %s
            ) AS target_has_historical_identity
        FROM model_identity_bridge_events e
        WHERE {" AND ".join(bridge_where)}
        ORDER BY
            e.live_player_id,
            e.activated_at DESC,
            e.model_identity_bridge_event_id DESC
        """,
        (live_sources, as_of, unresolved, as_of, as_of, *bridge_params),
    ).fetchall()
    bridged_live_ids: set[int] = set()
    for (
        live_player_id,
        action,
        historical_player_id,
        bridge_event_id,
        activated_at,
        target_has_historical,
    ) in bridge_rows:
        live_player_id = int(live_player_id)
        bridged_live_ids.add(live_player_id)
        if (
            str(action) == "BRIDGE"
            and historical_player_id is not None
            and bool(target_has_historical)
            and int(historical_player_id) != live_player_id
        ):
            resolutions[live_player_id] = IdentityResolution(
                player_id=live_player_id,
                resolved_player_id=int(historical_player_id),
                method=METHOD_PERSISTED_BRIDGE,
                bridge_event_id=int(bridge_event_id),
                visible_from=activated_at,
                evidence="model_identity_bridge_events",
                reference_policy=policy,
                observed_at=as_of,
            )

    unresolved = [
        player_id
        for player_id in unresolved
        if player_id not in bridged_live_ids
        or resolutions[player_id].resolved_player_id == player_id
    ]
    if not unresolved:
        return resolutions

    # Stage D: PIT-visible reference names for the remaining unresolved players.
    # With migration 0025 the resolver reads append-only version tables, so a rename or
    # alias linkage insertion after the cutoff cannot generate keys for an earlier
    # replay. Without the tables (pre-0025 deployment) the legacy current-state gates
    # are retained. ``alias_refresh_gate`` deliberately forces the legacy path so the
    # residual mutable-alias channel remains measurable.
    use_versioned_names = (
        strict and not alias_refresh_gate and identity_version_tables_available(conn)
    )
    if use_versioned_names:
        name_sql = """
            SELECT p.player_id,
                   COALESCE(v.canonical_name, p.canonical_name) AS name,
                   'CANONICAL' AS kind
            FROM players p
            LEFT JOIN LATERAL (
                SELECT vv.canonical_name
                FROM player_name_versions vv
                WHERE vv.player_id = p.player_id
                  AND vv.valid_from <= %s
                ORDER BY vv.valid_from DESC, vv.player_name_version_id DESC
                LIMIT 1
            ) v ON TRUE
            WHERE p.player_id = ANY(%s)
              AND (
                  v.canonical_name IS NOT NULL
                  OR p.updated_at = p.created_at
              )
        """
        name_query_params: tuple[object, ...] = (as_of, unresolved)
        if allow_provider_alias_keys:
            name_sql += (
                " UNION ALL "
                "SELECT av.player_id, av.alias AS name, 'ALIAS' AS kind "
                "FROM player_alias_versions av "
                "WHERE av.player_id = ANY(%s) "
                "AND av.valid_from <= %s "
                "AND (av.first_observed_at IS NULL OR av.first_observed_at <= %s)"
            )
            name_query_params = (
                as_of,
                unresolved,
                unresolved,
                as_of,
                as_of,
            )
    else:
        name_where = ["player_id = ANY(%s)"]
        name_params: list[object] = [unresolved]
        if strict:
            # The risk is a *mutated* canonical name, not a row that first appeared
            # later: a row inserted after the cutoff and never updated still carries
            # the name it was created with. An UPDATE after the cutoff is what can
            # rewrite history.
            name_where.append("(updated_at <= %s OR updated_at = created_at)")
            name_params.append(as_of)
        name_sql = (
            "SELECT player_id, canonical_name AS name, 'CANONICAL' AS kind FROM players "
            f"WHERE {' AND '.join(name_where)}"
        )
        name_query_params = tuple(name_params)
        if allow_provider_alias_keys:
            name_sql += (
                " UNION ALL "
                "SELECT player_id, alias AS name, 'ALIAS' AS kind FROM player_aliases "
                f"WHERE {' AND '.join(name_where)} "
                "AND created_at <= %s "
                "AND (first_observed_at IS NULL OR first_observed_at <= %s)"
            )
            name_query_params = (*name_params, *name_params, as_of, as_of)
    name_rows = conn.execute(name_sql, name_query_params).fetchall()

    canonical_names: dict[int, str] = {}
    alias_names: dict[int, set[str]] = {}
    for player_id, name, kind in name_rows:
        player_id = int(player_id)
        if kind == "CANONICAL":
            canonical_names[player_id] = str(name)
        else:
            alias_names.setdefault(player_id, set()).add(str(name))

    lookup_live_ids: list[int] = []
    lookup_aliases: list[str] = []
    lookup_tiers: list[int] = []
    name_source_by_player: dict[int, str] = {}
    for player_id in unresolved:
        canonical_tier = sorted(
            {
                normalize_person_lookup(alias)
                for alias in tennis_data_aliases(canonical_names[player_id])
                if alias and normalize_person_lookup(alias)
            }
        ) if player_id in canonical_names else []
        alias_tier = (
            sorted(
                {
                    normalize_person_lookup(variant)
                    for name in alias_names.get(player_id, ())
                    for variant in tennis_data_aliases(name)
                    if variant and normalize_person_lookup(variant)
                }
            )
            if allow_provider_alias_keys
            else []
        )
        if canonical_tier:
            name_source_by_player[player_id] = NAME_SOURCE_CANONICAL
        elif alias_tier:
            name_source_by_player[player_id] = NAME_SOURCE_ALIAS
        else:
            name_source_by_player[player_id] = NAME_SOURCE_UNAVAILABLE
            resolutions[player_id] = IdentityResolution(
                player_id=player_id,
                resolved_player_id=player_id,
                method=(
                    METHOD_NO_PLAYER
                    if player_id not in canonical_names and player_id not in alias_names
                    else METHOD_NO_HISTORICAL_CANDIDATE
                ),
                name_source=NAME_SOURCE_UNAVAILABLE,
                reference_policy=policy,
                observed_at=as_of,
            )
            continue
        # Canonical keys are emitted first so a canonical candidate always outranks a
        # provider-alias candidate for the same player.
        for tier, keys in ((1, canonical_tier), (2, alias_tier)):
            for alias in keys:
                lookup_live_ids.append(player_id)
                lookup_aliases.append(alias)
                lookup_tiers.append(tier)
    if not lookup_live_ids:
        return resolutions

    # Stage E: unique historical alias candidates, resolved in one set-based query.
    if use_versioned_names:
        # Append-only linkage history: the row visible at ``as_of`` is authoritative.
        alias_source = "player_alias_versions"
        alias_join_where = [
            "pa.source_system = 'TENNIS_DATA'",
            "pa.player_id <> l.live_player_id",
            "(pa.first_observed_at IS NULL OR pa.first_observed_at <= %s)",
            "pa.valid_from <= %s",
        ]
        alias_join_params: list[object] = [as_of, as_of]
        alias_visibility_expr = "COALESCE(pai.finished_at, pai.started_at, pa.valid_from)"
        alias_visible_from_expr = (
            "GREATEST("
            "COALESCE(pai.finished_at, pai.started_at, pa.valid_from), "
            "COALESCE(pa.first_observed_at, pa.valid_from)"
            ")"
        )
    else:
        alias_source = "player_aliases"
        alias_join_where = [
            "pa.source_system = 'TENNIS_DATA'",
            "pa.player_id <> l.live_player_id",
            "(pa.first_observed_at IS NULL OR pa.first_observed_at <= %s)",
        ]
        alias_join_params = [as_of]
        if strict and alias_refresh_gate:
            # Optional ultra-conservative gate. ``updated_at`` is bumped by every
            # ``last_observed_at`` refresh, so this is NOT the default: it discards
            # legitimate PIT evidence for any alias the pipeline re-observed after the
            # cutoff. It exists to measure the size of the residual alias-mutation
            # channel and is deliberately disabled on the versioned path.
            alias_join_where.append("pa.updated_at <= %s")
            alias_join_params.append(as_of)
        alias_visibility_expr = "COALESCE(pai.finished_at, pai.started_at, pa.created_at)"
        alias_visible_from_expr = (
            "GREATEST("
            "COALESCE(pai.finished_at, pai.started_at, pa.created_at), "
            "COALESCE(pa.first_observed_at, pa.created_at)"
            ")"
        )
    candidate_rows = conn.execute(
        f"""
        WITH lookup AS (
            SELECT live_player_id, normalized_alias, tier
            FROM unnest(%s::bigint[], %s::text[], %s::int[])
                AS t(live_player_id, normalized_alias, tier)
        )
        SELECT DISTINCT l.live_player_id, l.tier, pa.player_id, l.normalized_alias,
               {alias_visible_from_expr} AS visible_from
        FROM lookup l
        JOIN {alias_source} pa
          ON pa.normalized_alias = l.normalized_alias
         AND {" AND ".join(alias_join_where)}
        LEFT JOIN ingest_runs pai ON pai.ingest_run_id = pa.ingest_run_id
        WHERE {alias_visibility_expr} <= %s
        AND EXISTS (
              SELECT 1
              FROM player_source_ids psi
              LEFT JOIN ingest_runs psi_ir ON psi_ir.ingest_run_id = psi.ingest_run_id
              WHERE psi.player_id = pa.player_id
                AND psi.source_system <> ALL(%s)
                AND COALESCE(psi_ir.finished_at, psi_ir.started_at, psi.created_at) <= %s
          )
        AND EXISTS (
            SELECT 1
            FROM match_result_observations hist_mro
            JOIN match_versions hist_mv
              ON hist_mv.match_id = hist_mro.match_id
             AND hist_mv.source_observed_at <= hist_mro.source_observed_at
             AND hist_mv.as_of <= hist_mro.as_of
            WHERE (hist_mv.player_1_id = pa.player_id OR hist_mv.player_2_id = pa.player_id)
              AND hist_mro.outcome_status = 'COMPLETED'
              AND hist_mro.source_observed_at <= %s
              AND hist_mro.as_of <= %s
        )
        ORDER BY l.live_player_id, l.tier, pa.player_id, l.normalized_alias
        """,
        (
            lookup_live_ids,
            lookup_aliases,
            lookup_tiers,
            *alias_join_params,
            as_of,
            live_sources,
            as_of,
            as_of,
            as_of,
        ),
    ).fetchall()

    # candidates_by_live[player][tier][candidate] = set(aliases)
    candidates_by_live: dict[int, dict[int, dict[int, set[str]]]] = {}
    visible_by_candidate: dict[tuple[int, int, int], object] = {}
    for live_player_id, tier, candidate_player_id, normalized_alias, visible_from in candidate_rows:
        candidates_by_live.setdefault(int(live_player_id), {}).setdefault(
            int(tier), {}
        ).setdefault(int(candidate_player_id), set()).add(str(normalized_alias))
        visible_by_candidate[(int(live_player_id), int(tier), int(candidate_player_id))] = (
            visible_from
        )

    for player_id in unresolved:
        if resolutions[player_id].method != METHOD_NO_CANDIDATE:
            # Already classified in Stage D (no usable name, or not a player row).
            continue
        tiers = candidates_by_live.get(player_id, {})
        chosen_tier = 1 if tiers.get(1) else 2
        candidates = tiers.get(chosen_tier, {})
        name_source = (
            NAME_SOURCE_CANONICAL if chosen_tier == 1 else NAME_SOURCE_ALIAS
        )
        if len(candidates) == 1:
            candidate_id, aliases = next(iter(candidates.items()))
            resolutions[player_id] = IdentityResolution(
                player_id=player_id,
                resolved_player_id=candidate_id,
                method=METHOD_ALIAS,
                candidate_count=1,
                matched_alias=min(aliases),
                visible_from=visible_by_candidate.get(
                    (player_id, chosen_tier, candidate_id)
                ),
                evidence="tennis_data_alias_pit",
                name_source=name_source,
                reference_policy=policy,
                observed_at=as_of,
            )
        elif len(candidates) > 1:
            resolutions[player_id] = IdentityResolution(
                player_id=player_id,
                resolved_player_id=player_id,
                method=METHOD_AMBIGUOUS,
                candidate_count=len(candidates),
                name_source=name_source,
                reference_policy=policy,
                observed_at=as_of,
            )
        else:
            resolutions[player_id] = IdentityResolution(
                player_id=player_id,
                resolved_player_id=player_id,
                method=METHOD_NO_HISTORICAL_CANDIDATE,
                name_source=name_source_by_player.get(player_id, NAME_SOURCE_NONE),
                reference_policy=policy,
                observed_at=as_of,
            )
    return resolutions


def resolve_model_player_ids(
    conn: psycopg.Connection,
    *,
    player_ids: Iterable[int],
    as_of: datetime,
    reference_policy: str = REFERENCE_POLICY_STRICT,
    allow_provider_alias_keys: bool = False,
    alias_refresh_gate: bool = False,
) -> dict[int, int]:
    """Bulk, set-based equivalent of :func:`resolve_model_player_id`.

    Resolves every supplied player ID to its model identity using a bounded number of
    SQL statements rather than one query per player. Semantics are identical to calling
    :func:`resolve_model_player_id` individually: historical identities map to
    themselves, an active latest PIT-visible persisted bridge wins, a single unique
    ``TENNIS_DATA`` alias candidate wins, and zero or multiple candidates fail closed by
    retaining the supplied player ID. Nothing is written and no source mapping changes.
    """
    resolutions = resolve_model_player_ids_with_provenance(
        conn,
        player_ids=player_ids,
        as_of=as_of,
        reference_policy=reference_policy,
        allow_provider_alias_keys=allow_provider_alias_keys,
        alias_refresh_gate=alias_refresh_gate,
    )
    return {
        player_id: resolution.resolved_player_id
        for player_id, resolution in resolutions.items()
    }


__all__ = [
    "IDENTITY_REFERENCE_POLICY_VERSION",
    "NAME_SOURCE_ALIAS",
    "NAME_SOURCE_CANONICAL",
    "NAME_SOURCE_NONE",
    "NAME_SOURCE_UNAVAILABLE",
    "REFERENCE_POLICIES",
    "REFERENCE_POLICY_PERMISSIVE",
    "REFERENCE_POLICY_STRICT",
    "IdentityResolution",
    "has_historical_model_identity",
    "has_non_api_source_identity",
    "resolve_model_player_id",
    "resolve_model_player_ids",
    "resolve_model_player_ids_with_provenance",
]
