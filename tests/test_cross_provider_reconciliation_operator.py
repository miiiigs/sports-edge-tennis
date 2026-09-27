"""Orchestration tests for the reviewed cross-provider reconciliation operator."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import psycopg
import pytest
import test_cross_provider_reconciliation as helpers

OPERATOR_PATH = Path(__file__).resolve().parents[1] / "ops" / "reconcile_cross_provider_results.py"
_spec = importlib.util.spec_from_file_location(
    "reconcile_cross_provider_results", OPERATOR_PATH
)
operator = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = operator
_spec.loader.exec_module(operator)

SEP27_IDS = {
    131266,
    131279,
    131166,
    131175,
    131178,
    131212,
    131238,
    131239,
    131264,
}


def _count(db: psycopg.Connection, table: str, where: str = "", params: tuple = ()) -> int:
    sql = f"SELECT count(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return int(db.execute(sql, params).fetchone()[0])


def _mapping(target: int, evidence: int, expected: str) -> operator.ReviewedMapping:
    return operator.ReviewedMapping(target, evidence, expected)


def _quiet(*args, **kwargs) -> None:
    return None


def test_manifest_contains_exactly_seven_mappings():
    assert len(operator.REVIEWED_MAPPINGS) == 7
    assert [m.target_match_id for m in operator.REVIEWED_MAPPINGS] == [
        130874,
        130885,
        130886,
        130889,
        130898,
        131021,
        131038,
    ]
    assert [m.evidence_match_id for m in operator.REVIEWED_MAPPINGS] == [
        130832,
        131089,
        131069,
        131071,
        131139,
        131088,
        131140,
    ]


def test_manifest_excludes_sep27_ids():
    ids = {m.target_match_id for m in operator.REVIEWED_MAPPINGS}
    ids |= {m.evidence_match_id for m in operator.REVIEWED_MAPPINGS}
    assert ids.isdisjoint(SEP27_IDS)


def test_default_mode_is_dry_run():
    assert inspect.signature(operator.run_operator).parameters["apply"].default is False


def test_dry_run_performs_zero_writes(db: psycopg.Connection, monkeypatch):
    target, evidence = helpers._scenario(db)
    monkeypatch.setattr(operator, "_load_selected_side", lambda conn, match_id: 1)
    summary = operator.run_operator(
        db, mappings=(_mapping(target, evidence, "WON"),), emit=_quiet
    )
    assert summary.mode == operator.MODE_DRY_RUN
    assert _count(db, "cross_provider_match_reconciliations") == 0
    assert _count(db, "match_result_observations", "match_id = %s", (target,)) == 0


def test_all_safe_cohort_reports_would_apply(db: psycopg.Connection, monkeypatch):
    target, evidence = helpers._scenario(db)
    monkeypatch.setattr(operator, "_load_selected_side", lambda conn, match_id: 1)
    summary = operator.run_operator(
        db, mappings=(_mapping(target, evidence, "WON"),), emit=_quiet
    )
    assert summary.audits[0].action == operator.ACTION_WOULD_APPLY
    assert summary.expected_result_mismatches == 0


def test_expectation_mismatch_fails_closed(db: psycopg.Connection, monkeypatch):
    target, evidence = helpers._scenario(db)
    monkeypatch.setattr(operator, "_load_selected_side", lambda conn, match_id: 2)
    summary = operator.run_operator(
        db, mappings=(_mapping(target, evidence, "WON"),), emit=_quiet
    )
    assert summary.audits[0].action == operator.ACTION_EXPECTED_RESULT_MISMATCH
    assert summary.expected_result_mismatches == 1


def test_expectation_mismatch_blocks_apply(db: psycopg.Connection, monkeypatch):
    target, evidence = helpers._scenario(db)
    monkeypatch.setattr(operator, "_load_selected_side", lambda conn, match_id: 2)
    summary = operator.run_operator(
        db, apply=True, mappings=(_mapping(target, evidence, "WON"),), emit=_quiet
    )
    assert summary.aborted is True
    assert summary.newly_applied == 0
    assert _count(db, "cross_provider_match_reconciliations") == 0


def test_unsafe_row_blocks_apply_cohort(db: psycopg.Connection, monkeypatch):
    unsafe_target, unsafe_evidence = helpers._scenario(db, player_mismatch=True)
    safe_target, safe_evidence = helpers._scenario(db)
    monkeypatch.setattr(operator, "_load_selected_side", lambda conn, match_id: 1)
    summary = operator.run_operator(
        db,
        apply=True,
        mappings=(
            _mapping(unsafe_target, unsafe_evidence, "WON"),
            _mapping(safe_target, safe_evidence, "WON"),
        ),
        emit=_quiet,
    )
    assert summary.aborted is True
    assert summary.newly_applied == 0
    assert _count(db, "cross_provider_match_reconciliations") == 0


def test_successful_apply_delegates_to_service(db: psycopg.Connection, monkeypatch):
    direct_target, direct_evidence = helpers._scenario(db)
    reversed_target, reversed_evidence = helpers._scenario(db, reverse=True, winner_side=2)
    monkeypatch.setattr(operator, "_load_selected_side", lambda conn, match_id: 1)
    summary = operator.run_operator(
        db,
        apply=True,
        mappings=(
            _mapping(direct_target, direct_evidence, "WON"),
            _mapping(reversed_target, reversed_evidence, "WON"),
        ),
        emit=_quiet,
    )
    assert summary.newly_applied == 2
    assert _count(db, "cross_provider_match_reconciliations") == 2
    assert _count(db, "match_result_observations", "match_id = %s", (direct_target,)) == 1
    assert (
        _count(db, "match_result_observations", "match_id = %s", (reversed_target,)) == 1
    )


def test_second_run_reports_already_reconciled(db: psycopg.Connection, monkeypatch):
    direct_target, direct_evidence = helpers._scenario(db)
    reversed_target, reversed_evidence = helpers._scenario(db, reverse=True, winner_side=2)
    monkeypatch.setattr(operator, "_load_selected_side", lambda conn, match_id: 1)
    mappings = (
        _mapping(direct_target, direct_evidence, "WON"),
        _mapping(reversed_target, reversed_evidence, "WON"),
    )
    operator.run_operator(db, apply=True, mappings=mappings, emit=_quiet)
    summary = operator.run_operator(db, mappings=mappings, emit=_quiet)
    assert summary.already_reconciled == 2
    assert summary.newly_applied == 0
    assert summary.audits[0].action == operator.ACTION_ALREADY_RECONCILED
    assert _count(db, "cross_provider_match_reconciliations") == 2


def test_apply_failure_rolls_back_new_cohort(db: psycopg.Connection, monkeypatch):
    first_target, first_evidence = helpers._scenario(db)
    second_target, second_evidence = helpers._scenario(db)
    monkeypatch.setattr(operator, "_load_selected_side", lambda conn, match_id: 1)
    real_apply = operator.apply_cross_provider_reconciliation
    calls = {"n": 0}

    def _flaky(conn, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("forced failure")
        return real_apply(conn, **kwargs)

    monkeypatch.setattr(operator, "apply_cross_provider_reconciliation", _flaky)
    with pytest.raises(RuntimeError):
        operator.run_operator(
            db,
            apply=True,
            mappings=(
                _mapping(first_target, first_evidence, "WON"),
                _mapping(second_target, second_evidence, "WON"),
            ),
            emit=_quiet,
        )
    assert _count(db, "cross_provider_match_reconciliations") == 0
    assert _count(db, "match_result_observations", "match_id = %s", (first_target,)) == 0


def test_131038_appears_once():
    assert [m.target_match_id for m in operator.REVIEWED_MAPPINGS].count(131038) == 1


def test_summary_expects_one_won_six_lost():
    counts = operator.expected_result_counts(operator.REVIEWED_MAPPINGS)
    assert counts[operator.RESULT_WON] == 1
    assert counts[operator.RESULT_LOST] == 6
    assert counts[operator.RESULT_VOID] == 0


def test_manifest_has_explicit_provider_expectations():
    for mapping in operator.REVIEWED_MAPPINGS:
        assert mapping.target_source_system == "SPORTRADAR"
        assert mapping.evidence_source_system == "API_TENNIS"


def test_dry_run_detects_missing_reconciliation_table(
    db: psycopg.Connection, monkeypatch
):
    target, evidence = helpers._scenario(db)
    monkeypatch.setattr(operator, "_load_selected_side", lambda conn, match_id: 1)
    monkeypatch.setattr(operator, "_reconciliation_table_present", lambda conn: False)
    summary = operator.run_operator(
        db, mappings=(_mapping(target, evidence, "WON"),), emit=_quiet
    )
    assert summary.schema_present is False
    assert summary.audits[0].action == operator.ACTION_WOULD_APPLY


def test_apply_mode_fails_closed_when_schema_missing(
    db: psycopg.Connection, monkeypatch
):
    target, evidence = helpers._scenario(db)
    monkeypatch.setattr(operator, "_load_selected_side", lambda conn, match_id: 1)
    monkeypatch.setattr(operator, "_reconciliation_table_present", lambda conn: False)
    summary = operator.run_operator(
        db, apply=True, mappings=(_mapping(target, evidence, "WON"),), emit=_quiet
    )
    assert summary.aborted is True
    assert summary.blocker == "RECONCILIATION_SCHEMA_MISSING"
    assert summary.newly_applied == 0
    assert _count(db, "cross_provider_match_reconciliations") == 0
