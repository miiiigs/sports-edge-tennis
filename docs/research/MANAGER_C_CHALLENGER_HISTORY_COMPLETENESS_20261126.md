# Manager C Challenger favourites with limited observable history (2026-11-26)

Research-only, read-only. No production migration, deployment, provider request,
purchase, forecasting-policy change or Manager retraining. The frozen Manager C
envelope, RFC 3161 token, manifest, weights, thresholds and settlement boundary
are unchanged. The November window has been inspected before: every number here
is **descriptive historical research, not independent validation**.

Question: an independent 95,100-match analysis reported calibration problems when
players with fewer than 20 prior matches are favourites. Does that condition exist
in our Manager C Challenger cohorts, and is it caused by inexperience, incomplete
identity links, ITF-to-Challenger transition or inactivity?

Reproduce (authorised read-only connection required):

```
python -m sports_edge_tennis.validation.manager_c_challenger_history \
  --output docs/research/evidence/manager_c_challenger_history_real_20261126.json
```

Source commit: `b2b6f0d` (integration PR #392; repository base `a74d041`,
readiness commit `8b74a7f`). The adapter reuses the unchanged PR357 first-issue
SQL, the registered 20%/40% thresholds and the frozen cohort code.

## 1. Cohort reconciliation

`dataset_sha256 = e7d70fb821d7c69339adfce51bbd7fe35161adda49c595124d1791fef1b56863`
matches the registered PR357 baseline exactly. First-issue targets 1,683;
exclusions unchanged (`UNSAFE_OR_LATE_FIRST_PREMATCH_ISSUE` 9,
`UNSAFE_RESULT_GRADED_AS_UNRESOLVED` 17).

| Cohort | Issued | Settled | Challenger issued | Challenger settled |
|---|---|---|---|---|
| narrow (20%) | 117 | 107 | 110 | 100 |
| broad (40%) | 275 | 256 | 262 | 243 |
| marginal (broad − narrow) | 158 | 149 | 152 | 143 |

Exact match-ID nesting holds: marginal is `broad MINUS narrow`, not a new model.
These counts reproduce the previously registered cohort.

## 2. Favourites with fewer than 20 observable prior matches

Prior matches use the **Agent 1 PIT definition** (`load_agent1_history`): a match
is observable at cutoff T only when a result observation and a matching match
version were both visible by T. The PIT index was validated against the real
loader at sampled cutoffs: identical match IDs and per-player counts for all
6,153 players tested at 2026-11-10T12:00Z.

| Cohort | Selections | Favourite <20 | of which Challenger |
|---|---|---|---|
| narrow | 117 | 11 | 11 |
| broad | 275 | 27 | 26 |
| marginal | 158 | 16 | 15 |

No selection had a favourite with literally zero observable history. Raw counts
were 1–19 (mean 13.0–14.2).

## 3. What the deficit actually is

The existing PIT identity bridge (`resolve_model_player_ids`, fail-closed on
non-unique alias candidates) was applied at each original cutoff. It is invariant
across the sampled window (earliest/middle/latest cutoffs; zero target IDs
changed).

| Cohort | Favourite <20 raw | Recovered to ≥20 by identity bridge | Still <20 after bridge | Mean raw → bridged |
|---|---|---|---|---|
| narrow | 11 | **11** | 0 | 14.2 → 166.9 |
| broad | 27 | **26** | 1 | 13.5 → 147.2 |
| marginal | 16 | **15** | 1 | 13.0 → 133.6 |

**This is the central result.** The apparent "fewer than 20 prior matches" group
is overwhelmingly an artefact of incomplete identity linkage, not genuine
inexperience: 26 of 27 broad selections (15 of 16 marginal) become experienced
players once the existing bridge maps the live-only player onto their historical
identity. Only one favourite remains genuinely low-history after correction (it
appears in two broad selections).

Explicit classification (overlaps retained in the evidence JSON):

- `IDENTITY_GAP_RECOVERED`: 26 broad / 15 marginal / 11 narrow.
- `IDENTITY_LINK_UNRESOLVED`: 1 broad / 1 marginal / 0 narrow. This player has
  only live-provider source IDs and no unique historical alias, so the bridge
  correctly fails closed. It is *not* evidence of inexperience.
- `RETURN_FROM_INACTIVITY`: not claimable. Production stores 117,713 SACKMANN
  match versions with `match_start_at IS NULL` (migration 0003 intentionally
  dropped `NOT NULL`). Career start and inactivity are therefore marked
  `START_DATE_INCOMPLETE`, not inferred.
- `SHORT_CAREER_ITF_OR_DEBUT`: no ITF/FUT result is ingested. A short observable
  career cannot be split into genuine debut versus ITF transition, so it is
  reported as a union and never as "genuine inexperience".

There is therefore **no measurable population of genuinely inexperienced
favourites** in this cohort, and no basis for a blanket newcomer penalty.

## 4. Actual vs original AI-expected results

`expected wins` is the sum of the original Manager C selected-side probability.
Residual = actual − expected. Intervals are Wilson 95% for accuracy; the
player-clustered bootstrap resamples favourites (selections sharing a favourite
are dependent) and is unstable below ~10 clusters.

| Cohort / group | Settled | W–L | Accuracy [Wilson] | Expected wins | Residual (z) | Brier | Log loss |
|---|---|---|---|---|---|---|---|
| narrow, <20 raw | 11 | 6–5 | 0.5455 [0.280, 0.787] | 8.74 | −2.74 (−2.06) | 0.3005 | 0.8210 |
| narrow, ≥20 raw | 96 | 74–22 | 0.7708 [0.677, 0.844] | 74.54 | −0.54 (−0.13) | 0.1712 | 0.5208 |
| broad, <20 raw | 27 | 17–10 | 0.6296 [0.442, 0.785] | 19.24 | −2.24 (−0.97) | 0.2489 | 0.7005 |
| broad, ≥20 raw | 229 | 159–70 | 0.6943 [0.632, 0.750] | 162.59 | −3.59 (−0.53) | 0.2047 | 0.5959 |
| marginal, <20 raw | 16 | 11–5 | 0.6875 [0.444, 0.858] | 10.50 | +0.50 (+0.26) | 0.2134 | 0.6177 |
| marginal, ≥20 raw | 133 | 85–48 | 0.6391 [0.555, 0.716] | 88.06 | −3.06 (−0.56) | 0.2289 | 0.6501 |

Interpretation:

- The only ≥2σ shortfall is the **narrow** group (−2.74 wins, z = −2.06, n = 11).
  The broad group is −0.97σ and the marginal group is +0.26σ. This is a small,
  inconsistent signal, not a replicated calibration failure.
- Accuracy in the low-history group is lower than the ≥20 group in narrow and
  broad, but the Wilson intervals overlap substantially. Brier and log loss are
  worse, consistent with less-certain forecasts for these fixtures.
- After identity correction the "still <20" group is 2 settled selections
  (0–2). No inference is possible from it, and none is made.

## 5. Controls

- **Original predicted probability**: in the 0.60–0.70 broad bin the low-history
  group is 11–16 (0.688) versus 82–126 (0.651) for ≥20; in 0.70–0.80 it is 3–6
  (0.500) versus 51–73 (0.699); in 0.80+ it is 3–5 (0.600) versus 26–30 (0.867).
  The pattern is not monotone and each low-history cell is tiny.
- **Competition level**: all narrow and 26/27 broad low-history favourites are
  Challenger; one broad ATP selection is in the group.
- **Surface**: broad low-history 14–22 on hard, 1–2 on clay, 2–3 unknown; the ≥20
  group is 109–158 hard, 31–49 clay, 19–22 unknown.
- **Main draw vs qualifying**: broad low-history 10–18 main draw, 7–9
  qualifying. Qualifying is not the driver.
- **Historical availability** itself: broad bins are 5–7 at 1–9 prior matches,
  12–20 at 10–19, and 159–229 at 20+.

## 6. Before and after identity correction

This is a lawful, comparable comparison because the same fixtures and the same
original probabilities are used; only the PIT identity namespace changes.

- Before: 27 broad favourites have <20 observable matches (mean 13.5).
- After: 26 of those 27 have ≥20 (mean 147.2); 1 remains unresolved.
- The correction does **not** change any prediction, selection, probability or
  settlement. It changes only the research history count.
- The remaining 2 settled selections (0 wins, expected 1.53) are too few to
  support a calibration claim.

## 7. Does history completeness add information beyond calibrated confidence?

A separate descriptive logistic fit (not the frozen
`MANAGER_C_FORWARD_ABLATION_V1` manifest) was trained on 403 first-issue rows
whose outcomes were known by 2026-11-04T00:00Z and evaluated on the 256 settled
broad selections from 2026-11-04 to 2026-11-21. Features: logit(original
probability), `under_20`, `log1p(count)`, `zero_history`, `inactive_180d`.

| Model | Brier | Log loss |
|---|---|---|
| Calibrated confidence only | 0.21259 | 0.61521 |
| Plus history completeness | 0.21323 | 0.61657 |

Brier improvement = **−0.00064** (history is slightly worse); player-clustered
95% interval [−0.00138, +0.00007]. History completeness does **not** contribute
information beyond calibrated confidence in this sample. The test is
underpowered: after identity correction almost no selected fixture is genuinely
low-history, so the feature has little variation. The `inactive_180d` coefficient
is zero because start dates are incomplete.

## 8. Limits and what is not claimed

- The November window was previously inspected; this is not independent forward
  validation.
- ITF/FUT history is not ingested; genuine debut versus ITF transition is not
  separable.
- 117,713 historical match versions have no `match_start_at`; inactivity and
  career length are not inferred for affected players.
- `rating_snapshots` remains empty, so original-run rating uncertainty and
  surface Elo cannot be used.
- History counts are research-derived from PIT-visible rows, not persisted
  original-run feature snapshots.
- The identity bridge is an alias-uniqueness resolver, not a verified provider
  identity census; it fails closed on ambiguity.
- No model, threshold, weight, settlement rule or production policy was changed.

## 9. Conclusion

Within our existing Manager C research, Brian's external "fewer than 20 prior
matches" signal does **not** reproduce as a genuine inexperience effect. The
group exists in the deployed Agent 1 view because live-only players are not
bridged by default, but 26 of 27 broad cases are experienced players once the
existing PIT bridge is applied. The narrow cohort shows a small negative
residual (z ≈ −2) that is not replicated in broad or marginal and is based on 11
selections. There is no justification for a newcomer penalty, a new model, or a
forecasting-policy change. The actionable engineering issue is identity linkage
(and the already-measured pre-issue sequencing gap), not player inexperience.
