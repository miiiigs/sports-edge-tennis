from datetime import UTC, datetime
from pathlib import Path

import openpyxl
import psycopg

from sports_edge_tennis.data.manifest import build_source_document
from sports_edge_tennis.data.pipeline import ingest_sackmann_document, ingest_tennis_data_document


def _fixture_csv_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "historical" / "sackmann_day5_fixture.csv"


def _odds_workbook(tmp_path: Path) -> Path:
    workbook_path = tmp_path / "2026.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "Date",
            "Tournament",
            "Surface",
            "Round",
            "Best of",
            "Winner",
            "Loser",
            "B365W",
            "B365L",
        ]
    )
    sheet.append(["2026-08-25", "Sample Masters", "Hard", "R32", 3, "Nadal R.", "Djokovic N.", 1.8, 2.1])
    sheet.append(["2026-08-25", "Sample Masters", "Hard", "R32", 3, "Mystery P.", "Ghost G.", 1.9, 1.9])
    sheet.append(["2026-08-25", "Sample Masters", "Hard", "R32", 3, "Nadal R.", "Djokovic N.", 0.0, 2.1])
    workbook.save(workbook_path)
    return workbook_path


def _fetch_count(conn: psycopg.Connection, table_name: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0]


def test_historical_ingest_is_idempotent_and_logs_unmatched(db: psycopg.Connection, tmp_path: Path):
    match_document = build_source_document(
        path=_fixture_csv_path(),
        source_system="SACKMANN",
        source_name="Jeff Sackmann tennis_atp historical matches",
        dataset_type="historical_matches",
        season=2026,
    )
    match_quality_path = tmp_path / "match_quality.json"
    match_unmatched_path = tmp_path / "match_unmatched.json"
    first_result = ingest_sackmann_document(
        db,
        document=match_document,
        dry_run=False,
        quality_path=match_quality_path,
        unmatched_path=match_unmatched_path,
    )
    assert first_result["status"] == "COMPLETED"
    assert _fetch_count(db, "players") == 4
    assert _fetch_count(db, "matches") == 2
    assert _fetch_count(db, "match_versions") == 2
    assert _fetch_count(db, "match_result_observations") == 2
    assert _fetch_count(db, "match_stat_observations") == 4

    ingest_sackmann_document(
        db,
        document=match_document,
        dry_run=False,
        quality_path=match_quality_path,
        unmatched_path=match_unmatched_path,
    )
    assert _fetch_count(db, "players") == 4
    assert _fetch_count(db, "matches") == 2
    assert _fetch_count(db, "match_versions") == 2
    assert _fetch_count(db, "match_result_observations") == 2
    assert _fetch_count(db, "match_stat_observations") == 4
    assert _fetch_count(db, "ingest_runs") == 2

    odds_document = build_source_document(
        path=_odds_workbook(tmp_path),
        source_system="TENNIS_DATA",
        source_name="Tennis-Data historical odds archive",
        dataset_type="historical_odds",
        season=2026,
    )
    odds_quality_path = tmp_path / "odds_quality.json"
    odds_unmatched_path = tmp_path / "odds_unmatched.json"
    odds_result = ingest_tennis_data_document(
        db,
        document=odds_document,
        dry_run=False,
        quality_path=odds_quality_path,
        unmatched_path=odds_unmatched_path,
    )
    assert odds_result["status"] == "COMPLETED"
    assert _fetch_count(db, "odds_snapshots") == 2
    assert _fetch_count(db, "identity_resolution_events") == 2
    assert match_quality_path.exists()
    assert odds_quality_path.exists()
    assert odds_unmatched_path.exists()


def test_point_in_time_query_excludes_future_results(db: psycopg.Connection, tmp_path: Path):
    match_document = build_source_document(
        path=_fixture_csv_path(),
        source_system="SACKMANN",
        source_name="Jeff Sackmann tennis_atp historical matches",
        dataset_type="historical_matches",
        season=2026,
    )
    ingest_sackmann_document(
        db,
        document=match_document,
        dry_run=False,
        quality_path=tmp_path / "quality.json",
        unmatched_path=tmp_path / "unmatched.json",
    )

    match_id = db.execute(
        """
        SELECT m.match_id
        FROM matches m
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        WHERE t.canonical_name = 'Sample Masters'
        """
    ).fetchone()[0]

    query_time = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    version_count = db.execute(
        """
        SELECT count(*)
        FROM match_versions
        WHERE match_id = %s AND as_of <= %s
        """,
        (match_id, query_time),
    ).fetchone()[0]
    result_count = db.execute(
        """
        SELECT count(*)
        FROM match_result_observations
        WHERE match_id = %s AND as_of <= %s
        """,
        (match_id, query_time),
    ).fetchone()[0]
    assert version_count == 1
    assert result_count == 0
