"""Production system regimes, derived from immutable persisted lineage.

A "regime" is the production configuration that actually generated a decision.
Performance numbers are only meaningful inside one regime: pooling a bet from
one configuration with a bet from another answers neither "how is the current
system doing" nor "how did that system do".

Manager policy version alone is **not** a system identity. Agent 2 can be
retrained and redeployed while the Manager stays v1, and the Market edge
threshold can change while both stay put. Either changes which bets exist and
at what price, so either starts a new system. The fingerprint therefore spans
every dimension the schema records immutably at decision time:

``manager_policy_versions.policy_version``
    via ``decisions.manager_consensus_result_id ->
    manager_consensus_results.manager_policy_version_id``.

``agent_config_versions.config_version``, once per agent
    via the ``predictions`` rows of the decision's own
    ``(prediction_run_id, match_id)``. ``agent_config_versions`` is the
    schema's authoritative replay-critical Agent configuration identity.

``market_policy_versions.policy_version``
    via ``decisions.market_policy_version_id``.

``decision_veto_audits.veto_policy_version``
    the Veto policy that cleared this specific decision. A Veto change alters
    which bets survive, so it is part of the identity. It is the one dimension
    whose table is created lazily by the Veto service rather than by a
    migration, so its absence is recorded as ``MISSING`` rather than assumed.

Deliberately absent:

*Data provider.* Not persisted per run anywhere in the schema, so it is
reported as unavailable rather than guessed from timing. See
``DATA_PROVIDER_UNAVAILABLE``.

*Staking.* ``decisions.staking_rule_version`` is persisted, but staking is
computed after a decision is already a BET and cannot change whether the bet
enters the ledger or at what price. Results ROI is a flat one-unit number, so
a staking change cannot move it. Splitting cohorts on it would fragment the
sample for no gain. Bankroll performance is a different question and would
need it; one-unit ROI does not.

Regimes are discovered from the data, not enumerated here, so a future cutover
produces a new cohort without any code or dashboard edit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
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

#: Prefix for a regime whose five required dimensions are all proven.
COMPLETE_REGIME_PREFIX = "REGIME_"

#: Prefix for a regime missing or conflicting on a required dimension. A key
#: under this prefix can never equal one under ``COMPLETE_REGIME_PREFIX``, so
#: unattributable history can never be counted as the current system.
LEGACY_REGIME_PREFIX = "LEGACY_"

#: The latest production run disagreed with itself about its own lineage.
#: Reported rather than collapsed onto whichever configuration happened to win.
INCONSISTENT_CURRENT_REGIME_KEY = "LEGACY_INCONSISTENT_CURRENT_RUN"

#: Shown wherever the data provider cannot be proven from persisted evidence.
DATA_PROVIDER_UNAVAILABLE = "Provider lineage unavailable"

#: Fingerprint slots, in canonical order. ``agent_code`` is the enum value the
#: ``predictions`` rows carry.
AGENT_SLOTS: tuple[tuple[str, str], ...] = (
    ("A1", "AGENT_1_SURFACE_ELO"),
    ("A2", "AGENT_2_HIERARCHICAL_MARKOV"),
    ("A3", "AGENT_3_INTRANSITIVITY"),
)

#: A dimension was recorded exactly once, so it is proven.
STATE_OK = "OK"
#: Nothing was recorded for this dimension. Never guessed.
STATE_MISSING = "MISSING"
#: Two or more different values were recorded where one was expected.
STATE_CONFLICT = "CONFLICT"

_ODDS_PROVIDER_LABELS = {
    "PINNAPI": "Pinnacle",
    "API_TENNIS": "API-Tennis",
    "GOALSERVE_TENNIS": "Goalserve",
    "THE_ODDS_API": "The Odds API",
}


# ------------------------------------------------------- fingerprint format
#
# The canonical fingerprint is built identically in SQL and in Python. SQL owns
# cohorting (it has to: the grouping happens in the database), Python owns
# ``resolve_current_regime`` reporting and lets tests reason about keys without
# a round trip. ``test_sql_and_python_fingerprints_agree`` pins the two
# together, so a change to one that is not mirrored in the other fails loudly
# rather than silently splitting a cohort in half.


def _escape(value: str) -> str:
    """Make a recorded version string unambiguous inside the fingerprint."""
    return value.replace("\\", "\\\\").replace("|", "\\|").replace(":", "\\:")


def _sql_escape(expr: str) -> str:
    """The SQL mirror of :func:`_escape`."""
    return (
        f"replace(replace(replace({expr}, '\\', '\\\\'), '|', '\\|'), ':', '\\:')"
    )


@dataclass(frozen=True)
class Dimension:
    """One recorded dimension of a system identity."""

    tag: str
    state: str
    value: str | None

    @classmethod
    def of(cls, tag: str, value: str | None) -> "Dimension":
        if value is None or not str(value).strip():
            return cls(tag, STATE_MISSING, None)
        return cls(tag, STATE_OK, str(value))

    @classmethod
    def conflicted(cls, tag: str) -> "Dimension":
        return cls(tag, STATE_CONFLICT, None)

    @property
    def is_proven(self) -> bool:
        return self.state == STATE_OK

    def canonical(self) -> str:
        return f"{self.tag}:{self.state}:{_escape(self.value or '')}"


@dataclass(frozen=True)
class RegimeFingerprint:
    """The complete persisted identity of a bet-generating configuration."""

    manager: Dimension
    agents: tuple[Dimension, ...]
    market: Dimension
    veto: Dimension

    @property
    def required(self) -> tuple[Dimension, ...]:
        """Dimensions that must be proven before a regime counts as current.

        Veto is fingerprinted but not required: ``decision_veto_audits`` is
        created by the Veto service rather than by a migration, so demanding it
        would strand an otherwise fully-identified system in legacy on a
        database where the Veto layer has not written yet. A missing Veto
        record still produces a *different* fingerprint from a present one, so
        the two never pool.
        """
        return (self.manager, *self.agents, self.market)

    @property
    def is_complete(self) -> bool:
        return all(dimension.is_proven for dimension in self.required)

    def canonical(self) -> str:
        parts = [self.manager, *self.agents, self.market, self.veto]
        return "|".join(part.canonical() for part in parts)

    @property
    def regime_key(self) -> str:
        prefix = COMPLETE_REGIME_PREFIX if self.is_complete else LEGACY_REGIME_PREFIX
        digest = hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()
        return f"{prefix}{digest[:16]}"


def is_complete_regime_key(regime_key: str | None) -> bool:
    """True only for a regime whose required lineage was fully proven."""
    return bool(regime_key) and regime_key.startswith(COMPLETE_REGIME_PREFIX)


def regime_label_for(
    manager_policy_version: str | None,
    *,
    complete: bool,
    sequence: int | None = None,
) -> str:
    """Human wording. Never exposes a database identifier or a config id.

    An unattributable regime is never given an invented Manager version: it is
    named for what it is, so nobody reads a number under it as that system's
    record.
    """
    if not complete or not manager_policy_version:
        return "Legacy production regime"
    label = f"Manager {manager_policy_version}"
    if sequence is not None:
        label += f" · configuration {sequence}"
    return label


@dataclass(frozen=True)
class RegimeIdentity:
    """Which production configuration a decision was produced under."""

    regime_key: str
    manager_policy_version: str | None
    agent_config_versions: tuple[str | None, ...] = (None, None, None)
    market_policy_version: str | None = None
    veto_policy_version: str | None = None
    #: The latest production run did not agree with itself about its lineage.
    is_inconsistent: bool = False

    @property
    def has_complete_lineage(self) -> bool:
        return is_complete_regime_key(self.regime_key)

    @property
    def label(self) -> str:
        """Concise wording for the live system. Brian never reads a config id."""
        if self.is_inconsistent:
            return "Current configuration could not be determined"
        if not self.has_complete_lineage:
            return "Legacy production regime"
        return f"Manager {self.manager_policy_version} · current model configuration"

    @property
    def is_legacy_unversioned(self) -> bool:
        return not self.has_complete_lineage


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
    agent_config_versions: tuple[str | None, ...] = (None, None, None)
    market_policy_version: str | None = None
    veto_policy_version: str | None = None
    #: Chronological position among all regimes, oldest first. A display
    #: ordinal, not an identifier; it is never used to match a regime.
    sequence: int | None = None

    @property
    def has_complete_lineage(self) -> bool:
        return is_complete_regime_key(self.regime_key)

    @property
    def label(self) -> str:
        return regime_label_for(
            self.manager_policy_version,
            complete=self.has_complete_lineage,
            sequence=self.sequence,
        )

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


# --------------------------------------------------------------- SQL pieces


def veto_audits_available(conn: psycopg.Connection) -> bool:
    """Whether the Veto service has ever written its audit table here.

    ``decision_veto_audits`` is created on demand by ``veto.service`` rather
    than by a migration, so a read-only surface cannot assume it exists. When
    it does not, every decision reports ``MISSING`` for Veto — exactly what a
    decision with no audit row reports — so creating the table later does not
    silently renumber existing regimes.
    """
    row = conn.execute("SELECT to_regclass('public.decision_veto_audits')").fetchone()
    return bool(row and row[0])


def _agent_lineage_lateral(alias: str = "d") -> str:
    """Per-agent config identity for one decision's own run and match.

    A LATERAL aggregate, not a join: joining ``predictions`` directly would
    multiply each decision by its three agent rows and corrupt every count
    downstream. ``count(DISTINCT ...)`` is what makes an inconsistent run
    visible instead of silently collapsed — two configs for one agent is
    reported as a conflict, not averaged away.
    """
    selects = []
    for tag, agent_code in AGENT_SLOTS:
        selects.append(
            f"count(DISTINCT acv.config_version) "
            f"FILTER (WHERE p.agent_code = '{agent_code}') AS {tag.lower()}_n"
        )
        selects.append(
            f"min(acv.config_version) "
            f"FILTER (WHERE p.agent_code = '{agent_code}') AS {tag.lower()}_v"
        )
    return f"""
        LEFT JOIN LATERAL (
            SELECT {", ".join(selects)}
            FROM predictions p
            JOIN agent_config_versions acv
              ON acv.agent_config_version_id = p.agent_config_version_id
            WHERE p.prediction_run_id = {alias}.prediction_run_id
              AND p.match_id = {alias}.match_id
              AND p.experiment_id IS NULL
        ) agents ON TRUE
    """


def _dimension_sql(tag: str, state_expr: str, value_expr: str) -> str:
    """One ``TAG:STATE:VALUE`` fingerprint element."""
    return (
        f"'{tag}:' || {state_expr} || ':' || "
        f"COALESCE({_sql_escape(value_expr)}, '')"
    )


def _simple_dimension_sql(tag: str, value_expr: str) -> str:
    state = (
        f"CASE WHEN {value_expr} IS NULL OR btrim({value_expr}) = '' "
        f"THEN '{STATE_MISSING}' ELSE '{STATE_OK}' END"
    )
    return _dimension_sql(tag, state, value_expr)


def _agent_dimension_sql(tag: str) -> str:
    slot = tag.lower()
    state = (
        f"CASE WHEN agents.{slot}_n = 1 THEN '{STATE_OK}' "
        f"WHEN COALESCE(agents.{slot}_n, 0) = 0 THEN '{STATE_MISSING}' "
        f"ELSE '{STATE_CONFLICT}' END"
    )
    return _dimension_sql(tag, state, f"CASE WHEN agents.{slot}_n = 1 THEN agents.{slot}_v END")


def _veto_value_sql(*, veto_available: bool) -> str:
    # A database without the audit table is indistinguishable from a decision
    # the Veto service never audited, on purpose: both are MISSING, so the key
    # does not change the day the table appears.
    return "dva.veto_policy_version" if veto_available else "NULL::text"


def _fingerprint_sql(*, veto_available: bool) -> str:
    parts = [
        _simple_dimension_sql("MGR", "mpv.policy_version"),
        *(_agent_dimension_sql(tag) for tag, _ in AGENT_SLOTS),
        _simple_dimension_sql("MKT", "mkt.policy_version"),
        _simple_dimension_sql("VETO", _veto_value_sql(veto_available=veto_available)),
    ]
    return " || '|' || ".join(parts)


def _complete_sql() -> str:
    agent_checks = " AND ".join(
        f"agents.{tag.lower()}_n = 1" for tag, _ in AGENT_SLOTS
    )
    return f"""
        mpv.policy_version IS NOT NULL AND btrim(mpv.policy_version) <> ''
        AND mkt.policy_version IS NOT NULL AND btrim(mkt.policy_version) <> ''
        AND {agent_checks}
    """


def _regime_key_sql(*, veto_available: bool) -> str:
    """SQL that resolves a decision's regime key from its own lineage."""
    return f"""
        (CASE WHEN {_complete_sql()}
              THEN '{COMPLETE_REGIME_PREFIX}'
              ELSE '{LEGACY_REGIME_PREFIX}' END)
        || substr(
            encode(sha256(convert_to({_fingerprint_sql(veto_available=veto_available)}, 'UTF8')), 'hex'),
            1, 16
        )
    """


def _lineage_joins(*, veto_available: bool, alias: str = "d") -> str:
    veto_join = (
        f"LEFT JOIN decision_veto_audits dva ON dva.decision_id = {alias}.decision_id"
        if veto_available
        else ""
    )
    return f"""
            LEFT JOIN manager_consensus_results mcr
              ON mcr.manager_consensus_result_id = {alias}.manager_consensus_result_id
            LEFT JOIN manager_policy_versions mpv
              ON mpv.manager_policy_version_id = mcr.manager_policy_version_id
            LEFT JOIN market_policy_versions mkt
              ON mkt.market_policy_version_id = {alias}.market_policy_version_id
            {veto_join}
            {_agent_lineage_lateral(alias)}
    """


def _lineage_columns(*, veto_available: bool) -> str:
    agent_columns = ",\n                ".join(
        f"CASE WHEN agents.{tag.lower()}_n = 1 THEN agents.{tag.lower()}_v END "
        f"AS agent_{tag.lower()}_config_version"
        for tag, _ in AGENT_SLOTS
    )
    return f"""
                {_regime_key_sql(veto_available=veto_available)} AS regime_key,
                mpv.policy_version AS manager_policy_version,
                mkt.policy_version AS market_policy_version,
                {_veto_value_sql(veto_available=veto_available)} AS veto_policy_version,
                {agent_columns}
    """


def first_production_bets_cte(*, veto_available: bool = False) -> str:
    """Shared CTE: the first publishable Type A BET per sporting match.

    This is the established Results contract and it stays **global**. The first
    bet for a match is chosen across all production decisions before any regime
    filter is applied, so cohorting can never promote a later duplicate bet on
    the same match into first place. The lineage joins below add columns only;
    none of them appears in the ``DISTINCT ON`` ordering, so none of them can
    change which decision wins.
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
                {_lineage_columns(veto_available=veto_available)}
            FROM decisions d
            CROSS JOIN publication_boundary pb
            JOIN prediction_runs pr
              ON pr.prediction_run_id = d.prediction_run_id
            {_lineage_joins(veto_available=veto_available)}
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


def resolve_current_regime(conn: psycopg.Connection) -> RegimeIdentity | None:
    """The regime the production system is generating picks under right now.

    Resolved from persisted production truth — the most recent completed,
    finalized, non-experiment run that actually produced decisions — not from
    runtime environment variables. An environment variable describes the next
    run's intent; it cannot retroactively describe the runs that already
    happened, and it would silently relabel history the moment it changed.

    Only the versioned pipeline can define "current": a run is considered only
    if it produced a decision carrying a Manager consensus link. A
    pre-versioning decision has no lineage to read, so it describes no system
    and cannot become the current one no matter how recently it was written.
    A *modern* run with broken lineage is a different matter and is reported as
    such below rather than skipped over.

    The run's own decisions must agree on the configuration that produced them.
    If they do not, this reports the disagreement instead of picking a winner:
    a run that cannot say what it was is not a system anyone should be shown a
    performance number for. The returned key is legacy-prefixed in that case,
    so it can never match a real cohort and Current System stays empty rather
    than filling with somebody else's bets.
    """
    veto_available = veto_audits_available(conn)
    rows = conn.execute(
        f"""
        WITH latest_run AS (
            SELECT pr.prediction_run_id
            FROM prediction_runs pr
            WHERE {PRODUCTION_RUN_PREDICATE}
              AND EXISTS (
                  SELECT 1 FROM decisions d0
                  WHERE d0.prediction_run_id = pr.prediction_run_id
                    AND d0.experiment_id IS NULL
                    AND d0.manager_consensus_result_id IS NOT NULL
              )
            ORDER BY pr.prediction_finalized_at DESC, pr.prediction_run_id DESC
            LIMIT 1
        )
        SELECT DISTINCT
            {_lineage_columns(veto_available=veto_available)}
        FROM decisions d
        JOIN latest_run lr ON lr.prediction_run_id = d.prediction_run_id
        JOIN prediction_runs pr ON pr.prediction_run_id = d.prediction_run_id
        {_lineage_joins(veto_available=veto_available)}
        WHERE d.experiment_id IS NULL
          AND d.manager_consensus_result_id IS NOT NULL
        """
    ).fetchall()

    if not rows:
        return None
    if len(rows) > 1:
        # Two different configurations inside one run. Report it; never collapse.
        return RegimeIdentity(
            regime_key=INCONSISTENT_CURRENT_REGIME_KEY,
            manager_policy_version=None,
            is_inconsistent=True,
        )

    row = rows[0]
    return RegimeIdentity(
        regime_key=str(row[0]),
        manager_policy_version=str(row[1]) if row[1] else None,
        market_policy_version=str(row[2]) if row[2] else None,
        veto_policy_version=str(row[3]) if row[3] else None,
        agent_config_versions=tuple(
            str(row[4 + index]) if row[4 + index] else None
            for index in range(len(AGENT_SLOTS))
        ),
    )


def load_regime_cohorts(conn: psycopg.Connection) -> tuple[RegimeCohort, ...]:
    """Realised production performance, grouped by the regime that produced it.

    Every legitimate historical bet appears in exactly one cohort. Nothing is
    recomputed, re-scored or dropped: the original price, the original
    selection and the actual settlement are used exactly as persisted.
    """
    veto_available = veto_audits_available(conn)
    rows = conn.execute(
        f"""
        WITH {first_production_bets_cte(veto_available=veto_available)},
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
            ) AS odds_sources,
            max(d.market_policy_version) AS market_policy_version,
            max(d.veto_policy_version) AS veto_policy_version,
            max(d.agent_a1_config_version) AS agent_a1_config_version,
            max(d.agent_a2_config_version) AS agent_a2_config_version,
            max(d.agent_a3_config_version) AS agent_a3_config_version
        FROM dated d
        LEFT JOIN decision_settlements ds ON ds.decision_id = d.decision_id
        GROUP BY d.regime_key
        ORDER BY max(d.match_date) DESC NULLS LAST, d.regime_key
        """
    ).fetchall()

    cohorts = [
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
            market_policy_version=str(row[13]) if row[13] else None,
            veto_policy_version=str(row[14]) if row[14] else None,
            agent_config_versions=tuple(
                str(row[15 + index]) if row[15 + index] else None
                for index in range(len(AGENT_SLOTS))
            ),
        )
        for row in rows
    ]

    # Display ordinal, oldest system first, so the breakdown reads as a
    # sequence of systems rather than a list of hashes. Legacy cohorts carry
    # no ordinal: numbering them would imply an order we cannot prove.
    ordered = sorted(
        (c for c in cohorts if c.has_complete_lineage),
        key=lambda c: (c.first_bet_date or date.min, c.regime_key),
    )
    sequence_by_key = {c.regime_key: index for index, c in enumerate(ordered, start=1)}
    return tuple(
        replace(cohort, sequence=sequence_by_key.get(cohort.regime_key))
        for cohort in cohorts
    )


def current_regime_started_at(
    conn: psycopg.Connection, regime_key: str
) -> datetime | None:
    """When the first production bet under this regime was published."""
    veto_available = veto_audits_available(conn)
    row = conn.execute(
        f"""
        WITH {first_production_bets_cte(veto_available=veto_available)}
        SELECT min(b.published_at)
        FROM first_bets b
        WHERE b.regime_key = %s
        """,
        (regime_key,),
    ).fetchone()
    return row[0] if row else None


__all__ = [
    "AGENT_SLOTS",
    "COMPLETE_REGIME_PREFIX",
    "DATA_PROVIDER_UNAVAILABLE",
    "INCONSISTENT_CURRENT_REGIME_KEY",
    "LEGACY_REGIME_PREFIX",
    "PRODUCTION_RUN_PREDICATE",
    "STATE_CONFLICT",
    "STATE_MISSING",
    "STATE_OK",
    "Dimension",
    "RegimeCohort",
    "RegimeFingerprint",
    "RegimeIdentity",
    "current_regime_started_at",
    "first_production_bets_cte",
    "is_complete_regime_key",
    "load_regime_cohorts",
    "regime_label_for",
    "resolve_current_regime",
    "veto_audits_available",
]
