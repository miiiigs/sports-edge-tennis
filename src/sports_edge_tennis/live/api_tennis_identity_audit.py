"""Read-only API-Tennis provider identity audit.

Provider-real fixtures still fail closed when a provider player has no exact
``player_source_ids`` mapping. This module reports those gaps without ever merging
or mutating identity:

* authority order is exact provider-source mapping first, then uniquely matching
  deterministic ``TENNIS_DATA`` alias evidence;
* alias evidence must be a *unique* candidate that already carries a non-live
  historical identity and has PIT-visible completed history at ``as_of``;
* zero or multiple candidates stay blocked and are reported with their exact
  supporting and conflicting evidence;
* ambiguous canonical duplicate aliases are surfaced, not merged.

It is deliberately read-only: no player, alias or source-id row is created or
changed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from sports_edge_tennis.data.model_identity_bridge import LIVE_FIXTURE_SOURCE_SYSTEMS
from sports_edge_tennis.data.normalize import (
    normalize_person_lookup,
    tennis_data_aliases,
)


@dataclass(frozen=True)
class ProviderIdentityAudit:
    source_system: str
    source_player_id: str
    provider_name: str | None
    exact_mapped_player_id: int | None
    candidate_player_ids: tuple[int, ...]
    deterministic_resolution: bool
    resolution_player_id: int | None
    evidence: str
    conflict: str | None
    safe: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_system": self.source_system,
            "source_player_id": self.source_player_id,
            "provider_name": self.provider_name,
            "exact_mapped_player_id": self.exact_mapped_player_id,
            "candidate_player_ids": list(self.candidate_player_ids),
            "candidate_count": len(self.candidate_player_ids),
            "deterministic_resolution": self.deterministic_resolution,
            "resolution_player_id": self.resolution_player_id,
            "evidence": self.evidence,
            "conflict": self.conflict,
            "safe": self.safe,
        }


@dataclass(frozen=True)
class DuplicateAliasGroup:
    source_system: str
    normalized_alias: str
    player_ids: tuple[int, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_system": self.source_system,
            "normalized_alias": self.normalized_alias,
            "player_ids": list(self.player_ids),
        }


def _alias_keys(name: str) -> tuple[str, ...]:
    keys = {
        normalize_person_lookup(alias)
        for alias in tennis_data_aliases(name)
        if alias and normalize_person_lookup(alias)
    }
    return tuple(sorted(keys))


def audit_provider_identity(
    conn: psycopg.Connection,
    *,
    provider_players: Iterable[tuple[str, str, str | None]],
    as_of: datetime,
) -> tuple[ProviderIdentityAudit, ...]:
    """Audit provider player identities without mutating anything."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    requests = sorted(
        {
            (str(source_system), str(source_player_id), provider_name)
            for source_system, source_player_id, provider_name in provider_players
            if str(source_player_id).strip()
        }
    )
    audit: list[ProviderIdentityAudit] = []
    live_sources = list(LIVE_FIXTURE_SOURCE_SYSTEMS)

    for source_system, source_player_id, provider_name in requests:
        exact = conn.execute(
            """
            SELECT player_id
            FROM player_source_ids
            WHERE source_system = %s
              AND source_player_id = %s
            """,
            (source_system, source_player_id),
        ).fetchone()
        if exact is not None:
            player_id = int(exact[0])
            audit.append(
                ProviderIdentityAudit(
                    source_system=source_system,
                    source_player_id=source_player_id,
                    provider_name=provider_name,
                    exact_mapped_player_id=player_id,
                    candidate_player_ids=(player_id,),
                    deterministic_resolution=True,
                    resolution_player_id=player_id,
                    evidence="exact_provider_source_mapping",
                    conflict=None,
                    safe=True,
                )
            )
            continue

        if not provider_name:
            audit.append(
                ProviderIdentityAudit(
                    source_system=source_system,
                    source_player_id=source_player_id,
                    provider_name=None,
                    exact_mapped_player_id=None,
                    candidate_player_ids=(),
                    deterministic_resolution=False,
                    resolution_player_id=None,
                    evidence="no_provider_name_and_no_exact_mapping",
                    conflict="provider_name_missing",
                    safe=False,
                )
            )
            continue

        aliases = _alias_keys(provider_name)
        candidates: set[int] = set()
        if aliases:
            rows = conn.execute(
                """
                SELECT DISTINCT pa.player_id
                FROM player_aliases pa
                WHERE pa.source_system = 'TENNIS_DATA'
                  AND pa.normalized_alias = ANY(%s)
                  AND EXISTS (
                      SELECT 1
                      FROM player_source_ids psi
                      WHERE psi.player_id = pa.player_id
                        AND psi.source_system <> ALL(%s)
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM match_result_observations hist_mro
                      JOIN match_versions hist_mv
                        ON hist_mv.match_id = hist_mro.match_id
                       AND hist_mv.source_observed_at <= hist_mro.source_observed_at
                       AND hist_mv.as_of <= hist_mro.as_of
                      WHERE (hist_mv.player_1_id = pa.player_id
                             OR hist_mv.player_2_id = pa.player_id)
                        AND hist_mro.outcome_status = 'COMPLETED'
                        AND hist_mro.source_observed_at <= %s
                        AND hist_mro.as_of <= %s
                  )
                ORDER BY pa.player_id
                """,
                (list(aliases), live_sources, as_of, as_of),
            ).fetchall()
            candidates = {int(row[0]) for row in rows}

        candidate_ids = tuple(sorted(candidates))
        if len(candidate_ids) == 1:
            audit.append(
                ProviderIdentityAudit(
                    source_system=source_system,
                    source_player_id=source_player_id,
                    provider_name=provider_name,
                    exact_mapped_player_id=None,
                    candidate_player_ids=candidate_ids,
                    deterministic_resolution=True,
                    resolution_player_id=candidate_ids[0],
                    evidence=(
                        "unique_tennis_data_alias_with_historical_identity "
                        f"aliases={list(aliases)}"
                    ),
                    conflict=None,
                    safe=False,
                )
            )
        else:
            audit.append(
                ProviderIdentityAudit(
                    source_system=source_system,
                    source_player_id=source_player_id,
                    provider_name=provider_name,
                    exact_mapped_player_id=None,
                    candidate_player_ids=candidate_ids,
                    deterministic_resolution=False,
                    resolution_player_id=None,
                    evidence=(
                        "no_exact_provider_mapping "
                        f"alias_candidates={list(candidate_ids)} aliases={list(aliases)}"
                    ),
                    conflict=(
                        "multiple_plausible_canonical_players"
                        if len(candidate_ids) > 1
                        else "no_plausible_canonical_player"
                    ),
                    safe=False,
                )
            )
    return tuple(audit)


def audit_canonical_duplicate_aliases(
    conn: psycopg.Connection,
    *,
    source_system: str = "TENNIS_DATA",
) -> tuple[DuplicateAliasGroup, ...]:
    """Surface canonical players that share one normalized alias (no merge)."""
    rows = conn.execute(
        """
        SELECT pa.normalized_alias, array_agg(DISTINCT pa.player_id ORDER BY pa.player_id)
        FROM player_aliases pa
        WHERE pa.source_system = %s
        GROUP BY pa.normalized_alias
        HAVING count(DISTINCT pa.player_id) > 1
        ORDER BY pa.normalized_alias
        """,
        (source_system,),
    ).fetchall()
    return tuple(
        DuplicateAliasGroup(
            source_system=source_system,
            normalized_alias=str(alias),
            player_ids=tuple(int(value) for value in player_ids),
        )
        for alias, player_ids in rows
    )


__all__ = [
    "DuplicateAliasGroup",
    "ProviderIdentityAudit",
    "audit_canonical_duplicate_aliases",
    "audit_provider_identity",
]
