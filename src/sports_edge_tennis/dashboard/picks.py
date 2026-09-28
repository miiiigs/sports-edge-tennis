"""Client-facing pick view model.

Pure functions over persisted backend truth. No database access, no model
recalculation, no threshold of its own.

The client dashboard answers one question first: *what are we picking today?*
That requires collapsing the internal lifecycle into two concepts a reader can
hold in their head, without letting the underlying states blur together:

``MODEL PICK``
    The sporting models selected a winner. This includes Manager-qualified
    consensus picks and a clearly labelled tracking-only case where A1 and A2
    select the same player but A3 abstains and the A1/A2 confidence spread is
    wider than the Manager gate. The latter never becomes market-qualified.

``VALUE BET``
    A Manager-qualified model pick also cleared the Market value rules at the
    current offered price, and is a genuine actionable bet.

Other Manager NO_BET states are excluded from the main pick list. A qualified
pick blocked by the kill-only Veto is shown, but never as an executable bet and
never with a stake.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sports_edge_tennis.dashboard import presentation as pres
from sports_edge_tennis.runner.contracts import AGENT_1, AGENT_2, AGENT_3

# Internal classifications from pres.recommendation_classification, grouped into
# the two client-facing concepts plus the blocked state.
VALUE_BET = "VALUE_BET"
MODEL_PICK = "MODEL_PICK"
BLOCKED = "BLOCKED"
WIDE_SPREAD_CLASSIFICATION = "WIDE_CONFIDENCE_SPREAD"
WIDE_SPREAD_SUMMARY = (
    "Primary models agreed, but confidence spread was too wide. "
    "The displayed tracking probability is the A1/A2 average; this is not a "
    "Manager consensus and is never market-qualified for betting."
)

# Presentation-only weak-pick band. This never changes Manager, Market, Veto,
# staking, publication, or historical truth. It only lets the dashboard
# de-emphasize lower-confidence issued picks while keeping them visible.
WEAK_PICK_PROBABILITY_THRESHOLD = Decimal("0.600000")

KIND_LABELS = {
    VALUE_BET: "VALUE BET",
    MODEL_PICK: "MODEL PICK",
    BLOCKED: "NOT BETTING",
}

# Short, non-technical explanation of each kind. Text-only, so the distinction
# survives without colour.
KIND_SUMMARIES = {
    VALUE_BET: "The model likes this player and the current price is better than our model thinks it should be.",
    MODEL_PICK: "The model expects this player to win. The current price does not qualify it as a bet.",
    BLOCKED: "The model and the price both qualified, but a safety check stopped this one. We are not betting it.",
}


@dataclass(frozen=True)
class PickCard:
    """One client-visible selection.

    ``kind`` is the client concept. ``classification`` is retained so the
    underlying backend state stays inspectable and is never lost in
    translation.
    """

    match_id: int
    kind: str
    classification: str
    player: str
    opponent: str
    win_probability: Decimal | None
    tournament: str
    competition_tier: str | None
    scheduled_start: datetime | None
    offered_price: Decimal | None = None
    bookmaker: str | None = None
    edge_pct: Decimal | None = None
    stake_fraction: Decimal | None = None
    sharp_reference_price: Decimal | None = None
    market_reason_code: str | None = None
    published_at: datetime | None = None
    primary_a1_probability: Decimal | None = None
    primary_a2_probability: Decimal | None = None
    primary_average_probability: Decimal | None = None
    primary_probability_spread: Decimal | None = None

    @property
    def is_wide_spread(self) -> bool:
        return self.classification == WIDE_SPREAD_CLASSIFICATION

    @property
    def kind_label(self) -> str:
        if self.is_wide_spread:
            return "MODEL PICK · WIDE SPREAD"
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def kind_summary(self) -> str:
        if self.is_wide_spread:
            return WIDE_SPREAD_SUMMARY
        return KIND_SUMMARIES.get(self.kind, "")

    @property
    def is_actionable_bet(self) -> bool:
        """True only for a Market-qualified bet that the Veto did not kill."""
        return self.kind == VALUE_BET

    @property
    def is_weak(self) -> bool:
        """Dashboard-only confidence flag for issued Model Picks / Value Bets.

        Normal issued picks use Manager consensus probability. Wide-spread
        tracking picks use the explicit A1/A2 arithmetic mean for display
        filtering only; they remain non-Manager-qualified and non-bettable.
        Missing confidence is treated fail-closed as weak rather than promoted.
        """
        if self.kind not in (MODEL_PICK, VALUE_BET):
            return False
        if self.is_wide_spread:
            if self.primary_average_probability is None:
                return True
            return (
                Decimal(self.primary_average_probability)
                < WEAK_PICK_PROBABILITY_THRESHOLD
            )
        if self.win_probability is None:
            return True
        return Decimal(self.win_probability) < WEAK_PICK_PROBABILITY_THRESHOLD

    @property
    def weak_reason(self) -> str | None:
        if not self.is_weak:
            return None
        if self.is_wide_spread:
            if self.primary_average_probability is None:
                return "A1/A2 tracking probability is unavailable."
            return "A1/A2 tracking probability is below 60%."
        if self.win_probability is None:
            return "Model confidence is unavailable."
        return "Manager win probability is below 60%."

    @property
    def probability_text(self) -> str:
        return pres.percent(self.win_probability)

    @property
    def probability_basis_label(self) -> str:
        """Human-readable source of the headline probability.

        Normal picks report Manager consensus; wide-spread tracking rows report
        the A1/A2 average. The two bases must never read as the same thing.
        """
        if self.is_wide_spread:
            return "A1/A2 Tracking Average"
        return "Manager Consensus"

    @property
    def primary_a1_probability_text(self) -> str:
        return pres.percent(self.primary_a1_probability)

    @property
    def primary_a2_probability_text(self) -> str:
        return pres.percent(self.primary_a2_probability)

    @property
    def primary_average_probability_text(self) -> str:
        return pres.percent(self.primary_average_probability)

    @property
    def primary_probability_spread_text(self) -> str | None:
        if self.primary_probability_spread is None:
            return None
        return f"{float(self.primary_probability_spread) * 100:.2f}%"

    @property
    def stake_text(self) -> str | None:
        """Stake is shown only when a real executable stake exists.

        A Model Pick is not a bet, so it has no stake. A blocked pick has had
        its executable stake cleared. Neither may display a placeholder.
        """
        if not self.is_actionable_bet:
            return None
        if self.stake_fraction is None or Decimal(self.stake_fraction) <= 0:
            return None
        return f"{float(self.stake_fraction) * 100:.2f}%"

    @property
    def edge_text(self) -> str | None:
        if self.edge_pct is None:
            return None
        return pres.precise_percent(self.edge_pct)

    @property
    def start_text(self) -> str:
        return pres.start_time(self.scheduled_start)

    @property
    def tier_text(self) -> str:
        return pres.tier_label(self.competition_tier)

    @property
    def market_reason(self) -> str | None:
        return pres.market_reason_explanation(self.market_reason_code)


@dataclass(frozen=True)
class TodayPicks:
    """Everything the Today page needs, already filtered."""

    picks: tuple[PickCard, ...]
    value_bet_count: int
    model_pick_count: int
    blocked_count: int
    evaluated_count: int

    @property
    def total(self) -> int:
        return len(self.picks)

    @property
    def is_empty(self) -> bool:
        return not self.picks

    @property
    def count_text(self) -> str:
        total = self.total
        return "1 pick today" if total == 1 else f"{total} picks today"

    @property
    def qualified_model_pick_count(self) -> int:
        """Issued Manager-qualified Model Picks (includes those that became Value Bets)."""
        return sum(
            1
            for card in self.picks
            if card.kind in (MODEL_PICK, VALUE_BET) and not card.is_wide_spread
        )

    @property
    def wide_spread_model_pick_count(self) -> int:
        return sum(1 for card in self.picks if card.is_wide_spread)

    @property
    def total_model_pick_count(self) -> int:
        """Unique Model Picks: qualified issued picks plus additive Wide-Spread tracking."""
        return self.qualified_model_pick_count + self.wide_spread_model_pick_count


def pick_kind(classification: str) -> str | None:
    """Map an internal classification to a client concept.

    Returns ``None`` when the record is not a pick at all, which is the
    filter that keeps non-selections off the client dashboard.
    """
    if classification == "HIGH_CONFIDENCE_VALUE":
        return VALUE_BET
    if classification == "VETOED":
        return BLOCKED
    if classification in {"HIGH_CONFIDENCE_PICK", "PRICE_PENDING"}:
        return MODEL_PICK
    # "NO_BET" means the sporting models never selected a winner.
    return None


def _sort_key(card: PickCard) -> tuple[int, float, str]:
    """Value bets first, then model picks, then blocked; each by start time."""
    order = {VALUE_BET: 0, MODEL_PICK: 1, BLOCKED: 2}
    start = (
        card.scheduled_start.timestamp()
        if card.scheduled_start is not None
        else float("inf")
    )
    return (order.get(card.kind, 3), start, card.player)


def wide_spread_primary_details(
    match,
) -> tuple[int, Decimal, Decimal, Decimal] | None:
    """Return same-side A1/A2 details for the A3-abstention spread rejection.

    This is presentation-only. It does not alter Manager eligibility or create
    a market decision. The Manager reason is treated as necessary but not
    sufficient: persisted A1/A2 predictions must also independently prove the
    same selected side, and A3 must be persisted as ABSTAINED.

    The caller decides whether the returned A1/A2 mean clears the current 60%
    rule. The read model uses this to keep Today's primary population aligned
    with the Model Picks ledger without changing any stored decision.
    """
    if (
        match.manager_status != "NO_BET"
        or match.reason_code != "AGENT3_ABSTENTION_SPREAD_EXCEEDED"
    ):
        return None

    a1 = match.agents.get(AGENT_1)
    a2 = match.agents.get(AGENT_2)
    a3 = match.agents.get(AGENT_3)
    if (
        a1 is None
        or a2 is None
        or a3 is None
        or a1.status != "PREDICTED"
        or a2.status != "PREDICTED"
        or a3.status != "ABSTAINED"
        or a1.selection not in (1, 2)
        or a2.selection != a1.selection
        or a1.probability is None
        or a2.probability is None
    ):
        return None

    a1_probability = Decimal(a1.probability)
    a2_probability = Decimal(a2.probability)
    return (
        int(a1.selection),
        a1_probability,
        a2_probability,
        abs(a1_probability - a2_probability),
    )


def build_pick_card(match, decision) -> PickCard | None:
    """Build one card, or ``None`` when the match is not a client-visible pick.

    ``match`` is a ``read_model.MatchView``; ``decision`` is an optional
    ``market_overlay.MarketDecisionView`` for the same match.
    """
    wide_details = wide_spread_primary_details(match)
    if wide_details is not None:
        selected_side, a1_probability, a2_probability, spread = wide_details
        player = pres.side_name(selected_side, match.player_1, match.player_2)
        if not player:
            return None
        opponent = match.player_2 if player == match.player_1 else match.player_1
        average_probability = (a1_probability + a2_probability) / Decimal("2")
        return PickCard(
            match_id=match.match_id,
            kind=MODEL_PICK,
            classification=WIDE_SPREAD_CLASSIFICATION,
            player=player,
            opponent=opponent,
            win_probability=None,
            tournament=match.tournament,
            competition_tier=match.competition_tier,
            scheduled_start=match.scheduled_start,
            primary_a1_probability=a1_probability,
            primary_a2_probability=a2_probability,
            primary_average_probability=average_probability,
            primary_probability_spread=spread,
        )

    decision_status = decision.decision_status if decision is not None else None
    classification = pres.recommendation_classification(
        match.manager_status, decision_status
    )
    kind = pick_kind(classification)
    if kind is None:
        return None

    player = match.selected_player
    if not player:
        # A consensus without a resolvable selected side is not presentable.
        return None
    opponent = match.player_2 if player == match.player_1 else match.player_1

    return PickCard(
        match_id=match.match_id,
        kind=kind,
        classification=classification,
        player=player,
        opponent=opponent,
        win_probability=match.consensus_probability,
        tournament=match.tournament,
        competition_tier=match.competition_tier,
        scheduled_start=match.scheduled_start,
        offered_price=decision.market_decimal_price if decision else None,
        bookmaker=decision.price_bookmaker_code if decision else None,
        edge_pct=decision.edge_pct if decision else None,
        stake_fraction=decision.suggested_stake_fraction if decision else None,
        sharp_reference_price=decision.sharp_reference_price if decision else None,
        market_reason_code=decision.reason_code if decision else None,
        published_at=decision.published_at if decision else None,
    )


def build_today_picks(matches, market_by_match) -> TodayPicks:
    """Filter an evaluated slate down to the client-visible picks.

    ``matches`` is every evaluated target in the run. Manager consensus picks
    survive, plus the explicit A1+A2 same-side / A3-abstain wide-spread tracking
    case. All other Manager NO_BET records remain off the primary pick list.
    """
    cards: list[PickCard] = []
    for match in matches:
        card = build_pick_card(match, market_by_match.get(match.match_id))
        if card is not None:
            cards.append(card)

    cards.sort(key=_sort_key)
    return TodayPicks(
        picks=tuple(cards),
        value_bet_count=sum(1 for c in cards if c.kind == VALUE_BET),
        model_pick_count=sum(1 for c in cards if c.kind == MODEL_PICK),
        blocked_count=sum(1 for c in cards if c.kind == BLOCKED),
        evaluated_count=len(matches),
    )


def build_value_opportunities(matches, market_by_match) -> tuple[PickCard, ...]:
    """Only the market-qualified bets, for the dedicated Value page."""
    today = build_today_picks(matches, market_by_match)
    return tuple(card for card in today.picks if card.kind == VALUE_BET)


__all__ = [
    "BLOCKED",
    "KIND_LABELS",
    "KIND_SUMMARIES",
    "MODEL_PICK",
    "VALUE_BET",
    "WIDE_SPREAD_CLASSIFICATION",
    "WEAK_PICK_PROBABILITY_THRESHOLD",
    "PickCard",
    "TodayPicks",
    "build_pick_card",
    "build_today_picks",
    "build_value_opportunities",
    "pick_kind",
    "wide_spread_primary_details",
]
