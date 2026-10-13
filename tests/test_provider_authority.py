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
        ledger_schema=True,
    ):
        self.strict = strict
        self.schema = schema
        self.history = history
        self.sharp_mapping = sharp_mapping
        self.offered_mapping = offered_mapping
        self.ledger_schema = ledger_schema

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split()).lower()
        if "oddspapi_request_ledger" in normalized and "oddspapi_event_identity_cache" in normalized:
            return _Result((self.ledger_schema, self.ledger_schema))
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
        authority.DATA_FAILOVER_AUTH_ENV: authority.DATA_FAILOVER_AUTH_VALUE,
        authority.ODDS_FAILOVER_AUTH_ENV: authority.ODDS_FAILOVER_AUTH_VALUE,
        "SPORTRADAR_TENNIS_API_KEY": "sr-secret",
        "SPORTRADAR_TENNIS_ACCESS_LEVEL": "production",
        "SPORTRADAR_TENNIS_LANGUAGE": "en",
        "ODDSPAPI_API_KEY": "odds-secret",
    }


def test_default_authority_keeps_current_production_path() -> None:
    value = authority.load_provider_authority({})
    assert value.data_mode == authority.LEGACY_DATA_MODE
    assert value.odds_mode == authority.LEGACY_ODDS_MODE
    assert value.target_requested is False


def test_api_tennis_oddspapi_without_authorization_is_rejected() -> None:
    with pytest.raises(ValueError, match=authority.ODDS_ONLY_AUTH_ENV):
        authority.load_provider_authority(
            {authority.ODDS_MODE_ENV: authority.TARGET_ODDS_MODE}
        )
    with pytest.raises(ValueError, match=authority.ODDS_ONLY_AUTH_ENV):
        authority.load_provider_authority(
            {
                authority.ODDS_MODE_ENV: authority.TARGET_ODDS_MODE,
                authority.ODDS_ONLY_AUTH_ENV: "yes",
            }
        )


def _api_tennis_oddspapi_env():
    return {
        authority.DATA_MODE_ENV: authority.LEGACY_DATA_MODE,
        authority.ODDS_MODE_ENV: "oddspapi",
        authority.ODDS_ONLY_AUTH_ENV: authority.ODDS_ONLY_AUTH_VALUE,
        authority.ODDS_FAILOVER_AUTH_ENV: authority.ODDS_FAILOVER_AUTH_VALUE,
        "API_TENNIS_API_KEY": "api-tennis-secret",
        "ODDSPAPI_API_KEY": "odds-secret",
    }


def test_api_tennis_oddspapi_is_an_authorized_pair_with_alias() -> None:
    value = authority.load_provider_authority(_api_tennis_oddspapi_env())
    assert value.data_mode == authority.LEGACY_DATA_MODE
    assert value.odds_mode == authority.TARGET_ODDS_MODE
    assert value.api_tennis_oddspapi is True
    assert value.full_target_requested is False
    assert value.data_only is False


def test_api_tennis_oddspapi_ready_without_any_sportradar_configuration() -> None:
    result = authority.evaluate_cutover_readiness(
        _FakeConn(history=False), env=_api_tennis_oddspapi_env()
    )
    assert result.ready is True, result.blockers
    assert result.blockers == ()


def test_api_tennis_oddspapi_requires_odds_gate_mapping_and_keys() -> None:
    env = _api_tennis_oddspapi_env()
    env.pop(authority.ODDS_FAILOVER_AUTH_ENV)
    env.pop("API_TENNIS_API_KEY")
    env.pop("ODDSPAPI_API_KEY")
    result = authority.evaluate_cutover_readiness(
        _FakeConn(sharp_mapping=False, offered_mapping=False, ledger_schema=False),
        env=env,
    )
    assert result.ready is False
    assert set(result.blockers) == {
        "MISSING_ENV:API_TENNIS_API_KEY",
        "ODDS_FAILOVER_AUTHORIZATION_MISSING",
        "MISSING_ENV:ODDSPAPI_API_KEY",
        "ODDSPAPI_LEDGER_SCHEMA_NOT_READY",
        "ODDSPAPI_PINNACLE_SHARP_MAPPING_NOT_VERIFIED",
        "ODDSPAPI_OFFERED_MAPPING_NOT_VERIFIED",
    }
    assert not any("SPORTRADAR" in blocker for blocker in result.blockers)
    assert "DATA_FAILOVER_AUTHORIZATION_MISSING" not in result.blockers


def test_data_only_failover_is_allowed_but_gated() -> None:
    env = {authority.DATA_MODE_ENV: authority.TARGET_DATA_MODE}
    value = authority.load_provider_authority(env)
    assert value.data_mode == authority.TARGET_DATA_MODE
    assert value.odds_mode == authority.LEGACY_ODDS_MODE
    assert value.data_only is True
    assert value.target_requested is True

    result = authority.evaluate_cutover_readiness(_FakeConn(), env=env)
    assert result.ready is False
    assert "DATA_FAILOVER_AUTHORIZATION_MISSING" in result.blockers
    assert "MISSING_ENV:SPORTRADAR_TENNIS_API_KEY" in result.blockers
    # A data-only failover must not require the full cutover gate.
    assert not any("ODDSPAPI" in blocker for blocker in result.blockers)
    assert not any("MANAGER" in blocker for blocker in result.blockers)
    assert "TARGET_RUNTIME_NOT_ACTIVATABLE" not in result.blockers


def test_data_only_failover_ready_with_authorization_and_schema() -> None:
    env = {
        authority.DATA_MODE_ENV: authority.TARGET_DATA_MODE,
        authority.DATA_FAILOVER_AUTH_ENV: authority.DATA_FAILOVER_AUTH_VALUE,
        "SPORTRADAR_TENNIS_API_KEY": "sr-secret",
        "SPORTRADAR_TENNIS_ACCESS_LEVEL": "trial",
        "SPORTRADAR_TENNIS_LANGUAGE": "en",
    }
    assert authority.load_provider_authority(env).data_only is True
    result = authority.evaluate_cutover_readiness(_FakeConn(), env=env)
    assert result.ready is True
    assert result.blockers == ()


def test_provider_target_requires_explicit_authorizations_and_secrets() -> None:
    env = {
        authority.DATA_MODE_ENV: authority.TARGET_DATA_MODE,
        authority.ODDS_MODE_ENV: authority.TARGET_ODDS_MODE,
    }
    result = authority.evaluate_cutover_readiness(_FakeConn(), env=env)
    assert result.ready is False
    assert "DATA_FAILOVER_AUTHORIZATION_MISSING" in result.blockers
    assert "ODDS_FAILOVER_AUTHORIZATION_MISSING" in result.blockers
    assert "MISSING_ENV:SPORTRADAR_TENNIS_API_KEY" in result.blockers
    assert "MISSING_ENV:ODDSPAPI_API_KEY" in result.blockers
    # Provider cutover must NOT be coupled to the Manager policy version.
    assert not any("MANAGER" in blocker for blocker in result.blockers)
    assert not any("TARGET_RUNTIME" in blocker for blocker in result.blockers)


def test_provider_target_is_ready_with_manager_v1() -> None:
    # _target_env leaves TENNIS_MANAGER_POLICY_VERSION unset (production v1).
    result = authority.evaluate_cutover_readiness(_FakeConn(), env=_target_env())
    assert result.ready is True
    assert result.blockers == ()
    assert result.authority.full_target_requested is True


def test_provider_target_requires_ledger_schema_history_and_mapping() -> None:
    result = authority.evaluate_cutover_readiness(
        _FakeConn(
            schema=False,
            history=False,
            sharp_mapping=False,
            offered_mapping=False,
            ledger_schema=False,
        ),
        env=_target_env(),
    )
    assert result.ready is False
    assert "ODDSPAPI_LEDGER_SCHEMA_NOT_READY" in result.blockers
    assert "CORE_SCHEMA_INCOMPLETE" in result.blockers
    assert "HISTORICAL_CORPUS_EMPTY" in result.blockers
    assert "ODDSPAPI_PINNACLE_SHARP_MAPPING_NOT_VERIFIED" in result.blockers
    assert "ODDSPAPI_OFFERED_MAPPING_NOT_VERIFIED" in result.blockers
    assert not any("MANAGER" in blocker for blocker in result.blockers)


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
