# Requirements Traceability

Status legend:

- `NOT STARTED` — no implementation exists
- `IN PROGRESS` — partial implementation exists
- `COMPLETE` — implemented and validated

This repository is in the bootstrap phase. Every requirement below is traced to a
canonical specification area. The data foundation and schema contracts are now committed
and validated against disposable PostgreSQL 16 in CI, but historical ingest, model
execution, pricing, and production runtime work remain unimplemented. Statuses are truthful.

## Architecture

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| ARCH-1 | Separate tennis repository | developer brief / architecture supersession | `COMPLETE` (this repository) |
| ARCH-2 | Separate tennis database | developer brief / architecture supersession | `IN PROGRESS` (PostgreSQL schema foundation committed; no provisioned DB yet) |
| ARCH-3 | Separate tennis runtime | developer brief / architecture supersession | `COMPLETE` (dedicated package/runtime boundary plus isolation tests) |
| ARCH-4 | No football runtime imports | architecture supersession | `COMPLETE` (enforced by isolation tests) |

## Data Foundation

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| DATA-1 | PostgreSQL database contract | tennis build spec `3. Database`; 30-day plan Day 1 | `COMPLETE` |
| DATA-2 | Versioned SQL migration foundation | task instruction; 30-day plan Day 1 | `COMPLETE` |
| DATA-3 | Fail-closed player identity model | tennis build spec `2.2`; architecture invariants | `COMPLETE` |
| DATA-4 | Tournament, edition, and match identity contract | tennis build spec `1. Scope`; task instruction | `COMPLETE` |
| DATA-5 | Append-only odds snapshot storage | tennis build spec `3. Database`; price scanner sections | `COMPLETE` |
| DATA-6 | Ratings, predictions, and decisions contracts | tennis build spec `3. Database`; manager sections | `COMPLETE` |
| DATA-7 | Result and settlement boundary | task instruction; tennis build spec retirement/result sections | `COMPLETE` |
| DATA-8 | Ingest provenance and data-health tracking | 30-day plan Day 1; task instruction | `COMPLETE` |
| DATA-9 | Point-in-time `as_of` semantics | architecture invariants; tennis build spec `10. Backtest protocol` | `COMPLETE` |
| DATA-10 | Local database provisioning | task instruction | `NOT STARTED` |
| DATA-11 | PostgreSQL 16 migration/runtime validation gate | task instruction; 30-day plan Day 1 | `COMPLETE` (pytest integration suite + GitHub Actions PostgreSQL service lifecycle) |

## Scope

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| SCOPE-1 | Men's tennis only | tennis build spec | `NOT STARTED` |
| SCOPE-2 | Grand Slam men's singles | tennis build spec | `NOT STARTED` |
| SCOPE-3 | Masters 1000 | tennis build spec | `NOT STARTED` |
| SCOPE-4 | ATP 500 | tennis build spec | `NOT STARTED` |
| SCOPE-5 | ATP 250 | tennis build spec | `NOT STARTED` |
| SCOPE-6 | Challenger | tennis build spec | `NOT STARTED` |
| SCOPE-7 | Qualifying only where reliable | tennis build spec | `NOT STARTED` |
| SCOPE-8 | No Futures/ITF | tennis build spec | `NOT STARTED` |
| SCOPE-9 | No WTA | tennis build spec | `NOT STARTED` |

## Models

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| MODEL-1 | Surface Elo | tennis build spec | `NOT STARTED` |
| MODEL-2 | Serve/Return model | tennis build spec | `NOT STARTED` |
| MODEL-3 | Exact hierarchical Markov | tennis build spec | `NOT STARTED` |
| MODEL-4 | Common-opponent routing | tennis build spec | `NOT STARTED` |
| MODEL-5 | Intransitivity graph | tennis build spec | `NOT STARTED` |
| MODEL-6 | Agent 3 abstention | tennis build spec | `NOT STARTED` |
| MODEL-7 | Blind agent isolation | tennis build spec | `IN PROGRESS` (storage separation enforced; no agent runtime yet) |

## Manager

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| MGR-1 | C1 directional agreement | tennis build spec | `NOT STARTED` |
| MGR-2 | C2 spread | tennis build spec | `NOT STARTED` |
| MGR-3 | C3 50% floor | tennis build spec | `NOT STARTED` |
| MGR-4 | C4 Agent 3 abstention | tennis build spec | `NOT STARTED` |
| MGR-5 | Unweighted initial consensus | tennis build spec | `NOT STARTED` |

## Market

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| MKT-1 | Sharp reference | tennis build spec | `NOT STARTED` |
| MKT-2 | Power de-vig | tennis build spec | `NOT STARTED` |
| MKT-3 | Shin | tennis build spec | `NOT STARTED` |
| MKT-4 | Type A scanner | tennis build spec | `NOT STARTED` |
| MKT-5 | Type B scanner | tennis build spec | `NOT STARTED` |
| MKT-6 | Minimum acceptable price | tennis build spec | `NOT STARTED` |
| MKT-7 | Best-price scanning | tennis build spec | `NOT STARTED` |

## Safety

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| SAF-1 | Veto kill-only | tennis build spec | `NOT STARTED` |
| SAF-2 | Freshness | tennis build spec | `NOT STARTED` |
| SAF-3 | Zero-pick valid output | tennis build spec | `NOT STARTED` |
| SAF-4 | Withdrawal retraction | tennis build spec | `NOT STARTED` |
| SAF-5 | Point-in-time `as_of` | tennis build spec | `COMPLETE` (schema/docs/tests) |
| SAF-6 | Market leakage prohibition | tennis build spec | `COMPLETE` (architecture rule plus blind prediction contract) |

## Measurement

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| MSR-1 | Calibration | tennis build spec | `NOT STARTED` |
| MSR-2 | Brier | tennis build spec | `NOT STARTED` |
| MSR-3 | CLV | tennis build spec | `IN PROGRESS` (storage contract only; arithmetic not implemented) |
| MSR-4 | Decision log | tennis build spec | `IN PROGRESS` (storage contract only) |
| MSR-5 | No-bet logging | tennis build spec | `IN PROGRESS` (storage contract only) |
| MSR-6 | Model version | tennis build spec | `COMPLETE` (schema contract on ratings/predictions/decisions) |
| MSR-7 | Feature version | tennis build spec | `COMPLETE` (schema contract on ratings/predictions/decisions) |
| MSR-8 | Variant count | tennis build spec | `NOT STARTED` |
| MSR-9 | Chronological walk-forward | tennis build spec | `NOT STARTED` |
