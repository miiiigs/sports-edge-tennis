from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG,
    PredictionRequest,
    predict,
    replay_history,
)
from sports_edge_tennis.validation.agent1_walk_forward import (
    RunnerState,
    ValidationError,
    ValidationObservation,
    _calibration_table,
    _repository_sha,
    _record_to_validation_observation,
    _selected_confidence_table,
    _sha256_text,
    _stable_json_dumps,
    load_validation_observations,
    load_validation_observations_from_raw,
    build_prediction_target,
    normalize_observation_for_evaluation,
    predict_from_state,
    run_walk_forward_validation,
)


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def _observation(
    *,
    season_year: int,
    match_id: int,
    player_1_id: int,
    player_2_id: int,
    winner_side: int | None,
    visibility: datetime,
    surface: str | None = "HARD",
    tour_scope: str = "ATP_MAIN",
    competition_tier: str = "ATP_TOUR",
    qualifying_flag: bool = False,
    outcome_status: str = "COMPLETED",
) -> ValidationObservation:
    return ValidationObservation(
        season_year=season_year,
        match_id=match_id,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        surface=surface,
        tour_scope=tour_scope,
        competition_tier=competition_tier,
        best_of_sets=3,
        qualifying_flag=qualifying_flag,
        match_start_at=None,
        outcome_status=outcome_status,
        winner_side=winner_side,
        source_observed_at=visibility,
        as_of=visibility,
    )


def _state_from_observations(*observations: ValidationObservation) -> RunnerState:
    state = RunnerState.empty()
    for observation in observations:
        state.apply_revealed_observation(normalize_observation_for_evaluation(observation), DEFAULT_CONFIG)
    return state


def test_normalization_sorts_players_without_using_result_information():
    winner_first = normalize_observation_for_evaluation(
        _observation(
            season_year=2019,
            match_id=1,
            player_1_id=10,
            player_2_id=4,
            winner_side=1,
            visibility=_utc(2019, 1, 5, 23, 59, 59),
        )
    )
    winner_second = normalize_observation_for_evaluation(
        _observation(
            season_year=2019,
            match_id=2,
            player_1_id=10,
            player_2_id=4,
            winner_side=2,
            visibility=_utc(2019, 1, 6, 23, 59, 59),
        )
    )

    assert winner_first.player_1_id == 4
    assert winner_first.player_2_id == 10
    assert winner_first.winner_side == 2
    assert winner_first.side_swapped is True
    assert winner_second.player_1_id == 4
    assert winner_second.player_2_id == 10
    assert winner_second.winner_side == 1
    assert winner_second.side_swapped is True


def test_orientation_swap_is_probability_symmetric():
    state = _state_from_observations(
        _observation(
            season_year=2018,
            match_id=1,
            player_1_id=1,
            player_2_id=2,
            winner_side=1,
            visibility=_utc(2018, 1, 1, 23, 59, 59),
        ),
        _observation(
            season_year=2018,
            match_id=2,
            player_1_id=1,
            player_2_id=2,
            winner_side=1,
            visibility=_utc(2018, 1, 2, 23, 59, 59),
        ),
    )

    forward = predict_from_state(
        build_prediction_target(
            _observation(
                season_year=2019,
                match_id=3,
                player_1_id=1,
                player_2_id=2,
                winner_side=1,
                visibility=_utc(2019, 1, 1, 23, 59, 59),
            )
        ),
        state,
    )
    reverse = predict_from_state(
        build_prediction_target(
            _observation(
                season_year=2019,
                match_id=3,
                player_1_id=2,
                player_2_id=1,
                winner_side=2,
                visibility=_utc(2019, 1, 1, 23, 59, 59),
            )
        ),
        state,
    )

    assert reverse.player_1_probability == Decimal("1.000000") - forward.player_1_probability


def test_same_cohort_results_do_not_leak_between_predictions():
    base = [
        _observation(
            season_year=2018,
            match_id=1,
            player_1_id=1,
            player_2_id=2,
            winner_side=1,
            visibility=_utc(2018, 1, 1, 23, 59, 59),
        ),
        _observation(
            season_year=2019,
            match_id=2,
            player_1_id=1,
            player_2_id=3,
            winner_side=1,
            visibility=_utc(2019, 1, 1, 23, 59, 59),
        ),
        _observation(
            season_year=2019,
            match_id=3,
            player_1_id=1,
            player_2_id=4,
            winner_side=1,
            visibility=_utc(2019, 1, 1, 23, 59, 59),
        ),
    ]
    changed = list(base)
    changed[1] = _observation(
        season_year=2019,
        match_id=2,
        player_1_id=1,
        player_2_id=3,
        winner_side=2,
        visibility=_utc(2019, 1, 1, 23, 59, 59),
    )

    original = run_walk_forward_validation(base)["prediction_records"]
    altered = run_walk_forward_validation(changed)["prediction_records"]

    second_match_original = next(record for record in original if record.match_id == 3)
    second_match_altered = next(record for record in altered if record.match_id == 3)
    assert second_match_original.p1 == second_match_altered.p1


def test_target_label_does_not_change_prediction_probability():
    state = _state_from_observations(
        _observation(
            season_year=2018,
            match_id=1,
            player_1_id=1,
            player_2_id=2,
            winner_side=1,
            visibility=_utc(2018, 1, 1, 23, 59, 59),
        )
    )
    win_label = predict_from_state(
        build_prediction_target(
            _observation(
                season_year=2019,
                match_id=2,
                player_1_id=1,
                player_2_id=3,
                winner_side=1,
                visibility=_utc(2019, 1, 1, 23, 59, 59),
            )
        ),
        state,
    )
    lose_label = predict_from_state(
        build_prediction_target(
            _observation(
                season_year=2019,
                match_id=2,
                player_1_id=1,
                player_2_id=3,
                winner_side=2,
                visibility=_utc(2019, 1, 1, 23, 59, 59),
            )
        ),
        state,
    )

    assert win_label.player_1_probability == lose_label.player_1_probability


def test_warmup_is_not_scored_and_evaluation_uses_carried_state():
    report = run_walk_forward_validation(
        [
            _observation(
                season_year=2018,
                match_id=1,
                player_1_id=1,
                player_2_id=2,
                winner_side=1,
                visibility=_utc(2018, 1, 1, 23, 59, 59),
            ),
            _observation(
                season_year=2019,
                match_id=2,
                player_1_id=1,
                player_2_id=3,
                winner_side=1,
                visibility=_utc(2019, 1, 1, 23, 59, 59),
            ),
        ]
    )

    assert report["evaluation_population"]["scored_matches"] == 1
    record = report["prediction_records"][0]
    assert record.player_1_overall_prior_matches == 1
    assert record.p1 > Decimal("0.500000")


def test_non_completed_status_is_unscored_and_does_not_update_future_state():
    report = run_walk_forward_validation(
        [
            _observation(
                season_year=2018,
                match_id=1,
                player_1_id=1,
                player_2_id=2,
                winner_side=1,
                visibility=_utc(2018, 1, 1, 23, 59, 59),
            ),
            _observation(
                season_year=2019,
                match_id=2,
                player_1_id=1,
                player_2_id=3,
                winner_side=1,
                visibility=_utc(2019, 1, 1, 23, 59, 59),
                outcome_status="RETIRED",
            ),
            _observation(
                season_year=2019,
                match_id=3,
                player_1_id=1,
                player_2_id=4,
                winner_side=1,
                visibility=_utc(2019, 1, 2, 23, 59, 59),
            ),
        ]
    )

    retired_record = next(record for record in report["prediction_records"] if record.match_id == 2)
    future_record = next(record for record in report["prediction_records"] if record.match_id == 3)
    assert retired_record.scored is False
    assert future_record.player_1_overall_prior_matches == 1


def test_final_state_matches_replay_after_completed_cohorts():
    observations = [
        _observation(
            season_year=2018,
            match_id=1,
            player_1_id=1,
            player_2_id=2,
            winner_side=1,
            visibility=_utc(2018, 1, 1, 23, 59, 59),
        ),
        _observation(
            season_year=2019,
            match_id=2,
            player_1_id=2,
            player_2_id=3,
            winner_side=2,
            visibility=_utc(2019, 1, 1, 23, 59, 59),
        ),
        _observation(
            season_year=2019,
            match_id=3,
            player_1_id=1,
            player_2_id=3,
            winner_side=1,
            visibility=_utc(2019, 1, 2, 23, 59, 59),
        ),
    ]
    report = run_walk_forward_validation(observations)
    normalized_history = [
        normalize_observation_for_evaluation(item).to_historical_observation() for item in observations
    ]
    replay = replay_history(normalized_history, prediction_as_of=_utc(2030, 1, 1), config=DEFAULT_CONFIG)

    assert report["state"].player_states == replay.player_states


def test_metric_tables_respect_bin_edges_and_baseline():
    report = run_walk_forward_validation(
        [
            _observation(
                season_year=2018,
                match_id=1,
                player_1_id=1,
                player_2_id=2,
                winner_side=1,
                visibility=_utc(2018, 1, 1, 23, 59, 59),
            ),
            _observation(
                season_year=2019,
                match_id=2,
                player_1_id=1,
                player_2_id=3,
                winner_side=1,
                visibility=_utc(2019, 1, 1, 23, 59, 59),
            ),
            _observation(
                season_year=2019,
                match_id=3,
                player_1_id=2,
                player_2_id=3,
                winner_side=2,
                visibility=_utc(2019, 1, 2, 23, 59, 59),
            ),
        ]
    )

    assert report["baseline_metrics"]["log_loss"] == pytest.approx(0.69314718056)
    assert report["baseline_metrics"]["brier"] == pytest.approx(0.25)
    assert report["overall_metrics"]["n"] == 2
    calibration_rows = report["calibration_rows"]
    assert calibration_rows[-1]["bin"] == "[0.9,1.0]"
    assert report["improvement"]["log_loss_absolute_improvement"] is not None


def test_history_depth_bands_use_pre_match_counts():
    report = run_walk_forward_validation(
        [
            _observation(
                season_year=2018,
                match_id=1,
                player_1_id=1,
                player_2_id=2,
                winner_side=1,
                visibility=_utc(2018, 1, 1, 23, 59, 59),
            ),
            _observation(
                season_year=2018,
                match_id=2,
                player_1_id=1,
                player_2_id=3,
                winner_side=1,
                visibility=_utc(2018, 1, 2, 23, 59, 59),
            ),
            _observation(
                season_year=2019,
                match_id=3,
                player_1_id=1,
                player_2_id=4,
                winner_side=1,
                visibility=_utc(2019, 1, 1, 23, 59, 59),
            ),
        ]
    )

    record = report["prediction_records"][0]
    assert record.player_1_overall_prior_matches == 2
    assert record.player_2_overall_prior_matches == 0
    assert record.overall_history_band == "0"


def test_duplicate_evaluation_match_ids_raise():
    with pytest.raises(ValidationError, match="duplicate evaluation match ids"):
        run_walk_forward_validation(
            [
                _observation(
                    season_year=2019,
                    match_id=1,
                    player_1_id=1,
                    player_2_id=2,
                    winner_side=1,
                    visibility=_utc(2019, 1, 1, 23, 59, 59),
                ),
                _observation(
                    season_year=2019,
                    match_id=1,
                    player_1_id=1,
                    player_2_id=2,
                    winner_side=2,
                    visibility=_utc(2019, 1, 2, 23, 59, 59),
                ),
            ]
        )


def test_prediction_runner_matches_public_predict_on_same_state():
    history = [
        _observation(
            season_year=2018,
            match_id=1,
            player_1_id=1,
            player_2_id=2,
            winner_side=1,
            visibility=_utc(2018, 1, 1, 23, 59, 59),
        ),
        _observation(
            season_year=2018,
            match_id=2,
            player_1_id=1,
            player_2_id=2,
            winner_side=1,
            visibility=_utc(2018, 1, 2, 23, 59, 59),
        ),
    ]
    normalized_history = [
        normalize_observation_for_evaluation(item).to_historical_observation() for item in history
    ]
    state = _state_from_observations(*history)
    target = build_prediction_target(
        _observation(
            season_year=2019,
            match_id=3,
            player_1_id=1,
            player_2_id=3,
            winner_side=1,
            visibility=_utc(2019, 1, 1, 23, 59, 59),
        )
    )

    public_prediction = predict(target.to_request(), history=normalized_history)
    runner_prediction = predict_from_state(target, state)

    assert runner_prediction.player_1_probability == public_prediction.player_1_probability
    assert runner_prediction.player_2_probability == public_prediction.player_2_probability


def test_private_tables_handle_upper_edges():
    base_record = run_walk_forward_validation(
        [
            _observation(
                season_year=2019,
                match_id=1,
                player_1_id=1,
                player_2_id=2,
                winner_side=1,
                visibility=_utc(2019, 1, 1, 23, 59, 59),
            )
        ]
    )["prediction_records"][0]
    record = base_record.__class__(**{**base_record.__dict__, "p1": Decimal("1.000000"), "selected_probability": Decimal("1.000000"), "y": 1, "selected_side_won": True, "scored": True})

    calibration_rows, ece, _ = _calibration_table([record])
    confidence_rows = _selected_confidence_table([record])

    assert calibration_rows[-1]["n"] == 1
    assert confidence_rows[-1]["n"] == 1
    assert ece == 0.0


def test_deterministic_run_produces_identical_headline_hash():
    observations = [
        _observation(
            season_year=2018,
            match_id=1,
            player_1_id=1,
            player_2_id=2,
            winner_side=1,
            visibility=_utc(2018, 1, 1, 23, 59, 59),
        ),
        _observation(
            season_year=2019,
            match_id=2,
            player_1_id=1,
            player_2_id=3,
            winner_side=1,
            visibility=_utc(2019, 1, 1, 23, 59, 59),
        ),
        _observation(
            season_year=2019,
            match_id=3,
            player_1_id=2,
            player_2_id=3,
            winner_side=2,
            visibility=_utc(2019, 1, 2, 23, 59, 59),
        ),
    ]

    first = run_walk_forward_validation(observations)
    second = run_walk_forward_validation(observations)
    first_hash = _sha256_text(
        _stable_json_dumps(
            {
                "overall_metrics": first["overall_metrics"],
                "calibration": first["calibration_rows"],
                "runtime_hash": first["runtime"]["canonical_artifact_hash"],
            }
        )
    )
    second_hash = _sha256_text(
        _stable_json_dumps(
            {
                "overall_metrics": second["overall_metrics"],
                "calibration": second["calibration_rows"],
                "runtime_hash": second["runtime"]["canonical_artifact_hash"],
            }
        )
    )

    assert first_hash == second_hash


def test_load_validation_observations_uses_select_only_query():
    class FakeResult:
        def fetchall(self) -> list[tuple[object, ...]]:
            return []

    class FakeConnection:
        def __init__(self) -> None:
            self.sql: str | None = None

        def execute(self, sql: str, params: dict[str, int]) -> FakeResult:
            self.sql = sql
            assert params == {"year_start": 2016, "year_end": 2025}
            assert "INSERT" not in sql.upper()
            assert "UPDATE " not in sql.upper()
            assert "DELETE" not in sql.upper()
            assert sql.lstrip().startswith("WITH")
            return FakeResult()

    conn = FakeConnection()

    assert load_validation_observations(conn) == []
    assert conn.sql is not None


def test_record_to_validation_observation_uses_outcome_independent_side_sort():
    record = type(
        "Record",
        (),
        {
            "season_year": 2019,
            "winner": type("PlayerRef", (), {"source_player_id": "20"})(),
            "loser": type("PlayerRef", (), {"source_player_id": "10"})(),
            "surface": "HARD",
            "tour_scope": "ATP_MAIN",
            "competition_tier": "ATP_TOUR",
            "best_of_sets": 3,
            "qualifying_flag": False,
            "match_start_at": None,
            "outcome_status": "COMPLETED",
            "result_source_observed_at": _utc(2019, 1, 1, 23, 59, 59),
            "result_as_of": _utc(2019, 1, 1, 23, 59, 59),
        },
    )()

    observation = _record_to_validation_observation(record, match_id=7)

    assert observation.player_1_id == 10
    assert observation.player_2_id == 20
    assert observation.winner_side == 2


def test_load_validation_observations_from_raw_uses_parser_records(monkeypatch: pytest.MonkeyPatch):
    document = type("Document", (), {"filename": "atp_matches_2019.csv"})()
    record = type(
        "Record",
        (),
        {
            "source_record_hash": "abc123",
            "season_year": 2019,
            "winner": type("PlayerRef", (), {"source_player_id": "30"})(),
            "loser": type("PlayerRef", (), {"source_player_id": "10"})(),
            "surface": "CLAY",
            "tour_scope": "ATP_MAIN",
            "competition_tier": "ATP_TOUR",
            "best_of_sets": 5,
            "qualifying_flag": True,
            "match_start_at": None,
            "outcome_status": "COMPLETED",
            "result_source_observed_at": _utc(2019, 5, 5, 23, 59, 59),
            "result_as_of": _utc(2019, 5, 5, 23, 59, 59),
        },
    )()
    outcome = type("Outcome", (), {"records": [record]})()

    monkeypatch.setattr(
        "sports_edge_tennis.validation.agent1_walk_forward.build_local_sackmann_documents",
        lambda years, raw_root, source_revision: [document],
    )
    monkeypatch.setattr(
        "sports_edge_tennis.validation.agent1_walk_forward.parse_sackmann_csv",
        lambda doc: outcome,
    )

    observations = load_validation_observations_from_raw(
        raw_root=Path("data/raw/sackmann"),
        source_revision="rev123",
        year_start=2019,
        year_end=2019,
    )

    assert len(observations) == 1
    assert observations[0].match_id == 1
    assert observations[0].player_1_id == 10
    assert observations[0].player_2_id == 30
    assert observations[0].winner_side == 2


def test_repository_sha_reads_worktree_ref_from_common_packed_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    worktree = tmp_path / "worktree"
    admin_git_dir = tmp_path / "repo" / ".git" / "worktrees" / "validation"
    common_git_dir = tmp_path / "repo" / ".git"
    worktree.mkdir(parents=True)
    admin_git_dir.mkdir(parents=True)
    common_git_dir.mkdir(parents=True, exist_ok=True)
    sha = "91122c9d047e9d0a251f27521e62d54dcc8f1b55"

    (worktree / ".git").write_text(f"gitdir: {admin_git_dir.as_posix()}\n", encoding="utf-8")
    (admin_git_dir / "HEAD").write_text(
        "ref: refs/heads/feat/agent1-walk-forward-validation\n",
        encoding="utf-8",
    )
    (admin_git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    (common_git_dir / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{sha} refs/heads/feat/agent1-walk-forward-validation\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(worktree)

    assert _repository_sha() == sha
