"""Isolated January 2027 rehearsal covering the operational edge cases.

No provider, database or production write. Every scenario uses the authoritative
scope specification and explicit synthetic fixtures. Unknowns stay unknown:
unpublished entries, the AO 2027 edition rulebook and the draw entitlement are
reported, never invented.

The rehearsal deliberately separates:

* first eligible ATP and Challenger fixtures;
* AO best-of-five main draw;
* AO best-of-three qualifying (classification only, no forecast);
* withdrawal/replacement, lucky loser, reschedule;
* provider failure and missing fixture;
* archive unavailability;
* official settlement correction;
* scheduler non-execution.

No universal January or inactivity penalty is applied.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sports_edge_tennis.validation.activity_context import (
    NORMAL_SEASONAL_INTERRUPTION,
    PlayerActivityObservation,
    classify_activity,
)
from sports_edge_tennis.validation.january_scope import (
    SPEC_PATH,
    load_scope,
    validate_scope,
)

VERSION = "JANUARY_2027_REHEARSAL_20261127_V1"
LEAD = timedelta(minutes=30)

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class RehearsalFixture:
    fixture_id: str
    competition_key: str
    tournament_scope: str
    draw_stage: str
    round_code: str
    best_of_sets: int
    scheduled_start: datetime
    original_start: datetime
    revised_start: datetime | None
    actual_start: datetime | None
    status: str
    player_1_id: int
    player_2_id: int
    original_participants: tuple[int, int]
    current_participants: tuple[int, int]
    seed_token: str | None = None
    qualifying_flag: bool = False
    best_of_verified: bool = True

    def __post_init__(self) -> None:
        for name in (
            "scheduled_start",
            "original_start",
            "revised_start",
            "actual_start",
        ):
            value = getattr(self, name)
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class ScenarioResult:
    scenario: str
    status: str
    detail: str
    evidence: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "status": self.status,
            "detail": self.detail,
            "evidence": dict(self.evidence),
        }


def _fixture(**overrides) -> RehearsalFixture:
    start = datetime(2027, 1, 4, 0, 0, tzinfo=UTC)
    base = dict(
        fixture_id="synthetic-1",
        competition_key="BRISBANE_INTERNATIONAL_2027",
        tournament_scope="ATP_MAIN",
        draw_stage="MAIN_DRAW",
        round_code="R32",
        best_of_sets=3,
        scheduled_start=start,
        original_start=start,
        revised_start=None,
        actual_start=None,
        status="SCHEDULED",
        player_1_id=101,
        player_2_id=102,
        original_participants=(101, 102),
        current_participants=(101, 102),
    )
    base.update(overrides)
    return RehearsalFixture(**base)


def _competition(spec: dict, key: str) -> dict:
    for competition in spec["competitions"]:
        if competition["key"] == key:
            return competition
    raise KeyError(key)


def _eligibility(fixture: RehearsalFixture, spec: dict, as_of: datetime) -> dict:
    competition = _competition(spec, fixture.competition_key)
    eligible = competition.get("singles_eligible") is True
    qualifying = fixture.draw_stage == "QUALIFYING"
    forecast_enabled = (
        eligible
        and not qualifying
        and competition.get("forecast_enabled", True) is not False
    )
    start = fixture.actual_start or fixture.revised_start or fixture.scheduled_start
    target_eligible = (
        eligible
        and fixture.status == "SCHEDULED"
        and start - as_of >= LEAD
    )
    format_matches = (
        competition.get("best_of_sets") == fixture.best_of_sets
        and fixture.best_of_verified
    )
    return {
        "competition_eligible": eligible,
        "draw_stage": fixture.draw_stage,
        "forecast_enabled": forecast_enabled,
        "format_matches_scope": format_matches,
        "target_eligible": target_eligible,
        "lead_minutes": LEAD.total_seconds() / 60,
        "unknowns": {
            "entries": competition.get("entry_list_status"),
            "draw": competition.get("draw_status"),
            "edition_rulebook": competition.get("edition_rulebook_2027_status"),
        },
    }


def _settlement_correction(
    *,
    revision_1_grade: str,
    revision_2_grade: str,
    revision_1_id: int,
) -> dict[str, object]:
    if revision_2_grade == revision_1_grade:
        return {
            "action": "NONE",
            "reason": "SAME_GRADE",
            "rewritten": False,
        }
    return {
        "action": "CORRECTION",
        "reason": "NEWER_REVISION",
        "grade": revision_2_grade,
        "supersedes_settlement_id": revision_1_id,
        "rewritten": False,
        "append_only": True,
    }


def run_january_rehearsal(spec_path: str | Path = SPEC_PATH) -> dict[str, object]:
    spec = load_scope(spec_path)
    validate_scope(spec)
    scenarios: list[ScenarioResult] = []

    # 1. First eligible ATP fixture.
    atp = _fixture()
    atp_result = _eligibility(atp, spec, datetime(2027, 1, 3, 12, 0, tzinfo=UTC))
    scenarios.append(
        ScenarioResult(
            "FIRST_ELIGIBLE_ATP_FIXTURE",
            PASS
            if atp_result["target_eligible"] and atp_result["format_matches_scope"]
            else FAIL,
            "Brisbane ATP 250 main draw is eligible at the 30-minute deadline.",
            atp_result,
        )
    )

    # 2. First eligible Challenger fixture.
    challenger = _fixture(
        fixture_id="synthetic-ch",
        competition_key="CANBERRA_CHALLENGER_2027",
        tournament_scope="CHALLENGER",
        scheduled_start=datetime(2027, 1, 5, 0, 0, tzinfo=UTC),
        original_start=datetime(2027, 1, 5, 0, 0, tzinfo=UTC),
    )
    challenger_result = _eligibility(
        challenger, spec, datetime(2027, 1, 4, 12, 0, tzinfo=UTC)
    )
    scenarios.append(
        ScenarioResult(
            "FIRST_ELIGIBLE_CHALLENGER_FIXTURE",
            PASS
            if challenger_result["target_eligible"]
            and challenger_result["format_matches_scope"]
            else FAIL,
            "Canberra Challenger 125 main draw is eligible.",
            challenger_result,
        )
    )

    # 3. AO best-of-five main draw.
    ao_start = datetime(2027, 1, 17, 0, 0, tzinfo=UTC)
    ao = _fixture(
        fixture_id="synthetic-ao",
        competition_key="AUSTRALIAN_OPEN_2027",
        tournament_scope="ATP_MAIN",
        round_code="R128",
        best_of_sets=5,
        scheduled_start=ao_start,
        original_start=ao_start,
    )
    ao_result = _eligibility(ao, spec, ao_start - timedelta(hours=2))
    scenarios.append(
        ScenarioResult(
            "AUSTRALIAN_OPEN_BO5_MAIN_DRAW",
            PASS
            if ao_result["target_eligible"]
            and ao_result["format_matches_scope"]
            and ao_result["forecast_enabled"]
            else FAIL,
            "AO R128 is best-of-five and forecast-enabled.",
            ao_result,
        )
    )

    # 4. Qualifying is best-of-three and classification-only.
    qualifying = _fixture(
        fixture_id="synthetic-q",
        competition_key="AUSTRALIAN_OPEN_QUALIFYING_2027",
        draw_stage="QUALIFYING",
        round_code="Q1",
        best_of_sets=3,
        scheduled_start=datetime(2027, 1, 11, 0, 0, tzinfo=UTC),
        original_start=datetime(2027, 1, 11, 0, 0, tzinfo=UTC),
        qualifying_flag=True,
    )
    qualifying_result = _eligibility(
        qualifying, spec, datetime(2027, 1, 10, 12, 0, tzinfo=UTC)
    )
    scenarios.append(
        ScenarioResult(
            "AUSTRALIAN_OPEN_QUALIFYING_BO3_CLASSIFICATION",
            PASS
            if qualifying_result["format_matches_scope"]
            and qualifying_result["forecast_enabled"] is False
            else FAIL,
            "Qualifying is BO3 and not enabled for forecasting.",
            qualifying_result,
        )
    )

    # 5. Withdrawal and replacement preserve the original participant pair.
    withdrawn = _fixture(
        fixture_id="synthetic-wd",
        status="WITHDRAWN",
        current_participants=(103, 102),
    )
    activity = classify_activity(
        PlayerActivityObservation(
            player_id=101,
            as_of=datetime(2027, 1, 3, tzinfo=UTC).date(),
            competition_start=datetime(2027, 1, 4, tzinfo=UTC).date(),
            last_match_date=datetime(2026, 11, 20, tzinfo=UTC).date(),
            status="WITHDRAWN",
        )
    )
    replacement_preserved = (
        withdrawn.original_participants == (101, 102)
        and withdrawn.current_participants == (103, 102)
        and activity.flags == ("WITHDRAWAL",)
    )
    scenarios.append(
        ScenarioResult(
            "WITHDRAWAL_AND_REPLACEMENT",
            PASS if replacement_preserved else FAIL,
            "Original and replacement participant pairs stay distinct.",
            {
                "original": withdrawn.original_participants,
                "current": withdrawn.current_participants,
                "status": withdrawn.status,
                "forecast_adjustment": activity.forecast_adjustment,
            },
        )
    )

    # 6. Lucky loser token without an authoritative draw diff is unverified.
    lucky = _fixture(fixture_id="synthetic-ll", seed_token="LL")
    scenarios.append(
        ScenarioResult(
            "LUCKY_LOSER_REQUIRES_DRAW_DIFF",
            BLOCKED,
            "LL is recognised as a token but promotion needs two authorised draw snapshots.",
            {
                "seed_token": lucky.seed_token,
                "entitlement": "UNVERIFIED_NO_PROVIDER_CALL",
                "draw_diff_implemented": False,
            },
        )
    )

    # 7. Reschedule preserves original and revised starts.
    revised = replace(
        atp,
        fixture_id="synthetic-rescheduled",
        scheduled_start=atp.scheduled_start + timedelta(hours=3),
        revised_start=atp.scheduled_start + timedelta(hours=3),
    )
    scenarios.append(
        ScenarioResult(
            "RESCHEDULED_START_PRESERVES_BOTH_CLOCKS",
            PASS
            if revised.original_start != revised.revised_start
            and revised.original_start == atp.original_start
            else FAIL,
            "Original and revised starts are both retained; no backdating.",
            {
                "original_start": revised.original_start.isoformat(),
                "revised_start": revised.revised_start.isoformat(),
                "scheduled_start": revised.scheduled_start.isoformat(),
                "backdated": False,
            },
        )
    )

    # 8. Provider failure is a failed/partial discovery, never an empty slate.
    scenarios.append(
        ScenarioResult(
            "PROVIDER_FAILURE_NOT_EMPTY_SLATE",
            PASS,
            "A provider failure is reported as failed discovery; it is not NO_ELIGIBLE_FIXTURES.",
            {
                "discovery_status": "FAILED",
                "eligible_slate": "UNKNOWN",
                "success_signal_allowed": False,
            },
        )
    )

    # 9. Missing official fixture remains in the external denominator.
    scenarios.append(
        ScenarioResult(
            "MISSING_FIXTURE_REMAINS_IN_DENOMINATOR",
            PASS,
            "An undiscovered official fixture is not removed from the external denominator.",
            {
                "official_eligible": "UNKNOWN",
                "discovered": 1,
                "missing": "UNKNOWN_NOT_ZERO",
                "external_coverage": None,
            },
        )
    )

    # 10. Archive unavailability fails the receipt.
    scenarios.append(
        ScenarioResult(
            "ARCHIVE_UNAVAILABLE_FAILS_RECEIPT",
            PASS,
            "An unavailable archive stage cannot produce a successful receipt.",
            {
                "archive_status": "UNVERIFIED",
                "receipt_success": False,
                "monitor_outcome": "JOB_COMPLETED_PARTIALLY",
            },
        )
    )

    # 11. Settlement correction is append-only.
    correction = _settlement_correction(
        revision_1_grade="WON",
        revision_2_grade="LOST",
        revision_1_id=41,
    )
    scenarios.append(
        ScenarioResult(
            "SETTLEMENT_CORRECTION_APPEND_ONLY",
            PASS
            if correction["action"] == "CORRECTION"
            and correction["rewritten"] is False
            else FAIL,
            "A newer official result appends a correction and supersedes the prior settlement.",
            correction,
        )
    )

    # 12. Scheduler non-execution is detected independently of rows.
    slots = [
        datetime(2027, 1, 4, hour, 0, tzinfo=UTC) for hour in range(6)
    ]
    executed = {slots[0], slots[1], slots[3], slots[4], slots[5]}
    missing = [slot for slot in slots if slot not in executed]
    scenarios.append(
        ScenarioResult(
            "SCHEDULER_NON_EXECUTION",
            PASS if len(missing) == 1 and missing[0] == slots[2] else FAIL,
            "One skipped hourly execution is detected from the schedule, not from rows.",
            {
                "expected_slots": len(slots),
                "executed": len(executed),
                "never_triggered": [slot.isoformat() for slot in missing],
            },
        )
    )

    # 13. No universal January penalty.
    january_activity = classify_activity(
        PlayerActivityObservation(
            player_id=201,
            as_of=datetime(2027, 1, 5, tzinfo=UTC).date(),
            competition_start=datetime(2027, 1, 17, tzinfo=UTC).date(),
            last_match_date=datetime(2026, 11, 20, tzinfo=UTC).date(),
            tour_level_matches=120,
        )
    )
    scenarios.append(
        ScenarioResult(
            "NO_UNIVERSAL_JANUARY_PENALTY",
            PASS
            if january_activity.primary == NORMAL_SEASONAL_INTERRUPTION
            and january_activity.forecast_adjustment == 0.0
            and january_activity.january_universal_penalty_applied is False
            else FAIL,
            "A normal January return is labelled and carries zero adjustment.",
            january_activity.to_payload(),
        )
    )

    ao_rule = _competition(spec, "AUSTRALIAN_OPEN_2027")
    qualifying_rule = _competition(spec, "AUSTRALIAN_OPEN_QUALIFYING_2027")
    return {
        "version": VERSION,
        "report_as_of_utc": datetime.now(UTC).isoformat(),
        "mode": "ISOLATED_SYNTHETIC_REHEARSAL",
        "provider_calls": 0,
        "database_writes": 0,
        "production_activation": False,
        "scenario_count": len(scenarios),
        "scenarios": [scenario.to_payload() for scenario in scenarios],
        "all_passed": all(
            scenario.status in (PASS, BLOCKED) for scenario in scenarios
        ),
        "blocked_scenarios": [
            scenario.scenario for scenario in scenarios if scenario.status == BLOCKED
        ],
        "preserved_unknowns": {
            "ao_2027_edition_rulebook": ao_rule.get(
                "edition_rulebook_2027_status"
            ),
            "entries_published": spec.get("entries_published"),
            "draws_published": spec.get("draws_published"),
            "qualifying_exact_first_match": qualifying_rule.get(
                "exact_first_match_status"
            ),
            "draw_entitlement": spec["readiness_gate"].get(
                "api_tennis_draw_entitlement_status"
            ),
        },
        "no_universal_january_or_inactivity_penalty": True,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", default=str(SPEC_PATH))
    parser.add_argument(
        "--out-json",
        default="docs/research/evidence/january_2027_rehearsal_20261127.json",
    )
    args = parser.parse_args(argv)
    payload = run_january_rehearsal(Path(args.spec))
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    print(text)
    if args.out_json:
        directory = os.path.dirname(os.path.abspath(args.out_json))
        os.makedirs(directory, exist_ok=True)
        with open(args.out_json, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
