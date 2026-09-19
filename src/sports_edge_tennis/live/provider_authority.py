from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import psycopg


LEGACY_DATA_MODE = "API_TENNIS"
TARGET_DATA_MODE = "SPORTRADAR_V3"
LEGACY_ODDS_MODE = "LEGACY_SPLIT"
TARGET_ODDS_MODE = "GOALSERVE_TENNIS"

DATA_MODE_ENV = "TENNIS_DATA_PROVIDER_MODE"
ODDS_MODE_ENV = "TENNIS_ODDS_PROVIDER_MODE"
CUTOVER_AUTH_ENV = "TENNIS_PROVIDER_CUTOVER_AUTHORIZATION"
CUTOVER_AUTH_VALUE = "SPORTRADAR_GOALSERVE_V1"

SPORTRADAR_REQUIRED_ENV = (
    "SPORTRADAR_TENNIS_API_KEY",
    "SPORTRADAR_TENNIS_ACCESS_LEVEL",
    "SPORTRADAR_TENNIS_LANGUAGE",
)
GOALSERVE_REQUIRED_ENV = ("GOALSERVE_TENNIS_FEED_URL",)
MANAGER_POLICY_ENV = "TENNIS_MANAGER_POLICY_VERSION"
TARGET_RUNTIME_READY = False


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


@dataclass(frozen=True)
class CutoverReadiness:
    authority: ProviderAuthority
    ready: bool
    blockers: tuple[str, ...]


def load_provider_authority(env: Mapping[str, str]) -> ProviderAuthority:
    data_mode = str(env.get(DATA_MODE_ENV) or LEGACY_DATA_MODE).strip().upper()
    odds_mode = str(env.get(ODDS_MODE_ENV) or LEGACY_ODDS_MODE).strip().upper()

    if data_mode not in {LEGACY_DATA_MODE, TARGET_DATA_MODE}:
        raise ValueError(f"unsupported {DATA_MODE_ENV}={data_mode!r}")
    if odds_mode not in {LEGACY_ODDS_MODE, TARGET_ODDS_MODE}:
        raise ValueError(f"unsupported {ODDS_MODE_ENV}={odds_mode!r}")

    # A half-cutover is forbidden. This avoids scoring a Sportradar fixture
    # universe against the legacy odds identity universe, or vice versa.
    if (data_mode == TARGET_DATA_MODE) != (odds_mode == TARGET_ODDS_MODE):
        raise ValueError(
            "Tennis provider cutover must switch data and odds authority together"
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

    if not TARGET_RUNTIME_READY:
        blockers.append("TARGET_RUNTIME_NOT_ACTIVATABLE")

    if str(env.get(CUTOVER_AUTH_ENV) or "").strip() != CUTOVER_AUTH_VALUE:
        blockers.append("CUTOVER_AUTHORIZATION_MISSING")

    if str(env.get(MANAGER_POLICY_ENV) or "v1").strip().lower() != "v2":
        blockers.append("STRICT_MANAGER_V2_NOT_SELECTED")

    for name in (*SPORTRADAR_REQUIRED_ENV, *GOALSERVE_REQUIRED_ENV):
        if not str(env.get(name) or "").strip():
            blockers.append(f"MISSING_ENV:{name}")

    if not _strict_manager_v2_present(conn):
        blockers.append("STRICT_MANAGER_V2_NOT_MIGRATED")

    if not _core_schema_present(conn):
        blockers.append("CORE_SCHEMA_INCOMPLETE")

    if not _historical_corpus_present(conn):
        blockers.append("HISTORICAL_CORPUS_EMPTY")

    if not _goalserve_mapping_present(conn):
        blockers.append("GOALSERVE_BOOKMAKER_MAPPING_NOT_VERIFIED")

    # The adapters and schema can be prepared before the account payload exists,
    # but production authority must remain locked until the actual Goalserve
    # feed has been observed and its bookmaker identity mapping is persisted.
    return CutoverReadiness(
        authority=authority,
        ready=not blockers,
        blockers=tuple(blockers),
    )


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


def _goalserve_mapping_present(conn: psycopg.Connection) -> bool:
    row = conn.execute(
        """
        SELECT count(*)
        FROM provider_bookmaker_mappings
        WHERE provider_code = 'GOALSERVE_TENNIS'
          AND normalization_class IN ('SHARP', 'OFFERED')
        """
    ).fetchone()
    return bool(row and int(row[0]) > 0)


__all__ = [
    "CUTOVER_AUTH_ENV",
    "CUTOVER_AUTH_VALUE",
    "CutoverReadiness",
    "DATA_MODE_ENV",
    "GOALSERVE_REQUIRED_ENV",
    "LEGACY_DATA_MODE",
    "LEGACY_ODDS_MODE",
    "MANAGER_POLICY_ENV",
    "ODDS_MODE_ENV",
    "ProviderAuthority",
    "SPORTRADAR_REQUIRED_ENV",
    "TARGET_DATA_MODE",
    "TARGET_ODDS_MODE",
    "TARGET_RUNTIME_READY",
    "assert_cutover_ready",
    "evaluate_cutover_readiness",
    "load_provider_authority",
]
