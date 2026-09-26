"""Replay-local chronological history cache.

The baseline replay loader re-reads the entire multi-year A2/A3 history from
PostgreSQL for every replay cutoff. Profiling showed that repeated full-history
DB loading is the dominant cost (A2 ~64s / 130k rows; A3 far worse per row).

This cache loads the required history **once** at the final cutoff. The first
replay cutoff is built with the exact baseline constructors; subsequent A2/A3
cutoffs advance chronologically with the same incremental primitives used by
live production. Agent 1 remains a deterministic in-memory replay over the
single preloaded history set. Point-in-time visibility is preserved at every
cutoff.

A conservative PIT-stability guard disables the cache (falling back to the
baseline per-cutoff DB loads) whenever a match has two result/stat observations
whose visibility straddles a replay cutoff, because a single final load would
otherwise collapse the earlier observation.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
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
from sports_edge_tennis.runner.agent2_live_snapshot import (
    advance_agent2_history_snapshot,
)
from sports_edge_tennis.runner.agent3_live_snapshot import (
    advance_live_agent3_history_snapshot,
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
        SELECT
            match_id,
            GREATEST(source_observed_at, as_of) AS vc
        FROM match_result_observations
        WHERE GREATEST(source_observed_at, as_of) <= %s
        ORDER BY match_id, vc, match_result_observation_id
        """,
        (final,),
    ).fetchall()
    by_match: dict[int, list[datetime]] = {}
    for match_id, vc in result_rows:
        by_match.setdefault(int(match_id), []).append(vc)
    for values in by_match.values():
        ordered_values = sorted(set(values))
        for vc_old, vc_new in zip(ordered_values, ordered_values[1:]):
            if _straddles_any_cutoff(vc_new, vc_old, ordered):
                return False

    stat_rows = conn.execute(
        """
        SELECT
            match_id,
            player_id,
            GREATEST(source_observed_at, as_of) AS vc
        FROM match_stat_observations
        WHERE observation_kind = 'RAW_SOURCE'
          AND GREATEST(source_observed_at, as_of) <= %s
        ORDER BY match_id, player_id, vc, match_stat_observation_id
        """,
        (final,),
    ).fetchall()
    by_key: dict[tuple[int, int], list[datetime]] = {}
    for match_id, player_id, vc in stat_rows:
        by_key.setdefault((int(match_id), int(player_id)), []).append(vc)
    for values in by_key.values():
        ordered_values = sorted(set(values))
        for vc_old, vc_new in zip(ordered_values, ordered_values[1:]):
            if _straddles_any_cutoff(vc_new, vc_old, ordered):
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
        self._a1_visibility: list[datetime] = []
        self._a2_visibility: list[datetime] = []
        self._a3_visibility: list[datetime] = []
        if self.mode == MODE_CACHE_LOAD_ONCE:
            self._preload()

    def _preload(self) -> None:
        final = self._cutoffs[-1]
        first = self._cutoffs[0]
        self._a1_all = sorted(
            load_agent1_history(self._conn, cutoff=final),
            key=lambda item: max(item.source_observed_at, item.as_of),
        )
        self._a2_all = sorted(
            load_agent2_history(self._conn, cutoff=final),
            key=lambda item: item.visibility_cohort,
        )
        a3_days = (self._a3_lookback_days * 2) + 2
        self._a3_all = sorted(
            load_agent3_history(
                self._conn,
                cutoff=final,
                date_start=(first - timedelta(days=a3_days)).date(),
            ),
            key=lambda item: item.visibility_cohort,
        )
        self._a1_visibility = [
            max(item.source_observed_at, item.as_of) for item in self._a1_all
        ]
        self._a2_visibility = [item.visibility_cohort for item in self._a2_all]
        self._a3_visibility = [item.visibility_cohort for item in self._a3_all]
        print(
            "REPLAY_HISTORY_CACHE_READY "
            f"mode={self.mode} cutoffs={len(self._cutoffs)} "
            f"a1={len(self._a1_all)} a2={len(self._a2_all)} a3={len(self._a3_all)}",
            flush=True,
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

        a1_end = bisect_right(self._a1_visibility, cutoff)
        a2_end = bisect_left(self._a2_visibility, cutoff)
        a3_end = bisect_left(self._a3_visibility, cutoff)
        visible1 = a1[:a1_end]
        visible2 = a2[:a2_end]
        visible3 = a3[:a3_end]

        previous_cutoffs = [value for value in self._cache if value < cutoff]
        if previous_cutoffs:
            previous_cutoff = max(previous_cutoffs)
            previous = self._cache[previous_cutoff]
            a2_start = bisect_left(self._a2_visibility, previous_cutoff)
            a3_start = bisect_left(self._a3_visibility, previous_cutoff)
            delta2 = a2[a2_start:a2_end]
            delta3 = a3[a3_start:a3_end]
            agent2_snapshot = advance_agent2_history_snapshot(
                previous.agent2_snapshot,
                delta2,
                cutoff=cutoff,
            )
            agent3_snapshot = advance_live_agent3_history_snapshot(
                previous.agent3_snapshot,
                delta3,
                previous_cutoff=previous_cutoff,
                cutoff=cutoff,
            )
            print(
                "REPLAY_HISTORY_CACHE_INCREMENTAL "
                f"previous={previous_cutoff.isoformat()} cutoff={cutoff.isoformat()} "
                f"a2_delta={len(delta2)} a3_delta={len(delta3)}",
                flush=True,
            )
        else:
            agent2_snapshot = build_agent2_snapshot(a2, cutoff=cutoff)
            agent3_snapshot = build_live_agent3_history_snapshot(a3, cutoff=cutoff)
            print(
                "REPLAY_HISTORY_CACHE_BOOTSTRAP "
                f"cutoff={cutoff.isoformat()} a2_visible={len(visible2)} "
                f"a3_visible={len(visible3)}",
                flush=True,
            )

        return ReplayHistorySet(
            cutoff=cutoff,
            agent1_replay=replay_agent1_history(a1, cutoff, AGENT1_DEFAULT_CONFIG),
            agent2_snapshot=agent2_snapshot,
            agent3_snapshot=agent3_snapshot,
            agent1_snapshot_id=_agent1_snapshot_id(visible1, cutoff),
            agent2_snapshot_id=_agent2_snapshot_id(visible2, cutoff),
            agent3_snapshot_id=_agent3_snapshot_id(visible3, cutoff),
            mode=MODE_CACHE_LOAD_ONCE,
        )

    def history_for(self, cutoff: datetime) -> ReplayHistorySet:
        if cutoff in self._cache:
            return self._cache[cutoff]

        if self.mode == MODE_CACHE_LOAD_ONCE:
            # Build planned cutoffs chronologically even when callers request a
            # later cutoff first. This guarantees every snapshot is advanced
            # from its immediate predecessor rather than silently rebuilding
            # the full A2/A3 history.
            for planned_cutoff in self._cutoffs:
                if planned_cutoff > cutoff:
                    break
                if planned_cutoff not in self._cache:
                    self._cache[planned_cutoff] = self._cached_set(planned_cutoff)
            if cutoff not in self._cache:
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
