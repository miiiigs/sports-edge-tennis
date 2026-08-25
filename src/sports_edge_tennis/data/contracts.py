from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal


TourScope = Literal["ATP_MAIN", "CHALLENGER"]
CompetitionTier = Literal[
    "GRAND_SLAM",
    "MASTERS_1000",
    "ATP_TOUR",
    "ATP_500",
    "ATP_250",
    "CHALLENGER",
]
TennisSurface = Literal["HARD", "CLAY", "GRASS", "CARPET"]
MatchStatus = Literal["COMPLETED", "RETIRED", "WALKOVER", "CANCELLED"]
IdentityStatus = Literal["RESOLVED", "UNMATCHED", "AMBIGUOUS"]


@dataclass(frozen=True)
class SourceDocument:
    source_system: str
    source_name: str
    source_url: str | None
    canonical_source: str | None
    retrieval_source: str | None
    archive_revision: str | None
    upstream_revision: str | None
    source_license: str | None
    local_path: Path
    retrieved_at: datetime
    filename: str
    sha256: str
    size_bytes: int
    dataset_type: str
    season: int | None
    source_revision: str | None = None
    coverage_metadata: dict[str, object] = field(default_factory=dict)
    hash_recipe_version: str = "SE_SOURCE_HASH_V1"


@dataclass(frozen=True)
class PlayerReference:
    raw_name: str
    source_player_id: str | None = None


@dataclass(frozen=True)
class MatchStatLine:
    first_serves_in: int | None = None
    first_serve_points_won: int | None = None
    second_serve_points_won: int | None = None
    service_points_played: int | None = None
    return_points_won: int | None = None
    aces: int | None = None
    double_faults: int | None = None
    break_points_saved: int | None = None
    break_points_faced: int | None = None


@dataclass(frozen=True)
class SackmannMatchRecord:
    document: SourceDocument
    source_row_number: int
    source_match_id: str | None
    source_record_hash: str
    source_record_locator: str
    source_version_hash: str
    tournament_source_id: str | None
    tournament_name: str
    edition_name: str
    season_year: int
    tournament_start_date: date
    tournament_end_date: date | None
    surface: TennisSurface | None
    tour_scope: TourScope
    competition_tier: CompetitionTier
    round_code: str
    best_of_sets: int
    qualifying_flag: bool
    match_start_at: datetime | None
    match_source_observed_at: datetime
    match_as_of: datetime
    result_source_observed_at: datetime
    result_as_of: datetime
    winner: PlayerReference
    loser: PlayerReference
    scoreline_text: str | None
    outcome_status: MatchStatus
    winner_stats: MatchStatLine
    loser_stats: MatchStatLine
    raw_fields: dict[str, object]
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class OddsPriceRecord:
    document: SourceDocument
    source_row_number: int
    source_snapshot_id: str
    source_record_hash: str
    source_record_locator: str
    tournament_name: str
    event_date: date
    surface: TennisSurface | None
    round_code: str | None
    best_of_sets: int | None
    winner_name: str
    loser_name: str
    selection_name: str
    selection_role: Literal["WINNER", "LOSER"]
    bookmaker_code: str
    decimal_price: Decimal
    source_observed_at: datetime
    as_of: datetime
    raw_fields: dict[str, object]
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentityResolution:
    status: IdentityStatus
    player_id: int | None
    candidate_player_ids: tuple[int, ...] = ()


@dataclass
class UnmatchedRecord:
    source: str
    source_record: str
    raw_player_names: list[str]
    raw_tournament: str | None
    event_date: str | None
    reason: str
    candidate_ids: list[int] = field(default_factory=list)


@dataclass
class IngestCounters:
    rows_seen: int = 0
    rows_inserted: int = 0
    rows_rejected: int = 0
    rows_unmatched: int = 0
    rows_updated: int = 0
    duplicate_rows: int = 0
    matches_normalized: int = 0
    match_versions_inserted: int = 0
    result_rows: int = 0
    stat_rows: int = 0
    odds_rows: int = 0
    odds_candidate_rows: int = 0
    odds_matched_rows: int = 0
    odds_ambiguous_rows: int = 0
    resolved_players: int = 0
    unmatched_players: int = 0
    ambiguous_players: int = 0
    invalid_prices: int = 0
    excluded_rows: int = 0


@dataclass
class QualityAccumulator:
    source_files: list[str] = field(default_factory=list)
    years: dict[str, int] = field(default_factory=dict)
    tour_tiers: dict[str, int] = field(default_factory=dict)
    surfaces: dict[str, int] = field(default_factory=dict)
    bookmakers: dict[str, int] = field(default_factory=dict)
    known_gaps: list[str] = field(default_factory=list)

    def increment(self, bucket: dict[str, int], key: str | None) -> None:
        if not key:
            return
        bucket[key] = bucket.get(key, 0) + 1
