from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

import sports_edge_tennis.validation.sportradar_stage1b_match_only as stage1b
from sports_edge_tennis.validation.sportradar_stage1b_match_only import (
    AUTH_ENV,
    AUTH_TOKEN,
    DEFAULT_ARTIFACT,
    ENABLED_ENV,
    HASH_ENV,
    EXPECTED_ARTIFACT_SHA256,
    EXPECTED_ROWS,
    apply_stage1b_match_only,
    load_candidates,
    validate_candidates,
)
from sports_edge_tennis.validation.sportradar_identity_apply import sha256_file


EXPECTED_MATCH_IDS = {
    117926,
    117984,
    130381,
    130471,
    130473,
    130477,
    130532,
    130533,
    130542,
    130545,
    130546,
    130547,
    130548,
    130550,
    130551,
    130558,
    130577,
    130579,
    130581,
    130582,
    130586,
    130587,
}


def test_stage1b_artifact_is_locked_and_exactly_22_rows():
    assert DEFAULT_ARTIFACT.exists()
    assert sha256_file(DEFAULT_ARTIFACT) == EXPECTED_ARTIFACT_SHA256
    rows = validate_candidates(load_candidates())
    assert len(rows) == EXPECTED_ROWS == 22
    assert {row.match_id for row in rows} == EXPECTED_MATCH_IDS


def test_stage1b_candidates_keep_strict_evidence_shape():
    rows = validate_candidates(load_candidates())
    assert all(row.evidence_type == "EXACT_SPORTRADAR_NAME_TIME" for row in rows)
    assert all(row.orientation == "DIRECT" for row in rows)
    assert all(row.candidate_count == 1 for row in rows)
    assert all(0 <= row.start_delta_minutes <= 30 for row in rows)


def test_stage1b_rejects_time_outlier():
    row = load_candidates()[0]
    with pytest.raises(ValueError, match="start delta outside"):
        validate_candidates([replace(row, start_delta_minutes=31)] * EXPECTED_ROWS)


def test_stage1b_apply_has_no_player_insert_path():
    source = inspect.getsource(stage1b.apply_stage1b_match_only)
    assert "INSERT INTO player_source_ids" not in source
    assert "player_inserted" in source


def test_stage1b_revalidation_requires_both_player_ids_preexisting(monkeypatch):
    row = load_candidates()[0]
    monkeypatch.setattr(stage1b, "_match_source_state", lambda *args, **kwargs: "INSERT")
    monkeypatch.setattr(stage1b, "_latest_match_version", lambda *args, **kwargs: 123)
    monkeypatch.setattr(
        stage1b,
        "_player_source_state",
        lambda _conn, *, player_id, source_id: (
            "INSERT" if player_id == row.player_2_id else "EXISTING"
        ),
    )
    with pytest.raises(RuntimeError, match="pre-existing player identities"):
        stage1b._revalidate(object(), [row])


def test_stage1b_apply_is_disabled_by_default():
    with pytest.raises(RuntimeError, match=ENABLED_ENV):
        apply_stage1b_match_only(
            dsn="postgresql://unused",
            env={ENABLED_ENV: "false", AUTH_ENV: "DISABLED"},
        )

@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("evidence_type", "FUZZY", "unexpected evidence"),
        ("orientation", "REVERSED", "unexpected orientation"),
        ("candidate_count", 2, "candidate_count changed"),
    ],
)
def test_stage1b_rejects_evidence_contract_changes(field, value, message):
    rows = list(load_candidates())
    rows[0] = replace(rows[0], **{field: value})
    with pytest.raises(ValueError, match=message):
        validate_candidates(rows)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", ""),
        ("player_1_source_id", ""),
        ("player_2_source_id", ""),
    ],
)
def test_stage1b_rejects_missing_provider_identity(field, value):
    rows = list(load_candidates())
    rows[0] = replace(rows[0], **{field: value})
    with pytest.raises(ValueError, match="missing provider identity"):
        validate_candidates(rows)


def test_stage1b_rejects_player_collisions():
    rows = list(load_candidates())
    rows[0] = replace(rows[0], player_2_id=rows[0].player_1_id)
    with pytest.raises(ValueError, match="canonical player collision"):
        validate_candidates(rows)

    rows = list(load_candidates())
    rows[0] = replace(
        rows[0],
        player_2_source_id=rows[0].player_1_source_id,
    )
    with pytest.raises(ValueError, match="provider player collision"):
        validate_candidates(rows)


def test_stage1b_rejects_match_to_multiple_event_mapping():
    rows = list(load_candidates())
    rows[1] = replace(rows[1], match_id=rows[0].match_id)
    with pytest.raises(ValueError, match="canonical match maps to multiple events"):
        validate_candidates(rows)


def test_stage1b_rejects_event_to_multiple_match_mapping():
    rows = list(load_candidates())
    rows[1] = replace(rows[1], event_id=rows[0].event_id)
    with pytest.raises(ValueError, match="Sportradar event maps to multiple matches"):
        validate_candidates(rows)


def test_stage1b_rejects_canonical_player_to_multiple_source_ids():
    rows = list(load_candidates())
    rows[1] = replace(rows[1], player_1_id=rows[0].player_1_id)
    with pytest.raises(ValueError, match="canonical player maps to multiple Sportradar IDs"):
        validate_candidates(rows)


def test_stage1b_rejects_source_id_to_multiple_canonical_players():
    rows = list(load_candidates())
    rows[1] = replace(
        rows[1],
        player_1_source_id=rows[0].player_1_source_id,
    )
    with pytest.raises(ValueError, match="Sportradar player maps to multiple canonical IDs"):
        validate_candidates(rows)


def test_stage1b_apply_requires_exact_authorization():
    with pytest.raises(RuntimeError, match=AUTH_ENV):
        apply_stage1b_match_only(
            dsn="postgresql://unused",
            env={
                ENABLED_ENV: "true",
                AUTH_ENV: "wrong",
                HASH_ENV: EXPECTED_ARTIFACT_SHA256,
            },
        )


def test_stage1b_apply_requires_reviewed_artifact_hash():
    with pytest.raises(RuntimeError, match=HASH_ENV):
        apply_stage1b_match_only(
            dsn="postgresql://unused",
            env={
                ENABLED_ENV: "true",
                AUTH_ENV: AUTH_TOKEN,
                HASH_ENV: "not-reviewed",
            },
        )

