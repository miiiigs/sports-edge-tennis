"""Versioned tennis market catalogue and payload-orientation diagnostics.

The tennis match-winner definition is not inferred from a tutorial. It is a
versioned definition validated against the stored OddsPapi tennis corpus:

* sport ``12`` (tennis);
* market ``121``;
* exactly two outcomes, ``121`` and ``122``;
* outcome order is participant-1 then participant-2, as supplied by the
  fixture's ``participant1Id`` / ``participant2Id``.

The published tutorial's ``171``/``172`` is Counter-Strike moneyline in the
current catalogue and appears in zero stored tennis payloads. A caller may
supply a different catalogue entry, but only an explicitly validated one; the
adapter never guesses from "any two-outcome market".

This module is deliberately pure. It does not make provider calls, persist
rows, or silently reinterpret a changed schema. Unexpected market IDs, outcome
IDs, outcome counts or participant orientation produce explicit diagnostic
alerts that an operator must review.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

ODDSPAPI_TENNIS_SPORT_ID = 12
ODDSPAPI_TENNIS_CATALOGUE_VERSION = "ODDSPAPI_TENNIS_CATALOGUE_V1"


@dataclass(frozen=True)
class TennisMarketDefinition:
    """One validated, versioned market definition.

    ``outcome_ids`` are ordered: the first belongs to participant 1 and the
    second to participant 2. Persisting the order is what makes participant
    orientation auditable; reordering them would silently flip every price.
    """

    provider_code: str
    sport_id: int
    market_id: str
    outcome_ids: tuple[str, str]
    selection_type_order: tuple[str, str]
    valid_from: datetime
    evidence: str
    definition_version: str

    def __post_init__(self) -> None:
        if len(self.outcome_ids) != 2 or len(set(self.outcome_ids)) != 2:
            raise ValueError("a tennis match-winner definition needs two distinct outcomes")
        if self.selection_type_order != ("PLAYER_1", "PLAYER_2"):
            raise ValueError("outcome order must map to PLAYER_1 then PLAYER_2")
        if self.valid_from.tzinfo is None:
            raise ValueError("valid_from must be timezone-aware")
        if not self.market_id.strip():
            raise ValueError("market_id must be non-empty")


ODDSPAPI_TENNIS_MATCH_WINNER = TennisMarketDefinition(
    provider_code="ODDSPAPI",
    sport_id=ODDSPAPI_TENNIS_SPORT_ID,
    market_id="121",
    outcome_ids=("121", "122"),
    selection_type_order=("PLAYER_1", "PLAYER_2"),
    valid_from=datetime(2026, 9, 23, tzinfo=UTC),
    evidence=(
        "Validated against 11,087 stored tennis payloads (2026-11-26 checkpoint); "
        "every payload carries market 121 with outcomes 121/122. Market 171 is "
        "Counter-Strike moneyline and appears in zero tennis payloads."
    ),
    definition_version=ODDSPAPI_TENNIS_CATALOGUE_VERSION,
)

TENNIS_MARKET_CATALOGUE: tuple[TennisMarketDefinition, ...] = (
    ODDSPAPI_TENNIS_MATCH_WINNER,
)

ALERT_SEVERITY = ("INFO", "WARNING", "CRITICAL")


@dataclass(frozen=True)
class MarketDiagnosticAlert:
    code: str
    severity: str
    detail: str

    def __post_init__(self) -> None:
        if self.severity not in ALERT_SEVERITY:
            raise ValueError(f"unsupported alert severity {self.severity!r}")
        if not self.code.strip():
            raise ValueError("alert code must be non-empty")


def catalogue_for(
    provider_code: str,
    *,
    sport_id: int = ODDSPAPI_TENNIS_SPORT_ID,
    market_id: str | None = None,
) -> TennisMarketDefinition | None:
    """Return the versioned definition for a provider/sport/market."""
    for definition in TENNIS_MARKET_CATALOGUE:
        if definition.provider_code != provider_code or definition.sport_id != sport_id:
            continue
        if market_id is not None and definition.market_id != str(market_id):
            continue
        return definition
    return None


def outcome_ids_for(definition: TennisMarketDefinition) -> tuple[str, str]:
    """Ordered outcome ids used to orient PLAYER_1 and PLAYER_2."""
    return definition.outcome_ids


def _two_outcome_markets(markets: Mapping[str, Any]) -> list[tuple[str, tuple[str, ...]]]:
    found: list[tuple[str, tuple[str, ...]]] = []
    for market_id, raw_market in markets.items():
        if not isinstance(raw_market, Mapping):
            continue
        outcomes = raw_market.get("outcomes")
        if not isinstance(outcomes, Mapping):
            continue
        keys = tuple(str(key) for key in outcomes)
        if len(keys) == 2:
            found.append((str(market_id), keys))
    return found


def diagnose_payload(
    payload: Mapping[str, Any],
    *,
    expected: TennisMarketDefinition = ODDSPAPI_TENNIS_MATCH_WINNER,
) -> list[MarketDiagnosticAlert]:
    """Diagnose live-shape payload market/schema/orientation drift.

    The function accepts the live ``bookmakerOdds`` shape and the historical
    ``bookmakers`` shape. It never raises on malformed provider data: an alert
    is the fail-closed signal, and the caller decides whether to quarantine.
    """
    alerts: list[MarketDiagnosticAlert] = []
    sport_id = payload.get("sportId")
    if sport_id is not None and str(sport_id) != str(expected.sport_id):
        alerts.append(MarketDiagnosticAlert(
            "UNEXPECTED_SPORT_ID",
            "CRITICAL",
            f"payload sportId={sport_id!r}; expected {expected.sport_id} for tennis",
        ))

    live = payload.get("bookmakerOdds")
    historical = payload.get("bookmakers")
    if not isinstance(live, Mapping) and not isinstance(historical, Mapping):
        alerts.append(MarketDiagnosticAlert(
            "NO_BOOKMAKER_PAYLOAD",
            "WARNING",
            "payload has neither bookmakerOdds nor bookmakers",
        ))
        return alerts

    books = live if isinstance(live, Mapping) else historical
    seen_ids: set[str] = set()
    unexpected_markets: set[str] = set()
    market_present = False

    for slug, raw_book in books.items():
        if not isinstance(raw_book, Mapping):
            alerts.append(MarketDiagnosticAlert(
                "BOOKMAKER_NOT_OBJECT",
                "WARNING",
                f"bookmaker {slug!r} is {type(raw_book).__name__}, not an object",
            ))
            continue
        markets = raw_book.get("markets")
        if not isinstance(markets, Mapping):
            alerts.append(MarketDiagnosticAlert(
                "BOOKMAKER_WITHOUT_MARKETS",
                "WARNING",
                f"bookmaker {slug!r} has no markets object",
            ))
            continue
        market = markets.get(expected.market_id)
        if isinstance(market, Mapping):
            market_present = True
            outcomes = market.get("outcomes")
            if not isinstance(outcomes, Mapping):
                alerts.append(MarketDiagnosticAlert(
                    "WINNER_MARKET_WITHOUT_OUTCOMES",
                    "CRITICAL",
                    f"market {expected.market_id} has no outcomes object",
                ))
            else:
                keys = tuple(str(key) for key in outcomes)
                if keys != expected.outcome_ids:
                    alerts.append(MarketDiagnosticAlert(
                        "OUTCOME_IDS_CHANGED",
                        "CRITICAL",
                        f"market {expected.market_id} outcomes {keys}; "
                        f"catalogue requires {expected.outcome_ids}",
                    ))
                for outcome_id in expected.outcome_ids:
                    raw_outcome = outcomes.get(outcome_id)
                    if not isinstance(raw_outcome, Mapping):
                        alerts.append(MarketDiagnosticAlert(
                            "OUTCOME_MISSING",
                            "CRITICAL",
                            f"market {expected.market_id} is missing outcome {outcome_id}",
                        ))
                        continue
                    players = raw_outcome.get("players")
                    if isinstance(players, Mapping):
                        seen_ids.update(str(key) for key in players)
                        for player_key, player_value in players.items():
                            if not isinstance(player_value, Mapping):
                                alerts.append(MarketDiagnosticAlert(
                                    "NON_OBJECT_PLAYER_STATE",
                                    "WARNING",
                                    f"market {expected.market_id} outcome {outcome_id} "
                                    f"player {player_key!r} is "
                                    f"{type(player_value).__name__}; historical "
                                    "list states are not live prices",
                                ))
        else:
            for market_id, outcome_ids in _two_outcome_markets(markets):
                if market_id != expected.market_id:
                    unexpected_markets.add(f"{market_id}:{','.join(outcome_ids)}")

    if not market_present:
        alerts.append(MarketDiagnosticAlert(
            "WINNER_MARKET_ABSENT",
            "CRITICAL" if unexpected_markets else "WARNING",
            f"catalogue market {expected.market_id} absent"
            + (f"; two-outcome markets seen: {sorted(unexpected_markets)}"
               if unexpected_markets else ""),
        ))
    if seen_ids and seen_ids != {"0"}:
        alerts.append(MarketDiagnosticAlert(
            "UNEXPECTED_PLAYER_KEY",
            "WARNING",
            f"outcome players keys {sorted(seen_ids)}; live shape expects ['0']",
        ))
    return alerts


def orientation_evidence(
    payload: Mapping[str, Any],
    *,
    market_id: str,
    outcome_ids: Sequence[str],
) -> dict[str, Any]:
    """Describe the evidence used to orient the two selections.

    Named players can independently confirm orientation when present. The
    OddsPapi live shape normally leaves ``playerName`` null, so orientation is
    then anchored only by the validated outcome-id order. That limitation is
    reported rather than hidden.
    """
    participant_1 = str(payload.get("participant1Id") or "").strip()
    participant_2 = str(payload.get("participant2Id") or "").strip()
    names: list[str | None] = []
    books = payload.get("bookmakerOdds")
    if isinstance(books, Mapping):
        for raw_book in books.values():
            if not isinstance(raw_book, Mapping):
                continue
            markets = raw_book.get("markets")
            if not isinstance(markets, Mapping):
                continue
            market = markets.get(str(market_id))
            if not isinstance(market, Mapping):
                continue
            outcomes = market.get("outcomes")
            if not isinstance(outcomes, Mapping):
                continue
            for outcome_id in outcome_ids:
                raw_outcome = outcomes.get(str(outcome_id))
                players = raw_outcome.get("players") if isinstance(raw_outcome, Mapping) else None
                player = players.get("0") if isinstance(players, Mapping) else None
                names.append(player.get("playerName") if isinstance(player, Mapping) else None)
            break
    named = [str(value).strip() for value in names if value]
    if len(named) >= 2:
        basis = "NAMED_PLAYERS_PRESENT"
    else:
        basis = "VALIDATED_OUTCOME_ID_ORDER"
    return {
        "market_id": str(market_id),
        "outcome_ids": [str(value) for value in outcome_ids],
        "participant_1_provider_id": participant_1 or None,
        "participant_2_provider_id": participant_2 or None,
        "orientation_basis": basis,
        "named_player_values": named,
        "named_orientation_confirmed": len(named) >= 2,
    }
