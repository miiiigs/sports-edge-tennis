"""Extract a frozen, read-only Agent 2 history-lookback research dataset.

The historical Sackmann corpus does not carry a trustworthy ``match_start_at``
before 2026.  A true per-match pre-match replay is therefore impossible for the
2016-2025 experiment period.  This extractor keeps every timestamp needed to
build a *tournament-cohort* replay: all matches in one tournament edition are
predicted before any result from that edition is applied.

Run against the production Tennis database with a read-only session::

    python research/a2_form_lookback/extract_dataset.py > tmp/a2_frozen.jsonl

The output is one JSON object per line plus a final ``_manifest`` line.  No
credentials, raw payloads, or production rows are written anywhere else.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from contextlib import nullcontext
from datetime import UTC, datetime

import psycopg

DATASET_SCHEMA_VERSION = "a2_form_lookback_dataset_v1"
DEFAULT_CUTOFF = "2026-01-01T00:00:00+00:00"
MIN_SEASON = 2016
MAX_SEASON = 2025


def _dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _git_sha() -> str:
    override = (os.environ.get("TENNIS_REPLAY_GIT_SHA") or "").strip()
    if override:
        return override
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def _canonical(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)


def _rows(conn: psycopg.Connection, cutoff: datetime) -> list[tuple]:
    query = """
        WITH selected_results AS (
            SELECT
                mro.match_result_observation_id,
                mro.match_id,
                mro.outcome_status::text AS outcome_status,
                mro.winner_side,
                mro.source_observed_at,
                mro.as_of,
                ROW_NUMBER() OVER (
                    PARTITION BY mro.match_id
                    ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
                             mro.match_result_observation_id DESC
                ) AS rn
            FROM match_result_observations mro
            WHERE mro.source_observed_at <= %(cutoff)s
              AND mro.as_of <= %(cutoff)s
        ),
        selected_versions AS (
            SELECT
                mv.match_version_id,
                mv.match_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.match_start_at,
                mv.tour_scope::text AS tour_scope,
                mv.competition_tier::text AS competition_tier,
                mv.surface::text AS surface,
                mv.round_code,
                mv.best_of_sets,
                mv.qualifying_flag,
                mv.source_observed_at,
                mv.as_of,
                ROW_NUMBER() OVER (
                    PARTITION BY mv.match_id
                    ORDER BY mv.as_of DESC, mv.source_observed_at DESC,
                             mv.match_version_id DESC
                ) AS rn
            FROM match_versions mv
            WHERE mv.source_observed_at <= %(cutoff)s
              AND mv.as_of <= %(cutoff)s
        ),
        stat_cohort_candidates AS (
            SELECT
                match_id,
                source_system,
                source_observed_at,
                as_of,
                count(*) AS stat_row_count,
                count(DISTINCT player_id) AS player_count,
                json_agg(
                    json_build_object(
                        'player_id', player_id,
                        'first_serve_points_won', first_serve_points_won,
                        'second_serve_points_won', second_serve_points_won,
                        'service_points_played', service_points_played,
                        'return_points_won', return_points_won
                    ) ORDER BY player_id
                ) AS stats
            FROM match_stat_observations
            WHERE observation_kind = 'RAW_SOURCE'
              AND source_observed_at <= %(cutoff)s
              AND as_of <= %(cutoff)s
            GROUP BY match_id, source_system, source_observed_at, as_of
        ),
        selected_stats AS (
            SELECT *
            FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY match_id
                        ORDER BY as_of DESC, source_observed_at DESC, source_system DESC
                    ) AS rn
                FROM stat_cohort_candidates
                WHERE stat_row_count = 2
                  AND player_count = 2
            ) ranked
            WHERE rn = 1
        )
        SELECT
            m.match_id,
            te.season_year,
            t.tournament_id,
            t.canonical_name,
            te.start_date,
            te.end_date,
            r.outcome_status,
            r.winner_side,
            r.source_observed_at AS result_source_observed_at,
            r.as_of AS result_as_of,
            v.match_version_id,
            v.player_1_id,
            v.player_2_id,
            v.match_start_at,
            v.tour_scope,
            v.competition_tier,
            v.surface,
            v.round_code,
            v.best_of_sets,
            v.qualifying_flag,
            v.source_observed_at AS version_source_observed_at,
            v.as_of AS version_as_of,
            s.source_system AS stat_source_system,
            s.source_observed_at AS stat_source_observed_at,
            s.as_of AS stat_as_of,
            s.stats
        FROM matches m
        JOIN tournament_editions te
          ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN selected_results r ON r.match_id = m.match_id AND r.rn = 1
        JOIN selected_versions v ON v.match_id = m.match_id AND v.rn = 1
        LEFT JOIN selected_stats s ON s.match_id = m.match_id
        WHERE te.season_year >= %(min_season)s
          AND te.season_year <= %(max_season)s
        ORDER BY m.match_id
    """
    return conn.execute(
        query,
        {
            "cutoff": cutoff,
            "min_season": MIN_SEASON,
            "max_season": MAX_SEASON,
        },
    ).fetchall()


def _record(row: tuple) -> dict:
    (
        match_id,
        season_year,
        tournament_id,
        canonical_name,
        edition_start_date,
        edition_end_date,
        outcome_status,
        winner_side,
        result_source_observed_at,
        result_as_of,
        match_version_id,
        player_1_id,
        player_2_id,
        match_start_at,
        tour_scope,
        competition_tier,
        surface,
        round_code,
        best_of_sets,
        qualifying_flag,
        version_source_observed_at,
        version_as_of,
        stat_source_system,
        stat_source_observed_at,
        stat_as_of,
        stats,
    ) = row
    return {
        "match_id": int(match_id),
        "season_year": int(season_year),
        "tournament_id": int(tournament_id),
        "canonical_tournament_name": str(canonical_name),
        "edition_start_date": edition_start_date.isoformat() if edition_start_date else None,
        "edition_end_date": edition_end_date.isoformat() if edition_end_date else None,
        "outcome_status": str(outcome_status),
        "winner_side": int(winner_side) if winner_side is not None else None,
        "result_source_observed_at": result_source_observed_at.isoformat(),
        "result_as_of": result_as_of.isoformat(),
        "match_version_id": int(match_version_id),
        "player_1_id": int(player_1_id),
        "player_2_id": int(player_2_id),
        "match_start_at": match_start_at.isoformat() if match_start_at else None,
        "tour_scope": str(tour_scope),
        "competition_tier": str(competition_tier),
        "surface": surface,
        "round_code": round_code,
        "best_of_sets": int(best_of_sets),
        "qualifying_flag": bool(qualifying_flag),
        "version_source_observed_at": version_source_observed_at.isoformat(),
        "version_as_of": version_as_of.isoformat(),
        "stat_source_system": stat_source_system,
        "stat_source_observed_at": (
            stat_source_observed_at.isoformat() if stat_source_observed_at else None
        ),
        "stat_as_of": stat_as_of.isoformat() if stat_as_of else None,
        "stats": stats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    parser.add_argument("--output", default="-")
    args = parser.parse_args(argv)

    dsn = _dsn()
    if not dsn:
        print("A2_LOOKBACK_EXTRACT_BLOCKED no_database_dsn", file=sys.stderr)
        return 2
    cutoff = datetime.fromisoformat(args.cutoff)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)

    digest = hashlib.sha256()
    counts = {
        "rows": 0,
        "completed": 0,
        "supported_tour": 0,
        "surface_present": 0,
        "stats_present": 0,
    }
    source_database = None
    with (
        nullcontext(sys.stdout)
        if args.output == "-"
        else open(args.output, "w", encoding="utf-8")
    ) as out:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("SET default_transaction_read_only = on")
            source_database = conn.execute("SELECT current_database()").fetchone()[0]
            rows = _rows(conn, cutoff)
        for row in rows:
            record = _record(row)
            canonical = _canonical(record)
            digest.update(canonical.encode("utf-8"))
            digest.update(b"\n")
            out.write(canonical + "\n")
            counts["rows"] += 1
            if record["outcome_status"] == "COMPLETED" and record["winner_side"] in (1, 2):
                counts["completed"] += 1
            if record["tour_scope"] in ("ATP_MAIN", "CHALLENGER"):
                counts["supported_tour"] += 1
            if record["surface"] is not None:
                counts["surface_present"] += 1
            if record["stats"] is not None:
                counts["stats_present"] += 1
        manifest = {
            "_manifest": {
                "dataset_schema_version": DATASET_SCHEMA_VERSION,
                "extraction_utc": datetime.now(UTC).isoformat(),
                "cutoff": cutoff.isoformat(),
                "source_database": source_database,
                "git_sha": _git_sha(),
                "season_min": MIN_SEASON,
                "season_max": MAX_SEASON,
                "counts": counts,
                "dataset_sha256": digest.hexdigest(),
                "read_only": True,
            }
        }
        out.write(_canonical(manifest) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
