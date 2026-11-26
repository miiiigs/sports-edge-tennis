# Complete marginal Challenger calibration reconciliation (2026-11-26)

Research-only, read-only. No migration, deployment, provider request, purchase,
retrain, refreeze or forecasting-policy change. The frozen Manager C envelope,
RFC 3161 token, weights, thresholds and settlement boundary are unchanged. The
window has been inspected before: this is descriptive historical research, not
independent validation.

Reproduce:

```
python -m sports_edge_tennis.validation.manager_c_marginal_calibration \
  --output docs/research/evidence/manager_c_marginal_calibration_real_20261126.json
```

Source commit: the integration branch at the time of this document. The adapter
reuses the registered `MANAGER_C_ORIGINAL_SOURCE_V1` source SQL, the unchanged
20%/40% cutoffs and the existing PIT identity bridge.

## 1. Exact reconciliation

The marginal cohort is `broad MINUS narrow` by exact original match IDs, not a
new model. The registered dataset hash is reproduced exactly:
`e7d70fb821d7c69339adfce51bbd7fe35161adda49c595124d1791fef1b56863`.

| Item | Value |
|---|---|
| Marginal selected (all tours) | 158 |
| Marginal settled (all tours) | 149 |
| **Marginal Challenger selected** | **152** |
| **Marginal Challenger settled** | **143** |
| **Actual wins** | **92** |
| **Expected wins (Σ selected p)** | **94.559351** |
| Residual (actual − expected) | −2.559351 |
| Mean selected probability | 0.661254 |
| Actual rate | 0.643357 |
| Brier (selected side) | 0.227587 |
| Log loss (selected side) | 0.647396 |

This reproduces the original 143 / 92 / 94.559 figures exactly. The six
marginal ATP main selections are reported separately in the evidence JSON
(4 wins, expected 3.997); they are not mixed into the Challenger reconciliation.

## 2. Complete confidence distribution

Confidence is `max(selected p, 1 − selected p)`. Bands are half-open except the
last. Uncertainty columns are player-clustered bootstrap 95% intervals for
accuracy and residual (2,000 resamples; the independent-binomial z is shown
separately because it is anti-conservative).

| Band | Settled | W–L | Mean p | Expected | Actual | Residual | z (indep.) | Brier | Log loss | Accuracy 95% (cluster) | Residual 95% (cluster) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| below 0.55 | 0 | 0–0 | — | 0 | 0 | 0 | — | — | — | — | — |
| 0.55–0.60 | 0 | 0–0 | — | 0 | 0 | 0 | — | — | — | — | — |
| 0.60–0.65 | 54 | 28–26 | 0.6363 | 34.361 | 28 | −6.361 | −1.80 | 0.2645 | 0.7236 | 0.364–0.667 | −5.65 to +11.93 |
| 0.65–0.70 | 82 | 61–21 | 0.6741 | 55.274 | 61 | +5.726 | +1.35 | 0.1953 | 0.5802 | 0.638–0.835 | +9.64 to +29.79 |
| 0.70–0.75 | 7 | 3–4 | 0.7035 | 4.924 | 3 | −1.924 | −1.59 | 0.3211 | 0.8469 | 0.143–0.714 | −3.71 to +2.51 |
| 0.75–0.80 | 0 | 0–0 | — | 0 | 0 | 0 | — | — | — | — | — |
| 0.80+ | 0 | 0–0 | — | 0 | 0 | 0 | — | — | — | — | — |

The empty bands are structural, not missing data:

- the frozen broad 40% cutoff is **0.62133**, so no marginal selection can be
  below 0.62133; the 0.55–0.60 band and the 0.60–0.62133 portion are measured
  zeros by construction;
- the frozen narrow 20% cutoff is **0.7070145** and narrow picks are removed
  from marginal, so 0.75–0.80 and 0.80+ cannot contain a marginal selection;
- the 0.70–0.75 band contains only 0.70–0.7070.

The two populated adjacent bands have opposite residuals (−6.36 and +5.73).
That is offsetting noise around zero, not a monotone calibration failure. The
pooled residual is −2.56, and the dependence-aware residual z is **−0.42**
(section 3).

## 3. Expected winner-count variance and dependence

| Quantity | Value |
|---|---|
| Independent variance `Σ p(1−p)` | 31.952 |
| Independent SD | 5.653 |
| Player-clustered bootstrap variance of wins | 71.037 |
| Player-clustered bootstrap SD | 8.428 |
| Design effect (cluster / independent) | **2.223** |
| Clusters (unique favourites) | 105 |
| Largest cluster | 4 selections |
| Pooled residual z using cluster SD | **−0.417** |

`Σ p(1−p)` is exact only if selections are independent Bernoulli trials. They
are not: selections share favourites, opponents, tournament conditions and
market regimes. The cluster bootstrap resamples favourites and captures part of
that dependence; it still cannot capture unobserved shared shocks or opponent
overlap. The independent z values in section 2 must therefore be read as
optimistic. With the cluster estimate, the whole-cohort shortfall is well within
noise.

## 4. Identity completeness and history depth

The existing PIT bridge (`resolve_model_player_ids`, fail-closed on non-unique
aliases) is applied at the original cutoffs for research history counts only. It
does not change any prediction, selection, probability or settlement.

| Population | Raw favourite <20 | Recovered to ≥20 | Still <20 | Mean raw → bridged |
|---|---|---|---|---|
| Marginal Challenger selected (152) | 15 | 14 | 1 | 13.27 → 117.53 |
| Marginal Challenger settled (143) | 15 | 14 | 1 | 13.27 → 117.53 |
| **Full marginal selected, all tours (158)** | **16** | **15** | **1** | **13.00 → 133.63** |

The established finding — **15 of 16** marginal low-history cases recover at
least 20 matches after the bridge — is a property of the **full marginal
selected population** (158, including one ATP selection). On the
Challenger-only settled 143 it is **14 of 15**; the fifteenth case is the same
single unresolved identity. The two statements are consistent and must not be
interchanged.

Identity completeness on the 143 settled Challenger selections:

- both players have raw prior history: 142/143;
- both players have bridged prior history: 143/143;
- favourite identity mapped: 135/143;
- one favourite remains unresolved: it has only live-provider source IDs and no
  unique historical alias, so the bridge correctly fails closed;
- the `favourite_historical_identity_present` counter is zero because these
  players carry no non-live provider source rows; the resolver uses persisted
  bridges and unique `TENNIS_DATA` aliases. That counter is not proof of missing
  identity.

Bridged history depth (settled 143): 10–19 → 1; 20–49 → 16; 50–99 → 15;
100+ → 111. Surface experience on the match surface: 0 → 10; 1–4 → 1; 5–9 → 4;
10–19 → 18; 20+ → 110.

## 5. Inactivity, newcomers and ITF transitions

- **Inactivity is mostly unknown, not zero.** 135 of 143 settled selections
  have `START_DATE_INCOMPLETE`; production stores 117,713 SACKMANN match versions
  with `match_start_at IS NULL` (migration 0003 intentionally dropped
  `NOT NULL`). Only 8 selections have a known 0–30 day gap. Career start and
  inactivity are therefore not inferred for the affected players.
- **No genuine newcomer population is separable.** The one favourite still
  under 20 after the bridge is also the only `SHORT_CAREER_ITF_OR_DEBUT` case,
  and it overlaps `IDENTITY_LINK_UNRESOLVED`. It is not evidence of
  inexperience.
- **ITF transition cannot be distinguished from debut.** No ITF/FUT result is
  ingested. `SHORT_CAREER_ITF_OR_DEBUT` is a union, never a split and never a
  claim of genuine inexperience.

There is no basis for a blanket newcomer penalty.

## 6. Overlapping classifications

Primary counts on the 143 settled Challenger selections: history at least 20 →
128; identity gap recovered → 14; short career/ITF-or-debut → 1. Flags overlap:

| Overlap | Count |
|---|---|
| no flags | 7 |
| under 20 + identity recovered + start date incomplete | 14 |
| under 20 + identity link unresolved + short career/ITF-or-debut | 1 |
| start date incomplete only | 121 |

On the 152 selected Challenger rows the same overlap structure appears (8 / 14
/ 1 / 129), plus one additional start-date-incomplete row. Overlaps are
preserved rather than force-ranked into a single cause.

## 7. Controls for original probability

Lower raw accuracy in a subgroup is not miscalibration unless the subgroup's
residual is also adverse at the same predicted probability. Within each band:

| Band | Subgroup | Settled | W–L | Expected | Residual | z (indep.) |
|---|---|---|---|---|---|---|
| 0.60–0.65 | corrected history healthy | 48 | 25–23 | 30.538 | −5.538 | −1.66 |
| 0.60–0.65 | surface experience <5 | 6 | 3–3 | 3.823 | −0.823 | −0.70 |
| 0.65–0.70 | corrected history healthy | 76 | 56–20 | 51.251 | +4.749 | +1.16 |
| 0.65–0.70 | favourite bridged <20 | 1 | 0–1 | 0.679 | −0.679 | −1.45 |
| 0.65–0.70 | surface experience <5 | 5 | 5–0 | 3.344 | +1.656 | +1.57 |

The 0.60–0.65 shortfall is mostly the healthy-history group, not the
low-history group; the 0.65–0.70 surplus offset it. There is no monotone
history or surface gradient.

Other measured controls (settled, W–L, expected, residual):

- surface: clay 32, 20–12, 21.209, −1.209; hard 101, 64–37, 66.811, −2.811;
  unknown 10, 8–2, 6.540, +1.460;
- main draw 130, 86–44, 85.982, +0.018; qualifying 13, 6–7, 8.578, −2.578;
- surface experience 0: 10, 8–2, 6.540, +1.460; 20+: 110, 68–42, 72.827,
  −4.827;
- history depth 100+: 111, 72–39, 73.466, −1.466.

The qualifying and surface-depth cells are small and are not used to justify a
policy change.

## 8. Limits

- The November window was previously inspected; this is not forward validation.
- Expected wins and residuals use the original selected-side probability; no
  recalibration is fitted or applied.
- Only ATP main and Challenger results are ingested; ITF/FUT is absent.
- Career and inactivity are not inferred when `match_start_at` is missing.
- History counts are research-derived from PIT-visible rows, not persisted
  original-run feature snapshots.
- The identity bridge is an alias-uniqueness resolver, not a verified provider
  census; it fails closed on ambiguity.
- Selections are not independent; the cluster bootstrap is partial.
- The registered prospective Manager evaluation remains untouched and is the
  only independent comparison.

## 9. Conclusion

The original 143 / 92 / 94.559 reconciliation holds exactly. The full band
decomposition shows offsetting residuals, a pooled shortfall of 2.56 wins, and a
dependence-aware z of −0.42. The apparent low-history group is overwhelmingly an
identity-linkage artefact: 15 of 16 full-marginal cases recover at least 20
matches through the existing bridge, 14 of 15 on the Challenger-only settled
subset. The one remaining case is an unresolved live-only identity that also
looks like a short career; it is not evidence of inexperience. There is no
justified newcomer penalty, model change, threshold change or forecasting-policy
change. The actionable issue remains identity linkage and pre-issue odds
ordering.
