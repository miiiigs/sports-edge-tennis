"""Prospective paper-bankroll evidence for Sports Edge Tennis.

This module never places a wager and never mutates production data.  It replays
client-publishable live BET decisions using the stake fraction and price captured on the
original decision row. Only the first published BET for a match is included so
hourly refreshes cannot manufacture duplicate paper positions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import psycopg

STARTING_BANKROLL = Decimal("1000.00")
MONEY_QUANTUM = Decimal("0.01")
SUPPORTED_STAKING_RULE = "QUARTER_KELLY_CAP_2_V1"


@dataclass(frozen=True)
class PaperBet:
    decision_id: int
    match_id: int
    decision_at: datetime
    settlement_status: str
    settled_at: datetime | None
    decimal_price: Decimal
    stake_fraction: Decimal
    selection: str | None
    player_1: str | None
    player_2: str | None
    tournament: str | None
    bookmaker: str | None


@dataclass(frozen=True)
class PaperBankrollPoint:
    at: datetime
    decision_id: int
    event: str
    bankroll_equity: Decimal
    cash: Decimal
    open_exposure: Decimal
    realized_profit: Decimal


@dataclass(frozen=True)
class PaperBankrollSummary:
    starting_bankroll: Decimal
    current_bankroll: Decimal
    cash: Decimal
    open_exposure: Decimal
    realized_profit: Decimal
    roi: Decimal
    bets: int
    settled: int
    pending: int
    wins: int
    losses: int
    voids: int
    skipped_without_staking_evidence: int
    max_drawdown: Decimal
    points: tuple[PaperBankrollPoint, ...]


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def load_paper_bets(conn: psycopg.Connection) -> tuple[PaperBet, ...]:
    """Load first-published live bets with reproducible quarter-Kelly evidence."""
    rows = conn.execute(
        """
        WITH publication_boundary AS (
            SELECT min(published_at) AS first_published_at
            FROM decisions
            WHERE published_at IS NOT NULL
        ),
        first_bet AS (
            SELECT DISTINCT ON (d.match_id)
                d.decision_id,
                d.match_id,
                COALESCE(d.published_at, d.as_of) AS as_of,
                d.selection_side,
                d.market_decimal_price,
                d.price_bookmaker_code,
                d.staking_rule_version,
                d.suggested_stake_fraction
            FROM decisions d
            CROSS JOIN publication_boundary pb
            WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
              AND d.decision_status = 'BET'
              AND d.market_type = 'MATCH_WINNER'
              AND (
                  d.published_at IS NOT NULL
                  OR pb.first_published_at IS NULL
                  OR d.as_of < pb.first_published_at
              )
            ORDER BY d.match_id, COALESCE(d.published_at, d.as_of), d.decision_id
        )
        SELECT
            fb.decision_id,
            fb.match_id,
            fb.as_of,
            COALESCE(ds.settlement_status::text, 'PENDING') AS settlement_status,
            ds.settled_at,
            fb.market_decimal_price,
            fb.suggested_stake_fraction,
            CASE fb.selection_side
                WHEN 1 THEN p1.canonical_name
                WHEN 2 THEN p2.canonical_name
                ELSE NULL
            END AS selection,
            p1.canonical_name,
            p2.canonical_name,
            t.canonical_name,
            fb.price_bookmaker_code,
            fb.staking_rule_version
        FROM first_bet fb
        JOIN matches m ON m.match_id = fb.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        LEFT JOIN LATERAL (
            SELECT player_1_id, player_2_id
            FROM match_versions mv
            WHERE mv.match_id = fb.match_id
              AND mv.as_of <= fb.as_of
            ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        LEFT JOIN players p1 ON p1.player_id = mv.player_1_id
        LEFT JOIN players p2 ON p2.player_id = mv.player_2_id
        LEFT JOIN decision_settlements ds ON ds.decision_id = fb.decision_id
        WHERE fb.staking_rule_version = %s
          AND fb.suggested_stake_fraction IS NOT NULL
          AND fb.suggested_stake_fraction > 0
        ORDER BY fb.as_of, fb.decision_id
        """,
        (SUPPORTED_STAKING_RULE,),
    ).fetchall()

    return tuple(
        PaperBet(
            decision_id=int(row[0]),
            match_id=int(row[1]),
            decision_at=row[2],
            settlement_status=str(row[3]),
            settled_at=row[4],
            decimal_price=Decimal(row[5]),
            stake_fraction=Decimal(row[6]),
            selection=str(row[7]) if row[7] is not None else None,
            player_1=str(row[8]) if row[8] is not None else None,
            player_2=str(row[9]) if row[9] is not None else None,
            tournament=str(row[10]) if row[10] is not None else None,
            bookmaker=str(row[11]) if row[11] is not None else None,
        )
        for row in rows
    )


def count_legacy_bets_without_staking_evidence(conn: psycopg.Connection) -> int:
    """Count first-published bets that predate reproducible staking evidence."""
    row = conn.execute(
        """
        WITH publication_boundary AS (
            SELECT min(published_at) AS first_published_at
            FROM decisions
            WHERE published_at IS NOT NULL
        ),
        first_bet AS (
            SELECT DISTINCT ON (d.match_id)
                d.match_id,
                d.staking_rule_version,
                d.suggested_stake_fraction
            FROM decisions d
            CROSS JOIN publication_boundary pb
            WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
              AND d.decision_status = 'BET'
              AND d.market_type = 'MATCH_WINNER'
              AND (
                  d.published_at IS NOT NULL
                  OR pb.first_published_at IS NULL
                  OR d.as_of < pb.first_published_at
              )
            ORDER BY d.match_id, COALESCE(d.published_at, d.as_of), d.decision_id
        )
        SELECT count(*)
        FROM first_bet
        WHERE staking_rule_version IS DISTINCT FROM %s
           OR suggested_stake_fraction IS NULL
           OR suggested_stake_fraction <= 0
        """,
        (SUPPORTED_STAKING_RULE,),
    ).fetchone()
    return int(row[0]) if row else 0


def simulate_paper_bankroll(
    bets: tuple[PaperBet, ...],
    *,
    starting_bankroll: Decimal = STARTING_BANKROLL,
    skipped_without_staking_evidence: int = 0,
) -> PaperBankrollSummary:
    """Replay persisted paper positions in timestamp order.

    Stake size is the stored suggested fraction of bankroll equity at publication.
    Open stake remains part of bankroll equity at cost until settlement, while
    cash is reduced by the amount committed.  This allows overlapping matches
    without pretending the same cash is freely available twice.
    """
    if starting_bankroll <= 0:
        raise ValueError("starting_bankroll must be positive")

    cash = _money(starting_bankroll)
    open_positions: dict[int, tuple[Decimal, Decimal]] = {}
    stake_by_decision: dict[int, Decimal] = {}
    points: list[PaperBankrollPoint] = []
    realized_profit = Decimal("0.00")
    wins = losses = voids = settled = 0

    events: list[tuple[datetime, int, str, PaperBet]] = []
    for bet in bets:
        events.append((bet.decision_at, bet.decision_id, "OPEN", bet))
        if bet.settlement_status != "PENDING" and bet.settled_at is not None:
            events.append((bet.settled_at, bet.decision_id, "SETTLE", bet))
    events.sort(key=lambda item: (item[0], 0 if item[2] == "SETTLE" else 1, item[1]))

    peak_equity = _money(starting_bankroll)
    max_drawdown = Decimal("0")

    for event_at, decision_id, event_type, bet in events:
        open_exposure = sum((stake for stake, _price in open_positions.values()), Decimal("0"))
        equity_before = cash + open_exposure

        if event_type == "OPEN":
            stake = _money(equity_before * bet.stake_fraction)
            if stake <= 0:
                continue
            if stake > cash:
                stake = cash
            cash = _money(cash - stake)
            open_positions[decision_id] = (stake, bet.decimal_price)
            stake_by_decision[decision_id] = stake
            realized_delta = Decimal("0.00")
        else:
            position = open_positions.pop(decision_id, None)
            if position is None:
                continue
            stake, price = position
            status = bet.settlement_status
            if status == "WON":
                payout = _money(stake * price)
                cash = _money(cash + payout)
                realized_delta = _money(payout - stake)
                wins += 1
                settled += 1
            elif status == "LOST":
                realized_delta = -stake
                losses += 1
                settled += 1
            elif status in {"VOID", "PUSH", "CANCELLED"}:
                cash = _money(cash + stake)
                realized_delta = Decimal("0.00")
                voids += 1
                settled += 1
            else:
                # Unknown terminal states remain open rather than inventing P/L.
                open_positions[decision_id] = position
                continue
            realized_profit = _money(realized_profit + realized_delta)

        open_exposure = sum((stake for stake, _price in open_positions.values()), Decimal("0"))
        equity = _money(cash + open_exposure)
        if equity > peak_equity:
            peak_equity = equity
        if peak_equity > 0:
            drawdown = (peak_equity - equity) / peak_equity
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        points.append(
            PaperBankrollPoint(
                at=event_at,
                decision_id=decision_id,
                event=event_type,
                bankroll_equity=equity,
                cash=cash,
                open_exposure=_money(open_exposure),
                realized_profit=realized_profit,
            )
        )

    open_exposure = _money(
        sum((stake for stake, _price in open_positions.values()), Decimal("0"))
    )
    current_bankroll = _money(cash + open_exposure)
    roi = (current_bankroll - starting_bankroll) / starting_bankroll

    return PaperBankrollSummary(
        starting_bankroll=_money(starting_bankroll),
        current_bankroll=current_bankroll,
        cash=cash,
        open_exposure=open_exposure,
        realized_profit=realized_profit,
        roi=roi,
        bets=len(stake_by_decision),
        settled=settled,
        pending=len(open_positions),
        wins=wins,
        losses=losses,
        voids=voids,
        skipped_without_staking_evidence=skipped_without_staking_evidence,
        max_drawdown=max_drawdown,
        points=tuple(points),
    )


def load_paper_bankroll_summary(
    conn: psycopg.Connection,
    *,
    starting_bankroll: Decimal = STARTING_BANKROLL,
) -> PaperBankrollSummary:
    bets = load_paper_bets(conn)
    skipped = count_legacy_bets_without_staking_evidence(conn)
    return simulate_paper_bankroll(
        bets,
        starting_bankroll=starting_bankroll,
        skipped_without_staking_evidence=skipped,
    )


__all__ = [
    "PaperBet",
    "PaperBankrollPoint",
    "PaperBankrollSummary",
    "STARTING_BANKROLL",
    "SUPPORTED_STAKING_RULE",
    "load_paper_bets",
    "load_paper_bankroll_summary",
    "simulate_paper_bankroll",
]
