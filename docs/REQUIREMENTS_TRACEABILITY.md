# Requirements Traceability

Status legend:

- `LOCKED` - requirement and precedence are documented and frozen for future implementation
- `DESIGNED` - exact contract or schema shape is designed but not implemented
- `PLANNED` - future phase is identified, but implementation details remain deferred
- `NOT IMPLEMENTED` - no runtime implementation exists yet
- `IN PROGRESS` - partial implementation exists
- `COMPLETE` - implemented and validated

This repository is in the bootstrap phase. Every requirement below is traced to a
canonical specification area. The data foundation and T-ED1 schema contracts are committed
and validated against disposable PostgreSQL 16 in CI, while the T-ED2 immutability and
lock-guard contract is now designed but not yet implemented. Historical ingest, model
execution, pricing, and production runtime work remain otherwise unimplemented. Statuses
are truthful.

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
| DATA-10 | Local database provisioning | task instruction | `NOT IMPLEMENTED` |
| DATA-11 | PostgreSQL 16 migration/runtime validation gate | task instruction; 30-day plan Day 1 | `COMPLETE` (pytest integration suite + GitHub Actions PostgreSQL service lifecycle) |

## Scope

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| SCOPE-1 | Men's tennis only | tennis build spec | `NOT IMPLEMENTED` |
| SCOPE-2 | Grand Slam men's singles | tennis build spec | `NOT IMPLEMENTED` |
| SCOPE-3 | Masters 1000 | tennis build spec | `NOT IMPLEMENTED` |
| SCOPE-4 | ATP 500 | tennis build spec | `NOT IMPLEMENTED` |
| SCOPE-5 | ATP 250 | tennis build spec | `NOT IMPLEMENTED` |
| SCOPE-6 | Challenger | tennis build spec | `NOT IMPLEMENTED` |
| SCOPE-7 | Qualifying only where reliable | tennis build spec | `NOT IMPLEMENTED` |
| SCOPE-8 | No Futures/ITF | tennis build spec | `NOT IMPLEMENTED` |
| SCOPE-9 | No WTA | tennis build spec | `NOT IMPLEMENTED` |

## Models

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| MODEL-1 | Surface Elo | tennis build spec | `NOT IMPLEMENTED` |
| MODEL-2 | Serve/Return model | tennis build spec | `NOT IMPLEMENTED` |
| MODEL-3 | Exact hierarchical Markov | tennis build spec | `NOT IMPLEMENTED` |
| MODEL-4 | Common-opponent routing | tennis build spec | `NOT IMPLEMENTED` |
| MODEL-5 | Intransitivity graph | tennis build spec | `NOT IMPLEMENTED` |
| MODEL-6 | Agent 3 abstention | tennis build spec | `LOCKED` (contract preserved; runtime not implemented) |
| MODEL-7 | Blind agent isolation | tennis build spec | `IN PROGRESS` (storage separation enforced; no agent runtime yet) |

## Manager

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| MGR-1 | C1 directional agreement | tennis build spec `7.1 Consensus checks` | `LOCKED` |
| MGR-2 | C2 spread | tennis build spec `7.1 Consensus checks` | `LOCKED` |
| MGR-3 | C3 50% floor | tennis build spec `7.1 Consensus checks` | `LOCKED` |
| MGR-4 | C4 Agent 3 abstention | tennis build spec `7.1 Consensus checks` | `LOCKED` |
| MGR-5 | Unweighted initial consensus | tennis build spec `7.1 Consensus checks` | `LOCKED` |

## Market

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| MKT-1 | Sharp reference | tennis build spec | `NOT IMPLEMENTED` |
| MKT-2 | Power de-vig | tennis build spec | `NOT IMPLEMENTED` |
| MKT-3 | Shin | tennis build spec | `NOT IMPLEMENTED` |
| MKT-4 | Type A scanner | tennis build spec | `NOT IMPLEMENTED` |
| MKT-5 | Type B scanner | tennis build spec | `NOT IMPLEMENTED` |
| MKT-6 | Minimum acceptable price | tennis build spec | `NOT IMPLEMENTED` |
| MKT-7 | Best-price scanning | tennis build spec | `NOT IMPLEMENTED` |

## Safety

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| SAF-1 | Veto kill-only | tennis build spec `8. The Veto Agent` | `LOCKED` |
| SAF-2 | Freshness | tennis build spec `10.1 Data freshness gate` | `LOCKED` |
| SAF-3 | Zero-pick valid output | tennis build spec `11. The fixed slate rule` | `LOCKED` |
| SAF-4 | Withdrawal retraction | tennis build spec `8. The Veto Agent`; `11. Scheduling` | `LOCKED` |
| SAF-5 | Point-in-time `as_of` | tennis build spec | `COMPLETE` (schema/docs/tests) |
| SAF-6 | Market leakage prohibition | tennis build spec | `COMPLETE` (architecture rule plus blind prediction contract) |
| SAF-7 | Database-level immutability and lock guards for historical prediction lineage | additive Edge Desk design guidance plus task T-ED2 contract | `DESIGNED` |

## Measurement

| ID | Requirement | Source | Status |
| --- | --- | --- | --- |
| MSR-1 | Calibration | tennis build spec | `NOT IMPLEMENTED` |
| MSR-2 | Brier | tennis build spec | `NOT IMPLEMENTED` |
| MSR-3 | CLV | tennis build spec | `IN PROGRESS` (storage contract only; arithmetic not implemented) |
| MSR-4 | Decision log | tennis build spec | `IN PROGRESS` (storage contract only) |
| MSR-5 | No-bet logging | tennis build spec | `IN PROGRESS` (storage contract only) |
| MSR-6 | Model version | tennis build spec | `COMPLETE` (schema contract on ratings/predictions/decisions) |
| MSR-7 | Feature version | tennis build spec | `COMPLETE` (schema contract on ratings/predictions/decisions) |
| MSR-8 | Variant count | tennis build spec | `NOT IMPLEMENTED` |
| MSR-9 | Chronological walk-forward | tennis build spec | `NOT IMPLEMENTED` |

## Edge Desk realignment

| ID | Requirement | Governing source | Planned schema component | Implementation phase | Status | Validation method |
| --- | --- | --- | --- | --- | --- | --- |
| ED-1 | Edge Desk realignment is additive to dedicated Tennis | task T-ED0; developer brief; tennis build spec `0`; source authority docs | n/a (documentation contract) | T-ED0 | `LOCKED` | document review against source authority |
| ED-2 | Every full-slate Tennis execution has first-class run identity | tennis build spec `11. Scheduling`; task T-ED0 contract 1 and 4 | `prediction_runs` | T-ED1 | `COMPLETE` | migration fail-fast tests plus run-lifecycle and run-origin tests |
| ED-3 | Every predictive-agent output is retained regardless of manager outcome | tennis build spec `11. The fixed slate rule`; task T-ED0 contract 2 | `predictions`, `decision_agents` | T-ED1 and T-ED5 | `LOCKED` | lineage tests and no-bet retention tests |
| ED-4 | Historical predictions are reconstructable by immutable run + config identity | task T-ED0 contract 3; broader Sports AI pack additive only | `prediction_runs`, `agent_config_versions`, `predictions` | T-ED1 | `COMPLETE` | migration tests plus config-alignment, prediction-identity, and run-`as_of` tests |
| ED-5 | Historical manager policy is versioned and decisions reference the exact version used | tennis build spec `7.1` to `7.5`, `10.1`; task T-ED0 contract 5 | `decision_policy_versions`, `decisions` | T-ED1 | `COMPLETE` | policy-version tests and replay tests |
| ED-6 | Generic configuration must not weaken Tennis C1-C4 | tennis build spec `7.1 Consensus checks`; source authority docs | typed fields on `decision_policy_versions` plus architecture docs | T-ED0 and T-ED1 | `LOCKED` | source review plus policy-constraint tests |
| ED-7 | Decisions trace to the exact prediction rows used | task T-ED0 contract 7 | `decision_agents.prediction_id` plus composite lineage keys | T-ED1 | `COMPLETE` | composite-FK and constraint-trigger tests plus wrong-linkage failure tests |
| ED-8 | `NO_BET`, disagreement, veto, and unpublished states remain first-class internal history | tennis build spec `3. Database`, `8`, `11`, `12`; task T-ED0 contract 8 | `decisions` plus later publication-state expansion | T-ED0 through T-ED4 | `LOCKED` | document review plus no-bet retention tests |
| ED-9 | Market/value processing stays downstream of sporting prediction | tennis build spec `0`; `7`; architecture blind-isolation rules | `predictions` and `decisions` split | T-ED0 through predictive-agent phases | `LOCKED` | architecture review and anti-leakage tests |
| ED-10 | Historical decisions become replayable by run, predictions, configs, policy, and point-in-time cutoffs | task T-ED0 contract 10; point-in-time docs | `prediction_runs`, `predictions`, `agent_config_versions`, `decision_policy_versions`, `decisions` | T-ED1 to T-ED5 | `PLANNED` | replay integration tests after implementation plus finalization and market-rescan separation tests |
| ED-11 | Prediction persistence occurs only inside the atomic prediction-set finalization transaction, and late prediction inserts are rejected mechanically without weakening the committed run/finalization equality rule | additive Edge Desk lineage guidance; task T-ED2 contract | deferrable prediction finalization FK plus prediction insert guard requiring parent run `RUNNING` and unfinalized | T-ED2 | `DESIGNED` | independent design review plus future multi-connection PostgreSQL tests |
| ED-12 | Prediction, policy-version, config-version, decision-agent, and covered historical/provenance rows are mechanically immutable at the database level | additive Edge Desk lineage guidance; task T-ED2 contract | append-only blockers on `predictions`, `decision_policy_versions`, `agent_config_versions`, `decision_agents`, `rating_snapshots`, `odds_snapshots`, `match_versions`, `match_result_observations`, `match_stat_observations`, `player_source_ids`, `identity_resolution_events`, `identity_resolution_candidates`, `tournament_source_ids`, `match_source_ids`, `match_version_sources`, `matches`, and `data_health_checks` | T-ED2 | `DESIGNED` | independent design review plus direct SQL mutation-rejection tests |
| ED-13 | `prediction_runs`, `decisions`, and `ingest_runs` use exact controlled lifecycle guards rather than blanket append-only blocking | additive Edge Desk lineage guidance; task T-ED2 contract | `prediction_runs` state machine, one-way `published_at` rule, and `ingest_runs` terminal-only completion lifecycle | T-ED2 | `DESIGNED` | independent design review plus transition tests |
| ED-14 | Settlement/CLV append-only redesign and full publication-state closure remain deferred beyond T-ED2 | tennis build spec settlement/publication areas; task T-ED2 contract | later settlement/CLV/publication phases | T-ED3 and T-ED4 | `LOCKED` | design scope review |
