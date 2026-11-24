# Agent 1 identity-bridge forward shadow — pre-registration

**Status:** research only. No production Agent 1, Manager, Model Pick, odds, publication or settlement
change. `can_promote=false`.

**Date:** 2026-11-24
**Production branch observed:** `fix/tennis-aligned-five-day-rolling-20261028`
**Observed production deployment:** `a28af39b97ed529272d466d9d02e6cef603d6dd3` (2026-11-23 11:57Z)

## 1. Why this candidate

Agent 1 loads its historical rating ledger with **live player IDs unchanged** unless
`bridge_model_identities=True`. Live API-Tennis fixture IDs therefore do not always reach the
historical model namespace. The repository already contains the opt-in bridge and a read-only
shadow. Re-running that shadow on 2026-11-24 over the current production data produced:

| Agent 1, same 3,152 settled fixtures | Accuracy | Brier | Log loss |
|---|---:|---:|---:|
| Stored production A1 | 63.17% | 0.2293 | 0.6553 |
| Unbridged replay (control) | 63.10% | 0.2294 | 0.6556 |
| **Bridged history** | **65.74%** | **0.2132** | **0.6143** |
| Bridged + cross-provider dedupe | 65.74% | 0.2131 | 0.6141 |

Paired day-cluster bootstrap for Brier, bridged minus current: **−0.01618 [−0.02031, −0.01224]**.

Model Picks using the existing A1/A2 same-side, A3-abstained, mean ≥60% rule:

| Rule | Picks | Scored | W–L | Win |
|---|---:|---:|---:|---:|
| Production A1 | 1,359 | 1,223 | 836–387 | 68.4% |
| Bridged A1 shadow | 1,307 | 1,165 | 820–345 | 70.4% |
| Bridged + dedupe | 1,303 | 1,162 | 815–347 | 70.1% |

Harness reproduction of stored A1: 91.8% exact and 93.5% within 0.005 over 3,725 targets.

This is the only candidate tested in this review that improves **both** proper scores and winner
selection at the same time. Global probability shrinkage, Glicko-style uncertainty, time decay,
margin-weighted Elo, tour-separated rating pools and a Challenger debutant prior were also tested.
The only large apparent gains came from cold-start artifacts in a licensed API-only window; when the
same prior/decay ideas were applied to production A1 predictions, they **reduced** accuracy. Those
negative results must not be resurrected as a 72–75% claim.

## 2. Why the existing shadow is not yet proof

The 2026-11-24 shadow resolves live-to-history bridges using today's identity state and reuses a
period in which the defect was discovered. It is therefore **not** a forward test. The production
loader also filters result and match-version visibility by `as_of` and `source_observed_at` but not
by `ingested_at`, so a retrospective replay can see history that was ingested later.

Two changes make forward proof possible:

1. `load_agent1_history(..., ingested_by=...)` adds an optional ingestion-time guard. The default is
   `None`, so production input is byte-for-byte unchanged.
2. `validation.a1_identity_bridge_forward_shadow` reads an already-finalised production run, rebuilds
   history with `bridge_model_identities=True` **inside that run's own `as_of`**, and logs the
   original and bridged A1 probabilities. It never writes a prediction, decision, Manager result,
   Model Pick, odds value or outcome.

## 3. Pre-registered protocol

### Capture

- Capture every first-issued `AGENT_1_SURFACE_ELO` prediction from a completed, non-experiment
  production run that has at least 30 minutes before scheduled start.
- Use the run's `prediction_as_of` as the history cutoff and the `ingested_at` guard.
- Store only one record per `(prediction_run_id, match_id)`.
- Record the original A1 probability/side, the bridged A1 probability/side, model and feature
  versions, live and resolved player IDs, history row count and eligible-match count.
- Record **no outcome**. Hash each record and the whole capture set.
- Preserve the JSONL log in an external append-only archive; Railway logs alone are not durable
  evidence.

### Evaluation (after independent settlement)

- Join only authoritative `COMPLETED` results whose result visibility is after match start and within
  the frozen analysis cutoff. RETIRED, WALKOVER, DEFAULTED and CANCELLED remain ungraded.
- Evaluate A1 accuracy, Brier and log loss on **the same fixtures** for original and bridged A1.
- Report ATP and Challenger separately; Challenger will dominate the sample and must not be hidden.
- Report the paired day-cluster bootstrap for Brier and log loss.
- Report Model Picks accuracy and coverage using the unchanged Manager and publication rules.
- Report selection changes and their outcomes, but do not tune a threshold on them.
- Add player-order swap and shuffled-label negative controls to the grading harness before any
  promotion decision.

### Frozen minimum sample

Do not consider activation before **at least 200 settled bridged A1 selections** and **at least 30
calendar days** of genuine future captures. At the observed Challenger-dominated rate this is a
multi-week exercise, not a one-day result.

### Activation gate

All of the following must hold on the frozen forward sample:

1. Bridged A1 Brier improvement over original A1 with a paired 95% interval entirely above zero.
2. Bridged A1 log-loss improvement with a paired 95% interval entirely above zero.
3. Bridged A1 winner accuracy not worse than original A1; preferred point estimate at least +1.0
   percentage point.
4. Model Picks winner accuracy not worse than production at comparable coverage.
5. No material degradation in ATP; if ATP is too small, report it as unresolved rather than pooling
   it away.
6. Owner approval and a new Agent 1 configuration version. Do not reuse `agent1_surface_elo_v1`.

### Stop rules

Stop and report a negative result if the bridged replay fails to reproduce stored A1 on no-bridge
fixtures, if any future capture is written after the scheduled start, if an outcome enters the
capture record, or if the forward sample fails any gate. Keep the negative result.

## 4. Operational runbook

```powershell
$env:PYTHONPATH = "src"
python -m sports_edge_tennis.validation.a1_identity_bridge_forward_shadow `
  --run-id <completed_prediction_run_id>
```

The module sets its own session read-only. It can be run from a workstation against the public
Railway Postgres proxy or from a one-off read-only Railway service. It does not require a new
provider call and does not touch the production prediction path.

First operational capture (2026-11-24, run `2548`, before any outcome):

- 52 future A1 targets captured, 0 failures.
- 14 bridged selections differed from the stored original side.
- Record-set SHA-256 `f45bdf834f8cc17a1975934b700c409a158b4abb4331766cafef1fa090e51ef3`.
- This is a **capture exercise only**. It is not accuracy evidence and must not be described as one.

## 5. Limitations

- The bridge resolver may still use alias evidence that was ingested after `as_of` but has an earlier
  `as_of`. Forward capture reduces this to the short interval between prediction finalisation and
  observer execution; it does not eliminate it. A future revision should add ingestion-time filtering
  to the resolver itself.
- The bridge changes only Agent 1 history. It does not bridge A2/A3 history, Manager policy or odds.
- Bridged probabilities can change sides. That is the point of the experiment, not a defect, but it
  means Model Picks coverage can move.
- No licensed historical serve/return corpus is being introduced. Sackmann-derived training data
  remains licensing-unresolved and is not used by this observer.
- The 2026-11-24 shadow remains retrospective. Only the forward records can support activation.

## 6. Reproduction

- Existing retrospective shadow: `ops/validation/a1_identity_bridge_shadow_20261015.py`.
- New forward observer:
  `src/sports_edge_tennis/validation/a1_identity_bridge_forward_shadow.py`.
- Tests: `tests/test_a1_identity_bridge_forward_shadow.py`.
- Optional loader guard: `src/sports_edge_tennis/data/agent1_history.py` (`ingested_by`).
- Current PostgreSQL foundation CI is the required gate before this branch is considered reviewable.
