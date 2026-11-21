# 2026-11-21 research: original A1/A2 independence and selective-risk audit

**Status:** genuine read-only production-database evaluation; **research only, NOT an approved release.**  
**Owner:** Sports Edge Tennis / Brazzy365. **Requested by:** Brian's 21–23 November research charter.  
**Data cutoff:** 2026-11-21T03:30:00Z. **Repo commit:** \`a730c37211cf99764a655d47305baab208e0fc05\` (merged PR #343).  
**Run:** Railway isolated, one-off deployment \`2ce5db37-ec00-4e14-9321-f1122a91e075\`; restricted PostgreSQL read-only session and no production changes; temporary service deleted after completion.  
**Dataset SHA-256:** \`c712b6b74c61abf667328899c78f2075279e229832f93e4f9592060540fd51d2\`.  
**Code:** \`src/sports_edge_tennis/validation/a1_a2_independence_risk_coverage.py\`.  
**Command:** \`PGOPTIONS='-c default_transaction_read_only=on' TENNIS_DATABASE_URL=<existing production DSN> python -m sports_edge_tennis.validation.a1_a2_independence_risk_coverage --as-of-utc 2026-11-21T03:30:00Z\`.

## 1. Frozen, point-in-time matched population

- Source query: 2,361 canonical first-coissued A1/A2 rows; both original predictions originate in the same completed nonexperiment run and precede match start by at least 30 minutes. Each matched fixture appears once after source normalization.
- Excluded as unsafe: 17 late/invalid paired issuances; 4 missing PIT timestamps; 18 unsafe winner/result timestamps. These **39 are not silently counted as losses or wins**.
- Eligible for the **matched-pair** research cohort: **2,322** original forecasts; **2,126** completed with a safe recorded winner; **196 ungraded/void/pending**. Only the 2,126 safely settled fixtures are scored. This is NOT all eligible ATP and Challenger matches and cannot establish 40–50% publication coverage.
- **278 ATP** and **1,848 Challenger** settled paired matches, so most evidence is from Challenger. All comparisons below use the **same 2,126 settled match identifiers**, except independently confidence-ranked selective subsets, which are explicitly not paired by fixture.

## 2. Individual model and 50:50 log-odds blend on exactly identical settled matches

| Historical first-issued method | Won | Lost | Accuracy | Brier (lower better) | Log loss (lower better) | Mean stated winner confidence |
|---|---:|---:|---:|---:|---:|---:|
| A1 surface Elo | 1,322 | 804 | 62.18% | 0.231843 | 0.661681 | 68.40% |
| A2 serve/return Markov | 1,271 | 855 | 59.78% | 0.238880 | 0.673845 | 64.26% |
| Frozen equal log-odds blend | 1,333 | 793 | 62.70% | 0.227505 | 0.648000 | 65.33% |

- Against A1, the untrained 50:50 blend identifies **11 additional winners / 2,126 (+0.52 percentage points)**, improves Brier by **0.004337 (1.87% relative)**, and log loss by **0.013681 (2.07% relative)** on this descriptive historical cohort.
- Cluster bootstrap **95% CI for A1-minus-blend Brier:** **[0.000634, 0.007760]**, using tournament-edition clusters only. This is conditional on a previously inspected, selected cohort, does not correct all multiple testing, and does not account for every player/time dependency. Not independent holdout proof.
- **ATP (n=278):** A1 190/278=68.35%, A2 154/278=55.40%, blend 190/278=68.35%. A1 Brier **0.204764** vs blend **0.207681**: adding A2 **worsens** ATP probability accuracy in this cohort despite unchanged hit rate.
- **Challenger (n=1,848):** A1 1,132/1,848=61.26%, A2 1,117/1,848=60.44%, blend 1,143/1,848=61.85%. A1 Brier **0.235916** vs blend **0.230488**, indicating the aggregate blended gain is concentrated on Challenger. Further chronologically unseen evaluation is mandatory.

## 3. Does A2 provide complementary rather than independent information?

| On the same 2,126 predictions | Fixtures |
|---|---:|
| Both agents correct | 989 |
| Only A1 correct | 333 |
| Only A2 correct | 282 |
| Both agents wrong | 522 |
| A1 and A2 select the same side | 1,511 |
| A1 and A2 select opposite sides | 615 |

- A1/A2 predicted probability correlation: **0.6105**; binary prediction-error correlation: **0.3930**. They have **some complementary information and substantial correlated error**, not independent expert votes.
- When agreeing, both were correct in **989/1,511=65.45%**, and both wrong in **522/1,511=34.55%**. Agreement alone is not a reliable publication threshold.
- On 615 disagreements, A1 alone identified the winner 333 times; A2 alone 282 times. Exact unadjusted paired McNemar p-value **0.04369**. Historical, nonindependent tournament/player observations and exploration mean **no general superiority claim** should rest on this p-value.
- **Agent 3, actual Manager, odds/market, competitor outputs, and published Model Picks are NOT evaluated in this report.** Do not substitute this A1/A2 agreement for three-agent Manager decisions.

## 4. Confidence-ranked selection on the settled paired cohort, NOT total fixture coverage

These retrospective top-fraction criteria were computed from the full observed prediction-confidence distribution. Rankings depend on immutable original probabilities, not labels; however, confidence cutoffs were **not chosen/frozen chronologically using an earlier validation period**. All equal-confidence observations at a cutoff are retained, so achieved counts may differ from target fractions. Do not advertise these as tested live thresholds or verified overall ATP/Challenger eligible-match coverage.

| Portion of matched settled cohort | A1 wins–losses / accuracy | Equal-blend wins–losses / accuracy |
|---|---|---|
| Top ~25% | 390–143 / **73.17%** (533) | 397–135 / **74.62%** (532) |
| Top ~40% | 615–236 / **72.27%** (851) | 610–241 / **71.68%** (851) |
| Top 50% | 746–317 / **70.18%** (1,063) | 739–324 / **69.52%** (1,063) |
| Top ~75% | 1,058–537 / **66.33%** (1,595) | 1,059–536 / **66.39%** (1,595) |
| All co-issued settled | 1,322–804 / **62.18%** | 1,333–793 / **62.70%** |

**Crucial:** models choose different matches at the same fraction; these rows cannot support paired winner-comparison claims. The fraction denominator is 2,126 A1/A2 co-issued safely settled predictions, *not* all eligible or data-qualified fixtures. The 75% observed at top quarter is not Brian's **72–75% sustained at 40–50% eligible coverage** goal.

## 5. Independent calibration diagnostic

A1 winner-confidence **80–89.9%**: 331 samples, mean stated confidence **84.44%**, actual winner accuracy **72.21%**, gap **12.23 points**.  
A1 **90%+**: 87 samples, stated **93.00%**, actual **80.46%**, gap **12.54 points**.  
A2 **80–89.9%**: 166 samples, stated **84.12%**, actual **70.48%**, gap **13.64 points**.

The earlier 86.1%-versus-73.9% discrepancy was a **different sample** and should not be conflated with these bands. Calibration quality is distinct from match-winner accuracy.

## 6. Research decision, blockers, and next actions

**Positive signal, no release:** The original equally weighted A1/A2 blend delivers better historical Brier and log loss overall, but the winner-accuracy gain is small, differences vary by ATP/Challenger, and A1 beats the blend on ATP probability quality. Given the existing prior 464-match and 467-match studies, this result is **not an untouched independent holdout**.

1. **Full denominator and provenance:** Reconstruct which records comprised the earlier claimed ~14,000-match, ~75%-accurate historical sample. Do **not** equate that to 134,066 raw stored fixtures or 2,126 paired original predictions. Check feature availability and label shuffling of training/retraining pipelines, A4 and player mappings.
2. **Independent chronological validation:** Predeclare fixed model versions, separate calibration/training/validation, production-equivalent input timestamps, hard final holdout, and prospective 30-day/200-unique-settled evaluation. Freeze any **confidence cutoff** on earlier validation only, and measure **all eligible-match coverage**.
3. **Agent contribution:** Evaluate actual A3, original strict 3/3, existing v1 Agent3-abstention fallback, and Manager A–E using same first-issued eligible fixtures. Abstentions cannot be treated as losing predictions.
4. **Competing models:** Compare A1, A2, their 50:50 blend and advanced Elo/Glicko/opponent-adjusted serve model against an immutable baseline. Study ATP/Ch stratification with adequate samples; measure paired proper scores, odds and uncertainty.
5. **Market and competitors:** Extend same-issue bookmaker source coverage beyond the prior 20/1,179 settled matches only with authenticated pre-cutoff evidence, lawful source rights, and no hindsight quote leakage. Freeze authorized competitor forecasts prospectively.
6. **Operational:** Research modules remain isolated; no new model/Manager rules, no archival prediction writes, no additional API purchases and no ongoing research Railway service.

Reference PR: https://github.com/miiiigs/sports-edge-tennis/pull/343  
Reference master charter: https://trello.com/c/jvlNgP4E

## 7. Reproducibility declaration

- UTC cutoff: \`2026-11-21T03:30:00Z\`; SHA \`a730c37211cf99764a655d47305baab208e0fc05\`.
- Read-only PostgreSQL session via \`PGOPTIONS=-c default_transaction_read_only=on\` and in-module \`SET default_transaction_read_only=on\`.
- Source: first *co-issued* original A1/A2, completed nonexperiment production run, consistent player-side probability orientation and PIT-available match version.
- Only authoritative \`COMPLETED\` winner results with safe source-observed/ingested visibility at cutoff are graded.
- Dataset hash above; module deterministic; bootstrap routine uses fixed random seed. No results were used to change live predictions or refit a model.
- CI: PostgreSQL Foundation \`#44862111312\` passed for final PR #343 revision, before merge.
- The one-off Railway service was deleted immediately after collecting its report. A future rerun requires a separate authorized read-only execution path.

**No claim of full feature-level leakage certification, independent prospective performance, or achieved live 72–75% target is made.**
