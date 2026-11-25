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

All contrasts are **candidate minus baseline** on the same fixtures.

- Brier and log loss are lower-is-better. An improvement means the point estimate
  of `candidate − baseline` is **negative**, and its paired 95% interval must lie
  **entirely below zero**.
- Winner accuracy is higher-is-better. An improvement means
  `candidate − baseline` is **positive**, and its paired 95% interval should lie
  entirely above zero (or at minimum its lower bound should be reported honestly).

All of the following must hold on the frozen forward sample:

1. `Brier(bridged) − Brier(original) < 0` with a paired 95% interval entirely below zero.
2. `LogLoss(bridged) − LogLoss(original) < 0` with a paired 95% interval entirely below zero.
3. Bridged A1 winner accuracy not worse than original A1; preferred point estimate at
   least +1.0 percentage point.
4. Model Picks winner accuracy not worse than production at **equal selection count** and,
   separately, at a frozen eligible-slate coverage. Report overlap and selection changes.
5. No material degradation in ATP; if ATP is too small, report it as unresolved rather than
   pooling it away.
6. Owner approval and a new Agent 1 configuration version. Do not reuse `agent1_surface_elo_v1`.

### Stop rules

Stop and report a negative result if the bridged replay fails to reproduce stored A1 on no-bridge
fixtures, if any future capture is written after the scheduled start, if an outcome enters the
capture record, or if the forward sample fails any gate. Keep the negative result.

## 4. Operational runbook

```powershell
$env:PYTHONPATH = "src"
python -m sports_edge_tennis.validation.a1_identity_bridge_forward_shadow `
  --run-id <completed_prediction_run_id> `
  --require-first-issue `
  --archive-jsonl <operator-owned-append-only-path>
```

The module sets its own session read-only. It can be run from a workstation against the public
Railway Postgres proxy or from a one-off read-only Railway service. It does not require a new
provider call and does not touch the production prediction path.

### Verified captures

Run `2548` (2026-11-24, captured at 08:14Z before any outcome):

- 52 future A1 targets, 0 failures, 14 side changes.
- **0 of 52 were earliest-issued.** These are later re-issues, not first-issue evidence.
- All 52 were Challenger main draw; 0 ATP, 0 qualifying.
- Quarantine: not admissible to the forward first-issue sample.

Run `2535` (2026-11-23 23:01Z issue, captured 2026-11-24 08:20Z before start):

- 17 first-issued future A1 targets, 29 non-first targets filtered.
- All Challenger main draw; 6 side changes.
- `prior_a1_prediction_count = 0` for every record.
- Record-set SHA-256 `e00e5a5effb0e26b4c3a0203233a4a5601a68cb2470c2a6777092fb60686c93a`.
- This is a genuine pre-outcome first-issue capture, but **not an independent wall-clock
  collector receipt** because it was run nine hours after issue. It must not be used as the
  central archive proof.

### Equal-coverage retrospective comparison

Same retrospective harness, settled targets only. Model Picks change because the bridge
changes confidence and therefore which matches clear the existing 60% rule.

- Original: 1,223 picks, 836W–387L (68.36%).
- Bridged: 1,168 picks, 821W–347L (70.29%).
- Overlap: 1,024 matches, **all same side** (0 side flips on overlap).
- Original-only: 199 picks, 106W–93L (53.27%).
- Bridged-only: 144 picks, 91W–53L (63.19%).
- Winners added by bridge: 91. Winners removed: 106. Losses avoided: 93. Losses introduced: 53.
- Net: 55 fewer settled picks, 15 fewer wins, 40 fewer losses.

At equal selection counts (top confidence per model):

| N | Original | Bridged | ATP orig→bridge | CH orig→bridge | Overlap |
|---:|---:|---:|---:|---:|---:|
| 400 | 75.25% | **79.75%** | 84.78→85.42% | 74.01→78.98% | 306 |
| 600 | 73.17% | **76.17%** | 77.78→83.10% | 72.54→75.24% | 470 |
| 800 | 71.88% | **74.25%** | 78.35→78.95% | 70.98→73.62% | 644 |
| 1,000 | 69.80% | **71.50%** | 75.21→73.98% | 69.08→71.15% | 829 |
| 1,168 | 68.66% | **70.29%** | 75.00→73.72% | 67.86→69.84% | 992 |

The equal-count gain is concentrated in Challenger. ATP is flat-to-slightly-worse at the
larger counts. These are different matches at the same count, not paired head-to-head
selections; the overlap column shows that only 306–992 of the picks are shared.

A two-proportion 95% interval for `bridged − original` accuracy is:

| N | Difference | 95% CI |
|---:|---:|---:|
| 400 | +4.50 pp | [−1.29, +10.26] |
| 600 | +3.00 pp | [−1.92, +7.90] |
| 800 | +2.38 pp | [−1.97, +6.71] |
| 1,000 | +1.70 pp | [−2.29, +5.68] |
| 1,168 | +1.63 pp | [−2.11, +5.35] |

Every interval includes zero. The retrospective bridge is **promising but not statistically
established** at equal coverage. On the 1,024 shared picks the two models are identical
(730W–294L), so the entire apparent gain comes from dropping some original picks and adding
different picks, not from better probabilities on the same matches.

### Replay-fidelity diagnosis

The previously reported 91.8% “no-bridge reproduction” is **not** a first-issue reproduction.
On 3,725 targets used by the reviewed harness:

- **0 of 3,725 targets were the earliest A1 issue.** The harness selects the latest
  prediction per match, so 91.8% is re-issue fidelity.
- Missing surface: 571 targets, 73.6% exact. Known surface: 3,154 targets, **95.1% exact**
  (96.1% within 0.005).
- 306 of 3,725 (8.2%) differ by more than 1e-6. Roughly half are the unknown-surface cohort.
- Only 94 targets had player-ID mappings that differed between the original `as_of` and today;
  those were 79.8% exact.

Conclusion: the bridge uplift must be described as a **retrospective counterfactual on
re-issued predictions**, with known-surface fidelity around 95%, not as an exact
reproduction of original first-issued production probabilities.

## 5. Limitations

- The resolver now gates alias and source rows on the ingest run's visibility
  (`COALESCE(ingest_runs.finished_at, ingest_runs.started_at, created_at) <= as_of`),
  aliases additionally on `first_observed_at <= as_of`. Historical result evidence stays on
  `source_observed_at/as_of` because it is existence evidence, not the alias mapping itself.
  The resolver records resolution method, candidate count, matched alias and bridge event id.
  A residual risk remains for aliases whose values were updated in place after the cutoff;
  `updated_at` is not yet used as a visibility gate.
- The observer records whether the captured prediction is the earliest A1 issue, but a late
  post-hoc run of an earlier prediction is still not an independent wall-clock collector receipt.
  Only captures made proximate to issue should enter the central forward archive.
- The bridge changes only Agent 1 history. It does not bridge A2/A3 history, Manager policy or odds.
- Bridged probabilities can change sides. That is the point of the experiment, not a defect, but it
  means Model Picks coverage can move.
- No licensed historical serve/return corpus is being introduced. Sackmann-derived training data
  remains licensing-unresolved and is not used by this observer.
- The 2026-11-24 shadow remains retrospective. Only the forward records can support activation.

## 6. Licensing and provenance

- The observer reads existing production PostgreSQL rows only. It makes no provider calls and
  introduces no new training data.
- The historical rating ledger used by Agent 1 is the existing production corpus. Its historical
  source is predominantly Sackmann-associated; licensing for any new commercial training use is
  **not verified**. This observer does not fit new parameters on that corpus.
- The live fixtures and results used for grading are API-Tennis production observations already
  present in the canonical database.
- The new resolver provenance records which alias, persisted bridge or historical identity produced
  each mapping, so a later audit can distinguish source-backed identity from name matching.
- Do not use the retrospective bridge uplift as a commercial-performance claim.

## 7. Archival and settlement contract

- `--archive-jsonl PATH` appends canonical JSON lines with `O_APPEND` and `fsync`. The file is never
  truncated. Each capture record has `record_type=capture`, `settlement_key`, `record_sha256`,
  `grading_contract`, `outcome_included=false` and `production_changed=false`.
- The final line is a `record_type=summary` object containing `record_set_sha256`, scope counts,
  first-issue counts and archive byte/size attestation.
- Grading joins on `(match_id, prediction_id)` and must use the dedicated settlement path, not the
  capture process. A capture is admissible only if `is_earliest_issued=true`,
  `main_draw_eligible=true`, the archive receipt is externally timestamped, and the match had not
  started at capture.
- Local `tmp/` archives in this review are **not** the central archive. They are reproduction
  artifacts.

## 8. Reproduction

- Existing retrospective shadow: `ops/validation/a1_identity_bridge_shadow_20261015.py`.
- New forward observer:
  `src/sports_edge_tennis/validation/a1_identity_bridge_forward_shadow.py`.
- Tests: `tests/test_a1_identity_bridge_forward_shadow.py`.
- Optional loader guard: `src/sports_edge_tennis/data/agent1_history.py` (`ingested_by`).
- Current PostgreSQL foundation CI is the required gate before this branch is considered reviewable.

## 9. 2026-11-25 empirical extension

### 9.1 Remaining PIT weaknesses

Implemented:

- Alias and source mappings are gated by the ingest run's visibility
  (`COALESCE(finished_at, started_at, created_at) <= as_of`) and by
  `player_aliases.first_observed_at <= as_of`.
- `IdentityResolution.visible_from` records when the mapping first became legitimately visible.
- The forward capture records `visible_from` for both player slots.

Measured:

- 16,104 A1 history rows move under the live→history bridge, involving 1,260 live players.
- Only **48 of 16,104 moved rows (0.30%)** have a mapping whose `visible_from` is later than the
  history observation time. Reverting those 48 late mappings changes Brier by **−0.000034 to
  +0.000081** and accuracy by **0.0 to +0.10pp**; the bridge result is not driven by late mappings.
- 1,259 moved players resolve by `TENNIS_DATA` alias; 1 by persisted bridge event.

Still blocked / not claimed:

- `player_aliases.updated_at` cannot distinguish a value change from a last-observed refresh.
  Using it would fail closed on aliases observed after the cutoff. A versioned alias history table
  is required for exact in-place-change PIT.
- `players.canonical_name` is current-state. There is no player-name history table.
- The exact production request player IDs were never persisted. The experiment resolves target IDs
  at each original `as_of`; this reproduces 92.9% of stored A1 probabilities exactly, not 100%.

### 9.2 Unknown-surface targets

- 579 of 3,776 targets have no surface on the selected PIT match version.
- Production-style surface resolution resolves **209/579** (93 current edition, 19 historical
  tournament, 97 normalized historical tournament).
- Strict-PIT resolution (live version, same-match prior version, PIT edition, or PIT tournament-id
  history) supports only **66/579**.
- 60 targets have an edition surface that is visible only in the current table state, not at the
  original cutoff. They are not used as PIT corrections.
- 513/579 remain unresolved under strict PIT.

This explains a substantial part of the replay mismatch: the old replay used the raw match-version
surface, while production used its broader resolver. It also means most unknown-surface matches
cannot be corrected without additional point-in-time surface history.

### 9.3 Four-way comparison

Population: 3,776 latest A1 targets since 2026-09-10; 3,223 safely completed. Target identities are
resolved at each original `as_of`. History bridging is the current bridge state; the 48 late mappings
are reverted in variant F. A and C use unbridged history; B, D, E and F use bridged history.
A and B use production-style surface; C, D and E use strict-PIT surface.

| Variant | Fidelity exact | Accuracy | Brier | Log loss | ATP acc | CH acc |
|---|---:|---:|---:|---:|---:|---:|
| A original identities + production surface | 92.89% | 62.77% | 0.23035 | 0.65754 | 67.59% | 61.72% |
| B bridged identities + production surface | 2.26% | **65.65%** | **0.21373** | **0.61531** | 69.84% | 64.74% |
| C original identities + strict surface | 92.43% | 62.86% | 0.23022 | 0.65732 | 67.59% | 61.83% |
| D both corrections | 2.26% | 65.56% | 0.21346 | 0.61474 | 69.84% | 64.63% |
| E D + dedupe | 2.26% | 65.56% | 0.21341 | 0.61454 | 69.67% | 64.66% |
| F B with 48 late mappings reverted | 2.26% | 65.68% | 0.21371 | 0.61527 | 69.84% | 64.75% |

Paired day-cluster bootstrap (1,000 reps):

- A→B Brier improvement **0.01262–0.02063**; log-loss **0.03284–0.05219**; accuracy
  **+1.41 to +4.55pp**. All intervals exclude zero.
- A→C Brier **−0.000073 to +0.000340**; accuracy **−0.09 to +0.27pp**. Not significant.
- B→D Brier **−0.000208 to +0.000787**; accuracy **−0.36 to +0.13pp**. Not significant.
- D→E Brier **−0.000268 to +0.000324**; accuracy **−0.25 to +0.26pp**. Not significant.
- B→F Brier **−0.000034 to +0.000081**; accuracy **0.0 to +0.10pp**. Not significant.

Conclusion: the **identity bridge** drives the gain. Strict-PIT surface correction and
cross-provider dedupe are operationally real but too small to establish from this sample. The
retrospective bridge uplift is not a surface artefact.

Equal-volume top-confidence selections (A1 only):

| N | A | B | C | D | E |
|---:|---:|---:|---:|---:|---:|
| 400 | 77.00% | 87.00% | 77.00% | 86.75% | 87.25% |
| 600 | 74.67% | 84.67% | 74.67% | 84.17% | 84.33% |
| 800 | 74.50% | 81.87% | 74.50% | 82.00% | 82.25% |
| 1,000 | 73.60% | 79.80% | 73.50% | 80.00% | 80.00% |

These are different matches selected at the same volume, not paired head-to-head. The bridged
ranking is much more concentrated, so the apparent lift at high confidence is not proof of a
75% deployable system.

### 9.4 52-target quarantine classification

The 52 run-2548 targets are all Challenger main draw and **none is earliest-issued**. As of
2026-11-25 only 14 have safely completed.

- Original A1: 7/14 (50.0%). Bridged A1: 9/14 (64.3%).
- Original Manager: 9 consensus, 43 no-bet; 4/7 settled consensus picks correct.
- Counterfactual Manager (A1 replaced by bridged A1): 11 consensus, 41 no-bet; 3/7 settled
  consensus picks correct.
- 17/52 Manager decisions changed; 14/52 A1 sides changed.
- Reasons shift mainly between directional disagreement and A3-abstention spread.

This is a quarantined descriptive population, not forward evidence. It is explicitly excluded from
the activation sample.

### 9.5 Historical walk-forward feasibility (January / hard / inactivity)

- Sackmann-associated results exist for January in every season, e.g. 1,002 matches in Jan-2025
  (868 hard), 1,154 in Jan-2024 (968 hard), 1,118 in Jan-2023 (892 hard).
- Those rows were ingested retrospectively on 2026-08-31 and are licensing-unverified. They cannot
  be used for commercial model training or presented as original January forecasts.
- API-Tennis has 1,322 January-2026 fixtures and 1,247 completed results, but every result was
  observed on 2026-09-13, not at the January match time. January-2026 is therefore not a PIT
  walk-forward season.
- Licensed API-Tennis PIT results currently cover only 2026-09-13 onward (~72 days). That supports
  inactivity studies inside the window but not a previous January season.
- A lawful January walk-forward needs either (a) a licensed historical results/stat feed with
  original observation timestamps, or (b) a commercial licence for the Sackmann corpus plus a
  documented point-in-time acquisition trail. Neither is present.

