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

- The resolver gates alias and source rows on the ingest run's visibility
  (`COALESCE(ingest_runs.finished_at, ingest_runs.started_at, created_at) <= as_of`),
  aliases additionally on `first_observed_at <= as_of`, player names on
  `players.updated_at <= as_of` (or a never-updated row) and bridge events on
  `created_at <= as_of`. The resolver records resolution method, evidence, name source,
  candidate count, matched alias, bridge event id and a reference-state digest.
- Residual risk: `player_aliases` can be re-pointed out of band. The upsert key pins
  `player_id` and `normalized_alias`, so normal pipeline traffic cannot do it, but an
  operator `UPDATE` could. `updated_at` cannot distinguish that from a benign refresh.
  The reference digest makes such drift *detectable*, not preventable; the proposed
  immutability guard in `specs/identity_provenance_v2_20261125.md` is the fix.
- The reference fingerprint covers the captured players' closure. The history bridge
  (which reads the whole corpus) is not fingerprinted per capture; it is evidenced by
  row counts and the append-only `player_source_ids` ledger.
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
- Reference-data PIT and replay-mismatch audit:
  `src/sports_edge_tennis/validation/a1_identity_provenance_audit.py`.
- Manager interaction audit:
  `src/sports_edge_tennis/validation/a1_identity_manager_interaction.py`.
- Legacy 75% claim provenance audit:
  `src/sports_edge_tennis/validation/legacy_75pct_claim_audit.py`.
- Reference fingerprint / drift: `src/sports_edge_tennis/data/identity_provenance.py`.
- Reserved schema proposal: `specs/identity_provenance_v2_20261125.md`.
- Tests: `tests/test_a1_identity_bridge_forward_shadow.py`,
  `tests/test_a1_identity_provenance_audit.py`, `tests/test_model_identity_bulk_resolver.py`,
  `tests/test_replay_identity_pit.py`, `tests/test_live_model_player_history_bridge.py`.
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

## 10. 2026-11-25 December mission — identity bridge completion

This section records the December mission work. It is research only: no production Agent 1,
Manager, Model Pick, odds, publication or settlement change. `can_promote=false`.

### 10.1 Reference-data PIT policy (`identity_reference_pit_v2`)

The resolver previously gated alias and source rows on ingest-run visibility but left three
mutable surfaces. They are now explicit:

| Surface | Mutability | Gate |
|---|---|---|
| `players.canonical_name` | updated in place | reused only when `updated_at <= as_of`, or when the row was never updated (`updated_at = created_at`) |
| `player_aliases` | `last_observed_at`/`updated_at` move on every re-observation | `created_at <= as_of` and `first_observed_at <= as_of`; `updated_at` is **not** a value-version marker because the upsert key pins `player_id` and `normalized_alias` |
| `model_identity_bridge_events` | append-only, but `activated_at` is caller-supplied | `activated_at <= as_of` **and** `evidence_as_of <= as_of` **and** `created_at <= as_of` |

Two parameters make the policy auditable rather than implicit:

- `reference_policy="permissive"` reproduces the pre-2026-11-25 semantics and exists only to
  measure the effect of each gate. It must never be used for an evidence claim.
- `alias_refresh_gate=True` adds the over-conservative `player_aliases.updated_at <= as_of`
  gate. Because every re-observation bumps `updated_at`, this discards legitimate PIT
  evidence; it is a measurement instrument, not a deployable policy.

The strict replay resolver (`replay/identity_pit.py`, `identity_pit_v2`) now shares the same
policy: a linkage row created after the cutoff is no longer admitted merely because
`first_observed_at` predates it. That earlier behaviour was a hindsight channel: the
*mapping decision* was still made after the cutoff.

### 10.2 Versioned identity provenance without a schema migration

Each capture now freezes the reference state it used:

- `identity_provenance.reference_fingerprint` hashes every PIT-visible `players`,
  `player_aliases`, `player_source_ids` and `model_identity_bridge_events` row in the target
  players' closure. `last_observed_at`/`updated_at` are excluded so benign refreshes do not
  create false drift; linkage columns and `canonical_name` are included.
- `resolve_with_provenance` returns the resolution together with that digest.
- `reference_drift_for` recomputes the digest later. An out-of-band alias re-pointing or
  player rename is therefore detectable even though the current schema cannot version it.

A migration is still required for exact reconstruction. `specs/identity_provenance_v2_20261125.md`
specifies `player_name_versions`, `player_alias_versions`, an identity-linkage immutability guard
on `player_aliases`, and `identity_resolution_provenance`. **No migration file is added by this
branch.** `0023` and `0024` are owned by integration PR #392; this proposal must be numbered after
`0024` by that train.

### 10.3 Runtime resolution persistence

Every forward capture record now stores, per player slot: original (live) id, model id, method,
evidence, name source, candidate count, matched alias, bridge event id, `visible_from`, the
observation time (the run's `prediction_as_of`) and the match version id. The capture summary
stores the reference digest and row counts. Schema version is
`a1_identity_bridge_forward_shadow_record_v2`.

### 10.4 Remaining original-replay mismatch

`docs/research/evidence/a1_identity_replay_mismatch_20261125.json`, full population of
3,780 latest Agent 1 targets, production-style history and surface resolution:

- Exact reproduction (≤1e-6): **92.78%**; within 0.005: **94.60%**; 273 mismatches (7.22%).

Decomposition of the 273 mismatches (buckets are mutually exclusive, strongest cause first):

| Mechanism | Targets | Mismatches | Rate | Share of mismatches |
|---|---:|---:|---:|---:|
| Target identity bridged (PIT id ≠ live id) | 3,138 | 139 | 4.43% | 50.9% |
| Surface fallback resolution | 209 | 95 | 45.45% | 34.8% |
| Unknown surface | 371 | 23 | 6.20% | 8.4% |
| Unexplained remainder | 62 | 16 | 25.81% | 5.9% |

Key conclusions:

- **The late-ingest hypothesis is falsified.** At the earliest, median and latest cutoffs,
  **zero** result observations visible to the replay had been ingested after that cutoff
  (117,713 / 132,673 / 134,713 visible rows, 0 late). The replay is not reading history that
  production could not have seen.
- **Identity resolution is the largest single share (~51%).** The exact production request
  player ids were never persisted, so a re-issue replay reconstructs them. Most bridged targets
  still reproduce exactly (95.6%); the mismatch is concentrated where the reconstructed id
  differs from whatever production used.
- **Surface fallback is the highest-*rate* mechanism (~35% of mismatches from only 5.5% of
  targets).** Where the live version has no surface and production fell back to an edition or
  historical tournament surface, the replay frequently does not reproduce the stored
  probability. That is a resolver-reconstruction gap, not a model change.
- Only 5.9% remains unexplained.

This is a re-issue reproduction, not first-issue fidelity. Closing the identity share requires
persisting the runtime resolution (section 10.3, and the durable table in the reserved schema),
not further threshold work.

### 10.5 Manager interaction on matched populations

`docs/research/evidence/a1_identity_manager_interaction_20261125.json` re-evaluates the stored
Manager on the same matches, changing only Agent 1. Population: 3,271 safely settled matches with
all three agents and a stored Manager result (507 matches excluded as not safely settled).

- Agent 1 side changed on **562** matches; the Manager decision changed on **861** (26.3%).
- Consensus picks: **546 → 631**. Original 349W–197L (63.9%); counterfactual 417W–214L (66.1%).
- Of 255 shared consensus picks, **all 255 keep the same side**. The accuracy difference is
  therefore caused by which matches clear the Manager, not by better probabilities on the same
  matches.
- Reason transitions are overwhelmingly the A3-abstention-spread rule (270 + 210) and
  directional disagreement (105 + 98 + 96 + 81), i.e. the bridge moves Agent 1 across the
  Manager's existing spread thresholds.
- ATP: 58 → 68 consensus, 70.7% → 69.1% (coverage up, accuracy slightly down).
  Challenger: 488 → 563 consensus, 63.1% → 65.7%.
- The 52-target run-2548 cohort remains quarantined and is not part of this population.

This is a coverage/selection interaction, not a paired improvement. Any activation decision must
use the frozen equal-count and frozen-coverage comparisons in section 3.

### 10.6 First-issued shadow capture (run 2603)

Captured 2026-11-25T09:01:09Z for a run issued at 08:05:05Z, with matches starting
2026-11-27T01:00:00Z.

- 2 first-issued targets, 0 failures, both Challenger main draw, 0 ATP, 0 qualifying.
- Both were the earliest Agent 1 issue for their match (`prior_a1_prediction_count = 0`).
- Agent 1 side changed on 1 of 2.
- Player resolutions: live 4714 → model 2103 (alias `imamura m`), live 4591 → model 3373
  (alias `sanchis d j`), live 4514 → model 758 (alias `uchida k`), live 5150 → itself
  (`NO_HISTORICAL_CANDIDATE`).
- Reference digest `991afee5ef45d4205a8e3833228b2c3813f34e046eb7f0b508a83c5f452612bd`
  over 4 players / 17 aliases / 12 source ids / 0 bridge events.
- Record-set SHA-256 `31f0ec4893593f6fb3cabaa14a074d925a53ba70a556bb222e647aaf5a893f60`.
- Archive: `docs/research/evidence/a1_identity_bridge_forward_capture_2603_20261125.jsonl`.

This is a genuine first-issued, pre-outcome capture made ~56 minutes after issue and before
scheduled start. It is still not an independent wall-clock collector receipt: the archive is
committed by hand. Run-2548's 52 targets remain quarantined; run-2535's 17 records remain
pre-outcome but not an independent receipt.

### 10.7 Reference-policy and replay-mismatch measurements

**Reference-data leakage (full population).** `a1_identity_reference_policy_20261125.json`,
3,778 targets (3,271 settled), 6,556 player resolutions per policy:

| Policy | Changed mappings vs permissive | Affected targets |
|---|---:|---:|
| `strict` | **0** | 0 |
| `strict_alias_refresh_gate` | **0** | 0 |

The strict gates are **mapping-neutral on the current corpus**. The bridge is not resolving
anything that depends on reference state created or mutated after the original cutoff:

- `players.updated_at = created_at` for all players, so the canonical-name gate never fires.
- The single persisted bridge event has `created_at` 10.6 ms after `activated_at`, far inside any
  cutoff boundary that matters.
- Every alias the bridge actually resolved through had not been refreshed after its cutoff, so
  even the over-conservative `updated_at` gate changes nothing.

This is the verification the mission asked for: **the identity correction cannot introduce
future information through mutable reference data on the observed corpus**, and the gates remain
in place to keep that true as the corpus evolves. It is a present-tense empirical result, not a
proof about future mutations; the residual out-of-band alias re-pointing risk stays open until
the immutability guard in `specs/identity_provenance_v2_20261125.md` lands.

**Residual replay mismatch.** `a1_identity_replay_mismatch_20261125.json`, 3,780 targets:
92.78% exact / 94.60% within 0.005; 273 mismatches. Zero result observations visible to the
replay were ingested after any cutoff, so late history is *not* a cause. Of the 273 mismatches,
50.9% are target-identity reconstruction, 34.8% surface-fallback reconstruction, 8.4% unknown
surface and 5.9% unexplained (full table in section 10.4).

### 10.8 Legacy "~14,000 at ~75%" claim — provenance verdict

`docs/research/evidence/legacy_75pct_claim_audit_20261125.json` (read-only, 2026-11-25):

- 134,383 stored matches; 129,045 safely `COMPLETED` results.
- 117,713 pre-2026 match-version rows exist, and **0** were ingested before their match start.
  Every pre-2026 version was ingested on **2026-08-31** (retrospective backfill).
- The only stored quantity near 14,000 is `trained_history_count = 14,229` in the 2026-11-23
  label-shuffle artifact over a 623-match holdout. That is a training-corpus row count, not a
  prediction sample, and is the most plausible origin of the number.
- The largest reproducible original-forecast cohort is 3,375 settled matches at **62.52%**
  accuracy, 12.48 percentage points below 75%.
- Required artifacts (immutable run manifest, exact fixture-id set, pre-match feature
  timestamps, post-start outcome join, commercial-use rights) are all absent.

**Verdict: `UNREPRODUCIBLE`. Formal recommendation: `RETIRE`.** The claim must not appear in
commercial, marketing, investor or model-quality contexts. It may only be cited as an
unreproducible legacy internal claim with this audit attached. Replace it with the reproducible
original-forecast cohort and the forward activation gate in section 3.

## 11. 2026-11-26 implementation completion

This section records the work that converted the remaining design items into implementation
and bounded evidence. It is still research only: `can_promote=false`, no production
migration, no activation and no deployment.

### 11.1 Migration train reconciliation

The branch adopts integration PR #392's `0023` (Manager C feature snapshots) and `0024`
(OddsPapi identity history) as parents, then allocates the next free version, `0025`, to
identity provenance. This removes the earlier manifest gap and makes the cumulative schema
snapshot confluent. `0025` is not applied to production.

### 11.2 Versioned reference data — implemented

`0025_identity_provenance_v2` adds:

- `player_name_versions` — append-only canonical-name history. Effective interval is
  derived from the next `valid_from`; there is no `valid_to` UPDATE. A rename writes a new
  version at `NEW.updated_at` with a real `recorded_at`, so a backdated rename is visible.
  Backfill uses `GREATEST(created_at, updated_at)`, so a pre-migration rename is never
  asserted to have existed from row creation. When no version is visible at a cutoff, a
  never-updated player row still supplies its current name, exactly as the pre-0025 policy
  allowed; a renamed row fails closed. The residual "row created after the cutoff but never
  updated" channel is unchanged and bounded by source-id and history visibility.
- `player_alias_versions` — append-only alias linkage history, written on insertion.
- `guard_player_alias_identity()` — rejects any link-column UPDATE on `player_aliases`.
  Normal pipeline re-observation still updates only `last_observed_at`/`updated_at`.
- `identity_resolution_provenance` — durable runtime record (section 11.3).

The bulk resolver and the replay resolver now read the version tables when they exist and
fall back to the legacy current-table gates on a pre-0025 schema. `identity_reference_pit_v3`
is the versioned policy; `alias_refresh_gate` deliberately forces the legacy path so the
residual mutable-alias channel stays measurable.

### 11.3 Exact runtime resolution persistence — implemented, inactive

`data/identity_runtime.py` writes and reads one row per player per resolution:

`prediction_run_id`, `match_id`, `match_version_id`, `predicted_at`, `original_player_id`,
`model_player_id`, `resolution_method`, `evidence`, `name_source`, `candidate_count`,
`matched_alias`, `bridge_event_id`, `visible_from`, `reference_policy`, `reference_sha256`,
`record_sha256`, `source_record_sha256`, `capture_source`.

Before insertion it verifies that the match version belongs to the match, that the version
timestamps and run `as_of` are not later than the observation time, and that `visible_from`
is not later than the observation. Re-persisting the same key with the same digest is a
no-op; a different digest fails closed as a lineage conflict.

The live runner passes no observations today, so production behaviour is unchanged.

### 11.4 First-issued capture integration — completed

- `a1_identity_bridge_forward_shadow` can now discover completed, non-experiment runs
  finalized since a watermark, in finalized order, and never re-appends a run already
  present in the archive.
- `validation.a1_identity_capture_integration` verifies a JSONL capture archive and, with
  `--apply` only, writes the same facts into `identity_resolution_provenance`. Default is a
  dry run.
- Run-2603's 2 first-issued records remain the only admissible capture. Run-2548's 52
  targets stay quarantined; run-2535's 17 pre-outcome records are not an independent
  receipt.

### 11.5 Shadow non-interference — verified locally

A regression test wraps the observer's connection in a statement guard that rejects any
non-read-only statement, runs the observer against a real local PostgreSQL run, and asserts
that `predictions`, `prediction_runs`, `decisions`, `manager_consensus_results`,
`publication_control_events` and `decision_settlements` are byte-for-byte unchanged. The
observer sets `default_transaction_read_only=on` and records `outcome_included=false` and
`production_changed=false`.

### 11.6 Reference-policy experiment — explicit forecasting effect

The 3,778-target census already showed **0** changed mappings for `strict` and
`strict_alias_refresh_gate` versus `permissive`. The evidence file now records the
forecasting consequence explicitly: `affected_targets = 0`, `changed_probabilities = 0`,
`max_abs_probability_delta = 0.0`, replay fidelity `n = 0` and no paired interval. Zero
mapping changes imply zero replayed probability changes, so this is exact, not a failed
measurement.

This result is under `identity_reference_pit_v2`. The version-table policy is v3 and must be
re-measured on production after 0025 is applied. Do not cite the v2 census as a v3 result.

### 11.7 Residual replay mismatch — lineage, not late data

The full-population audit is unchanged: 3,780 targets, 92.78% exact, 94.60% within 0.005,
273 mismatches, zero late-ingested result observations at every sampled cutoff.

A new bounded case audit of the 30 largest re-issue mismatches
(`a1_identity_replay_mismatch_cases_20261126.json`, derived from the 3,725-target fidelity
harness) shows:

- **30/30** changed target identity at the original cutoff; **0/30** were first-issued.
- **24/30** changed both players; **26/30** changed player 1.
- **18/30** flipped the modelled side; only **8/30** had a null surface.
- Absolute probability deltas: 0.188 / 0.283 / 0.451 (min / median / max).

The dominant mechanism is therefore reconstructed target identity — exactly the lineage gap
the durable runtime record closes for future runs. It is not evidence of a model change or
late history. The 5.9% unexplained remainder of the 3,780-target audit stays fail-closed and
is not attributed to a reconstructable value.

### 11.8 Manager interaction — bounded and statistically inconclusive

The existing 3,271-match comparison is unchanged: 546 → 631 consensus, 63.9% → 66.1%, all
255 shared picks keep the same side. The evidence now adds an explicitly **unpaired**
two-proportion interval: **+2.17 pp, 95% CI [−3.30, +7.63] pp**, which includes zero. ATP
coverage rises 58 → 68 while accuracy slips 70.7% → 69.1%; Challenger rises 488 → 563 and
63.1% → 65.7%. This is a coverage interaction, not a paired activation result.

### 11.9 Legacy claim and readiness

The ~14,000 / ~75% claim remains formally `UNREPRODUCIBLE` / `RETIRE`; the largest
reproducible original-forecast cohort is 3,375 settled at 62.52%.

`A1_IDENTITY_BRIDGE_PROSPECTIVE_READINESS_20261126.md` and its JSON companion record every
activation gate. The candidate is **NOT_ELIGIBLE**: 2/200 settled first-issued selections,
2/30 days, no independent receipt, no paired interval, no forward ATP sample, no new config
version and no owner approval.



## 12. 2026-11-26 historical population reconciliation

`A1_HISTORICAL_POPULATION_RECONCILIATION_20261126.md` and
`docs/research/evidence/a1_historical_reconciliation_20261126.json` reconcile the three
historical populations that were previously quoted without a common freeze.

- E1 (3,152) is the 2026-11-24T08:10Z settlement snapshot. E2 (3,201) adds exactly the
  49 COMPLETED results from the 08:14Z batch; the same batch also carried 2 CANCELLED and
  1 RETIRED observation.
- At fixture level E1 ⊂ E2 ⊂ E3, +49 then +22, no removals. At prediction-target level the
  sets are not nested: E3 adds 51 and removes 29 because re-issued matches move the selected
  `prediction_id`/`run_id` while the fixture is unchanged. The net +71 is therefore two
  settlement additions, not 71 new model targets.
- The earlier 575/594 top-800 result ranks **Model Picks** (A1/A2 same side, A3 abstained,
  mean ≥60%). The latest 596/655 result ranks **all Agent 1 targets by confidence**. They
  are different ranking universes, not a like-for-like 61-win improvement.
- Within the latest experiment the 59-win A→B difference decomposes into shared,
  original-only and bridge-only selections; the exact fixture sets and the accounting
  identity are in the evidence above.
- The historical population remains **development-reused**, not untouched validation.
  No production activation, migration or deployment is implied.
