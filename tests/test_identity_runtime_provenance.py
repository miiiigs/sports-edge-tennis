"""Regression tests for migration 0025 runtime identity provenance."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import psycopg
import pytest

from sports_edge_tennis.data.identity_runtime import (
    CAPTURE_SOURCE_SHADOW,
    IdentityProvenanceError,
    IdentityResolutionObservation,
    load_identity_resolutions,
    observations_from_forward_capture,
    persist_identity_resolutions,
    read_capture_archive,
)
from tests.abstention_helpers import (
    fetchval,
    insert_match,
    insert_run,
    utc,
)

DIGEST = "a" * 64


def _fixture(conn: psycopg.Connection) -> tuple[int, int, int, int, int]:
    as_of = utc(hour=8)
    match_id = insert_match(conn, match_start_at=utc(hour=12))
    p1, p2 = conn.execute(
        "SELECT player_1_id, player_2_id FROM match_versions WHERE match_id = %s",
        (match_id,),
    ).fetchone()
    version_id = conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets, qualifying_flag,
            match_status, source_observed_at, as_of, ingested_at
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE,
                'SCHEDULED', %s, %s, %s)
        RETURNING match_version_id
        """,
        (match_id, p1, p2, utc(hour=12), utc(hour=7), utc(hour=7), utc(hour=7)),
    ).fetchone()[0]
    run_id = insert_run(conn, as_of=as_of)
    return run_id, match_id, int(version_id), int(p1), int(p2)


def _observation(
    *,
    run_id: int,
    match_id: int,
    version_id: int,
    original: int,
    model: int,
    predicted_at: datetime | None = None,
    method: str = "ALIAS",
    record_seed: str = "one",
) -> IdentityResolutionObservation:
    return IdentityResolutionObservation(
        prediction_run_id=run_id,
        match_id=match_id,
        match_version_id=version_id,
        predicted_at=predicted_at or utc(hour=8),
        original_player_id=original,
        model_player_id=model,
        method=method,
        evidence=f"tennis_data_alias_pit:{record_seed}",
        name_source="PIT_CANONICAL_NAME",
        candidate_count=1,
        matched_alias="player one",
        reference_policy="identity_reference_pit_v3",
        reference_sha256=DIGEST,
        capture_source=CAPTURE_SOURCE_SHADOW,
        source_record_sha256="b" * 64,
    )


def test_persist_is_idempotent_and_loads_exact_fields(db: psycopg.Connection):
    run_id, match_id, version_id, p1, p2 = _fixture(db)
    observations = [
        _observation(
            run_id=run_id,
            match_id=match_id,
            version_id=version_id,
            original=p1,
            model=p2,
        ),
        _observation(
            run_id=run_id,
            match_id=match_id,
            version_id=version_id,
            original=p2,
            model=p2,
            method="NO_HISTORICAL_CANDIDATE",
            record_seed="two",
        ),
    ]
    first = persist_identity_resolutions(db, observations)
    assert first == {"inserted": 2, "existing": 0, "total": 2}

    second = persist_identity_resolutions(db, observations)
    assert second == {"inserted": 0, "existing": 2, "total": 2}

    loaded = load_identity_resolutions(db, prediction_run_id=run_id)
    assert len(loaded) == 2
    assert all(row.verified for row in loaded)
    by_original = {row.observation.original_player_id: row for row in loaded}
    assert by_original[p1].observation.model_player_id == p2
    assert by_original[p1].observation.match_version_id == version_id
    assert by_original[p1].observation.predicted_at == utc(hour=8)
    assert by_original[p1].observation.source_record_sha256 == "b" * 64
    assert by_original[p1].observation.capture_source == CAPTURE_SOURCE_SHADOW

    count = fetchval(db, "SELECT count(*) FROM identity_resolution_provenance")
    assert count == 2


def test_conflicting_digest_fails_closed(db: psycopg.Connection):
    run_id, match_id, version_id, p1, p2 = _fixture(db)
    persist_identity_resolutions(
        db,
        [
            _observation(
                run_id=run_id,
                match_id=match_id,
                version_id=version_id,
                original=p1,
                model=p2,
            )
        ],
    )
    conflicting = _observation(
        run_id=run_id,
        match_id=match_id,
        version_id=version_id,
        original=p1,
        model=p1,
        method="NO_HISTORICAL_CANDIDATE",
    )
    with pytest.raises(IdentityProvenanceError, match="lineage conflict"):
        persist_identity_resolutions(db, [conflicting])


def test_match_version_later_than_observation_is_rejected(db: psycopg.Connection):
    run_id, match_id, version_id, p1, p2 = _fixture(db)
    with pytest.raises(IdentityProvenanceError, match="later than the resolution"):
        persist_identity_resolutions(
            db,
            [
                _observation(
                    run_id=run_id,
                    match_id=match_id,
                    version_id=version_id,
                    original=p1,
                    model=p2,
                    predicted_at=utc(hour=7),
                )
            ],
        )


def test_wrong_match_version_orientation_is_rejected(db: psycopg.Connection):
    run_id, match_id, _version_id, p1, p2 = _fixture(db)
    other_match = insert_match(db, match_start_at=utc(hour=13))
    other_version = fetchval(
        db,
        "SELECT match_version_id FROM match_versions WHERE match_id = %s",
        (other_match,),
    )
    with pytest.raises(IdentityProvenanceError, match="does not belong"):
        persist_identity_resolutions(
            db,
            [
                _observation(
                    run_id=run_id,
                    match_id=match_id,
                    version_id=int(other_version),
                    original=p1,
                    model=p2,
                )
            ],
        )


def _capture_record(*, match_version_id: int, live_1: int, live_2: int) -> dict:
    record = {
        "schema_version": "a1_identity_bridge_forward_shadow_record_v2",
        "record_type": "capture",
        "prediction_run_id": 77,
        "prediction_id": 88,
        "match_id": 99,
        "match_version_id": match_version_id,
        "prediction_as_of": "2026-11-25T09:01:09+00:00",
        "live_player_1_id": live_1,
        "live_player_2_id": live_2,
        "model_player_1_id": live_1,
        "model_player_2_id": live_2,
        "player_1_resolution": {
            "method": "NO_HISTORICAL_CANDIDATE",
            "evidence": "none",
            "name_source": "PIT_CANONICAL_NAME",
            "candidate_count": 0,
            "matched_alias": None,
            "bridge_event_id": None,
            "visible_from": None,
        },
        "player_2_resolution": {
            "method": "NO_HISTORICAL_CANDIDATE",
            "evidence": "none",
            "name_source": "PIT_CANONICAL_NAME",
            "candidate_count": 0,
            "matched_alias": None,
            "bridge_event_id": None,
            "visible_from": None,
        },
        "identity_reference": {
            "policy_version": "identity_reference_pit_v3",
            "reference_sha256": DIGEST,
        },
        "outcome_included": False,
        "production_changed": False,
        "can_promote": False,
    }
    import hashlib

    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    record["record_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return record


def test_forward_capture_records_become_two_observations(tmp_path: Path):
    record = _capture_record(match_version_id=5, live_1=101, live_2=202)
    path = tmp_path / "capture.jsonl"
    path.write_text(
        json.dumps(record, sort_keys=True) + "\n"
        + json.dumps({"record_type": "summary"}) + "\n",
        encoding="utf-8",
    )
    verified = read_capture_archive(path)
    assert len(verified) == 1
    observations = observations_from_forward_capture(verified)
    assert len(observations) == 2
    assert {o.original_player_id for o in observations} == {101, 202}
    assert all(o.capture_source == CAPTURE_SOURCE_SHADOW for o in observations)
    assert all(o.source_record_sha256 == record["record_sha256"] for o in observations)


def test_forward_capture_with_outcome_is_rejected():
    record = _capture_record(match_version_id=5, live_1=101, live_2=202)
    record["outcome_included"] = True
    with pytest.raises(IdentityProvenanceError, match="exclude outcomes"):
        observations_from_forward_capture([record])


def test_tampered_capture_archive_is_rejected(tmp_path: Path):
    record = _capture_record(match_version_id=5, live_1=101, live_2=202)
    record["model_player_1_id"] = 999
    path = tmp_path / "capture.jsonl"
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(IdentityProvenanceError, match="verification failed"):
        read_capture_archive(path)


def test_capture_integration_dry_run_then_apply(db: psycopg.Connection, tmp_path: Path):
    from sports_edge_tennis.validation.a1_identity_capture_integration import integrate

    run_id, match_id, version_id, p1, p2 = _fixture(db)
    record = _capture_record(match_version_id=version_id, live_1=p1, live_2=p2)
    record.update(
        {
            "prediction_run_id": run_id,
            "match_id": match_id,
            "prediction_id": 1,
            "prediction_as_of": utc(hour=8).isoformat(),
        }
    )
    import hashlib

    record.pop("record_sha256")
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    record["record_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    path = tmp_path / "capture.jsonl"
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

    dry = integrate(db, archive_path=str(path), apply=False)
    assert dry["observations"] == 2
    assert dry["persistence"]["inserted"] == 0
    assert fetchval(db, "SELECT count(*) FROM identity_resolution_provenance") == 0

    applied = integrate(db, archive_path=str(path), apply=True)
    assert applied["persistence"] == {
        "inserted": 2,
        "existing": 0,
        "total": 2,
        "applied": True,
    }
    replay = integrate(db, archive_path=str(path), apply=True)
    assert replay["persistence"]["existing"] == 2
    assert replay["persistence"]["inserted"] == 0
