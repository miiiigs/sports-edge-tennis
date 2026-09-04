from __future__ import annotations

import hashlib
from decimal import Decimal, ROUND_HALF_UP, localcontext

from sports_edge_tennis.market.contracts import (
    CANONICAL_ROLE_ORDER,
    MARKET_PRECISION,
    SnapshotRole,
)

_POWER_BRACKET_LO = Decimal("0.5")
_POWER_BRACKET_HI = Decimal("1.5")
_BISECT_ITERATIONS = 140


def _decimal_power(base: Decimal, exponent: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MARKET_PRECISION
        ctx.rounding = ROUND_HALF_UP
        return (exponent * base.ln()).exp()


def _power_objective(k: Decimal, q1: Decimal, q2: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MARKET_PRECISION
        ctx.rounding = ROUND_HALF_UP
        return _decimal_power(q1, k) + _decimal_power(q2, k) - Decimal("1")


def _solve_power_k(q1: Decimal, q2: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MARKET_PRECISION
        ctx.rounding = ROUND_HALF_UP

        lo = _POWER_BRACKET_LO
        hi = _POWER_BRACKET_HI
        f_lo = _power_objective(lo, q1, q2)
        f_hi = _power_objective(hi, q1, q2)

        while f_lo * f_hi > 0:
            if f_lo > 0:
                lo = hi
                hi = hi * Decimal("2")
            else:
                hi = lo
                lo = lo / Decimal("2")
            f_lo = _power_objective(lo, q1, q2)
            f_hi = _power_objective(hi, q1, q2)

        for _ in range(_BISECT_ITERATIONS):
            mid = (lo + hi) / Decimal("2")
            f_mid = _power_objective(mid, q1, q2)
            if f_mid > 0:
                lo = mid
            else:
                hi = mid

        return (lo + hi) / Decimal("2")


def power_devig(odds1: Decimal, odds2: Decimal) -> tuple[Decimal, Decimal]:
    """Return the two-way de-vigged fair probabilities using the POWER method.

    q_i = 1 / odds_i, find k such that sum(q_i^k) = 1, then p_i = q_i^k.
    """
    with localcontext() as ctx:
        ctx.prec = MARKET_PRECISION
        ctx.rounding = ROUND_HALF_UP
        q1 = Decimal("1") / odds1
        q2 = Decimal("1") / odds2
        if q1 + q2 == Decimal("1"):
            return q1, q2
        k = _solve_power_k(q1, q2)
        return _decimal_power(q1, k), _decimal_power(q2, k)


def booksum(odds1: Decimal, odds2: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MARKET_PRECISION
        ctx.rounding = ROUND_HALF_UP
        return Decimal("1") / odds1 + Decimal("1") / odds2


def edge_pct(p_model: Decimal, offered_price: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MARKET_PRECISION
        ctx.rounding = ROUND_HALF_UP
        return p_model * offered_price - Decimal("1")


def minimum_acceptable_price(p_model: Decimal, threshold: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MARKET_PRECISION
        ctx.rounding = ROUND_HALF_UP
        return (Decimal("1") + threshold) / p_model


def snapshot_lineage_hash(entries: tuple[tuple[SnapshotRole, int], ...] | list) -> str:
    """Deterministic SHA-256 lineage hash over canonical role + odds_snapshot_id.

    Only the immutable role and odds_snapshot_id participate; ordering is the
    canonical role order, never query order. Empty lineage yields the canonical
    SHA-256 of the empty string.
    """
    ordered = sorted(entries, key=lambda entry: CANONICAL_ROLE_ORDER.index(entry[0]))
    parts = [f"{role}:{odds_snapshot_id}" for role, odds_snapshot_id in ordered]
    canonical = "|".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
