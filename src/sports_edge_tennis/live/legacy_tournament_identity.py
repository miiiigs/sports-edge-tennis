"""Deterministic bridge from legacy canonical tournaments to Sportradar competition identity.

Many legacy (API-Tennis era) tournament editions have a NULL surface and no
Sportradar competition identity, so authoritative surface recovery cannot query
provider season metadata for them. This module derives that identity exclusively
from already-ingested Sportradar evidence: a legacy canonical match is linked to a
Sportradar-linked canonical match only when the unordered canonical player pair
matches exactly and the match start times are within a tight window in the same
season year. The linked Sportradar match's canonical tournament competition id is
the evidence.

The bridge is deterministic and fail-closed: a tournament is bridged only when all
of its linked editions agree on exactly one Sportradar competition id. It never
uses name/city/month heuristics, never creates matches, and never deletes
duplicates.
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

DEFAULT_WINDOW_MINUTES = 15
SOURCE_SYSTEM = "SPORTRADAR"
AUDIT_DATASET = "LEGACY_TOURNAMENT_IDENTITY_BRIDGE"


@dataclass(frozen=True)
class LegacyEdition:
    tournament_edition_id: int
    tournament_id: int
    season_year: int


@dataclass(frozen=True)
class IdentityBridgeOutcome:
    editions_examined: int
    editions_deterministic: int
    editions_ambiguous: int
    editions_unresolved: int
    tournaments_bridged: int
    tournaments_already_linked: int
    competitions: tuple[str, ...]


def load_legacy_editions(
    conn: psycopg.Connection,
    *,
    player_ids: list[int] | tuple[int, ...],
    max_editions: int = 200,
) -> list[LegacyEdition]:
    rows = conn.execute(
        """
        SELECT DISTINCT te.tournament_edition_id, te.tournament_id, te.season_year
        FROM tournament_editions te
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        WHERE te.surface IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM tournament_source_ids tsi
              WHERE tsi.tournament_id = t.tournament_id AND tsi.source_system = 'SPORTRADAR'
          )
          AND EXISTS (
              SELECT 1 FROM matches m
              JOIN match_versions mv ON mv.match_id = m.match_id
              WHERE m.tournament_edition_id = te.tournament_edition_id
                AND mv.surface IS NULL
                AND (mv.player_1_id = ANY(%(player_ids)s) OR mv.player_2_id = ANY(%(player_ids)s))
          )
        ORDER BY te.season_year DESC, te.tournament_edition_id
        LIMIT %(max_editions)s
        """,
        {"player_ids": list(player_ids), "max_editions": max_editions},
    ).fetchall()
    return [
        LegacyEdition(int(row[0]), int(row[1]), int(row[2])) for row in rows
    ]


def derive_edition_evidence(
    conn: psycopg.Connection,
    *,
    player_ids: list[int] | tuple[int, ...],
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    max_editions: int = 200,
) -> dict[int, set[str]]:
    """Map each legacy edition to the exact Sportradar competition ids from linked events."""
    rows = conn.execute(
        """
        WITH legacy AS (
            SELECT ml.match_id, ml.tournament_edition_id, te.tournament_id, te.season_year,
                   vl.player_1_id, vl.player_2_id, vl.match_start_at
            FROM matches ml
            JOIN match_versions vl ON vl.match_id = ml.match_id
            JOIN tournament_editions te ON te.tournament_edition_id = ml.tournament_edition_id
            JOIN tournaments t ON t.tournament_id = te.tournament_id
            WHERE vl.surface IS NULL
              AND vl.match_start_at IS NOT NULL
              AND (vl.player_1_id = ANY(%(player_ids)s) OR vl.player_2_id = ANY(%(player_ids)s))
              AND NOT EXISTS (
                  SELECT 1 FROM tournament_source_ids tsi
                  WHERE tsi.tournament_id = t.tournament_id AND tsi.source_system = 'SPORTRADAR'
              )
        )
        SELECT legacy.tournament_edition_id, tsi.source_tournament_id
        FROM legacy
        JOIN match_versions v2
          ON v2.player_1_id = legacy.player_1_id AND v2.player_2_id = legacy.player_2_id
        JOIN matches m2 ON m2.match_id = v2.match_id
        JOIN tournament_editions te2 ON te2.tournament_edition_id = m2.tournament_edition_id
        JOIN tournament_source_ids tsi
          ON tsi.tournament_id = te2.tournament_id AND tsi.source_system = 'SPORTRADAR'
        WHERE v2.match_start_at IS NOT NULL
          AND abs(EXTRACT(EPOCH FROM (v2.match_start_at - legacy.match_start_at)))
              <= (%(window_minutes)s * 60)
          AND EXTRACT(YEAR FROM v2.match_start_at) = legacy.season_year
          AND EXISTS (
              SELECT 1 FROM match_source_ids ms
              WHERE ms.match_id = m2.match_id AND ms.source_system = 'SPORTRADAR'
          )
        GROUP BY legacy.tournament_edition_id, tsi.source_tournament_id
        """,
        {
            "player_ids": list(player_ids),
            "window_minutes": window_minutes,
            "max_editions": max_editions,
        },
    ).fetchall()
    evidence: dict[int, set[str]] = defaultdict(set)
    for edition_id, competition_id in rows:
        evidence[int(edition_id)].add(str(competition_id))
    return dict(evidence)


def _tournament_has_source(
    conn: psycopg.Connection, *, tournament_id: int
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM tournament_source_ids WHERE tournament_id = %s AND source_system = 'SPORTRADAR'",
        (tournament_id,),
    ).fetchone()
    return row is not None


def _competition_taken_by_other(
    conn: psycopg.Connection, *, tournament_id: int, competition_id: str
) -> bool:
    row = conn.execute(
        "SELECT tournament_id FROM tournament_source_ids WHERE source_system = 'SPORTRADAR' AND source_tournament_id = %s",
        (competition_id,),
    ).fetchone()
    return row is not None and int(row[0]) != tournament_id


def _attach_source(
    conn: psycopg.Connection,
    *,
    tournament_id: int,
    competition_id: str,
    edition_ids: list[int],
) -> None:
    conn.execute(
        "INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id) "
        "VALUES (%s, %s, %s)",
        (tournament_id, SOURCE_SYSTEM, competition_id),
    )
    notes = (
        f"competition_id={competition_id} tournament_id={tournament_id} "
        f"editions={','.join(str(e) for e in edition_ids)}"
    )
    conn.execute(
        """
        INSERT INTO data_health_checks (
            source_system, dataset_name, checked_at, latest_observation_at,
            freshness_state, coverage_ratio, unmatched_count, error_count, status, notes
        )
        VALUES ('SPORTRADAR', %s, %s, %s, 'FRESH', 1.0, 0, 0, 'HEALTHY', %s)
        """,
        (AUDIT_DATASET, datetime.now(UTC), datetime.now(UTC), notes),
    )


def bridge_legacy_tournament_identities(
    conn: psycopg.Connection,
    *,
    player_ids: list[int] | tuple[int, ...],
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    max_editions: int = 200,
    dry_run: bool = False,
) -> IdentityBridgeOutcome:
    editions = load_legacy_editions(
        conn, player_ids=player_ids, max_editions=max_editions
    )
    evidence = derive_edition_evidence(
        conn,
        player_ids=player_ids,
        window_minutes=window_minutes,
        max_editions=max_editions,
    )

    tournament_competitions: dict[int, set[str]] = defaultdict(set)
    tournament_editions: dict[int, list[int]] = defaultdict(list)
    for edition in editions:
        tournament_editions[edition.tournament_id].append(edition.tournament_edition_id)
        for competition_id in evidence.get(edition.tournament_edition_id, ()):  # type: ignore[arg-type]
            tournament_competitions[edition.tournament_id].add(competition_id)

    deterministic = 0
    ambiguous = 0
    unresolved = 0
    bridged = 0
    already_linked = 0
    bridged_competitions: list[str] = []

    for edition in editions:
        comps = evidence.get(edition.tournament_edition_id, set())
        if len(comps) == 1:
            deterministic += 1
        elif len(comps) > 1:
            ambiguous += 1
        else:
            unresolved += 1

    for tournament_id, comps in tournament_competitions.items():
        if len(comps) != 1:
            continue
        competition_id = next(iter(comps))
        if _tournament_has_source(conn, tournament_id=tournament_id):
            continue
        if _competition_taken_by_other(
            conn, tournament_id=tournament_id, competition_id=competition_id
        ):
            # The exact competition identity is already held by the corresponding
            # Sportradar duplicate tournament. tournament_source_ids is append-only
            # and unique per (source_system, source_tournament_id), so the legacy
            # tournament cannot independently own it; the identity already exists.
            already_linked += 1
            print(
                "LEGACY_TOURNAMENT_IDENTITY_ALREADY_LINKED "
                f"tournament_id={tournament_id} competition_id={competition_id}",
                flush=True,
            )
            continue
        bridged += 1
        bridged_competitions.append(competition_id)
        print(
            "LEGACY_TOURNAMENT_IDENTITY_BRIDGED "
            f"tournament_id={tournament_id} competition_id={competition_id}",
            flush=True,
        )
        if not dry_run:
            with conn.transaction():
                _attach_source(
                    conn,
                    tournament_id=tournament_id,
                    competition_id=competition_id,
                    edition_ids=sorted(tournament_editions[tournament_id]),
                )

    return IdentityBridgeOutcome(
        editions_examined=len(editions),
        editions_deterministic=deterministic,
        editions_ambiguous=ambiguous,
        editions_unresolved=unresolved,
        tournaments_bridged=bridged,
        tournaments_already_linked=already_linked,
        competitions=tuple(bridged_competitions),
    )


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic legacy tournament -> Sportradar competition identity bridge"
    )
    parser.add_argument("--player-ids", required=True, help="Comma-separated canonical player IDs")
    parser.add_argument("--window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES)
    parser.add_argument("--max-editions", type=int, default=200)
    parser.add_argument("--dsn", default=_db_dsn())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    player_ids = [int(v) for v in str(args.player_ids).split(",") if v.strip()]
    if not player_ids:
        print("LEGACY_TOURNAMENT_IDENTITY_BLOCKED reason=NO_PLAYER_IDS", flush=True)
        return 2
    if not args.dsn:
        print("LEGACY_TOURNAMENT_IDENTITY_BLOCKED reason=NO_DATABASE", flush=True)
        return 2

    with psycopg.connect(args.dsn, autocommit=False) as conn:
        outcome = bridge_legacy_tournament_identities(
            conn,
            player_ids=player_ids,
            window_minutes=args.window_minutes,
            max_editions=args.max_editions,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            conn.commit()

    print(
        "LEGACY_TOURNAMENT_IDENTITY "
        f"editions_examined={outcome.editions_examined} "
        f"editions_deterministic={outcome.editions_deterministic} "
        f"editions_ambiguous={outcome.editions_ambiguous} "
        f"editions_unresolved={outcome.editions_unresolved} "
        f"tournaments_bridged={outcome.tournaments_bridged} "
        f"tournaments_already_linked={outcome.tournaments_already_linked} "
        f"competitions={','.join(outcome.competitions) or '-'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
