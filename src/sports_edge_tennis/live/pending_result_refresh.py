"""Bounded same-day terminal-result refresh for pending production Model Picks.

The broad historical refresh (``history_refresh_dispatch``, daily at ``10 0 * * *``)
intentionally fetches ``end_date = UTC today - 1 day``. That leaves a normal one-day
delay: a pick on day D is not scored until day D+1. This module adds a *separate*,
bounded lane that resolves completed sporting results for production Model Picks that
are still ``PENDING`` on the picked canonical ``match_id``.

Design constraints:

* It never replaces or broadens the daily historical job.
* It only ever looks at production Model Picks (first-production Manager CONSENSUS
  plus the aligned A1/A2 wide-spread tracking picks) and settlement-eligible Type A
  match-winner BET decisions inside a minimal recent window (default: UTC today and
  the previous day). The Type A cohort is the one ``settle_type_a_match_winner_decisions``
  reports as ``pending_results``; it is only a lookup target here, never a wager.
* Canonical identity is owned by the shared repository ingestion path, not by this
  module. A selected API-Tennis fixture is handed to the normal
  ``ingest_api_tennis_source_a_fixtures`` flow, which resolves identity as:

      existing source mapping OR shared v1 prospective resolver
      -> reuse the existing canonical match when exact
      -> create a new canonical match only when genuinely new
      -> fail closed on ambiguity

  This module performs no independent identity comparison and never widens the
  shared ``cross_provider_exact_match_v1`` 15-minute tolerance. In dry-run mode it
  only *reads* the same shared resolver to predict the outcome.

* A fixture is only ingested once it has been associated with a pending pick: either
  its API-Tennis source id is already attached to the picked canonical match, or the
  shared resolver proves the unseen event reuses the picked canonical match.

Terminal results are persisted through the existing canonical result path. This is
result-ingestion infrastructure only: it creates no wagers and changes no model,
Manager, Market, Veto or staking behavior.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.data.cross_provider_reconciliation import (
    REUSE_STATUS_AMBIGUOUS,
    REUSE_STATUS_REUSED,
    SportingIdentity,
    resolve_cross_provider_canonical_reuse,
)
from sports_edge_tennis.data.sources.api_tennis_live import (
    SOURCE_SYSTEM,
    ingest_api_tennis_source_a_fixtures,
    parse_api_tennis_fixtures,
)
from sports_edge_tennis.live.api_tennis_fixture_compat import (
    enrich_api_tennis_fixture_surfaces,
    normalize_api_tennis_fixture_rounds,
    normalize_api_tennis_upcoming_status,
)
from sports_edge_tennis.live.api_tennis_history_reconciliation import (
    _provider_player_map,
    _provider_tournament_map,
)
from sports_edge_tennis.live.credentials import MissingCredentialError, load_secret
from sports_edge_tennis.live.history_refresh import (
    HistoryRefreshError,
    _persist_terminal_results,
    fetch_api_tennis_fixture_window,
    parse_terminal_result,
)
from sports_edge_tennis.live.production_lock import (
    acquire_production_batch_lock,
    release_production_batch_lock,
)
from sports_edge_tennis.live.transport import TransportError
from sports_edge_tennis.performance.regimes import first_production_model_picks_cte

# Include the previous two UTC dates so matches shown on the prior
# Asia/Manila dashboard day (UTC+8) are not stranded outside recovery.
# Still one bounded provider request at most, and only when a pick lacks
# a canonical terminal result.
DEFAULT_WINDOW_DAYS = 3
MAX_WINDOW_DAYS = 3
DEFAULT_MAX_TARGETS = 50
MAX_TARGETS_LIMIT = 200

PICK_SOURCE_MANAGER = "MANAGER_CONSENSUS"
PICK_SOURCE_WIDE_SPREAD = "A1_A2_WIDE_SPREAD"
PICK_SOURCE_TYPE_A_BET = "TYPE_A_BET"

_TERMINAL_OUTCOMES = ("COMPLETED", "RETIRED", "WALKOVER", "CANCELLED")
_TERMINAL_SQL = ",".join(f"'{status}'" for status in _TERMINAL_OUTCOMES)
# Exactly the cohort ``settle_type_a_match_winner_decisions`` treats as eligible.
_TYPE_A_BET_PREDICATE = """
    signal_type = 'TYPE_A_MODEL_EDGE'
    AND decision_status = 'BET'
    AND market_type = 'MATCH_WINNER'
    AND selection_side IN (1, 2)
"""

FetchWindow = Callable[[str, date, date], tuple[datetime, list[dict[str, Any]]]]


@dataclass(frozen=True)
class PendingPickTarget:
    """One bounded production Model Pick whose canonical result is unresolved."""

    match_id: int
    match_start_at: datetime
    player_1_id: int
    player_2_id: int
    tournament_id: int
    season_year: int
    selected_side: int | None
    pick_source: str
    # ``pick_source`` names only the earliest pick for the match; this flags any
    # settlement-eligible Type A BET on it, so BET coverage is never hidden.
    has_type_a_bet: bool = False


@dataclass(frozen=True)
class PendingResultRefreshSummary:
    checked: int = 0
    provider_requests: int = 0
    fixtures_seen: int = 0
    terminal_found: int = 0
    same_match: int = 0
    reused: int = 0
    unresolved: int = 0
    unsafe: int = 0
    provider_errors: int = 0
    persisted: int = 0
    unchanged: int = 0
    expected_won: int = 0
    expected_lost: int = 0
    expected_void: int = 0
    manager_targets: int = 0
    wide_spread_targets: int = 0
    type_a_bet_targets: int = 0

    def log_line(self) -> str:
        return (
            "PENDING_RESULT_REFRESH "
            f"checked={self.checked} provider_requests={self.provider_requests} "
            f"terminal_found={self.terminal_found} same_match={self.same_match} "
            f"reused={self.reused} unresolved={self.unresolved} "
            f"unsafe={self.unsafe} provider_errors={self.provider_errors}"
        )

    def detail_line(self) -> str:
        return (
            "PENDING_RESULT_REFRESH_DETAIL "
            f"fixtures_seen={self.fixtures_seen} persisted={self.persisted} "
            f"unchanged={self.unchanged} expected_won={self.expected_won} "
            f"expected_lost={self.expected_lost} expected_void={self.expected_void} "
            f"manager_targets={self.manager_targets} "
            f"wide_spread_targets={self.wide_spread_targets} "
            f"type_a_bet_targets={self.type_a_bet_targets}"
        )


def _db_dsn() -> str | None:
    import os

    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def window_bounds(as_of: datetime, window_days: int = DEFAULT_WINDOW_DAYS) -> tuple[date, date]:
    """Minimal bounded recent window, anchored on UTC today."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if not 1 <= window_days <= MAX_WINDOW_DAYS:
        raise ValueError(f"window_days must be between 1 and {MAX_WINDOW_DAYS}")
    end = as_of.astimezone(UTC).date()
    start = end - timedelta(days=window_days - 1)
    return start, end


def pending_pick_matches_ctes() -> str:
    """CTE chain ending in ``pick_matches``: one earliest production pick per match.

    Shared by the recovery lane and the read-only pending-result diagnostics so
    both always reason about exactly the same pick population.
    """
    # Imported lazily: the aligned Model Pick CTE lives with the dashboard read
    # model, and importing it at module import time would layer live on dashboard.
    from sports_edge_tennis.dashboard.model_pick_history import (
        aligned_production_model_picks_cte,
    )

    return f"""
        {aligned_production_model_picks_cte()},
        {first_production_model_picks_cte()},
        pick_matches AS (
            SELECT DISTINCT ON (match_id)
                match_id,
                picked_at,
                selected_side,
                pick_source
            FROM (
                SELECT
                    match_id,
                    picked_at,
                    selected_side,
                    '{PICK_SOURCE_MANAGER}'::text AS pick_source
                FROM first_model_picks
                UNION ALL
                SELECT
                    match_id,
                    picked_at,
                    selected_side,
                    '{PICK_SOURCE_WIDE_SPREAD}'::text AS pick_source
                FROM aligned_model_picks
                UNION ALL
                SELECT
                    match_id,
                    created_at AS picked_at,
                    selection_side AS selected_side,
                    '{PICK_SOURCE_TYPE_A_BET}'::text AS pick_source
                FROM decisions
                WHERE {_TYPE_A_BET_PREDICATE}
            ) combined
            ORDER BY
                match_id,
                picked_at,
                CASE pick_source
                    WHEN '{PICK_SOURCE_MANAGER}' THEN 0
                    WHEN '{PICK_SOURCE_WIDE_SPREAD}' THEN 1
                    ELSE 2
                END
        )
    """


def _targets_sql() -> str:
    return f"""
        WITH {pending_pick_matches_ctes()},
        latest_versions AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id,
                mv.match_start_at,
                mv.player_1_id,
                mv.player_2_id
            FROM match_versions mv
            WHERE mv.match_start_at IS NOT NULL
            ORDER BY
                mv.match_id,
                mv.as_of DESC,
                mv.source_observed_at DESC,
                mv.match_version_id DESC
        )
        SELECT
            pm.match_id,
            lv.match_start_at,
            lv.player_1_id,
            lv.player_2_id,
            te.tournament_id,
            te.season_year,
            pm.selected_side,
            pm.pick_source,
            EXISTS (
                SELECT 1
                FROM decisions d
                WHERE d.match_id = pm.match_id
                  AND {_TYPE_A_BET_PREDICATE}
            ) AS has_type_a_bet
        FROM pick_matches pm
        JOIN latest_versions lv ON lv.match_id = pm.match_id
        JOIN matches m ON m.match_id = pm.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        WHERE lv.match_start_at <= %(as_of)s
          AND (lv.match_start_at AT TIME ZONE 'UTC')::date
              BETWEEN %(window_start)s AND %(window_end)s
          AND NOT EXISTS (
              SELECT 1
              FROM match_result_observations mro
              WHERE mro.match_id = pm.match_id
                AND mro.outcome_status::text IN ({_TERMINAL_SQL})
          )
        ORDER BY lv.match_start_at, pm.match_id
        LIMIT %(max_targets)s
    """


def load_pending_model_pick_targets(
    conn: psycopg.Connection,
    *,
    as_of: datetime,
    window_start: date,
    window_end: date,
    max_targets: int = DEFAULT_MAX_TARGETS,
) -> tuple[PendingPickTarget, ...]:
    """Bounded, deduplicated production Model Picks still awaiting a result."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if window_end < window_start:
        raise ValueError("window_end must be >= window_start")
    if not 1 <= max_targets <= MAX_TARGETS_LIMIT:
        raise ValueError(f"max_targets must be between 1 and {MAX_TARGETS_LIMIT}")

    rows = conn.execute(
        _targets_sql(),
        {
            "as_of": as_of,
            "window_start": window_start,
            "window_end": window_end,
            "max_targets": max_targets,
        },
    ).fetchall()
    return tuple(
        PendingPickTarget(
            match_id=int(row[0]),
            match_start_at=row[1],
            player_1_id=int(row[2]),
            player_2_id=int(row[3]),
            tournament_id=int(row[4]),
            season_year=int(row[5]),
            selected_side=int(row[6]) if row[6] is not None else None,
            pick_source=str(row[7]),
            has_type_a_bet=bool(row[8]),
        )
        for row in rows
    )


def _event_link_map(
    conn: psycopg.Connection, event_keys: Iterable[str]
) -> dict[str, int]:
    """Read-only ``event_key -> canonical match_id`` for already-linked events."""
    keys = sorted({key for key in event_keys if key})
    if not keys:
        return {}
    rows = conn.execute(
        """
        SELECT source_match_id, match_id
        FROM match_source_ids
        WHERE source_system = %s AND source_match_id = ANY(%s)
        ORDER BY source_match_id, match_source_id
        """,
        (SOURCE_SYSTEM, keys),
    ).fetchall()
    mapping: dict[str, int] = {}
    for source_match_id, match_id in rows:
        mapping.setdefault(str(source_match_id), int(match_id))
    return mapping


def _expected_result(terminal, selected_side: int | None) -> str | None:
    if selected_side not in (1, 2):
        return None
    if terminal.outcome_status == "COMPLETED":
        if terminal.winner_side == selected_side:
            return "WON"
        if terminal.winner_side in (1, 2):
            return "LOST"
        return None
    if terminal.outcome_status in ("RETIRED", "WALKOVER", "CANCELLED"):
        return "VOID"
    return None


def refresh_pending_results(
    conn: psycopg.Connection,
    *,
    api_key: str | None,
    as_of: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    max_targets: int = DEFAULT_MAX_TARGETS,
    fetch_window: FetchWindow = fetch_api_tennis_fixture_window,
    dry_run: bool = False,
) -> PendingResultRefreshSummary:
    """Resolve same-day terminal results for bounded pending production picks.

    ``dry_run=True`` performs the same bounded provider lookup and shared-resolver
    prediction but writes nothing, for a read-only production probe.
    """
    window_start, window_end = window_bounds(as_of, window_days)
    targets = load_pending_model_pick_targets(
        conn,
        as_of=as_of,
        window_start=window_start,
        window_end=window_end,
        max_targets=max_targets,
    )
    if not targets:
        return PendingResultRefreshSummary(checked=0)
    if not api_key:
        raise MissingCredentialError("API_TENNIS_API_KEY is not configured")

    try:
        received_at, fixtures = fetch_window(api_key, window_start, window_end)
    except TransportError:
        return PendingResultRefreshSummary(
            checked=len(targets),
            provider_requests=1,
            provider_errors=1,
        )

    records, _rejections = parse_api_tennis_fixtures(fixtures, received_at=received_at)
    player_map, _ambiguous_players = _provider_player_map(
        conn,
        (
            player_key
            for record in records
            for player_key in (
                record.first_player_source_id,
                record.second_player_source_id,
            )
        ),
    )
    tournament_map = _provider_tournament_map(
        conn, (record.tournament_source_id for record in records)
    )
    event_links = _event_link_map(conn, (record.source_match_id for record in records))
    pending_pairs = {
        frozenset((target.player_1_id, target.player_2_id)) for target in targets
    }

    # Use the shared prospective resolver read-only, and only for unseen events
    # whose players belong to a pending pick, to associate fixtures with targets.
    resolutions: dict[str, object] = {}
    ambiguous_pairs: set[frozenset[int]] = set()
    for record in records:
        if record.source_match_id in event_links:
            continue
        provider_p1 = player_map.get(record.first_player_source_id)
        provider_p2 = player_map.get(record.second_player_source_id)
        if provider_p1 is None or provider_p2 is None:
            continue
        pair = frozenset((provider_p1, provider_p2))
        if pair not in pending_pairs:
            continue
        incoming = SportingIdentity(
            player_1_id=provider_p1,
            player_2_id=provider_p2,
            tournament_id=tournament_map.get(record.tournament_source_id),
            season_year=record.match_start_at.year,
            tour_scope=record.tour_scope,
            competition_tier=record.competition_tier,
            match_start_at=record.match_start_at,
        )
        resolution = resolve_cross_provider_canonical_reuse(conn, incoming=incoming)
        resolutions[record.source_match_id] = resolution
        if resolution.status == REUSE_STATUS_AMBIGUOUS:
            ambiguous_pairs.add(pair)

    checked = len(targets)
    terminal_found = same_match = reused = unresolved = unsafe = 0
    persisted = unchanged = 0
    expected_won = expected_lost = expected_void = 0

    for target in targets:
        pair = frozenset((target.player_1_id, target.player_2_id))
        if pair in ambiguous_pairs:
            unsafe += 1
            continue

        same = [
            record
            for record in records
            if event_links.get(record.source_match_id) == target.match_id
        ]
        unseen = [
            record
            for record in records
            if record.source_match_id not in event_links
            and getattr(resolutions.get(record.source_match_id), "status", None)
            == REUSE_STATUS_REUSED
            and getattr(resolutions.get(record.source_match_id), "match_id", None)
            == target.match_id
        ]
        candidates = same + unseen
        if not candidates:
            unresolved += 1
            continue
        if len(candidates) > 1:
            unsafe += 1
            continue

        record = candidates[0]
        is_same_match = record in same
        try:
            terminal = parse_terminal_result(record.raw_fields)
        except HistoryRefreshError:
            unsafe += 1
            continue

        if terminal is not None:
            terminal_found += 1
            expected = _expected_result(terminal, target.selected_side)
            if expected == "WON":
                expected_won += 1
            elif expected == "LOST":
                expected_lost += 1
            elif expected == "VOID":
                expected_void += 1

        if dry_run:
            if terminal is None:
                unresolved += 1
            elif is_same_match:
                same_match += 1
            else:
                reused += 1
            continue

        if terminal is None:
            unresolved += 1
            continue

        normalized = normalize_api_tennis_upcoming_status(
            [dict(record.raw_fields)], received_at=received_at
        )
        normalized = normalize_api_tennis_fixture_rounds(normalized)
        normalized = enrich_api_tennis_fixture_surfaces(conn, normalized, api_key=None)
        outcome = ingest_api_tennis_source_a_fixtures(
            conn, normalized, received_at=received_at
        )
        if not outcome.matches:
            unsafe += 1
            continue
        result_match_id = int(outcome.matches[0].match_id)
        _seen, inserted, unchanged_count, _rejected = _persist_terminal_results(
            conn,
            fixtures=normalized,
            ingested_matches=outcome.matches,
            received_at=received_at,
        )
        persisted += inserted
        unchanged += unchanged_count

        if result_match_id != target.match_id:
            unsafe += 1
        elif is_same_match:
            same_match += 1
        else:
            reused += 1

    return PendingResultRefreshSummary(
        checked=checked,
        provider_requests=1,
        fixtures_seen=len(fixtures),
        terminal_found=terminal_found,
        same_match=same_match,
        reused=reused,
        unresolved=unresolved,
        unsafe=unsafe,
        provider_errors=0,
        persisted=persisted,
        unchanged=unchanged,
        expected_won=expected_won,
        expected_lost=expected_lost,
        expected_void=expected_void,
        manager_targets=sum(
            1 for target in targets if target.pick_source == PICK_SOURCE_MANAGER
        ),
        wide_spread_targets=sum(
            1 for target in targets if target.pick_source == PICK_SOURCE_WIDE_SPREAD
        ),
        type_a_bet_targets=sum(1 for target in targets if target.has_type_a_bet),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded same-day pending Model Pick result refresh"
    )
    parser.add_argument("--dsn", default=_db_dsn())
    parser.add_argument("--as-of", type=datetime.fromisoformat)
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--max-targets", type=int, default=DEFAULT_MAX_TARGETS)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read-only probe: provider lookup + prediction, zero writes",
    )
    args = parser.parse_args(argv)

    if not args.dsn:
        print("PENDING_RESULT_REFRESH_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    if not 1 <= args.window_days <= MAX_WINDOW_DAYS:
        print(
            f"PENDING_RESULT_REFRESH_BLOCKED reason=WINDOW_DAYS_MAX_{MAX_WINDOW_DAYS}",
            flush=True,
        )
        return 2
    if not 1 <= args.max_targets <= MAX_TARGETS_LIMIT:
        print(
            f"PENDING_RESULT_REFRESH_BLOCKED reason=MAX_TARGETS_MAX_{MAX_TARGETS_LIMIT}",
            flush=True,
        )
        return 2

    as_of = args.as_of or datetime.now(UTC)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    try:
        api_key = load_secret("API_TENNIS_API_KEY")
    except MissingCredentialError:
        api_key = None
    if not api_key:
        print("PENDING_RESULT_REFRESH_BLOCKED reason=MISSING_API_TENNIS_KEY", flush=True)
        return 2

    try:
        lock_info = acquire_production_batch_lock(args.dsn)
    except psycopg.Error as exc:
        print(
            "PENDING_RESULT_REFRESH_BLOCKED "
            f"reason=LOCK_UNAVAILABLE kind={type(exc).__name__}",
            flush=True,
        )
        return 2
    if lock_info is None:
        print(
            "PENDING_RESULT_REFRESH_SKIPPED reason=ALREADY_RUNNING",
            flush=True,
        )
        return 0
    try:
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            summary = refresh_pending_results(
                conn,
                api_key=api_key,
                as_of=as_of,
                window_days=args.window_days,
                max_targets=args.max_targets,
                dry_run=args.dry_run,
            )
        if args.dry_run:
            print("PENDING_RESULT_REFRESH_DRY_RUN writes=0", flush=True)
        print(summary.log_line(), flush=True)
        print(summary.detail_line(), flush=True)
    except Exception as exc:  # noqa: BLE001 - concise fail-closed CLI boundary
        print(
            f"PENDING_RESULT_REFRESH_FAILED kind={type(exc).__name__}",
            flush=True,
        )
        return 2
    finally:
        release_production_batch_lock(lock_info)
    return 0


if __name__ == "__main__":
    sys.exit(main())
