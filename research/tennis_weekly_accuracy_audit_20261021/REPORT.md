# Tennis Weekly Accuracy and Prediction Failure Audit

**Window:** 2026-10-19 to 2026-10-21, Asia/Manila
**Prepared:** 2026-10-21
**Scope:** read-only production reconciliation, offline analysis, synthetic tests
**Production changes:** none. No model, Manager, Market, Veto, staking, schema,
database, deployment, or provider call was changed.

---

## 1. Executive answer

1. **Brian's report is confirmed, but the size depends on which published
   population is used.** The official first-qualified Manager Model Picks at
   >=60% went **9-8 (52.9%)** on 17 settled picks, with 3 pending and no voids.
   The dashboard-aligned headline, which also includes A1/A2 wide-spread
   tracking, went **42-25 (62.7%)** over the same three UTC days after the
   latest result refresh, with 16 pending and 5 voids. The dashboard's rolling
   seven-day headline was **89-44 (66.9%)**.
2. **The week cannot mathematically reach 70% on either headline population.**
   Even if all 3 pending Manager picks win, the official rate is **12/20 =
   60.0%**. Even if all 16 pending aligned picks win, the aligned rate is
   **58/83 = 69.9%**.
3. **The 70% target is not statistically rejected by three days of data.** The
   official 9/17 has a 95% Wilson interval of **31.0%-73.8%**; a one-sided
   binomial test against 70% gives p=0.105. The seven-day aligned 89/133 gives
   p=0.246. This is a real shortfall, not yet a statistically decisive one.
4. **The dashboard headline is not a Manager accuracy estimate.** In this
   window 69 of 88 aligned picks were A1/A2 **tracking-only** rows. The Manager
   subset of the aligned ledger was 8-8 (50.0%). The all-time aligned rate is
   70.6%, but the all-time Manager-only regime rate is 65.0%.
5. **Market disagreement is the strongest failure signal.** Picks more than
   20pp above the pre-pick sharp implied probability won **9/24 (37.5%)**
   against 72.7% predicted; the rest of the primary population won **144/188
   (76.6%)** (Fisher p=0.00017). Every one of this week's four largest losses
   carried a +20pp or larger disagreement.
6. **Tournament qualification metadata remains a material cohort defect.**
   Label/flag mismatches won **52/85 (61.2%)** versus **101/127 (79.5%)** for
   coherent labels (p=0.0047). At >=80% the split was **12/23 (52.2%)** versus
   **28/33 (84.8%)**. This is primarily a source-semantics and identity problem,
   not evidence that the match model is wrong on coherent tournaments.
7. **Value Bets are a separate, loss-making population.** They won **28/63
   (44.4%)** with a flat-stake ROI of **-21.0%** at recorded issue prices. This
   week they went **1/6 with -4.2 units (-70.0%)**. Edges above 35pp won
   **6/22 (27.3%)**; issued prices at or above 2.00 won **8/28 (28.6%)**. Large
   claimed edge is behaving as a model-error indicator, not as value.
8. **Data corruption did not cause this week's headline.** No week match has
   conflicting result observations, an identity conflict, or a walkover in the
   official >=60% population. The open repair work in PRs #243/#245/#246
   matters for long-run calibration and for 4 of the 8 losing picks' metadata,
   but it does not move this week's score.
9. **Recommendation:** **READY FOR SHADOW VALIDATION** of the market-
   confirmation gate and the qualification-metadata fix; **HOLD** any
   production probability, Manager, or staking change.

---

## 2. Provenance and safety

| Item | Value |
| --- | --- |
| Repository | `miiiigs/sports-edge-tennis` |
| Research branch | `research/tennis-weekly-accuracy-audit-20261021` |
| Base | `origin/main` at `b6b7e50` |
| Prior research | PR #247 (failure forensics), PR #239 (A2 lookback), PR #248 (A2 calibration), PR #234 (filter monitor) |
| Population | Forward-issued live Manager picks, 2026-09-09 to 2026-10-21 |
| Extract cutoff | `2026-10-21T03:30:00Z` |
| Extract completed | `2026-10-21T04:06:27Z` |
| Dashboard refresh | `2026-10-21T15:21:43Z` |
| Frozen rows | 973: 346 Manager, 553 A1/A2 tracking, 67 Value Bets, 7 Veto |
| File SHA-256 | `a6b1331d1d051abecae2fcf9fdbd6d37402c73db4b0ea6d9a9bd658d484341e4` |
| Extractor row SHA-256 | `0578947a8e76b06006aeb6044c47769a9dbe828dd9f6f5e0deabd6c3be92a759` |

The database session used `SET default_transaction_read_only = on`. The frozen
dataset is intentionally not committed. The extractor and analysis are
reproducible from the commands in `README.md`.

The Railway SSH endpoint was unavailable for most of this audit because of the
2026-10-21 Railway outage. The settled Manager population was unaffected: all
17 scored Manager picks were already settled at the extract cutoff, and the
remaining three were still in progress at the dashboard refresh. The aligned
headline was refreshed through the authenticated read-only dashboard API.

---

## 3. Denominators and settlement policy

**Official Manager Model Pick**

1. `manager_status = CONSENSUS`, selected side 1 or 2.
2. Production contract `predictive_agent_result_v1`, completed and finalized
   run, `experiment_id IS NULL`.
3. The **first** such evaluation per canonical `match_id`; later hourly reruns
   never replace it.
4. `consensus_probability >= 0.60`.
5. Point-in-time match version visible at the pick; pick and finalization
   precede the match start.
6. Scored only when the latest canonical result is `COMPLETED` with
   `winner_side IN (1, 2)`.
7. `RETIRED`, `WALKOVER`, and `CANCELLED` are **VOID** and excluded from the
   accuracy denominator. `PENDING` and `CONFLICTED` are also excluded and
   reported separately.

Win rate is `Wins / (Wins + Losses)`. It is never `wins / all picks`.

**Aligned headline (dashboard)**

The earliest qualifying evaluation per match across Manager `CONSENSUS >=60%`
and A1/A2 same-side tracking where A3 abstained and the Manager rejected only
for spread. Tracking rows are labelled `A1_A2_WIDE_SPREAD` and never become
Value Bets. The dashboard API reports
`includes_a1_a2_wide_spread_tracking: true`.

**Value Bets**

Separate denominator. ROI is a flat one unit at the recorded issue price on
settled bet records only. Guide Odds are not issue prices.

---

## 4. This week's actual results

### 4.1 Official Manager population (Asia/Manila)

| Metric | Value |
| --- | ---: |
| First-qualified Manager picks at >=60% | 20 |
| Settled (won + lost) | 17 |
| Wins | 9 |
| Losses | 8 |
| Accuracy | **52.9%** |
| 95% Wilson interval | 31.0%-73.8% |
| Mean predicted probability | 69.7% |
| Calibration gap | **-16.7pp** |
| Brier score | 0.2799 |
| Log loss | 0.7734 |
| ECE | 0.1672 |
| Pending | 3 |
| Void | 0 |
| Conflicted | 0 |
| Best case (all pending win) | 60.0% |
| Worst case (all pending lose) | 45.0% |

Manager first evaluations at **all** probabilities: 37 picks, 31 settled,
16-15 (51.6%), 5 pending, 1 cancelled.

### 4.2 Dashboard-aligned population

| Snapshot | Picks | Settled | W-L | Accuracy | Pending | Void | Best case |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Frozen extract, Manila window | 88 | 63 | 38-25 | 60.3% | 20 | 5 | 69.9% |
| Dashboard refresh 15:21Z, UTC 19-21 | 88 | 67 | 42-25 | **62.7%** | 16 | 5 | 69.9% |

The aligned ledger contains 19 Manager picks and 69 tracking picks. The Manager
subset was 8-8 (50.0%); the tracking subset was 30-17 (63.8%) at the extract
cutoff and 34-17 after the refresh.

### 4.3 Dashboard rolling windows (UTC, 15:21Z snapshot)

| Population | 7 days | 30 days | All time |
| --- | --- | --- | --- |
| Aligned headline | 89/133 = 66.9% | 373/522 = 71.5% | 475/673 = 70.6% |
| Manager, all probabilities | 34/54 = 63.0% | 151/233 = 64.8% | 204/314 = 65.0% |

The headline 70% all-time figure is driven by the inclusion of tracking rows.
The genuine Manager regime has been a ~65% population, not a ~70% one.

---

## 5. Dashboard-versus-source reconciliation

Five distinct discrepancies explain why Brian's visible number and a
Manager-only reconstruction can disagree:

1. **Population mix.** The aligned headline includes 69 tracking-only picks in
   this window. It is explicitly labelled as including A1/A2 tracking, but a
   reader can easily treat it as Manager accuracy. The Manager subset was 8-8.
2. **Window length.** "Last 7 days" includes 2026-10-15 to 2026-10-18, when the
   aligned ledger went 49-20 (71.0%). The Monday-to-date window is materially
   worse.
3. **Timezone.** The dashboard defaults to UTC. The reporting window here is
   Asia/Manila; matches near local midnight move between days and change daily
   counts by a few picks.
4. **Result lag.** The dashboard hit rate excludes pending and void. At the
   extract cutoff the aligned three-day population had 20 pending; after the
   refresh it still had 16. Different refresh times legitimately produce
   different hit rates on a small denominator.
5. **API population mismatch.** In the same
   `/api/dashboard/model-pick-results` response, `days` is built from the
   original Manager all-probability ledger while `picks` is built from the
   aligned detail ledger. For 2026-10-21 the response returns `days.picks = 6`
   and 16 detail `picks`. Both are internally correct but they describe
   different populations. **This should be fixed in a separate presentation
   change**; it is a reporting defect, not a scoring defect.

None of these discrepancies changes the conclusion: all three published views
are below 70% for the week.

---

## 6. Losing-prediction forensics

### 6.1 The eight official losses

| Match | Date | Tournament | Pick | Prob | Result | Model - sharp | Markers |
| --- | --- | --- | --- | ---: | --- | ---: | --- |
| 132404 | 10-19 | Guangzhou Q1 | K. Samrej | 86.9% | 0-2 | **+33.8pp** | label/flag mismatch, Value Bet, 80-90 band |
| 132355 | 10-19 | Florence Q R32 | J. Forejtek | 60.7% | lost 2-0 | +2.4pp | label/flag mismatch, low confidence |
| 132358 | 10-19 | Florence Q R32 | G. Cadenasso | 63.0% | lost 2-0 | -1.8pp | label/flag mismatch, low confidence |
| 132428 | 10-19 | Florence Q Q1 | D. Popko | 62.1% | 0-2 | +13.5pp | 103-day layoff, zero surface/stat history, Vetoed |
| 132383 | 10-19 | Cali R32 | G. Villanueva | 76.4% | 1-2 | **+20.3pp** | Value Bet at 1.72 |
| 132423 | 10-19 | Fort Worth R32 | E. Winter | 66.8% | 0-2 | **+40.0pp** | Value Bet at 4.00, sharp underdog, 1 prior surface match |
| 132444 | 10-20 | Cali Q1 | A. Hernandez | 72.7% | 0-2 | +11.8pp | Value Bet at 1.57, label/flag mismatch |
| 132488 | 10-21 | Cali | N. Kicker | 67.3% | lost 2-0 | **+34.6pp** | Value Bet at 2.655, sharp underdog |

### 6.2 Classification

The categories overlap; no single cause is confirmed.

| Category | Cases | Evidence |
| --- | --- | --- |
| A. Normal statistical loss | Forejtek, Cadenasso | Low confidence, model within 2.4pp of sharp. These are expected losses. |
| B. Overconfidence | Samrej, Villanueva, Winter, Kicker | 67-87% claims against 20-40pp adverse market disagreement. |
| C. Poor model-market agreement | Samrej, Villanueva, Winter, Hernandez, Kicker, Popko | Positive disagreement of +11.8pp to +40.0pp. |
| D. Tournament metadata defect | Samrej, Forejtek, Cadenasso, Hernandez | Canonical name/flag disagreement in 4 of 8 losses. |
| E. Missing or unreliable player information | Popko, Winter | 103-day layoff with no surface history; 1 prior surface match against a 7-1 surface opponent. |
| F. Identity or result error | none | No conflicting observations; no walkover; no duplicate-match association in the official population. |
| G. Calibration issue | Samrej | The 80-90% band is the overconfident band; the single week pick there lost. |

Aggregate loss markers:

- **7 of 8** losses were above the sharp implied probability; **6 of 8** were
  above it by more than 10pp and **4 of 8** by 20pp or more.
- **4 of 8** losses were Value Bets; all four lost. The week's only winning
  Value Bet was Moller (+13.1pp).
- **2 of 8** losses were sharp underdogs (Winter, Kicker). Across the full
  population, sharp-underdog picks won **10/23 (43.5%)**.
- **4 of 8** losses carried the qualification label/flag mismatch.
- **0 of 8** had a result-observation conflict or identity defect.
- The Veto blocked two relevant picks: Popko (lost, correctly blocked) and
  Kukushkin (won, so the Veto cost a win). Week Veto net was neutral; all-time
  Veto cohort is 4/7.

This is consistent with PR #247: the failures are concentrated in
market-disagreement and metadata-mismatch cohorts, not spread uniformly.

---

## 7. Confidence and calibration

Full Manager >=60% population, 212 settled:

| Band | N | W-L | Accuracy | Mean p | Gap | Brier | Log loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 60-70% | 98 | 67-31 | 68.4% | 64.2% | +4.2pp | 0.2220 | 0.6367 |
| 70-80% | 58 | 46-12 | 79.3% | 74.4% | +4.9pp | 0.1644 | 0.5107 |
| 80-90% | 41 | 28-13 | 68.3% | 84.3% | **-16.0pp** | 0.2416 | 0.7026 |
| 90-100% | 15 | 12-3 | 80.0% | 93.7% | **-13.7pp** | 0.1707 | 0.5428 |
| **All** | **212** | **153-59** | **72.2%** | **72.9%** | **-0.8pp** | **0.2064** | **0.6083** |

ECE is 0.0933 overall, but the 80%+ region is badly overconfident. This is not
a global calibration failure: the 60-80% bands are slightly underconfident.
A blanket calibration layer would overcorrect the bands that are already
performing and undercorrect the one that is not.

At >=80%, the split is conditional:

- coherent tournament label + pre-pick sharp: **16/17 (94.1%)**;
- coherent label, no pre-pick sharp: **12/16 (75.0%)**;
- mismatch + pre-pick sharp: **8/13 (61.5%)**;
- mismatch + no pre-pick sharp: **4/10 (40.0%)**.

Week 80-90%: 1 pick, 0 wins (Samrej). Week 70-80%: 7 picks, 5 wins (71.4%);
week 60-70%: 9 picks, 4 wins (44.4%).

---

## 8. Value Bets (separate denominator)

All-time, forward-issued:

| Metric | All Value Bets | Published subset |
| --- | ---: | ---: |
| Picks | 67 | 52 |
| Settled | 63 | 49 |
| Wins | 28 | 22 |
| Accuracy | **44.4%** | **44.9%** |
| Mean predicted probability | 70.3% | - |
| Flat-stake profit | -13.21u | -9.12u |
| Flat-stake ROI | **-21.0%** | **-18.6%** |

By claimed edge and issued price:

| Slice | Settled | W-L | Accuracy | ROI |
| --- | ---: | ---: | ---: | ---: |
| Edge <= 20pp | 25 | 13-12 | 52.0% | -18.6% |
| Edge 20-35pp | 16 | 9-7 | 56.3% | -4.0% |
| Edge 35-60pp | 11 | 3-8 | 27.3% | -41.7% |
| Edge > 60pp | 11 | 3-8 | 27.3% | -30.4% |
| Issued price < 2.00 | 35 | 20-15 | 57.1% | -10.2% |
| Issued price >= 2.00 | 28 | 8-20 | 28.6% | -34.4% |
| Edge > 35pp and price >= 2.00 | 20 | 5-15 | 25.0% | -39.3% |

This week: 1/6, -4.2 units (-70.0%). Every losing Value Bet had a +11.8pp to
+40.0pp model-over-sharp gap. Even the 20-35pp band is only break-even at best.
The Value Bet selector is systematically buying overstated edge.

**Do not pool this with Manager accuracy.** The 44.4% Value Bet rate and the
52.9% Manager rate are different questions with different denominators.

---

## 9. Model errors versus data errors

Reviewed open work:

| PR | Purpose | Status |
| --- | --- | --- |
| #243 | Historical identity and result repair audit, pending-result diagnostics | Draft, checks green |
| #245 | Approved repair preparation, migration 0021 walkover-winner-optional | Draft, checks green |
| #246 | Fail-closed rollback guard for migration 0021 | Draft, checks green |

Findings for this week:

- The official Manager population has **zero** walkovers, zero result-state
  disagreements, and zero identity conflicts. The single cancellation is in the
  all-probability context, not the >=60% population.
- The walkover repair target (match 132211) is **2026-10-16**, outside the
  reporting window, and settlement policy maps it to VOID.
- Duplicate-fixture holds are 2026-09-28 and 2026-10-01, outside the window.
- The 3 pending Manager picks and 16 pending aligned picks are **result-lag**
  cases, not corrupt results.
- The metadata defect affecting 4 of 8 losses is consistent with the audit in
  PR #243 but is a source-semantics issue, not a mis-settled result.

Therefore data errors do **not** explain the week's low rate. They do inflate
long-run overconfidence in qualification events and must be fixed before the
model's probability reliability can be judged on those cohorts. No repair was
applied here; any repair requires the separate owner-approved procedure in
PR #245 and the rollback guard in #246.

---

## 10. Three strongest improvement hypotheses

### H1 - Market-confirmation gate (strongest)

**Rule:** do not issue a Manager pick when the model is more than 20pp above
the pre-pick sharp implied probability.

**Evidence:**

- rejected cohort 9/24 = 37.5% versus kept cohort 144/188 = 76.6%;
  Fisher p=0.00017;
- monotonic: >+20pp 37.5%, +10-20pp 64.3%, within +/-10pp 83.1%, model below
  sharp by >10pp 92.3%;
- chronological holdout (Oct 13-21): kept 33/43 = 76.7% (+8.1pp), coverage
  84.3%;
- this week the gate removes 4 losses and 1 win; kept 8/12 = 66.7%.

**Mechanism:** a large positive gap is adverse selection, not independent
insight. It is the same mechanism that makes Value Bets lose.

**Caveat:** the 20pp threshold was observed in the same research window. It
must be shadowed forward before it becomes a rule.

### H2 - Tournament qualification metadata reconciliation

**Rule:** fix the canonical name/flag semantics at ingestion and display; while
unfixed, defer picks whose label and flag disagree.

**Evidence:**

- mismatch 52/85 = 61.2% versus coherent 101/127 = 79.5% (p=0.0047);
- at >=80%: 12/23 = 52.2% versus 28/33 = 84.8%;
- mismatch Brier 0.2754 versus 0.1602 coherent;
- 4 of this week's 8 losses carry the mismatch.

**Mechanism:** the model receives or is evaluated against an event whose round
and qualification status are misdescribed. This is a data-contract defect; it
should be corrected rather than learned around.

**Caveat:** the mismatch is partly a proxy for low-tier qualifying events with
thinner data. Fixing the label alone may not fix the accuracy. The audit must
compare the same tournaments before and after repair.

### H3 - Cap implausible Value Bet edge and long price

**Rule:** do not issue a Value Bet when claimed edge exceeds 35pp or the
required price is at or above 2.00 without an independent market confirmation.

**Evidence:**

- edge >35pp: 6/22 = 27.3%, ROI -36.0%;
- price >=2.00: 8/28 = 28.6%, ROI -34.4%;
- both: 5/20 = 25.0%, ROI -39.3%;
- all Value Bets: 28/63 = 44.4%, ROI -21.0%.

**Mechanism:** the edge calculation is using stale or non-contemporaneous
prices, or the model probability is wrong; either way, a very large edge is a
warning sign.

**Caveat:** this is a betting-safety rule, not a Manager accuracy filter. It
must not be used to hide losses from the accuracy ledger.

### Rejected hypotheses

- **A1/A2 spread gate:** spread >=0.05 won 32/42 = 76.2%; spread <0.05 won
  121/170 = 71.2%. The gate is not a failure predictor (holdout delta -5.2pp).
- **Thin surface history:** 63/86 = 73.3% versus 90/126 = 71.4%. Not a general
  failure mode; it appears only in individual cases.
- **High confidence without sharp, alone:** full +1.5pp but holdout -1.2pp with
  90% coverage. Not stable.
- **Long inactivity:** rejected as a general rule by PR #247 and not supported
  here; Popko is a case-level exception.
- **Blanket calibration:** the 60-80% bands are already slightly
  underconfident; a global correction would harm them.

---

## 11. Offline tests completed

Common cohort: 212 settled Manager picks at >=60%, same matches for every row.
Chronological holdout: 51 settled picks from 2026-10-13 to 2026-10-21.
Base rates: full 72.2%, holdout 68.6%, week 52.9%.

| Filter | Full kept | Delta | Holdout kept | Delta | Holdout coverage | Week kept | Week rejected |
| --- | --- | ---: | --- | ---: | ---: | --- | --- |
| R1 market >+20pp | 144/188 = 76.6% | +4.4pp | 33/43 = 76.7% | +8.1pp | 84.3% | 8/12 = 66.7% | 1/5 = 20.0% |
| R2 qualification mismatch | 101/127 = 79.5% | +7.4pp | 25/34 = 73.5% | +4.9pp | 66.7% | 6/10 = 60.0% | 3/7 = 42.9% |
| R3 high conf, no sharp | 137/186 = 73.7% | +1.5pp | 31/46 = 67.4% | -1.2pp | 90.2% | 9/17 = 52.9% | no rejects |
| R4 combined | 84/98 = 85.7% | +13.5pp | 20/24 = 83.3% | +14.7pp | 47.1% | 5/6 = 83.3% | 4/11 = 36.4% |
| C1 spread >=0.05 (control) | 121/170 = 71.2% | -1.0pp | 26/41 = 63.4% | -5.2pp | 80.4% | 8/15 = 53.3% | 1/2 = 50.0% |
| C2 thin surface (control) | 90/126 = 71.4% | -0.7pp | 31/44 = 70.5% | +1.8pp | 86.3% | 8/13 = 61.5% | 1/4 = 25.0% |

Uncertainty:

- R1 holdout 33/43: 95% CI 62.3%-86.9%.
- R2 holdout 25/34: 95% CI 56.9%-85.4%.
- R4 holdout 20/24: 95% CI 64.2%-93.3%.
- R4 week 5/6: 95% CI 43.7%-97.0% - far too small to act on alone.

R4 is the strongest combined candidate, but it retains only 47% of the
holdout. It is not a production rule until both components pass independent
shadow tests.

---

## 12. Follow-up experiments with predeclared success criteria

All experiments are read-only shadow measurements. No live betting, stake,
probability, publication, or Manager change.

**E1 - Market-confirmation gate (H1)**

- Population: next 100 settled first-qualified Manager picks at >=60% from
  2026-10-22.
- Compare kept vs unguarded on the same matches.
- Success: kept accuracy >= 76%, coverage >= 80%, delta vs unguarded >= +4pp,
  calibration gap within +/-8pp.
- Stop: coverage < 75% or delta <= 0 after 100 picks.
- Report accuracy, Brier, log loss, ECE, and counts pending/void separately.

**E2 - Qualification metadata repair (H2)**

- Scope: next 100 qualification-flagged events (pilot tournaments, not a
  global backfill).
- First run the identity audit; then apply only approved append-only
  corrections.
- Success: mismatch rate < 2% in the pilot, mismatch cohort accuracy within
  5pp of coherent, no change to any settlement outcome.
- Stop: any result or identity conflict, or any repair that cannot be reverted.

**E3 - Value Bet edge/price cap (H3)**

- Population: next 50 settled Value Bets at recorded issue prices.
- Tag or block edge >35pp and price >=2.00; keep the full ledger for audit.
- Success: kept accuracy >= 50% and kept ROI > -5%.
- Stop: kept sample <20 after 60 days, or kept ROI <= -15%.
- This experiment never uses Guide Odds.

**E4 - A3 coverage restoration (structural, optional)**

- Measure the share of 3/3 consensus picks after any A3 support improvement.
- Success: >=10% of Manager picks are 3/3 and their accuracy exceeds 2-agent
  picks by >=3pp over 100 settled picks.
- Stop: no material 3/3 coverage after 30 days.

**E5 - Combined rule (only after E1 and E2 pass)**

- Predeclare R4 on the next 100 settled picks, with the same coverage floor
  (>=45%) and a delta >= +8pp.
- Never evaluate R4 on the same window that selected its thresholds.

---

## 13. Implementation and PR status

Implemented in this branch:

- `research/tennis_weekly_accuracy_audit_20261021/dataset.py` - typed frozen
  loader and settlement policy.
- `metrics.py` - accuracy, Wilson intervals, Brier, log loss, ECE, bootstrap.
- `reconciliation.py` - population reconciliation, aligned reconstruction,
  Value Bet ROI, subgroup tables, chronological holdout.
- `filters.py` - six predeclared filters, including two negative controls.
- `run_audit.py` - CLI and Markdown/JSON artifacts.
- `artifacts/week_reconciliation.json` and `artifacts/tables.md`.
- `tests/test_tennis_weekly_accuracy_audit.py` - 8 synthetic tests.

Validation:

```text
pytest tests/test_tennis_weekly_accuracy_audit.py -q   8 passed
ruff check research/... tests/...                     All checks passed
ruff format --check research/... tests/...            clean
```

Full-suite status is reported in the PR after push; the new code does not
touch production modules.

---

## 14. Readiness

| Item | Status |
| --- | --- |
| Weekly reconciliation | **READY** |
| Failure forensics | **READY** |
| H1 market gate | **READY FOR SHADOW VALIDATION** |
| H2 qualification metadata fix | **READY FOR AUDIT / SHADOW; repairs require separate approval** |
| H3 Value Bet cap | **READY FOR SHADOW; not a Manager accuracy rule** |
| R4 combined rule | **HOLD until E1 and E2 pass** |
| Production probability, Manager, staking, or schema change | **HOLD** |
| Database repair or backfill | **HOLD - separate owner-approved procedure** |

Final classification: **READY FOR SHADOW VALIDATION** for the read-only
experiments, with production changes on **HOLD**.

---

## 15. Limitations

1. **Small short-window sample.** 17 settled official picks cannot distinguish
   a 60% process from a 70% process; the confidence interval is 24pp wide.
2. **Threshold discovery overlap.** PR #247 and this audit use the same live
   window. The chronological holdout is a stability check, not a clean
   out-of-sample discovery.
3. **Metadata proxy.** Label/flag mismatch correlates with tier and round. The
   repair experiment must separate the metadata effect from event difficulty.
4. **Single-provider result truth.** Most matches have one API-TENNIS result
   observation. There is no independent second-provider verification, although
   no conflicting observation exists.
5. **Pending results.** The 3 Manager and 16 aligned pending matches were still
   unsettled at the refresh. The official conclusion is robust to them: even
   all pending wins leave the Manager at 60.0% and aligned at 69.9%.
6. **Railway outage.** The production SSH lane was unavailable for most of the
   audit; the frozen extract and authenticated dashboard API were used instead.
   Re-run the extractor when SSH is restored to confirm no new settled results.
7. **ROI scope.** ROI uses flat stakes and recorded issue prices only, on
   settled Value Bets. It is not a bankroll simulation and not a CLV estimate.
8. **No causal claims.** The loss categories are empirical markers. Only the
   shadow experiments can establish that a rule improves future accuracy.
