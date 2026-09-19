# Decision Log

This log records irreversible architectural and governance decisions. Entries are append-only.

## D001

Tennis is developed in a dedicated repository and runtime, independent from football.

## D002

Tennis receives a separate database. Not provisioned yet.

## D003

Legacy tennis scaffolds from the football repository (`elo.py`, `simulation.py`) are
reference-only. They must not be imported or used as Agent 1 or Agent 2.

## D004

All final tennis validation must be chronological walk-forward.

## D005

Prediction agents must not consume bookmaker prices.

## D006

Evaluation priority:

```
Calibration
CLV
ROI
```

## D007

Any materially tested model variation must be counted. No silent tuning.

## D008

Tennis data persistence is PostgreSQL-first and uses committed SQL migrations rather than
ORM-generated schema state.

## D009

Canonical player, tournament, edition, and match identities use immutable surrogate keys.
Names and fuzzy matches are never primary keys.

## D010

Player identity resolution fails closed. Ambiguous and unmatched names are stored as
explicit resolution events rather than being silently auto-mapped.

## D011

Match identity, match result observations, and decision settlement are separate layers.
Post-match facts must never be stored on the pre-match fixture identity in a way that hides
look-ahead leakage.

## D012

Odds snapshots are append-only and must retain `source_observed_at`, `as_of`, and
`ingested_at` separately. No price overwrites.

## D013

Ratings, predictions, and manager decisions are immutable historical records carrying
`model_version`, `feature_version`, and `as_of`.

## D014

Type A model-edge signals and Type B price-dispersion signals are distinct decision classes
and must remain separable in storage and reporting.

## D015

Fallback provenance hashes use the repository-wide `SE_SOURCE_HASH_V1` contract:
versioned canonical JSON, UTF-8, Unicode NFKC normalization, explicit nulls, normalized
timestamps/decimals, and SHA-256 digests.

## D016

`match_versions` remains canonical normalized match state. Stable source-to-match identity
lives in `match_source_ids`, while append-only version lineage lives in
`match_version_sources`.

## D017

Schema changes are not considered validated until the committed PostgreSQL 16 integration
suite passes in GitHub Actions against an empty disposable database through UP, runtime
constraints, DOWN, and clean re-UP.

## D018

Side-labelled market selections must agree with their stored side number:
`PLAYER_1` / `SIDE_1` map to `selection_side = 1`, and `PLAYER_2` / `SIDE_2` map to
`selection_side = 2`.

## D019

Edge Desk realignment is additive to the dedicated Sports Edge Tennis repository. It does
not replace Tennis with the broader generic Sports AI architecture.

## D020

Tennis keeps exactly three predictive agents: Surface-adjusted Elo, Hierarchical Markov /
Barnett-Clarke, and the Intransitivity Filter.

## D021

The Tennis veto agent remains kill-only and must never be counted as a fourth predictive
vote.

## D022

The Tennis manager contract C1-C4 remains authoritative unless a newer explicit Tennis
requirement directly supersedes it.

## D023

Every applicable predictive-agent output must be retained regardless of disagreement,
`NO_BET`, veto, stale market, price failure, publication outcome, or rerun supersession.

## D024

Execution/run identity becomes a first-class schema concept. Historical prediction and
decision rows must trace back to an explicit full-slate run rather than to timestamp
proximity or version strings alone. In T-ED1, one run means one coherent full-slate Tennis
evaluation pass; no match-run table is introduced, and execution status remains separate
from prediction finalization. Optional parent linkage records rerun origin only and remains
separate from any future authoritative supersession concept. Within one run, a given agent
may produce at most one prediction for a given match; another variant requires a new run.

## D025

Historical manager policy is versioned. Decisions must reference the exact policy version
used at evaluation time. The same match and prediction run may legitimately produce multiple
immutable downstream decisions when market/value/veto evaluation is rerun later.

## D026

Historical decisions must trace to the exact immutable prediction rows used by the manager.
`decision_agents` therefore carries the composite lineage keys needed for PostgreSQL to
enforce same-decision, same-run, same-match, and same-agent linkage. Duplicated probability
fields inside `decision_agents` are audit copies, not sufficient lineage by themselves.

## D027

Mechanical post-finalization immutability enforcement is deferred to T-ED2. T-ED1 only
introduces the lineage entities needed for run, policy, config, and prediction references.
T-ED1 may still use relational correctness constraints, including composite foreign keys and
a narrow lineage constraint trigger for audit-copy/status alignment. Agent configuration
authority lives in `agent_config_versions`, while prediction `model_version` and
`feature_version` remain enforced immutable audit copies.

## D028

Migration `0004` is reserved for run, policy, and agent-configuration lineage after design
approval. It must fail fast if legacy `predictions`, `decisions`, or `decision_agents`
tables already contain rows, and it must not be used to begin T-ED2 immutability work or
to synthesize fake historical lineage.

## D029

T-ED2 preserves the committed-state rule
`predictions.prediction_locked_at = prediction_runs.prediction_finalized_at`, but future
`0005` must make `fk_predictions_prediction_run_finalization` deferrable and require the
prediction-finalization transaction to be the only valid persistence path for prediction
rows. That transaction must lock the target `prediction_runs` row, verify
`run_status = 'RUNNING'` and `prediction_finalized_at IS NULL`, insert the full prediction
set, set `prediction_finalized_at`, and commit atomically. No separate `prediction_locks`
table or hidden bypass flag is part of the chosen design.

## D030

In T-ED2, `predictions`, `decision_policy_versions`, `agent_config_versions`,
`decision_agents`, `rating_snapshots`, `odds_snapshots`, `match_versions`,
`match_source_ids`, `match_version_sources`, `match_result_observations`,
`match_stat_observations`, `player_source_ids`, `identity_resolution_events`,
`identity_resolution_candidates`, `tournament_source_ids`, `matches`, and
`data_health_checks` become mechanically append-only at the database level.

## D031

`prediction_runs` and `ingest_runs` remain controlled lifecycle rows rather than generic
append-only rows. For `prediction_runs`, identity fields become immutable immediately after
insert, the only state-changing `RUNNING -> RUNNING` update is
`prediction_finalized_at: NULL -> timestamp` while `ended_at` stays `NULL`,
`prediction_finalized_at` may move only once from `NULL` to a timestamp, `ended_at` may
move only once from `NULL` to a timestamp on the terminal transition, and terminal runs
never reopen or rewrite into another terminal state. For `ingest_runs`, source identity
fields become immutable immediately after insert, T-ED2 permits only one terminal
`RUNNING -> COMPLETED|PARTIAL|FAILED|CANCELLED` update that sets `finished_at`, final
counters, and final `error_summary`, and state-changing `RUNNING -> RUNNING` updates are
rejected because the current repository does not incrementally mutate ingest rows before
terminalization; terminal ingest rows then freeze.

## D032

`decisions` remains historical truth. T-ED2 therefore rejects decision rewrites and deletes,
but permits one narrow `published_at` transition from `NULL` to a timestamp exactly once.
Full publication-state expansion, retractions, and operator-facing publication lifecycle
remain T-ED4 work.

## D033

T-ED2 does not add a hidden decision-completion flag merely to freeze `decision_agents`
timing. Inserted `decision_agents` rows become immutable, but late-insert closure for a
previously incomplete decision is deferred until a later phase defines an explicit
decision-finalization contract. Publication cannot serve as the universal closure boundary
because `NO_BET` and `VETOED` decisions may remain unpublished. Until that future
decision-lifecycle or T-ED4-adjacent design exists, downstream features must not assume
decision-agent membership is timing-closed, and an unused `(decision_id, agent_code)` slot
may still be inserted later. Settlement and CLV append-only redesign remain T-ED3 work.

## D034

Migration `0005_append_only_enforcement_and_lock_guards` is the implementation vehicle for
the locked T-ED2 contract. It changes the prediction finalization FK to
`DEFERRABLE INITIALLY IMMEDIATE`, adds PostgreSQL trigger-based append-only enforcement and
lifecycle guards, preserves the committed-state rule
`predictions.prediction_locked_at = prediction_runs.prediction_finalized_at`, and adds no
new tables, columns, or indexes.
## D035

The legacy Tennis Manager policy remains immutable replay truth as `tennis_manager_consensus/v1`. The target production policy is versioned as `v2` and requires all three predictive agents to be active. An Agent 3 abstention may never be silently reinterpreted as a v2 consensus.

## D036

The Tennis provider migration is an atomic authority change: `API_TENNIS + LEGACY_SPLIT` may switch only to `SPORTRADAR_V3 + GOALSERVE_TENNIS` as a pair. A partial data-only or odds-only cutover is invalid and must fail closed.

## D037

Cross-provider player, tournament, match, and odds identities must reuse the canonical identity universe. Stable Sportradar or Goalserve source IDs may be attached only after deterministic reconciliation. Ambiguous player identity, multiple plausible match candidates, or a source-ID conflict must not be resolved by fuzzy choice or duplicate canonical creation.

## D038

The verified API-Tennis Agent 2 historical regime is immutable evidence. Sportradar terminal results and final statistics are appended prospectively at their actual receive time. Provider migration must not backdate observations, rewrite old source truth, or recompute historical Manager v1 rows as though the target provider/policy had existed earlier.

