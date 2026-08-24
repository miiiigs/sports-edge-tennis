# Sports Edge Tennis

Independent men's tennis probability and pricing platform.

## Purpose

An independent men's tennis probability and pricing platform, developed in a dedicated
repository, runtime, and database, fully separated from the football platform.

## Current State

Repository bootstrap plus PostgreSQL schema foundation. No historical ingest, model,
market, or production runtime functionality is implemented yet.

## Not Yet Implemented

- historical ingest
- Surface Elo
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
