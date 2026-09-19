from __future__ import annotations

from types import SimpleNamespace

import pytest

import sports_edge_tennis.live.provider_authority as authority


class _FakeConn:
    def __init__(self, *, strict=True, schema=True, history=True, mappings=True):
        self.strict = strict
        self.schema = schema
        self.history = history
        self.mappings = mappings

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split()).lower()
        if "manager_policy_versions" in normalized and "policy_version = 'v2'" in normalized:
            row = (False, "0.080000", "0.500000") if self.strict else None
            return _Result(row)
        if "to_regclass('public.manager_policy_versions')" in normalized:
            return _Result((self.schema,))
        if "select to_regclass(%s) is not null" in normalized:
            return _Result((self.schema,))
        if "from match_result_observations" in normalized and "match_stat_observations" in normalized:
            return _Result((100 if self.history else 0, 200 if self.history else 0))
        if "from provider_bookmaker_mappings" in normalized:
            return _Result((2 if self.mappings else 0,))
        raise AssertionError(normalized)


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def _target_env():
    return {
        authority.DATA_MODE_ENV: authority.TARGET_DATA_MODE,
        authority.ODDS_MODE_ENV: authority.TARGET_ODDS_MODE,
        authority.CUTOVER_AUTH_ENV: authority.CUTOVER_AUTH_VALUE,
        "SPORTRADAR_TENNIS_API_KEY": "sr-secret",
        "SPORTRADAR_TENNIS_ACCESS_LEVEL": "production",
        "SPORTRADAR_TENNIS_LANGUAGE": "en",
        "GOALSERVE_TENNIS_FEED_URL": "https://example.invalid/account/tennis.json",
        authority.MANAGER_POLICY_ENV: "v2",
    }


def test_default_authority_keeps_current_production_path() -> None:
    value = authority.load_provider_authority({})
    assert value.data_mode == authority.LEGACY_DATA_MODE
    assert value.odds_mode == authority.LEGACY_ODDS_MODE
    assert value.target_requested is False


def test_half_cutover_is_rejected() -> None:
    with pytest.raises(ValueError, match="must switch data and odds authority together"):
        authority.load_provider_authority(
            {authority.DATA_MODE_ENV: authority.TARGET_DATA_MODE}
        )


def test_target_cutover_requires_explicit_authorization_and_secrets() -> None:
    env = {
        authority.DATA_MODE_ENV: authority.TARGET_DATA_MODE,
        authority.ODDS_MODE_ENV: authority.TARGET_ODDS_MODE,
    }
    result = authority.evaluate_cutover_readiness(_FakeConn(), env=env)
    assert result.ready is False
    assert "TARGET_RUNTIME_NOT_ACTIVATABLE" in result.blockers
    assert "CUTOVER_AUTHORIZATION_MISSING" in result.blockers
    assert "STRICT_MANAGER_V2_NOT_SELECTED" in result.blockers
    assert "MISSING_ENV:SPORTRADAR_TENNIS_API_KEY" in result.blockers
    assert "MISSING_ENV:GOALSERVE_TENNIS_FEED_URL" in result.blockers


def test_target_cutover_requires_strict_manager_schema_history_and_mapping() -> None:
    result = authority.evaluate_cutover_readiness(
        _FakeConn(strict=False, schema=False, history=False, mappings=False),
        env=_target_env(),
    )
    assert result.ready is False
    assert "STRICT_MANAGER_V2_NOT_MIGRATED" in result.blockers
    assert "CORE_SCHEMA_INCOMPLETE" in result.blockers
    assert "HISTORICAL_CORPUS_EMPTY" in result.blockers
    assert "GOALSERVE_BOOKMAKER_MAPPING_NOT_VERIFIED" in result.blockers


def test_target_cutover_remains_blocked_until_runtime_write_path_is_complete() -> None:
    result = authority.evaluate_cutover_readiness(_FakeConn(), env=_target_env())
    assert result.ready is False
    assert result.blockers == ("TARGET_RUNTIME_NOT_ACTIVATABLE",)
    assert result.authority.full_target_requested is True
