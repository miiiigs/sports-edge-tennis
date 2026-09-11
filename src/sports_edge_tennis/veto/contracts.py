from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


VetoTriggerCode = Literal[
    "CURRENT_MATCH_NOT_SCHEDULED",
    "FIXTURE_FEED_STALE",
    "PREVIOUS_MATCH_RETIRED",
    "RETURN_FROM_6W_ABSENCE",
    "TIGHT_TURNAROUND_18H",
    "FIVE_SET_UNDER_24H",
    "ODDS_MOVED_AGAINST_15PCT",
]

VetoUnavailableCode = Literal[
    "INJURY_72H",
    "PREVIOUS_MATCH_MEDICAL_TIMEOUT",
    "EXACT_PREVIOUS_MATCH_END_TIME",
    "PREVIOUS_MATCH_DURATION",
    "ODDS_MOVE_BASELINE",
    "HISTORICAL_SOURCE_FRESHNESS_48H",
]


@dataclass(frozen=True)
class PreviousMatchEvidence:
    player_id: int
    player_side_in_target: int
    match_id: int
    match_start_at: datetime
    outcome_status: str
    player_retired: bool
    sets_won_player_1: int | None
    sets_won_player_2: int | None

    @property
    def completed_sets(self) -> int | None:
        if self.sets_won_player_1 is None or self.sets_won_player_2 is None:
            return None
        return self.sets_won_player_1 + self.sets_won_player_2


@dataclass(frozen=True)
class VetoContext:
    match_id: int
    prediction_run_id: int
    selected_side: int
    decision_as_of: datetime
    target_start_at: datetime
    current_match_status: str
    fixture_source_observed_at: datetime
    previous_matches: tuple[PreviousMatchEvidence, ...]
    sharp_move_against_pct: Decimal | None


@dataclass(frozen=True)
class VetoEvaluation:
    vetoed: bool
    triggered_codes: tuple[VetoTriggerCode, ...]
    unavailable_codes: tuple[VetoUnavailableCode, ...]
    evidence: tuple[dict[str, object], ...]

    @property
    def primary_trigger(self) -> VetoTriggerCode | None:
        return self.triggered_codes[0] if self.triggered_codes else None
