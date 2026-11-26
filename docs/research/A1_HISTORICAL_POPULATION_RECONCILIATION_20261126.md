# Agent 1 historical population reconciliation — 2026-11-26

**Status:** research only. `can_promote=false`, no production change, no migration,
no deployment, no provider call, no expenditure and no new training. The historical
population is **development-reused**, not untouched validation.

**PR:** #388 · **PR #392 reconciled head:** `af6e09be` · **Merge commit:**
`2a44627`

## Verdict

The three numbers are not three measurements of one population. They are three
different settlement snapshots, three different ranking universes and, for the latest
experiment, a reconstructed baseline rather than the stored forecast. The identity
bridge's effect survives that reconciliation, but the historical result remains
explanatory evidence, not an activation result.

## Phase 1 — exact historical populations

| Experiment | Freeze | Settled | Original | Corrected | Brier A | Brier B | LogLoss A | LogLoss B |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| E1 earlier Agent 1 | 2026-11-24T08:10Z | 3152 | 63.17% | 65.74% | 0.2293 | 0.2132 | 0.6553 | 0.6143 |
| E2 earlier Model Picks | 2026-11-24T09:00Z | 3201 | 68.36% picks | 70.29% picks | — | — | — | — |
| E3 latest Agent 1 | 2026-11-25T07:20Z | 3223 | 62.77% | 65.65% | 0.2304 | 0.2137 | 0.6575 | 0.6153 |

E1 is a genuine earlier settlement snapshot: the 52-result ingestion at
2026-11-24T08:14Z contained 49 COMPLETED, 2 CANCELLED and 1 RETIRED observation,
so it settled exactly 49 of the matches that E2 sees. This is why
E1 is 3,152 and E2 is 3,201, and it is not a model, eligibility or variant difference.

The quoted E1 63.17%/65.74% uses the historical `p > 0.5` rule. Under the current
`p >= 0.5` rule the same snapshot is 63.13%/65.70%;
the one-win difference at both variants is a single 0.5 tie (match 134226, winner
side 2). That tie is now explicit rather than hidden in the metric convention.

| Set reconciliation (prediction targets) | Count |
|---|---:|
| E2 targets | 3,725 |
| E3 targets | 3,776 |
| Target intersection | 3,627 |
| Targets added by E3 | 149 |
| Targets removed by E3 | 98 |
| Settled intersection | 3,172 |
| Settled added by E3 | 51 |
| Settled removed by E3 | 29 |
| Net E1 → E3 | 71 |

| Set reconciliation (fixtures, match_id) | Count |
|---|---:|
| Settled matches E1 / E2 / E3 | 3,152 / 3,201 / 3,223 |
| Added E1→E2 / removed | 49 / 0 |
| Added E2→E3 / removed | 22 / 0 |
| Added E1→E3 / removed | 71 / 0 |
| Target matches E2→E3 added / removed | 51 / 0 |

At **fixture level** the populations are nested: E1 ⊂ E2 ⊂ E3, +49 then +22,
no removals. At **prediction-target level** they are not nested: E3 adds
51 and removes 29 because
re-issued matches move the selected `prediction_id`/`run_id` while the fixture stays
the same. The net +71 from E1 to E3 is therefore 49 newly settled fixtures plus 22
newly settled fixtures — not 71 new model targets and not a homogeneous new cohort.

### Versions and baseline provenance

- Stored Agent 1 model/feature/config: `agent1_surface_elo_v1` /
  `agent1_surface_elo_inputs_v1` / `agent1_surface_elo_config_v1`; calibration version is NULL.
- E1/E2 original probabilities are **stored** `predictions.win_probability`.
- E3 A is a **reconstructed unbridged replay**, not the stored forecast (92.89% exact
  reproduction on the same population). E3 B is a reconstructed bridged replay.
- E1/E2/E3 were generated under pre-v2, permissive-equivalent resolver semantics. The
  branch now defaults to `identity_reference_pit_v3`, which has **not** been re-measured
  on production because 0025 is not applied.
- Research code: E1/E2 `853a3a3`; E3 `9f68cea`; branch head before this reconciliation
  `2a6053a5`; PR #392 head `af6e09be`.

## Phase 2 — decomposition of the 59 additional top-800 winners

The latest experiment's top-800 by Agent 1 confidence is a different ranking universe
from the earlier Model Picks equal-volume top-800. The 59 is the **within-E3** A→B
difference, not the cross-experiment difference.

| Group | Selections | Wins | Losses |
|---|---:|---:|---:|
| Shared A∩B | 509 | A 413 / B 413 | — |
| Original-only (A top, not B top) | 291 | 183 | 108 |
| Bridge-only (B top, not A top) | 291 | 242 | 49 |
| **A top-800 total** | 800 | **596** | 204 |
| **B top-800 total** | 800 | **655** | 145 |

**Accounting identity:** `655 = 413 + 242; 596 = 413 + 183; difference = 59`. The difference is exactly
**59**, reconciling to the reported 59 additional correct selections.

| Group detail | n | Mean conf A | Mean conf B | Δconf | Mean matches A | Mean matches B | Any bridged target player |
|---|---:|---:|---:|---:|---:|---:|---:|
| Shared | 509 | 0.8577 | 0.8282 | -0.0295 | 178.8222 | 209.4244 | 507 |
| Original-only | 291 | 0.8235 | 0.6529 | -0.1706 | 159.4794 | 194.9416 | 291 |
| Bridge-only | 291 | 0.6862 | 0.7874 | 0.1012 | 128.3041 | 156.2474 | 277 |

Tie handling at the top-800 boundary (descending confidence; stable chronological
order, so a tie is not randomly broken):

| Ranking | Boundary confidence | Selected at boundary | Eligible at boundary |
|---|---:|---:|---:|
| E3 A | 0.769532 | 1 | 1 |
| E3 B | 0.736089 | 1 | 1 |
| E2 original picks | 0.669598 | 1 | 1 |
| E2 bridged picks | 0.655820 | 1 | 1 |

- Shared selections with identical predicted winner: 509.
- Shared selections with changed predicted winner: 0.
- Probability-only changes inside the shared same-side set: 495.
- Rank correlation over all settled targets: 0.6040.
- Rank displacement over the union of both top-800 sets: median 405, mean 635.8, max 3,098 places.

Exact fixture sets are in the machine-readable companion:
`docs/research/evidence/a1_top800_sets_20261126.json` (800 A rows, 800 B rows,
291 original-only and 291 bridge-only prediction ids).

## Phase 3 — complete paired evidence (3,223 settled targets)

| Outcome | Count |
|---|---:|
| Both correct | 1,796 |
| Both incorrect | 880 |
| A correct, B incorrect | 227 |
| A incorrect, B correct | 320 |

Exact McNemar: **p = 0.000081** on
547 discordant pairs.

| Metric | A (unbridged) | B (bridged) | Improvement | Relative |
|---|---:|---:|---:|---:|
| Accuracy | 62.77% | 65.65% | 2.89% pp | — |
| Brier | 0.2304 | 0.2137 | 0.0166 | 7.21% |
| Log loss | 0.6575 | 0.6153 | 0.0422 | 6.42% |

Paired day-block bootstrap (1,000 reps):
- Brier B−A: [-0.02064, -0.01268].
- Log loss B−A: [-0.05233, -0.03286].
- Accuracy B−A: [1.41%, 4.55%].

| Tour | n | A accuracy | B accuracy | Brier A→B |
|---|---:|---:|---:|---:|
| ATP | 577 | 67.59% | 69.84% | 0.2093 → 0.1969 |
| CHALLENGER | 2,646 | 61.72% | 64.74% | 0.2349 → 0.2174 |

Classification: **historical development reuse**, not untouched validation. These
outcomes have been inspected repeatedly; the paired interval is descriptive.

## Phase 4 — mechanism

- Affected E3 forecasts: **3,150**; side flips: **547**.
- Target slots: 6,446; bridged target slots: 5,652;
  distinct original/model players: 1,089 / 1,085.
- Affected with a changed target id: 3,090; unaffected target id: 60.
- Recovered target-player historical matches across all settled targets: **203,682**.
- Direct identity recovery (a target player was bridged and recovered history):
  n=3,078, mean |Δp|=0.0924, recovered matches=203,682, mean |ΔElo|=76.7095.
- Indirect-only changes (target ids unchanged; propagation through the opponent ledger):
  n=72, mean |Δp|=0.0495, mean |ΔElo|=37.4216.

The bridge rewrites the **history** player IDs; the target's own model id is resolved
separately at each `as_of`. A target whose own historical rows were disconnected is
direct recovery; a target whose opponents' ratings moved is indirect. The ledger
records both. Newcomers with zero matches on both sides, ITF transitions and
limited-surface histories are reported only where the evidence separates them.

- ITF: UNRESOLVED: no ITF results are ingested; ITF transition and genuine debut are a union, not separable.

## Phase 5 — cross-experiment explanation and provenance

| Top-800 comparison | Wins | Accuracy |
|---|---:|---:|
| E2 Model Picks original | 575 | 71.88% |
| E2 Model Picks bridged | 594 | 74.25% |
| E3 A (all A1 targets) | 596 | 74.50% |
| E3 B (all A1 targets) | 655 | 81.88% |

The earlier/latest top-800 difference is decomposed by re-ranking:

| Re-ranking | Wins | Accuracy |
|---|---:|---:|
| E2 all targets, stored, top 800 | 597 | 74.62% |
| E2 all targets, bridged, top 800 | 653 | 81.62% |
| E3 Model Pick rule, A, top 800 | 575 | 71.88% |
| E3 Model Pick rule, B, top 800 | 593 | 74.12% |
| E3 on E2 settled intersection, A, top 800 | 596 | 74.50% |
| E3 on E2 settled intersection, B, top 800 | 651 | 81.38% |

Overlap between E2 original and E3 A top-800: 460 prediction ids;
468 fixtures. Between E2 bridged and E3 B:
433 prediction ids; 440 fixtures.
The remaining difference is the ranking universe (Model Picks only vs all A1
targets), the +22 settled targets, the PIT-vs-runtime target identity policy and
the stored-vs-replayed baseline. It is not one homogeneous uplift.

Under the **same Model Pick rule**, E3 B gives 593 wins
versus E2's 594 (a one-win difference); E3 A gives
575 versus E2's 575.
The 655/596 figures are Agent 1-only rankings, not deployable Model Picks. That
is precisely why the earlier 575/594 and latest 596/655 are not like-for-like.

## Provenance and quarantine

- PR #392 head `af6e09bef019ef3a5cc67273ad08094ee0b8a8a0` was merged without conflicts;
  both workers' migration and evidence files are present.
- Migration chain is contiguous through `0023` (Manager C feature snapshots), `0024`
  (OddsPapi identity history) and `0025` (identity provenance). `0025` remains
  **unapplied** to production.
- Exact runtime identity persistence is now transactionally integrated with prediction
  persistence behind the opt-in `persist_identity_resolutions` field; default is off.
- 52 run-2548 re-issued targets remain quarantined. 17 run-2535 pre-outcome records and
  2 run-2603 first-issued records are **not independent wall-clock receipts**.
  Admissible independent receipts: **0**.
- Historical overlap (they may be counted only as retrospective fixtures, never as
  forward receipts): run-2548 has 14 of 52 in the E3 settled population; run-2535
  has 5 of 17; run-2603 has 0 of 2.
- The retired legacy ~14,000-match / ~75% claim remains `UNREPRODUCIBLE` / `RETIRE`.

## Validation against the frozen evidence

| Check | Result |
|---|---|
| E1_settled_is_3152 | PASS |
| E2_settled_is_3201 | PASS |
| E3_settled_is_3223 | PASS |
| E2_top800_original_is_575 | PASS |
| E2_top800_bridged_is_594 | PASS |
| E3_top800_A_is_596 | PASS |
| E3_top800_B_is_655 | PASS |
| top800_identity_holds | PASS |

## Remaining blockers

- Reference-policy v3 (0025) not applied to production and therefore not re-measured.
- No independent wall-clock forward receipt; 52 re-issued targets remain quarantined and 17 pre-outcome records are not independent receipts.
- The historical population is development-reused, not untouched validation.

## Freeze recommendation

**Not ready for an activation freeze.** The historical result is now explained and
reconciled, but the prospective gates remain unmet: 2/200 settled first-issued
selections, 2/30 days, no independent receipt, no forward ATP sample, no re-measured
v3 policy and no owner approval. If the question is whether the *explanation* is ready
for review, the answer is yes; if it is whether the *candidate* is ready to freeze,
the answer is no.
