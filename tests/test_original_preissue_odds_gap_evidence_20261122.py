"""Pinned Nov22 read-only market gap evidence regression, never a backtest."""
import hashlib
import json
from pathlib import Path

PATH=Path(__file__).parents[1]/"docs/research/forward_captures/original_preissue_odds_gap_20261122.json"


def test_pinned_preissue_odds_100_sample_and_1342_population():
    r=json.loads(PATH.read_text())
    assert r["version"]=="FIRST_ISSUE_PREISSUE_BOOKMAKER_GAP_V1"
    assert r["original_selection_rows"]==1342
    assert r["fully_qualifying_live_preissue_two_sided"]==23
    assert r["unqualified_original_selections"]==1319
    assert r["unmatched_sample_count"]==100
    assert len(r["unmatched_sample_rows"])==100
    assert r["source_git_head_sha"]=="06f28ecb87b7fa67064a6ee6f6cebce03f590e26"
    assert r["railway_deployment_id"]=="c3bb8129-10fc-43b0-9b46-0b7b17163b2a"
    assert r["failure_categories"]=={
        "INVALID_OR_LATE_PIT_ORIGINAL_ISSUE":7,
        "MAPPED_ODDSPAPI_BUT_NO_STORED_ODDS":3,
        "NO_ODDSPAPI_FIXTURE_MAPPING_NO_QUOTE":675,
        "ONLY_LATER_OR_BACKFILLED_ODDS":634,
        "QUALIFIED_ORIGINAL_LIVE_PREISSUE_PAIR":23,
    }
    assert sum(r["failure_categories"].values())==1342
    assert len({x["match_id"] for x in r["unmatched_sample_rows"]})==100
    assert all(x["qualified_pair_count"]==0 for x in r["unmatched_sample_rows"])
    assert all(not x["qualifying_market_is_live_and_received_before_first_issue"]
               for x in r["unmatched_sample_rows"])
    assert hashlib.sha256(
        json.dumps(r["unmatched_sample_rows"],sort_keys=True,separators=(",",":")).encode()
    ).hexdigest()=="b9618298215526c1192078da7237c9b85b16b7447cbc88cfd82211e32e05a876"
    assert r["no_new_provider_calls"] and r["no_db_writes"] and r["no_purchases"]
    assert r["actual_official_eligible_fixture_denominator"] is None
    assert r["not_independent_original_publication_proof"]
