"""Production system regimes, derived from immutable persisted lineage.

A "regime" is the production configuration that actually generated a decision.
Performance numbers are only meaningful inside one regime: pooling a Manager v1
bet with a Manager v2 bet answers neither "how is the current system doing" nor
"how did that system do".

This module never infers a regime. It reads what was recorded at decision time:

``manager_policy_versions.policy_version``
    via ``decisions.manager_consensus_result_id ->
    manager_consensus_results.manager_policy_version_id``. Immutable, written by
    the Manager when the decision was made.

``decisions.price_source_system``
    the provider that actually supplied the offered price for that bet.

The **data** provider (fixture source) is deliberately absent. It is not
persisted per run anywhere in the schema, so it is reported as unavailable
rather than guessed from timing. See ``DATA_PROVIDER_UNAVAILABLE``.

Regimes are discovered from the data, not enumerated here, so a future cutover
produces a new cohort without any code or dashboard edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import psycopg

# A production run. Experiments, backtests and unfinalized runs are excluded
# from every client-facing performance number. This mirrors the predicate
# already used by performance.evidence so the two surfaces cannot disagree.
PRODUCTION_RUN_PREDICATE = """
    pr.prediction_contract_version = 'predictive_agent_result_v1'
    AND pr.run_status = 'COMPLETED'
    AND pr.prediction_finalized_at IS NOT NULL
    AND pr.experiment_id IS NULL
"""

#: Regime key used when a decision predates versioned Manager lineage.
LEGACY_REGIME_KEY = "LEGACY_UNVERSIONED"

#: Shown wherever the data provider cannot be proven from persisted evidence.
DATA_PROVIDER_UNAVAILABLE = "Provider lineage unavailable"

_ODDS_PROVIDER_LABELS = {
    "PINNAPI": "Pinnacle",
    "API_TENNIS": "API-Tennis",
    "GOALSERVE_TENNIS": "Goalserve",
    "THE_ODDS_API": "The Odds API",
}


def regime_key_for(manager_policy_version: str | None) -> str:
    """Stable machine key for a regime."""
    if not manager_policy_version:
        return LEGACY_REGIME_KEY
    return f"MANAGER_{manager_policy_version.upper()}"


def regime_label_for(manager_policy_version: str | None) -> str:
    """Human wording. Never exposes a database identifier."""
    if not manager_policy_version:
        return "Legacy production regime"
    return f"Manager {manager_policy_version}"


@dataclass(frozen=True)
class RegimeIdentity:
    """Which production configuration a decision was produced under."""

    regime_key: str
    manager_policy_version: str | None

    @property
    def label(self) -> str:
        return regime_label_for(self.manager_policy_version)

    @property
    def is_legacy_unversioned(self) -> bool:
        return self.regime_key == LEGACY_REGIME_KEY


@dataclass(frozen=True)
class RegimeCohort:
    """One regime's realised production betting record."""

    regime_key: str
    manager_policy_version: str | None
    bets: int
    settled: int
    pending: int
    wins: int
    losses: int
    voids: int
    cancelled: int
    one_unit_profit: Decimal
    first_bet_date: date | None
    last_bet_date: date | None
    odds_sources: tuple[str, ...]

    @property
    def label(self) -> str:
        return regime_label_for(self.manager_policy_version)

    @property
    def roi(self) -> Decimal | None:
        return (self.one_unit_profit / Decimal(self.settled)) if self.settled else None

    @property
    def hit_rate(self) -> float | None:
        decided = self.wins + self.losses
        return (self.wins / decided) if decided else None

    @property
    def odds_provider_label(self) -> str:
        """Proven offered-price providers for this regime, in plain words."""
        if not self.odds_sources:
            return DATA_PROVIDER_UNAVAILABLE
        return " + ".join(
            _ODDS_PROVIDER_LABELS.get(code, code) for code in self.odds_sources
        )

    @property
    def period_text(self) -> str:
        if self.first_bet_date is None or self.last_bet_date is None:
            return "—"
        if self.first_bet_date == self.last_bet_date:
            return self.first_bet_date.strftime("%d %b %Y")
        return (
            f"{self.first_bet_date.strftime('%d %b %Y')} – "
            f"{self.last_bet_date.strftime('%d %b %Y')}"
        )


def resolve_current_regime(conn: psycopg.Connection) -> RegimeIdentity | None:
    """The regime the production system is generating picks under right now.

    Resolved from persisted production truth — the most recent completed,
    finalized, non-experiment run that actually produced a Manager result — not
    from runtime environment variables. An environment variable describes the
    next run's intent; it cannot retroactively describe the runs that already
    happened, and it would silently relabel history the moment it changed.
    """
    row = conn.execute(
        f"""
        SELECT mpv.policy_version
        FROM manager_consensus_results mcr
        JOIN prediction_runs pr
          ON pr.prediction_run_id = mcr.prediction_run_id
        JOIN manager_policy_versions mpv
          ON mpv.manager_policy_version_id = mcr.manager_policy_version_id
        WHERE {PRODUCTION_RUN_PREDICATE}
        ORDER BY pr.prediction_finalized_at DESC,
                 pr.prediction_run_id DESC,
                 mcr.manager_consensus_result_id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    version = str(row[0]) if row[0] else None
    return RegimeIdentity(
        regime_key=regime_key_for(version), manager_policy_version=version
    )


def _regime_expression() -> str:
    """SQL that resolves a decision's regime key from its own lineage."""
    return """
        COALESCE(
            'MANAGER_' || upper(mpv.policy_version),
            '""" + LEGACY_REGIME_KEY + """'
        )
    """


def first_production_bets_cte() -> str:
    """Shared CTE: the first publishable Type A BET per sporting match.

    This is the established Results contract and it stays **global**. The first
    bet for a match is chosen across all production decisions before any regime
    filter is applied, so cohorting can never promote a later duplicate bet on
    the same match into first place.
    """
    return f"""
        publication_boundary AS (
            SELECT min(published_at) AS first_published_at
            FROM decisions
            WHERE published_at IS NOT NULL
        ),
        first_bets AS (
            SELECT DISTINCT ON (d.match_id)
                d.decision_id,
                d.match_id,
                d.as_of,
                COALESCE(d.published_at, d.as_of) AS published_at,
                d.market_decimal_price,
                d.selection_side,
                d.price_bookmaker_code,
                d.consensus_probability,
                d.price_source_system,
                {_regime_expression()} AS regime_key,
                mpv.policy_version AS manager_policy_version
            FROM decisions d
            CROSS JOIN publication_boundary pb
            JOIN prediction_runs pr
              ON pr.prediction_run_id = d.prediction_run_id
            LEFT JOIN manager_consensus_results mcr
              ON mcr.manager_consensus_result_id = d.manager_consensus_result_id
            LEFT JOIN manager_policy_versions mpv
              ON mpv.manager_policy_version_id = mcr.manager_policy_version_id
            WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
              AND d.decision_status = 'BET'
              AND d.market_type = 'MATCH_WINNER'
              AND d.experiment_id IS NULL
              AND {PRODUCTION_RUN_PREDICATE}
              AND (
                  d.published_at IS NOT NULL
                  OR pb.first_published_at IS NULL
                  OR d.as_of < pb.first_published_at
              )
            ORDER BY d.match_id, COALESCE(d.published_at, d.as_of), d.decision_id
        )
    """


def load_regime_cohorts(conn: psycopg.Connection) -> tuple[RegimeCohort, ...]:
    """Realised production performance, grouped by the regime that produced it.

    Every legitimate historical bet appears in exactly one cohort. Nothing is
    recomputed, re-scored or dropped: the original price, the original
    selection and the actual settlement are used exactly as persisted.
    """
    rows = conn.execute(
        f"""
        WITH {first_production_bets_cte()},
        dated AS (
            SELECT
                b.*,
                COALESCE(
                    (mv.match_start_at AT TIME ZONE 'UTC')::date,
                    (b.published_at AT TIME ZONE 'UTC')::date
                ) AS match_date
            FROM first_bets b
            LEFT JOIN LATERAL (
                SELECT mv0.match_start_at
                FROM match_versions mv0
                WHERE mv0.match_id = b.match_id
                  AND mv0.as_of <= b.published_at
                  AND mv0.source_observed_at <= b.published_at
                ORDER BY mv0.as_of DESC,
                         mv0.source_observed_at DESC,
                         mv0.match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
        )
        SELECT
            d.regime_key,
            max(d.manager_policy_version) AS manager_policy_version,
            count(*)::int AS bets,
            count(*) FILTER (
                WHERE ds.settlement_status IN ('WON', 'LOST', 'VOID', 'PUSH', 'CANCELLED')
            )::int AS settled,
            count(*) FILTER (
                WHERE ds.settlement_status IS NULL OR ds.settlement_status = 'PENDING'
            )::int AS pending,
            count(*) FILTER (WHERE ds.settlement_status = 'WON')::int AS wins,
            count(*) FILTER (WHERE ds.settlement_status = 'LOST')::int AS losses,
            count(*) FILTER (WHERE ds.settlement_status IN ('VOID', 'PUSH'))::int AS voids,
            count(*) FILTER (WHERE ds.settlement_status = 'CANCELLED')::int AS cancelled,
            COALESCE(sum(
                CASE
                    WHEN ds.settlement_status = 'WON' THEN d.market_decimal_price - 1
                    WHEN ds.settlement_status = 'LOST' THEN -1
                    ELSE 0
                END
            ), 0) AS one_unit_profit,
            min(d.match_date) AS first_bet_date,
            max(d.match_date) AS last_bet_date,
            array_remove(
                array_agg(DISTINCT d.price_source_system), NULL
            ) AS odds_sources
        FROM dated d
        LEFT JOIN decision_settlements ds ON ds.decision_id = d.decision_id
        GROUP BY d.regime_key
        ORDER BY max(d.match_date) DESC NULLS LAST, d.regime_key
        """
    ).fetchall()
    return tuple(
        RegimeCohort(
            regime_key=str(row[0]),
            manager_policy_version=str(row[1]) if row[1] else None,
            bets=int(row[2]),
            settled=int(row[3]),
            pending=int(row[4]),
            wins=int(row[5]),
            losses=int(row[6]),
            voids=int(row[7]),
            cancelled=int(row[8]),
            one_unit_profit=Decimal(row[9]),
            first_bet_date=row[10],
            last_bet_date=row[11],
            odds_sources=tuple(sorted(row[12] or ())),
        )
        for row in rows
    )


def current_regime_started_at(
    conn: psycopg.Connection, regime_key: str
) -> datetime | None:
    """When the first production bet under this regime was published."""
    row = conn.execute(
        f"""
        WITH {first_production_bets_cte()}
        SELECT min(b.published_at)
        FROM first_bets b
        WHERE b.regime_key = %s
        """,
        (regime_key,),
    ).fetchone()
    return row[0] if row else None


__all__ = [
    "DATA_PROVIDER_UNAVAILABLE",
    "LEGACY_REGIME_KEY",
    "PRODUCTION_RUN_PREDICATE",
    "RegimeCohort",
    "RegimeIdentity",
    "current_regime_started_at",
    "first_production_bets_cte",
    "load_regime_cohorts",
    "regime_key_for",
    "regime_label_for",
    "resolve_current_regime",
]
