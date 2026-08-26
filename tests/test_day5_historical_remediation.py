import json
from datetime import UTC, datetime
from pathlib import Path

import openpyxl
import psycopg

from sports_edge_tennis.data.manifest import build_source_document, write_manifest
from sports_edge_tennis.data.pipeline import ingest_sackmann_document, ingest_tennis_data_document
from sports_edge_tennis.data.sources.sackmann import parse_sackmann_csv
from sports_edge_tennis.data.sources.tennis_data import parse_tennis_data_file


def _build_tennis_data_workbook(
    tmp_path: Path,
    *,
    filename: str,
    event_date: str,
    tournament_name: str,
    winner_name: str,
    loser_name: str,
    round_code: str | None,
    best_of_sets: int | None,
) -> Path:
    workbook_path = tmp_path / filename
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
    sheet.append(
        [
            event_date,
            tournament_name,
            "Hard",
            round_code or "",
            best_of_sets if best_of_sets is not None else "",
            winner_name,
            loser_name,
            1.80,
            2.00,
        ]
    )
    workbook.save(workbook_path)
    return workbook_path


def test_parse_sackmann_realistic_no_match_date_header_uses_null_match_time_and_grand_slam_boundary(
    tmp_path: Path,
):
    csv_path = tmp_path / "atp_matches_2025.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round",
                "2025-580,Australian Open,Hard,128,G,20250113,701,101,Player One,102,Player Two,6-4 6-4 6-4,5,F",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    document = build_source_document(
        path=csv_path,
        source_system="SACKMANN",
        source_name="Jeff Sackmann tennis_atp historical matches",
        dataset_type="historical_matches",
        season=2025,
    )

    outcome = parse_sackmann_csv(document)

    assert len(outcome.records) == 1
    record = outcome.records[0]
    assert record.tournament_start_date.isoformat() == "2025-01-13"
    assert record.tournament_end_date.isoformat() == "2025-01-26"
    assert record.competition_tier == "GRAND_SLAM"
    assert record.match_start_at is None
    assert record.match_as_of == datetime(2025, 1, 26, 23, 59, 59, tzinfo=UTC)
    assert record.result_as_of == datetime(2025, 1, 26, 23, 59, 59, tzinfo=UTC)
    assert "match_start_at unknown" in record.limitations[0]


def test_parse_sackmann_missing_match_date_uses_one_week_challenger_boundary(tmp_path: Path):
    csv_path = tmp_path / "challenger_matches_2024.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round",
                "2024-7700,Sample Challenger,Clay,32,C,20240819,401,101,Player One,102,Player Two,6-4 6-4,3,F",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    document = build_source_document(
        path=csv_path,
        source_system="SACKMANN",
        source_name="Jeff Sackmann tennis_atp historical matches",
        dataset_type="historical_matches",
        season=2024,
    )

    outcome = parse_sackmann_csv(document)

    assert len(outcome.records) == 1
    record = outcome.records[0]
    assert record.competition_tier == "CHALLENGER"
    assert record.tournament_start_date.isoformat() == "2024-08-19"
    assert record.tournament_end_date.isoformat() == "2024-08-25"
    assert record.match_start_at is None
    assert record.match_as_of == datetime(2024, 8, 25, 23, 59, 59, tzinfo=UTC)
    assert record.result_as_of == datetime(2024, 8, 25, 23, 59, 59, tzinfo=UTC)


def test_grand_slam_temporal_regression_uses_database_as_of_filtering_for_unknown_sackmann_match_dates(
    db: psycopg.Connection,
    tmp_path: Path,
):
    csv_path = tmp_path / "grand_slam_temporal_regression.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round,comment,w_ace,w_df,w_svpt,w_1stIn,w_1stWon,w_2ndWon,w_bpSaved,w_bpFaced,l_ace,l_df,l_svpt,l_1stIn,l_1stWon,l_2ndWon,l_bpSaved,l_bpFaced",
                "2025-580,Australian Open,Hard,128,G,20250113,101,1001,Early Winner,1002,Early Loser,6-2 6-3 6-2,5,R128,,5,1,50,30,24,10,2,3,2,3,49,31,20,9,1,4",
                "2025-580,Australian Open,Hard,128,G,20250113,501,1003,Quarter Winner,1004,Quarter Loser,7-5 6-4 6-4,5,QF,,8,2,64,40,31,15,5,8,6,4,61,37,27,13,3,7",
                "2025-580,Australian Open,Hard,128,G,20250113,601,1005,Semi Winner,1006,Semi Loser,6-4 3-6 6-4 6-4,5,SF,,11,2,79,48,36,19,7,10,7,5,77,45,33,18,4,9",
                "2025-580,Australian Open,Hard,128,G,20250113,701,1005,Semi Winner,1003,Quarter Winner,6-4 6-4 6-4,5,F,,9,1,58,34,28,12,3,5,4,2,57,36,24,11,2,5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    document = build_source_document(
        path=csv_path,
        source_system="SACKMANN",
        source_name="Jeff Sackmann tennis_atp historical matches",
        dataset_type="historical_matches",
        season=2025,
    )

    ingest_sackmann_document(
        db,
        document=document,
        dry_run=False,
        quality_path=tmp_path / "quality.json",
        unmatched_path=tmp_path / "unmatched.json",
    )

    during_tournament = datetime(2025, 1, 24, 12, 0, tzinfo=UTC)
    after_boundary = datetime(2025, 1, 27, 0, 0, tzinfo=UTC)

    final_match_versions_during = db.execute(
        """
        SELECT count(*)
        FROM match_versions mv
        JOIN matches m ON m.match_id = mv.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        WHERE t.canonical_name = 'Australian Open'
          AND mv.round_code = 'F'
          AND mv.as_of <= %s
        """,
        (during_tournament,),
    ).fetchone()[0]
    final_results_during = db.execute(
        """
        SELECT count(*)
        FROM match_result_observations mro
        JOIN matches m ON m.match_id = mro.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN match_versions mv ON mv.match_id = mro.match_id
        WHERE t.canonical_name = 'Australian Open'
          AND mv.round_code IN ('SF', 'F')
          AND mro.as_of <= %s
        """,
        (during_tournament,),
    ).fetchone()[0]
    final_stats_during = db.execute(
        """
        SELECT count(*)
        FROM match_stat_observations mso
        JOIN matches m ON m.match_id = mso.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN match_versions mv ON mv.match_id = mso.match_id
        WHERE t.canonical_name = 'Australian Open'
          AND mv.round_code IN ('SF', 'F')
          AND mso.as_of <= %s
        """,
        (during_tournament,),
    ).fetchone()[0]
    final_results_after = db.execute(
        """
        SELECT count(*)
        FROM match_result_observations mro
        JOIN matches m ON m.match_id = mro.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN match_versions mv ON mv.match_id = mro.match_id
        WHERE t.canonical_name = 'Australian Open'
          AND mv.round_code = 'F'
          AND mro.as_of <= %s
        """,
        (after_boundary,),
    ).fetchone()[0]
    final_stats_after = db.execute(
        """
        SELECT count(*)
        FROM match_stat_observations mso
        JOIN matches m ON m.match_id = mso.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN match_versions mv ON mv.match_id = mso.match_id
        WHERE t.canonical_name = 'Australian Open'
          AND mv.round_code = 'F'
          AND mso.as_of <= %s
        """,
        (after_boundary,),
    ).fetchone()[0]

    assert final_match_versions_during == 0
    assert final_results_during == 0
    assert final_stats_during == 0
    assert final_results_after == 1
    assert final_stats_after == 2


def test_one_week_temporal_regression_stays_hidden_until_week_end(
    db: psycopg.Connection,
    tmp_path: Path,
):
    csv_path = tmp_path / "one_week_temporal_regression.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round,comment,w_ace,w_df,w_svpt,w_1stIn,w_1stWon,w_2ndWon,w_bpSaved,w_bpFaced,l_ace,l_df,l_svpt,l_1stIn,l_1stWon,l_2ndWon,l_bpSaved,l_bpFaced",
                "2024-7700,Sample Challenger,Clay,32,C,20240819,401,1001,Final Winner,1002,Final Loser,6-4 6-4,3,F,,9,1,58,34,28,12,3,5,4,2,57,36,24,11,2,5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    document = build_source_document(
        path=csv_path,
        source_system="SACKMANN",
        source_name="Jeff Sackmann tennis_atp historical matches",
        dataset_type="historical_matches",
        season=2024,
    )

    ingest_sackmann_document(
        db,
        document=document,
        dry_run=False,
        quality_path=tmp_path / "quality.json",
        unmatched_path=tmp_path / "unmatched.json",
    )

    during_tournament = datetime(2024, 8, 25, 12, 0, tzinfo=UTC)
    after_boundary = datetime(2024, 8, 26, 0, 0, tzinfo=UTC)

    final_results_during = db.execute(
        """
        SELECT count(*)
        FROM match_result_observations mro
        JOIN matches m ON m.match_id = mro.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        WHERE t.canonical_name = 'Sample Challenger'
          AND mro.as_of <= %s
        """,
        (during_tournament,),
    ).fetchone()[0]
    final_results_after = db.execute(
        """
        SELECT count(*)
        FROM match_result_observations mro
        JOIN matches m ON m.match_id = mro.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        WHERE t.canonical_name = 'Sample Challenger'
          AND mro.as_of <= %s
        """,
        (after_boundary,),
    ).fetchone()[0]

    assert final_results_during == 0
    assert final_results_after == 1


def test_cross_source_reconciliation_matches_unknown_sackmann_timing_without_fabricated_date(
    db: psycopg.Connection,
    tmp_path: Path,
):
    csv_path = tmp_path / "atp_matches_2025.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round",
                "2025-580,Australian Open,Hard,128,G,20250113,701,1001,Player Winner,1002,Player Loser,6-4 6-4 6-4,5,F",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    match_document = build_source_document(
        path=csv_path,
        source_system="SACKMANN",
        source_name="Jeff Sackmann tennis_atp historical matches",
        dataset_type="historical_matches",
        season=2025,
    )
    ingest_sackmann_document(
        db,
        document=match_document,
        dry_run=False,
        quality_path=tmp_path / "match_quality.json",
        unmatched_path=tmp_path / "match_unmatched.json",
    )

    odds_path = _build_tennis_data_workbook(
        tmp_path,
        filename="australian_open_odds.xlsx",
        event_date="2025-01-26",
        tournament_name="Australian Open",
        winner_name="Player Winner",
        loser_name="Player Loser",
        round_code="F",
        best_of_sets=5,
    )
    odds_document = build_source_document(
        path=odds_path,
        source_system="TENNIS_DATA",
        source_name="Tennis-Data historical odds archive",
        dataset_type="historical_odds",
        season=2025,
    )

    result = ingest_tennis_data_document(
        db,
        document=odds_document,
        dry_run=False,
        quality_path=tmp_path / "odds_quality.json",
        unmatched_path=tmp_path / "odds_unmatched.json",
    )

    assert result["status"] == "COMPLETED"
    assert result["counters"]["rows_unmatched"] == 0
    assert result["counters"]["odds_matched_rows"] == 2
    assert db.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0] == 2
    assert db.execute("SELECT match_start_at FROM match_versions").fetchone()[0] is None


def test_cross_source_reconciliation_fails_closed_when_unknown_timing_candidates_are_ambiguous(
    db: psycopg.Connection,
    tmp_path: Path,
):
    csv_path = tmp_path / "ambiguous_atp_matches_2025.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round",
                "2025-9900,Sample Finals,Hard,8,A,20251110,101,2001,Player Winner,2002,Player Loser,6-4 6-4,3,RR",
                "2025-9900,Sample Finals,Hard,8,A,20251110,401,2001,Player Winner,2002,Player Loser,6-4 6-4,3,F",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    match_document = build_source_document(
        path=csv_path,
        source_system="SACKMANN",
        source_name="Jeff Sackmann tennis_atp historical matches",
        dataset_type="historical_matches",
        season=2025,
    )
    ingest_sackmann_document(
        db,
        document=match_document,
        dry_run=False,
        quality_path=tmp_path / "match_quality.json",
        unmatched_path=tmp_path / "match_unmatched.json",
    )

    odds_path = _build_tennis_data_workbook(
        tmp_path,
        filename="ambiguous_finals_odds.xlsx",
        event_date="2025-11-13",
        tournament_name="Sample Finals",
        winner_name="Player Winner",
        loser_name="Player Loser",
        round_code=None,
        best_of_sets=None,
    )
    odds_document = build_source_document(
        path=odds_path,
        source_system="TENNIS_DATA",
        source_name="Tennis-Data historical odds archive",
        dataset_type="historical_odds",
        season=2025,
    )

    quality_path = tmp_path / "odds_quality.json"
    unmatched_path = tmp_path / "odds_unmatched.json"
    result = ingest_tennis_data_document(
        db,
        document=odds_document,
        dry_run=False,
        quality_path=quality_path,
        unmatched_path=unmatched_path,
    )
    unmatched_payload = json.loads(unmatched_path.read_text(encoding="utf-8"))

    assert result["status"] == "COMPLETED"
    assert result["counters"]["odds_matched_rows"] == 0
    assert result["counters"]["rows_unmatched"] == 2
    assert result["counters"]["odds_ambiguous_rows"] == 2
    assert db.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0] == 0
    assert {entry["reason"] for entry in unmatched_payload} == {"odds_match_resolution_failed"}


def test_parse_tennis_data_excludes_avg_and_max_columns_from_bookmakers(tmp_path: Path):
    workbook_path = tmp_path / "2012.xlsx"
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
            "EXW",
            "EXL",
            "LBW",
            "LBL",
            "PSW",
            "PSL",
            "SJW",
            "SJL",
            "AvgW",
            "AvgL",
            "MaxW",
            "MaxL",
        ]
    )
    sheet.append(
        [
            "2012-01-02",
            "Sample Open",
            "Hard",
            "R32",
            3,
            "Player Winner",
            "Player Loser",
            1.8,
            2.0,
            1.82,
            1.98,
            1.81,
            1.99,
            1.84,
            1.96,
            1.79,
            2.02,
            1.83,
            1.97,
            1.85,
            2.05,
        ]
    )
    workbook.save(workbook_path)

    document = build_source_document(
        path=workbook_path,
        source_system="TENNIS_DATA",
        source_name="Tennis-Data historical odds archive",
        dataset_type="historical_odds",
        season=2012,
    )

    outcome = parse_tennis_data_file(document)
    bookmaker_codes = {record.bookmaker_code for record in outcome.records}

    assert bookmaker_codes == {"B365", "EX", "LB", "PS", "SJ"}
    assert "Avg" not in bookmaker_codes
    assert "Max" not in bookmaker_codes
    assert len(outcome.records) == 10


def test_dry_run_identity_metrics_are_marked_not_executed(tmp_path: Path):
    fixture_path = (
        Path(__file__).resolve().parent / "fixtures" / "historical" / "sackmann_day5_fixture.csv"
    )
    document = build_source_document(
        path=fixture_path,
        source_system="SACKMANN",
        source_name="Jeff Sackmann tennis_atp historical matches",
        dataset_type="historical_matches",
        season=2024,
    )

    quality_path = tmp_path / "quality.json"
    unmatched_path = tmp_path / "unmatched.json"
    result = ingest_sackmann_document(
        None,
        document=document,
        dry_run=True,
        quality_path=quality_path,
        unmatched_path=unmatched_path,
    )
    quality_payload = json.loads(quality_path.read_text(encoding="utf-8"))

    assert result["status"] == "DRY_RUN"
    assert result["identity_resolution_status"] == "NOT_EXECUTED"
    assert result["counters"]["rows_unmatched"] is None
    assert result["counters"]["unmatched_players"] is None
    assert result["counters"]["ambiguous_players"] is None
    assert quality_payload["identity_resolution_status"] == "NOT_EXECUTED"
    assert quality_payload["rows_unmatched"] is None
    assert quality_payload["unmatched_players"] is None
    assert quality_payload["ambiguous_players"] is None


def test_manifest_preserves_split_provenance_and_is_deterministic(tmp_path: Path):
    path_a = tmp_path / "b_source.csv"
    path_b = tmp_path / "a_source.csv"
    path_a.write_text("alpha\n", encoding="utf-8")
    path_b.write_text("beta\n", encoding="utf-8")

    document_a = build_source_document(
        path=path_a,
        source_system="SACKMANN",
        source_name="Jeff Sackmann tennis_atp historical matches",
        dataset_type="historical_matches",
        season=2025,
        canonical_source="Jeff Sackmann tennis_atp",
        retrieval_source="Aneeshers/tennis-sackmann-archive",
        archive_revision="83733587353df8a41f2fd4f516147d5aa83f5a8d",
        upstream_revision=None,
        source_license="CC BY-NC-SA 4.0",
    )
    document_b = build_source_document(
        path=path_b,
        source_system="TENNIS_DATA",
        source_name="Tennis-Data historical odds archive",
        dataset_type="historical_odds",
        season=2012,
        canonical_source="Tennis-Data",
        retrieval_source="Tennis-Data",
        archive_revision=None,
        upstream_revision=None,
        source_license=None,
    )

    generated_at = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
    manifest_path_1 = tmp_path / "manifest-1.json"
    manifest_path_2 = tmp_path / "manifest-2.json"
    write_manifest(manifest_path_1, [document_a, document_b], generated_at=generated_at)
    write_manifest(manifest_path_2, [document_b, document_a], generated_at=generated_at)

    manifest_1 = manifest_path_1.read_text(encoding="utf-8")
    manifest_2 = manifest_path_2.read_text(encoding="utf-8")
    payload = json.loads(manifest_1)

    assert manifest_1 == manifest_2
    assert payload["sources"][0]["canonical_source"] == "Jeff Sackmann tennis_atp"
    assert payload["sources"][0]["retrieval_source"] == "Aneeshers/tennis-sackmann-archive"
    assert payload["sources"][0]["archive_revision"] == "83733587353df8a41f2fd4f516147d5aa83f5a8d"
    assert payload["sources"][0]["upstream_revision"] is None
    assert payload["sources"][0]["source_license"] == "CC BY-NC-SA 4.0"
    assert payload["sources"][0]["file_sha256"] == document_a.sha256
