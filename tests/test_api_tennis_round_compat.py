from datetime import UTC, datetime

from sports_edge_tennis.data.sources.api_tennis_live import parse_api_tennis_fixtures
from sports_edge_tennis.live.api_tennis_fixture_compat import normalize_api_tennis_fixture_rounds


def _fixture(**overrides):
    payload = {
        "event_key": "900001",
        "tournament_key": "4901",
        "tournament_name": "ATP Challenger Porto",
        "event_type_type": "Atp Challenger Men Singles",
        "event_date": "2026-09-10",
        "event_time": "14:30",
        "event_status": "Not Started",
        "event_first_player": "Player One",
        "event_second_player": "Player Two",
        "first_player_key": "101",
        "second_player_key": "202",
        "event_surface": "Clay",
    }
    payload.update(overrides)
    return payload


def test_documented_tournament_round_suffixes_map_to_model_vocabulary():
    fixtures = [
        _fixture(tournament_round="ATP Example - Final"),
        _fixture(tournament_round="ATP Example - Semi-finals"),
        _fixture(tournament_round="ATP Example - Quarter-finals"),
        _fixture(tournament_round="ATP Example - 1/8-finals"),
        _fixture(tournament_round="ATP Example - 1/16-finals"),
        _fixture(tournament_round="ATP Example - 1/32-finals"),
        _fixture(tournament_round="ATP Example - 1/64-finals"),
    ]

    normalized = normalize_api_tennis_fixture_rounds(fixtures)

    assert [row.get("round_code") for row in normalized] == [
        "F",
        "SF",
        "QF",
        "R16",
        "R32",
        "R64",
        "R128",
    ]


def test_existing_round_field_wins_over_tournament_round():
    [normalized] = normalize_api_tennis_fixture_rounds(
        [_fixture(event_round="SF", tournament_round="ATP Example - Quarter-finals")]
    )

    assert normalized["event_round"] == "SF"
    assert "round_code" not in normalized


def test_unknown_tournament_round_fails_closed():
    [normalized] = normalize_api_tennis_fixture_rounds(
        [_fixture(tournament_round="ATP Example - Qualification round four")]
    )

    assert "round_code" not in normalized


def test_event_qualification_maps_only_when_canonical_flag_is_missing():
    normalized = normalize_api_tennis_fixture_rounds(
        [
            _fixture(event_qualification="True"),
            _fixture(event_qualification="0"),
            _fixture(event_qualification="True", qualifying_flag=False),
        ]
    )

    assert normalized[0]["qualifying_flag"] is True
    assert normalized[1]["qualifying_flag"] is False
    assert normalized[2]["qualifying_flag"] is False


def test_normalized_provider_fields_reach_source_a_record():
    [normalized] = normalize_api_tennis_fixture_rounds(
        [
            _fixture(
                tournament_round="ATP Challenger Porto - Quarter-finals",
                event_qualification="False",
            )
        ]
    )
    records, rejected = parse_api_tennis_fixtures(
        [normalized],
        received_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    )

    assert rejected == []
    assert len(records) == 1
    assert records[0].round_code == "QF"
    assert records[0].qualifying_flag is False


def test_qualifying_bracket_maps_to_q1_and_q2_not_sf_f():
    normalized = normalize_api_tennis_fixture_rounds(
        [
            _fixture(
                event_key="q-first",
                tournament_round="Shanghai - Semi-finals",
                event_qualification="True",
            ),
            _fixture(
                event_key="q-final",
                tournament_round="Shanghai - Final",
                event_qualification="True",
            ),
        ]
    )

    assert [row["round_code"] for row in normalized] == ["Q1", "Q2"]
    assert all(row["qualifying_flag"] is True for row in normalized)


def test_qualifying_explicit_q_stage_is_preserved():
    [normalized] = normalize_api_tennis_fixture_rounds(
        [_fixture(tournament_round="Shanghai - Q3", event_qualification="True")]
    )

    assert normalized["round_code"] == "Q3"


def test_main_draw_rounds_unchanged_by_qualifying_logic():
    normalized = normalize_api_tennis_fixture_rounds(
        [
            _fixture(
                event_key="m-first",
                tournament_round="ATP Example - Semi-finals",
                event_qualification="False",
            ),
            _fixture(
                event_key="m-final",
                tournament_round="ATP Example - Final",
                event_qualification="False",
            ),
        ]
    )

    assert [row["round_code"] for row in normalized] == ["SF", "F"]


def test_unknown_qualifying_stage_fails_closed():
    [normalized] = normalize_api_tennis_fixture_rounds(
        [
            _fixture(
                tournament_round="Shanghai - Mystery stage",
                event_qualification="True",
            )
        ]
    )

    assert "round_code" not in normalized
