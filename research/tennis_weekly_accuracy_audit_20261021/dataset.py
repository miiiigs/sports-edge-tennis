"""Typed loading and settlement policy for frozen forward-issued picks.

The input is the JSON Lines artifact produced by the read-only production
extractor (one row per population and canonical match, plus a final manifest
line). Nothing here opens a database connection.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

MANAGER_CONSENSUS = "manager_consensus"
WIDE_SPREAD_TRACKING = "wide_spread_tracking"
VALUE_BET = "value_bet"
VETOED = "vetoed"

PICK_POPULATIONS = frozenset({MANAGER_CONSENSUS, WIDE_SPREAD_TRACKING})
MANAGER_CONSENSUS_PICK = "MANAGER_CONSENSUS"
A1_A2_WIDE_SPREAD_PICK = "A1_A2_WIDE_SPREAD"

WON = "WON"
LOST = "LOST"
VOID = "VOID"
PENDING = "PENDING"
CONFLICTED = "CONFLICTED"

TERMINAL_VOID_STATUSES = frozenset({"RETIRED", "WALKOVER", "CANCELLED"})
SCORED_STATUSES = frozenset({WON, LOST})


class DatasetError(ValueError):
    """Raised when a frozen dataset row cannot be interpreted safely."""


@dataclass(frozen=True)
class ValueBet:
    decision_id: int | None
    edge_pct: float | None
    is_value_bet: bool
    is_vetoed: bool
    price: float | None
    probability: float | None
    published_at: datetime | None
    veto_codes: tuple[str, ...]


@dataclass(frozen=True)
class Pick:
    population: str
    match_id: int
    tournament: str
    tournament_id: int | None
    tier: str | None
    surface: str | None
    round_code: str | None
    qualifying_flag: bool
    selected_side: int
    picked_player_name: str
    opponent_player_name: str
    probability: float
    picked_at: datetime
    match_start_at: datetime | None
    prediction_finalized_at: datetime | None
    outcome_status: str | None
    winner_side: int | None
    result_states_disagree: bool
    probability_spread: float | None
    model_minus_sharp: float | None
    sharp_picked_probability: float | None
    sharp_favourite_side: int | None
    tournament_name_has_qualification: bool
    surface_missing: bool
    days_since_last_match: int | None
    prior_surface_matches: int | None
    prior_stat_matches: int | None
    value_bet: ValueBet
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def result_status(self) -> str:
        if self.result_states_disagree:
            return CONFLICTED
        if self.outcome_status in TERMINAL_VOID_STATUSES:
            return VOID
        if self.outcome_status == "COMPLETED" and self.winner_side in (1, 2):
            return WON if self.winner_side == self.selected_side else LOST
        return PENDING

    @property
    def scored(self) -> bool:
        return self.result_status in SCORED_STATUSES

    @property
    def won(self) -> bool:
        return self.result_status == WON

    @property
    def target(self) -> int:
        return 1 if self.won else 0

    @property
    def qualification_mismatch(self) -> bool:
        """Canonical tournament name and qualification flag disagree."""

        return bool(self.qualifying_flag) != bool(
            self.tournament_name_has_qualification
        )


@dataclass(frozen=True)
class AlignedPick:
    """The dashboard's current-rule aligned Model Pick for one match."""

    pick: Pick
    pick_type: str

    @property
    def probability(self) -> float:
        return self.pick.probability

    @property
    def match_id(self) -> int:
        return self.pick.match_id

    @property
    def picked_at(self) -> datetime:
        return self.pick.picked_at


@dataclass(frozen=True)
class Dataset:
    picks: tuple[Pick, ...]
    manifest: dict[str, Any]
    sha256: str
    path: Path
    populations: dict[str, int]

    def by_population(self, population: str) -> tuple[Pick, ...]:
        return tuple(p for p in self.picks if p.population == population)


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise DatasetError(f"invalid timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise DatasetError(f"invalid numeric value {value!r}") from exc


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise DatasetError(f"invalid integer value {value!r}") from exc


def load_dataset(path: str | Path) -> Dataset:
    source = Path(path)
    picks: list[Pick] = []
    manifest: dict[str, Any] = {}
    digest = hashlib.sha256()
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            digest.update(stripped.encode("utf-8"))
            digest.update(b"\n")
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"line {line_number} is not JSON") from exc
            if row.get("_manifest"):
                manifest = row
                continue
            picks.append(_parse_pick(row, line_number))
    if not picks:
        raise DatasetError("dataset contains no pick rows")
    counts: dict[str, int] = {}
    for pick in picks:
        counts[pick.population] = counts.get(pick.population, 0) + 1
    return Dataset(
        picks=tuple(picks),
        manifest=manifest,
        sha256=digest.hexdigest(),
        path=source,
        populations=counts,
    )


def _parse_pick(row: dict[str, Any], line_number: int) -> Pick:
    population = str(row.get("population") or "")
    if population not in PICK_POPULATIONS | {VALUE_BET, VETOED}:
        raise DatasetError(f"line {line_number} has unknown population {population!r}")
    if population == VALUE_BET:
        value_bet = _value_bet_from_decision(row)
    else:
        value_bet = _value_bet_from_nested(row.get("value_bet") or {})
    data_quality = row.get("data_quality") or {}
    history = (row.get("history") or {}).get("picked") or {}
    market = row.get("market") or {}
    try:
        selected_side = int(row["selected_side"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DatasetError(f"line {line_number} has no selected side") from exc
    probability = _float(row.get("consensus_probability"))
    if probability is None:
        raise DatasetError(f"line {line_number} has no probability")
    picked_at = _parse_datetime(row.get("picked_at") or row.get("as_of"))
    if picked_at is None:
        raise DatasetError(f"line {line_number} has no pick timestamp")
    return Pick(
        population=population,
        match_id=int(row["match_id"]),
        tournament=str(row.get("tournament") or ""),
        tournament_id=_int(row.get("tournament_id")),
        tier=row.get("tier"),
        surface=row.get("surface"),
        round_code=row.get("round_code"),
        qualifying_flag=bool(row.get("qualifying_flag")),
        selected_side=selected_side,
        picked_player_name=str(row.get("picked_player_name") or ""),
        opponent_player_name=str(row.get("opponent_player_name") or ""),
        probability=probability,
        picked_at=picked_at,
        match_start_at=_parse_datetime(row.get("match_start_at")),
        prediction_finalized_at=_parse_datetime(row.get("prediction_finalized_at")),
        outcome_status=row.get("outcome_status"),
        winner_side=_int(row.get("winner_side")),
        result_states_disagree=bool(data_quality.get("result_states_disagree")),
        probability_spread=_float(row.get("probability_spread")),
        model_minus_sharp=_float(market.get("model_minus_sharp")),
        sharp_picked_probability=_float(market.get("sharp_picked_probability")),
        sharp_favourite_side=_int(market.get("sharp_favourite_side")),
        tournament_name_has_qualification=bool(
            data_quality.get("tournament_name_has_qualification")
        ),
        surface_missing=bool(data_quality.get("surface_missing")),
        days_since_last_match=_int(history.get("days_since_last_match")),
        prior_surface_matches=_int(history.get("prior_surface_matches")),
        prior_stat_matches=_int(history.get("prior_stat_matches")),
        value_bet=value_bet,
        raw=row,
    )


def _value_bet_from_nested(payload: dict[str, Any]) -> ValueBet:
    return ValueBet(
        decision_id=_int(payload.get("decision_id")),
        edge_pct=_float(payload.get("edge_pct")),
        is_value_bet=bool(payload.get("is_value_bet")),
        is_vetoed=bool(payload.get("is_vetoed")),
        price=_float(payload.get("price")),
        probability=_float(payload.get("probability")),
        published_at=_parse_datetime(payload.get("published_at")),
        veto_codes=tuple(payload.get("veto_codes") or ()),
    )


def _value_bet_from_decision(row: dict[str, Any]) -> ValueBet:
    return ValueBet(
        decision_id=_int(row.get("decision_id")),
        edge_pct=_float(row.get("edge_pct")),
        is_value_bet=str(row.get("decision_status") or "").upper() == "BET",
        is_vetoed=str(row.get("decision_status") or "").upper() == "VETOED",
        price=_float(row.get("market_decimal_price")),
        probability=_float(row.get("consensus_probability")),
        published_at=_parse_datetime(row.get("published_at")),
        veto_codes=tuple(
            code
            for code in str(row.get("triggered_codes") or "").replace(",", " ").split()
            if code
        ),
    )


def local_date(pick: Pick, timezone_name: str) -> date:
    """Calendar day used by the dashboard: match start, else pick time."""

    moment = pick.match_start_at or pick.picked_at
    return moment.astimezone(ZoneInfo(timezone_name)).date()


def in_window(
    picks: Iterable[Pick], start: date, end: date, timezone_name: str
) -> tuple[Pick, ...]:
    return tuple(p for p in picks if start <= local_date(p, timezone_name) <= end)


def aligned_picks(
    picks: Iterable[Pick], *, minimum_probability: float = 0.60
) -> tuple[AlignedPick, ...]:
    """Earliest qualifying pick per match across Manager and tracking sides.

    Mirrors ``aligned_production_model_picks_cte``: Manager CONSENSUS at or
    above the floor, plus A1/A2 same-side tracking when the Manager rejected on
    spread and A3 abstained. The earliest qualifying evaluation per canonical
    match wins; a later, more confident rerun never replaces it.
    """

    best: dict[int, AlignedPick] = {}
    for pick in picks:
        if pick.population not in PICK_POPULATIONS:
            continue
        if pick.probability < minimum_probability:
            continue
        pick_type = (
            MANAGER_CONSENSUS_PICK
            if pick.population == MANAGER_CONSENSUS
            else A1_A2_WIDE_SPREAD_PICK
        )
        candidate = AlignedPick(pick=pick, pick_type=pick_type)
        current = best.get(pick.match_id)
        if current is None or (candidate.picked_at, candidate.pick_type) < (
            current.picked_at,
            current.pick_type,
        ):
            best[pick.match_id] = candidate
    return tuple(
        sorted(
            best.values(), key=lambda item: (item.pick.match_start_at, item.match_id)
        )
    )


def manager_primary(
    dataset: Dataset, *, minimum_probability: float = 0.60
) -> tuple[Pick, ...]:
    return tuple(
        pick
        for pick in dataset.by_population(MANAGER_CONSENSUS)
        if pick.probability >= minimum_probability
    )


def dataset_manifest_summary(dataset: Dataset) -> dict[str, Any]:
    return {
        "path": dataset.path.name,
        "sha256": dataset.sha256,
        "rows": len(dataset.picks),
        "populations": dataset.populations,
        "extract_cutoff": dataset.manifest.get("extract_cutoff"),
        "extracted_at": dataset.manifest.get("extracted_at"),
        "row_sha256": dataset.manifest.get("row_sha256"),
        "row_count": dataset.manifest.get("row_count"),
        "dataset_schema_version": dataset.manifest.get("dataset_schema_version"),
    }


__all__ = [
    "A1_A2_WIDE_SPREAD_PICK",
    "CONFLICTED",
    "LOST",
    "MANAGER_CONSENSUS",
    "MANAGER_CONSENSUS_PICK",
    "PENDING",
    "VALUE_BET",
    "VETOED",
    "VOID",
    "WIDE_SPREAD_TRACKING",
    "WON",
    "AlignedPick",
    "Dataset",
    "DatasetError",
    "Pick",
    "aligned_picks",
    "dataset_manifest_summary",
    "in_window",
    "load_dataset",
    "local_date",
    "manager_primary",
]
