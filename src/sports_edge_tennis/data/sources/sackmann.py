from __future__ import annotations

import csv
import re
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
_DATE_ONLY_MATCH_DATE_LIMITATION = (
    "Sackmann match_date is date-only. Sports Edge stores the match day boundary for PIT, "
    "but keeps match_start_at unknown rather than inventing an exact match time."
)
_KNOWN_COLUMNS = {
    "tourney_id",
    "tourney_name",
    "surface",
    "draw_size",
    "tourney_level",
    "tourney_date",
    "match_date",
    "match_num",
    "winner_id",
    "winner_seed",
    "winner_entry",
    "winner_name",
    "winner_hand",
    "winner_ht",
    "winner_ioc",
    "winner_age",
    "loser_id",
    "loser_seed",
    "loser_entry",
    "loser_name",
    "loser_hand",
    "loser_ht",
    "loser_ioc",
    "loser_age",
    "score",
    "best_of",
    "round",
    "minutes",
    "comment",
    "w_ace",
    "w_df",
    "w_svpt",
    "w_1stIn",
    "w_1stWon",
    "w_2ndWon",
    "w_SvGms",
    "w_bpSaved",
    "w_bpFaced",
    "l_ace",
    "l_df",
    "l_svpt",
    "l_1stIn",
    "l_1stWon",
    "l_2ndWon",
    "l_SvGms",
    "l_bpSaved",
    "l_bpFaced",
    "winner_rank",
    "winner_rank_points",
    "loser_rank",
    "loser_rank_points",
}
_STAT_COLUMNS = (
    "w_ace",
    "w_df",
    "w_svpt",
    "w_1stIn",
    "w_1stWon",
    "w_2ndWon",
    "w_SvGms",
    "w_bpSaved",
    "w_bpFaced",
    "l_ace",
    "l_df",
    "l_svpt",
    "l_1stIn",
    "l_1stWon",
    "l_2ndWon",
    "l_SvGms",
    "l_bpSaved",
    "l_bpFaced",
)
_COMMENT_STATUS_MAP = {
    "retired": "RETIRED",
    "walkover": "WALKOVER",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "postponed": "POSTPONED",
}
_SCORE_STATUS_MAP = {
    "DEF": "RETIRED",
    "RET": "RETIRED",
    "W/O": "WALKOVER",
    "WO": "WALKOVER",
}
_SET_TOKEN_RE = re.compile(r"^\d+-\d+")


@dataclass(frozen=True)
class SackmannParseOutcome:
    records: list[SackmannMatchRecord]
    rejected_rows: list[dict[str, object]]
    audit: "SackmannParseAudit"


@dataclass(frozen=True)
class SackmannStatusVocabularyEntry:
    raw_value: str | None
    normalized_status: str | None
    row_count: int
    accepted: bool


@dataclass(frozen=True)
class SackmannParseAudit:
    source_headers: tuple[str, ...]
    unknown_source_columns: tuple[str, ...]
    missing_player_id_rows: int
    missing_tournament_id_rows: int
    missing_match_num_rows: int
    missing_match_date_rows: int
    unknown_surface_rows: int
    stat_null_patterns: dict[str, int]
    stat_field_non_null_counts: dict[str, int]
    status_vocabulary: tuple[SackmannStatusVocabularyEntry, ...]


@dataclass(frozen=True)
class _StatusResolution:
    raw_value: str | None
    normalized_status: str | None
    accepted: bool
    reason: str | None = None


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
    if level in {"A", "F", "G", "M", "O"}:
        return "ATP_MAIN"
    return "ATP_MAIN"


def _competition_tier(level: str, row: dict[str, str]) -> str:
    if level == "G":
        return "GRAND_SLAM"
    if level == "M":
        return "MASTERS_1000"
    if level in {"A", "F", "O"}:
        return "ATP_TOUR"
    if level == "C":
        return "CHALLENGER"
    if level in {"D", "S"}:
        raise SackmannRowError(f"out_of_scope_tourney_level:{level}")
    explicit = row.get("competition_tier", "").strip().upper()
    if explicit in {"ATP_500", "ATP_250"}:
        return explicit
    raise SackmannRowError("unsupported_tourney_level")


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


def _source_match_id(row: dict[str, str], winner_id: str | None, loser_id: str | None) -> str | None:
    tourney_id = row.get("tourney_id", "").strip()
    match_num = row.get("match_num", "").strip()
    if tourney_id and match_num:
        return f"{tourney_id}:{match_num}"
    return None


def _normalize_status_token(value: str) -> str:
    return value.strip().upper().rstrip(".")


def _has_score_tokens(score_value: str) -> bool:
    return any(_SET_TOKEN_RE.match(token) for token in score_value.split())


def _resolve_status(score: str | None, comment: str | None) -> _StatusResolution:
    comment_value = (comment or "").strip()
    if comment_value:
        normalized_comment = comment_value.casefold()
        normalized_status = _COMMENT_STATUS_MAP.get(normalized_comment)
        if normalized_status == "POSTPONED":
            return _StatusResolution(
                raw_value=comment_value,
                normalized_status=normalized_status,
                accepted=False,
                reason="unsupported_result_status:POSTPONED",
            )
        if normalized_status is None:
            return _StatusResolution(
                raw_value=comment_value,
                normalized_status=None,
                accepted=False,
                reason=f"unsupported_status_comment:{comment_value}",
            )
        return _StatusResolution(
            raw_value=comment_value,
            normalized_status=normalized_status,
            accepted=True,
        )

    score_value = (score or "").strip()
    if not score_value:
        return _StatusResolution(
            raw_value=None,
            normalized_status=None,
            accepted=False,
            reason="missing_score_or_status",
        )
    # Rare bare advancement marker seen in the Sackmann qual/chall archive.
    # The source row contains no playable score tokens, minutes, or stats, so we
    # quarantine it instead of asserting a richer canonical outcome.
    if score_value == ">":
        return _StatusResolution(
            raw_value=score_value,
            normalized_status=None,
            accepted=False,
            reason="ambiguous_advancement_marker",
        )

    trailing_token = _normalize_status_token(score_value.split()[-1])
    normalized_status = _SCORE_STATUS_MAP.get(trailing_token)
    if normalized_status is not None:
        return _StatusResolution(
            raw_value=trailing_token,
            normalized_status=normalized_status,
            accepted=True,
        )

    if _has_score_tokens(score_value):
        return _StatusResolution(
            raw_value=None,
            normalized_status="COMPLETED",
            accepted=True,
        )

    return _StatusResolution(
        raw_value=score_value,
        normalized_status=None,
        accepted=False,
        reason=f"unsupported_score_status:{score_value}",
    )


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


def _stat_null_pattern(row: dict[str, str]) -> str:
    present_count = sum(1 for column in _STAT_COLUMNS if row.get(column) not in (None, ""))
    if present_count == 0:
        return "no_stats"
    if present_count == len(_STAT_COLUMNS):
        return "full_stats"
    return "partial_stats"


def _build_parse_audit(
    *,
    fieldnames: list[str],
    missing_player_id_rows: int,
    missing_tournament_id_rows: int,
    missing_match_num_rows: int,
    missing_match_date_rows: int,
    unknown_surface_rows: int,
    stat_null_patterns: dict[str, int],
    stat_field_non_null_counts: dict[str, int],
    status_counts: dict[tuple[str | None, str | None, bool], int],
) -> SackmannParseAudit:
    status_vocabulary = tuple(
        SackmannStatusVocabularyEntry(
            raw_value=raw_value,
            normalized_status=normalized_status,
            row_count=row_count,
            accepted=accepted,
        )
        for (raw_value, normalized_status, accepted), row_count in sorted(
            status_counts.items(),
            key=lambda item: (
                item[0][0] or "",
                item[0][1] or "",
                item[0][2],
            ),
        )
    )
    return SackmannParseAudit(
        source_headers=tuple(fieldnames),
        unknown_source_columns=tuple(sorted(set(fieldnames) - _KNOWN_COLUMNS)),
        missing_player_id_rows=missing_player_id_rows,
        missing_tournament_id_rows=missing_tournament_id_rows,
        missing_match_num_rows=missing_match_num_rows,
        missing_match_date_rows=missing_match_date_rows,
        unknown_surface_rows=unknown_surface_rows,
        stat_null_patterns=dict(sorted(stat_null_patterns.items())),
        stat_field_non_null_counts=dict(sorted(stat_field_non_null_counts.items())),
        status_vocabulary=status_vocabulary,
    )


def parse_sackmann_csv(document: SourceDocument) -> SackmannParseOutcome:
    records: list[SackmannMatchRecord] = []
    rejected_rows: list[dict[str, object]] = []
    status_counts: dict[tuple[str | None, str | None, bool], int] = {}
    missing_player_id_rows = 0
    missing_tournament_id_rows = 0
    missing_match_num_rows = 0
    missing_match_date_rows = 0
    unknown_surface_rows = 0
    stat_null_patterns: dict[str, int] = {}
    stat_field_non_null_counts = {column: 0 for column in _STAT_COLUMNS}
    with document.local_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SackmannRowError("missing_header_row")
        missing_columns = sorted(_REQUIRED_COLUMNS - set(reader.fieldnames))
        if missing_columns:
            raise SackmannRowError(f"missing_required_columns:{','.join(missing_columns)}")

        for row_number, row in enumerate(reader, start=2):
            winner_id = row.get("winner_id", "").strip() or None
            loser_id = row.get("loser_id", "").strip() or None
            if winner_id and loser_id and winner_id == loser_id:
                rejected_rows.append(
                    {
                        "row_number": row_number,
                        "reason": "self_match_source_player_id",
                        "tournament_name": row.get("tourney_name"),
                        "winner_name": row.get("winner_name"),
                        "loser_name": row.get("loser_name"),
                    }
                )
                continue
            if not winner_id or not loser_id:
                missing_player_id_rows += 1
            if not (row.get("tourney_id", "").strip()):
                missing_tournament_id_rows += 1
            if not (row.get("match_num", "").strip()):
                missing_match_num_rows += 1
            if not (row.get("match_date", "").strip()):
                missing_match_date_rows += 1
            if row.get("surface") not in (None, "") and row.get("surface") not in _SURFACE_MAP:
                unknown_surface_rows += 1
            stat_pattern = _stat_null_pattern(row)
            stat_null_patterns[stat_pattern] = stat_null_patterns.get(stat_pattern, 0) + 1
            for column in _STAT_COLUMNS:
                if row.get(column) not in (None, ""):
                    stat_field_non_null_counts[column] += 1
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

                source_match_id = _source_match_id(row, winner_id, loser_id)
                explicit_match_date = row.get("match_date")
                status_resolution = _resolve_status(row.get("score"), row.get("comment"))
                status_key = (
                    status_resolution.raw_value,
                    status_resolution.normalized_status,
                    status_resolution.accepted,
                )
                status_counts[status_key] = status_counts.get(status_key, 0) + 1
                if not status_resolution.accepted or status_resolution.normalized_status is None:
                    raise SackmannRowError(status_resolution.reason or "unsupported_status")
                if explicit_match_date:
                    match_day = datetime.strptime(explicit_match_date, "%Y-%m-%d").date()
                    match_start_at = None
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
                else:
                    limitations.append(_DATE_ONLY_MATCH_DATE_LIMITATION)

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
                        outcome_status=status_resolution.normalized_status,
                        winner_stats=_stat_line(row, "w"),
                        loser_stats=_stat_line(row, "l"),
                        raw_fields=raw_fields,
                        limitations=tuple(limitations),
                    )
                )
            except (SackmannRowError, ValueError) as exc:
                if "status_resolution" not in locals():
                    status_resolution = _StatusResolution(
                        raw_value=None,
                        normalized_status=None,
                        accepted=False,
                        reason=str(exc),
                    )
                rejected_rows.append(
                    {
                        "row_number": row_number,
                        "reason": str(exc),
                        "tournament_name": row.get("tourney_name"),
                        "winner_name": row.get("winner_name"),
                        "loser_name": row.get("loser_name"),
                    }
                )
            finally:
                status_resolution = None
    return SackmannParseOutcome(
        records=records,
        rejected_rows=rejected_rows,
        audit=_build_parse_audit(
            fieldnames=list(reader.fieldnames),
            missing_player_id_rows=missing_player_id_rows,
            missing_tournament_id_rows=missing_tournament_id_rows,
            missing_match_num_rows=missing_match_num_rows,
            missing_match_date_rows=missing_match_date_rows,
            unknown_surface_rows=unknown_surface_rows,
            stat_null_patterns=stat_null_patterns,
            stat_field_non_null_counts=stat_field_non_null_counts,
            status_counts=status_counts,
        ),
    )
