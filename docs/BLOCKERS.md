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
Superseded on 2026-08-24 by a committed GitHub Actions PostgreSQL 16 validation gate;
local disposable runtime still remains unavailable in this workstation environment.

## BLK-5

BLK-1 is now partially cleared for Agent 1 historical validation. A local-only Sackmann ATP
results pipeline exists for acquisition verification, dry-run parsing, disposable PostgreSQL
ingest, PIT boundary checks, and Agent 1 extractor probing.

Historical odds ingestion is still not implemented, so broader Week 3 modeling and any
market-aware validation remain blocked on a later controlled phase.

## BLK-6

BLK-4 is cleared inside the dedicated historical-validation worktree. A local PostgreSQL 16
runtime is now available for disposable validation runs, and committed migrations `0001`
through `0005` were applied successfully during the historical ingest verification flow.

This does not change the production authorization boundary or convert database provisioning
into an open-ended task.
