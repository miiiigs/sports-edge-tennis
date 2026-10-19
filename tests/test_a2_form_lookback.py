from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from research.a2_form_lookback import harness
from research.a2_form_lookback.harness import (
    FrozenMatch,
    _history_bucket,
    _inactivity_bucket,
    _point_metrics,
    active_group_keys,
    build_groups,
    eligible_matches,
    load_dataset,
    run_variant,
)


def _raw_match(
    match_id: int,
    day: int,
    *,
    season: int = 2025,
    surface: str | None = "HARD",
    tournament_id: int | None = None,
    player_1: int = 1,
    player_2: int = 2,
) -> dict:
    start = datetime(2025, 1, 1, 12, tzinfo=UTC) + timedelta(days=day)
    observed = start + timedelta(hours=3)
    tournament = tournament_id if tournament_id is not None else 1000 + match_id
    return {
        "match_id": match_id,
        "season_year": season,
        "tournament_id": tournament,
        "canonical_tournament_name": "Test Challenger",
        "edition_start_date": start.date().isoformat(),
        "edition_end_date": (start.date() + timedelta(days=6)).isoformat(),
        "outcome_status": "COMPLETED",
        "winner_side": 1,
        "result_source_observed_at": observed.isoformat(),
        "result_as_of": observed.isoformat(),
        "match_version_id": match_id,
        "player_1_id": player_1,
        "player_2_id": player_2,
        "match_start_at": None,
        "tour_scope": "CHALLENGER",
        "competition_tier": "CHALLENGER",
        "surface": surface,
        "round_code": "R32",
        "best_of_sets": 3,
        "qualifying_flag": False,
        "version_source_observed_at": observed.isoformat(),
        "version_as_of": observed.isoformat(),
        "stat_source_system": "TEST",
        "stat_source_observed_at": observed.isoformat(),
        "stat_as_of": observed.isoformat(),
        "stats": [
            {
                "player_id": player_1,
                "first_serve_points_won": 40,
                "second_serve_points_won": 15,
                "service_points_played": 80,
                "return_points_won": 30,
            },
            {
                "player_id": player_2,
                "first_serve_points_won": 34,
                "second_serve_points_won": 14,
                "service_points_played": 80,
                "return_points_won": 25,
            },
        ],
    }


def _write_dataset(path: Path, raw_records: list[dict]) -> None:
    digest = __import__("hashlib").sha256()
    for raw in raw_records:
        digest.update(harness._canonical(raw).encode("utf-8"))
        digest.update(b"\n")
    manifest = {
        "_manifest": {
            "dataset_schema_version": "test",
            "dataset_sha256": digest.hexdigest(),
            "counts": {"rows": len(raw_records)},
        }
    }
    with path.open("w", encoding="utf-8") as handle:
        for raw in raw_records:
            handle.write(harness._canonical(raw) + "\n")
        handle.write(harness._canonical(manifest) + "\n")


def _eligible(raw: dict):
    match = harness._is_eligible(FrozenMatch.from_dict(raw))
    assert match is not None
    return match


def test_eligibility_and_visibility():
    match = _eligible(_raw_match(1, 0))
    assert match.frozen.split == "test"
    assert match.frozen.history_visible_at == match.frozen.result_source_observed_at
    assert harness._is_eligible(FrozenMatch.from_dict(_raw_match(2, 0, surface=None))) is None
    broken = _raw_match(3, 0)
    broken["stats"] = broken["stats"][:1]
    assert harness._is_eligible(FrozenMatch.from_dict(broken)) is None
    unsupported = _raw_match(4, 0)
    unsupported["tour_scope"] = "WTA"
    assert harness._is_eligible(FrozenMatch.from_dict(unsupported)) is None


def test_group_cohort_is_latest_visibility():
    first = _eligible(_raw_match(1, 0, tournament_id=10))
    second_raw = _raw_match(2, 0, tournament_id=10)
    second_raw["result_source_observed_at"] = (
        datetime(2025, 1, 2, 12, tzinfo=UTC).isoformat()
    )
    second_raw["result_as_of"] = second_raw["result_source_observed_at"]
    second_raw["version_source_observed_at"] = second_raw["result_source_observed_at"]
    second_raw["version_as_of"] = second_raw["result_source_observed_at"]
    second_raw["stat_source_observed_at"] = second_raw["result_source_observed_at"]
    second_raw["stat_as_of"] = second_raw["result_source_observed_at"]
    second = _eligible(second_raw)
    groups = build_groups([first, second])
    assert len(groups) == 1
    assert groups[0].cohort == datetime(2025, 1, 2, 12, tzinfo=UTC)


def test_active_group_keys_visibility_window():
    groups = [
        harness.TournamentGroup(
            key=(index, 2025),
            matches=[],
            cohort=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=index * 200),
            season_year=2025,
            match_date=date(2025, 1, 1),
        )
        for index in range(4)
    ]
    # current index 3 = day 600, 365-day cutoff = day 235 -> only day 400.
    assert active_group_keys(groups, 3, 365) == [(2, 2025)]
    assert active_group_keys(groups, 3, None) == [(0, 2025), (1, 2025), (2, 2025)]


def test_add_remove_inverse_for_same_players():
    first = _eligible(_raw_match(1, 0, tournament_id=10))
    second = _eligible(_raw_match(2, 1, tournament_id=11))
    state = harness._VariantState(window_days=365)
    state.activate(harness.TournamentGroup((10, 2025), [first], first.frozen.history_visible_at, 2025, first.frozen.match_date))
    state.activate(harness.TournamentGroup((11, 2025), [second], second.frozen.history_visible_at, 2025, second.frozen.match_date))
    harness._remove_historical_match_in_place(state.players, state.population, first.observation)
    state.active_matches.discard(first.frozen.match_id)

    fresh = harness._VariantState(window_days=365)
    fresh.activate(harness.TournamentGroup((11, 2025), [second], second.frozen.history_visible_at, 2025, second.frozen.match_date))
    assert harness.snapshot_history_state(fresh.players, fresh.population) == harness.snapshot_history_state(
        state.players, state.population
    )


def test_known_metrics_and_buckets():
    records = [
        {
            "match_id": 1,
            "predicted": True,
            "probability": 0.70,
            "y": 1,
        },
        {
            "match_id": 2,
            "predicted": True,
            "probability": 0.80,
            "y": 0,
        },
    ]
    metrics = _point_metrics(records)
    assert metrics["eligible"] == 2
    assert metrics["predicted"] == 2
    assert metrics["accuracy"] == pytest.approx(0.5)
    assert metrics["brier"] == pytest.approx(0.365)
    assert metrics["ece"] == pytest.approx(0.55)
    assert metrics["coverage"] == pytest.approx(1.0)
    assert _history_bucket(5) == "5_9"
    assert _history_bucket(4) == "insufficient_lt5"
    assert _inactivity_bucket(180) == "return_ge_180d"
    assert _inactivity_bucket(179) == "active_lt_180d"
    assert _inactivity_bucket(None) == "no_prior_history"


@pytest.mark.skipif(harness.np is None, reason="numpy research dependency not installed")
def test_paired_bootstrap_is_deterministic():
    baseline = [
        {"match_id": 1, "predicted": True, "probability": 0.7, "y": 1},
        {"match_id": 2, "predicted": True, "probability": 0.8, "y": 0},
    ]
    variant = [
        {"match_id": 1, "predicted": True, "probability": 0.6, "y": 1},
        {"match_id": 2, "predicted": True, "probability": 0.9, "y": 0},
    ]
    first = harness._paired_bootstrap(baseline, variant, reps=50, seed=7)
    second = harness._paired_bootstrap(baseline, variant, reps=50, seed=7)
    assert first == second
    assert first["n_common"] == 2


def test_end_to_end_window_can_abstain_where_baseline_predicts(tmp_path):
    raw_records = [_raw_match(index + 1, index) for index in range(7)]
    dataset = tmp_path / "frozen.jsonl"
    _write_dataset(dataset, raw_records)
    records, manifest = load_dataset(dataset)
    assert manifest["counts"]["rows"] == 7
    assert len(eligible_matches(records)) == 7

    baseline_path = tmp_path / "baseline.jsonl"
    window_path = tmp_path / "window.jsonl"
    run_variant(dataset, variant="baseline", window_days=None, output_path=baseline_path)
    run_variant(dataset, variant="2", window_days=2, output_path=window_path)
    baseline = [json.loads(line) for line in baseline_path.read_text(encoding="utf-8").splitlines()]
    window = [json.loads(line) for line in window_path.read_text(encoding="utf-8").splitlines()]
    assert baseline[-1]["predicted"] is True
    assert window[-1]["predicted"] is False
