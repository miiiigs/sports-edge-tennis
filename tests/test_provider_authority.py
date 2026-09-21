from __future__ import annotations

from types import SimpleNamespace

import pytest

import sports_edge_tennis.live.provider_authority as authority


class _FakeConn:
    def __init__(
        self,
        *,
        strict=True,
        schema=True,
        history=True,
        sharp_mapping=True,
        offered_mapping=True,
    ):
        self.strict = strict
        self.schema = schema
        self.history = history
        self.sharp_mapping = sharp_mapping
        self.offered_mapping = offered_mapping

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
            return _Result((self.sharp_mapping, self.offered_mapping))
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
        "ODDSPAPI_API_KEY": "odds-secret",
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
    assert "MISSING_ENV:ODDSPAPI_API_KEY" in result.blockers


def test_target_cutover_requires_strict_manager_schema_history_and_mapping() -> None:
    result = authority.evaluate_cutover_readiness(
        _FakeConn(
            strict=False,
            schema=False,
            history=False,
            sharp_mapping=False,
            offered_mapping=False,
        ),
        env=_target_env(),
    )
    assert result.ready is False
    assert "STRICT_MANAGER_V2_NOT_MIGRATED" in result.blockers
    assert "CORE_SCHEMA_INCOMPLETE" in result.blockers
    assert "HISTORICAL_CORPUS_EMPTY" in result.blockers
    assert "ODDSPAPI_PINNACLE_SHARP_MAPPING_NOT_VERIFIED" in result.blockers
    assert "ODDSPAPI_OFFERED_MAPPING_NOT_VERIFIED" in result.blockers


def test_target_cutover_remains_blocked_until_runtime_write_path_is_complete() -> None:
    result = authority.evaluate_cutover_readiness(_FakeConn(), env=_target_env())
    assert result.ready is False
    assert result.blockers == ("TARGET_RUNTIME_NOT_ACTIVATABLE",)
    assert result.authority.full_target_requested is True


def test_target_cutover_requires_both_oddspapi_mapping_classes() -> None:
    missing_sharp = authority.evaluate_cutover_readiness(
        _FakeConn(sharp_mapping=False, offered_mapping=True),
        env=_target_env(),
    )
    assert "ODDSPAPI_PINNACLE_SHARP_MAPPING_NOT_VERIFIED" in missing_sharp.blockers
    assert "ODDSPAPI_OFFERED_MAPPING_NOT_VERIFIED" not in missing_sharp.blockers

    missing_offered = authority.evaluate_cutover_readiness(
        _FakeConn(sharp_mapping=True, offered_mapping=False),
        env=_target_env(),
    )
    assert "ODDSPAPI_PINNACLE_SHARP_MAPPING_NOT_VERIFIED" not in missing_offered.blockers
    assert "ODDSPAPI_OFFERED_MAPPING_NOT_VERIFIED" in missing_offered.blockers


def test_oddspapi_mapping_gate_queries_latest_mapping_per_bookmaker() -> None:
    seen = {}

    class _Conn:
        def execute(self, sql, params=None):
            normalized = " ".join(sql.split()).lower()
            if "with latest as" in normalized:
                seen["sql"] = normalized
                return _Result((True, True))
            raise AssertionError(normalized)

    assert authority._oddspapi_mapping_state(_Conn()) == (True, True)
    assert "distinct on (provider_bookmaker_key)" in seen["sql"]
    assert "effective_from desc" in seen["sql"]
    assert "mapping_version desc" in seen["sql"]
