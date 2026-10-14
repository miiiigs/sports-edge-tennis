from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import psycopg


LEGACY_DATA_MODE = "API_TENNIS"
TARGET_DATA_MODE = "SPORTRADAR_V3"
LEGACY_ODDS_MODE = "LEGACY_SPLIT"
TARGET_ODDS_MODE = "ODDSPAPI_TENNIS"

DATA_MODE_ENV = "TENNIS_DATA_PROVIDER_MODE"
ODDS_MODE_ENV = "TENNIS_ODDS_PROVIDER_MODE"
CUTOVER_AUTH_ENV = "TENNIS_PROVIDER_CUTOVER_AUTHORIZATION"
CUTOVER_AUTH_VALUE = "SPORTRADAR_ODDSPAPI_V1"
DATA_FAILOVER_AUTH_ENV = "TENNIS_DATA_FAILOVER_AUTHORIZATION"
DATA_FAILOVER_AUTH_VALUE = "SPORTRADAR_DATA_ONLY_V1"
ODDS_FAILOVER_AUTH_ENV = "TENNIS_ODDS_FAILOVER_AUTHORIZATION"
ODDS_FAILOVER_AUTH_VALUE = "ODDSPAPI_ODDS_V1"
# API-Tennis fixtures/results scored against OddsPapi odds (Pinnapi retired).
# Separate from the Sportradar cutover so neither authorization implies the other.
ODDS_ONLY_AUTH_ENV = "TENNIS_ODDS_ONLY_AUTHORIZATION"
ODDS_ONLY_AUTH_VALUE = "API_TENNIS_ODDSPAPI_V1"
# Operator-friendly spelling accepted for the target odds mode.
_ODDS_MODE_ALIASES = {"ODDSPAPI": TARGET_ODDS_MODE}

SPORTRADAR_REQUIRED_ENV = (
    "SPORTRADAR_TENNIS_API_KEY",
    "SPORTRADAR_TENNIS_ACCESS_LEVEL",
    "SPORTRADAR_TENNIS_LANGUAGE",
)
ODDSPAPI_REQUIRED_ENV = ("ODDSPAPI_API_KEY",)
MANAGER_POLICY_ENV = "TENNIS_MANAGER_POLICY_VERSION"


@dataclass(frozen=True)
class ProviderAuthority:
    data_mode: str
    odds_mode: str

    @property
    def target_requested(self) -> bool:
        return self.data_mode == TARGET_DATA_MODE or self.odds_mode == TARGET_ODDS_MODE

    @property
    def full_target_requested(self) -> bool:
        return self.data_mode == TARGET_DATA_MODE and self.odds_mode == TARGET_ODDS_MODE

    @property
    def api_tennis_oddspapi(self) -> bool:
        """API-Tennis fixtures/results with OddsPapi sharp + offered odds."""
        return self.data_mode == LEGACY_DATA_MODE and self.odds_mode == TARGET_ODDS_MODE

    @property
    def data_only(self) -> bool:
        """Sportradar fixtures scored against the legacy odds authority."""
        return self.data_mode == TARGET_DATA_MODE and self.odds_mode == LEGACY_ODDS_MODE


@dataclass(frozen=True)
class CutoverReadiness:
    authority: ProviderAuthority
    ready: bool
    blockers: tuple[str, ...]


def load_provider_authority(env: Mapping[str, str]) -> ProviderAuthority:
    data_mode = str(env.get(DATA_MODE_ENV) or LEGACY_DATA_MODE).strip().upper()
    odds_mode = str(env.get(ODDS_MODE_ENV) or LEGACY_ODDS_MODE).strip().upper()
    odds_mode = _ODDS_MODE_ALIASES.get(odds_mode, odds_mode)

    if data_mode not in {LEGACY_DATA_MODE, TARGET_DATA_MODE}:
        raise ValueError(f"unsupported {DATA_MODE_ENV}={data_mode!r}")
    if odds_mode not in {LEGACY_ODDS_MODE, TARGET_ODDS_MODE}:
        raise ValueError(f"unsupported {ODDS_MODE_ENV}={odds_mode!r}")

    # API-Tennis fixtures + OddsPapi odds is only a supported pair under its
    # own explicit authorization: OddsPapi events are then linked to API-Tennis
    # fixtures by deterministic participant/start-time reconciliation instead of
    # Betradar ids. Without the authorization it stays a hard error, so a stray
    # odds-mode variable can never silently change the odds authority. A
    # data-only failover (Sportradar fixtures + legacy odds) is gated separately
    # in evaluate_cutover_readiness.
    if (
        data_mode == LEGACY_DATA_MODE
        and odds_mode == TARGET_ODDS_MODE
        and str(env.get(ODDS_ONLY_AUTH_ENV) or "").strip() != ODDS_ONLY_AUTH_VALUE
    ):
        raise ValueError(
            "API_TENNIS + ODDSPAPI_TENNIS requires "
            f"{ODDS_ONLY_AUTH_ENV}={ODDS_ONLY_AUTH_VALUE}"
        )
    return ProviderAuthority(data_mode=data_mode, odds_mode=odds_mode)


def evaluate_cutover_readiness(
    conn: psycopg.Connection,
    *,
    env: Mapping[str, str],
) -> CutoverReadiness:
    authority = load_provider_authority(env)
    blockers: list[str] = []

    if not authority.target_requested:
        return CutoverReadiness(authority=authority, ready=True, blockers=())

    # Provider readiness is capability-specific. The Sportradar data failover and
    # the OddsPapi odds failover are independent of the Manager policy version;
    # Manager v2 / A3 v2 remain separate future switches and are never required
    # to change a data or odds provider.
    if authority.data_mode == TARGET_DATA_MODE:
        blockers.extend(_data_readiness_blockers(conn, env))
    else:
        # API-Tennis stays the data authority; Sportradar is never required.
        blockers.extend(_api_tennis_data_blockers(conn, env))
    if authority.odds_mode == TARGET_ODDS_MODE:
        blockers.extend(_odds_readiness_blockers(conn, env))

    return CutoverReadiness(
        authority=authority,
        ready=not blockers,
        blockers=tuple(blockers),
    )


def _data_readiness_blockers(
    conn: psycopg.Connection,
    env: Mapping[str, str],
) -> list[str]:
    blockers: list[str] = []
    if str(env.get(DATA_FAILOVER_AUTH_ENV) or "").strip() != DATA_FAILOVER_AUTH_VALUE:
        blockers.append("DATA_FAILOVER_AUTHORIZATION_MISSING")
    for name in SPORTRADAR_REQUIRED_ENV:
        if not str(env.get(name) or "").strip():
            blockers.append(f"MISSING_ENV:{name}")
    if not _core_schema_present(conn):
        blockers.append("CORE_SCHEMA_INCOMPLETE")
    if not _historical_corpus_present(conn):
        blockers.append("HISTORICAL_CORPUS_EMPTY")
    return blockers


def _api_tennis_data_blockers(
    conn: psycopg.Connection,
    env: Mapping[str, str],
) -> list[str]:
    blockers: list[str] = []
    if not str(env.get("API_TENNIS_API_KEY") or "").strip():
        blockers.append("MISSING_ENV:API_TENNIS_API_KEY")
    if not _core_schema_present(conn):
        blockers.append("CORE_SCHEMA_INCOMPLETE")
    return blockers


def _odds_readiness_blockers(
    conn: psycopg.Connection,
    env: Mapping[str, str],
) -> list[str]:
    blockers: list[str] = []
    if str(env.get(ODDS_FAILOVER_AUTH_ENV) or "").strip() != ODDS_FAILOVER_AUTH_VALUE:
        blockers.append("ODDS_FAILOVER_AUTHORIZATION_MISSING")
    for name in ODDSPAPI_REQUIRED_ENV:
        if not str(env.get(name) or "").strip():
            blockers.append(f"MISSING_ENV:{name}")
    if not _oddspapi_ledger_schema_present(conn):
        blockers.append("ODDSPAPI_LEDGER_SCHEMA_NOT_READY")
    sharp_mapping, offered_mapping = _oddspapi_mapping_state(conn)
    if not sharp_mapping:
        blockers.append("ODDSPAPI_PINNACLE_SHARP_MAPPING_NOT_VERIFIED")
    if not offered_mapping:
        blockers.append("ODDSPAPI_OFFERED_MAPPING_NOT_VERIFIED")
    return blockers


def _oddspapi_ledger_schema_present(conn: psycopg.Connection) -> bool:
    row = conn.execute(
        """
        SELECT
            to_regclass('public.oddspapi_request_ledger') IS NOT NULL,
            to_regclass('public.oddspapi_event_identity_cache') IS NOT NULL
        """
    ).fetchone()
    return bool(row and row[0] and row[1])


def assert_cutover_ready(
    conn: psycopg.Connection,
    *,
    env: Mapping[str, str],
) -> ProviderAuthority:
    readiness = evaluate_cutover_readiness(conn, env=env)
    if not readiness.ready:
        raise RuntimeError(
            "TENNIS_PROVIDER_CUTOVER_BLOCKED "
            + ",".join(readiness.blockers)
        )
    return readiness.authority


def _strict_manager_v2_present(conn: psycopg.Connection) -> bool:
    if conn.execute(
        "SELECT to_regclass('public.manager_policy_versions') IS NOT NULL"
    ).fetchone()[0] is not True:
        return False
    row = conn.execute(
        """
        SELECT allow_agent3_abstention,
               three_agent_max_probability_spread,
               min_active_agent_probability
        FROM manager_policy_versions
        WHERE policy_key = 'tennis_manager_consensus'
          AND policy_version = 'v2'
        """
    ).fetchone()
    return bool(
        row is not None
        and row[0] is False
        and str(row[1]) == "0.080000"
        and str(row[2]) == "0.500000"
    )


def _core_schema_present(conn: psycopg.Connection) -> bool:
    required = (
        "matches",
        "match_versions",
        "match_result_observations",
        "match_stat_observations",
        "prediction_runs",
        "predictions",
        "manager_consensus_results",
        "odds_snapshots",
        "raw_odds_observations",
        "provider_bookmaker_mappings",
        "decision_settlements",
    )
    return all(
        conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",)).fetchone()[0]
        is True
        for table in required
    )


def _historical_corpus_present(conn: psycopg.Connection) -> bool:
    row = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM match_result_observations),
            (SELECT count(*) FROM match_stat_observations)
        """
    ).fetchone()
    return bool(row and int(row[0]) > 0 and int(row[1]) > 0)


# Mirrors live.oddspapi.ODDSPAPI_SHARP_BOOKMAKER / ODDSPAPI_PLAN_BOOKMAKERS;
# kept literal to avoid importing the HTTP transport here (asserted by tests).
ODDSPAPI_SHARP_KEY = "pinnacle+30"
ODDSPAPI_OFFERED_KEYS = ("1xbet", "unibet", "bet365")


def _oddspapi_mapping_state(
    conn: psycopg.Connection,
) -> tuple[bool, bool]:
    row = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (provider_bookmaker_key)
                provider_bookmaker_key,
                bookmaker_code,
                normalization_class
            FROM provider_bookmaker_mappings
            WHERE provider_code = 'ODDSPAPI'
            ORDER BY
                provider_bookmaker_key,
                effective_from DESC,
                mapping_version DESC,
                provider_bookmaker_mapping_id DESC
        )
        SELECT
            -- The exact key the batch requests must be the sharp reference;
            -- any other SHARP key would leave every sharp quote unmapped.
            COALESCE(bool_or(
                provider_bookmaker_key = %(sharp_key)s
                AND normalization_class = 'SHARP'
                AND bookmaker_code = 'PINNACLE'
            ), FALSE)
            AND NOT COALESCE(bool_or(
                provider_bookmaker_key = ANY(%(offered_keys)s)
                AND normalization_class = 'SHARP'
            ), FALSE) AS sharp_pinnacle,
            COALESCE(bool_or(
                provider_bookmaker_key = ANY(%(offered_keys)s)
                AND normalization_class = 'OFFERED'
            ), FALSE) AS offered_book
        FROM latest
        """,
        {
            "sharp_key": ODDSPAPI_SHARP_KEY,
            "offered_keys": list(ODDSPAPI_OFFERED_KEYS),
        },
    ).fetchone()
    if row is None:
        return False, False
    return bool(row[0]), bool(row[1])


__all__ = [
    "CUTOVER_AUTH_ENV",
    "CUTOVER_AUTH_VALUE",
    "CutoverReadiness",
    "DATA_FAILOVER_AUTH_ENV",
    "DATA_FAILOVER_AUTH_VALUE",
    "DATA_MODE_ENV",
    "ODDSPAPI_REQUIRED_ENV",
    "LEGACY_DATA_MODE",
    "LEGACY_ODDS_MODE",
    "MANAGER_POLICY_ENV",
    "ODDS_FAILOVER_AUTH_ENV",
    "ODDS_FAILOVER_AUTH_VALUE",
    "ODDS_MODE_ENV",
    "ODDS_ONLY_AUTH_ENV",
    "ODDS_ONLY_AUTH_VALUE",
    "ProviderAuthority",
    "SPORTRADAR_REQUIRED_ENV",
    "TARGET_DATA_MODE",
    "TARGET_ODDS_MODE",
    "assert_cutover_ready",
    "evaluate_cutover_readiness",
    "load_provider_authority",
]
