"""Replay-local chronological history cache.

The baseline replay loader re-reads the entire multi-year A2/A3 history from
PostgreSQL for every replay cutoff. Profiling showed that repeated full-history
DB loading is the dominant cost (A2 ~64s / 130k rows; A3 far worse per row).

This cache loads the required history **once** at the final cutoff and rebuilds
each earlier cutoff in memory using the exact same baseline constructors
(``replay_history``, ``build_history_snapshot``,
``build_live_agent3_history_snapshot``). Point-in-time visibility is preserved
because those constructors filter observations by their own cutoff.

A conservative PIT-stability guard disables the cache (falling back to the
baseline per-cutoff DB loads) whenever a match has two result/stat observations
whose visibility straddles a replay cutoff, because a single final load would
otherwise collapse the earlier observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG as AGENT1_DEFAULT_CONFIG,
    replay_history as replay_agent1_history,
)
from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.data.agent2_history import (
    build_history_snapshot as build_agent2_snapshot,
    load_agent2_history,
)
from sports_edge_tennis.data.agent3_history import load_agent3_history
from sports_edge_tennis.replay.serde import payload_sha256
from sports_edge_tennis.runner.agent3_live_snapshot import (
    build_live_agent3_history_snapshot,
)

MODE_CACHE_LOAD_ONCE = "CACHE_LOAD_ONCE"
MODE_BASELINE_PER_CUTOFF = "BASELINE_PER_CUTOFF"


@dataclass(frozen=True)
class ReplayHistorySet:
    cutoff: datetime
    agent1_replay: object
    agent2_snapshot: object
    agent3_snapshot: object
    agent1_snapshot_id: str
    agent2_snapshot_id: str
    agent3_snapshot_id: str
    mode: str


def _observation_max(observations, attribute: str) -> datetime | None:
    values = [getattr(item, attribute) for item in observations]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _agent1_snapshot_id(observations, cutoff: datetime) -> str:
    return payload_sha256(
        {
            "kind": "AGENT_1",
            "cutoff": cutoff,
            "count": len(observations),
            "max_observed": _observation_max(observations, "source_observed_at"),
        }
    )


def _agent2_snapshot_id(observations, cutoff: datetime) -> str:
    return payload_sha256(
        {
            "kind": "AGENT_2",
            "cutoff": cutoff,
            "count": len(observations),
            "max_visible": _observation_max(observations, "result_as_of"),
        }
    )


def _agent3_snapshot_id(observations, cutoff: datetime) -> str:
    return payload_sha256(
        {
            "kind": "AGENT_3",
            "cutoff": cutoff,
            "count": len(observations),
            "max_visible": _observation_max(observations, "result_as_of"),
        }
    )


def _straddles_any_cutoff(vc_new: datetime, vc_old: datetime, cutoffs: list[datetime]) -> bool:
    if vc_old >= vc_new:
        return False
    for cutoff in cutoffs:
        if vc_old <= cutoff < vc_new:
            return True
    return False


def cache_is_pit_stable(conn: psycopg.Connection, *, cutoffs: list[datetime]) -> bool:
    """Return True only when a single final load is PIT-equivalent for all cutoffs."""

    ordered = sorted(set(cutoffs))
    if len(ordered) < 2:
        return True
    final = ordered[-1]

    result_rows = conn.execute(
        """
        SELECT match_id, vc FROM (
            SELECT
                match_id,
                GREATEST(source_observed_at, as_of) AS vc,
                row_number() OVER (
                    PARTITION BY match_id
                    ORDER BY GREATEST(source_observed_at, as_of) DESC, match_result_observation_id DESC
                ) AS rn
            FROM match_result_observations
            WHERE GREATEST(source_observed_at, as_of) <= %s
        ) ranked
        WHERE rn <= 2
        """,
        (final,),
    ).fetchall()
    by_match: dict[int, list[datetime]] = {}
    for match_id, vc in result_rows:
        by_match.setdefault(int(match_id), []).append(vc)
    for values in by_match.values():
        if len(values) == 2 and _straddles_any_cutoff(values[0], values[1], ordered):
            return False

    stat_rows = conn.execute(
        """
        SELECT match_id, player_id, vc FROM (
            SELECT
                match_id,
                player_id,
                GREATEST(source_observed_at, as_of) AS vc,
                row_number() OVER (
                    PARTITION BY match_id, player_id
                    ORDER BY GREATEST(source_observed_at, as_of) DESC, match_stat_observation_id DESC
                ) AS rn
            FROM match_stat_observations
            WHERE observation_kind = 'RAW_SOURCE'
              AND GREATEST(source_observed_at, as_of) <= %s
        ) ranked
        WHERE rn <= 2
        """,
        (final,),
    ).fetchall()
    by_key: dict[tuple[int, int], list[datetime]] = {}
    for match_id, player_id, vc in stat_rows:
        by_key.setdefault((int(match_id), int(player_id)), []).append(vc)
    for values in by_key.values():
        if len(values) == 2 and _straddles_any_cutoff(values[0], values[1], ordered):
            return False

    return True


class ReplayHistoryCache:
    """Load history once and expose PIT-correct history sets for each cutoff."""

    def __init__(
        self,
        conn: psycopg.Connection,
        *,
        cutoffs: list[datetime],
        a3_lookback_days: int,
    ) -> None:
        ordered = sorted(set(cutoffs))
        if not ordered:
            raise ValueError("at least one cutoff is required")
        self._conn = conn
        self._cutoffs = ordered
        self._a3_lookback_days = a3_lookback_days
        self.mode = (
            MODE_CACHE_LOAD_ONCE
            if cache_is_pit_stable(conn, cutoffs=ordered)
            else MODE_BASELINE_PER_CUTOFF
        )
        self._cache: dict[datetime, ReplayHistorySet] = {}
        self._a1_all = None
        self._a2_all = None
        self._a3_all = None
        if self.mode == MODE_CACHE_LOAD_ONCE:
            self._preload()

    def _preload(self) -> None:
        final = self._cutoffs[-1]
        first = self._cutoffs[0]
        self._a1_all = load_agent1_history(self._conn, cutoff=final)
        self._a2_all = load_agent2_history(self._conn, cutoff=final)
        a3_days = (self._a3_lookback_days * 2) + 2
        self._a3_all = load_agent3_history(
            self._conn,
            cutoff=final,
            date_start=(first - timedelta(days=a3_days)).date(),
        )

    def _baseline_set(self, cutoff: datetime) -> ReplayHistorySet:
        a1 = load_agent1_history(self._conn, cutoff=cutoff)
        a2 = load_agent2_history(self._conn, cutoff=cutoff)
        a3_days = (self._a3_lookback_days * 2) + 2
        a3 = load_agent3_history(
            self._conn,
            cutoff=cutoff,
            date_start=(cutoff - timedelta(days=a3_days)).date(),
        )
        return ReplayHistorySet(
            cutoff=cutoff,
            agent1_replay=replay_agent1_history(a1, cutoff, AGENT1_DEFAULT_CONFIG),
            agent2_snapshot=build_agent2_snapshot(a2, cutoff=cutoff),
            agent3_snapshot=build_live_agent3_history_snapshot(a3, cutoff=cutoff),
            agent1_snapshot_id=_agent1_snapshot_id(a1, cutoff),
            agent2_snapshot_id=_agent2_snapshot_id(a2, cutoff),
            agent3_snapshot_id=_agent3_snapshot_id(a3, cutoff),
            mode=MODE_BASELINE_PER_CUTOFF,
        )

    def _cached_set(self, cutoff: datetime) -> ReplayHistorySet:
        a1 = self._a1_all
        a2 = self._a2_all
        a3 = self._a3_all
        # Snapshot ids mirror the baseline per-cutoff load's visible row set.
        visible1 = [item for item in a1 if item.source_observed_at <= cutoff and item.as_of <= cutoff]
        visible2 = [
            item
            for item in a2
            if item.result_source_observed_at < cutoff and item.result_as_of < cutoff
        ]
        visible3 = [
            item
            for item in a3
            if item.result_source_observed_at < cutoff and item.result_as_of < cutoff
        ]
        return ReplayHistorySet(
            cutoff=cutoff,
            agent1_replay=replay_agent1_history(a1, cutoff, AGENT1_DEFAULT_CONFIG),
            agent2_snapshot=build_agent2_snapshot(a2, cutoff=cutoff),
            agent3_snapshot=build_live_agent3_history_snapshot(a3, cutoff=cutoff),
            agent1_snapshot_id=_agent1_snapshot_id(visible1, cutoff),
            agent2_snapshot_id=_agent2_snapshot_id(visible2, cutoff),
            agent3_snapshot_id=_agent3_snapshot_id(visible3, cutoff),
            mode=MODE_CACHE_LOAD_ONCE,
        )

    def history_for(self, cutoff: datetime) -> ReplayHistorySet:
        if cutoff not in self._cache:
            if self.mode == MODE_CACHE_LOAD_ONCE:
                self._cache[cutoff] = self._cached_set(cutoff)
            else:
                self._cache[cutoff] = self._baseline_set(cutoff)
        return self._cache[cutoff]


__all__ = [
    "MODE_CACHE_LOAD_ONCE",
    "MODE_BASELINE_PER_CUTOFF",
    "ReplayHistorySet",
    "ReplayHistoryCache",
    "cache_is_pit_stable",
]
