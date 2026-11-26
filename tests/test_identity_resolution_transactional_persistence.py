"""Runtime identity-resolution provenance is atomic with prediction persistence."""
from __future__ import annotations

import pytest

from sports_edge_tennis.data.identity_runtime import (
    IdentityProvenanceError,
    IdentityResolutionObservation,
)
from sports_edge_tennis.data.prediction_persistence import (
    PredictiveAgentMatrixPrediction,
    persist_and_finalize_prediction_matrix,
)
from tests.abstention_helpers import (
    A1,
    A2,
    A3,
    insert_match,
    insert_run,
    insert_target,
    make_result,
    seed_agent_configs,
    utc,
)

ZERO_SHA = "0" * 64


def _setup(db):
    match_id = insert_match(db, ingested_at=utc(hour=9))
    match_version_id, p1, p2 = db.execute(
        "SELECT match_version_id, player_1_id, player_2_id FROM match_versions WHERE match_id=%s",
        (match_id,),
    ).fetchone()
    run_id = insert_run(db, as_of=utc(hour=9))
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
    rows = [
        PredictiveAgentMatrixPrediction(
            match_id=match_id,
            agent_config_version_id=configs[code],
            result=make_result(code, utc(hour=9), status="PREDICTED", side=1, probability="0.6"),
            match_version_id=match_version_id,
        )
        for code in (A1, A2, A3)
    ]
    return match_id, match_version_id, p1, p2, run_id, rows


def _observation(*, run_id, match_id, match_version_id, player_id):
    return IdentityResolutionObservation(
        prediction_run_id=run_id,
        match_id=match_id,
        match_version_id=match_version_id,
        predicted_at=utc(hour=9, minute=1),
        original_player_id=player_id,
        model_player_id=player_id,
        method="NO_HISTORICAL_CANDIDATE",
        evidence="none",
        name_source="NONE",
        reference_policy="strict",
        reference_sha256=ZERO_SHA,
        visible_from=utc(hour=9),
    )


def test_identity_rows_and_predictions_commit_together(db):
    match_id, match_version_id, p1, p2, run_id, rows = _setup(db)
    observations = [
        _observation(
            run_id=run_id,
            match_id=match_id,
            match_version_id=match_version_id,
            player_id=player,
        )
        for player in (p1, p2)
    ]

    persisted = persist_and_finalize_prediction_matrix(
        db,
        prediction_run_id=run_id,
        rows=rows,
        prediction_finalized_at=utc(hour=9, minute=5),
        ended_at=utc(hour=9, minute=5),
        identity_resolution_observations=observations,
    )
    assert len(persisted) == 3
    assert db.execute(
        "SELECT count(*) FROM identity_resolution_provenance WHERE prediction_run_id=%s",
        (run_id,),
    ).fetchone()[0] == 2
    assert db.execute(
        "SELECT run_status FROM prediction_runs WHERE prediction_run_id=%s", (run_id,)
    ).fetchone()[0] == "COMPLETED"


def test_bad_identity_observation_rolls_back_the_prediction_matrix(db):
    match_id, match_version_id, p1, _p2, run_id, rows = _setup(db)
    bad = _observation(
        run_id=run_id,
        match_id=match_id,
        match_version_id=match_version_id + 10_000_000,
        player_id=p1,
    )

    with pytest.raises(IdentityProvenanceError):
        persist_and_finalize_prediction_matrix(
            db,
            prediction_run_id=run_id,
            rows=rows,
            prediction_finalized_at=utc(hour=9, minute=5),
            ended_at=utc(hour=9, minute=5),
            identity_resolution_observations=[bad],
        )

    assert db.execute(
        "SELECT count(*) FROM predictions WHERE prediction_run_id=%s", (run_id,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT run_status FROM prediction_runs WHERE prediction_run_id=%s", (run_id,)
    ).fetchone()[0] == "RUNNING"
    assert db.execute(
        "SELECT count(*) FROM identity_resolution_provenance WHERE prediction_run_id=%s",
        (run_id,),
    ).fetchone()[0] == 0


def test_default_path_writes_no_identity_rows_and_stays_unchanged(db):
    _match_id, _mv, _p1, _p2, run_id, rows = _setup(db)
    persist_and_finalize_prediction_matrix(
        db,
        prediction_run_id=run_id,
        rows=rows,
        prediction_finalized_at=utc(hour=9, minute=5),
        ended_at=utc(hour=9, minute=5),
    )
    assert db.execute(
        "SELECT count(*) FROM identity_resolution_provenance WHERE prediction_run_id=%s",
        (run_id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM predictions WHERE prediction_run_id=%s", (run_id,)
    ).fetchone()[0] == 3
