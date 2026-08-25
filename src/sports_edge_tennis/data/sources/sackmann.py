from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable

from ..contracts import MatchStatLine, PlayerReference, SackmannMatchRecord, SourceDocument
from ..hashing import source_hash
from ..normalize import collapse_whitespace


class SackmannRowError(ValueError):
    """Raised when a Sackmann row cannot be safely normalized."""


_SURFACE_MAP = {
    "Hard": "HARD",
    "Clay": "CLAY",
    "Grass": "GRASS",
    "Carpet": "CARPET",
    "HARD": "HARD",
    "CLAY": "CLAY",
    "GRASS": "GRASS",
    "CARPET": "CARPET",
}

_REQUIRED_COLUMNS = {
    "tourney_name",
    "tourney_date",
    "tourney_level",
    "winner_name",
    "loser_name",
    "round",
    "best_of",
}
_ATP_MAIN_UNKNOWN_MATCH_DURATION_DAYS = 13
_CHALLENGER_UNKNOWN_MATCH_DURATION_DAYS = 6
_UNKNOWN_MATCH_DATE_LIMITATION = (
    "Sackmann rows without explicit match_date keep match_start_at unknown. Match, result, "
    "and stat visibility stays delayed until a conservative tournament-end boundary because "
    "tourney_date is only a tournament-start boundary, not an individual match date."
)


@dataclass(frozen=True)
class SackmannParseOutcome:
    records: list[SackmannMatchRecord]
    rejected_rows: list[dict[str, object]]


def _parse_int(value: str | None, *, required: bool = False) -> int | None:
    if value is None or value == "":
        if required:
            raise SackmannRowError("missing_required_integer")
        return None
    return int(value)


def _parse_tourney_date(raw_value: str) -> date:
    try:
        return datetime.strptime(raw_value, "%Y%m%d").date()
    except ValueError as exc:
        raise SackmannRowError("invalid_tourney_date") from exc


def _surface(value: str | None) -> str | None:
    if not value:
        return None
    normalized = _SURFACE_MAP.get(value)
    if normalized is None:
        raise SackmannRowError("unsupported_surface")
    return normalized


def _tour_scope(level: str) -> str:
    if level == "C":
        return "CHALLENGER"
    return "ATP_MAIN"


def _competition_tier(level: str, row: dict[str, str]) -> str:
    if level == "G":
        return "GRAND_SLAM"
    if level == "M":
        return "MASTERS_1000"
    if level == "A":
        return "ATP_TOUR"
    if level == "C":
        return "CHALLENGER"
    explicit = row.get("competition_tier", "").strip().upper()
    if explicit in {"ATP_500", "ATP_250"}:
        return explicit
    raise SackmannRowError("unsupported_tourney_level")


def _match_start_at(match_day: date) -> datetime:
    return datetime.combine(match_day, time(hour=12, tzinfo=UTC))


def _prematch_observed_at(match_day: date) -> datetime:
    return datetime.combine(match_day, time.min, tzinfo=UTC)


def _result_observed_at(match_day: date) -> datetime:
    return datetime.combine(match_day, time(hour=23, minute=59, second=59, tzinfo=UTC))


def _conservative_tournament_duration_days(competition_tier: str) -> int:
    if competition_tier == "CHALLENGER":
        return _CHALLENGER_UNKNOWN_MATCH_DURATION_DAYS
    # Sackmann rows without explicit match dates can still cover multi-week ATP main-draw
    # events, so ATP-main visibility remains fail-closed through the second Sunday.
    return _ATP_MAIN_UNKNOWN_MATCH_DURATION_DAYS


def _conservative_tournament_end_date(tourney_date: date, competition_tier: str) -> date:
    duration_days = _conservative_tournament_duration_days(competition_tier)
    return tourney_date + timedelta(days=duration_days)


def _conservative_tournament_visibility_boundary(tourney_date: date, competition_tier: str) -> datetime:
    return _result_observed_at(_conservative_tournament_end_date(tourney_date, competition_tier))


def _match_status(score: str | None, comment: str | None) -> str:
    comment_value = (comment or "").strip().casefold()
    if comment_value == "retired":
        return "RETIRED"
    if comment_value == "walkover":
        return "WALKOVER"
    if not score and comment_value == "cancelled":
        return "CANCELLED"
    return "COMPLETED"


def _source_match_id(row: dict[str, str], winner_id: str | None, loser_id: str | None) -> str | None:
    tourney_id = row.get("tourney_id", "").strip()
    match_num = row.get("match_num", "").strip()
    if tourney_id and match_num:
        return f"{tourney_id}:{match_num}"
    player_tokens = sorted(token for token in (winner_id, loser_id) if token)
    if player_tokens:
        return (
            f"{row.get('tourney_name', '').strip()}:{row.get('tourney_date', '').strip()}:"
            f"{row.get('round', '').strip()}:{'|'.join(player_tokens)}"
        )
    return None


def _stat_line(row: dict[str, str], prefix: str) -> MatchStatLine:
    def maybe(column: str) -> int | None:
        return _parse_int(row.get(column), required=False)

    service_points_played = maybe(f"{prefix}_svpt")
    first_serve_points_won = maybe(f"{prefix}_1stWon")
    second_serve_points_won = maybe(f"{prefix}_2ndWon")
    return_points_won = None
    opponent_svpt = maybe("l_svpt" if prefix == "w" else "w_svpt")
    opponent_first_won = maybe("l_1stWon" if prefix == "w" else "w_1stWon")
    opponent_second_won = maybe("l_2ndWon" if prefix == "w" else "w_2ndWon")
    if (
        opponent_svpt is not None
        and opponent_first_won is not None
        and opponent_second_won is not None
    ):
        return_points_won = opponent_svpt - opponent_first_won - opponent_second_won

    return MatchStatLine(
        first_serves_in=maybe(f"{prefix}_1stIn"),
        first_serve_points_won=first_serve_points_won,
        second_serve_points_won=second_serve_points_won,
        service_points_played=service_points_played,
        return_points_won=return_points_won,
        aces=maybe(f"{prefix}_ace"),
        double_faults=maybe(f"{prefix}_df"),
        break_points_saved=maybe(f"{prefix}_bpSaved"),
        break_points_faced=maybe(f"{prefix}_bpFaced"),
    )


def parse_sackmann_csv(document: SourceDocument) -> SackmannParseOutcome:
    records: list[SackmannMatchRecord] = []
    rejected_rows: list[dict[str, object]] = []
    with document.local_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SackmannRowError("missing_header_row")
        missing_columns = sorted(_REQUIRED_COLUMNS - set(reader.fieldnames))
        if missing_columns:
            raise SackmannRowError(f"missing_required_columns:{','.join(missing_columns)}")

        for row_number, row in enumerate(reader, start=2):
            try:
                tourney_date = _parse_tourney_date(row["tourney_date"])
                season_year = tourney_date.year
                best_of_sets = _parse_int(row["best_of"], required=True)
                if best_of_sets not in {3, 5}:
                    raise SackmannRowError("unsupported_best_of_sets")

                level = row["tourney_level"].strip()
                tour_scope = _tour_scope(level)
                tier = _competition_tier(level, row)
                tournament_end_date = _conservative_tournament_end_date(tourney_date, tier)
                round_code = collapse_whitespace(row["round"])
                qualifying_flag = round_code.startswith("Q")
                winner_name = collapse_whitespace(row["winner_name"])
                loser_name = collapse_whitespace(row["loser_name"])
                if not winner_name or not loser_name:
                    raise SackmannRowError("missing_player_name")

                winner_id = row.get("winner_id", "").strip() or None
                loser_id = row.get("loser_id", "").strip() or None
                source_match_id = _source_match_id(row, winner_id, loser_id)
                explicit_match_date = row.get("match_date")
                if explicit_match_date:
                    match_day = datetime.strptime(explicit_match_date, "%Y-%m-%d").date()
                    match_start_at = _match_start_at(match_day)
                    match_observed_at = _prematch_observed_at(match_day)
                    result_observed_at = _result_observed_at(match_day)
                    if match_day > tournament_end_date:
                        tournament_end_date = match_day
                else:
                    result_observed_at = _conservative_tournament_visibility_boundary(tourney_date, tier)
                    match_start_at = None
                    match_observed_at = result_observed_at
                limitations: list[str] = []
                if not explicit_match_date:
                    limitations.append(_UNKNOWN_MATCH_DATE_LIMITATION)

                raw_fields = {
                    key: value
                    for key, value in row.items()
                    if value not in (None, "")
                }
                raw_fields["source_row_number"] = row_number
                raw_fields["source_file_name"] = document.filename
                record_hash = source_hash(document.source_system, raw_fields)
                records.append(
                    SackmannMatchRecord(
                        document=document,
                        source_row_number=row_number,
                        source_match_id=source_match_id,
                        source_record_hash=record_hash,
                        source_record_locator=f"{document.filename}#{row_number}",
                        source_version_hash=document.sha256,
                        tournament_source_id=row.get("tourney_id", "").strip() or None,
                        tournament_name=collapse_whitespace(row["tourney_name"]),
                        edition_name=f"{collapse_whitespace(row['tourney_name'])} {season_year}",
                        season_year=season_year,
                        tournament_start_date=tourney_date,
                        tournament_end_date=tournament_end_date,
                        surface=_surface(row.get("surface")),
                        tour_scope=tour_scope,
                        competition_tier=tier,
                        round_code=round_code,
                        best_of_sets=best_of_sets,
                        qualifying_flag=qualifying_flag,
                        match_start_at=match_start_at,
                        match_source_observed_at=match_observed_at,
                        match_as_of=match_observed_at,
                        result_source_observed_at=result_observed_at,
                        result_as_of=result_observed_at,
                        winner=PlayerReference(winner_name, winner_id),
                        loser=PlayerReference(loser_name, loser_id),
                        scoreline_text=collapse_whitespace(row["score"]) if row.get("score") else None,
                        outcome_status=_match_status(row.get("score"), row.get("comment")),
                        winner_stats=_stat_line(row, "w"),
                        loser_stats=_stat_line(row, "l"),
                        raw_fields=raw_fields,
                        limitations=tuple(limitations),
                    )
                )
            except (SackmannRowError, ValueError) as exc:
                rejected_rows.append(
                    {
                        "row_number": row_number,
                        "reason": str(exc),
                        "tournament_name": row.get("tourney_name"),
                        "winner_name": row.get("winner_name"),
                        "loser_name": row.get("loser_name"),
                    }
                )
    return SackmannParseOutcome(records=records, rejected_rows=rejected_rows)
