"""One explicitly authorized, unmetered OddsPapi account check.

Never print response bodies, keys, account/subscription identifiers or URLs.
No retries, no odds requests, no upgrade and no quota-consuming operation.
Run only in the existing authorized secret-bearing environment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


class AccountEvidenceError(ValueError):
    pass


def summarize(payload):
    """Allowlist fields rather than attempting to redact arbitrary payloads."""
    current = payload.get("current_subscription_id")
    if not isinstance(current, str) or not current:
        raise AccountEvidenceError("current subscription missing")
    subscriptions = payload.get("subscriptions")
    if not isinstance(subscriptions, list):
        raise AccountEvidenceError("subscriptions missing")
    selected = [item for item in subscriptions if isinstance(item, dict)
                and item.get("subscription_id") == current
                and item.get("is_active") is True]
    if len(selected) != 1:
        raise AccountEvidenceError("unique active subscription not established")
    plan = selected[0]
    limit, count = plan.get("request_limit"), plan.get("request_count")
    if type(limit) is not int or type(count) is not int or min(limit, count) < 0:
        raise AccountEvidenceError("numeric allowance and consumption required")
    books = plan.get("bookmakers")
    sports = plan.get("sport_ids")
    if not isinstance(books, dict) or not isinstance(sports, list):
        raise AccountEvidenceError("bookmakers or sports unavailable")
    if any(type(sport) is not int or sport <= 0 for sport in sports):
        raise AccountEvidenceError("invalid sport ids")
    out_books = {}
    for slug, flags in books.items():
        if not isinstance(slug, str) or not re.fullmatch(r"[a-zA-Z0-9+_.-]{1,80}", slug):
            raise AccountEvidenceError("invalid bookmaker identifier")
        if not isinstance(flags, dict):
            raise AccountEvidenceError("invalid bookmaker capabilities")
        out_books[slug] = {name: flags.get(name) if type(flags.get(name)) is bool else None
                           for name in ("has_live_odds", "has_player_props")}
    remaining = max(0, limit-count)
    return {"provider": "ODDSPAPI", "request_limit": limit,
            "request_count": count, "remaining_allowance": remaining,
            "tennis_included": 12 in sports, "sport_ids": sorted(set(sports)),
            "bookmakers": out_books,
            "historical_endpoint_quota_units": 0,
            "historical_endpoint_cooldown_seconds": 5,
            "historical_endpoint_may_be_blocked_at_quota_limit": remaining == 0,
            "commercial_rights_verified": False, "pilot_execution_authorized": False,
            "can_promote": False}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-account-check", action="store_true")
    args = parser.parse_args(argv)
    if not args.authorized_account_check:
        print(json.dumps({"status": "BLOCKED_NO_AUTHORIZATION_FLAG", "provider_calls": 0}))
        return 2
    key = os.getenv("ODDSPAPI_API_KEY")
    if not key:
        print(json.dumps({"status": "BLOCKED_MISSING_EXISTING_CREDENTIAL", "provider_calls": 0}))
        return 2
    # Official documented HTTPS host only; forbid redirecting credential-bearing URL.
    url = "https://api.oddspapi.io/v4/account?" + urllib.parse.urlencode({"apiKey": key})
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise AccountEvidenceError("unexpected account response size")
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise AccountEvidenceError("invalid account response")
        result = summarize(payload)
    except Exception:
        # Exception messages and URLs can contain the API key. Never log either.
        print(json.dumps({"status": "ACCOUNT_CHECK_UNVERIFIED", "provider_calls_attempted": 1}))
        return 1
    result.update(status="ACCOUNT_RESPONSE_VERIFIED",
                  observed_at=datetime.now(timezone.utc).isoformat(),
                  provider_calls_attempted=1, quota_consuming_calls=0)
    # Fail closed if a malformed supplier response echoes the key into an
    # otherwise allowed field. Never output anything containing the credential.
    output = json.dumps(result, sort_keys=True)
    if key in output:
        print(json.dumps({"status": "ACCOUNT_CHECK_UNVERIFIED", "provider_calls_attempted": 1}))
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
