# Requirements Traceability

Status legend:

- `NOT STARTED` — no implementation exists
- `IN PROGRESS` — partial implementation exists
- `COMPLETE` — implemented and validated

This repository is in the bootstrap phase. Every requirement below is traced to a
canonical specification area, but none of the model/runtime requirements are implemented
yet. Statuses are truthful.

## Architecture

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| ARCH-1 | Separate tennis repository | developer brief / architecture supersession | `COMPLETE` (this repository) |
| ARCH-2 | Separate tennis database | developer brief / architecture supersession | `NOT STARTED` |
| ARCH-3 | Separate tennis runtime | developer brief / architecture supersession | `NOT STARTED` |
| ARCH-4 | No football runtime imports | architecture supersession | `COMPLETE` (enforced by isolation tests) |

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
| MODEL-7 | Blind agent isolation | tennis build spec | `NOT STARTED` |

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
| SAF-5 | Point-in-time `as_of` | tennis build spec | `NOT STARTED` |
| SAF-6 | Market leakage prohibition | tennis build spec | `NOT STARTED` (enforced as architecture rule) |

## Measurement

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| MSR-1 | Calibration | tennis build spec | `NOT STARTED` |
| MSR-2 | Brier | tennis build spec | `NOT STARTED` |
| MSR-3 | CLV | tennis build spec | `NOT STARTED` |
| MSR-4 | Decision log | tennis build spec | `NOT STARTED` |
| MSR-5 | No-bet logging | tennis build spec | `NOT STARTED` |
| MSR-6 | Model version | tennis build spec | `NOT STARTED` |
| MSR-7 | Feature version | tennis build spec | `NOT STARTED` |
| MSR-8 | Variant count | tennis build spec | `NOT STARTED` |
| MSR-9 | Chronological walk-forward | tennis build spec | `NOT STARTED` |
