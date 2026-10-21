from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "research" / "tennis_model_error_forensics_20261021"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, MODULE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


analyze = _load("analyze")
extract = _load("extract_live_picks")


def _row(probability: float, won: bool, **extra):
    row = {
        "population": "manager_consensus",
        "consensus_probability": probability,
        "won": won,
        "scored": True,
        "void": False,
        "outcome_status": "COMPLETED",
        "selected_side": 1,
        "abstaining_agent_count": 1,
    }
    row.update(extra)
    return row


def test_band_boundaries():
    assert analyze.band(0.5999) == "<60"
    assert analyze.band(0.60) == "60-69"
    assert analyze.band(0.6999) == "60-69"
    assert analyze.band(0.70) == "70-79"
    assert analyze.band(0.7999) == "70-79"
    assert analyze.band(0.80) == "80-89"
    assert analyze.band(0.8999) == "80-89"
    assert analyze.band(0.90) == "90+"


def test_edge_bucket_uses_fraction_not_percent():
    assert analyze.edge_bucket(0.1158) == "10pp+"
    assert analyze.edge_bucket(0.03) == "2-5pp"
    assert analyze.edge_bucket(0.001) == "0-2pp"
    assert analyze.edge_bucket(-0.01) == "negative"
    assert analyze.edge_bucket(None) == "unknown"


def test_wilson_interval_contains_observed_rate():
    low, high = analyze.wilson_interval(28, 41)
    assert low < 28 / 41 < high
    assert 0 <= low <= high <= 1


def test_brier_and_log_loss_perfect_predictions():
    rows = [_row(0.99, True), _row(0.99, True)]
    assert analyze.brier_score(rows) == (0.99 - 1) ** 2
    assert analyze.log_loss(rows) < 0.02


def test_ece_is_zero_for_perfectly_calibrated_constant_bin():
    rows = [_row(0.75, True), _row(0.75, True), _row(0.75, True), _row(0.75, False)]
    assert analyze.ece(rows) == 0.0


def test_scored_rows_excludes_unsettled_and_void():
    rows = [
        _row(0.7, True),
        _row(0.7, False, scored=False, outcome_status=None),
        _row(0.7, False, scored=False, outcome_status="RETIRED", void=True),
    ]
    assert len(analyze.scored_rows(rows)) == 1


def test_calibration_gap_and_p_value_direction():
    rows = [_row(0.8425, False) for _ in range(13)] + [_row(0.8425, True) for _ in range(28)]
    summary = analyze.summarize(rows, bootstrap=False)
    assert summary["scored"] == 41
    assert summary["wins"] == 28
    assert summary["calibration_gap"] < 0
    assert summary["calibration_p_value"] < 0.01


def test_history_and_inactivity_buckets():
    assert analyze.history_bucket(0) == "<5"
    assert analyze.history_bucket(5) == "5-9"
    assert analyze.history_bucket(10) == "10-19"
    assert analyze.history_bucket(20) == "20-49"
    assert analyze.history_bucket(50) == "50+"
    assert analyze.inactivity_bucket(0) == "0-7"
    assert analyze.inactivity_bucket(8) == "8-30"
    assert analyze.inactivity_bucket(31) == "31-90"
    assert analyze.inactivity_bucket(91) == "91-180"
    assert analyze.inactivity_bucket(181) == ">180"
    assert analyze.inactivity_bucket(None) == "unknown"


def test_market_agreement_requires_pre_pick_sharp():
    assert analyze.market_agreement({"selected_side": 1, "market": {}}) == "no_pre_pick_sharp"
    assert (
        analyze.market_agreement(
            {"selected_side": 1, "market": {"sharp_favourite_side": 1}}
        )
        == "model_picked_market_favourite"
    )
    assert (
        analyze.market_agreement(
            {"selected_side": 2, "market": {"sharp_favourite_side": 1}}
        )
        == "model_picked_market_underdog"
    )


def test_cause_flags_do_not_fire_on_clean_row():
    row = {
        "consensus_probability": 0.7,
        "selected_side": 1,
        "abstaining_agent_count": 0,
        "agents": {"AGENT_3_INTRANSITIVITY": {"status": "PREDICTED"}},
        "history": {
            "picked": {
                "prior_matches": 30,
                "prior_stat_matches": 20,
                "days_since_last_match": 3,
                "prior_surface_matches": 10,
                "prior_surface_win_rate": 0.6,
            },
            "opponent": {
                "prior_matches": 30,
                "prior_10_wins": 4,
                "prior_surface_matches": 10,
                "prior_surface_win_rate": 0.5,
            },
        },
        "market": {"model_minus_sharp": 0.02, "sharp_favourite_side": 1},
        "data_quality": {
            "surface_missing": False,
            "name_flag_mismatch": False,
            "result_states_disagree": False,
        },
    }
    assert analyze.cause_flags(row) == []


def test_case_studies_only_selects_high_confidence_losses():
    rows = [
        _row(0.91, False, match_id=1, match_start_at="2026-01-01T00:00:00+00:00"),
        _row(0.91, True, match_id=2, match_start_at="2026-01-02T00:00:00+00:00"),
        _row(0.75, False, match_id=3, match_start_at="2026-01-03T00:00:00+00:00"),
    ]
    cases = analyze.case_studies(rows)
    assert [case["match_id"] for case in cases] == [1]


def test_extractor_history_features_exclude_future_and_same_match():
    from datetime import UTC, datetime

    picked_at = datetime(2026, 10, 10, 12, tzinfo=UTC)
    history = [
        # visible, before pick
        {
            "match_id": 1,
            "player_id": 10,
            "opponent_id": 20,
            "match_date": datetime(2026, 10, 1, tzinfo=UTC),
            "surface": "HARD",
            "won": True,
            "result_as_of": datetime(2026, 10, 1, 18, tzinfo=UTC),
        },
        # result not visible until after the pick
        {
            "match_id": 2,
            "player_id": 10,
            "opponent_id": 20,
            "match_date": datetime(2026, 10, 2, tzinfo=UTC),
            "surface": "HARD",
            "won": False,
            "result_as_of": datetime(2026, 10, 11, tzinfo=UTC),
        },
        # same match, started after the pick
        {
            "match_id": 3,
            "player_id": 10,
            "opponent_id": 20,
            "match_date": datetime(2026, 10, 10, 14, tzinfo=UTC),
            "surface": "CLAY",
            "won": False,
            "result_as_of": datetime(2026, 10, 10, 20, tzinfo=UTC),
        },
        # visible opponent history, different opponent
        {
            "match_id": 4,
            "player_id": 20,
            "opponent_id": 10,
            "match_date": datetime(2026, 10, 3, tzinfo=UTC),
            "surface": "HARD",
            "won": False,
            "result_as_of": datetime(2026, 10, 3, 18, tzinfo=UTC),
        },
    ]
    stats = {1: {}, 3: {}}
    features = extract._history_features(
        history,
        player_id=10,
        opponent_id=20,
        picked_at=picked_at,
        surface="HARD",
        stat_coverage=stats,
    )
    assert features["prior_matches"] == 1
    assert features["prior_wins"] == 1
    assert features["prior_surface_matches"] == 1
    assert features["prior_stat_matches"] == 1
    assert features["h2h_prior_matches"] == 1
    assert features["days_since_last_match"] == 9


def test_load_jsonl_ignores_manifest_row(tmp_path: Path):
    dataset = tmp_path / "dataset.jsonl"
    rows, manifest = analyze.load_jsonl(
        _write_jsonl(
            dataset,
            [
                _row(0.7, True),
                {"_manifest": True, "row_count": 1, "row_sha256": "abc"},
            ],
        )
    )
    assert len(rows) == 1
    assert manifest["row_count"] == 1


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return path


def test_math_erf_fallback_for_normal_p_value():
    # 28/41 at an 0.8425 expected rate is a clear miss.
    p = analyze.normal_two_sided_p(28, 41, 0.8425)
    assert p is not None
    assert p < 0.01
    assert math.isfinite(p)
