# Blockers

This log records current blockers. Append-only.

## BLK-1

No tennis historical data pipeline exists yet (Sackmann + odds archive ingestion). This
blocks almost all Week 3 tennis modeling work. Scheduled for a later controlled phase.

## BLK-2

Tennis database is not provisioned. Database design/provisioning is a separate controlled
phase and must not be performed during repository bootstrap.

## BLK-3

Live sharp-reference provider is unresolved. Blocked until a later scoped task.

## BLK-4

No disposable local PostgreSQL runtime was available in this task environment (`psql`
missing; Docker daemon unavailable). The schema foundation was therefore validated
statically rather than by applying migrations to an empty local database.
