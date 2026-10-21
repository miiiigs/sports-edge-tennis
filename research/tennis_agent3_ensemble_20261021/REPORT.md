# Tennis Agent 3 Recovery and Multi-Agent Model Improvement — Lane 4 Report

- **Experiment:** `tennis_agent3_ensemble_20261021`
- **Draft PR:** https://github.com/miiiigs/sports-edge-tennis/pull/252
- **Branch:** `research/tennis-agent3-ensemble-20261021`
- **Base:** PR #248 head `a88dcd457cac271d1119c091d002eea4c4f49e9d`
  (verified from `git ls-remote`)
- **Starting main:** `b6b7e506687172a0b08441bb006013d131a19fcd`
- **Reference PR #247 head:** `b9bdacdbeb32f911b784ce1044d918df717502f2`
- **Reference PR #239 head:** `eec591921367cd81736e9feba1b52011657f1512`
- **Reference PR #197 head:** `3ff0b2a6095534c6f1846d910fbb002c9b1c46ef`
- **Data cutoff:** `2026-10-21T23:59:59+00:00`
- **Source access:** read-only PostgreSQL session
  (`SET default_transaction_read_only = on`)
- **Production activation:** prohibited and not performed
- **Manager thresholds, A1/A2 weights, betting logic, risk and stakes:** unchanged
- **Lane 1 / Lane 2 dashboard recovery branches:** untouched

**Decision: HOLD. Do not activate any model change. Do not accept Agent 3 as
an independent third check on current evidence. Continue only observational
shadow collection; the A2 calibration candidate fails its pre-registered
decision-quality guardrails on the live ensemble.**

---

## 1. Executive summary

1. **Agent 3 is not rare because of an identity or pipeline failure.** In the
   8,583 support diagnostics available since 2026-10-08, every identity was
   marked `RESOLVED`. The dominant cause is deliberate direct-matchup support
   gating: 66.1% of evaluations had **zero** prior head-to-head observations,
   19.1% had exactly one, and 14.3% had two or more but a decay-weighted
   effective count below the locked 2.5 threshold.
2. **Unique-match coverage is worse than the evaluation-level headline.**
   Agent 3 made 172 predicted evaluation rows, but these are reruns over only
   **10 unique matches out of 2,259 evaluated (0.4427%)**. PR #247's 0.56%
   figure was evaluation-level, not match-level.
3. **The direct-support gates are internally inconsistent.** The raw gate
   requires at least 2 prior H2H matches, while the effective gate requires at
   least 2.5 decay-weighted observations. Two observations can never reach 2.5
   because each temporal weight is at most 1.0. A raw count of exactly 2 is
   therefore not actionable; a pair needs at least 3 retained observations.
   This is a design/config inconsistency, not a data shortage alone.
4. **The network-only rescue path does not rescue.** 170 evaluations passed the
   structural network-only gate, but 153 still failed later signal/probability
   gates; no `NETWORK_ONLY` prediction appears in the diagnostics. Shared
   opponents are often visible in the graph, but the eligible shared-opponent
   count is zero in 57.0% of evaluations and below 5 in 89.5%.
5. **Agent 3 did not demonstrate incremental predictive value.** It predicted
   only 10 unique matches. On the 9 scored matches it went 7-2 (77.8%, Wilson
   45.3-93.7%), Brier 0.2299 and log loss 0.6528. On the same matches Agent 1
   had 88.9% accuracy, Brier 0.1346 and log loss 0.4331; Agent 2 had 85.7%,
   Brier 0.1327 and log loss 0.4435. Agent 3 was materially worse.
6. **Agent 3 was directionally identical to Agent 1 in every three-active
   unique match (8/8; and in all 140 rerun evaluations of those matches).** It
   never dissented from an A1/A2 agreement. Its claimed independence is not
   supported by the observed data. Whether this is model convergence or a
   shared-orientation/data artifact cannot be resolved without A3 input
   snapshots.
7. **The three-agent Manager path is structurally blocked.** All 8 unique
   three-active Manager rows were `NO_BET` (5 spread, 3 directional
   disagreement). The minimum three-agent spread was 0.08756, already above the
   locked 0.08 gate, and the median was 0.1526. Agent 3's probabilities are
   compressed into roughly 0.53-0.65 while A1/A2 often exceed 0.70, so the
   0.08 spread gate can reject 3/3 agreement even when all sides match.
8. **Agent 2 temperature calibration has a real but insufficient probability
   benefit and a negative decision-quality effect on the live ensemble.**
   Applying the frozen PR #248 slope to Agent 2 only:
   - Manager consensus coverage falls from 308 to 262 matches (-46, -14.9%);
   - 147 existing consensuses are lost and 101 new ones appear;
   - selected sides never change (0);
   - the 147 lost picks had a 77.4% baseline win rate; the 101 new picks had
     60.0% accuracy;
   - on the 145 scored common-population picks, Brier changes from 0.237924 to
     0.236925 (delta -0.000999, 95% CI -0.004550 to +0.002350) and log loss
     from 0.669553 to 0.665959 (delta -0.003594, CI -0.014581 to +0.005619);
   - ATP Tour common-population Brier **worsens** by +0.009484 (95% CI
     +0.004992 to +0.014049), which violates the pre-registered subgroup
     guardrail.
9. **No production change is justified.** The evidence supports a narrow
   probability-calibration observation, not a winner-selection or coverage
   improvement. The three-agent design cannot be evaluated as intended because
   the third agent is effectively absent and, when present, is not independent.

---

## 2. What was verified before proceeding

`git ls-remote` on 2026-10-21 returned:

| Ref | Head |
| --- | --- |
| `refs/pull/197/head` | `3ff0b2a6095534c6f1846d910fbb002c9b1c46ef` |
| `refs/pull/239/head` | `eec591921367cd81736e9feba1b52011657f1512` |
| `refs/pull/247/head` | `b9bdacdbeb32f911b784ce1044d918df717502f2` |
| `refs/pull/248/head` | `a88dcd457cac271d1119c091d002eea4c4f49e9d` |
| `origin/main` | `b6b7e506687172a0b08441bb006013d131a19fcd` |

PR #248 remains the approved standalone Agent 2 calibration experiment. This
work reuses its frozen temperature slope (`0.6439254782294896`) and does not
refit or repeat the standalone experiment.

Because the shared worktree was concurrently switched to another lane's branch,
Lane 4 was moved to a separate git worktree at
`C:\dev\sports-edge-tennis-lane4-20261021`. No Lane 1/Lane 2 file was modified.

Frozen extraction manifest (raw JSONL intentionally not committed):

| File | Rows | SHA-256 |
| --- | ---: | --- |
| `a3_evaluations.jsonl` | 31,121 | `510e7879aa6b49a818718779ce0bbfbee709076fc6f80311be8d42d4f1bae5e8` |
| `a3_diagnostics.jsonl` | 8,583 | `07b1c7157166fcbd27442bd7e0efd1a76ccf2224da38f2b0207097d8a7cc74b5` |
| `ensemble_rows.jsonl` | 2,259 | `728392f6ab5ebbfcdc62b943ea37d78d421b6b00bb6490fa18b295d75dec1f0e` |

Extraction UTC: `2026-10-21T15:29:48Z`. The extractor is deterministic for the
same cutoff; reruns after new production rows will change the hashes.

---

## 3. Agent 3 root-cause investigation

### 3.1 Code path and gates

`src/sports_edge_tennis/agents/agent3_intransitivity.py`:

1. `predict()` abstains immediately on `surface is None` (`UNKNOWN_SURFACE`),
   unresolved PIT state (`PIT_STATE_UNRESOLVED`) or `supported_matchup=False`
   (`UNSUPPORTED_OR_UNKNOWN`).
2. It evaluates an all-surface and a surface-specific bucket.
3. A bucket becomes direct-supported only when both conditions hold:
   - `raw_count >= direct_raw_support` (locked at 2);
   - `effective_count >= direct_effective_support` (locked at 2.5).
4. If no bucket is direct-supported, a network-only candidate requires:
   - `eligible_shared_opponents >= 8`; and
   - `q_total >= 16`.
5. Direct-supported candidates must also pass:
   - `abs(final_score) >= signal_floor` (0.12);
   - selected probability `>= activation_probability_floor` (0.53);
   - no `CONFLICTING_DIRECT_NETWORK`;
   - no quantized 0.500000.
6. Network-only candidates must pass a higher 0.16 signal floor and the same
   0.53 activation floor.

The Manager then applies the existing v1 policy: all active sides must agree,
the three-agent spread must be <= 0.08, or the Agent 3 abstention spread must be
<= 0.06.

### 3.2 Measured coverage

Source: `artifacts/analysis.json` and `artifacts/tables.md`.

| Metric | Value |
| --- | ---: |
| A3 evaluation rows (hourly reruns included) | 31,121 |
| Unique matches evaluated | 2,259 |
| Unique matches with an A3 prediction | 10 |
| Unique-match coverage | 0.4427% |
| First evaluation as_of | 2026-09-08T02:01:09Z |
| Last evaluation as_of | 2026-10-21T12:04:24Z |
| Seasons represented | 2026 only |

Coverage by surface (unique first evaluation per match):

| Surface | Evaluated | Predicted | Coverage |
| --- | ---: | ---: | ---: |
| HARD | 907 | 6 | 0.66% |
| CLAY | 765 | 3 | 0.39% |
| Missing surface | 587 | 1 | 0.17% |

Coverage by tier (unique first evaluation per match):

| Tier | Evaluated | Predicted | Coverage |
| --- | ---: | ---: | ---: |
| ATP_TOUR | 493 | 5 | 1.01% |
| CHALLENGER | 1,746 | 5 | 0.29% |
| ATP_250 | 20 | 0 | 0.00% |

The largest tournament samples were Shanghai 2026 (1/145), Porto 2026 (1/53)
and Szczecin 2026 (1/50). The 10 predictions were spread over 9 tournaments;
only Tokyo 2026 produced two.

### 3.3 Abstention reason counts

Evaluation level (all 31,121 rows):

| Status / reason | Count | Share |
| --- | ---: | ---: |
| `ABSTAINED` with NULL reason (pre-0017 or not captured) | 18,554 | 59.62% |
| `ABSTAINED` / `INSUFFICIENT_MATCHUP_SUPPORT` | 11,855 | 38.09% |
| `ABSTAINED` / `UNKNOWN_SURFACE` | 514 | 1.65% |
| `ABSTAINED` / `WEAK_SIGNAL` | 26 | 0.08% |
| `PREDICTED` | 172 | 0.55% |

Unique-match first evaluation:

| Status / reason | Count |
| --- | ---: |
| `ABSTAINED` with NULL reason | 1,318 |
| `ABSTAINED` / `INSUFFICIENT_MATCHUP_SUPPORT` | 899 |
| `ABSTAINED` / `UNKNOWN_SURFACE` | 31 |
| `ABSTAINED` / `WEAK_SIGNAL` | 1 |
| `PREDICTED` | 10 |

The NULL bucket is a data-capture limitation, not evidence of a different
cause. Migration 0017 added the reason code after much of the live history;
2,433 older consensus abstentions also have a blank reason per PR #247.

### 3.4 Support diagnostics (2026-10-08 to 2026-10-21)

8,583 evaluation rows over 642 matches; 36 predicted evaluations over 2 unique
matches.

| Final status / gate | Count |
| --- | ---: |
| `ABSTAINED` / `INSUFFICIENT_MATCHUP_SUPPORT` | 8,021 |
| `ABSTAINED` / `UNKNOWN_SURFACE` | 514 |
| `ABSTAINED` / `WEAK_SIGNAL` | 12 |
| `PREDICTED` | 36 |

Identity status: `RESOLVED` 8,583; `LIVE_UNRESOLVED` 0. The diagnostic's
definition is weak in one direction: it marks `RESOLVED` if *either* player is
present in the snapshot, so it does not prove both identities are correct.

Direct/network gate decomposition:

| Cohort | Evaluations |
| --- | ---: |
| Direct raw support = 0 | 5,670 (66.1%) |
| Direct raw support = 1 | 1,638 (19.1%) |
| Direct raw >= 2 but effective < 2.5 | 1,227 (14.3%) |
| Direct gate pass | 48 (0.56%) |
| Network normal support (direct pass + >=5 eligible shared) | 48 |
| `DIRECT_PLUS_NETWORK` | 48 |
| `DIRECT_ONLY` | 0 |
| Eligible shared opponents = 0 | 4,890 (57.0%) |
| Eligible shared opponents 1-4 | 2,802 (32.6%) |
| Eligible shared opponents 5-7 | 577 (6.7%) |
| Eligible shared opponents >= 8 | 314 (3.7%) |
| Network-only structural gate pass | 170 (2.0%) |
| Network-only structural pass but not predicted | 153 |

The 8,021 `INSUFFICIENT_MATCHUP_SUPPORT` diagnostics split exactly as:

| Cohort | Count |
| --- | ---: |
| Direct raw = 0 | 5,156 |
| Direct raw = 1 | 1,638 |
| Direct raw >= 2 but effective < 2.5 | 1,227 |

No other support explanation is needed for the insufficient-support bucket.

### 3.5 The 2 vs 2.5 inconsistency

This is the clearest correctable design issue found:

- `direct_raw_support = 2`;
- `direct_effective_support = 2.5`;
- every temporal weight is <= 1.0.

Two observations therefore have effective support <= 2.0, strictly below 2.5.
The diagnostic data contains 819 evaluations with exactly 2 raw direct
observations and only 456 with 3 or more. The raw=2 boundary is effectively
dead code: the model requires at least 3 retained observations in practice.

This does **not** justify lowering the threshold in production. It means the
locked configuration does not describe the intended "two prior matches" rule,
and any correction must be pre-registered and evaluated as a separate
experiment.

### 3.6 Classification: legitimate versus correctable

| Cause | Classification | Evidence |
| --- | --- | --- |
| No prior H2H in 1,460-day lookback | Legitimate insufficient data | 5,670 diagnostics; 5,156 of insufficient bucket |
| Only one prior H2H | Legitimate insufficient data | 1,638 diagnostics |
| Two prior H2H but decayed effective support < 2.5 | Correctable gate inconsistency | 1,227 diagnostics; 2.0 maximum with two observations |
| Sparse eligible shared-opponent graph | Legitimate sparse network | 57.0% zero eligible shared opponents; 89.5% below 5 |
| Missing PIT surface | Data defect, upstream | 514/8,583 diagnostics (6.0%); 31/2,259 unique first evaluations (1.37%) |
| Identity mapping failure | Not observed in diagnostic window | 0 `LIVE_UNRESOLVED`; weaker definition caveat |
| Weak signal after support passes | Intended safety behavior | 12 diagnostics; 26 evaluation rows |
| Pipeline failure | Not observed | Predictions and diagnostics were written for every evaluated match |
| Historical 2019-2025 A3 coverage | Unavailable, not measured | No frozen historical A1/A3 prediction population |

The root cause is therefore **primarily legitimate sparsity, amplified by a
raw/effective threshold inconsistency and a network-only path whose later
signal gate rejects almost everything that structurally qualifies**. Identity
and pipeline failures are not supported by the evidence.

---

## 4. Agent 3's real contribution

### 4.1 Standalone performance

10 unique predicted matches, 9 scored, 7 wins.

| Metric | Agent 3 | Agent 1 on same matches | Agent 2 on same matches |
| --- | ---: | ---: | ---: |
| Scored | 9 | 9 | 7 |
| Wins | 7 | 8 | 6 |
| Accuracy | 77.8% | 88.9% | 85.7% |
| Accuracy 95% Wilson | 45.3-93.7% | n/a | n/a |
| Brier | 0.2299 | 0.1346 | 0.1327 |
| Log loss | 0.6528 | 0.4331 | 0.4435 |
| ECE (0.5-1.0) | 0.2364 | 0.2283 | 0.3498 |

The sample is far too small for a precise accuracy estimate; the confidence
interval spans coin-flip to near-certainty. More importantly, Agent 3's
probabilities are compressed near 0.54 while it won 7/9, so its confidence is
not informative at the level needed for a third check. Its Brier and log loss
are materially worse than either active agent on the same matches.

### 4.2 Directional redundancy with Agent 1

| Check | Result |
| --- | ---: |
| A3 agrees with A1 | 9/10 unique predicted matches |
| A3 agrees with A2 | 5/10 unique predicted matches |
| A3 side equals A1 side in three-active unique rows | 8/8 (100%) |
| A3 dissent when A1/A2 agree | 0 |
| A3 side equals A1 side across all 140 rerun three-active rows | 140/140 |

Across every observed three-active row, Agent 3 voted the same side as Agent 1.
It never acted as an independent tiebreaker or dissent. This is inconsistent
with the intended "independent third check" and is a release blocker for any
three-agent claim until the shared input/orientation dependency is understood.
The current evidence cannot distinguish genuine model convergence from a
shared-data or orientation artifact because A3 input snapshots are not
persisted.

### 4.3 Manager impact

Unique first-Manager-per-match population:

| Manager status | Matches |
| --- | ---: |
| `CONSENSUS` | 308 |
| `NO_BET` | 1,951 |
| Total | 2,259 |

Active-agent distribution: 1 active 1,001; 2 active 1,250; 3 active 8.

All 308 consensuses were two-agent; Agent 3 abstained in every one. Manager
reason counts:

| Reason | Matches |
| --- | ---: |
| `TWO_OR_MORE_ABSTENTIONS` | 1,001 |
| `AGENT3_ABSTENTION_SPREAD_EXCEEDED` | 593 |
| `DIRECTIONAL_DISAGREEMENT` | 350 |
| `NONE` (consensus) | 308 |
| `THREE_AGENT_SPREAD_EXCEEDED` | 5 |
| `UNSUPPORTED_ABSTENTION_CONFIGURATION` | 2 |

Three-active rows:

| Metric | Value |
| --- | ---: |
| Unique matches | 8 |
| Manager status | 8 `NO_BET` |
| Spread reasons | 5 |
| Directional reasons | 3 |
| Minimum spread | 0.08756 |
| Median spread | 0.15260 |
| Maximum spread | 0.39904 |
| Rows with spread <= 0.08 | 0 |

Even when all three sides agree, the 0.08 spread gate rejects the consensus
because Agent 3's probability scale is compressed. The three-agent policy is
not merely under-covered; it is structurally incompatible with the current A3
probability output.

A counterfactual two-agent policy on these 8 unique rows (treating A3 as
abstaining) would have produced:

| A3-abstention spread gate | Scored picks | Wins | Accuracy |
| --- | ---: | ---: | ---: |
| 0.06 | 1 | 1 | 100% |
| 0.08 | 3 | 3 | 100% |

This is a tiny, selected sample and is not evidence that removing A3 improves
the model. It only demonstrates that A3 participation currently suppresses
coverage without adding directional information.

**Conclusion:** no incremental predictive or decision value is demonstrated.
The observed direction is worse standalone performance, perfect directional
redundancy with A1, and zero three-agent consensus.

---

## 5. Integrated A1/A2/A3 shadow design

The implemented shadow preserves every original probability and decision and
records the following for each unique first-Manager-per-match row:

- original A1 status, side and probability;
- original A2 status, side and probability;
- calibrated A2 selected-side probability;
- original A3 status, side and probability, including its abstention reason;
- issued Manager status, side, consensus probability, spread, active count and
  reason;
- hypothetical Manager decision with calibrated A2 under the same policy;
- confidence spread before and after;
- whether the selected side changes;
- whether the Manager qualification gate changes and in which direction;
- the later verified result, scored only after the decision.

`policy.py` mirrors the production v1/v2 policy. A parametrized test compares
it against `sports_edge_tennis.manager.consensus.evaluate_consensus` for every
structural branch, including all-agree, three-agent spread, A3 abstention,
A2/A1 abstention, two abstentions, directional disagreement, probability floor
and v2 abstention configuration.

Leakage prevention:

- the source session is read-only;
- no production row is written;
- the calibration slope is frozen from PR #248, not fitted on live outcomes;
- shadow decisions use only prediction rows and policy rows;
- outcomes are joined only for retrospective scoring;
- a test mutates the outcome and asserts that every decision field is
  unchanged;
- forward collection is append-only and idempotent on
  `(match_id, prediction_run_id)`.

---

## 6. Agent 2 calibration impact on Manager decisions

Population: 2,259 unique first-Manager-per-match rows from the live forward
window; 1,938 have a current verified result. The comparison is a retrospective
impact audit, not a holdout.

### 6.1 Decision flips

| Metric | Baseline | Candidate |
| --- | ---: | ---: |
| Manager consensus | 308 | 262 |
| Coverage retention | 100% | 85.1% |
| Net coverage change | — | -46 (-14.9%) |

| Gate flip | Matches |
| --- | ---: |
| `NONE` | 2,011 |
| `LOST_CONSENSUS` | 147 |
| `NEW_CONSENSUS` | 101 |
| Selected-side changes | 0 |

The lower A2 confidence usually increases the spread when A2 is the minimum
active agent, so calibration loses more consensuses than it creates.

### 6.2 Common scored population

145 scored matches have a consensus under both policies.

| Metric | Baseline | Candidate | Delta | 95% CI |
| --- | ---: | ---: | ---: | --- |
| Accuracy | 57.24% | 57.24% | 0.00 pp | n/a |
| Brier | 0.237924 | 0.236925 | -0.000999 | -0.004550 to +0.002350 |
| Log loss | 0.669553 | 0.665959 | -0.003594 | -0.014581 to +0.005619 |
| ECE | 0.070500 | 0.051685 | -0.018815 | n/a |

The common-population probability improvement is directionally positive but
statistically indistinguishable from zero for Brier and log loss. Accuracy
cannot change on the common population because calibration preserves the
selected side.

### 6.3 Coverage churn

| Group | Scored | Wins | Accuracy | Wilson 95% |
| --- | ---: | ---: | ---: | --- |
| New consensuses | 85 | 51 | 60.0% | 49.2-69.9% |
| Lost consensuses (baseline outcome) | 133 | 103 | 77.4% | 69.7-83.7% |

Calibration removes a materially better-performing set of picks and replaces
them with a lower-accuracy set. The net coverage falls. This is a
decision-quality regression even though the retained probabilities are slightly
better calibrated.

### 6.4 By surface and tier

Paired common population:

| Surface | Common scored | Baseline accuracy | Candidate accuracy | Brier delta | 95% CI | Lost | New |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| HARD | 62 | 51.6% | 51.6% | -0.002228 | -0.007893 to +0.002781 | 72 | 44 |
| CLAY | 74 | 59.5% | 59.5% | -0.000210 | -0.004678 to +0.004217 | 72 | 51 |
| Missing | 9 | 77.8% | 77.8% | +0.000979 | -0.012561 to +0.012377 | 3 | 6 |

| Tier | Common scored | Baseline accuracy | Candidate accuracy | Brier delta | 95% CI | Lost | New |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| ATP_TOUR | 20 | 75.0% | 75.0% | +0.009484 | +0.004992 to +0.014049 | 14 | 11 |
| CHALLENGER | 125 | 54.4% | 54.4% | -0.002677 | -0.006782 to +0.001062 | 133 | 90 |
| ATP_250 | 0 | n/a | n/a | n/a | n/a | 0 | 0 |

ATP Tour is a pre-registered subgroup with n >= 20 and shows a statistically
clear Brier **worsening**. That alone fails the forward acceptance guardrail.

### 6.5 Agent 2 standalone calibration on the live population

278 scored A2 predictions:

| Metric | Baseline | Calibrated |
| --- | ---: | ---: |
| Brier | 0.216522 | 0.218012 |
| Log loss | 0.626767 | 0.626117 |
| ECE | 0.071107 | 0.061387 |
| Predictions >= 0.80 | 146 | 40 |
| Predictions >= 0.90 | 37 | 9 |

The live sample shows ECE and log loss improving slightly, Brier worsening
slightly, and high-confidence display coverage collapsing. This is consistent
with PR #248's finding that the method improves probability honesty but not
winner selection. It is not evidence of a decision-quality improvement.

### 6.6 Cases where calibration worsens

83 of 145 common-population matches have a worse Brier under calibration; 62
improve. The largest individual Brier worsening cases are high-confidence
baseline winners whose calibrated probability is pulled down, for example:

| Match | Baseline p | Calibrated p | Won | Brier delta |
| --- | ---: | ---: | --- | ---: |
| 130725 | 0.681756 | 0.647156 | yes | +0.023219 |
| 117882 | 0.724569 | 0.685240 | yes | +0.023211 |
| 130784 | 0.771512 | 0.726187 | yes | +0.022767 |

The pattern is systematic: when A2 was the minimum active probability,
lowering it widens the Manager spread, removes the pick, and the removed pick
often won. Hidden in an aggregate Brier improvement, this is a coverage and
selection-quality loss.

---

## 7. Implemented and tested research changes

Added under `research/tennis_agent3_ensemble_20261021/`:

- `extract.py` — read-only extraction with cutoff and SHA-256 manifest;
- `analysis.py` — coverage, abstention classification, diagnostics gate
  decomposition, redundancy and contribution metrics;
- `policy.py` — exact research mirror of Manager v1/v2;
- `ensemble_shadow.py` — frozen A2 calibration impact with gate flips,
  coverage churn, common-population metrics, subgroup CIs and worsening cases;
- `forward_shadow.py` — append-only, idempotent forward ledger;
- `artifacts/analysis.json`, `artifacts/tables.md`;
- `artifacts/ensemble_shadow.json`, `artifacts/ensemble_tables.md`;
- `artifacts/preregistration.json`;
- `tests/test_tennis_agent3_ensemble.py`.

No production source, migration, Manager policy, odds, staking or settlement
code was changed.

Validation commands:

```bash
python -m pytest tests/test_tennis_agent3_ensemble.py -q
python -m py_compile research/tennis_agent3_ensemble_20261021/*.py
```

Result: 15 tests passed. The exact code head and test output are recorded in
`VALIDATION.md` once the research commit is created.

---

## 8. Forward shadow monitoring protocol

Do not treat the retrospective impact audit as forward evidence. If this line
of research continues, run a genuinely forward, append-only shadow:

1. Run `extract.py` and `forward_shadow.py` at least daily, or after each
   production prediction run.
2. Start the forward ledger empty; never mix retrospective rows into the
   forward cohort.
3. Record the first collection timestamp and every row's `manager_as_of`.
4. Require at least **8 weeks and 50 scored Manager decisions** before an
   interim read.
5. Report, at minimum:
   - number of new and lost consensuses;
   - selected-side changes;
   - accuracy and Wilson intervals for new, lost and retained picks;
   - paired Brier and log-loss deltas on the common scored population with
     bootstrap confidence intervals;
   - by-surface and by-tier results, with subgroup n;
   - A3 eligibility and abstention reasons;
   - A2 high-confidence display coverage;
   - cases where calibration worsens a pick, with match IDs.
6. Stop or reject if any of the pre-registered triggers fires:
   - any selected-side change without an explicit new policy;
   - paired Brier or log-loss delta upper 95% CI above zero;
   - a subgroup with n >= 30 and Brier delta upper CI above zero;
   - new-consensus accuracy below 0.55;
   - lost-consensus winners are not disclosed.
7. Do not change thresholds, weights, stakes or production writes during the
   shadow. A forward shadow is an observation, not an activation.

For Agent 3 specifically, do not lower support gates to force participation.
First fix the raw/effective inconsistency and the missing-surface data path in
a pre-registered experiment, and add A3 input snapshots so the A1/A3
directional identity can be explained.

---

## 9. Release blockers

1. **A3 unique-match coverage is 0.44% (10 of 2,259).** There is no statistical
   basis for a three-agent claim.
2. **A3's direct raw/effective gate is internally inconsistent.** Raw=2 cannot
   pass effective>=2.5; the model effectively requires >=3 H2H observations.
3. **The 0.08 three-agent spread gate is incompatible with A3's compressed
   probability scale.** All 8 unique three-active rows were `NO_BET`; the
   minimum spread was 0.08756.
4. **A3 is directionally identical to A1 in all observed three-active rows.**
   The independent third check is not demonstrated.
5. **A3 input snapshots are not persisted.** The root cause of the A1/A3
   identity cannot be established from outputs alone.
6. **No frozen historical A1/A3 prediction population exists for 2019-2025.**
   A faithful full-ensemble historical replay is not possible; the evidence is
   the 2026-09-08 to 2026-10-21 forward-issued window.
7. **A3 diagnostics start only on 2026-10-08.** 18,554 evaluation rows and 1,318
   unique matches have a NULL reason. The pre-window reason mix is not
   measurable.
8. **A2 calibration reduces Manager coverage and loses winners.** 147 lost
   consensuses had a 77.4% baseline win rate vs 60.0% for 101 new consensuses.
9. **The ATP Tour subgroup worsens under calibration.** Brier delta +0.009484
   (95% CI +0.004992 to +0.014049), violating the pre-registered guardrail.
10. **The common-population Brier/log-loss improvements are not significant.**
    95% CIs include zero.
11. **P0 bookmaker odds incident isolation remains in force.** No production
    activation, threshold change, paid API experiment, database mutation or
    Lane 1/2 branch change was made.

---

## 10. Limitations

- The live A3 sample is 10 unique matches, 9 scored. Wilson intervals are wide.
- The live Manager sample is 2,259 unique matches and 1,938 scored, but only
  145 common-population consensuses are available for a paired A2 calibration
  comparison.
- The retrospective calibration impact is not a holdout. The slope comes from
  PR #248's 2019-2022 fit, but the live population is a different composition
  and has already been observed.
- The A3 diagnostics identity flag is weak: `RESOLVED` means at least one
  player is present, not that both identities are correct.
- A3 historical replay is not attempted because there is no frozen historical
  A1/A3 prediction population. Reconstructing A3 alone would not answer the
  incremental-value question.
- No bookmaker price, CLV, ROI or stake outcome is claimed. This report is
  about probability and Manager decision quality, not betting profitability.
- The 140 rerun three-active rows are repeated evaluations of 8 unique matches;
  they are not independent samples.

---

## 11. Final decision

**HOLD.**

- **Agent 3:** reject as an independent third check on current evidence.
  Participation is structurally too sparse, the support gates are internally
  inconsistent, the probability scale is incompatible with the Manager spread
  gate, and the observed direction is redundant with Agent 1. Do not lower
  safety thresholds to force participation.
- **Agent 2 calibration:** accept only as a probability-calibration
  observation. Reject production activation and reject promotion to a Manager
  candidate because coverage falls, winners are lost, and the ATP Tour Brier
  subgroup worsens.
- **Ensemble:** HOLD the three-agent design. The intended 3/3 consensus path
  cannot be evaluated when A3 predicts 0.44% of unique matches and the 0.08
  spread gate rejects all observed three-active rows.
- **Next evidence needed:** fix the A3 raw/effective gate inconsistency and
  missing-surface data path in a pre-registered experiment; persist A3 input
  snapshots; run a genuinely forward A1/A2/A3 shadow for at least 8 weeks and
  50 scored Manager decisions. No model change should be activated before
  those conditions are met.
