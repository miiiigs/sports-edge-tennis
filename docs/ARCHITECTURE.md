# Architecture

T-ED0 through T-ED2 contract lock for the dedicated Tennis repository, re-verified on
Friday, August 28, 2026.

Edge Desk realignment is additive. It does not replace the dedicated Tennis build with the
generic Sports AI architecture, and it does not weaken the Tennis-specific consensus rules.

The `0004_prediction_runs_and_policy_versions` schema migration is implemented on `main`.
The `0005_append_only_enforcement_and_lock_guards` schema migration is now implemented.
Agent 1 now exists as a pure deterministic computation module without database persistence,
while Agent 2, Agent 3, manager, veto, pricing, and orchestration runtime work still remain
future phases.

The historical validation worktree adds a local-only ATP results acquisition and ingest lane
for Agent 1 research validation. It does not authorize production data operations, live
provider connections, Supabase usage, schema redesign, or non-approved historical sources.

```text
SLATE GENERATOR
        |
        +------> AGENT 1
        |       Surface-adjusted Elo
        |
        +------> AGENT 2
        |       Serve/Return
        |       Exact hierarchical Markov
        |
        +------> AGENT 3
                Intransitivity graph

               BLIND

                 |
              MANAGER
                 |
               VETO
                 |
           PRICE SCANNER
                 |
        DECISION + CLV LOG
```

## T-ED0 contract lock

- Sports Edge Tennis remains a dedicated repository, database, runtime, deployment, and
  decision-history boundary.
- Tennis keeps exactly three predictive agents:
  - Agent 1: Surface-adjusted Elo
  - Agent 2: Hierarchical Markov / Barnett-Clarke
  - Agent 3: Intransitivity filter
- Agent 3 may abstain.
- The veto agent is kill-only and must never become a fourth predictive vote.
- Every scheduled in-scope Tennis singles match must have a recoverable execution path.
  Agent execution is not allowed to start only after a match already "looks like a bet."
- Every applicable predictive-agent output must be retained even when the eventual manager
  outcome is `NO_BET`, `VETOED`, unpublished, or later superseded by a rerun.
- Internal audit truth and operator-facing publication are separate concerns. "Not
  published" does not mean "not evaluated."

## Tennis manager contract

Unless a newer explicit Tennis requirement replaces it, the canonical Tennis manager logic
remains:

- C1: all active predictive agents must agree on direction
- C2: active-agent probability spread must be `<= 0.08`
- C3: no active predictive agent may place the selected side below `0.50`
- C4: required data-sufficiency and freshness checks must pass
- Consensus probability is the unweighted mean of the active predictive-agent
  probabilities.

This repository must NOT silently transform Tennis into majority voting, 2-of-3 consensus,
or a generic meta-model override merely because broader Edge Desk material supports those
patterns.

## T-ED1 design direction

The next schema phase is intentionally narrow:

- `prediction_runs` becomes the first-class full-slate execution identity.
- `prediction_runs` remains slate-scoped by contract in `0004`; match-level execution
  bookkeeping stays deferred to T-ED5.
- Execution status, prediction finalization, and rerun origin remain separate concepts.
- `decision_policy_versions` stores immutable historical manager policy versions.
- `agent_config_versions` stores immutable agent-configuration identity for exact replay.
- `agent_config_versions` is authoritative for replay identity; `predictions.model_version`
  and `predictions.feature_version` remain enforced immutable audit copies.
- `decision_agents` stores the composite lineage keys needed for PostgreSQL to prove
  same-decision, same-run, same-match, and same-agent linkage.
- No separate `prediction_locks` table is required in `0004`; run finalization is recorded
  on `prediction_runs`, while hard post-finalization write guards are deferred to T-ED2.
- `0004` must fail fast if existing `predictions`, `decisions`, or `decision_agents` rows
  already exist, because exact lineage cannot be reconstructed safely.

Chosen run model for T-ED1:

- One `prediction_runs` row represents one full-slate evaluation pass.
- `0004` does not add a match-run table.
- `run_scope` is omitted because T-ED1 has exactly one supported scope: full-slate Tennis.
- `run_mode` is omitted because it is not required for the T-ED1 lineage contract.
- Execution status is limited to `RUNNING`, `COMPLETED`, `FAILED`, and `CANCELLED`.
- `prediction_finalized_at` records logical closure of the prediction set independently from
  execution status.
- Optional parent linkage records rerun origin such as `RETRY` or `RECALCULATION`; it does
  not encode downstream supersession authority.
- Match-level execution bookkeeping is deferred to T-ED5 rather than added prematurely in
  `0004`.
- Predictions and decisions remain match-centric rows that point back to the same
  full-slate run identity.

## T-ED2 implementation direction

The implemented T-ED2 schema phase remains narrow and PostgreSQL-native:

- T-ED2 adds mechanical immutability and lock guards without redesigning the approved T-ED1
  run, policy, config, or decision-agent lineage model.
- `predictions`, `decision_policy_versions`, `agent_config_versions`, and
  `decision_agents` become append-only at the database level.
- Existing append-only historical observation and provenance tables such as
  `match_versions`, `match_result_observations`, `match_stat_observations`,
  `rating_snapshots`, `odds_snapshots`, `identity_resolution_candidates`,
  `player_source_ids`, `tournament_source_ids`, `match_source_ids`,
  `match_version_sources`, `identity_resolution_events`, `matches`, and
  `data_health_checks` also receive mechanical append-only protection in T-ED2.
- `prediction_runs` and `ingest_runs` remain lifecycle rows rather than generic append-only
  rows; T-ED2 therefore constrains their allowed state transitions instead of banning all
  updates.
- `decisions` remains historical truth, but T-ED2 permits one narrow `published_at`
  transition from `NULL` to a timestamp exactly once because the richer publication
  lifecycle still belongs to T-ED4.
- Prediction finalization in `0005` must stay transaction-safe under concurrency. The
  chosen design keeps the committed-state rule
  `predictions.prediction_locked_at = prediction_runs.prediction_finalized_at`, makes that
  finalization FK deferrable, and requires prediction rows to be persisted only inside the
  explicit prediction-set finalization transaction. That transaction must lock the parent
  run row, verify `run_status = 'RUNNING'` and `prediction_finalized_at IS NULL`, insert the
  full prediction set, set the run finalization timestamp, and then commit. A terminal or
  already-finalized run is mechanically closed to further prediction inserts, including
  terminal rows whose finalization timestamp remains `NULL`.
- No hidden trigger bypasses, session flags, or role-based escape hatches are part of the
  T-ED2 contract. Controlled changes to the guards must happen through an explicit reviewed
  migration.
- `0005` adds no new tables, columns, or indexes; it changes only FK timing behavior,
  helper functions, and triggers.
- Settlement/CLV append-only redesign remains T-ED3, fuller publication-state architecture
  remains T-ED4, and match-level execution bookkeeping remains T-ED5.

## Historical Agent 1 ingest lane

- Approved source scope for this phase is the Sackmann ATP archive lineage through the
  `Aneeshers/tennis-sackmann-archive` mirror, used for local research validation only.
- In-scope historical files are ATP main-draw plus qualifying/challenger CSVs for
  `2016-2025`; smoke validation is anchored on `2024-2025` before expansion through
  `2016-2023`.
- Raw source files remain immutable under `data/raw/sackmann/`. Validation manifests,
  dry-run reports, and ingest summaries remain ignored artifacts under
  `artifacts/historical_ingest/`.
- `src/sports_edge_tennis/data/historical_validation.py` is the reusable runner for
  acquisition verification, dry runs, disposable database reset, ingest, PIT boundary
  validation, extractor probing, and idempotency checks.
- `src/sports_edge_tennis/data/agent1_history.py` is the read-only extractor that exposes
  the latest PIT-eligible historical observation per match for Agent 1 cutoff queries.
- Historical parser failures remain fail-closed. Approved quarantine reasons in this phase
  are missing score/status rows, out-of-scope Davis Cup or team-event rows, out-of-scope
  doubles rows, and malformed self-match source rows.

## Blind isolation

Prediction agents may NOT consume:

```text
sportsbook prices
market consensus
closing lines
opening lines
other-agent outputs
```

The manager/scanner layer is where market comparison begins.

## High-risk engineering invariants

### Point-in-time

Every historical computation must ultimately support `as_of`. No future result may
influence an earlier prediction.

### Identity

Ambiguous player-name resolution must fail closed. Unmatched names must be logged.

### Tennis scoring

Future Agent 2 must support exact:

```text
point -> game -> tiebreak -> set -> match
```

with event/year-specific rules.

### Agent 3

The intransitivity threshold must come from the historical score distribution, not ROI
tuning.

### Manager

No directional disagreement may become a bet.

Tennis C1-C4 precedence must remain explicit in both documentation and future policy
versioning.

### Veto

Veto can only kill.

### Market

Bookmaker odds must not be model features.

### Lineage

Historical runs, predictions, policy versions, and downstream decisions must remain
reconstructable by exact immutable identifiers rather than by timestamp proximity or
version-string guessing.
