"""Replay-local chronological history cache.

The baseline replay loader re-reads the entire multi-year A2/A3 history from
PostgreSQL for every replay cutoff. Profiling showed that repeated full-history
DB loading is the dominant cost (A2 ~64s / 130k rows; A3 far worse per row).

The cache partitions replay cutoffs into point-in-time-stable chronological
segments. Each segment performs one exact database re-baseline at its final
cutoff, bootstraps A2/A3 once at the segment's first cutoff, then advances
chronologically with the same incremental primitives used by live production.
Agent 1 remains a deterministic in-memory replay over the segment load.

Segment boundaries are created by corrected result cohorts, corrected complete
A2 stat cohorts, and persisted model-identity bridge activations. This preserves
the exact historical state on both sides of corrections without paying the cost
of a full A2/A3 rebuild for every replay cutoff.
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
from sports_edge_tennis.data.model_identity_bridge import LIVE_FIXTURE_SOURCE_SYSTEMS
from sports_edge_tennis.replay.serde import payload_sha256
from sports_edge_tennis.runner.agent2_live_snapshot import (
    advance_agent2_history_snapshot,
)
from sports_edge_tennis.runner.agent3_live_snapshot import (
    advance_live_agent3_history_snapshot,
    build_live_agent3_history_snapshot,
)

MODE_CACHE_LOAD_ONCE = "CACHE_LOAD_ONCE"
MODE_CACHE_SEGMENTED = "CACHE_SEGMENTED"
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



def cache_correction_boundaries(
    conn: psycopg.Connection,
    *,
    cutoffs: list[datetime],
) -> tuple[datetime, ...]:
    """Return visibility boundaries that can change a final-cutoff history load.

    A2/A3 select the latest PIT-visible result/stat cohort per match. A later
    correction replaces the earlier row when a segment is loaded at its final
    cutoff. Persisted identity bridges can also change model-player resolution
    at activated_at. Each transition becomes a hard segment boundary when it
    lands inside the requested replay window.
    """

    ordered = sorted(set(cutoffs))
    if len(ordered) < 2:
        return ()
    first = ordered[0]
    final = ordered[-1]
    boundaries: set[datetime] = set()

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
        for _vc_old, vc_new in zip(ordered_values, ordered_values[1:]):
            if first < vc_new <= final:
                boundaries.add(vc_new)

    stat_rows = conn.execute(
        """
        SELECT
            match_id,
            GREATEST(source_observed_at, as_of) AS vc
        FROM match_stat_observations
        WHERE observation_kind = 'RAW_SOURCE'
          AND GREATEST(source_observed_at, as_of) <= %s
        GROUP BY match_id, source_system, source_observed_at, as_of
        HAVING count(*) = 2
           AND count(DISTINCT player_id) = 2
        ORDER BY match_id, vc
        """,
        (final,),
    ).fetchall()
    stat_cohorts_by_match: dict[int, list[datetime]] = {}
    for match_id, vc in stat_rows:
        stat_cohorts_by_match.setdefault(int(match_id), []).append(vc)
    for match_id, values in stat_cohorts_by_match.items():
        ordered_stats = sorted(set(values))
        first_result_visibility = min(by_match.get(match_id, ()), default=None)
        for stat_index, vc in enumerate(ordered_stats):
            if not (first < vc <= final):
                continue
            # A later complete stat cohort can replace the cohort attached by
            # the segment-final history load, so every correction is a hard
            # boundary. The first stat cohort only needs a boundary when the
            # match result was already visible earlier. If first result and
            # first stats become visible together, the entire history row is
            # absent before that instant, so the snapshot cutoff filter already
            # prevents leakage and no extra segment is necessary.
            if stat_index > 0 or (
                first_result_visibility is not None
                and first_result_visibility < vc
            ):
                boundaries.add(vc)

    bridge_rows = conn.execute(
        """
        SELECT DISTINCT activated_at
        FROM model_identity_bridge_events
        WHERE activated_at > %s
          AND activated_at <= %s
        ORDER BY activated_at
        """,
        (first, final),
    ).fetchall()
    boundaries.update(row[0] for row in bridge_rows if row[0] is not None)

    # The fallback alias resolver can change a live-only player's model identity
    # when a candidate historical player gets its first PIT-visible completed
    # result. Since player aliases/source IDs are reference identity data, that
    # first completed-result visibility is the temporal fact that makes the
    # candidate usable by resolve_model_player_ids.
    alias_evidence_rows = conn.execute(
        """
        WITH candidate_players AS (
            SELECT DISTINCT pa.player_id
            FROM player_aliases pa
            WHERE pa.source_system = 'TENNIS_DATA'
              AND EXISTS (
                  SELECT 1
                  FROM player_source_ids psi
                  WHERE psi.player_id = pa.player_id
                    AND psi.source_system <> ALL(%s)
              )
        ),
        candidate_evidence AS (
            SELECT
                cp.player_id,
                GREATEST(mro.source_observed_at, mro.as_of) AS visible_at
            FROM candidate_players cp
            JOIN match_versions mv
              ON mv.player_1_id = cp.player_id
              OR mv.player_2_id = cp.player_id
            JOIN match_result_observations mro
              ON mro.match_id = mv.match_id
             AND mro.outcome_status = 'COMPLETED'
             AND mv.source_observed_at <= mro.source_observed_at
             AND mv.as_of <= mro.as_of
            WHERE GREATEST(mro.source_observed_at, mro.as_of) <= %s
        )
        SELECT player_id, min(visible_at) AS first_visible_at
        FROM candidate_evidence
        GROUP BY player_id
        HAVING min(visible_at) > %s
           AND min(visible_at) <= %s
        ORDER BY first_visible_at, player_id
        """,
        (list(LIVE_FIXTURE_SOURCE_SYSTEMS), final, first, final),
    ).fetchall()
    boundaries.update(row[1] for row in alias_evidence_rows if row[1] is not None)

    # A2 can recover a NULL match surface from point-in-time-visible tournament
    # evidence at the history-loader cutoff. Segment on those relevant surface
    # evidence arrivals so a later recovered surface cannot be attached to a
    # historical row at an earlier cutoff.
    surface_evidence_rows = conn.execute(
        """
        WITH selected_results AS (
            SELECT
                mro.match_id,
                mro.source_observed_at,
                mro.as_of,
                ROW_NUMBER() OVER (
                    PARTITION BY mro.match_id
                    ORDER BY
                        mro.as_of DESC,
                        mro.source_observed_at DESC,
                        mro.match_result_observation_id DESC
                ) AS result_rank
            FROM match_result_observations mro
            WHERE mro.source_observed_at <= %s
              AND mro.as_of <= %s
        ),
        selected_versions AS (
            SELECT
                sr.match_id,
                te.tournament_id,
                t.canonical_name,
                t.tour_scope::text AS tour_scope,
                te.season_year,
                te.surface::text AS edition_surface,
                mv.surface::text AS match_surface,
                ROW_NUMBER() OVER (
                    PARTITION BY sr.match_id
                    ORDER BY
                        mv.as_of DESC,
                        mv.source_observed_at DESC,
                        mv.match_version_id DESC
                ) AS version_rank
            FROM selected_results sr
            JOIN matches m
              ON m.match_id = sr.match_id
            JOIN tournament_editions te
              ON te.tournament_edition_id = m.tournament_edition_id
            JOIN tournaments t
              ON t.tournament_id = te.tournament_id
            JOIN match_versions mv
              ON mv.match_id = sr.match_id
             AND mv.source_observed_at <= sr.source_observed_at
             AND mv.as_of <= sr.as_of
            WHERE sr.result_rank = 1
        ),
        missing_targets AS (
            SELECT
                match_id,
                tournament_id,
                canonical_name,
                tour_scope,
                season_year
            FROM selected_versions
            WHERE version_rank = 1
              AND match_surface IS NULL
              AND edition_surface IS NULL
        ),
        surface_evidence AS (
            SELECT DISTINCT
                GREATEST(mv_hist.source_observed_at, mv_hist.as_of) AS visible_at
            FROM missing_targets target
            JOIN tournaments t_hist
              ON t_hist.tournament_id = target.tournament_id
              OR (
                  t_hist.canonical_name = target.canonical_name
                  AND t_hist.tour_scope::text = target.tour_scope
              )
            JOIN tournament_editions te_hist
              ON te_hist.tournament_id = t_hist.tournament_id
             AND te_hist.season_year <= target.season_year
            JOIN matches m_hist
              ON m_hist.tournament_edition_id = te_hist.tournament_edition_id
            JOIN match_versions mv_hist
              ON mv_hist.match_id = m_hist.match_id
             AND mv_hist.surface IS NOT NULL
            WHERE GREATEST(mv_hist.source_observed_at, mv_hist.as_of) > %s
              AND GREATEST(mv_hist.source_observed_at, mv_hist.as_of) <= %s
        )
        SELECT visible_at
        FROM surface_evidence
        ORDER BY visible_at
        """,
        (final, final, first, final),
    ).fetchall()
    boundaries.update(row[0] for row in surface_evidence_rows if row[0] is not None)

    return tuple(sorted(boundaries))


def cache_cutoff_segments(
    conn: psycopg.Connection,
    *,
    cutoffs: list[datetime],
) -> tuple[tuple[datetime, ...], ...]:
    """Partition replay cutoffs into PIT-stable chronological cache segments."""

    ordered = sorted(set(cutoffs))
    if not ordered:
        return ()
    if len(ordered) == 1:
        return (tuple(ordered),)

    boundaries = cache_correction_boundaries(conn, cutoffs=ordered)
    grouped: dict[int, list[datetime]] = {}
    for cutoff in ordered:
        segment_key = bisect_right(boundaries, cutoff)
        grouped.setdefault(segment_key, []).append(cutoff)
    return tuple(tuple(grouped[key]) for key in sorted(grouped))


def cache_is_pit_stable(conn: psycopg.Connection, *, cutoffs: list[datetime]) -> bool:
    """True when all requested cutoffs can safely share one final history load."""

    return len(cache_cutoff_segments(conn, cutoffs=cutoffs)) <= 1


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
        self._segments = cache_cutoff_segments(conn, cutoffs=ordered)
        self._cutoff_segment = {
            cutoff: segment_index
            for segment_index, segment in enumerate(self._segments)
            for cutoff in segment
        }
        self.mode = (
            MODE_CACHE_LOAD_ONCE
            if len(self._segments) == 1
            else MODE_CACHE_SEGMENTED
        )
        self._cache: dict[datetime, ReplayHistorySet] = {}
        self._active_segment_index: int | None = None
        self._a1_all = None
        self._a2_all = None
        self._a3_all = None
        self._a1_visibility: list[datetime] = []
        self._a2_visibility: list[datetime] = []
        self._a3_visibility: list[datetime] = []
        if self.mode == MODE_CACHE_LOAD_ONCE:
            self._load_segment(0)
        else:
            print(
                "REPLAY_HISTORY_CACHE_SEGMENTED "
                f"cutoffs={len(self._cutoffs)} segments={len(self._segments)} "
                f"sizes={','.join(str(len(segment)) for segment in self._segments)}",
                flush=True,
            )

    def _load_segment(self, segment_index: int) -> None:
        segment = self._segments[segment_index]
        final = segment[-1]
        first = segment[0]
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
        self._active_segment_index = segment_index
        print(
            "REPLAY_HISTORY_CACHE_SEGMENT_READY "
            f"mode={self.mode} segment={segment_index + 1}/{len(self._segments)} "
            f"cutoffs={len(segment)} first={first.isoformat()} final={final.isoformat()} "
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
        segment_index = self._cutoff_segment[cutoff]
        if self._active_segment_index != segment_index:
            self._load_segment(segment_index)

        a1 = self._a1_all
        a2 = self._a2_all
        a3 = self._a3_all

        a1_end = bisect_right(self._a1_visibility, cutoff)
        a2_end = bisect_left(self._a2_visibility, cutoff)
        a3_end = bisect_left(self._a3_visibility, cutoff)
        visible1 = a1[:a1_end]
        visible2 = a2[:a2_end]
        visible3 = a3[:a3_end]

        previous_cutoffs = [
            value
            for value in self._cache
            if value < cutoff and self._cutoff_segment.get(value) == segment_index
        ]
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
                f"segment={segment_index + 1}/{len(self._segments)} "
                f"previous={previous_cutoff.isoformat()} cutoff={cutoff.isoformat()} "
                f"a2_delta={len(delta2)} a3_delta={len(delta3)}",
                flush=True,
            )
        else:
            agent2_snapshot = build_agent2_snapshot(a2, cutoff=cutoff)
            agent3_snapshot = build_live_agent3_history_snapshot(a3, cutoff=cutoff)
            print(
                "REPLAY_HISTORY_CACHE_BOOTSTRAP "
                f"segment={segment_index + 1}/{len(self._segments)} "
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
            mode=self.mode,
        )

    def history_for(self, cutoff: datetime) -> ReplayHistorySet:
        if cutoff in self._cache:
            return self._cache[cutoff]

        if cutoff not in self._cutoff_segment:
            self._cache[cutoff] = self._baseline_set(cutoff)
            return self._cache[cutoff]

        for planned_cutoff in self._cutoffs:
            if planned_cutoff > cutoff:
                break
            if planned_cutoff not in self._cache:
                self._cache[planned_cutoff] = self._cached_set(planned_cutoff)
        return self._cache[cutoff]


__all__ = [
    "MODE_CACHE_LOAD_ONCE",
    "MODE_CACHE_SEGMENTED",
    "MODE_BASELINE_PER_CUTOFF",
    "ReplayHistorySet",
    "ReplayHistoryCache",
    "cache_correction_boundaries",
    "cache_cutoff_segments",
    "cache_is_pit_stable",
]
