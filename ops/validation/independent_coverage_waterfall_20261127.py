"""Independent coverage waterfall evidence from committed aggregate data.

This script does not query the database and does not call a provider. It reuses
the committed read-only coverage refresh and applies the explicit unknown
contract: no licensed complete official export exists, so external coverage is
UNKNOWN, never an internal ratio.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime

from sports_edge_tennis.validation.independent_coverage_waterfall import (
    EXTERNAL_COVERAGE_UNKNOWN,
    FIXTURE_STAGES,
    SELECTION_STAGES,
    aggregate_fixture_totals,
    coverage_from_totals,
    selection_evidence_status,
)

VERSION = "TENNIS_INDEPENDENT_COVERAGE_WATERFALL_20261127_V1"
SOURCE = "docs/research/evidence/internal_coverage_waterfall_refresh_20261125T1740Z.json"


def _group(rows, key):
    grouped = {}
    for row in rows:
        grouped.setdefault(row.get(key), []).append(row)
    return grouped


def build_evidence(source_path: str) -> dict[str, object]:
    with open(source_path, encoding="utf-8") as handle:
        source = json.load(handle)
    rows = source["internal_coverage_waterfall"]

    overall_totals = aggregate_fixture_totals(rows)
    fixture = coverage_from_totals(
        overall_totals,
        stages=FIXTURE_STAGES,
        track="FIXTURE",
        official_total=None,
        official_export_status=EXTERNAL_COVERAGE_UNKNOWN,
    )

    selection_totals = {
        "SELECTED": overall_totals["SELECTED"],
        "INDEPENDENTLY_ARCHIVED": None,
        "PUBLISHED": None,
        "BOOKMAKER_QUALIFIED": None,
        "SETTLED_SELECTION": None,
    }
    selection = coverage_from_totals(
        selection_totals,
        stages=SELECTION_STAGES,
        track="SELECTION",
        official_total=None,
        official_export_status=EXTERNAL_COVERAGE_UNKNOWN,
    )

    by_tour = {}
    for tour, tour_rows in _group(rows, "tour").items():
        by_tour[str(tour)] = coverage_from_totals(
            aggregate_fixture_totals(tour_rows),
            stages=FIXTURE_STAGES,
            track="FIXTURE",
            official_total=None,
            official_export_status=EXTERNAL_COVERAGE_UNKNOWN,
        )

    by_draw_stage = {"MAIN_DRAW": [], "QUALIFYING": []}
    for row in rows:
        key = "QUALIFYING" if row.get("qualifying") else "MAIN_DRAW"
        by_draw_stage[key].append(row)
    draw_stage_payload = {
        name: coverage_from_totals(
            aggregate_fixture_totals(group),
            stages=FIXTURE_STAGES,
            track="FIXTURE",
            official_total=None,
            official_export_status=EXTERNAL_COVERAGE_UNKNOWN,
        )
        for name, group in by_draw_stage.items()
    }

    daily = []
    for row in rows:
        totals = aggregate_fixture_totals([row])
        track = coverage_from_totals(
            totals,
            stages=FIXTURE_STAGES,
            track="FIXTURE",
            official_total=None,
            official_export_status=EXTERNAL_COVERAGE_UNKNOWN,
        )
        daily.append(
            {
                "day_utc": row.get("day_utc"),
                "tour": row.get("tour"),
                "draw_stage": "QUALIFYING" if row.get("qualifying") else "MAIN_DRAW",
                "fixture_track": track["stages"],
                "stored_internal_fixtures": int(row.get("stored_matches") or 0),
                "pit_visible": int(row.get("pit_visible") or 0),
                "targeted_on_time": int(row.get("targeted_on_time") or 0),
                "manager_consensus": int(row.get("manager_consensus") or 0),
                "settled_fixtures": int(row.get("settled_completed") or 0),
            }
        )

    source_blocker = source.get("official_source_blocker") or {}
    return {
        "version": VERSION,
        "report_as_of_utc": datetime.now(UTC).isoformat(),
        "source_evidence": SOURCE,
        "source_report_as_of_utc": source.get("report_as_of_utc"),
        "source_window_start_utc": source.get("window_start_utc"),
        "source_window_end_utc": source.get("window_end_utc"),
        "fresh_query": False,
        "provider_calls": 0,
        "database_writes": 0,
        "scraping": 0,
        "scope_warning": (
            "Internal stored-provider records are not an independent official "
            "denominator. External coverage is UNKNOWN, never zero."
        ),
        "external_populations": {
            "official_eligible": None,
            "official_eligible_status": EXTERNAL_COVERAGE_UNKNOWN,
            "discovered_official": None,
            "mapped_official": None,
            "external_coverage": None,
            "external_coverage_status": EXTERNAL_COVERAGE_UNKNOWN,
            "external_denominator_includes": [
                "UNDISCOVERED_OFFICIAL_FIXTURES",
                "FAILED_OR_MISSING_FORECASTS",
                "UNMAPPED_OFFICIAL_FIXTURES",
            ],
            "reason": source_blocker.get(
                "reason",
                "No licensed complete ATP/Challenger fixture export was supplied.",
            ),
            "blocker_status": source_blocker.get(
                "status", "BLOCKED_NO_AUTHORISED_INDEPENDENT_OFFICIAL_SOURCE"
            ),
        },
        "fixture_track": fixture,
        "fixture_settlement": {
            "settled_fixtures": overall_totals["SETTLED_FIXTURE"],
            "scope": "ALL_DISCOVERED_FIXTURES_NOT_A_FUNNEL_STAGE",
            "official_settlement_distinct_from_bookmaker": True,
            "official_settlement_status": "INTERNAL_COMPLETED_RESULT_ONLY",
            "bookmaker_settlement_status": "UNKNOWN_NOT_IN_COMMITTED_EVIDENCE",
        },
        "selection_track": selection,
        "selection_stage_evidence": selection_evidence_status(),
        "fixture_track_by_tour": by_tour,
        "fixture_track_by_draw_stage": draw_stage_payload,
        "daily": daily,
        "coverage_contract": {
            "unknown_is_not_zero": True,
            "undiscovered_removed_from_denominator": False,
            "failed_forecasts_removed_from_denominator": False,
            "fixtures_and_selections_are_distinct": True,
            "qualifying_and_main_draw_separated": True,
            "official_and_bookmaker_settlement_distinct": True,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=SOURCE)
    parser.add_argument(
        "--out-json",
        default="docs/research/evidence/independent_coverage_waterfall_20261127.json",
    )
    args = parser.parse_args(argv)
    evidence = build_evidence(args.source)
    text = json.dumps(evidence, indent=2, sort_keys=True)
    print(text)
    if args.out_json:
        directory = os.path.dirname(os.path.abspath(args.out_json))
        os.makedirs(directory, exist_ok=True)
        with open(args.out_json, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
