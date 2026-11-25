# Agent 1 identity bridge — prospective readiness and activation acceptance

**Status:** research only. `can_promote=false`. No production Agent 1, Agent 2, Agent 3,
Manager, Model Pick, odds, publication or settlement change.
**Date:** 2026-11-26
**PR:** #388 (research) · migration train #392 (0023/0024)
**Machine-readable companion:** `docs/research/evidence/a1_identity_bridge_readiness_20261126.json`

## 1. Decision

The identity bridge remains **not eligible for activation**. The implementation work is
complete enough to enter a prospective trial, but no forward sample exists that can pass
the pre-registered gate.

- First-issued, pre-outcome bridged selections: **2** of the required **200**.
- Genuine future capture span: **2 calendar days** of the required **30**.
- Independent wall-clock collector receipt: **none**.
- ATP forward sample: **0** (unresolved, not pooled away).
- Paired proper-score intervals: **not computable** at n=2.
- Owner approval and a new Agent 1 config version: **not present**.

The retrospective and bounded analyses below explain *why* the candidate is worth a
prospective trial. They do not substitute for it.

## 2. Workstream status

| Workstream | Status | Evidence |
|---|---|---|
| Reference-policy experiment | Complete under `identity_reference_pit_v2`; re-measurement required under v3 | `a1_identity_reference_policy_20261125.json` |
| Mutable alias / canonical-name PIT | Schema implemented, **not applied** | `0025_identity_provenance_v2`, `identity_provenance_v2_20261125.md` |
| Runtime resolution persistence | Implemented and tested, inactive | `data/identity_runtime.py` |
| Replay discrepancy | 92.78% exact; remainder explained or fail-closed | `a1_identity_replay_mismatch_20261125.json`, `a1_identity_replay_mismatch_cases_20261126.json` |
| First-issued shadow capture | Archive active; not an independent receipt | `a1_identity_bridge_forward_capture_2603_20261125.jsonl` |
| Shadow non-interference | Regression-verified locally | `tests/test_a1_identity_bridge_forward_shadow.py` |
| Manager interaction | Bounded comparison complete; interval includes zero | `a1_identity_manager_interaction_20261125.json` |
| Legacy ~14k / 75% claim | Retired as unreproducible | `legacy_75pct_claim_audit_20261125.json` |

## 3. What is now exact, and what is still reconstructed

### 3.1 Exact

- Every forward capture freezes the PIT-visible reference state it used: player ids,
  model ids, method, evidence, name source, candidate count, matched alias, bridge event,
  `visible_from`, observation time, `match_version_id` and a reference digest.
- `player_name_versions` and `player_alias_versions` are append-only. A rename or alias
  linkage insertion after a cutoff can no longer generate keys for an earlier replay.
- `player_aliases` linkage columns are immutable for normal traffic. The pipeline's
  re-observation upsert still updates only `last_observed_at`/`updated_at`.
- `identity_resolution_provenance` stores the same facts durably with a per-row digest
  and a unique `(prediction_run_id, match_id, original_player_id)` key.
- A conflict for the same key with a different digest fails closed; it is not treated
  as a correction.
- The forward observer is read-only and a regression test rejects any non-read-only
  statement and verifies that prediction, decision, Manager, publication and settlement
  rows are unchanged.

### 3.2 Still reconstructed

- The exact production request player ids from **past** runs were never persisted. No
  migration can recover them. The 7.22% replay mismatch is therefore a lineage limit,
  not a model defect.
- The 30 largest re-issue mismatches all changed target identity at the original cutoff;
  24 changed both players; 18 flipped the modelled side. None was first-issued and only
  8 had a null surface. This is identity reconstruction, not late history.
- The current production database has no independent collector receipt for the
  2-record run-2603 capture. The file is hash-verified but was committed by hand.
- The 52-target run-2548 cohort and the 17 pre-outcome run-2535 records remain
  quarantined. They are descriptive, not forward evidence.

## 4. Reference-policy experiment — completed result

Full population: 3,778 targets, 6,556 player resolutions per policy.

| Policy | Changed mappings vs permissive | Affected targets | Forecast effect |
|---|---:|---:|---|
| `strict` | 0 | 0 | 0 changed probabilities |
| `strict_alias_refresh_gate` | 0 | 0 | 0 changed probabilities |

Because no mapping changed, no replayed probability can change. The experiment's
forecasting consequence is exactly zero on this corpus; the value of the gates is that
they keep that true as the corpus evolves. This was measured under
`identity_reference_pit_v2`. The version-table resolver is `identity_reference_pit_v3`
and must be re-measured after 0025 is applied; do not present the v2 census as a v3
result.

## 5. Manager interaction — bounded, not a paired win

Population: 3,271 safely settled matches with all three agents and a stored Manager
result; 507 excluded as not safely settled. Only Agent 1 changes.

- Agent 1 side changed on 562 matches; Manager decision changed on 861 (26.3%).
- Consensus picks: 546 → 631; accuracy 63.9% → 66.1%.
- Shared consensus picks: 255 of 255 keep the same side. The movement is **match
  selection**, not a better probability on the same match.
- Unpaired difference: +2.17 pp, 95% CI **[−3.30, +7.63] pp** — includes zero.
- ATP: 58 → 68 consensus, 70.7% → 69.1%; Challenger: 488 → 563, 63.1% → 65.7%.

This is a coverage/selection interaction. It cannot be used as an activation result.

## 6. Replay fidelity — explained remainder

`a1_identity_replay_mismatch_20261125.json`, 3,780 targets: 92.78% exact, 94.60% within
0.005, 273 mismatches.

| Mechanism | Targets | Mismatches | Share of mismatches |
|---|---:|---:|---:|
| Target identity bridged | 3,138 | 139 | 50.9% |
| Surface fallback | 209 | 95 | 34.8% |
| Unknown surface | 371 | 23 | 8.4% |
| Unexplained | 62 | 16 | 5.9% |

Zero replay-visible result observations were ingested after any cutoff, so late history
is falsified as a cause. The unexplained remainder is small, bounded and fail-closed; it
is not currently attributable to a specific reconstructable value. The new provenance
table prevents this class for future runs.

## 7. Runtime persistence contract

`identity_resolution_provenance` is written only when a caller supplies observations.
The live runner supplies none today, so production behaviour is unchanged.

The writer validates before inserting:

- `match_version_id` exists and belongs to the persisted `match_id`;
- `match_versions.as_of`, `source_observed_at` and `ingested_at` are not later than the
  resolution observation time;
- the prediction run exists and its `as_of` is not later than the observation time;
- `visible_from` is not later than the observation time;
- reference and record digests are 64-character hex.

The reader verifies every row's digest and fails closed on mismatch. The capture
integration command is a dry run by default and requires `--apply` to write.

## 8. Activation acceptance gates

All contrasts are candidate minus baseline on the same frozen forward fixtures.

| Gate | Requirement | Observed | Met |
|---|---|---:|---|
| Sample | ≥200 settled first-issued bridged selections | 2 | No |
| Duration | ≥30 calendar days of genuine future captures | 2 | No |
| Receipt | independent wall-clock collector archive | none | No |
| Brier | paired 95% interval entirely below zero | not computable | No |
| Log loss | paired 95% interval entirely below zero | not computable | No |
| ATP | no material degradation; report separately | 0 forward | No |
| Model Picks | equal-count and frozen-coverage comparison | not computable | No |
| Config | new Agent 1 configuration version | absent | No |
| Approval | owner sign-off and rollback plan | absent | No |

## 9. Release requirements (in order)

1. Merge #392 so 0023/0024 are in the applied chain.
2. Apply 0025 through the reviewed migration process; verify up/down and cumulative
   schema parity in PostgreSQL CI.
3. Re-run the reference-policy audit under `identity_reference_pit_v3` on production
   and publish the new mapping/forecast file.
4. Wire provenance persistence into the activated prediction transaction. Do not
   backfill past runs.
5. Schedule the first-issued shadow capture and obtain an independent, externally
   timestamped archive receipt.
6. Accumulate ≥200 settled selections over ≥30 days.
7. Report paired Brier/log-loss intervals, equal-count and frozen-coverage Model Picks
   comparisons, overlap and ATP safety.
8. Create a new Agent 1 config version, record owner approval and a rollback plan.
9. Activate only if every gate passes. Otherwise keep the negative result.

## 10. Rollback and monitoring

- The migration down path drops the version tables, the alias guard and the provenance
  table. It does **not** recreate provenance rows; export the archive first.
- Because the version tables are append-only, a bad backfill requires a reviewed
  forward repair, not an in-place edit.
- Monitor: capture rate per run, first-issue share, mapping-method mix, ATP/Challenger
  split, reference-drift detections, provenance digest mismatches and any non-read-only
  statement attempt in the observer.
- Stop rules: any post-start capture, any outcome in a capture record, any conflicting
  provenance digest, any observer write, or any failure of the paired gates keeps the
  candidate off.

## 11. Reproduction

- `python -m sports_edge_tennis.validation.a1_identity_provenance_audit --out ...`
- `python -m sports_edge_tennis.validation.a1_identity_manager_interaction --out ...`
- `python -m sports_edge_tennis.validation.legacy_75pct_claim_audit --out ...`
- `python -m sports_edge_tennis.validation.a1_identity_bridge_forward_shadow --discover-since <ISO> --require-first-issue --archive-jsonl <path>`
- `python -m sports_edge_tennis.validation.a1_identity_capture_integration --archive-jsonl <path> [--apply]`

All production analysis commands are read-only except the explicit `--apply` capture
integration, which writes only `identity_resolution_provenance`.
