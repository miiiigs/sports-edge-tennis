from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sports_edge_tennis.data.contracts import MatchStatLine


@dataclass(frozen=True)
class SportradarResultEvidence:
    outcome_status: str
    winner_provider_id: str | None
    retired_provider_id: str | None
    sets_home: int | None
    sets_away: int | None
    games_home: int | None
    games_away: int | None
    scoreline_text: str | None


@dataclass(frozen=True)
class SportradarPlayerStatEvidence:
    provider_player_id: str
    line: MatchStatLine


@dataclass(frozen=True)
class SportradarResultStatEvidence:
    result: SportradarResultEvidence | None
    stats: tuple[SportradarPlayerStatEvidence, ...]


def parse_result_stat_evidence(
    summary: Mapping[str, Any],
) -> SportradarResultStatEvidence:
    event = summary.get("sport_event")
    if not isinstance(event, Mapping):
        return SportradarResultStatEvidence(None, ())
    competitors = _list(event.get("competitors"))
    if len(competitors) != 2:
        return SportradarResultStatEvidence(None, ())
    by_qualifier = {
        str(row.get("qualifier") or "").strip().casefold(): row
        for row in competitors
        if str(row.get("qualifier") or "").strip()
    }
    if set(by_qualifier) != {"home", "away"}:
        return SportradarResultStatEvidence(None, ())
    home_id = _text(by_qualifier["home"].get("id"))
    away_id = _text(by_qualifier["away"].get("id"))
    if not home_id or not away_id or home_id == away_id:
        return SportradarResultStatEvidence(None, ())

    status = summary.get("sport_event_status")
    result = _parse_result(status, home_id=home_id, away_id=away_id)
    stats = _parse_stats(summary.get("statistics"), home_id=home_id, away_id=away_id)
    return SportradarResultStatEvidence(result, stats)


def _parse_result(
    value: Any,
    *,
    home_id: str,
    away_id: str,
) -> SportradarResultEvidence | None:
    if not isinstance(value, Mapping):
        return None
    status = _text(value.get("status")).casefold()
    match_status = _text(value.get("match_status")).casefold()
    winning_reason = _text(value.get("winning_reason")).casefold()
    winner_id = _text(value.get("winner_id")) or None

    if status in {"cancelled", "canceled"}:
        outcome = "CANCELLED"
        winner_id = None
        retired_id = None
    elif winning_reason == "walkover" or match_status == "walkover":
        outcome = "WALKOVER"
        retired_id = None
    elif winning_reason in {"retirement", "retired"} or match_status == "retired":
        outcome = "RETIRED"
        retired_id = _other_player(winner_id, home_id, away_id)
    elif status in {"closed", "ended"} and winner_id in {home_id, away_id}:
        outcome = "COMPLETED"
        retired_id = None
    else:
        return None

    home_sets = _nonnegative_int(value.get("home_score"))
    away_sets = _nonnegative_int(value.get("away_score"))
    period_scores = _list(value.get("period_scores"))
    games_home = games_away = 0
    game_rows = 0
    score_parts: list[str] = []
    for period in period_scores:
        if _text(period.get("type")).casefold() not in {"", "set"}:
            continue
        hg = _nonnegative_int(period.get("home_score"))
        ag = _nonnegative_int(period.get("away_score"))
        if hg is None or ag is None:
            continue
        games_home += hg
        games_away += ag
        game_rows += 1
        ht = _nonnegative_int(period.get("home_tiebreak_score"))
        at = _nonnegative_int(period.get("away_tiebreak_score"))
        suffix = ""
        if ht is not None or at is not None:
            suffix = f"({ht if ht is not None else '-'}-{at if at is not None else '-'})"
        score_parts.append(f"{hg}-{ag}{suffix}")

    return SportradarResultEvidence(
        outcome_status=outcome,
        winner_provider_id=winner_id if winner_id in {home_id, away_id} else None,
        retired_provider_id=retired_id,
        sets_home=home_sets,
        sets_away=away_sets,
        games_home=games_home if game_rows else None,
        games_away=games_away if game_rows else None,
        scoreline_text=" ".join(score_parts) or None,
    )


def _parse_stats(
    value: Any,
    *,
    home_id: str,
    away_id: str,
) -> tuple[SportradarPlayerStatEvidence, ...]:
    if not isinstance(value, Mapping):
        return ()
    totals = value.get("totals")
    if not isinstance(totals, Mapping):
        return ()
    competitors = _list(totals.get("competitors"))
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in competitors:
        player_id = _text(row.get("id"))
        stats = row.get("statistics")
        if player_id and isinstance(stats, Mapping):
            by_id[player_id] = stats
    if set(by_id).intersection({home_id, away_id}) != {home_id, away_id}:
        return ()

    parsed: dict[str, dict[str, int | None]] = {}
    for player_id in (home_id, away_id):
        stats = by_id[player_id]
        first_won = _nonnegative_int(stats.get("first_serve_points_won"))
        second_won = _nonnegative_int(stats.get("second_serve_points_won"))
        service_won = _nonnegative_int(stats.get("service_points_won"))
        service_lost = _nonnegative_int(stats.get("service_points_lost"))
        if None in {first_won, second_won, service_won, service_lost}:
            return ()
        if first_won + second_won != service_won:
            return ()
        service_played = service_won + service_lost
        parsed[player_id] = {
            "first_serves_in": _nonnegative_int(stats.get("first_serve_successful")),
            "first_serve_points_won": first_won,
            "second_serve_points_won": second_won,
            "service_points_played": service_played,
            "service_points_won": service_won,
            "aces": _nonnegative_int(stats.get("aces")),
            "double_faults": _nonnegative_int(stats.get("double_faults")),
            "break_points_saved": _nonnegative_int(stats.get("breakpoints_saved")),
            "break_points_faced": _nonnegative_int(stats.get("breakpoints_faced")),
        }

    output: list[SportradarPlayerStatEvidence] = []
    for player_id, opponent_id in ((home_id, away_id), (away_id, home_id)):
        own = parsed[player_id]
        opponent = parsed[opponent_id]
        opponent_played = int(opponent["service_points_played"])
        opponent_won = int(opponent["service_points_won"])
        return_won = opponent_played - opponent_won
        if return_won < 0:
            return ()
        output.append(
            SportradarPlayerStatEvidence(
                provider_player_id=player_id,
                line=MatchStatLine(
                    first_serves_in=own["first_serves_in"],
                    first_serve_points_won=int(own["first_serve_points_won"]),
                    second_serve_points_won=int(own["second_serve_points_won"]),
                    service_points_played=int(own["service_points_played"]),
                    return_points_won=return_won,
                    aces=own["aces"],
                    double_faults=own["double_faults"],
                    break_points_saved=own["break_points_saved"],
                    break_points_faced=own["break_points_faced"],
                ),
            )
        )
    return tuple(output)


def _other_player(
    winner_id: str | None,
    home_id: str,
    away_id: str,
) -> str | None:
    if winner_id == home_id:
        return away_id
    if winner_id == away_id:
        return home_id
    return None


def _nonnegative_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _text(value: Any) -> str:
    return str(value or "").strip()


__all__ = [
    "SportradarPlayerStatEvidence",
    "SportradarResultEvidence",
    "SportradarResultStatEvidence",
    "parse_result_stat_evidence",
]
