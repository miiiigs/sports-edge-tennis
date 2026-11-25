"""Runtime wiring: prediction and Agent 1 feature snapshot commit atomically."""
from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest

from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG as A1_CONFIG,
    HistoricalMatchObservation,
    advance_replay_history,
    replay_history,
    resolved_player_states,
)
from sports_edge_tennis.agents.contracts import PredictiveAgentContractError
from sports_edge_tennis.data.feature_snapshots import (
    Agent1FeatureContext,
    build_agent1_feature_snapshot,
    persist_feature_snapshot,
)
from sports_edge_tennis.data.prediction_persistence import (
    PredictiveAgentMatrixPrediction,
    persist_and_finalize_prediction_matrix,
)
from tests.abstention_helpers import (
    A1,
    A2,
    A3,
    finalize_matrix,
    insert_match,
    insert_run,
    insert_target,
    make_result,
    seed_agent_configs,
    utc,
)


def _match_shape(db, match_id: int) -> tuple[int, int, int]:
    return db.execute(
        "SELECT match_version_id, player_1_id, player_2_id FROM match_versions "
        "WHERE match_id = %s",
        (match_id,),
    ).fetchone()


def _states(db, match_version_id: int):
    from sports_edge_tennis.agents.agent1_surface_elo import PlayerRatingState

    _, p1, p2 = db.execute(
        "SELECT match_version_id, player_1_id, player_2_id FROM match_versions "
        "WHERE match_version_id = %s",
        (match_version_id,),
    ).fetchone()

    def state(player_id: int, overall_count: int):
        return PlayerRatingState(
            player_id=player_id,
            overall_rating=Decimal("1512.5"),
            overall_match_count=overall_count,
            surface_ratings={"HARD": Decimal("1495.25")},
            surface_match_counts={"HARD": 4},
        )

    return state(p1, 12), state(p2, 9)


def _context(db, match_version_id: int, *, as_of=None, swap=False):
    p1, p2 = _states(db, match_version_id)
    if swap:
        p1, p2 = p2, p1
    return Agent1FeatureContext(
        player_1=p1,
        player_2=p2,
        surface="HARD",
        qualifying_flag=False,
        model_version="m",
        feature_version="f",
        as_of=as_of or utc(),
    )


def _setup(db):
    match_id = insert_match(db)
    match_version_id, _, _ = _match_shape(db, match_id)
    run_id = insert_run(db, as_of=utc())
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
    return match_id, match_version_id, run_id, configs


def _predicted(code: str):
    return make_result(code, utc(), status="PREDICTED", side=1, probability="0.6")


def _rows(match_id, match_version_id, configs, *, swap=False, db=None):
    return [
        PredictiveAgentMatrixPrediction(
            match_id=match_id,
            agent_config_version_id=configs[A1],
            result=_predicted(A1),
            match_version_id=match_version_id,
            agent1_features=_context(db, match_version_id, swap=swap),
        ),
        PredictiveAgentMatrixPrediction(
            match_id=match_id,
            agent_config_version_id=configs[A2],
            result=_predicted(A2),
            match_version_id=match_version_id,
        ),
        PredictiveAgentMatrixPrediction(
            match_id=match_id,
            agent_config_version_id=configs[A3],
            result=_predicted(A3),
            match_version_id=match_version_id,
        ),
    ]


def test_prediction_and_snapshot_commit_together_with_exact_lineage(db):
    match_id, match_version_id, run_id, configs = _setup(db)
    persisted = persist_and_finalize_prediction_matrix(
        db,
        prediction_run_id=run_id,
        rows=_rows(match_id, match_version_id, configs, db=db),
        prediction_finalized_at=utc(minute=5),
        ended_at=utc(minute=5),
    )
    assert len(persisted) == 3

    snapshot = db.execute(
        """
        SELECT s.prediction_id, s.prediction_run_id, s.match_id, s.match_version_id,
               s.agent_config_version_id, s.agent_code::text,
               s.player_1_id, s.player_2_id, s.surface::text,
               s.player_1_overall_rating, s.player_1_overall_match_count,
               s.rating_uncertainty, s.rating_uncertainty_source,
               s.identity_complete, s.surface_known, s.main_draw_verified,
               s.model_version, s.feature_version, s.as_of, s.prediction_locked_at,
               p.win_probability, p.model_version, p.feature_version,
               p.agent_config_version_id
        FROM prediction_feature_snapshots s
        JOIN predictions p ON p.prediction_id = s.prediction_id
        WHERE p.prediction_run_id = %s
        """,
        (run_id,),
    ).fetchall()
    assert len(snapshot) == 1
    row = snapshot[0]
    assert row[0] in persisted
    assert row[1] == run_id
    assert row[2] == match_id
    assert row[3] == match_version_id
    assert row[4] == configs[A1]
    assert row[5] == A1
    assert row[8] == "HARD"
    assert row[9] == Decimal("1512.5")
    assert row[10] == 12
    # Agent 1 never computed uncertainty: evidence stays NULL, not zero.
    assert row[11] is None and row[12] is None
    assert row[13] is True and row[14] is True and row[15] is True
    assert (row[16], row[17]) == ("m", "f")
    assert row[18] == utc()
    assert row[19] == utc(minute=5)
    # Original forecast parity: snapshot adds evidence, never rewrites the row.
    assert row[20] == Decimal("0.6")
    assert (row[21], row[22], row[23]) == ("m", "f", configs[A1])


def test_snapshot_failure_rolls_back_prediction_run_transaction(db):
    match_id, match_version_id, run_id, configs = _setup(db)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        persist_and_finalize_prediction_matrix(
            db,
            prediction_run_id=run_id,
            rows=_rows(match_id, match_version_id, configs, swap=True, db=db),
            prediction_finalized_at=utc(minute=5),
            ended_at=utc(minute=5),
        )

    assert db.execute("SELECT count(*) FROM predictions").fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM prediction_feature_snapshots"
    ).fetchone()[0] == 0
    run = db.execute(
        "SELECT run_status, prediction_finalized_at FROM prediction_runs "
        "WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()
    assert run == ("RUNNING", None)


def test_live_strict_mode_rejects_agent1_without_evidence(db):
    match_id, match_version_id, run_id, configs = _setup(db)
    rows = _rows(match_id, match_version_id, configs, db=db)
    rows[0] = PredictiveAgentMatrixPrediction(
        match_id=match_id,
        agent_config_version_id=configs[A1],
        result=_predicted(A1),
        match_version_id=match_version_id,
    )
    with pytest.raises(
        PredictiveAgentContractError, match="requires request-time feature evidence"
    ):
        persist_and_finalize_prediction_matrix(
            db,
            prediction_run_id=run_id,
            rows=rows,
            prediction_finalized_at=utc(minute=5),
            ended_at=utc(minute=5),
            require_agent1_features=True,
        )
    assert db.execute("SELECT count(*) FROM predictions").fetchone()[0] == 0


def test_features_are_rejected_for_non_agent1_rows(db):
    match_id, match_version_id, run_id, configs = _setup(db)
    row = PredictiveAgentMatrixPrediction(
        match_id=match_id,
        agent_config_version_id=configs[A2],
        result=_predicted(A2),
        match_version_id=match_version_id,
        agent1_features=_context(db, match_version_id),
    )
    with pytest.raises(PredictiveAgentContractError):
        persist_and_finalize_prediction_matrix(
            db,
            prediction_run_id=run_id,
            rows=[row],
            prediction_finalized_at=utc(minute=5),
            ended_at=utc(minute=5),
        )
    assert db.execute("SELECT count(*) FROM predictions").fetchone()[0] == 0


def test_snapshot_is_unique_per_prediction_and_append_only(db):
    match_id, match_version_id, run_id, configs = _setup(db)
    ids = finalize_matrix(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        results=[_predicted(A1), _predicted(A2), _predicted(A3)],
        finalized_at=utc(minute=5),
    )
    snapshot = build_agent1_feature_snapshot(
        prediction_id=ids[0],
        prediction_run_id=run_id,
        match_id=match_id,
        match_version_id=match_version_id,
        agent_config_version_id=configs[A1],
        player_1=_states(db, match_version_id)[0],
        player_2=_states(db, match_version_id)[1],
        surface="HARD",
        qualifying_flag=False,
        model_version="m",
        feature_version="f",
        as_of=utc(),
        prediction_locked_at=utc(minute=5),
    )
    persist_feature_snapshot(db, snapshot)
    with pytest.raises(psycopg.errors.UniqueViolation):
        persist_feature_snapshot(db, snapshot)
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("UPDATE prediction_feature_snapshots SET model_version = 'x'")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("DELETE FROM prediction_feature_snapshots")


def test_uncertainty_requires_named_source_at_database_level(db):
    match_id, match_version_id, run_id, configs = _setup(db)
    ids = finalize_matrix(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        results=[_predicted(A1), _predicted(A2), _predicted(A3)],
        finalized_at=utc(minute=5),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            """
            INSERT INTO prediction_feature_snapshots (
                prediction_id, prediction_run_id, match_id, match_version_id,
                agent_config_version_id, agent_code, player_1_id, player_2_id,
                identity_complete, surface_known, main_draw_verified,
                model_version, feature_version, as_of, prediction_locked_at,
                rating_uncertainty
            )
            SELECT %s, %s, %s, %s, %s, 'AGENT_1_SURFACE_ELO', player_1_id,
                   player_2_id, false, false, true, 'm', 'f', %s, %s, 0.1
            FROM match_versions WHERE match_version_id = %s
            """,
            (ids[0], run_id, match_id, match_version_id, configs[A1],
             utc(), utc(minute=5), match_version_id),
        )
    # A named source is accepted and stored verbatim next to the value.
    db.execute(
        """
        INSERT INTO prediction_feature_snapshots (
            prediction_id, prediction_run_id, match_id, match_version_id,
            agent_config_version_id, agent_code, player_1_id, player_2_id,
            identity_complete, surface_known, main_draw_verified,
            model_version, feature_version, as_of, prediction_locked_at,
            rating_uncertainty, rating_uncertainty_source
        )
        SELECT %s, %s, %s, %s, %s, 'AGENT_1_SURFACE_ELO', player_1_id,
               player_2_id, false, false, true, 'm', 'f', %s, %s, 0.1,
               'agent1_replay_standard_error_v1'
        FROM match_versions WHERE match_version_id = %s
        """,
        (ids[0], run_id, match_id, match_version_id, configs[A1],
         utc(), utc(minute=5), match_version_id),
    )
    stored = db.execute(
        "SELECT rating_uncertainty, rating_uncertainty_source "
        "FROM prediction_feature_snapshots WHERE prediction_id = %s",
        (ids[0],),
    ).fetchone()
    assert stored == (Decimal("0.1"), "agent1_replay_standard_error_v1")


def test_request_time_state_survives_a_later_replay_advance():
    from datetime import datetime, timedelta, timezone

    from sports_edge_tennis.agents.agent1_surface_elo import PredictionRequest

    base = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)

    def observation(match_id: int, offset_hours: int) -> HistoricalMatchObservation:
        observed = base + timedelta(hours=offset_hours)
        return HistoricalMatchObservation(
            match_id=match_id,
            player_1_id=101,
            player_2_id=202,
            surface="HARD",
            tour_scope="ATP_MAIN",
            competition_tier="ATP_250",
            best_of_sets=3,
            qualifying_flag=False,
            match_start_at=observed,
            outcome_status="COMPLETED",
            winner_side=1,
            source_observed_at=observed,
            as_of=observed,
        )

    request = PredictionRequest(
        player_1_id=101,
        player_2_id=202,
        surface="HARD",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        best_of_sets=3,
        qualifying_flag=False,
        as_of=base + timedelta(hours=10),
    )
    replay = replay_history([observation(1, 1)], request.as_of, A1_CONFIG)
    p1, p2 = resolved_player_states(request, replay, A1_CONFIG)
    captured = Agent1FeatureContext(
        player_1=p1,
        player_2=p2,
        surface="HARD",
        qualifying_flag=False,
        model_version="m",
        feature_version="f",
        as_of=request.as_of,
    )
    assert captured.player_1.overall_match_count == 1

    advanced = advance_replay_history(
        replay,
        [observation(2, 12)],
        previous_prediction_as_of=request.as_of,
        prediction_as_of=request.as_of + timedelta(hours=10),
        config=A1_CONFIG,
    )
    assert advanced.player_states[101].overall_match_count == 2
    # The captured context is frozen: it still describes the request cutoff.
    assert captured.player_1.overall_match_count == 1
    advanced_rating = advanced.player_states[101].overall_rating
    assert captured.player_1.overall_rating != advanced_rating
