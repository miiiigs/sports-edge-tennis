# Architecture

T-ED0 contract lock for the dedicated Tennis repository, re-verified on Wednesday,
August 26, 2026.

Edge Desk realignment is additive. It does not replace the dedicated Tennis build with the
generic Sports AI architecture, and it does not weaken the Tennis-specific consensus rules.

Target structure only. T-ED1 is design-only in this repository state. No `0004` migration
or runtime implementation exists yet.

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
