import json

import pytest

from ops.validation.oddspapi_account_check_20261128 import (
    AccountEvidenceError, summarize, main,
)


def account():
    return {"api_key": "secret", "current_subscription_id": "secret-plan",
            "subscriptions": [{"subscription_id": "secret-plan", "is_active": True,
                               "request_limit": 500, "request_count": 100,
                               "sport_ids": [12], "bookmakers": {
                                   "pinnacle+30": {"has_live_odds": False,
                                                   "has_player_props": False,
                                                   "unexpected": "secret"}}}]}


def test_only_allowlisted_account_fields_and_known_quota():
    result = summarize(account())
    assert result["remaining_allowance"] == 400
    assert result["tennis_included"] is True
    assert result["commercial_rights_verified"] is False
    assert result["pilot_execution_authorized"] is False
    assert "secret" not in json.dumps(result)


def test_exhausted_and_unverified_plans_fail_closed():
    payload = account()
    payload["subscriptions"][0]["request_count"] = 500
    assert summarize(payload)["historical_endpoint_may_be_blocked_at_quota_limit"]
    payload["subscriptions"][0]["request_limit"] = "500"
    with pytest.raises(AccountEvidenceError):
        summarize(payload)


def test_missing_authorization_never_calls_network(monkeypatch, capsys):
    monkeypatch.setenv("ODDSPAPI_API_KEY", "secret")
    assert main([]) == 2
    assert json.loads(capsys.readouterr().out)["provider_calls"] == 0


def test_missing_credential_never_calls_network(monkeypatch, capsys):
    monkeypatch.delenv("ODDSPAPI_API_KEY", raising=False)
    assert main(["--authorized-account-check"]) == 2
    assert json.loads(capsys.readouterr().out)["provider_calls"] == 0
