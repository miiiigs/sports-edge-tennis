# Tennis production browser-native authentication

The production entrypoint `sports_edge_tennis.dashboard.production_app:app`
uses HTTPS plus the browser-native HTTP Basic challenge. It does not show the
old email signup/login page, set database auth sessions, or allow guest access.

## Required Railway service variables

Configure these **only on tennis-dashboard**, not on the live/validation runners:

- `TENNIS_DASHBOARD_BASIC_AUTH_ENABLED=1`
- `TENNIS_DASHBOARD_BASIC_AUTH_USERNAME=brian`
- `TENNIS_DASHBOARD_BASIC_AUTH_PASSWORD=<strong randomly generated password>`
  - at least 20 UTF-8 bytes; prefer a randomly generated 24+ character secret.
  - **never** use `admin123` or another widely-known/test password.
  - do not put the password in Git, logs, URLs, or messages to untrusted parties.

The guard returns HTTP 503 (fail closed) if credentials are missing/weak. On
Railway, missing `TENNIS_DASHBOARD_BASIC_AUTH_ENABLED` also yields HTTP 503.
Local and CI processes without Railway environment identity retain public app
access to support existing unit tests and development.

The existing historical `TENNIS_DASHBOARD_AUTH_*` variables are obsolete for
the production entrypoint and are no longer consulted. Do not delete account
tables: historical owner/session data is separate from the Tennis warehouse.

## Release order

1. Preserve the existing dashboard deployment SHA/start command as rollback.
2. Add the three Basic-auth Railway variables to `tennis-dashboard`. Because
   the old binary ignores them, there is no authentication gap.
3. Confirm this PR's CI passes. Merge the guarded production entrypoint.
4. Check Railway deploy health. Without any Authorization header, GET
   `/today`, `/model-pick-results`, `/static/...` and `/api/dashboard/...`
   must respond 401 with `WWW-Authenticate: Basic`. GET `/healthz` must be 200.
5. In a private browser tab open the **HTTPS** Tennis dashboard; the browser
   asks for username `brian` and the independently delivered strong password.
6. Verify dashboard, model pick, value bet, history and diagnostic pages.
7. Confirm `/login` and `/signup` do not exist anymore (authorized request
   returns 404); no old email form or logout button should appear.
8. Rotate the Basic password in Railway to revoke previous access; an active
   browser may cache the old credentials, so a private tab or restart helps.

## Behavior and limitations

HTTP Basic always requests **both username and password** in normal browsers.
It cannot natively display a password-only challenge. No custom JavaScript
password modal is implemented, to avoid a weaker client-side security barrier.

Browsers commonly cache Basic credentials; there is no reliable server-driven
logout, account-specific roles, MFA, password reset or audit identity. Treat
this as a temporary single-owner read-only dashboard gate, not scalable
multiuser authorization. For more users or admin privilege tiers, return to an
audited account/session flow with stronger session controls.

No betting/odds/settlement/picks schemas or data are changed.
