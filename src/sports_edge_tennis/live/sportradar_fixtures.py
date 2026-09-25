from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from sports_edge_tennis.live.sportradar_probe import normalize_sportradar_surface


SOURCE_SYSTEM = "SPORTRADAR"
ADAPTER_VERSION = "SPORTRADAR_TENNIS_DAILY_SUMMARY_V1"

# Sportradar emits draw/qualification slot labels in place of a player name until
# the bracket resolves. These are not legitimate participants and must never be
# modeled; they are only ever replaced by a deterministic participant update.
_PLACEHOLDER_NAME_PATTERN = re.compile(
    r"^(R\d+P\d+|Qf\d+|WQF\d+)$", re.IGNORECASE
)


def is_placeholder_name(name: str | None) -> bool:
    return bool(_PLACEHOLDER_NAME_PATTERN.match((name or "").strip()))

SUPPORTED_STATUSES = {
    "not_started": "SCHEDULED",
    "scheduled": "SCHEDULED",
    "postponed": "POSTPONED",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "closed": "COMPLETED",
    "ended": "COMPLETED",
    "retired": "RETIRED",
    "walkover": "WALKOVER",
}

_OUT_OF_SCOPE_TOKENS = (
    "wta",
    "women",
    "woman",
    "doubles",
    "double",
    "mixed",
    "junior",
    "itf",
    "exhibition",
)


@dataclass(frozen=True)
class SportradarFixtureCandidate:
    source_match_id: str
    competition_source_id: str
    competition_name: str
    season_source_id: str | None
    player_1_source_id: str
    player_1_name: str
    player_2_source_id: str
    player_2_name: str
    match_start_at: datetime
    match_status: str
    tour_scope: str
    competition_tier: str
    surface: str | None
    round_code: str | None
    best_of_sets: int
    qualifying_flag: bool
    received_at: datetime
    raw_summary: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class SportradarFixtureParseOutcome:
    rows_seen: int
    rows_accepted: int
    rows_rejected: int
    fixtures: tuple[SportradarFixtureCandidate, ...]
    rejection_reasons: tuple[tuple[str, int], ...]


class SportradarFixtureError(ValueError):
    pass


def parse_daily_summaries(
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
) -> SportradarFixtureParseOutcome:
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    summaries = _mapping_list(payload.get("summaries"))
    accepted: list[SportradarFixtureCandidate] = []
    reasons: dict[str, int] = {}
    for summary in summaries:
        try:
            accepted.append(parse_summary(summary, received_at=received_at))
        except SportradarFixtureError as exc:
            key = str(exc)
            reasons[key] = reasons.get(key, 0) + 1
    return SportradarFixtureParseOutcome(
        rows_seen=len(summaries),
        rows_accepted=len(accepted),
        rows_rejected=len(summaries) - len(accepted),
        fixtures=tuple(accepted),
        rejection_reasons=tuple(sorted(reasons.items())),
    )


def parse_summary(
    summary: Mapping[str, Any],
    *,
    received_at: datetime,
) -> SportradarFixtureCandidate:
    event = _mapping(summary.get("sport_event"), "missing_sport_event")
    source_match_id = _required_text(event, "id", "missing_event_id")
    match_start_at = _timestamp(event.get("start_time"), "invalid_start_time")

    context = _mapping(event.get("sport_event_context"), "missing_event_context")
    competition = _mapping(context.get("competition"), "missing_competition")
    competition_source_id = _required_text(
        competition, "id", "missing_competition_id"
    )
    competition_name = _required_text(
        competition, "name", "missing_competition_name"
    )
    category = context.get("category")
    category_name = (
        str(category.get("name") or "").strip()
        if isinstance(category, Mapping)
        else ""
    )
    competition_level = str(competition.get("level") or "").strip()
    competition_type = str(competition.get("type") or "").strip()
    competition_gender = str(competition.get("gender") or "").strip()
    tour_scope, competition_tier = classify_competition(
        competition_name=competition_name,
        competition_level=competition_level,
        category_name=category_name,
        competition_type=competition_type,
        competition_gender=competition_gender,
    )

    competitors = _mapping_list(event.get("competitors"))
    if len(competitors) != 2:
        raise SportradarFixtureError("invalid_competitor_count")
    by_qualifier: dict[str, Mapping[str, Any]] = {}
    for competitor in competitors:
        qualifier = str(competitor.get("qualifier") or "").strip().casefold()
        if qualifier in by_qualifier:
            raise SportradarFixtureError("duplicate_competitor_qualifier")
        by_qualifier[qualifier] = competitor
    if set(by_qualifier) != {"home", "away"}:
        raise SportradarFixtureError("missing_home_away_qualifiers")
    first = by_qualifier["home"]
    second = by_qualifier["away"]
    player_1_source_id = _required_text(
        first, "id", "missing_player_1_id"
    )
    player_2_source_id = _required_text(
        second, "id", "missing_player_2_id"
    )
    if player_1_source_id == player_2_source_id:
        raise SportradarFixtureError("duplicate_player_id")
    player_1_name = _required_text(
        first, "name", "missing_player_1_name"
    )
    player_2_name = _required_text(
        second, "name", "missing_player_2_name"
    )

    status_payload = _mapping(
        summary.get("sport_event_status"), "missing_event_status"
    )
    raw_status = str(status_payload.get("status") or "").strip().casefold()
    match_status = SUPPORTED_STATUSES.get(raw_status)
    if match_status is None:
        raise SportradarFixtureError("unsupported_event_status")

    # The broad status only says whether the sport event is over. Sportradar
    # match_status carries the tennis-specific terminal outcome, including
    # retired and walkover, even when broad status is simply ended.
    detailed_status = str(
        status_payload.get("match_status") or ""
    ).strip().casefold()
    if raw_status in {"closed", "ended"}:
        if detailed_status == "retired":
            match_status = "RETIRED"
        elif detailed_status == "walkover":
            match_status = "WALKOVER"
        elif detailed_status in {"cancelled", "canceled"}:
            match_status = "CANCELLED"

    # winning_reason is a secondary terminal-exception signal. Keep it as a
    # conservative fallback for feeds that omit the detailed match_status.
    winning_reason = str(
        status_payload.get("winning_reason") or ""
    ).strip().casefold()
    if raw_status in {"closed", "ended"}:
        if winning_reason == "walkover":
            match_status = "WALKOVER"
        elif winning_reason in {"retirement", "defaulted"}:
            match_status = "RETIRED"

    season = context.get("season")
    season_source_id = (
        str(season.get("id") or "").strip() or None
        if isinstance(season, Mapping)
        else None
    )
    raw_surface = (
        str(context.get("surface") or "").strip()
        or str(competition.get("surface") or "").strip()
    )
    surface = normalize_sportradar_surface(raw_surface) if raw_surface else None

    mode = context.get("mode")
    raw_best_of = mode.get("best_of") if isinstance(mode, Mapping) else None
    if raw_best_of in (None, ""):
        best_of_sets = 3
    else:
        try:
            best_of_sets = int(raw_best_of)
        except (TypeError, ValueError) as exc:
            raise SportradarFixtureError("invalid_best_of_sets") from exc
        if best_of_sets not in (3, 5):
            raise SportradarFixtureError("unsupported_best_of_sets")

    round_payload = context.get("round")
    round_code = (
        str(round_payload.get("name") or "").strip() or None
        if isinstance(round_payload, Mapping)
        else None
    )
    stage = context.get("stage")
    phase = (
        str(stage.get("phase") or "").strip().casefold()
        if isinstance(stage, Mapping)
        else ""
    )
    qualifying_flag = (
        "qual" in phase
        or bool(round_code and "qual" in round_code.casefold())
    )

    return SportradarFixtureCandidate(
        source_match_id=source_match_id,
        competition_source_id=competition_source_id,
        competition_name=competition_name,
        season_source_id=season_source_id,
        player_1_source_id=player_1_source_id,
        player_1_name=player_1_name,
        player_2_source_id=player_2_source_id,
        player_2_name=player_2_name,
        match_start_at=match_start_at,
        match_status=match_status,
        tour_scope=tour_scope,
        competition_tier=competition_tier,
        surface=surface,
        round_code=round_code,
        best_of_sets=best_of_sets,
        qualifying_flag=qualifying_flag,
        received_at=received_at.astimezone(UTC),
        raw_summary=dict(summary),
    )


def classify_competition(
    *,
    competition_name: str,
    competition_level: str,
    category_name: str,
    competition_type: str = "",
    competition_gender: str = "",
) -> tuple[str, str]:
    normalized_level = competition_level.casefold().replace("_", " ").replace("-", " ")
    normalized_type = competition_type.casefold().replace("_", " ").replace("-", " ")
    normalized_gender = competition_gender.casefold()
    combined = " ".join(
        (competition_name, normalized_level, category_name)
    ).casefold()

    if normalized_type and normalized_type != "singles":
        raise SportradarFixtureError("out_of_scope_event_type")
    if normalized_gender in {"women", "woman", "female", "mixed"}:
        raise SportradarFixtureError("out_of_scope_event_type")
    if any(token in combined for token in _OUT_OF_SCOPE_TOKENS):
        raise SportradarFixtureError("out_of_scope_event_type")

    if "challenger" in combined:
        return "CHALLENGER", "CHALLENGER"

    atp_like = (
        "atp" in combined
        or "grand slam" in combined
        or "davis cup" in combined
    )
    if not atp_like:
        raise SportradarFixtureError("unsupported_event_type")

    if "grand slam" in combined:
        return "ATP_MAIN", "GRAND_SLAM"
    if "masters 1000" in combined or "atp 1000" in combined:
        return "ATP_MAIN", "MASTERS_1000"
    if "atp 500" in combined:
        return "ATP_MAIN", "ATP_500"
    if "atp 250" in combined:
        return "ATP_MAIN", "ATP_250"
    return "ATP_MAIN", "ATP_TOUR"


def _mapping(value: Any, reason: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SportradarFixtureError(reason)
    return value


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _required_text(
    value: Mapping[str, Any],
    key: str,
    reason: str,
) -> str:
    text = str(value.get(key) or "").strip()
    if not text:
        raise SportradarFixtureError(reason)
    return text


def _timestamp(value: Any, reason: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise SportradarFixtureError(reason)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SportradarFixtureError(reason) from exc
    if parsed.tzinfo is None:
        raise SportradarFixtureError(reason)
    return parsed.astimezone(UTC)


__all__ = [
    "ADAPTER_VERSION",
    "SOURCE_SYSTEM",
    "SportradarFixtureCandidate",
    "SportradarFixtureError",
    "SportradarFixtureParseOutcome",
    "classify_competition",
    "is_placeholder_name",
    "parse_daily_summaries",
    "parse_summary",
]
