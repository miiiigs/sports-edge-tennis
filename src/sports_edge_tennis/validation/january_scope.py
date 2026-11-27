"""January 2027 scope and readiness validation.

The specification is deliberately a *scope* document, not a fixture list. It
records only tournaments whose dates and formats are supported by a cited
source, marks every unpublished entry list and draw as unpublished, and states
the exact uncertainties (2027 edition rulebook, AO qualifying first match,
API-Tennis draw entitlement).

Nothing here creates a fixture, an entry or a forecast. Readiness is a gate.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Mapping

SPEC_PATH = Path("specs/january_2027_scope_20261127.json")

SUPPORTED_TOURS = frozenset({"ATP_MAIN", "CHALLENGER"})
SUPPORTED_SURFACES = frozenset({"HARD", "CLAY", "GRASS", "CARPET"})
MAIN_DRAW_BEST_OF = 5
QUALIFYING_BEST_OF = 3
DECIDER_RULE = "TB10_AT_6_6"


class JanuaryScopeError(ValueError):
    """Raised when the January scope makes an unsupported or invented claim."""


def load_scope(path: str | Path = SPEC_PATH) -> dict[str, object]:
    with open(path, encoding="utf-8") as handle:
        spec = json.load(handle)
    if not isinstance(spec, dict):
        raise JanuaryScopeError("scope specification must be a JSON object")
    validate_scope(spec)
    return spec


def validate_scope(spec: Mapping[str, object]) -> None:
    if not str(spec.get("version", "")).startswith("JANUARY_2027_SCOPE_"):
        raise JanuaryScopeError("unexpected scope version")
    verified = _aware_datetime(spec.get("verified_as_of_utc"))
    if verified.year < 2026:
        raise JanuaryScopeError("scope must be verified in 2026 or later")
    if spec.get("time_basis") != "TOURNAMENT_LOCAL_DATE":
        raise JanuaryScopeError("time basis must be explicit")
    if spec.get("utc_conversion_required") is not True:
        raise JanuaryScopeError("UTC conversion must be required")
    if spec.get("entries_published") is not False:
        raise JanuaryScopeError("this scope must not claim published entries")
    if spec.get("draws_published") is not False:
        raise JanuaryScopeError("this scope must not claim published draws")
    if spec.get("fixtures") not in ([], None):
        raise JanuaryScopeError("this scope must not invent fixtures")

    sources = spec.get("sources")
    if not isinstance(sources, Mapping) or not sources:
        raise JanuaryScopeError("sources are required")
    for source_id, source in sources.items():
        if not isinstance(source, Mapping):
            raise JanuaryScopeError(f"source {source_id!r} must be an object")
        if not source.get("url"):
            raise JanuaryScopeError(f"source {source_id!r} needs a URL")

    competitions = spec.get("competitions")
    if not isinstance(competitions, list) or not competitions:
        raise JanuaryScopeError("competitions are required")
    keys: set[str] = set()
    eligible_starts: list[date] = []
    for competition in competitions:
        key = str(competition.get("key") or "")
        if not key or key in keys:
            raise JanuaryScopeError(f"duplicate or empty competition key {key!r}")
        keys.add(key)
        _validate_dates(competition)
        source_ids = competition.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids:
            raise JanuaryScopeError(f"{key} must cite at least one source")
        for source_id in source_ids:
            if source_id not in sources:
                raise JanuaryScopeError(f"{key} cites unknown source {source_id!r}")
        if competition.get("singles_eligible") is True:
            _validate_eligible(competition, key)
            eligible_starts.append(
                date.fromisoformat(str(competition["scheduled_start"]))
            )
        else:
            if not competition.get("exclusion_reason"):
                raise JanuaryScopeError(f"{key} must state an exclusion reason")

    unsupported = spec.get("unsupported_competitions")
    if not isinstance(unsupported, list):
        raise JanuaryScopeError("unsupported_competitions must be a list")
    for competition in unsupported:
        if competition.get("source_ids"):
            for source_id in competition["source_ids"]:
                if source_id not in sources:
                    raise JanuaryScopeError(
                        f"unsupported {competition.get('key')} cites unknown source"
                    )
        elif not competition.get("verification_status"):
            raise JanuaryScopeError(
                f"unsupported {competition.get('key')} needs an explicit "
                "verification status when it has no source"
            )

    gate = spec.get("readiness_gate")
    if not isinstance(gate, Mapping):
        raise JanuaryScopeError("readiness_gate is required")
    if gate.get("readiness_must_precede_first_eligible_fixture") is not True:
        raise JanuaryScopeError("readiness must precede the first fixture")
    if eligible_starts:
        expected = min(eligible_starts).isoformat()
        if gate.get("first_eligible_date") != expected:
            raise JanuaryScopeError(
                f"first_eligible_date must be {expected}, "
                f"got {gate.get('first_eligible_date')!r}"
            )
    if gate.get("owner_approval_status") != "NOT_REQUESTED":
        raise JanuaryScopeError("owner approval is not requested at this checkpoint")
    if gate.get("api_tennis_draw_entitlement_status") != (
        "UNVERIFIED_NO_PROVIDER_CALL"
    ):
        raise JanuaryScopeError("draw entitlement must remain unverified")


def _validate_dates(competition: Mapping[str, object]) -> None:
    try:
        start = date.fromisoformat(str(competition["scheduled_start"]))
        end = date.fromisoformat(str(competition["scheduled_end"]))
    except (KeyError, ValueError) as exc:
        raise JanuaryScopeError(
            f"{competition.get('key')} has an invalid scheduled date"
        ) from exc
    if end < start:
        raise JanuaryScopeError(f"{competition.get('key')} ends before it starts")
    if start.year != 2027:
        raise JanuaryScopeError("every January 2027 competition must start in 2027")


def _validate_eligible(competition: Mapping[str, object], key: str) -> None:
    if competition.get("tour_scope") not in SUPPORTED_TOURS:
        raise JanuaryScopeError(f"{key} outside supported tours")
    if competition.get("surface") not in SUPPORTED_SURFACES:
        raise JanuaryScopeError(f"{key} has an unsupported surface")
    best_of = competition.get("best_of_sets")
    tier = competition.get("competition_tier")
    if tier == "GRAND_SLAM":
        if best_of != MAIN_DRAW_BEST_OF:
            raise JanuaryScopeError(f"{key} Grand Slam main draw must be BO5")
    elif tier == "GRAND_SLAM_QUALIFYING":
        if best_of != QUALIFYING_BEST_OF:
            raise JanuaryScopeError(f"{key} Grand Slam qualifying must be BO3")
        if competition.get("exact_first_match_utc") is not None:
            raise JanuaryScopeError(
                f"{key} must not invent an exact first-match time"
            )
    elif tier in ("ATP_250", "ATP_500", "CHALLENGER_50", "CHALLENGER_75",
                  "CHALLENGER_100", "CHALLENGER_125"):
        if best_of != 3:
            raise JanuaryScopeError(f"{key} must be BO3")
    else:
        raise JanuaryScopeError(f"{key} has an unsupported competition tier")
    if competition.get("entry_list_status") != "NOT_PUBLISHED":
        raise JanuaryScopeError(f"{key} must record entries as unpublished")
    if competition.get("draw_status") != "NOT_PUBLISHED":
        raise JanuaryScopeError(f"{key} must record the draw as unpublished")


def eligible_competitions(
    spec: Mapping[str, object],
) -> list[Mapping[str, object]]:
    return [
        competition
        for competition in spec["competitions"]  # type: ignore[index]
        if competition.get("singles_eligible") is True
    ]


def first_eligible_start(spec: Mapping[str, object]) -> date:
    starts = [
        date.fromisoformat(str(competition["scheduled_start"]))
        for competition in eligible_competitions(spec)
    ]
    if not starts:
        raise JanuaryScopeError("no eligible competition")
    return min(starts)


def qualifying_window(spec: Mapping[str, object]) -> tuple[date, date]:
    for competition in eligible_competitions(spec):
        if competition.get("competition_tier") == "GRAND_SLAM_QUALIFYING":
            return (
                date.fromisoformat(str(competition["scheduled_start"])),
                date.fromisoformat(str(competition["scheduled_end"])),
            )
    raise JanuaryScopeError("no qualifying competition")


def assert_readiness_precedes(
    spec: Mapping[str, object], readiness_completed_at: datetime
) -> None:
    """Readiness must be complete before the first independently eligible match."""

    if readiness_completed_at.tzinfo is None or readiness_completed_at.utcoffset() is None:
        raise JanuaryScopeError("readiness timestamp must be timezone-aware")
    first = datetime.combine(
        first_eligible_start(spec), datetime.min.time(), tzinfo=readiness_completed_at.tzinfo
    )
    if readiness_completed_at >= first:
        raise JanuaryScopeError(
            "readiness must precede the first eligible January fixture"
        )


def readiness_matrix(spec: Mapping[str, object]) -> list[dict[str, object]]:
    ao = next(
        (
            competition
            for competition in spec["competitions"]  # type: ignore[index]
            if competition.get("key") == "AUSTRALIAN_OPEN_2027"
        ),
        None,
    )
    qualifying = next(
        (
            competition
            for competition in spec["competitions"]  # type: ignore[index]
            if competition.get("competition_tier") == "GRAND_SLAM_QUALIFYING"
        ),
        None,
    )
    matrix = [
        _item(
            "Authoritative 2027 schedule",
            "PASS",
            "AO 2027 dates and the ATP January weeks are cited to ausopen.com "
            "and the ATP calendar.",
            ["S1", "S2"],
        ),
        _item(
            "Men's ATP and Challenger singles scope",
            "PASS",
            "Only ATP_MAIN and CHALLENGER men's singles are eligible; team and "
            "exhibition events are explicitly excluded.",
            ["S2"],
        ),
        _item(
            "Main-draw and qualifying separation",
            "PASS",
            "Qualifying is a separate competition, classified BO3 and not enabled "
            "for forecasting.",
            ["S1", "S3", "S5"],
        ),
        _item(
            "Best-of-five main draw and best-of-three qualifying",
            "PASS",
            "AO main draw is BO5; AO qualifying and ATP/Challenger events are BO3.",
            ["S2", "S3", "S5"],
        ),
        _item(
            "Deciding-set tiebreak rule",
            "PASS",
            "AO main draw uses a 10-point tiebreak at 6-6.",
            ["S5"],
        ),
        _item(
            "UTC timestamps and original/revised/actual starts",
            "PASS_OPERATIONAL_CONTRACT",
            "Tournament-local dates are converted to UTC; fixture lifecycle keeps "
            "original, revised and actual starts separately.",
            ["S1"],
        ),
        _item(
            "Participant replacements, lucky losers, withdrawals, cancellations",
            "PARTIAL",
            "The lifecycle model represents them, but no 2027 draw or entry list "
            "exists yet and draw-diff detection is not implemented.",
            ["S1"],
        ),
        _item(
            "Official and bookmaker settlement distinction",
            "PASS_CONTRACT_ONLY",
            "The settlement model keeps the official result and the bookmaker "
            "grading distinct; no bookmaker grading table exists.",
            [],
        ),
        _item(
            "2027 edition-specific AO rulebook",
            "BLOCKED",
            "No edition-specific 2027 rulebook was located; general format only.",
            ["S5"],
        ),
        _item(
            "Published 2027 entry lists and draws",
            "UNKNOWN_NOT_PUBLISHED",
            "No entry list or draw is claimed.",
            ["S1"],
        ),
        _item(
            "AO qualifying exact first match",
            "UNKNOWN",
            "Opening Week is 10-16 January; the ATP calendar lists qualifying in "
            "the week commencing 12 January; the official order of play is not "
            "published.",
            ["S1", "S3"],
        ),
        _item(
            "API-Tennis get_draw and include_qualification entitlement",
            "BLOCKED_UNVERIFIED",
            "No provider call was made. A single owner-authorised probe must be "
            "coordinated with PR #351.",
            [],
        ),
        _item(
            "Owner approval and controlled release",
            "BLOCKED_NOT_REQUESTED",
            "No deployment, activation or spend is requested here.",
            [],
        ),
    ]
    matrix.append(
        _item(
            "Readiness precedes the first eligible fixture",
            "SCHEDULED_GATE",
            f"Readiness must complete before {first_eligible_start(spec).isoformat()}.",
            [],
        )
    )
    if ao is not None and ao.get("best_of_sets") != 5:
        raise JanuaryScopeError("AO main draw must remain BO5")
    if qualifying is not None and qualifying.get("best_of_sets") != 3:
        raise JanuaryScopeError("AO qualifying must remain BO3")
    return matrix


def _item(
    item: str, status: str, detail: str, source_ids: list[str]
) -> dict[str, object]:
    return {
        "item": item,
        "status": status,
        "detail": detail,
        "source_ids": source_ids,
    }


def _aware_datetime(value: object) -> datetime:
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise JanuaryScopeError("timestamp must be timezone-aware")
    return moment
