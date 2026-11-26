"""Offline summariser for API-Tennis ``get_draw`` payloads.

The provider documents ``include_qualification=1`` and a ``seed`` field that
can contain a numeric seed or a status token such as ``Q`` (qualifier) or
``WC`` (wild card). A single snapshot can therefore reconcile seeds,
qualifiers, wild cards, byes and still-undecided TBD slots. It cannot prove a
lucky-loser or replacement transition: those require comparing two draw
observations, which this module deliberately does not infer.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

QUALIFIER = "QUALIFIER"
WILDCARD = "WILDCARD"
LUCKY_LOSER = "LUCKY_LOSER"
SEEDED = "SEEDED"
BYE = "BYE"
TBD = "TBD"
UNCLASSIFIED = "UNCLASSIFIED"

REPLACEMENT_DETECTION = "REQUIRES_DRAW_DIFF"
SINGLE_SNAPSHOT_LIMITS = (
    "lucky-loser transitions",
    "late replacements",
    "withdrawals after the snapshot",
    "official draw corrections",
)


@dataclass(frozen=True)
class DrawEntrySummary:
    stage: str
    qualification: bool
    round_name: str
    match_number: int
    status: str
    slot_classification: str
    seed_label: str | None
    player_key: str | None
    winner_player_key: str | None
    next_slot_key: int | None


@dataclass(frozen=True)
class DrawSummary:
    tournament_key: str | None
    tournament_name: str | None
    tournament_season: str | None
    tournament_surface: str | None
    source: str | None
    bracket_count: int
    entries: tuple[DrawEntrySummary, ...]
    counts: dict[str, int]

    @property
    def classification_counts(self) -> dict[str, int]:
        return {
            key: value
            for key, value in self.counts.items()
            if key in {
                QUALIFIER,
                WILDCARD,
                LUCKY_LOSER,
                SEEDED,
                BYE,
                TBD,
                UNCLASSIFIED,
            }
        }


def classify_seed_label(seed: Any) -> str:
    token = "" if seed is None else str(seed).strip().upper()
    if not token:
        return UNCLASSIFIED
    if token == "Q":
        return QUALIFIER
    if token == "WC":
        return WILDCARD
    if token == "LL":
        return LUCKY_LOSER
    if token.isdigit():
        return SEEDED
    return UNCLASSIFIED


def summarize_draw(payload: Mapping[str, Any]) -> DrawSummary:
    """Summarise a captured ``get_draw`` payload without any provider call."""
    result = payload.get("result", payload)
    if not isinstance(result, Mapping):
        raise TypeError("draw payload result must be an object")
    tournament = result.get("tournament")
    tournament = tournament if isinstance(tournament, Mapping) else {}
    brackets = result.get("brackets")
    if not isinstance(brackets, list):
        raise TypeError("draw payload brackets must be a list")

    entries: list[DrawEntrySummary] = []
    counts = {
        QUALIFIER: 0,
        WILDCARD: 0,
        LUCKY_LOSER: 0,
        SEEDED: 0,
        BYE: 0,
        TBD: 0,
        UNCLASSIFIED: 0,
    }
    for bracket in brackets:
        if not isinstance(bracket, Mapping):
            continue
        stage = str(bracket.get("stage") or "")
        qualification = bool(bracket.get("qualification"))
        rounds = bracket.get("rounds")
        if not isinstance(rounds, list):
            continue
        for round_row in rounds:
            if not isinstance(round_row, Mapping):
                continue
            round_name = str(round_row.get("round_name") or "")
            matches = round_row.get("matches")
            if not isinstance(matches, list):
                continue
            for match in matches:
                if not isinstance(match, Mapping):
                    continue
                status = str(match.get("status") or "").strip().upper()
                first = match.get("first_player")
                second = match.get("second_player")
                first = first if isinstance(first, Mapping) else {}
                second = second if isinstance(second, Mapping) else {}
                first_class = classify_seed_label(first.get("seed"))
                second_class = classify_seed_label(second.get("seed"))
                for classification in (first_class, second_class):
                    if classification != UNCLASSIFIED:
                        counts[classification] = counts.get(classification, 0) + 1
                seed = first.get("seed") or second.get("seed")
                classification = first_class
                if classification == UNCLASSIFIED:
                    classification = second_class
                if status == "BYE":
                    classification = BYE
                    counts[BYE] = counts.get(BYE, 0) + 1
                elif status in {"TBA", "TBD"} or (
                    not first and not second and status != "BYE"
                ):
                    classification = TBD
                    counts[TBD] = counts.get(TBD, 0) + 1
                player = first or second
                entries.append(
                    DrawEntrySummary(
                        stage=stage,
                        qualification=qualification,
                        round_name=round_name,
                        match_number=int(match.get("match_number") or 0),
                        status=status,
                        slot_classification=classification,
                        seed_label=None if seed in (None, "") else str(seed),
                        player_key=None
                        if not player
                        else str(player.get("player_key") or "") or None,
                        winner_player_key=(
                            None
                            if match.get("winner_player_key") in (None, "")
                            else str(match.get("winner_player_key"))
                        ),
                        next_slot_key=(
                            None
                            if match.get("next_slot_key") in (None, "")
                            else int(match.get("next_slot_key"))
                        ),
                    )
                )

    return DrawSummary(
        tournament_key=(
            None
            if tournament.get("tournament_key") in (None, "")
            else str(tournament.get("tournament_key"))
        ),
        tournament_name=(
            None
            if tournament.get("tournament_name") in (None, "")
            else str(tournament.get("tournament_name"))
        ),
        tournament_season=(
            None
            if tournament.get("tournament_season") in (None, "")
            else str(tournament.get("tournament_season"))
        ),
        tournament_surface=(
            None
            if tournament.get("tournament_surface") in (None, "")
            else str(tournament.get("tournament_surface"))
        ),
        source=None if result.get("source") is None else str(result.get("source")),
        bracket_count=len(brackets),
        entries=tuple(entries),
        counts=counts,
    )


__all__ = [
    "BYE",
    "LUCKY_LOSER",
    "QUALIFIER",
    "REPLACEMENT_DETECTION",
    "SEEDED",
    "SINGLE_SNAPSHOT_LIMITS",
    "TBD",
    "UNCLASSIFIED",
    "WILDCARD",
    "DrawEntrySummary",
    "DrawSummary",
    "classify_seed_label",
    "summarize_draw",
]
