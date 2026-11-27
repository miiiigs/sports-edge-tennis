"""Independent coverage waterfall with explicit unknown stages.

The waterfall has two distinct populations that must never be merged:

1. the **fixture track** - official eligible fixtures, discovered, mapped,
   evaluated and settled fixtures;
2. the **selection track** - selected, independently archived, published,
   bookmaker-qualified and settled selections.

A selection is a model decision, not a fixture. A fixture can be evaluated and
settled without being selected. Counting one as the other inflates coverage.

Unknown evidence is never converted to a negative or a zero denominator. When no
licensed complete official export exists, external coverage is ``None`` with an
explicit ``UNKNOWN_*`` status; an internal ratio must never be reported as
official coverage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

FIXTURE_STAGES: tuple[str, ...] = (
    "DISCOVERED",
    "MAPPED",
    "EVALUATED",
)

SELECTION_STAGES: tuple[str, ...] = (
    "SELECTED",
    "INDEPENDENTLY_ARCHIVED",
    "PUBLISHED",
    "BOOKMAKER_QUALIFIED",
    "SETTLED_SELECTION",
)

EXTERNAL_COVERAGE_UNKNOWN = "UNKNOWN_NO_LICENSED_OFFICIAL_EXPORT"
EXTERNAL_COVERAGE_COMPLETE = "LICENSED_COMPLETE_EXPORT"
EXTERNAL_COVERAGE_PARTIAL = "LICENSED_PARTIAL_EXPORT"


@dataclass(frozen=True)
class StageCount:
    stage: str
    track: str
    count: int | None
    previous_stage: str | None
    missing_from_previous: int | None
    coverage_of_previous: float | None
    coverage_of_official: float | None
    missing_identified: tuple[str, ...]
    missing_unidentified_count: int | None
    unknown_reason: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "track": self.track,
            "count": self.count,
            "previous_stage": self.previous_stage,
            "missing_from_previous": self.missing_from_previous,
            "coverage_of_previous": self.coverage_of_previous,
            "coverage_of_official": self.coverage_of_official,
            "missing_identified": list(self.missing_identified),
            "missing_unidentified_count": self.missing_unidentified_count,
            "unknown_reason": self.unknown_reason,
        }


def coverage_waterfall(
    records: Sequence[Mapping[str, object]],
    *,
    stages: Sequence[str],
    track: str,
    id_field: str = "fixture_id",
    official_total: int | None = None,
    official_export_status: str = EXTERNAL_COVERAGE_UNKNOWN,
    max_missing_ids: int = 50,
) -> dict[str, object]:
    """Compute a strictly nested waterfall and record every missing stage.

    ``records`` carry a boolean or ``None`` for each stage. ``None`` means the
    evidence is unavailable and breaks the chain from that point: downstream
    counts are reported as ``None`` rather than as a smaller number.
    """

    if not stages:
        raise ValueError("stages must not be empty")
    if official_total is not None and official_total < 0:
        raise ValueError("official_total must not be negative")
    if official_export_status == EXTERNAL_COVERAGE_COMPLETE:
        if official_total is None:
            raise ValueError("a complete official export requires a total")
    elif official_export_status == EXTERNAL_COVERAGE_UNKNOWN:
        official_total = None

    identified: list[str] = []
    for index, record in enumerate(records):
        identifier = record.get(id_field)
        if identifier is None:
            identifier = f"UNIDENTIFIED:{index}"
        elif str(identifier) in identified:
            raise ValueError(f"duplicate {id_field} {identifier!r}")
        identified.append(str(identifier))

    unknown_at: dict[str, str] = {}
    for stage in stages:
        for index, record in enumerate(records):
            value = record.get(stage)
            if value is not None and not isinstance(value, bool):
                raise ValueError(
                    f"stage {stage!r} must be bool or None, got {type(value).__name__}"
                )
            if value is None and stage not in unknown_at:
                unknown_at[stage] = "STAGE_EVIDENCE_UNAVAILABLE"

    # A known True at a later stage requires the earlier stages to be known True.
    for position, stage in enumerate(stages[1:], start=1):
        earlier = stages[:position]
        for index, record in enumerate(records):
            if record.get(stage) is True:
                for previous in earlier:
                    if record.get(previous) is not True:
                        raise ValueError(
                            f"record {identified[index]} is {stage}=True but "
                            f"{previous}!={record.get(previous)!r}"
                        )

    counts: list[int | None] = []
    chain_broken = False
    for stage in stages:
        if chain_broken or stage in unknown_at:
            counts.append(None)
            chain_broken = True
            continue
        counts.append(sum(1 for record in records if record[stage] is True))

    stage_counts: list[StageCount] = []
    for position, stage in enumerate(stages):
        count = counts[position]
        previous_stage = stages[position - 1] if position else None
        previous_count = counts[position - 1] if position else official_total
        missing = None
        coverage_previous = None
        coverage_official = None
        missing_ids: tuple[str, ...] = ()
        missing_unidentified = None
        if count is not None:
            if official_total is not None and position == 0:
                previous_count = official_total
            if previous_count is not None:
                missing = previous_count - count
                if missing < 0:
                    raise ValueError(
                        f"stage {stage!r} exceeds its predecessor {previous_stage!r}"
                    )
                if previous_count > 0:
                    coverage_previous = count / previous_count
                if official_total:
                    coverage_official = count / official_total
                candidates = [
                    identifier
                    for identifier, record in zip(identified, records)
                    if record[stage] is not True
                    and (position == 0 or record[stages[position - 1]] is True)
                ]
                missing_ids = tuple(candidates[:max_missing_ids])
                missing_unidentified = max(0, missing - len(candidates))
        stage_counts.append(
            StageCount(
                stage=stage,
                track=track,
                count=count,
                previous_stage=previous_stage,
                missing_from_previous=missing,
                coverage_of_previous=coverage_previous,
                coverage_of_official=coverage_official,
                missing_identified=missing_ids,
                missing_unidentified_count=missing_unidentified,
                unknown_reason=unknown_at.get(stage),
            )
        )

    external_coverage_known = (
        official_export_status == EXTERNAL_COVERAGE_COMPLETE
        and official_total is not None
        and counts[0] is not None
    )
    return {
        "track": track,
        "stage_order": list(stages),
        "official_export_status": official_export_status,
        "official_total": official_total,
        "official_total_is_known": official_total is not None,
        "external_coverage": (
            counts[-1] / official_total
            if external_coverage_known and official_total
            else None
        ),
        "external_coverage_status": (
            "KNOWN" if external_coverage_known else EXTERNAL_COVERAGE_UNKNOWN
        ),
        "stages": [item.to_payload() for item in stage_counts],
        "partial_internal_ratio_is_not_official_coverage": True,
    }


def coverage_from_totals(
    totals: Mapping[str, object],
    *,
    stages: Sequence[str],
    track: str,
    official_total: int | None = None,
    official_export_status: str = EXTERNAL_COVERAGE_UNKNOWN,
) -> dict[str, object]:
    """Count-based waterfall for committed aggregate evidence.

    ``None`` for any stage means the evidence is unavailable and breaks the
    chain; downstream counts and coverage become ``None``.
    """

    if not stages:
        raise ValueError("stages must not be empty")
    if official_export_status == EXTERNAL_COVERAGE_COMPLETE and official_total is None:
        raise ValueError("a complete official export requires a total")
    if official_export_status == EXTERNAL_COVERAGE_UNKNOWN:
        official_total = None

    counts: list[int | None] = []
    broken = False
    unknown_at: dict[str, str] = {}
    for stage in stages:
        if broken or stage not in totals or totals[stage] is None:
            counts.append(None)
            if stage not in unknown_at:
                unknown_at[stage] = "STAGE_EVIDENCE_UNAVAILABLE"
            broken = True
            continue
        value = int(totals[stage])
        if value < 0:
            raise ValueError(f"negative count for {stage!r}")
        counts.append(value)

    stages_payload: list[dict[str, object]] = []
    for position, stage in enumerate(stages):
        count = counts[position]
        previous_stage = stages[position - 1] if position else None
        previous_count = counts[position - 1] if position else official_total
        missing = None
        coverage_previous = None
        coverage_official = None
        if count is not None and previous_count is not None:
            missing = previous_count - count
            if missing < 0:
                raise ValueError(
                    f"stage {stage!r} exceeds its predecessor {previous_stage!r}"
                )
            if previous_count > 0:
                coverage_previous = count / previous_count
            if official_total:
                coverage_official = count / official_total
        stages_payload.append(
            {
                "stage": stage,
                "track": track,
                "count": count,
                "previous_stage": previous_stage,
                "missing_from_previous": missing,
                "coverage_of_previous": coverage_previous,
                "coverage_of_official": coverage_official,
                "missing_identified": [],
                "missing_unidentified_count": missing,
                "unknown_reason": unknown_at.get(stage),
            }
        )

    external_known = (
        official_export_status == EXTERNAL_COVERAGE_COMPLETE
        and official_total is not None
        and counts[-1] is not None
    )
    return {
        "track": track,
        "stage_order": list(stages),
        "official_eligible": official_total,
        "official_export_status": official_export_status,
        "official_total": official_total,
        "official_total_is_known": official_total is not None,
        "external_coverage": (
            counts[-1] / official_total
            if external_known and official_total
            else None
        ),
        "external_coverage_status": (
            "KNOWN" if external_known else EXTERNAL_COVERAGE_UNKNOWN
        ),
        "stages": stages_payload,
        "partial_internal_ratio_is_not_official_coverage": True,
    }


def fixture_track(
    records: Sequence[Mapping[str, object]],
    *,
    official_total: int | None,
    official_export_status: str = EXTERNAL_COVERAGE_UNKNOWN,
) -> dict[str, object]:
    return coverage_waterfall(
        records,
        stages=FIXTURE_STAGES,
        track="FIXTURE",
        id_field="fixture_id",
        official_total=official_total,
        official_export_status=official_export_status,
    )


def selection_track(
    records: Sequence[Mapping[str, object]],
    *,
    max_missing_ids: int = 50,
) -> dict[str, object]:
    return coverage_waterfall(
        records,
        stages=SELECTION_STAGES,
        track="SELECTION",
        id_field="selection_id",
        official_total=None,
        official_export_status=EXTERNAL_COVERAGE_UNKNOWN,
        max_missing_ids=max_missing_ids,
    )


def aggregate_fixture_totals(
    aggregate_rows: Iterable[Mapping[str, object]],
) -> dict[str, int]:
    """Map committed daily aggregate rows to fixture-track totals.

    The committed rows do not contain fixture identifiers or independent
    archive/publication ledgers. Only counts that genuinely exist are summed.
    """

    totals = {
        "DISCOVERED": 0,
        "MAPPED": 0,
        "PIT_VISIBLE": 0,
        "EVALUATED": 0,
        "SELECTED": 0,
        "SETTLED_FIXTURE": 0,
    }
    for row in aggregate_rows:
        stored = int(row.get("stored_matches") or 0)
        no_source = int(row.get("no_provider_source_id") or 0)
        totals["DISCOVERED"] += stored
        totals["MAPPED"] += max(0, stored - no_source)
        totals["PIT_VISIBLE"] += int(row.get("pit_visible") or 0)
        totals["EVALUATED"] += int(row.get("a1_predicted") or 0)
        totals["SELECTED"] += int(row.get("manager_consensus") or 0)
        totals["SETTLED_FIXTURE"] += int(row.get("settled_completed") or 0)
    return totals


def summarise_counts(
    rows: Iterable[Mapping[str, object]],
    fields: Sequence[str],
) -> dict[str, int]:
    totals = {field: 0 for field in fields}
    for row in rows:
        for field in fields:
            totals[field] += int(row.get(field) or 0)
    return totals


def selection_evidence_status() -> dict[str, object]:
    """State exactly which selection stages the repository can currently prove.

    The committed coverage evidence contains Manager consensus and settled
    fixture counts, but no per-selection independent archive, publication or
    bookmaker qualification ledger. Those stages are unknown, not zero.
    """

    return {
        "SELECTED": "COUNTED_FROM_MANAGER_CONSENSUS",
        "INDEPENDENTLY_ARCHIVED": "UNKNOWN_NOT_IN_COMMITTED_EVIDENCE",
        "PUBLISHED": "UNKNOWN_NOT_IN_COMMITTED_EVIDENCE",
        "BOOKMAKER_QUALIFIED": "UNKNOWN_NOT_IN_COMMITTED_EVIDENCE",
        "SETTLED_SELECTION": "UNKNOWN_NOT_IN_COMMITTED_EVIDENCE",
    }
