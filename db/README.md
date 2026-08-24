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
│   └── 0001_tennis_data_foundation.down.sql
└── schema/
    └── 0001_tennis_data_foundation.sql
```

## Current validation mode

This task established the PostgreSQL schema foundation but did not provision a running
PostgreSQL instance. The committed validation is therefore static:

- migration ordering and identity checks
- bootstrap schema snapshot determinism
- schema invariants asserted from the committed SQL

If a disposable local PostgreSQL runtime becomes available later, apply the migrations to
an empty local database only.

## Manual local apply

Example with `psql`, when a disposable local PostgreSQL instance exists:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/0001_tennis_data_foundation.up.sql
```

Rollback:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/0001_tennis_data_foundation.down.sql
```
