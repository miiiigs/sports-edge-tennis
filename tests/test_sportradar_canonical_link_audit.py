import inspect

import sports_edge_tennis.validation.sportradar_canonical_link_audit as audit_module
from sports_edge_tennis.validation.sportradar_canonical_link_audit import (
    build_apply_preview_rows,
    build_triple_exact_summary,
    classify_betradar_check,
    classify_oddspapi_crosscheck,
    classify_orientation,
    classify_source_binding,
    delta_bucket,
    event_id_key,
    event_id_variants,
    player_binding_state,
    sha256_file,
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


def test_betradar_check_accepts_exact_and_prefix_normalized_matches():
    assert (
        classify_betradar_check(
            sportradar_event_id="sr:sport_event:123",
            oddspapi_betradar_id="sr:sport_event:123",
        )
        == "MATCH"
    )
    assert (
        classify_betradar_check(
            sportradar_event_id="sr:sport_event:123",
            oddspapi_betradar_id="123",
        )
        == "MATCH"
    )
    assert (
        classify_betradar_check(
            sportradar_event_id="sr:sport_event:123",
            oddspapi_betradar_id="456",
        )
        == "CONFLICT"
    )


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


def test_source_binding_reports_match_insert_existing_and_conflict():
    assert (
        classify_source_binding(
            canonical_id=1,
            source_id="sr:sport_event:10",
            existing_source_ids={},
            candidate_source_to_canonical_ids={"10": {1}},
        )
        == "INSERT"
    )
    assert (
        classify_source_binding(
            canonical_id=1,
            source_id="sr:sport_event:10",
            existing_source_ids={"10": 1},
        )
        == "EXISTING_SAME"
    )
    assert (
        classify_source_binding(
            canonical_id=1,
            source_id="sr:sport_event:10",
            existing_source_ids={"10": 2},
        )
        == "CONFLICT_OTHER_MATCH"
    )


def test_source_binding_reports_player_insert_existing_and_conflict():
    assert (
        classify_source_binding(
            canonical_id=1,
            source_id="sr:competitor:10",
            existing_source_ids={},
            candidate_source_to_canonical_ids={"sr:competitor:10": {1}},
        )
        == "INSERT"
    )
    assert (
        classify_source_binding(
            canonical_id=1,
            source_id="sr:competitor:10",
            existing_source_ids={"sr:competitor:10": 1},
        )
        == "EXISTING_SAME"
    )
    assert (
        classify_source_binding(
            canonical_id=1,
            source_id="sr:competitor:10",
            existing_source_ids={"sr:competitor:10": 2},
        )
        == "CONFLICT_OTHER_MATCH"
    )


def test_orientation_reports_direct_reverse_and_invalid():
    assert (
        classify_orientation(
            canonical_player_1_id=1,
            canonical_player_2_id=2,
            p1_source_id="sr:competitor:1",
            p2_source_id="sr:competitor:2",
            sport_event_player_1_id="sr:competitor:1",
            sport_event_player_2_id="sr:competitor:2",
        )
        == "DIRECT"
    )
    assert (
        classify_orientation(
            canonical_player_1_id=1,
            canonical_player_2_id=2,
            p1_source_id="sr:competitor:2",
            p2_source_id="sr:competitor:1",
            sport_event_player_1_id="sr:competitor:1",
            sport_event_player_2_id="sr:competitor:2",
        )
        == "REVERSED"
    )
    assert (
        classify_orientation(
            canonical_player_1_id=1,
            canonical_player_2_id=2,
            p1_source_id="sr:competitor:1",
            p2_source_id="sr:competitor:1",
            sport_event_player_1_id="sr:competitor:1",
            sport_event_player_2_id="sr:competitor:2",
        )
        == "INVALID"
    )


def _candidate_row(**overrides):
    row = {
        "canonical_match_id": 100,
        "canonical_player_1_id": 1,
        "canonical_player_1_name": "A Player",
        "canonical_player_2_id": 2,
        "canonical_player_2_name": "B Player",
        "sportradar_event_id": "sr:sport_event:900",
        "sportradar_player_1_id": "sr:competitor:11",
        "sportradar_player_1_name": "A Player",
        "sportradar_player_2_id": "sr:competitor:22",
        "sportradar_player_2_name": "B Player",
        "oddspapi_fixture_id": "fx-1",
        "oddspapi_betradar_id": "900",
        "start_delta_minutes": 4,
        "orientation": "DIRECT",
        "evidence_type": "TRIPLE_EXACT_BETRADAR",
        "p1_sportradar_id": "sr:competitor:11",
        "p2_sportradar_id": "sr:competitor:22",
    }
    row.update(overrides)
    return row


def test_apply_preview_reports_apply_and_idempotent_existing_second_run():
    candidate = _candidate_row()

    first = build_apply_preview_rows([candidate], existing_matches={}, existing_players={})
    assert first[0]["match_source_action"] == "INSERT"
    assert first[0]["p1_player_source_action"] == "INSERT"
    assert first[0]["p2_player_source_action"] == "INSERT"
    assert first[0]["final_action"] == "APPLY"

    second = build_apply_preview_rows(
        [candidate],
        existing_matches={"900": 100},
        existing_players={"sr:competitor:11": 1, "sr:competitor:22": 2},
    )
    assert second[0]["match_source_action"] == "EXISTING"
    assert second[0]["p1_player_source_action"] == "EXISTING"
    assert second[0]["p2_player_source_action"] == "EXISTING"
    assert second[0]["final_action"] == "EXISTING"


def test_apply_preview_rejects_match_player_betradar_and_orientation_conflicts():
    rows = [
        _candidate_row(canonical_match_id=100),
        _candidate_row(
            canonical_match_id=101,
            canonical_player_1_id=3,
            canonical_player_2_id=4,
        ),
        _candidate_row(
            canonical_match_id=102,
            sportradar_event_id="sr:sport_event:901",
            oddspapi_betradar_id="902",
        ),
        _candidate_row(
            canonical_match_id=103,
            sportradar_event_id="sr:sport_event:903",
            oddspapi_betradar_id="903",
            p2_sportradar_id="sr:competitor:11",
        ),
    ]

    preview = build_apply_preview_rows(rows, existing_matches={}, existing_players={})

    assert preview[0]["match_source_action"] == "DUPLICATE_PROVIDER_ID"
    assert preview[1]["p1_player_source_action"] == "DUPLICATE_PROVIDER_ID"
    assert preview[2]["betradar_check"] == "CONFLICT"
    assert preview[3]["orientation_check"] == "INVALID"
    assert all(row["final_action"].startswith("SKIP") for row in preview)


def test_artifact_hash_validation_is_stable(tmp_path):
    path = tmp_path / "candidates.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")

    assert sha256_file(path) == "ea14f99c47575613ab22111122c847728c61007f6bfd7b062d02fcb99df3feb0"


def test_preview_summary_reconciles_to_input_count():
    candidate = _candidate_row()
    preview = build_apply_preview_rows([candidate], existing_matches={}, existing_players={})

    summary = build_triple_exact_summary(
        read_only="on",
        broad_summary={
            "sportradar": {"daily_summary_calls": 12},
            "oddspapi": {"fixture_calls": 2},
        },
        candidate_rows=[candidate],
        preview_rows=preview,
        candidate_sha256="abc",
    )

    assert summary["database"]["writes"] == 0
    assert summary["triple_exact_input"] == 1
    assert summary["eligibility"]["reconciled_total"] == 1


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
    source = inspect.getsource(audit_module.audit_sportradar_canonical_links).upper()

    assert "INSERT " not in source
    assert "UPDATE " not in source
    assert "DELETE " not in source
    assert "CREATE " not in source
