from pathlib import Path

import psycopg

from sports_edge_tennis.data.historical_validation import (
    build_dataset_specs,
    evaluate_dry_run_gate,
    run_dry_runs,
    validate_sackmann_boundaries,
)
from sports_edge_tennis.data.manifest import build_source_document
from sports_edge_tennis.data.pipeline import ingest_sackmann_document


def _fixture_csv_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "historical" / "sackmann_day5_fixture.csv"


def test_build_dataset_specs_uses_exact_approved_filenames():
    specs = build_dataset_specs((2024, 2025))

    assert [(spec.dataset, spec.year, spec.filename) for spec in specs] == [
        ("atp_main", 2024, "atp_matches_2024.csv"),
        ("atp_qual_chall", 2024, "atp_matches_qual_chall_2024.csv"),
        ("atp_main", 2025, "atp_matches_2025.csv"),
        ("atp_qual_chall", 2025, "atp_matches_qual_chall_2025.csv"),
    ]


def test_dry_run_gate_allows_known_quarantines_but_rejects_unknown_status(tmp_path: Path):
    approved_csv_path = tmp_path / "approved.csv"
    approved_csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,best_of,round",
                "2025-0605,Tour Finals,Hard,8,F,20251109,385,3,Player Three,4,Player Four,7-6(4) 7-5,3,F",
                "2019-7288,Seoul CH,Hard,64,C,20190429,255,117361,Akira Santillan,126174,Youssef Hossam,Def.,3,R64",
                "2019-7316,Cary CH,Hard,64,C,20190909,221,117365,Petros Chrysochos,132052,Christian Langmo,>,3,Q1",
                "2025-M-DC-2025-FLS-M-FRA-BEL-01,Davis Cup Finals QF: FRA vs BEL,Hard,2,D,20251118,1,5,Player Five,6,Player Six,2-6 7-5 7-5,3,RR",
                "2016-7182,Happy Valley CH,Hard,32,C,20160104,244,122059,Steven De Waard,122059,Steven De Waard,6-7(3) 6-4 6-2,3,Q1",
                "2025-7393,Tiburon CH,Hard,32,C,20250929,9387,11,Player Eleven,12,Player Twelve,,3,R32",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    approved_document = build_source_document(
        path=approved_csv_path,
        source_system="SACKMANN",
        source_name="Jeff Sackmann tennis_atp historical matches",
        dataset_type="historical_matches",
        season=2025,
    )

    approved_result = evaluate_dry_run_gate(
        run_dry_runs([approved_document], artifact_dir=tmp_path / "approved-artifacts")
    )

    assert approved_result["gate_pass"] is True
    assert approved_result["unknown_reason_counts"] == {}

    unsupported_csv_path = tmp_path / "unsupported.csv"
    unsupported_csv_path.write_text(
        "\n".join(
            [
                "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score,comment,best_of,round",
                "2025-9900,United Cup,Hard,18,A,20250101,400,123,Taylor Fritz,456,Hubert Hurkacz,6-4 5-7,abandoned,3,F",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    unsupported_document = build_source_document(
        path=unsupported_csv_path,
        source_system="SACKMANN",
        source_name="Jeff Sackmann tennis_atp historical matches",
        dataset_type="historical_matches",
        season=2025,
    )

    unsupported_result = evaluate_dry_run_gate(
        run_dry_runs([unsupported_document], artifact_dir=tmp_path / "unsupported-artifacts")
    )

    assert unsupported_result["gate_pass"] is False
    assert unsupported_result["unknown_reason_counts"] == {
        "unsupported_status_comment:abandoned": 1
    }


def test_validate_sackmann_boundaries_matches_ingested_fixture(
    db: psycopg.Connection,
    tmp_path: Path,
):
    document = build_source_document(
        path=_fixture_csv_path(),
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

    boundary_summary = validate_sackmann_boundaries(db, [document])

    assert boundary_summary["expected_records"] == 2
    assert boundary_summary["explicit_match_date_rows"] == 2
    assert boundary_summary["conservative_tournament_boundary_rows"] == 0
    assert boundary_summary["version_boundary_mismatches"] == 0
    assert boundary_summary["result_boundary_mismatches"] == 0
