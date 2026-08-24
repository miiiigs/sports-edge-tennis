# Full Spec Alignment Audit

Date: Tuesday, August 18, 2026

Authoritative sources reviewed:

- `initial_data/00-developer-brief.pdf`
- `initial_data/01-tennis-build-spec.pdf`
- `initial_data/02-football-build-spec.pdf`
- `initial_data/03-thirty-day-plan.pdf`

Secondary references reviewed:

- `initial_data/*.md`
- existing repository documentation under `docs/`
- current source, tests, research artifacts, and SQLite stores

The PDFs are treated as authoritative wherever wording differs.

## Executive Summary

The repository is not currently aligned with the full August 2026 specification set.

It does contain a meaningful transitional foundation:

- a real football historical ingestion spine
- reusable blind/firewall and locking concepts
- reusable evaluation, dashboard, and paper/shadow concepts
- a generic football GAP core with tested chronological shots, shots-on-target, and corners walk-forward lanes
- preserved legacy football Poisson research for benchmarking

However, the repo is still structurally non-compliant with the primary brief:

- there is one repository, not two
- there is one shared Python runtime, not separate football and tennis runtimes
- active storage is SQLite, not two separate PostgreSQL databases
- football production logic is still legacy `1X2`-centric outside the new GAP slice
- tennis is only a light scaffold and is far from the required three-agent architecture

The honest current position is:

- football mechanics are now at `Day 11A COMPLETE` for the currently loaded football scope
- the Day 10 model layer remains the frozen fair-price authority, and Day 11A now consumes it through a verified manager-core layer
- the football product-output surface now includes verified Day 10 and Day 11 packages under `research/product_outputs/football/`
- the locked next football path is Day 12 safeguards/veto foundation, then a football logical-pipeline freeze, before tennis implementation begins
- Week 1 foundations are still incomplete
- Week 3 tennis work is largely not started
- Week 4 measurement/paper layers exist in partial form but are not yet spec-compliant

## 1. Current Repository State

### 1.1 Architecture

Current architecture on Tuesday, August 18, 2026:

- one repository root containing code, docs, research, initial PDFs, and workflow artifacts
- one Python package: `code/src/sports_ai`
- one shared runtime containing football, tennis, market, storage, evaluation, and review tooling
- local SQLite research and ledger stores are the active implementation
- PostgreSQL is referenced in config and documentation, but not the live persistence path

Top-level repository structure:

- `code/` Python package, tests, packaging metadata
- `docs/` project planning, blockers, data source notes, status docs
- `research/` historical artifacts, SQLite databases, dashboards, manual inputs
- `initial_data/` authoritative specs and markdown mirrors
- `manifest/` agent manifest
- `n8n/` workflow artifact

Package structure under `code/src/sports_ai`:

- `agents/`
- `data_sources/`
- `evaluation/`
- `experiments/`
- `pipeline/`
- `storage/`
- `models/`

Python module count:

- `53` Python modules under `code/src/sports_ai` excluding `__pycache__` and egg-info

### 1.2 Tests

Current test surface:

- `24` test files
- `165` explicit `test_...` functions
- `175` collected pytest cases

Coverage is strongest around:

- football Poisson research
- football GAP invariants and walk-forward chronology
- football ingestion and watchlist flows
- devig math
- firewall and locking
- paper/shadow ledger and reporting

Coverage is weak for:

- tennis architecture beyond simulation mechanics
- separate-process blindness
- spec-required measurement gates
- full repository/database separation

### 1.3 Implemented Models

Football:

- `poisson_1x2_v1`
- `poisson_1x2_v2`
- `poisson_1x2_v3`
- `poisson_1x2_v4` with Dixon-Coles low-score correction math
- generic `gap_ratings` core supporting `shots`, `shots_on_target`, and `corners` at the contract level
- chronological shots GAP walk-forward experiment
- chronological shots-on-target GAP walk-forward experiment
- chronological corners GAP walk-forward experiment
- Day 8 downstream goal-conversion layer from fitted GAP process-stat outputs
- Day 9 downstream Dixon-Coles scoreline layer from Day 8 expected goals
- Day 10 downstream football market-pricing layer from the frozen Day 9 scoreline matrix and Day 8 corners object
- deterministic Poisson versus negative-binomial corner-distribution comparison with evidence-based family selection

Tennis:

- simple Elo-based player strength agent
- point-level tennis simulation agent using Monte Carlo

Market / arithmetic:

- proportional devig
- power devig
- Shin devig
- weighted consensus across books

### 1.4 Implemented Data Pipelines

Football historical:

- `football_data_co_uk.py` profiles historical CSVs
- `football_history.py` ingests football-data.co.uk CSVs into separated sports rows and market rows
- `football_research_store.py` persists those bundles into SQLite
- `football_gap_data_readiness.py` audits actual stored-row readiness against expected GAP inputs

Football upcoming / live:

- `football_upcoming.py` discovers upcoming fixtures from multiple sources
- `football_watchlist.py` generates a football watchlist and optional paper/shadow candidates
- `review_app.py` can run live watchlist actions and export dashboard state

Paper/shadow:

- `paper_shadow_ledger.py` stores immutable decisions, later outcome updates, lock rows, and market snapshots
- `evaluation/reporting.py` summarizes outputs
- `evaluation/dashboard.py` renders HTML dashboards

Tennis:

- no historical tennis ingest pipeline exists yet
- no tennis odds-join pipeline exists yet

### 1.5 Existing APIs / Providers

Connected in code:

- football-data.co.uk historical CSV archive
- football-data.co.uk current CSV fetches
- football-data.org fixtures API
- API-Football fixtures API
- manual JSON / CSV fixture and market inputs

Not implemented in active code:

- tennis historical provider integration
- tennis odds archive integration
- live sharp-reference odds stack resolved per spec
- xG provider integration
- lineup / injury / weather / referee veto feeds

### 1.6 Current Database / Storage Design

Active stores:

- `research/football_research/store/football_research.sqlite`
- `research/paper_shadow_football.sqlite`
- `research/paper_shadow_watchlist.sqlite`
- `research/paper_shadow_historical.sqlite`
- `research/paper_shadow_demo.sqlite`

Observed table counts:

`research/football_research/store/football_research.sqlite`

- `football_ingest_runs`: `25`
- `football_events`: `12,080`
- `football_market_quotes`: `46,904`
- `football_coverage_snapshots`: `25`

`research/paper_shadow_watchlist.sqlite`

- `paper_shadow_decisions`: `30`
- `paper_shadow_lock_rows`: `30`
- `paper_shadow_market_snapshots`: `30`
- `paper_shadow_outcome_updates`: `30`

Current design characteristics:

- SQLite-first, local, transitional
- sports and market rows are separated in football historical ingest
- ledger rows are append-only by application pattern, not by database-level immutability enforcement
- `as_of` is not yet universally present across persisted schemas
- no separate football and tennis databases exist

### 1.7 Current Football Data Scope

Historical football archive loaded:

- competitions: `ENG1`, `ENG2`, `ENG3`, `ENG4`, `ESP1`
- seasons: `2122`, `2223`, `2324`, `2425`, `2526`

Important gap:

- current store is not the full football-spec scope
- missing expected competitions include `EFLC`, `FAC`, `FRA1`, `GER1`, `ITA1`, `SCO1`, `UCL`, `UEL`

### 1.8 Current Paper / Shadow Workflow

Existing workflow:

- generate a decision candidate or watchlist row
- optionally persist prediction and market lock rows
- store a paper/shadow decision
- append later close / settlement updates
- compute CLV and simple outcome metrics
- export dashboard HTML
- optionally drive this through `review_app.py`

This is useful and reusable, but it is not yet the spec-compliant final paper-publication system.

## 2. Specification Gap Analysis

Status legend:

- `COMPLETE`
- `PARTIAL`
- `NOT STARTED`
- `BLOCKED`

### A. Developer Brief Compliance

| Requirement | Status | Evidence | Gap |
| --- | --- | --- | --- |
| Two repositories | `NOT STARTED` | one repo only | football and tennis still coexist in one workspace |
| Two databases | `NOT STARTED` | shared SQLite stores | no separate football and tennis DBs |
| No shared runtime | `NOT STARTED` | one shared `sports_ai` package | direct conflict with brief |
| Blind sports analysis | `PARTIAL` | firewall, lock chain, manifest zones exist | enforced for selected paths only; not separate-process architecture |
| Market separation | `PARTIAL` | football historical ingest separates sports rows and market rows | not universal across the system and not split per final architecture |
| Measurement layers built before model trust | `PARTIAL` | metrics, CLV, dashboard, ledger exist | calibration/CLV/log coverage are incomplete and not yet spec-compliant |
| Staking / arithmetic rules | `PARTIAL` | EV-style logic and devig methods exist | no full arithmetic library, no odds-ratio/additive, no sample-size calculator, CLV formula is wrong, no authoritative quarter-Kelly implementation |
| No automatic betting | `COMPLETE` | no bookmaker execution code; paper/shadow only | aligned |
| Paper publication workflow | `PARTIAL` | ledger, review app, dashboard, watchlist exist | not yet aligned to full-spec logging, freshness, kill-switch, and publication constraints |
| R1 football shots/corners not goals | `PARTIAL` | encoded in GAP layer | repo still contains goals-first legacy production logic outside GAP slice |
| R2 power/Shin devig for longshots | `PARTIAL` | power and Shin exist | no repo-wide enforcement and current football watchlist uses simple devig path for 1X2 |
| R4 walk-forward only | `PARTIAL` | several chronological football experiments exist | not yet universal across all required model families |
| R5 every record carries `as_of` | `PARTIAL` | some timing fields exist | not enforced across all schemas and model artifacts |
| R6 log every decision including no-bets | `PARTIAL` | ledger supports `PASS` and `WATCH` | not yet universal across both sports and all planned scanners |
| R7 version model/features/calibration together | `PARTIAL` | model versions exist in many flows | no complete variant counter / unified versioning discipline |
| R8 fail loudly | `PARTIAL` | some diagnostics and blocking logic exist | no full freshness gate / halt-on-stale system |
| R9 publish probability, fair price, market price, gap | `PARTIAL` | watchlist and ledger rows often contain these fields | not standardized across all outputs and sports |
| R10 no certainty language | `PARTIAL` | forbidden-decision words exist in config/demo path | not enforced repo-wide |

Bottom line for the Developer Brief:

- reusable concepts exist
- the required top-level architecture is not implemented
- arithmetic and measurement are present but incomplete
- no-betting rule is respected

### B. Football Specification Compliance

#### Data

| Requirement | Status | Evidence | Gap |
| --- | --- | --- | --- |
| Required leagues loaded | `PARTIAL` | five competitions loaded into research store | spec scope still materially incomplete |
| Shots data | `COMPLETE` for loaded scope | readiness report confirms stored coverage in loaded competitions | not complete for full spec scope |
| Shots on target data | `COMPLETE` for loaded scope | readiness report confirms stored coverage in loaded competitions | not complete for full spec scope |
| Corners data | `COMPLETE` for loaded scope | readiness report confirms stored coverage in loaded competitions | not complete for full spec scope |
| Odds data | `PARTIAL` | historical 1X2 quote rows loaded | not yet full market architecture required by spec |
| Point-in-time data / `as_of` discipline | `PARTIAL` | chronological experiments and timestamps exist | not enforced as a universal persisted contract |

#### Model

| Requirement | Status | Evidence | Gap |
| --- | --- | --- | --- |
| GAP ratings core | `PARTIAL` | generic `gap_ratings.py` implemented and tested | shots, shots-on-target, and corners runners now exist; not yet full football engine |
| Home/away separation | `COMPLETE` in GAP core | venue-specific attack/defense state exists | needs persistence and full runtime integration |
| Attack/defence state | `COMPLETE` in GAP core | explicit attack and defense ratings per venue | same note as above |
| Learning-rate fitting | `COMPLETE` for loaded Day 7 scope | `football_gap_lambda_fitting.py` plus fitted Day 7 artifacts record per-competition/statistic walk-forward lambda selection | full football build still missing later Day 10+ stages |
| Independent shots / SOT / corners systems | `COMPLETE` for loaded Day 7 scope | contract supports all three, realized walk-forward lanes exist, and fitted artifacts now persist explicit `as_of` and `model_version` | broader football engine still incomplete beyond Day 7 |
| Expected shots conversion to goal expectation | `COMPLETE` for loaded Day 8 scope | `football_day8_goal_conversion.py` plus Day 8 artifacts fit league-and-venue conversions from fitted GAP `shots` and `shots_on_target` to expected goals | broader football build still missing downstream Day 10+ stages |
| Goal expectation downstream of GAP | `COMPLETE` for loaded Day 8 scope | expected goals are now generated in a separate chronological downstream layer with explicit `as_of`, model versions, and no GAP-state mutation | major remaining gap is market derivation and live/runtime integration, not the Day 8 conversion contract |
| Dixon-Coles scoreline model | `COMPLETE` for loaded Day 9 scope | `football_day9_scoreline_model.py` plus Day 9 artifacts fit rho chronologically downstream of Day 8 expected goals and persist the full `0..10 x 0..10` scoreline matrix | broader football build still missing Day 11+ live/runtime integration |
| Market derivation from one scoreline object | `COMPLETE` for loaded Day 10 scope | `football_day10_market_pricing.py` derives goals markets from the frozen Day 9 matrix and corner totals from the Day 8 corner object with deterministic source ids and settlement-aware fair prices | broader football build still missing Day 11 arithmetic/manager logic, live/runtime integration, and later market families |

#### Markets

| Requirement | Status | Evidence | Gap |
| --- | --- | --- | --- |
| Over/under goals | `COMPLETE` for loaded Day 10 scope | goals totals are priced from the frozen Day 9 scoreline matrix across integer, half, and quarter verification lines | broader live line-selection and Day 11 edge logic still missing |
| Corners | `COMPLETE` for loaded Day 10 scope | corner totals are priced from the frozen Day 8 corner object with adaptive support and explicit tail metadata | broader live line-selection and Day 11 edge logic still missing |
| BTTS | `COMPLETE` for loaded Day 10 scope | BTTS yes/no is priced directly from the frozen Day 9 scoreline matrix | broader live/runtime integration still missing |
| Asian handicap | `COMPLETE` for loaded Day 10 scope | settlement-aware AH pricing exists with integer, half, and quarter-line handling under a fixed `home_handicap` convention | broader Day 11 probability semantics and line-selection rules still missing |
| 1X2 secondary role | `PARTIAL` | Day 10 now derives `1X2` mechanically from the scoreline object as a secondary market family | current repo still contains legacy `1X2`-centric lanes outside the new bounded build path |

#### Safety / operations

| Requirement | Status | Evidence | Gap |
| --- | --- | --- | --- |
| xG veto | `NOT STARTED` | no xG ingest or veto path | missing |
| Lineup / injury / weather checks | `NOT STARTED` | no such veto feeds are wired | missing |
| Market separation | `PARTIAL` | historical ingestion separates rows | not complete in full football runtime |
| Cup restrictions | `NOT STARTED` | no hard cup-only market filter | missing |
| Type A vs Type B scanner separation | `PARTIAL` | legacy watchlist and historical close-value lanes are separate concepts | not implemented as full football live scanner system |

Bottom line for football:

- the repo has a real and valuable football research base
- the new football path is only started, not complete
- the most aligned current asset is the closed Day 7 plus Day 8 football GAP foundation
- the most non-aligned current asset is the continued `1X2`-centric live/research lane

### C. Tennis Specification Compliance

| Requirement | Status | Evidence | Gap |
| --- | --- | --- | --- |
| Separate tennis architecture / repo / DB | `NOT STARTED` | tennis lives in shared package | direct architecture conflict |
| ATP-only scope | `PARTIAL` | current tennis code is generic and men-focused by naming/usage | no data ingest or scope enforcement pipeline exists |
| Surface Elo | `PARTIAL` | simple Elo agent exists | no overall/hard/clay/grass four-book system or 50/50 blend |
| Serve/return model | `NOT STARTED` | no SPW/RPW pipeline | missing |
| Markov model | `PARTIAL` | Monte Carlo tennis simulation exists | spec requires exact recursive Barnett-Clarke style propagation for match probability |
| Intransitivity model | `NOT STARTED` | no graph agent exists | missing |
| Blind agent isolation | `PARTIAL` | manifest zones and firewall concept exist | spec requires three separate blind processes; not implemented |
| Manager | `NOT STARTED` | no tennis manager with C1-C4 consensus rules | missing |
| Veto agent | `NOT STARTED` | no tennis veto module | missing |
| Derivatives | `NOT STARTED` | no set betting / total games / handicap outputs | missing |
| Tennis historical ingest | `NOT STARTED` | no Sackmann + tennis-data pipeline | missing |
| Challenger routing / common opponents | `NOT STARTED` | not implemented | missing |

Bottom line for tennis:

- tennis is still a scaffold, not a build
- only two early ingredients exist: Elo and a scoring simulator
- the required operational architecture is mostly absent

### D. Thirty-Day Plan Progress Mapping

| Day | Requirement | Expected Deliverable | Current Status |
| --- | --- | --- | --- |
| 1 | Environment, repositories, database | two repos, two DBs, core schema, append-only odds snapshots | `PARTIAL` |
| 2 | Arithmetic library | full shared-per-repo arithmetic library, tests, CLV formula, Kelly | `PARTIAL` |
| 3 | Odds feed and sharp reference | named sharp source, snapshots landing, rate limits documented | `PARTIAL` |
| 4 | Football historical data | full archive loaded, as_of populated, coverage report | `PARTIAL` |
| 5 | Tennis historical data | match + odds joined, coverage report, unmatched names logged | `NOT STARTED` |
| 6 | GAP ratings engine part one | shots GAP running, hand-checked, goals banned | `COMPLETE` |
| 7 | GAP ratings engine part two | shots/SOT/corners, fitted lambdas, persisted ratings | `COMPLETE` for loaded football scope |
| 8 | Statistics to goal expectation | GAP stats to goal expectation, corner distribution evidence | `COMPLETE` for loaded football scope |
| 9 | Scoreline model | fitted Dixon-Coles downstream of GAP | `COMPLETE` for loaded football scope |
| 10 | Matrix to market prices | all in-scope football markets priced | `COMPLETE` for loaded football scope |
| 11 | Football manager and thresholds | slate generator, thresholds, cup rules, reason-coded decisions | `PARTIAL` |
| 12 | AH line-type rule and xG check | line-type rule, xG veto path | `PARTIAL` |
| 13 | Surface Elo | four-rating surface Elo with fitted K | `PARTIAL` |
| 14 | Serve/return adjustment | adjusted serve probabilities | `NOT STARTED` |
| 15 | Hierarchical Markov propagation | exact recursion, event/year format rules | `PARTIAL` |
| 16 | Common-opponent routing | common-opponent input layer only | `NOT STARTED` |
| 17 | Intransitivity graph | score and threshold distribution | `NOT STARTED` |
| 18 | Intransitivity model and abstention | graph model plus abstention | `NOT STARTED` |
| 19 | Tennis manager | process isolation, consensus rules, thresholds | `NOT STARTED` |
| 20 | Price scanners | Type A / Type B scanners for both systems | `PARTIAL` |
| 21 | Veto agents | both sports kill-only veto layers | `NOT STARTED` |
| 22 | Freshness gate, monitoring, kill switch | stale-feed halt, alerts, CLV stop, drawdown stop | `NOT STARTED` |
| 23 | Calibration and CLV pipelines | automatic closes, correct CLV, reliability diagrams | `PARTIAL` |
| 24 | Scheduling and publication | full schedule, UTC handling, empty slate, retraction path | `PARTIAL` |
| 25 | Football walk-forward backtest | full archive walk-forward, no-leak assertion | `PARTIAL` |
| 26 | Tennis walk-forward backtest | per-agent and consensus tennis evaluation | `NOT STARTED` |
| 27 | Dashboard | full measurement-layer dashboard | `PARTIAL` |
| 28 | Paper trading launch | both systems live to permanent paper ledger, stake rule recorded | `PARTIAL` |
| 29 | Hardening | failure-mode tests, idempotency, secrets, backup/restore | `PARTIAL` |
| 30 | Handover | runbook, limitations, variant counts, unattended running | `PARTIAL` |

## 3. Existing Work Classification

### Keep And Integrate

These assets align strongly with the new specifications and should remain part of the migration path:

- `firewall.py`
- `locking.py`
- `agents/market/devig.py`
- `data_sources/football_history.py`
- `storage/football_research_store.py` concept and schema ideas
- `storage/paper_shadow_ledger.py` concept and immutable decision pattern
- `experiments/football_gap_data_readiness.py`
- `agents/football/gap_ratings.py`
- `experiments/football_gap_shots_walkforward.py`
- `experiments/football_gap_shots_on_target_walkforward.py`
- `experiments/football_gap_corners_walkforward.py`
- `evaluation/metrics.py` baseline measurement utilities
- `evaluation/dashboard.py` and `evaluation/reporting.py` as reusable reporting shells

Why:

- these pieces match the migration direction or provide strong spec-compatible infrastructure

### Keep As Baseline

These assets are useful for benchmarking, auditing, or arithmetic reuse, but they are not the main product direction:

- `poisson_1x2_v1`
- `poisson_1x2_v2`
- `poisson_1x2_v3`
- `poisson_1x2_v4`
- Track B model selection / finalization artifacts
- historical football close-value lane
- optimizer diagnostics tied to the Poisson family
- legacy watchlist decision policy validations

Why:

- they preserve prior research and reusable scoreline math
- they should not drive the new football build

### Rework

These components are useful but require non-trivial adaptation:

- `pipeline/football_watchlist.py`
- `review_app.py`
- `evaluation/clv.py`
- local SQLite schemas if retained as transitional tooling
- tennis Elo and simulation code
- current scheduling/publication pathways
- current decision reason taxonomy

Why:

- they provide a base, but their rules, storage, or architecture do not yet meet the new PDF requirements

### Retire

Retire means "do not extend as the main build direction," not "delete now."

- using football `1X2` as the main football product path
- threshold tuning / profitability optimization ahead of the required football model stack
- treating the demo daily tennis pipeline as representative of the final tennis system
- any assumption that current shared-runtime architecture is acceptable long term

### Requested Component Evaluation

| Component | Classification | Notes |
| --- | --- | --- |
| Poisson `1X2` v1-v4 | `KEEP AS BASELINE` | preserve for benchmarking and reusable Dixon-Coles math |
| DQ audits | `KEEP AND INTEGRATE` | directly useful for spec-required readiness and quality gates |
| Optimizer diagnostics | `KEEP AS BASELINE` | valuable for legacy model governance, not central to new specs |
| Paper shadow ledger | `KEEP AND INTEGRATE` | core append-only concept is strong; schema/rules need refinement |
| Odds snapshots | `REWORK` | concept is required, but storage/enforcement/provider stack is incomplete |
| CLV infrastructure | `REWORK` | useful base, but formula and capture requirements are incomplete |
| Backtesting framework | `KEEP AND INTEGRATE` | chronological validation shell is valuable and aligned |
| GAP engine | `KEEP AND INTEGRATE` | strongest current alignment with the football spec |
| Walk-forward experiments | `KEEP AND INTEGRATE` | one of the best current assets |

## 4. Weekly Progress Summary Against the 30-Day Plan

### Week 1 - Foundations

Status:

- `PARTIAL`

Evidence:

- package, tests, and some data pipelines exist
- devig math exists
- local persistence exists
- fixture discovery exists

Missing:

- two repositories
- two PostgreSQL databases
- append-only odds snapshots enforced at DB level
- complete arithmetic library
- full `as_of` discipline
- tennis historical data foundation
- resolved sharp reference stack

### Week 2 - Football

Status:

- `PARTIAL`, with five strong completed slices

Evidence:

- football historical ingest exists
- football GAP data-readiness report exists and has been run on the real store
- generic GAP core exists and is well tested
- chronological shots GAP walk-forward exists and is deterministic
- chronological shots-on-target GAP walk-forward exists and is deterministic
- chronological corners GAP walk-forward exists and is deterministic
- walk-forward lambda fitting exists with explicit `as_of` and variant logging
- chronological Day 8 expected-goals conversion and corner-distribution evidence now exist
- chronological Day 9 Dixon-Coles scoreline matrices now exist downstream of Day 8 expected goals
- reusable Dixon-Coles arithmetic exists in legacy form

Missing:

- full in-scope data coverage
- O/U, BTTS, AH, corners market derivation
- xG veto and football safety layer

### Week 3 - Tennis

Status:

- `NOT STARTED` in spec terms, despite early scaffolding

Evidence:

- simple Elo code exists
- scoring simulation exists

Missing:

- tennis data ingest
- surface Elo implementation
- serve/return pipeline
- exact recursive Markov model
- common-opponent routing
- intransitivity filter and model
- tennis manager
- tennis veto
- standalone tennis architecture

### Week 4 - Measurement / Paper Trading

Status:

- `PARTIAL`

Evidence:

- ledger exists
- dashboard exists
- review app exists
- CLV and calibration utilities exist
- live football watchlist can create paper/shadow candidates

Missing:

- correct CLV formula including closing margin
- full freshness gate
- kill switches
- both-sport price scanners
- both-sport veto agents
- automated closing-price capture for both systems
- fully compliant scheduling and publication
- both-sport permanent paper trading with recorded stake rule

## 5. Ranked Blocker List

### 1. Shared architecture conflicts with the primary brief

Impact:

- prevents honest compliance with the most fundamental requirement

Severity:

- `CRITICAL`

Recommended action:

- complete a dependency map, then split football and tennis into separate repositories, runtimes, and databases after the current football GAP migration slice is stable

### 2. Full football-spec historical scope is not loaded

Impact:

- blocks honest claims about full football readiness, multi-league GAP fitting, and downstream evaluation

Severity:

- `CRITICAL`

Recommended action:

- finish loading the missing in-scope competitions and preserve scope-readiness reporting as the gate

### 3. Tennis historical data pipeline is absent

Impact:

- blocks almost all Week 3 tennis work

Severity:

- `CRITICAL`

Recommended action:

- implement Sackmann plus odds archive ingestion before any serious tennis modeling work

### 4. Live sharp-reference provider is unresolved

Impact:

- blocks live scanner trust, CLV reference quality, and final publication logic

Severity:

- `HIGH`

Recommended action:

- resolve the sharp reference source first, document measured booksum on real fixtures, and standardize capture/storage

### 5. Arithmetic layer is incomplete and CLV is not spec-compliant

Impact:

- corrupts measurement and staking interpretation if left unchanged

Severity:

- `HIGH`

Recommended action:

- build the full arithmetic library per repository, fix CLV formula, add missing functions and tests, and record method names in outputs

### 6. Football downstream stack is incomplete beyond Day 9

Impact:

- current football build cannot yet produce the required markets from the required model path

Severity:

- `HIGH`

Recommended action:

- preserve the frozen Day 7, Day 8, and Day 9 artifacts, then build football market derivation from the single scoreline and corners objects without reopening the upstream GAP or conversion selection layers

### 7. Veto feeds and freshness gates are missing

Impact:

- publication safety remains below spec

Severity:

- `HIGH`

Recommended action:

- do not expand live publication claims until veto feeds and freshness halts exist

### 8. Provider identity / normalization is incomplete

Impact:

- later multi-provider joins can become brittle or incorrect

Severity:

- `MEDIUM`

Recommended action:

- add explicit team and player identity normalization tables before expanding providers further

### 9. Staking rule is not yet authoritative in implementation

Impact:

- paper-trading recommendations cannot yet claim full spec alignment

Severity:

- `MEDIUM`

Recommended action:

- implement the quarter-Kelly capped-at-2% rule only after the arithmetic layer is finalized in the repo-specific library

## 6. Recommended Next Implementation Order

Safest next sequence:

1. Compliance fixes that do not destroy current research value
2. Required football data foundations
3. Football GAP completion
4. Tennis data and model foundations
5. Measurement-layer corrections
6. Paper-trading hardening

Concrete order:

1. Keep the current repository intact as the migration workspace for now.
2. Do not extend football `1X2` as the main product direction.
3. Preserve the closed Day 7, Day 8, and Day 9 football artifacts as the frozen baseline.
4. Derive football markets in spec order from the Day 9 scoreline object and the Day 8 corner object: O/U, BTTS, AH, corners, with `1X2` as secondary.
5. Correct the arithmetic / CLV foundation.
6. Only then begin the tennis historical ingest and full three-agent build.
7. Defer profitability optimization and threshold tuning until the required model stack is complete.

## 7. Current Project Position in the Plan

The project does not sit neatly on one day because it is out of sequence:

- ahead of plan on one football research slice: `Day 10 COMPLETE` for the loaded scope
- still behind on several Week 1 foundations
- effectively pre-`Day 5` for tennis

The most honest summary is:

- architecture and data foundations are behind the PDFs
- football GAP mechanics are the most mature aligned work
- tennis is still largely pre-build

## 8. Final Audit Answer Before More Coding

### Current project state

- one shared transitional repo
- SQLite-backed research and paper/shadow storage
- strong football historical ingestion and research artifacts
- strong legacy football Poisson baseline
- promising new football GAP shots, shots-on-target, and corners slices plus closed Day 8, Day 9, and Day 10 downstream fair-price layers
- minimal tennis scaffolding

### Current day in the 30-day plan

- effectively at `Day 10 COMPLETE` for the currently loaded football mechanics
- still incomplete on major `Day 1` to `Day 5` foundations

### Completed deliverables

- football historical ingest foundation
- actual football readiness reporting against stored rows
- generic GAP ratings core
- deterministic chronological shots GAP walk-forward
- deterministic chronological shots-on-target GAP walk-forward
- deterministic chronological corners GAP walk-forward
- walk-forward lambda fitting with logged variants and explicit `as_of`
- chronological GAP-statistics-to-goal conversion plus corner-distribution evidence
- chronological Dixon-Coles scoreline matrices downstream of Day 8 expected goals
- deterministic Day 10 fair-price derivation for `1X2`, `BTTS`, goals totals, Asian handicap, and corner totals from frozen pre-match model objects
- blind/firewall and lock-chain concepts
- paper/shadow ledger, dashboard, and review shell
- preserved football Poisson research baseline

### Missing deliverables

- two repos / two DBs / no shared runtime
- full football-spec historical scope
- Day 11 football arithmetic / manager layer
- tennis ingest and three-agent architecture
- full veto/freshness/kill-switch safety layer
- current forward-production football parameter snapshots and live odds/sharp-reference integration

### Biggest risks

- continuing to extend legacy `1X2` as if it were the new football product
- mistaking reusable transitional infrastructure for full spec compliance
- starting tennis modeling before tennis data foundations exist
- doing threshold or ROI optimization before the required model stack is complete

### Recommended next coding task

- begin Day 11 only through the bounded arithmetic/manager path that preserves settlement-aware push and quarter-line semantics, while treating current forward-production football parameter fitting and live odds/sharp-reference resolution as parallel dependencies rather than silently solved problems

### Day 7 through Day 10 acceptance audits

- the explicit Day 7 closure record lives in:
  - `research/spec_migration/FOOTBALL_DAY7_ACCEPTANCE_AUDIT_2026-08-17.md`
- the explicit Day 8 closure record lives in:
  - `research/spec_migration/FOOTBALL_DAY8_ACCEPTANCE_AUDIT_2026-08-17.md`
- the explicit Day 9 closure record lives in:
  - `research/spec_migration/FOOTBALL_DAY9_ACCEPTANCE_AUDIT_2026-08-17.md`
- the explicit Day 10 closure record lives in:
  - `research/spec_migration/FOOTBALL_DAY10_ACCEPTANCE_AUDIT_2026-08-18.md`
