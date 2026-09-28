"""Orchestration tests for the reviewed cross-provider reconciliation operator."""

from __future__ import annotations

import importlib.util
import inspect
import os
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


PROD_TARGETS = [130874, 130885, 130886, 130889, 130898, 131021, 131038]
PROD_EVIDENCE = [130832, 131089, 131069, 131071, 131139, 131088, 131140]
PROD_WINNER = {130832: 1, 131089: 2, 131069: 2, 131071: 1, 131139: 2, 131088: 2, 131140: 2}
PROD_SELECTED = {130874: 2, 130885: 1, 130886: 2, 130889: 2, 130898: 1, 131021: 1, 131038: 1}


def _seed_production_cohort(conn: psycopg.Connection) -> None:
    for target, evidence in zip(PROD_TARGETS, PROD_EVIDENCE):
        pa = helpers._mk_player(conn, f"A{target}")
        pb = helpers._mk_player(conn, f"B{target}")
        tournament = helpers._mk_tournament(conn, f"T{target}")
        target_edition = helpers._mk_edition(conn, tournament, 2026, f"ET{target}")
        evidence_edition = helpers._mk_edition(conn, tournament, 2026, f"EE{target}")
        conn.execute(
            "INSERT INTO matches (match_id, tournament_edition_id) "
            "OVERRIDING SYSTEM VALUE VALUES (%s, %s)",
            (target, target_edition),
        )
        conn.execute(
            "INSERT INTO matches (match_id, tournament_edition_id) "
            "OVERRIDING SYSTEM VALUE VALUES (%s, %s)",
            (evidence, evidence_edition),
        )
        helpers._mk_version(conn, target, pa, pb, helpers.BASE_START)
        helpers._mk_version(conn, evidence, pa, pb, helpers.BASE_START)
        helpers._mk_source(conn, target, "SPORTRADAR", f"sr-{target}")
        helpers._mk_source(conn, evidence, "API_TENNIS", f"at-{evidence}")
        helpers._mk_result(conn, evidence, winner_side=PROD_WINNER[evidence])


def _sides(conn: psycopg.Connection, match_id: int):
    return PROD_SELECTED.get(match_id)


def _test_dsn(monkeypatch) -> str:
    dsn = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.delenv("TENNIS_DATABASE_URL", raising=False)
    return dsn


def test_cli_apply_commits_across_new_connection(db: psycopg.Connection, monkeypatch):
    _seed_production_cohort(db)
    monkeypatch.setattr(operator, "_load_selected_side", _sides)
    dsn = _test_dsn(monkeypatch)

    assert operator.main(["--apply"]) == 0

    with psycopg.connect(dsn, autocommit=True) as fresh:
        assert (
            int(
                fresh.execute(
                    "SELECT count(*) FROM cross_provider_match_reconciliations"
                ).fetchone()[0]
            )
            == 7
        )
        for target in PROD_TARGETS:
            assert (
                int(
                    fresh.execute(
                        "SELECT count(*) FROM match_result_observations WHERE match_id = %s",
                        (target,),
                    ).fetchone()[0]
                )
                == 1
            )


def test_cli_apply_rolls_back_across_new_connection(
    db: psycopg.Connection, monkeypatch
):
    _seed_production_cohort(db)
    monkeypatch.setattr(operator, "_load_selected_side", _sides)
    dsn = _test_dsn(monkeypatch)

    real_apply = operator.apply_cross_provider_reconciliation
    calls = {"n": 0}

    def _flaky(conn, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("forced failure")
        return real_apply(conn, **kwargs)

    monkeypatch.setattr(operator, "apply_cross_provider_reconciliation", _flaky)

    with pytest.raises(RuntimeError):
        operator.main(["--apply"])

    with psycopg.connect(dsn, autocommit=True) as fresh:
        assert (
            int(
                fresh.execute(
                    "SELECT count(*) FROM cross_provider_match_reconciliations"
                ).fetchone()[0]
            )
            == 0
        )
        assert (
            int(
                fresh.execute(
                    "SELECT count(*) FROM match_result_observations "
                    "WHERE match_id = ANY(%s)",
                    (PROD_TARGETS,),
                ).fetchone()[0]
            )
            == 0
        )


def test_cli_dry_run_writes_nothing_across_new_connection(
    db: psycopg.Connection, monkeypatch
):
    _seed_production_cohort(db)
    monkeypatch.setattr(operator, "_load_selected_side", _sides)
    dsn = _test_dsn(monkeypatch)

    assert operator.main([]) == 0

    with psycopg.connect(dsn, autocommit=True) as fresh:
        assert (
            int(
                fresh.execute(
                    "SELECT count(*) FROM cross_provider_match_reconciliations"
                ).fetchone()[0]
            )
            == 0
        )
        assert (
            int(
                fresh.execute(
                    "SELECT count(*) FROM match_result_observations "
                    "WHERE match_id = ANY(%s)",
                    (PROD_TARGETS,),
                ).fetchone()[0]
            )
            == 0
        )


def _wide(target: int, evidence: int, expected: str) -> operator.ReviewedMapping:
    return operator.ReviewedMapping(
        target, evidence, expected, selection_source=operator.SELECTION_WIDE_SPREAD
    )


def test_wide_spread_cohort_has_exactly_seven_mappings():
    assert len(operator.WIDE_SPREAD_REVIEWED_COHORT) == 7
    assert [m.target_match_id for m in operator.WIDE_SPREAD_REVIEWED_COHORT] == [
        130873,
        130884,
        130888,
        130896,
        131027,
        130899,
        130901,
    ]
    assert [m.evidence_match_id for m in operator.WIDE_SPREAD_REVIEWED_COHORT] == [
        130838,
        131086,
        131072,
        131093,
        131092,
        131136,
        131142,
    ]
    assert [m.expected_result for m in operator.WIDE_SPREAD_REVIEWED_COHORT] == [
        "WON",
        "LOST",
        "LOST",
        "LOST",
        "VOID",
        "WON",
        "LOST",
    ]


def test_first_cohort_remains_unchanged():
    assert operator.FIRST_PRODUCTION_REVIEWED_COHORT is operator.REVIEWED_MAPPINGS
    assert [
        m.target_match_id for m in operator.FIRST_PRODUCTION_REVIEWED_COHORT
    ] == [130874, 130885, 130886, 130889, 130898, 131021, 131038]


def test_cohort_excludes_130903_and_131036():
    ids: set[int] = set()
    for cohort in operator.COHORTS.values():
        for mapping in cohort:
            ids.add(mapping.target_match_id)
            ids.add(mapping.evidence_match_id)
    assert 130903 not in ids
    assert 131036 not in ids
    assert 131137 not in ids


def test_cohort_selection_keys():
    assert set(operator.COHORTS) == {"first-production", "wide-spread-20260924-26"}


def test_wide_spread_uses_a1_a2_side_not_manager_side(
    db: psycopg.Connection, monkeypatch
):
    target, evidence = helpers._scenario(db, winner_side=1)
    monkeypatch.setattr(operator, "_load_selected_side", lambda conn, match_id: 2)
    monkeypatch.setattr(
        operator, "_load_wide_spread_selected_side", lambda conn, match_id: 1
    )
    summary = operator.run_operator(db, mappings=(_wide(target, evidence, "WON"),), emit=_quiet)
    assert summary.audits[0].selected_side == 1
    assert summary.audits[0].action == operator.ACTION_WOULD_APPLY


def test_wide_spread_won_verification(db: psycopg.Connection, monkeypatch):
    target, evidence = helpers._scenario(db, winner_side=1)
    monkeypatch.setattr(
        operator, "_load_wide_spread_selected_side", lambda conn, match_id: 1
    )
    summary = operator.run_operator(db, mappings=(_wide(target, evidence, "WON"),), emit=_quiet)
    assert summary.audits[0].computed_result == "WON"
    assert summary.audits[0].action == operator.ACTION_WOULD_APPLY


def test_wide_spread_lost_verification(db: psycopg.Connection, monkeypatch):
    target, evidence = helpers._scenario(db, winner_side=2)
    monkeypatch.setattr(
        operator, "_load_wide_spread_selected_side", lambda conn, match_id: 1
    )
    summary = operator.run_operator(db, mappings=(_wide(target, evidence, "LOST"),), emit=_quiet)
    assert summary.audits[0].computed_result == "LOST"
    assert summary.audits[0].action == operator.ACTION_WOULD_APPLY


def test_wide_spread_void_for_retired(db: psycopg.Connection, monkeypatch):
    target, evidence = helpers._scenario(db, with_result=False)
    helpers._mk_result(db, evidence, outcome="RETIRED", winner_side=2)
    monkeypatch.setattr(
        operator, "_load_wide_spread_selected_side", lambda conn, match_id: 1
    )
    summary = operator.run_operator(db, mappings=(_wide(target, evidence, "VOID"),), emit=_quiet)
    assert summary.audits[0].computed_result == "VOID"
    assert summary.audits[0].action == operator.ACTION_WOULD_APPLY


def test_wide_spread_dry_run_zero_writes(db: psycopg.Connection, monkeypatch):
    target, evidence = helpers._scenario(db)
    monkeypatch.setattr(
        operator, "_load_wide_spread_selected_side", lambda conn, match_id: 1
    )
    summary = operator.run_operator(db, mappings=(_wide(target, evidence, "WON"),), emit=_quiet)
    assert summary.mode == operator.MODE_DRY_RUN
    assert _count(db, "cross_provider_match_reconciliations") == 0
    assert _count(db, "match_result_observations", "match_id = %s", (target,)) == 0
