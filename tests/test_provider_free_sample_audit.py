"""No-spend local provider sample audit fixture tests."""
import csv
import json
from pathlib import Path

import pytest

from sports_edge_tennis.validation.provider_free_sample_audit import (
    analyze,main,normalize,
)


def csv_file(tmp_path:Path,columns:list[str],rows:list[list[str]],name="matches.csv")->Path:
    p=tmp_path/name
    with p.open("w",encoding="utf-8",newline="") as f:
        w=csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)
    return p


def test_header_aliases_nonempty_fraction_and_unverified_market(tmp_path):
    f=csv_file(
        tmp_path,
        ["Match ID","Player 1 ID","Player 2 ID","Start Time","Surface",
         "Winner ID","Elo1","Elo2","Extra Research Field"],
        [
            ["a","1","2","2026-11-03T13:00:00Z","HARD","1","1680","1650","x"],
            ["b","3","4","2026-11-04T13:00:00Z","","4","1700","",""],
        ],
    )
    report=analyze(f,kind="matches")
    assert report["rows_inspected"]==2
    assert report["fields"]["fixture_id"]["column"]=="match_id"
    assert report["fields"]["surface"]["nonempty_rows"]==1
    assert report["fields"]["surface"]["nonempty_fraction"]==.5
    assert report["fields"]["book1_odds"]["column"] is None
    assert report["fields"]["rating1"]["nonempty_rows"]==2
    assert report["fields"]["rating2"]["nonempty_rows"]==1
    assert report["potential_joint_field_upper_bounds"]["rating_both_sides_with_valid_asof_upper_bound"]==0
    assert "Extra Research Field" in report["unknown_columns"]
    assert report["match_start_value_granularity"]=={"timestamp_aware":2}
    assert report["full_atp_challenger_coverage"]=="NOT_VERIFIED"
    assert not report["purchase_approved"]
    assert report["cost_usd"]==0
    assert len(report["sha256"])==64
    assert "a" not in json.dumps(report["fields"])


def test_date_only_is_distinguished_from_prematch_aware(tmp_path):
    f=csv_file(tmp_path,["match_id","date"],[
        ["a","2026-11-03"],["b","2026-11-03T13:00:00"],["c","not_a_date"]
    ])
    a=analyze(f,kind="matches")
    assert a["match_start_value_granularity"]=={
        "date_only":1,"timestamp_naive":1,"unrecognized":1}
    assert a["source_observation_time_validity"]=="NOT_VERIFIED"


def test_two_side_market_requires_real_timestamp_and_book(tmp_path):
    f=csv_file(tmp_path,["match_id","home_odds","away_odds","bookmaker","odds_created_at"],[
        ["a","1.50","2.30","pinnacle","2026-11-03T11:00:00Z"],
        ["b","1.60","","pinnacle","2026-11-04T11:00:00Z"],
    ])
    a=analyze(f,kind="matches")
    assert a["potential_joint_field_upper_bounds"]["two_sided_odds_with_timestamp_potential_upper_bound"]==1
    assert a["full_atp_challenger_coverage"]=="NOT_VERIFIED"
    assert a["commercial_use_and_storage_rights"]=="NOT_VERIFIED"


def test_duplicate_canonical_sample_id_cannot_be_silent(tmp_path):
    f=csv_file(tmp_path,["match_id"],[["m1"],["m2"],["m1"]])
    a=analyze(f,kind="matches")
    assert a["duplicate_nonempty_sample_identifiers"]==1


def test_player_sample_independent_fields(tmp_path):
    f=csv_file(tmp_path,["player_id","full_name","current_rank","last_match_at"],[
        ["p1","Player A","10","2026-10-15"],
        ["p2","Player B","",""],
    ],"players.csv")
    a=analyze(f,kind="players")
    assert a["fields"]["player_id"]["nonempty_rows"]==2
    assert a["fields"]["global_rank"]["nonempty_rows"]==1
    assert a["fields"]["rating_uncertainty"]["column"] is None
    assert a["potential_joint_field_upper_bounds"]=={}


def test_invalid_sample_rejected(tmp_path):
    f=csv_file(tmp_path,["Match ID","match-id"],[["a","a"]])
    with pytest.raises(ValueError,match="duplicate column"):
        analyze(f,kind="matches")
    with pytest.raises(ValueError,match="kind"):
        analyze(f,kind="events")
    with pytest.raises(ValueError,match="limit"):
        analyze(f,kind="matches",limit=0)


def test_cli_outputs_no_raw_rows_or_identity_values(tmp_path,capsys):
    f=csv_file(tmp_path,["match_id","surface"],[["secret_user_identity","HARD"]])
    assert main(["--matches-csv",str(f)])==0
    out=capsys.readouterr().out
    assert out.startswith("TENNIS_PROVIDER_FREE_SAMPLE_AUDIT ")
    assert "secret_user_identity" not in out
    d=json.loads(out.split(" ",1)[1])
    assert d[0]["rows_inspected"]==1
    assert d[0]["network_requests"]==0
    assert d[0]["database_writes"]==0
