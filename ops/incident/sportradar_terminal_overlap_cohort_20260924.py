"""Deterministic local cohort validation for Sportradar terminal overlap linkage.

Reconstructs the Terminal A duplicate pattern inside a disposable database:

* historical COMPLETED canonical matches already exist (API-Tennis source id)
  but carry no Sportradar match source id;
* the same fixtures are then presented to Sportradar terminal ingestion.

The script reports how many terminal events link to the existing canonical match
instead of creating a duplicate, how many are genuinely new, ambiguous or
conflicting, and how much Agent 2 final-stat evidence now attaches to the
original canonical matches.

It only mutates the database it is pointed at and rolls the whole run back at the
end. Point it at a disposable local test database via ``TEST_POSTGRES_DSN`` (or
``--dsn``). It refuses to run against a production environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.live.sportradar_fixtures import (
    SOURCE_SYSTEM,
    parse_summary,
)
from sports_edge_tennis.live.sportradar_history_refresh import (
    refresh_sportradar_history_day,
)
from sports_edge_tennis.live.sportradar_reconciliation import (
    resolve_sportradar_event_overlap,
)

RECEIVED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
MATCH_DAY = date(2026, 9, 19)
SEED_OBSERVED_AT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
SEED_AS_OF = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
START_AT = datetime(2026, 9, 19, 13, 0, tzinfo=UTC)
COHORT_COUNTS = {
    "LINK_CLEAN": 10,
    "CONFLICT_WINNER": 3,
    "AMBIGUOUS": 2,
    "SR_ID_CONFLICT": 3,
    "TOURNAMENT_EVIDENCE_CONFLICT": 2,
    "NEW": 4,
}

STATS_A = {
    "first_serve_successful": 40,
    "first_serve_points_won": 30,
    "second_serve_points_won": 10,
    "service_points_won": 40,
    "service_points_lost": 20,
    "total_breakpoints": 5,
    "breakpoints_won": 2,
    "aces": 3,
    "double_faults": 1,
}
STATS_B = {
    "first_serve_successful": 35,
    "first_serve_points_won": 25,
    "second_serve_points_won": 8,
    "service_points_won": 33,
    "service_points_lost": 25,
    "total_breakpoints": 6,
    "breakpoints_won": 3,
    "aces": 2,
    "double_faults": 2,
}


def _apply_migrations(conn: psycopg.Connection) -> None:
    if conn.execute("SELECT to_regclass('public.matches')").fetchone()[0] is not None:
        return
    manifest = json.loads(
        (REPO_ROOT / "db" / "migrations" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for entry in manifest["migrations"]:
        sql = (REPO_ROOT / entry["up"]).read_text(encoding="utf-8")
        conn.execute(sql)


class _Cohort:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self.repository = HistoricalRepository(conn, _seed_run(conn))
        self.canonical: dict[str, dict] = {}
        self.summaries: list[dict] = []
        self.expected: dict[str, str] = {}

    def _players(self, key: str) -> tuple[int, int]:
        tag = key.replace(":", "_")
        p1 = self.repository.create_player_with_source_id(
            source_system=SOURCE_SYSTEM,
            source_player_id=f"sr:player:{tag}:a",
            canonical_name=f"Cohort {tag} A",
            observed_at=SEED_OBSERVED_AT,
        )
        p2 = self.repository.create_player_with_source_id(
            source_system=SOURCE_SYSTEM,
            source_player_id=f"sr:player:{tag}:b",
            canonical_name=f"Cohort {tag} B",
            observed_at=SEED_OBSERVED_AT,
        )
        return p1, p2

    def _canonical_match(
        self,
        key: str,
        *,
        p1: int,
        p2: int,
        start: datetime,
        tournament_source_bridge: str | None = None,
        extra_suffix: str = "",
        attach_sr_event_id: str | None = None,
    ) -> int:
        tag = key.replace(":", "_")
        tournament_id = self.repository.get_or_create_tournament(
            source_system="API_TENNIS",
            source_tournament_id=f"at:comp:{tournament_source_bridge or tag}",
            canonical_name=f"Cohort Event {tag}",
            tour_scope="CHALLENGER",
            competition_tier="CHALLENGER",
        )
        if tournament_source_bridge:
            self.conn.execute(
                """
                INSERT INTO tournament_source_ids (
                    tournament_id, source_system, source_tournament_id
                )
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (tournament_id, SOURCE_SYSTEM, tournament_source_bridge),
            )
        edition_id = self.repository.get_or_create_tournament_edition(
            tournament_id=tournament_id,
            season_year=start.year,
            edition_name=f"Cohort Event {tag} {start.year}",
            surface="HARD",
            start_date=MATCH_DAY,
            end_date=None,
        )
        locator = self.repository.upsert_match_version(
            tournament_edition_id=edition_id,
            source_system="API_TENNIS",
            source_match_id=f"at:match:{tag}{extra_suffix}",
            source_record_hash=f"hash:at:{tag}{extra_suffix}",
            source_record_locator=f"api_tennis:{tag}{extra_suffix}",
            source_file_name="cohort",
            source_version_hash=f"vhash:at:{tag}{extra_suffix}",
            player_1_id=p1,
            player_2_id=p2,
            match_start_at=start,
            tour_scope="CHALLENGER",
            competition_tier="CHALLENGER",
            surface="HARD",
            round_code="Round of 32",
            best_of_sets=3,
            qualifying_flag=False,
            match_status="COMPLETED",
            source_observed_at=SEED_OBSERVED_AT,
            as_of=SEED_AS_OF,
        )
        self.repository.insert_match_result(
            match_id=locator.match_id,
            outcome_status="COMPLETED",
            winner_side=1,
            retired_side=None,
            sets_won_player_1=2,
            sets_won_player_2=0,
            games_won_player_1=12,
            games_won_player_2=7,
            scoreline_text="2-0",
            source_observed_at=SEED_OBSERVED_AT,
            as_of=SEED_AS_OF,
        )
        if attach_sr_event_id:
            self.conn.execute(
                """
                INSERT INTO match_source_ids (
                    match_id, source_system, source_match_id
                )
                VALUES (%s, %s, %s)
                """,
                (locator.match_id, SOURCE_SYSTEM, attach_sr_event_id),
            )
        self.canonical[key] = {
            "match_id": locator.match_id,
            "player_1_id": p1,
            "player_2_id": p2,
        }
        return locator.match_id

    def _fixture_summary(
        self,
        key: str,
        *,
        event_id: str,
        competition_id: str,
        p1_provider: str,
        p2_provider: str,
        start: datetime,
        winner: str,
    ) -> dict:
        tag = key.replace(":", "_")
        self._players(key)
        return {
            "sport_event": {
                "id": event_id,
                "start_time": start.isoformat().replace("+00:00", "Z"),
                "sport_event_context": {
                    "competition": {
                        "id": competition_id,
                        "name": f"Cohort Event {tag}",
                        "level": "challenger",
                        "type": "singles",
                        "gender": "men",
                    },
                    "category": {"name": "ATP"},
                    "season": {"id": f"sr:season:{tag}"},
                    "surface": "hard",
                    "mode": {"best_of": "3"},
                    "round": {"name": "Round of 32"},
                    "stage": {"phase": "main"},
                },
                "competitors": [
                    {"id": p1_provider, "name": f"Cohort {tag} A", "qualifier": "home"},
                    {"id": p2_provider, "name": f"Cohort {tag} B", "qualifier": "away"},
                ],
            },
            "sport_event_status": {
                "status": "closed",
                "match_status": "ended",
                "winner_id": winner,
                "home_score": 2,
                "away_score": 0,
                "period_scores": [
                    {"type": "set", "home_score": 6, "away_score": 4},
                    {"type": "set", "home_score": 6, "away_score": 3},
                ],
            },
            "statistics": {
                "totals": {
                    "competitors": [
                        {"id": p1_provider, "statistics": STATS_A},
                        {"id": p2_provider, "statistics": STATS_B},
                    ]
                }
            },
        }

    def seed(self) -> None:
        for index in range(COHORT_COUNTS["LINK_CLEAN"]):
            key = f"link:{index}"
            p1, p2 = self._players(key)
            self._canonical_match(key, p1=p1, p2=p2, start=START_AT)
            self._add_fixture(key, winner_provider_suffix="a")

        for index in range(COHORT_COUNTS["CONFLICT_WINNER"]):
            key = f"winner:{index}"
            p1, p2 = self._players(key)
            self._canonical_match(key, p1=p1, p2=p2, start=START_AT)
            self._add_fixture(key, winner_provider_suffix="b")

        for index in range(COHORT_COUNTS["AMBIGUOUS"]):
            key = f"ambig:{index}"
            p1, p2 = self._players(key)
            self._canonical_match(
                key, p1=p1, p2=p2, start=START_AT, extra_suffix=":one"
            )
            self._canonical_match(
                key,
                p1=p1,
                p2=p2,
                start=START_AT.replace(minute=20),
                extra_suffix=":two",
            )
            self._add_fixture(key, winner_provider_suffix="a")

        for index in range(COHORT_COUNTS["SR_ID_CONFLICT"]):
            key = f"srid:{index}"
            p1, p2 = self._players(key)
            self._canonical_match(
                key,
                p1=p1,
                p2=p2,
                start=START_AT,
                attach_sr_event_id=f"sr:event:old:{key}",
            )
            self._add_fixture(key, winner_provider_suffix="a")

        for index in range(COHORT_COUNTS["TOURNAMENT_EVIDENCE_CONFLICT"]):
            key = f"evidence:{index}"
            p1, p2 = self._players(key)
            self._canonical_match(
                key,
                p1=p1,
                p2=p2,
                start=START_AT,
                tournament_source_bridge=f"sr:competition:other:{key}",
            )
            self._add_fixture(key, winner_provider_suffix="a")

        for index in range(COHORT_COUNTS["NEW"]):
            key = f"new:{index}"
            self._players(key)
            self._add_fixture(key, winner_provider_suffix="a")

    def _add_fixture(self, key: str, *, winner_provider_suffix: str) -> None:
        tag = key.replace(":", "_")
        summary = self._fixture_summary(
            key,
            event_id=f"sr:event:{tag}",
            competition_id=f"sr:competition:{tag}",
            p1_provider=f"sr:player:{tag}:a",
            p2_provider=f"sr:player:{tag}:b",
            start=START_AT,
            winner=f"sr:player:{tag}:{winner_provider_suffix}",
        )
        self.summaries.append(summary)
        parse_summary(summary, received_at=RECEIVED_AT)


def _seed_run(conn: psycopg.Connection) -> int:
    return conn.execute(
        """
        INSERT INTO ingest_runs (source_system, source_dataset, started_at, status)
        VALUES ('COHORT', 'terminal_overlap_cohort', %s, 'RUNNING')
        RETURNING ingest_run_id
        """,
        (RECEIVED_AT,),
    ).fetchone()[0]


def _counts(conn: psycopg.Connection) -> dict[str, int]:
    return {
        "matches": conn.execute("SELECT count(*) FROM matches").fetchone()[0],
        "sr_match_events": conn.execute(
            "SELECT count(*) FROM match_source_ids WHERE source_system='SPORTRADAR'"
        ).fetchone()[0],
        "sr_stat_matches": conn.execute(
            """
            SELECT count(DISTINCT match_id)
            FROM match_stat_observations
            WHERE source_system = 'SPORTRADAR'
            """
        ).fetchone()[0],
        "sr_stat_rows": conn.execute(
            """
            SELECT count(*)
            FROM match_stat_observations
            WHERE source_system = 'SPORTRADAR'
            """
        ).fetchone()[0],
        "sr_result_rows": conn.execute(
            """
            SELECT count(*)
            FROM match_result_observations
            WHERE source_observed_at = %s
            """,
            (RECEIVED_AT,),
        ).fetchone()[0],
    }


def run(conn: psycopg.Connection) -> dict:
    cohort = _Cohort(conn)
    cohort.seed()

    classification: Counter[str] = Counter()
    duplicate_prevented = 0
    for summary in cohort.summaries:
        fixture = parse_summary(summary, received_at=RECEIVED_AT)
        resolution = resolve_sportradar_event_overlap(conn, fixture=fixture)
        classification[resolution.status] += 1
        if resolution.status == "EXACT_EXISTING_MATCH":
            canonical = cohort.canonical.get(
                _key_for_event(fixture.source_match_id, cohort)
            )
            if canonical and canonical["match_id"] == resolution.match_id:
                duplicate_prevented += 1

    before = _counts(conn)
    outcome = refresh_sportradar_history_day(
        conn,
        api_key="cohort",
        target_date=MATCH_DAY,
        fetch_rows=lambda _key, _day: (RECEIVED_AT, cohort.summaries),
    )
    after = _counts(conn)

    linked_originals = sum(
        1
        for key, canonical in cohort.canonical.items()
        if conn.execute(
            """
            SELECT 1
            FROM match_stat_observations
            WHERE match_id = %s AND source_system = 'SPORTRADAR'
            LIMIT 1
            """,
            (canonical["match_id"],),
        ).fetchone()
        is not None
    )

    return {
        "fixtures_evaluated": len(cohort.summaries),
        "classification": dict(sorted(classification.items())),
        "linked_to_existing_canonical": classification["EXACT_EXISTING_MATCH"],
        "genuinely_new_canonical_events": classification["NEW_CANONICAL_EVENT"],
        "ambiguous": classification["AMBIGUOUS_EXISTING_MATCH"],
        "conflicts": (
            classification["SOURCE_ID_CONFLICT"]
            + classification["TERMINAL_TRUTH_CONFLICT"]
            + classification["TOURNAMENT_EVIDENCE_CONFLICT"]
        ),
        "duplicates_prevented": duplicate_prevented,
        "matches_before": before["matches"],
        "matches_after": after["matches"],
        "net_new_matches": after["matches"] - before["matches"],
        "sr_match_events_before": before["sr_match_events"],
        "sr_match_events_after": after["sr_match_events"],
        "original_canonical_matches_gaining_sr_final_stats": linked_originals,
        "sr_stat_rows_before": before["sr_stat_rows"],
        "sr_stat_rows_after": after["sr_stat_rows"],
        "sr_stat_matches_before": before["sr_stat_matches"],
        "sr_stat_matches_after": after["sr_stat_matches"],
        "refresh_outcome": {
            "fixtures_seen": outcome.fixtures_seen,
            "fixtures_ingested": outcome.fixtures_ingested,
            "terminal_seen": outcome.terminal_seen,
            "observations_inserted": outcome.observations_inserted,
            "observations_unchanged": outcome.observations_unchanged,
            "result_rejections": outcome.result_rejections,
            "stat_rows_inserted": outcome.stat_rows_inserted,
            "stat_rejections": outcome.stat_rejections,
        },
        "aborted": False,
    }


def _key_for_event(event_id: str, cohort: _Cohort) -> str:
    for key in cohort.canonical:
        if f"sr:event:{key.replace(':', '_')}" == event_id:
            return key
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("TEST_POSTGRES_DSN")
        or os.environ.get("DATABASE_URL"),
    )
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    if not args.dsn:
        print(json.dumps({"aborted": True, "reason": "no_dsn"}))
        return 2
    if os.environ.get("TENNIS_ENVIRONMENT", "").lower() == "production":
        print(json.dumps({"aborted": True, "reason": "production_refused"}))
        return 2

    with psycopg.connect(args.dsn, autocommit=True) as setup:
        _apply_migrations(setup)

    with psycopg.connect(args.dsn) as conn:
        report = run(conn)
        if not args.keep:
            conn.rollback()
    print("SPORTRADAR_TERMINAL_OVERLAP_COHORT " + json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
