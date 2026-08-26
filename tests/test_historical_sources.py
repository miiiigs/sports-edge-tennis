from pathlib import Path

import openpyxl

from sports_edge_tennis.data.manifest import build_source_document
from sports_edge_tennis.data.sources.sackmann import parse_sackmann_csv
from sports_edge_tennis.data.sources.tennis_data import parse_tennis_data_file


def test_parse_sackmann_fixture_file():
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
    outcome = parse_sackmann_csv(document)
    assert len(outcome.records) == 2
    assert outcome.records[0].competition_tier == "MASTERS_1000"
    assert outcome.records[1].tour_scope == "CHALLENGER"
    assert outcome.rejected_rows == []


def test_parse_sackmann_realistic_atp_level_without_explicit_competition_tier(tmp_path: Path):
    csv_path = tmp_path / "atp_matches_2025.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round",
                "2025-9900,United Cup,Hard,18,A,20250101,400,123,Taylor Fritz,456,Hubert Hurkacz,6-4 5-7 7-6(4),3,F",
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
    assert outcome.records[0].tour_scope == "ATP_MAIN"
    assert outcome.records[0].competition_tier == "ATP_TOUR"
    assert outcome.rejected_rows == []


def test_parse_sackmann_qual_chall_file_keeps_non_challenger_qualifying_rows_atp_main(tmp_path: Path):
    csv_path = tmp_path / "atp_matches_qual_chall_2025.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round",
                "2025-580,Australian Open,Hard,128,G,20250112,700,111,Dominik Koepfer,222,Ignacio Buse,6-4 6-3,3,Q1",
                "2025-7393,Canberra CH,Hard,32,C,20250101,387,333,Joao Fonseca,444,Ethan Quinn,6-4 6-3,3,F",
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

    assert len(outcome.records) == 2
    assert outcome.records[0].tour_scope == "ATP_MAIN"
    assert outcome.records[0].competition_tier == "GRAND_SLAM"
    assert outcome.records[0].qualifying_flag is True
    assert outcome.records[1].tour_scope == "CHALLENGER"
    assert outcome.records[1].competition_tier == "CHALLENGER"


def test_parse_tennis_data_xlsx_and_reject_invalid_price(tmp_path: Path):
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
    sheet.append(["2026-08-25", "Sample Masters", "Hard", "R32", 3, "Nadal R.", "Djokovic N.", 0.0, 2.1])
    workbook.save(workbook_path)

    document = build_source_document(
        path=workbook_path,
        source_system="TENNIS_DATA",
        source_name="Tennis-Data historical odds archive",
        dataset_type="historical_odds",
        season=2026,
    )
    outcome = parse_tennis_data_file(document)
    assert len(outcome.records) == 2
    assert outcome.records[0].bookmaker_code == "B365"
    assert len(outcome.rejected_rows) == 1
    assert outcome.rejected_rows[0]["reason"] == "invalid_decimal_price"
