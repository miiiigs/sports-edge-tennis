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
    assert outcome.records[0].match_start_at is None
    assert outcome.records[1].tour_scope == "CHALLENGER"
    assert outcome.rejected_rows == []
    assert outcome.audit.missing_player_id_rows == 0
    assert outcome.audit.missing_match_date_rows == 0
    assert outcome.audit.stat_null_patterns["partial_stats"] == 2


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


def test_parse_sackmann_score_suffix_retired_without_comment_and_audits_status(tmp_path: Path):
    csv_path = tmp_path / "atp_matches_2025.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round",
                "2025-9900,United Cup,Hard,18,A,20250101,400,123,Taylor Fritz,456,Hubert Hurkacz,6-4 5-7 7-6(4),3,F",
                "2025-9900,United Cup,Hard,18,A,20250101,399,123,Taylor Fritz,456,Hubert Hurkacz,6-7(4) 6-5 RET,3,SF",
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
    vocabulary = {
        (entry.raw_value, entry.normalized_status, entry.accepted): entry.row_count
        for entry in outcome.audit.status_vocabulary
    }

    assert len(outcome.records) == 2
    assert outcome.records[1].outcome_status == "RETIRED"
    assert vocabulary[(None, "COMPLETED", True)] == 1
    assert vocabulary[("RET", "RETIRED", True)] == 1


def test_parse_sackmann_default_token_is_mapped_and_bare_advancement_marker_is_quarantined(
    tmp_path: Path,
):
    csv_path = tmp_path / "atp_matches_qual_chall_2019.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round",
                "2019-7288,Seoul CH,Hard,64,C,20190429,255,117361,Akira Santillan,126174,Youssef Hossam,Def.,3,R64",
                "2019-7316,Cary CH,Hard,64,C,20190909,221,117365,Petros Chrysochos,132052,Christian Langmo,>,3,Q1",
                "2016-0670,Binghamton CH,Hard,32,C,20160718,279,105655,Hiroki Moriya,122248,Peter Kobelt,6-0 4-1 Def.,3,R32",
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
        season=2019,
    )

    outcome = parse_sackmann_csv(document)
    vocabulary = {
        (entry.raw_value, entry.normalized_status, entry.accepted): entry.row_count
        for entry in outcome.audit.status_vocabulary
    }

    assert len(outcome.records) == 2
    assert [record.outcome_status for record in outcome.records] == [
        "RETIRED",
        "RETIRED",
    ]
    assert vocabulary[("DEF", "RETIRED", True)] == 2
    assert vocabulary[(">", None, False)] == 1
    assert outcome.rejected_rows == [
        {
            "loser_name": "Christian Langmo",
            "reason": "ambiguous_advancement_marker",
            "row_number": 3,
            "tournament_name": "Cary CH",
            "winner_name": "Petros Chrysochos",
        }
    ]


def test_parse_sackmann_rejects_identical_source_player_ids(tmp_path: Path):
    csv_path = tmp_path / "atp_matches_qual_chall_2016.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round",
                "2016-7182,Happy Valley CH,Hard,32,C,20160104,244,122059,Steven De Waard,122059,Steven De Waard,6-7(3) 6-4 6-2,3,Q1",
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
        season=2016,
    )

    outcome = parse_sackmann_csv(document)

    assert outcome.records == []
    assert outcome.rejected_rows == [
        {
            "loser_name": "Steven De Waard",
            "reason": "self_match_source_player_id",
            "row_number": 2,
            "tournament_name": "Happy Valley CH",
            "winner_name": "Steven De Waard",
        }
    ]


def test_parse_sackmann_olympics_and_finals_are_in_scope_but_davis_cup_is_rejected(tmp_path: Path):
    csv_path = tmp_path / "atp_matches_2025.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round",
                "2024-0096,Paris Olympics,Clay,64,O,20240729,101,1,Player One,2,Player Two,6-0 6-1,3,R64",
                "2025-0605,Tour Finals,Hard,8,F,20251109,385,3,Player Three,4,Player Four,7-6(4) 7-5,3,F",
                "2025-M-DC-2025-FLS-M-FRA-BEL-01,Davis Cup Finals QF: FRA vs BEL,Hard,2,D,20251118,1,5,Player Five,6,Player Six,2-6 7-5 7-5,3,RR",
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
    assert {record.tournament_name: record.competition_tier for record in outcome.records} == {
        "Paris Olympics": "ATP_TOUR",
        "Tour Finals": "ATP_TOUR",
    }
    assert outcome.rejected_rows[0]["reason"] == "out_of_scope_tourney_level:D"


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
