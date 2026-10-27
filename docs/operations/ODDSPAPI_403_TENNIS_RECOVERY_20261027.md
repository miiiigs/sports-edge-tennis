# OddsPapi 403 Tennis access recovery - 27 Oct 2026

Evidence: production runner reports HTTP_403 FORBIDDEN on OddsPapi every recent cycle; at 2026-10-27T08:01Z, 51 targets and nine Manager consensuses yielded zero offered quotes and zero new BETs.

Official docs: https://oddspapi.io/fr/docs/errors and https://oddspapi.io/us/docs/requests-and-quota . Status 403 RESTRICTED_ACCESS indicates subscription, sport, bookmaker, live rights or blocked IP. Invalid key is normally 401 and quota exhausted is 429.

The app already passes OddsPapi apiKey as a request parameter. Do not change authentication format.

Operator checks: confirm key variable is configured without revealing its value; inspect subscription activity, tennis sportId 12, authorized bookmaker slugs, live rights and IP with provider account owner. Account endpoint /v4/account is unmetered but can return api_key: NEVER log raw account JSON.

This patch logs only fixed 403 reason tokens. No provider body, key, URL or billing details are logged. No retry of 403. No new provider request, price substitution, betting-policy or model changes.

After subscription restored, verify a genuine provider 200 on authorized original-time offers, selected-side mapping and natural-cycle nonzero quote coverage. No price means no BET. Do not deploy before exact-head CI and normal production-release acceptance.