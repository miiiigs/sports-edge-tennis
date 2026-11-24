import copy

import pytest

from sports_edge_tennis.validation.odds_identity_market_funnel import audit


def data():
    base = {"event_id": "odds1", "bookmaker": "pinnacle+30", "source": "ODDSPAPI",
            "market": "MATCH_WINNER", "observed_at": "2026-11-23T10:00:00Z",
            "as_of": "2026-11-23T10:00:01Z", "ingested_at": "2026-11-23T10:00:02Z",
            "status": "OPEN"}
    return {
        "audit_at": "2026-11-23T12:00:00Z",
        "fixtures": [{"match_id": 1, "source_ids": {"SPORTRADAR": "sr:sport_event:123"},
                      "players": [10, 20], "verified_tournament_ids": [7],
                      "start_at": "2026-11-23T14:00:00Z",
                      "cutoff": "2026-11-23T12:00:00Z"}],
        "events": [{"event_id": "odds1", "external_ids": {"SPORTRADAR": "123"},
                    "canonical_players": [20, 10], "tournament_id": 7,
                    "observed_at": "2026-11-23T09:00:00Z",
                    "start_at": "2026-11-23T14:00:00Z"}],
        "quotes": [{**base, "player_id": 10, "price": 1.5},
                   {**base, "player_id": 20, "price": 2.8}],
    }


def state(d):
    return audit(d)["rows"][0]["books"]["pinnacle+30"]["state"]


def identity(d):
    return audit(d)["rows"][0]["identity"]


def test_exact_namespaced_id_and_reversed_provider_slots():
    d = data()
    before = copy.deepcopy(d)
    result = audit(d)
    assert d == before
    assert result["provider_calls"] == 0
    assert result["database_writes"] is False
    assert result["qualified_any_book"] == 1
    assert result["rows"][0]["books"]["pinnacle+30"]["quote"]["p_player1"] == pytest.approx((1/1.5)/(1/1.5+1/2.8))


def test_namespaces_do_not_mix():
    d = data()
    d["fixtures"][0]["source_ids"] = {"API_TENNIS": "123"}
    assert identity(d) == "MISSING_IDENTITY"


def test_missing_identity_is_not_missing_market():
    d = data()
    d["events"] = []
    assert identity(d) == "MISSING_IDENTITY"
    assert audit(d)["rows"][0]["books"] == {}


def test_no_market_after_verified_identity():
    d = data()
    d["quotes"] = []
    assert state(d) == "NO_STORED_MARKET"


def test_late_ingestion():
    d = data()
    for q in d["quotes"]:
        q["ingested_at"] = "2026-11-23T12:01:00Z"
    assert state(d) == "LATE_QUOTATION"


def test_suspension_cannot_resurrect_older_open_price():
    d = data()
    d["quotes"].append({**d["quotes"][0], "status": "SUSPENDED",
                        "observed_at": "2026-11-23T11:00:00Z",
                        "as_of": "2026-11-23T11:00:01Z",
                        "ingested_at": "2026-11-23T11:00:02Z"})
    assert state(d) == "SUSPENDED_MARKET"


def test_unverified_player_and_tournament():
    d = data()
    d["events"][0]["canonical_players"] = [10, 30]
    assert identity(d) == "INVALID_PLAYER_SIDE_MAPPING"
    d = data()
    d["events"][0]["tournament_id"] = 8
    assert identity(d) == "UNVERIFIED_TOURNAMENT"


def test_competing_provider_events_fail_closed():
    d = data()
    d["events"].append({**d["events"][0], "event_id": "odds2"})
    assert identity(d) == "IDENTITY_CONFLICT"


def test_duplicate_event_conflict_even_with_other_alias():
    d = data()
    d["events"].append({**d["events"][0], "external_ids": {"SPORTRADAR": "456"}})
    assert identity(d) == "IDENTITY_CONFLICT"


def test_one_event_cannot_map_to_two_canonical_matches():
    d = data()
    d["fixtures"].append({**d["fixtures"][0], "match_id": 2})
    assert audit(d)["identity_counts"] == {"IDENTITY_CONFLICT": 2}


def test_invalid_quote_side_and_cross_source():
    d = data()
    d["quotes"][0]["player_id"] = 30
    assert state(d) == "INVALID_PLAYER_SIDE_MAPPING"
    d = data()
    d["quotes"][0]["source"] = "OTHER"
    assert state(d) == "UNSYNCHRONIZED_MARKET"


def test_duplicate_identical_rows_deduplicate():
    d = data()
    d["events"] *= 2
    d["fixtures"] *= 2
    d["quotes"] *= 2
    assert audit(d)["qualified_any_book"] == 1


def test_identity_seen_after_original_cutoff_not_live_available():
    d = data()
    d["fixtures"][0]["cutoff"] = "2026-11-23T08:30:00Z"
    assert state(d) == "IDENTITY_NOT_AVAILABLE_AT_CUTOFF"


def test_no_timezone_or_bad_time_is_rejected():
    d = data()
    d["quotes"][0]["observed_at"] = "2026-11-23T10:00:00"
    assert state(d) == "INVALID_QUOTE_TIMESTAMP"
    d = data()
    d["audit_at"] = "2026-11-23T12:00:00"
    with pytest.raises(ValueError):
        audit(d)


def test_conflicting_duplicate_canonical_fixture_raises():
    d = data()
    d["fixtures"].append({**d["fixtures"][0], "players": [20, 10]})
    with pytest.raises(ValueError):
        audit(d)
