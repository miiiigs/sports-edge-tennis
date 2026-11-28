# Agent 1 selective performance validation — accuracy, coverage and chronology

**Status:** research only. `can_promote=false`. No production Agent 1, Agent 2, Agent 3,
Manager, Model Pick, odds, publication or settlement change. No migration applied, no
deployment, no provider call, no expenditure and no new model.

**Date:** 2026-11-26
**Branch:** `research/a1-identity-bridge-forward-shadow-20261124`
**PR:** #388
**Reconciled base:** `607ea74a859af473d1295ddf72aee3d7c22fcb11` (remote head at fetch)
**Experiment code:** `ops/validation/a1_selective_performance_experiment_20261126.py`
**Module:** `src/sports_edge_tennis/validation/a1_selective_performance.py`
**Tests:** `tests/test_a1_selective_performance.py`
**Evidence:** `docs/research/evidence/a1_selective_performance_20261126.json`

## 0. Bottom line

On the exact **3,223 safely completed** Agent 1 targets frozen at 2026-11-25T07:20Z,
the corrected (identity-bridged) replay reaches:

| Coverage target | Exact selections | Correct | Accuracy | Wilson 95% | Brier | Log loss |
|---|---:|---:|---:|---|---:|---:|
| 40% | 1,289 | 1,000 | **77.58%** | 75.22–79.77% | 0.1692 | 0.5185 |
| 45% | 1,450 | 1,107 | **76.34%** | 74.09–78.46% | 0.1753 | 0.5319 |
| 50% | 1,611 | 1,197 | **74.30%** | 72.11–76.38% | 0.1833 | 0.5493 |

The original (unbridged) replay gives 72.46%, 71.66% and 70.45% at the same points.
The retrospective 72–75% target is therefore met by the corrected version at 40%, 45%
and 50% coverage, and the 75% target is met at 40% and 45% but **not** at 50%
(74.30%). The result is not a thin one at 40%/45%: the Wilson lower bounds are 75.2%
and 74.1%.

Two things prevent that sentence from becoming an activation claim:

1. **Market benchmark.** On the 1,593 settled fixtures with a legitimate pre-issue
   `MATCH_WINNER` quote, the market is stronger across the curve: 86.19% at 40%,
   84.50% at 45%, 81.91% at 50% and 70.94% at 100%, versus corrected 78.65%, 76.40%,
   75.00% and 67.42% on the same fixtures. The model is not beating the available
   market evidence.
2. **Coverage at true 50%.** The marginal 45%→50% slice is only 55.9% accurate.
   The 74.30% at exactly 50% is a cumulative average that is being diluted quickly.
   If the live policy drifts even modestly above 50% coverage, the 72% floor is at risk.

Chronological freezing supports the direction but does **not** reproduce 40–50%
coverage exactly. Thresholds frozen on data through 2026-10-31 and applied to the
untouched later period (2026-11-01..25) achieved 36.6%, 41.0% and 46.4% coverage with
78.40%, 76.43% and 74.16% accuracy. At the coverage actually achieved, the 72–75%
range survives. At a forced 50% coverage it would sit close to the 72% floor.

## 1. Population, provenance and what is being measured

### 1.1 The four quoted populations are snapshots, not four eligibility rules

| Quantity | Count | Definition |
|---|---:|---|
| Latest non-experiment Agent 1 targets, 2026-09-10..2026-11-25T07:20Z | 3,776 | one latest prediction per match |
| Safely completed at the E3 freeze | 3,223 | latest result `COMPLETED`, winner side 1/2, visible by 07:20Z |
| Settlement snapshot at the Manager run | 3,271 | same 3,776 targets; 48 later results (last 2026-11-25T08:13Z) |
| Settlement snapshot at 2026-11-26T04:03:46Z | 3,284 | same 3,776 targets; 13 further results |
| Target universe at 2026-11-25T11:00Z | 3,778 | net +2 fixtures; **53 prediction targets added and 51 removed** |
| Target universe at 2026-11-25T12:13Z | 3,780 | net +4 fixtures; 55 added / 51 removed versus 07:20Z |

The net counts hide re-issue churn. Between 07:20Z and 11:00Z the **fixture** set grows
by exactly two matches (134382, 134383), but the selected `prediction_id` set changes by
53 additions and 51 removals because re-issued matches replace their target row. The
identity bridge is evaluated on the frozen 3,223 settled rows, not on the growing
universe. The exact ids are in
`docs/research/evidence/a1_identity_integrity_20261126.json`.

### 1.2 The five-record provenance discrepancy

The 3,778-target reference-policy artifact reports 7,551 classified player resolutions
for 7,556 slots (3,778 × 2). That is five unclassified slots.

- The 3,776-target ledger used here is fully classified: **7,552 / 7,552**.
- The settled 3,223 population is fully classified: **6,446 / 6,446**.
- The 3,778 snapshot adds two fixtures (four slots): `7,552 + 4 − 5 = 7,551`.

So the five records are a **reporting gap in the 3,778 artifact**, not five missing
fixtures and not five missing settled forecasts. They do not enter the 3,223
evaluation. The next v3 census must emit one record per slot (including
`NO_PLAYER`/`NO_CANDIDATE`) instead of only a method aggregate; that is the closure
requirement, not a re-run of the performance experiment.

### 1.3 Pre-match integrity

21 of the 3,223 rows have `as_of >= match_start_at`. Twenty are clock/rounding
artefacts of 0–42 seconds; one (prediction 5656, match 117905) is 2,574 seconds
(42.9 minutes) after the listed start. Excluding all 21 gives a strict pre-match
population of 3,202:

| Coverage | Original accuracy | Corrected accuracy | Four-way A/B/C/D |
|---|---:|---:|---|
| 40% (1,280) | 72.34% | 77.42% | 595 / 588 / 644 / 654 |
| 45% (1,440) | 71.74% | 76.11% | |
| 50% (1,601) | 70.39% | 74.20% | |

The headline does not depend on the borderline rows. It should still be reported
because a live policy cannot issue after the start.

### 1.4 Classification

This is **corrected retrospective research on a development-reused population**. It is
not the original stored production forecast (E3 A is a reconstructed unbridged replay,
92.89% exact reproduction), not a strict point-in-time replay of every request identity,
and not prospective validation. The strict v3 policy is implemented but not applied to
production (`0025` is unapplied), so the 3,223 experiment was measured under the
pre-v2 permissive-equivalent resolver semantics.

## 2. Phase 1 — the full accuracy–coverage experiment

### 2.1 Deterministic rules

- Confidence: `max(p1, 1 − p1)`, rounded to 6 decimals (the model's probability quantum).
- Selection: top `floor(fraction × 3,223)` rows; at 100% all rows.
- Ranking: descending confidence, then ascending `prediction_id`, then `match_id`.
- Predicted side: player 1 when `p1 >= 0.5`.
- Exact selection counts: 322, 644, 805, 966, 1,128, 1,289, 1,450, 1,611, 2,417, 3,223.
- Equal-confidence boundary diagnostics are reported separately. The deterministic
  tie-break is a convention, not a claim that tied forecasts are distinguishable.

### 2.2 Original Agent 1

| Cov. | k | W | L | Acc. | Wilson 95% | Marginal acc. | Mean conf. | Exp. W | Obs. W | Brier | Log loss |
|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| 10% | 322 | 250 | 72 | 77.64% | 72.78–81.85% | 77.64% | 0.8997 | 289.7 | 250 | 0.1885 | 0.6008 |
| 20% | 644 | 484 | 160 | 75.16% | 71.68–78.34% | 72.67% | 0.8605 | 554.2 | 484 | 0.1982 | 0.6079 |
| 25% | 805 | 600 | 205 | 74.53% | 71.41–77.42% | 72.05% | 0.8448 | 680.1 | 600 | 0.1997 | 0.6071 |
| 30% | 966 | 706 | 260 | 73.08% | 70.20–75.79% | 65.84% | 0.8305 | 802.3 | 706 | 0.2056 | 0.6173 |
| 35% | 1,128 | 828 | 300 | 73.40% | 70.75–75.90% | 75.31% | 0.8171 | 921.7 | 828 | 0.2027 | 0.6087 |
| 40% | 1,289 | 934 | 355 | 72.46% | 69.96–74.83% | 65.84% | 0.8047 | 1,037.2 | 934 | 0.2059 | 0.6139 |
| 45% | 1,450 | 1,039 | 411 | 71.66% | 69.28–73.92% | 65.22% | 0.7928 | 1,149.5 | 1,039 | 0.2084 | 0.6180 |
| 50% | 1,611 | 1,135 | 476 | 70.45% | 68.18–72.63% | 59.63% | 0.7813 | 1,258.7 | 1,135 | 0.2122 | 0.6249 |
| 75% | 2,417 | 1,600 | 817 | 66.20% | 64.29–68.06% | 57.69% | 0.7286 | 1,761.1 | 1,600 | 0.2237 | 0.6454 |
| 100% | 3,223 | 2,023 | 1,200 | 62.77% | 61.08–64.42% | 52.48% | 0.6809 | 2,194.6 | 2,023 | 0.2304 | 0.6575 |

### 2.3 Corrected Agent 1 (identity bridge)

| Cov. | k | W | L | Acc. | Wilson 95% | Marginal acc. | Mean conf. | Exp. W | Obs. W | Brier | Log loss |
|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| 10% | 322 | 284 | 38 | 88.20% | 84.22–91.28% | 88.20% | 0.8699 | 280.1 | 284 | 0.1053 | 0.3685 |
| 20% | 644 | 539 | 105 | 83.70% | 80.64–86.35% | 79.19% | 0.8292 | 534.0 | 539 | 0.1352 | 0.4406 |
| 25% | 805 | 659 | 146 | 81.86% | 79.05–84.37% | 74.53% | 0.8129 | 654.4 | 659 | 0.1462 | 0.4660 |
| 30% | 966 | 779 | 187 | 80.64% | 78.03–83.01% | 74.53% | 0.7983 | 771.2 | 779 | 0.1536 | 0.4833 |
| 35% | 1,128 | 893 | 235 | 79.17% | 76.70–81.44% | 70.37% | 0.7850 | 885.5 | 893 | 0.1615 | 0.5013 |
| 40% | 1,289 | 1,000 | 289 | 77.58% | 75.22–79.77% | 66.46% | 0.7728 | 996.1 | 1,000 | 0.1692 | 0.5185 |
| 45% | 1,450 | 1,107 | 343 | 76.34% | 74.09–78.46% | 66.46% | 0.7616 | 1,104.3 | 1,107 | 0.1753 | 0.5319 |
| 50% | 1,611 | 1,197 | 414 | 74.30% | 72.11–76.38% | 55.90% | 0.7508 | 1,209.5 | 1,197 | 0.1833 | 0.5493 |
| 75% | 2,417 | 1,681 | 736 | 69.55% | 67.68–71.35% | 60.05% | 0.7026 | 1,698.2 | 1,681 | 0.2020 | 0.5902 |
| 100% | 3,223 | 2,116 | 1,107 | 65.65% | 64.00–67.27% | 53.97% | 0.6609 | 2,130.0 | 2,116 | 0.2137 | 0.6153 |

The calibration gap flips from +5.3 pp overconfidence at 100% (original) to +0.4 pp
(corrected). At 40–50% the corrected gap is −0.3 to +0.8 pp, i.e. essentially
calibrated. The original is overconfident by 7.6–8.0 pp at the same points.

### 2.4 ATP and Challenger

| Coverage | Corrected ATP n / acc. / Brier | Corrected Challenger n / acc. / Brier |
|---|---|---|
| 40% | 280 / 79.64% / 0.1533 | 1,009 / 77.01% / 0.1737 |
| 45% | 303 / 79.21% / 0.1567 | 1,147 / 75.59% / 0.1802 |
| 50% | 330 / 77.27% / 0.1648 | 1,281 / 73.54% / 0.1881 |
| 100% | 577 / 69.84% / 0.1969 | 2,646 / 64.74% / 0.2174 |

ATP is stronger at every point, but the 40–50% claim is carried mainly by the 2,646
Challenger fixtures. The ATP sample at 40–50% is only 280–330 selections.

### 2.5 Risk–coverage, AURC and AUGRC

The complete every-rank curve is in
`docs/research/evidence/a1_selective_risk_coverage_20261126.csv` (3,223 rows per
version). Risk is `1 − accuracy` in the prefix; generalized risk is
`prefix errors / 3,223`.

| Metric | Original | Corrected |
|---|---:|---:|
| AURC (tie-averaged mean cumulative selective risk) | 0.29342 | **0.24055** |
| AUGRC paper (trapezoidal finite-pool, tie-averaged) | 0.16170 | **0.14124** |
| AUGRC left Riemann (library convention, comparison) | 0.16176 | 0.14129 |
| Optimal binary-ranking AUGRC (lower bound for this error rate) | 0.06931 | 0.05899 |

The AUGRC estimator follows Traub et al., *Overcoming Common Flaws in the Evaluation
of Selective Classification Systems*, NeurIPS 2024: an error at zero-based rank `r`
contributes `(n − r − 0.5) / n²`, with equal-confidence ties averaged exactly. AURC is
the tie-averaged mean of cumulative selective risk. Both are descriptive; the
population is not a random sample of future fixtures.

### 2.6 Confidence-band calibration at 40–50%

| Corrected band | 40% n / acc. / mean conf. | 45% | 50% |
|---|---|---|---|
| 0.6–0.7 | 194 / 68.0% / 0.689 | 355 / 67.3% / 0.681 | 516 / 63.8% / 0.673 |
| 0.7–0.8 | 675 / 75.1% / 0.745 | 675 / 75.1% / 0.745 | 675 / 75.1% / 0.745 |
| 0.8–0.9 | 354 / 85.0% / 0.842 | 354 / 85.0% / 0.842 | 354 / 85.0% / 0.842 |
| 0.9–1.0 | 66 / 90.9% / 0.932 | 66 / 90.9% / 0.932 | 66 / 90.9% / 0.932 |

The 0.8–0.9 band is slightly underconfident (gap −0.8 pp); the 0.6–0.7 band becomes
overconfident at 50% (+3.5 pp). The 45→50% degradation is concentrated in the
0.6–0.7 band, not in the high-confidence core.

## 3. Phase 2 — four-way ranking/winner decomposition

### 3.1 The 2×2

| | Original winner call | Corrected winner call |
|---|---:|---:|
| Original ranking | A = **596** | B = **589** |
| Corrected ranking | C = **646** | D = **655** |

- Ranking effect at original winner: C − A = **+50**.
- Ranking effect at corrected winner: D − B = **+66**.
- Winner effect at original ranking: B − A = **−7**.
- Winner effect at corrected ranking: D − C = **+9**.
- Order-averaged ranking effect: (50 + 66) / 2 = **+58**.
- Order-averaged winner effect: (−7 + 9) / 2 = **+1**.
- Interaction: (D − C) − (B − A) = 9 − (−7) = **+16**.
- Identity: `655 − 596 = 59 = 58 + 1`.

Brian's +58/+1 order-averaged attribution is arithmetically exact. It is **not**
independent causal attribution: the two factors interact by +16 wins, and the
decomposition does not transfer to a new population.

### 3.2 The 509 common selections

- Shared selections: **509**; original wins 413, corrected wins 413.
- Side changes inside the shared set: **0**.
- Probability-only changes inside the shared set: **495**.
- Rank correlation over all 3,223: 0.604; median top-set displacement 405 places.
- On the identical 509 fixtures, probability quality improves:
  Brier 0.15745 → 0.15050; log loss 0.50703 → 0.47665.

The shared-set gain is therefore **probability quality**, not a different winner call.
The top-800 gain is mostly **selection**: 291 removed fixtures, 291 added fixtures.

### 3.3 Removed vs added cohorts

| Attribute | Removed (A top, not B) | Added (B top, not A) |
|---|---:|---:|
| Selections | 291 | 291 |
| Wins / accuracy | 183 / 62.89% | 242 / 83.16% |
| Mean confidence | 0.8235 | 0.7874 |
| Mean |Δp| | 0.1834 | 0.1105 |
| Mean player overall matches | 159.5 | 156.2 |
| Mean player surface matches | 85.7 | 77.6 |
| Mean player overall Elo | 1,658.4 | 1,673.9 |
| Mean player surface Elo | 1,610.9 | 1,611.2 |
| Mean inactivity (own version) | 7.5 days | 36.7 days |
| Mean inactivity (other version) | 9.1 days | 16.4 days |
| Any bridged player | 100.0% | 95.2% |
| ATP / Challenger | 54 / 237 | 46 / 245 |
| Direct / indirect recovery | 291 / 0 | 270 / 15 |
| Rounds | F16, QF21, R16 68, R32 96, SF38, Q1 30 | F9, QF12, R16 47, R32 107, SF48, Q1 48 |
| Identity methods (slots) | ALIAS 532, AMBIG 15, NO_HIST 35 | ALIAS 490, AMBIG 24, HIST_ID 1, NO_HIST 66, PERSISTED 1 |
| Bookmaker quote available | 129 | 145 |
| Market accuracy on available | 67.44% | 84.14% |

The added cohort is genuinely less active: its inactivity is 16.4 days even under
the original identity namespace and 36.7 days under the corrected one. The bridge is
therefore doing what the mechanism claims: it recovers the class of players whose
recent identity was disconnected. It also means the added cohort is selected on a
longer-layoff, lower-confidence profile, not on a higher-confidence one.

The inactivity stratification of the corrected top-800 is reassuring only in part:

| Shorter player inactivity | n | Accuracy | Brier |
|---|---:|---:|---:|
| ≤ 7 days | 557 | 81.33% | 0.1497 |
| 8–30 days | 210 | 84.29% | 0.1291 |
| 31–90 days | 9 | 88.89% | 0.1081 |
| > 90 days | 24 | 70.83% | 0.2254 |

The >90-day bucket is materially weaker. The added cohort is not dominated by it
(24 of 800), but a live policy must cap or shade long layoffs.

Bookmaker evidence on the two cohorts does not support a model edge: the market
scores 67.44% on the removed cohort's 129 quoted fixtures and 84.14% on the added
cohort's 145 quoted fixtures, versus 62.89% and 83.16% for the respective model
versions. Those subsets are selected by quote availability; they are context, not a
paired market test.

### 3.4 What the +59 does and does not mean

- It is a **within-population re-ranking** result at a fixed 800 selections.
- It is not a deployable Model Picks result; Model Picks has its own eligibility rule
  and gives 575/593 under the same replay, not 596/655.
- It is not a 59-win forward gain. The top-800 boundary, the 3,223 settled universe and
  the identity policy are all frozen retrospectively.

## 4. Phase 3 — identity, surface ratings and historical integrity

### 4.1 Direct and indirect recovery

- Affected forecasts (any probability change): **3,150**.
- Direct recovery (a target player was bridged and history recovered): **3,078**;
  mean |Δp| 0.09243; mean |Δ overall Elo| 76.71; cumulative recovered matches 203,682.
- Indirect-only (target ids unchanged; opponent ledger moved): **72**; mean |Δp|
  0.04949; mean |Δ overall Elo| 37.42; recovered matches 0.
- Mutual exclusivity: **true** (3,078 + 72 = 3,150; no overlap).

Cumulative recovered matches (203,682) count a match once per target that benefits
from it. The unique history census is smaller: 16,105 history matches changed
attribution, representing 28,852 player-match links for 1,234 players. The two numbers
answer different questions and must not be substituted for each other.

### 4.2 Identity fragmentation and merge risk

| Quantity | Value |
|---|---:|
| Settled rows / player slots | 3,223 / 6,446 |
| Unique live players | 1,089 |
| Unique model players | 1,085 |
| Bridged slots | 5,652 (87.68%) |
| ALIAS | 5,650 |
| AMBIGUOUS | 193 |
| NO_HISTORICAL_CANDIDATE | 600 |
| PERSISTED_BRIDGE | 2 |
| HISTORICAL_ID | 1 |
| Multi-candidate slots | 193 |
| Live ids with multiple model ids | 0 |
| Model ids receiving multiple live ids | 4 |
| Collisions (both sides same model id) | 0 |

193 ambiguous slots (3.0% of slots) are the fail-closed risk set. Four model ids
receive more than one live id; each is an intended merge of fragmented source
identities, but it is also where an incorrect merge would do the most damage. The
bridge never produced a same-model collision on both sides of a fixture.

### 4.3 Rating stability

Across 5,948 player slots with both ratings:

| Overall Elo |Δ| | Value |
|---|---:|
| Mean | 73.82 |
| Median | 57.13 |
| p95 | 216.17 |
| Max | 445.42 |
| Share > 100 | 26.06% |
| Share exactly 0 | 5.35% |

Surface Elo moves less (mean 36.63, median 21.82, max 401.24) because surface history
is weighted separately. A 100+ Elo move is a material change; a quarter of player slots
experience one. That is the expected consequence of recovering a disconnected history,
and it is the main stability risk if a merge is wrong.

### 4.4 How overall Elo informs surface rating

The production rule is:

```
w = min(surface_matches, 10) / 10 × 0.5
effective = (1 − w) × overall + w × surface
```

- Mean weight 0.383, median 0.5; 68.7% of slots are at the 0.5 cap; 12.8% have zero
  surface weight.
- A player with fewer than 10 surface matches is therefore never more than 50% driven
  by surface form; the overall rating carries at least half.
- Corrected accuracy by the *shorter* of the two players' surface histories:
  0 matches 68.82% (n=558), 1–4 62.78% (n=575), 5–9 66.22% (n=373), 10+ 65.46%
  (n=1,717). The 1–4 bucket is the weakest; limited surface experience is a real
  uncertainty, not a cosmetic one.

### 4.5 Historical timestamps and duplicates

- All 3,223 `as_of` and `match_start_at` values carry sub-second precision.
- Database consistency screen: 234,764 match versions and 134,755 result observations
  with zero `ingested_at < source_observed_at`, zero `as_of < source_observed_at`, and
  zero results observed before the match start.
- 21 forecasts have `as_of >= match_start_at` (one material; see §1.3).
- Duplicate-match risk: 57 groups of the same unordered player pair on the same
  calendar day, covering 122 distinct match ids. This is a flag, not proof of
  duplication; the replay deduplicates by `match_id`.

### 4.6 Migration 0025 in isolation

`docs/research/evidence/a1_migration_0025_isolated_20261126.json` records an
ephemeral PostgreSQL 16 validation:

- full chain `0001..0025` applied from an empty schema;
- `0025` down removes `player_name_versions`, `player_alias_versions`,
  `identity_resolution_provenance` and the alias identity guard;
- `0025` up restores all three tables, the guard and the three append-only triggers;
- unique provenance key `uq_identity_resolution_provenance_key` present;
- 59 targeted PostgreSQL tests passed (24 provenance/runtime/replay/persistence +
  35 forward-shadow/bulk-resolver/history-bridge).

Production remains at 0022. 0023/0024/0025 are **not** applied. The strict v3 policy
has therefore not been re-measured on the production schema; the 3,223 experiment is
not a v3 result.

## 5. Phase 4 — chronological validation

### 5.1 Frozen thresholds on an earlier period

Development: 2026-09-10..2026-10-31. Holdout: 2026-11-01..2026-11-25 (1,025 rows).
The threshold is the confidence at the requested coverage rank in the development
period only; no holdout outcome enters it.

| Target | Dev n / k / threshold | Holdout k / achieved cov. | Accuracy | Wilson 95% | Brier | Log loss | ATP / CH acc. |
|---|---:|---:|---:|---|---:|---:|---|
| Original 40% | 2,198 / 879 / 0.7119 | 382 / 37.27% | 72.51% | 67.83–76.75% | 0.2044 | 0.6071 | 78.57% / 71.47% |
| Original 45% | 2,198 / 989 / 0.6930 | 429 / 41.85% | 71.79% | 67.36–75.85% | 0.2067 | 0.6112 | 78.95% / 70.70% |
| Original 50% | 2,198 / 1,099 / 0.6705 | 483 / 47.12% | 70.60% | 66.39–74.49% | 0.2105 | 0.6183 | 78.69% / 69.43% |
| Corrected 40% | 2,198 / 879 / 0.6833 | 375 / 36.59% | **78.40%** | 73.96–82.27% | 0.1677 | 0.5178 | 78.00% / 78.46% |
| Corrected 45% | 2,198 / 989 / 0.6681 | 420 / 40.98% | **76.43%** | 72.14–80.24% | 0.1761 | 0.5358 | 77.36% / 76.29% |
| Corrected 50% | 2,198 / 1,099 / 0.6487 | 476 / 46.44% | **74.16%** | 70.04–77.89% | 0.1849 | 0.5547 | 77.59% / 73.68% |

The achieved coverage is below the target because the holdout confidence distribution
shifted down (the 50% threshold selects 46.4%, not 50%). This is the honest live
behaviour: a frozen threshold does not guarantee a coverage quota. The accuracy at the
coverage actually achieved is 72–78%, consistent with the retrospective curve.

### 5.2 Expanding weekly walk-forward

Thresholds are re-frozen each week from all earlier weeks only.

| Target | Weeks scored | Aggregate k | Wins | Accuracy | Brier | Log loss |
|---|---:|---:|---:|---:|---:|---:|
| Original 40% | 8 | 1,091 | 783 | 71.77% | 0.2109 | 0.6279 |
| Original 45% | 8 | 1,210 | 861 | 71.16% | 0.2125 | 0.6298 |
| Original 50% | 8 | 1,340 | 939 | 70.07% | 0.2157 | 0.6352 |
| Corrected 40% | 8 | 997 | 780 | **78.23%** | 0.1678 | 0.5171 |
| Corrected 45% | 8 | 1,116 | 854 | **76.52%** | 0.1753 | 0.5335 |
| Corrected 50% | 8 | 1,252 | 937 | **74.84%** | 0.1823 | 0.5485 |

The weekly path is not uniformly strong: corrected 40% ranges from 74.3% (2026-W43)
to 81.9% (2026-W44); 50% ranges from 68.3% (2026-W43) to 80.0% (2026-W41). The aggregate
is stable enough to support the retrospective finding, not enough to certify a live
floor.

### 5.3 What chronological validation does not establish

- The holdout is untouched by threshold selection, but **not untouched by model or
  replay development**. The identity bridge, surface handling and reconciliation code
  were written with knowledge of the whole period. This is a pseudo-out-of-sample
  check, not a prospective trial.
- The achieved coverage is 36.6–46.4%, not 40–50%. The 75% target is met at the
  achieved coverage, but a forced 50% quota would dilute it toward the 72% floor.
- There is no independent wall-clock receipt and no first-issued forward sample.

## 6. Candidate readiness and promotion decision

**`can_promote = false`.** Keep the candidate off.

The scientific position at this checkpoint:

| Question | Answer |
|---|---|
| Does corrected Agent 1 reach 72–75% at 40–50% retrospective coverage? | **Yes** (77.58 / 76.34 / 74.30). |
| Is the 75% target achieved at 50%? | **No** (74.30%); yes at 40% and 45%. |
| Does chronological validation support it? | **Directionally yes** at 36.6–46.4% achieved coverage (78.40 / 76.43 / 74.16); no forced-50% proof. |
| Is the gain a better probability on the same fixtures or a different selection? | Mostly **selection** (291 removed / 291 added); shared 509 have 0 side changes and a real Brier/log-loss gain. |
| Is the +59 a causal ranking effect? | No. Order-averaged +58 ranking / +1 winner is algebraic; interaction +16. |
| Is the candidate better than the market? | **No** on the 1,593 pre-issue quoted fixtures: market 70.94% full / 86.19% at 40% vs corrected 67.42% / 78.65%. |
| Is the identity policy production-ready? | **No.** v3 is unapplied and unmeasured on production; 193 ambiguous slots remain. |
| Is there a forward receipt? | **No.** 0 admissible independent receipts; 2/200 first-issued settled selections. |

Remaining scientific blockers:

1. No independent prospective sample: 2/200 settled first-issued selections, 2/30 days,
   no external wall-clock archive.
2. v3 not applied to production; the 3,223 result is pre-v2 resolver semantics.
3. Market benchmark is stronger at every evaluated coverage point on the quoted subset.
4. 50% coverage is at the edge: marginal 45→50% accuracy is 55.9%.
5. 193 ambiguous resolution slots and 4 multi-live-id merges remain fail-closed risks.
6. Long-layoff (>90 days) selections are weaker (70.8%, n=24).
7. 21 forecasts at/after start; one materially late (42.9 minutes).
8. No ATP forward sample; ATP holdout n is only 50–58 selections.
9. No new Agent 1 config version, owner approval or rollback sign-off for activation.

Prospective acceptance requirements remain unchanged:

- ≥200 settled **first-issued** bridged selections over ≥30 days;
- an independent, externally timestamped collector archive;
- paired Brier and log-loss 95% intervals entirely below zero on the frozen forward set;
- equal-count and frozen-coverage Model Picks comparisons;
- no material ATP degradation and a separately reported ATP sample;
- v3 re-measurement after 0025 is applied;
- a new Agent 1 config version, owner approval and a rollback plan.

## 7. Reproduction

```powershell
# 1. Export the sanitised population (read-only database session)
python ops/validation/a1_selective_population_export_20261126.py

# 2. Run the frozen experiment (offline; 1,000-rep day-block bootstrap)
python ops/validation/a1_selective_performance_experiment_20261126.py

# 3. Identity and population integrity (read-only)
python ops/validation/a1_identity_integrity_audit_20261126.py

# 4. Tests
python -m pytest -q tests/test_a1_selective_performance.py
```

Committed artifacts:

- `a1_selective_population_20261126.csv` — 3,223 settled fixture rows, 67 columns.
- `a1_target_census_20261126.csv` — 3,776 targets with exclusion reasons.
- `a1_selective_ranked_populations_20261126.csv` — both complete rankings.
- `a1_selective_risk_coverage_20261126.csv` — every-rank risk curve.
- `a1_selective_population_meta_20261126.json` — source ledger hash, row counts, quotes.
- `a1_selective_performance_20261126.json` — full experiment evidence.
- `a1_identity_integrity_20261126.json` — churn, snapshots, history, duplicates,
  timestamps, rating stability, five-slot reconciliation.
- `a1_migration_0025_isolated_20261126.json` — isolated up/down/up validation.
- `a1_selective_performance_manifest_20261126.json` — artifact hashes and code versions.

No names, aliases, contact details, account identifiers or provider payloads are in the
population exports. The bookmaker benchmark contains only the devigged probability
derived from a pre-issue quote; no prices are published.

## 8. Evidence classifications (kept separate)

| Class | What it is | Use |
|---|---|---|
| Original production forecast | Stored `predictions.win_probability` for E1/E2 | Historical reference only |
| Strict point-in-time replay | Reconstructed unbridged replay with PIT target ids and history visibility | E3 A baseline |
| Corrected retrospective research | Bridged replay, production surface | This experiment |
| Prospective shadow | First-issued capture after the freeze | 2 records, 0 admissible receipts |

The 3,223 result belongs to the third class. It must not be quoted as the first, second
or fourth.
