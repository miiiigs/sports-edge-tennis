import inspect

import sports_edge_tennis.validation.sportradar_canonical_link_audit as audit_module
from sports_edge_tennis.validation.sportradar_canonical_link_audit import (
    classify_oddspapi_crosscheck,
    delta_bucket,
    event_id_key,
    event_id_variants,
    player_binding_state,
    summarize_player_bindings,
)


def test_event_id_variants_normalize_prefixed_and_bare_betradar_ids():
    assert event_id_variants("sr:sport_event:123") == frozenset(
        {"sr:sport_event:123", "123"}
    )
    assert event_id_variants("123") == frozenset({"sr:sport_event:123", "123"})
    assert event_id_key("sr:sport_event:123") == "123"


def test_oddspapi_crosscheck_triple_exact():
    status, row = classify_oddspapi_crosscheck(
        event_id="sr:sport_event:123",
        oddspapi_by_betradar={"123": [{"fixtureId": "fx1"}]},
    )

    assert status == "TRIPLE_EXACT"
    assert row == {"fixtureId": "fx1"}


def test_oddspapi_crosscheck_conflicting_betradar_id():
    status, row = classify_oddspapi_crosscheck(
        event_id="sr:sport_event:123",
        oddspapi_by_betradar={"123": [{"fixtureId": "fx1"}, {"fixtureId": "fx2"}]},
    )

    assert status == "BETRADAR_CONFLICT"
    assert row == {"fixtureId": "fx1"}


def test_oddspapi_crosscheck_sportradar_only_when_no_fixture():
    status, row = classify_oddspapi_crosscheck(
        event_id="sr:sport_event:123",
        oddspapi_by_betradar={},
    )

    assert status == "SPORTRADAR_ONLY"
    assert row is None


def test_player_binding_state_reports_existing_candidate_and_conflict():
    existing = {"sr:competitor:1": 10, "sr:competitor:2": 22}

    assert (
        player_binding_state(
            player_id=10,
            source_player_id="sr:competitor:1",
            existing_players=existing,
        )
        == "EXISTING"
    )
    assert (
        player_binding_state(
            player_id=11,
            source_player_id="sr:competitor:3",
            existing_players=existing,
        )
        == "CANDIDATE_NEW"
    )
    assert (
        player_binding_state(
            player_id=11,
            source_player_id="sr:competitor:2",
            existing_players=existing,
        )
        == "CONFLICT"
    )


def test_summarize_player_bindings_counts_unique_rows_and_conflicts():
    rows = [
        {
            "canonical_player_1_id": 1,
            "player_1_sportradar_id": "sr:competitor:1",
            "player_1_binding": "EXISTING",
            "canonical_player_2_id": 2,
            "player_2_sportradar_id": "sr:competitor:2",
            "player_2_binding": "CANDIDATE_NEW",
        },
        {
            "canonical_player_1_id": 1,
            "player_1_sportradar_id": "sr:competitor:1",
            "player_1_binding": "EXISTING",
            "canonical_player_2_id": 3,
            "player_2_sportradar_id": "sr:competitor:2",
            "player_2_binding": "CONFLICT",
        },
    ]

    summary = summarize_player_bindings(rows)

    assert summary["unique_exact_player_bindings"] == 3
    assert summary["existing_player_mappings"] == 1
    assert summary["candidate_new_player_mappings"] == 1
    assert summary["identity_conflicts"] == 1
    assert summary["ambiguous_player_bindings"] == 1


def test_delta_bucket_boundaries_are_stable():
    assert delta_bucket(0) == "0"
    assert delta_bucket(15) == "1-15"
    assert delta_bucket(30) == "16-30"
    assert delta_bucket(60) == "31-60"
    assert delta_bucket(120) == "61-120"
    assert delta_bucket(180) == "121-180"
    assert delta_bucket(181) == ">180"


def test_audit_module_has_no_sql_write_verbs():
    source = inspect.getsource(audit_module).upper()

    assert "INSERT " not in source
    assert "UPDATE " not in source
    assert "DELETE " not in source
