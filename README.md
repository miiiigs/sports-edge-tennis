# Sports Edge Tennis

Independent men's tennis probability and pricing platform.

## Purpose

An independent men's tennis probability and pricing platform, developed in a dedicated
repository, runtime, and database, fully separated from the football platform.

## Current State

PostgreSQL schema foundation, approved 2016-2025 historical corpus acquisition/local ingest/PIT
validation, Agent 1 Surface Elo implementation, and the first genuine 2019-2025 out-of-sample
walk-forward validation are now committed in-repo. Independent quantitative review is still
required before merge or any stronger acceptance claim.

## Not Yet Implemented

- historical ingest
- serve/return
- Markov
- intransitivity
- manager
- veto
- price scanner
- paper mode
- production

## Development Principle

```text
Understand
-> Investigate
-> Implement
-> Validate
-> Independent Review
-> Git
-> Release Safety
```

Unfinished functionality is not marketed as complete.

## Layout

```text
specs/   canonical requirement artifacts and source manifest
docs/    governance, status, architecture, traceability
db/      PostgreSQL migrations, schema snapshot, and database foundation docs
src/sports_edge_tennis/   package source
tests/   isolation, architecture, and schema-foundation tests
```
