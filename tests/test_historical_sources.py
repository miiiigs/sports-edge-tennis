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
