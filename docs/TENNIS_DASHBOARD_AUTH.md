# Tennis Dashboard Owner Authentication

This dashboard authentication layer is optional and remains disabled until Railway is deliberately configured for owner-only access.

Authentication state is stored in the dedicated PostgreSQL schema `sports_edge_auth`. It does not write to canonical Tennis model, match, odds, Manager, Veto, or decision tables.

## Environment variables

- `TENNIS_DASHBOARD_AUTH_ENABLED`
  - default: `0`
  - set to `1` only when owner access is ready
- `TENNIS_DASHBOARD_AUTH_ALLOW_SIGNUP`
  - default: `0`
  - temporarily set to `1` only for an authorized owner bootstrap
- `TENNIS_DASHBOARD_AUTH_ALLOWED_EMAILS`
  - comma-separated owner/invite allowlist
  - must be configured before account creation
- `TENNIS_DASHBOARD_AUTH_COOKIE_SECURE`
  - default: `1`
  - keep enabled on Railway HTTPS

The auth wrapper requires the same `TENNIS_DATABASE_URL` / `DATABASE_URL` already used by the dashboard. No password is stored in Railway variables or Git.

## Production start command

The Railway `tennis-dashboard` service starts the production composition below.
That module imports `owner_access.app` first, so the owner middleware and
login/signup routes are installed on the same FastAPI app *before* all the
production-only evidence routes are registered.

```text
python -m pip install --quiet . && exec python -m uvicorn sports_edge_tennis.dashboard.production_app:app --host 0.0.0.0 --port $PORT
```

The fallback `Procfile` also points to `production_app:app`. The legacy
`dashboard.app:app` entrypoint is **not** an authenticated production entrypoint.

Importing the wrapper alone does not turn protection on:
`TENNIS_DASHBOARD_AUTH_ENABLED=1` must be present on the deployed service.

The public health check remains:

```text
/healthz
```

## Safe owner bootstrap

1. Confirm the authorized owner email out of band.
2. Preserve the current dashboard source SHA and start command as the rollback point.
3. Set `TENNIS_DASHBOARD_AUTH_ALLOWED_EMAILS` to the authorized email.
4. Set `TENNIS_DASHBOARD_AUTH_ENABLED=1`.
5. Temporarily set `TENNIS_DASHBOARD_AUTH_ALLOW_SIGNUP=1`.
6. Deploy the reviewed `production_app:app` composition with auth enabled, preserving its evidence routes and current Railway start command.
7. Verify `/healthz` remains `200` while an unauthenticated dashboard page redirects to `/login` and an unauthenticated dashboard API returns `401`.
8. The owner creates the account through `/signup` using a password known only to them.
9. Verify sign-in, dashboard access, sign-out, and sign-in again.
10. Set `TENNIS_DASHBOARD_AUTH_ALLOW_SIGNUP=0` and redeploy/apply the variable change.
11. Verify `/signup` is no longer available while the existing owner account still signs in.

Do not leave public signup enabled after bootstrap.

## Rollback

Authentication can be disabled without deleting account data:

1. Set `TENNIS_DASHBOARD_AUTH_ENABLED=0`.
2. Keep `production_app:app` as the entrypoint; auth-disabled mode preserves the
   existing dashboard and production evidence routes.
3. Redeploy the previous known-good SHA/branch only if the composition itself
   causes an unrelated regression. Do not use `dashboard.app:app` as a
   normal-production rollback because it drops production-only routes.

Do not drop `sports_edge_auth` during an operational rollback. Keeping the schema preserves owner account/session audit state and does not affect canonical Tennis warehouse truth.

## Security properties

- PBKDF2-SHA256 salted password hashes
- opaque random session tokens; only token hashes are stored
- 30-day server-side session expiry
- HttpOnly cookie
- SameSite=Lax
- Secure cookie by default
- owner email allowlist required for account creation
- signup disabled by default
- no guest mode
- `/healthz` remains public
- auth routes perform no external provider calls
