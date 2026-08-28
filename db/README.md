# Database Foundation

This repository uses explicit PostgreSQL SQL files as the canonical schema source.

## Principles

- PostgreSQL is the target database technology.
- Migrations are committed, ordered, reviewable SQL.
- No ORM-generated schema state is used.
- Raw source observations, odds snapshots, ratings, predictions, and decisions are
  append-only historical records unless documented otherwise.
- Local schema validation must never touch football, shared, staging, or production
  databases.

## Layout

```text
db/
├── migrations/
│   ├── manifest.json
│   ├── 0001_tennis_data_foundation.up.sql
│   ├── 0001_tennis_data_foundation.down.sql
│   ├── 0002_day5_historical_integrity.up.sql
│   ├── 0002_day5_historical_integrity.down.sql
│   ├── 0003_day5_temporal_reconciliation.up.sql
│   ├── 0003_day5_temporal_reconciliation.down.sql
│   ├── 0004_prediction_runs_and_policy_versions.up.sql
│   ├── 0004_prediction_runs_and_policy_versions.down.sql
│   ├── 0005_append_only_enforcement_and_lock_guards.up.sql
│   └── 0005_append_only_enforcement_and_lock_guards.down.sql
└── schema/
    ├── 0001_tennis_data_foundation.sql
    ├── 0002_day5_historical_integrity.sql
    ├── 0003_day5_temporal_reconciliation.sql
    ├── 0004_prediction_runs_and_policy_versions.sql
    └── 0005_append_only_enforcement_and_lock_guards.sql
```

## Current validation mode

This repository now validates the schema in two layers:

- static checks for migration ordering, snapshot determinism, and contract drift
- real PostgreSQL 16 integration validation in GitHub Actions against an empty disposable
  database

The GitHub Actions workflow applies:

```text
empty database
-> UP 0001
-> UP 0002
-> UP 0003
-> UP 0004
-> UP 0005
-> schema/runtime constraint suite
-> DOWN 0005
-> clean re-UP 0005
-> DOWN 0004
-> clean re-UP 0004
-> DOWN 0003
-> clean re-UP 0003
-> DOWN 0002
-> cleanup verification
-> clean re-UP 0002
```

If a disposable local PostgreSQL runtime becomes available later, apply the migrations to
an empty local database only. The local manual path is optional; the CI gate is mandatory
for schema changes.

## Deterministic source hashes

Fallback provenance hashes use the repository-wide `SE_SOURCE_HASH_V1` contract documented
in [`docs/DATA_MODEL.md`](/C:/dev/sports-edge-tennis/docs/DATA_MODEL.md): versioned
canonical JSON, UTF-8, Unicode NFKC normalization, explicit nulls, normalized timestamps
and decimals, then SHA-256.

## Manual local apply

Example with `psql`, when a disposable local PostgreSQL instance exists:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/0001_tennis_data_foundation.up.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/0002_day5_historical_integrity.up.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/0003_day5_temporal_reconciliation.up.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/0004_prediction_runs_and_policy_versions.up.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/0005_append_only_enforcement_and_lock_guards.up.sql
```

Rollback:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/0005_append_only_enforcement_and_lock_guards.down.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/0004_prediction_runs_and_policy_versions.down.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/0003_day5_temporal_reconciliation.down.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/0002_day5_historical_integrity.down.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/0001_tennis_data_foundation.down.sql
```
