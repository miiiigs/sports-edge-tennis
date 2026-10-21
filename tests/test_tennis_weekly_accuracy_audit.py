"""Synthetic coverage for the read-only weekly accuracy audit."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from research.tennis_weekly_accuracy_audit_20261021.dataset import (
    CONFLICTED,
    LOST,
    PENDING,
    VOID,
    WON,
    aligned_picks,
    in_window,
    load_dataset,
    local_date,
)
from research.tennis_weekly_accuracy_audit_20261021.filters import (
    PREDECLARED_FILTERS,
    filter_by_name,
    high_confidence_without_market,
    market_disagreement,
    qualification_mismatch,
)
from research.tennis_weekly_accuracy_audit_20261021.metrics import (
    accuracy_metrics,
    probability_bands,
    wilson_interval,
)
from research.tennis_weekly_accuracy_audit_20261021.reconciliation import (
    flat_stake_roi,
    reconcile_week,
)


def _row(
    *,
    match_id: int,
    population: str = "manager_consensus",
    probability: float = 0.65,
    picked_at: str = "2026-10-19T02:00:00+00:00",
    match_start_at: str | None = "2026-10-19T08:00:00+00:00",
    outcome: str | None = "COMPLETED",
    winner_side: int | None = 1,
    selected_side: int = 1,
    qualify_flag: bool = False,
    name_has_qualification: bool = False,
    model_minus_sharp: float | None = None,
    spread: float = 0.02,
    surface_matches: int = 10,
    disagree: bool = False,
    value_bet: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "population": population,
        "match_id": match_id,
        "tournament": "Florence (Italy) - Qualification"
        if name_has_qualification
        else "Florence",
        "qualifying_flag": qualify_flag,
        "selected_side": selected_side,
        "picked_player_name": "A. Player",
        "opponent_player_name": "B. Player",
        "consensus_probability": f"{probability:.6f}",
        "picked_at": picked_at,
        "match_start_at": match_start_at,
        "prediction_finalized_at": picked_at,
        "outcome_status": outcome,
        "winner_side": winner_side,
        "probability_spread": f"{spread:.6f}",
        "surface": "CLAY",
        "tier": "CHALLENGER",
        "round_code": "R32",
        "data_quality": {
            "result_states_disagree": disagree,
            "tournament_name_has_qualification": name_has_qualification,
            "surface_missing": False,
        },
        "market": {
            "model_minus_sharp": model_minus_sharp,
            "sharp_picked_probability": None,
            "sharp_favourite_side": None,
        },
        "history": {
            "picked": {
                "days_since_last_match": 2,
                "prior_surface_matches": surface_matches,
                "prior_stat_matches": 5,
            }
        },
        "value_bet": value_bet
        or {
            "decision_id": None,
            "edge_pct": None,
            "is_value_bet": False,
            "is_vetoed": False,
            "price": None,
            "probability": None,
            "published_at": None,
            "veto_codes": None,
        },
        "agents": {},
    }


def _value_bet_row(
    match_id: int, *, price: float, winner_side: int
) -> dict[str, object]:
    row = _row(
        match_id=match_id,
        population="value_bet",
        winner_side=winner_side,
    )
    row.update(
        {
            "as_of": "2026-10-18T12:00:00+00:00",
            "decision_id": match_id,
            "decision_status": "BET",
            "edge_pct": "0.100000",
            "market_decimal_price": f"{price:.6f}",
            "published_at": "2026-10-18T12:00:00+00:00",
            "triggered_codes": None,
        }
    )
    return row


def _write_dataset(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    path = tmp_path / "dataset.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
        handle.write(
            json.dumps(
                {"_manifest": True, "extract_cutoff": "2026-10-21T03:30:00+00:00"}
            )
            + "\n"
        )
    return path


def _load(tmp_path: Path, rows: list[dict[str, object]]):
    return load_dataset(_write_dataset(tmp_path, rows))


def test_result_status_mapping(tmp_path: Path) -> None:
    dataset = _load(
        tmp_path,
        [
            _row(match_id=1, selected_side=1, winner_side=1),
            _row(match_id=2, selected_side=1, winner_side=2),
            _row(match_id=3, outcome="RETIRED", winner_side=1),
            _row(match_id=4, outcome="WALKOVER", winner_side=1),
            _row(match_id=5, outcome="CANCELLED", winner_side=None),
            _row(match_id=6, outcome=None, winner_side=None),
            _row(match_id=7, disagree=True),
        ],
    )
    assert [pick.result_status for pick in dataset.picks] == [
        WON,
        LOST,
        VOID,
        VOID,
        VOID,
        PENDING,
        CONFLICTED,
    ]


def test_accuracy_excludes_pending_and_void_rows(tmp_path: Path) -> None:
    dataset = _load(
        tmp_path,
        [
            _row(match_id=1, winner_side=1),
            _row(match_id=2, winner_side=1),
            _row(match_id=3, winner_side=2),
            _row(match_id=4, outcome="RETIRED", winner_side=1),
            _row(match_id=5, outcome=None, winner_side=None),
        ],
    )
    metrics = accuracy_metrics(dataset.picks)
    assert metrics.n == 3
    assert metrics.wins == 2
    assert metrics.accuracy == pytest.approx(2 / 3)
    assert metrics.mean_probability == pytest.approx(0.65)
    assert metrics.calibration_gap == pytest.approx(2 / 3 - 0.65)


def test_aligned_population_freezes_the_earliest_qualifying_pick(
    tmp_path: Path,
) -> None:
    dataset = _load(
        tmp_path,
        [
            _row(
                match_id=1,
                population="wide_spread_tracking",
                probability=0.64,
                picked_at="2026-10-18T10:00:00+00:00",
                selected_side=1,
            ),
            _row(
                match_id=1,
                population="manager_consensus",
                probability=0.74,
                picked_at="2026-10-18T12:00:00+00:00",
                selected_side=1,
            ),
            _row(
                match_id=2,
                population="manager_consensus",
                probability=0.59,
                picked_at="2026-10-18T10:00:00+00:00",
            ),
        ],
    )
    aligned = aligned_picks(dataset.picks)
    assert len(aligned) == 1
    assert aligned[0].match_id == 1
    assert aligned[0].pick_type == "A1_A2_WIDE_SPREAD"
    assert aligned[0].probability == pytest.approx(0.64)


def test_timezone_buckets_match_start_not_pick_time(tmp_path: Path) -> None:
    dataset = _load(
        tmp_path,
        [
            _row(
                match_id=1,
                picked_at="2026-10-18T20:00:00+00:00",
                match_start_at="2026-10-19T16:30:00+00:00",
            )
        ],
    )
    pick = dataset.picks[0]
    assert local_date(pick, "Asia/Manila") == date(2026, 10, 20)
    assert local_date(pick, "UTC") == date(2026, 10, 19)
    assert in_window([pick], date(2026, 10, 19), date(2026, 10, 19), "UTC")
    assert not in_window([pick], date(2026, 10, 19), date(2026, 10, 19), "Asia/Manila")


def test_predeclared_filters_use_documented_thresholds(tmp_path: Path) -> None:
    dataset = _load(
        tmp_path,
        [
            _row(match_id=1, model_minus_sharp=0.200001),
            _row(match_id=2, model_minus_sharp=0.20),
            _row(match_id=3, qualify_flag=True, name_has_qualification=True),
            _row(match_id=4, qualify_flag=False, name_has_qualification=False),
            _row(match_id=5, probability=0.80, model_minus_sharp=None),
        ],
    )
    picks = {pick.match_id: pick for pick in dataset.picks}
    assert market_disagreement(picks[1])
    assert not market_disagreement(picks[2])
    assert not qualification_mismatch(picks[3])
    assert high_confidence_without_market(picks[5])
    assert filter_by_name("R1_MARKET_DISAGREEMENT_20").rejects(picks[1])
    assert len({item.name for item in PREDECLARED_FILTERS}) == len(PREDECLARED_FILTERS)


def test_wilson_interval_and_probability_bands(tmp_path: Path) -> None:
    interval = wilson_interval(9, 17)
    assert interval is not None
    assert interval[0] < 9 / 17 < interval[1]
    dataset = _load(
        tmp_path,
        [
            _row(match_id=1, probability=0.65, winner_side=1),
            _row(match_id=2, probability=0.75, winner_side=2),
            _row(match_id=3, probability=0.85, winner_side=1),
        ],
    )
    bands = probability_bands(dataset.picks)
    assert [band["band"] for band in bands] == [
        "0.60-0.70",
        "0.70-0.80",
        "0.80-0.90",
        "0.90-1.01",
    ]
    assert [band["n"] for band in bands] == [1, 1, 1, 0]


def test_flat_stake_roi_uses_recorded_price_only(tmp_path: Path) -> None:
    dataset = _load(
        tmp_path,
        [
            _value_bet_row(1, price=2.0, winner_side=1),
            _value_bet_row(2, price=1.5, winner_side=2),
        ],
    )
    roi = flat_stake_roi(dataset.picks)
    assert roi["bets"] == 2
    assert roi["profit_units"] == pytest.approx(0.0)
    assert roi["roi"] == pytest.approx(0.0)


def test_reconcile_week_reports_bounds_and_populations(tmp_path: Path) -> None:
    dataset = _load(
        tmp_path,
        [
            _row(match_id=1, probability=0.70, winner_side=1),
            _row(match_id=2, probability=0.70, winner_side=2),
            _row(match_id=3, probability=0.70, outcome=None, winner_side=None),
            _row(match_id=4, probability=0.55, winner_side=1),
        ],
    )
    report = reconcile_week(
        dataset,
        start=date(2026, 10, 19),
        end=date(2026, 10, 21),
        timezone_name="Asia/Manila",
    )
    manager = report["populations"]["manager_primary"]
    assert manager["picks"] == 3
    assert manager["scored"] == 2
    assert manager["wins"] == 1
    assert manager["pending"] == 1
    assert report["best_case"]["manager_primary"] == pytest.approx(2 / 3)
    assert len(report["manager_week_ledger"]) == 3
    assert report["experiments"]["filters"]
