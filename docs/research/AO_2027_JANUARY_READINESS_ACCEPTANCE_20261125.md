# Australian Open 2027 January readiness acceptance report — 25 November 2026

**Branch / PR:** `workstream/ao-2027-format-readiness-20261125` (PR #389)
**Baseline:** `f6ee17b28820f95fd63534aa15fa6d6de1b58c28`
**Status:** **CONDITIONAL / NOT RELEASED.** The format and pipeline work is
implemented and rehearsed; the 2027 edition-specific regulation, a real AO
forecast cohort and owner approval are still missing.
**Mode:** read-only production research plus isolated local PostgreSQL
rehearsals. No provider calls, purchases, production writes, deployment or
forecast-quality claim.
**Test-clock correction:** the earlier future-dated synthetic clock could not
satisfy PostgreSQL's `created_at >= as_of` guard; the regression now uses a
November 2026 observation/run clock for a January 2027 fixture, with no guard
relaxed. See
[`AO_2027_TEST_CLOCK_RECONCILIATION_20261125.md`](AO_2027_TEST_CLOCK_RECONCILIATION_20261125.md).

## Acceptance matrix

| Area | Evidence | Result |
|---|---|---|
| AO 2027 main draw is best-of-five | Official AO scoring guide; `resolve_scoring_rule` for every `R128`–`F` round | **PASS** |
| Deciding set uses a 10-point tiebreak at 6–6 | AO official guide (AO 2019 onward, uniform from 2022); `BO5_DECIDER_TB10_AT_6_6`; independent Markov reference | **PASS** |
| AO 2027 edition-specific rulebook | No 2027 rulebook located; 2026 is the latest found | **BLOCKED** |
| Qualifying is best-of-three and not enabled for Agent 2 | `Q1`–`Q3` parse as BO3/qualifying; resolver returns no scoring rule | **PASS** |
| United Cup is outside AO 2027 scope | Parser keeps it `ATP_TOUR`/BO3; resolver returns BO3, never AO BO5 | **PASS** |
| January tournament discovery | Brisbane, Hong Kong, Adelaide, Auckland, Australian Open parse with the expected formats/tiers | **PASS (parser)** |
| AO identity/round/surface/edition anomalies | Historical census: 128 AO 2026 BO3 rows, split editions 2248/2249, NULL surface/end date, 40 QF flag rows, 115 stripped qualifying labels | **PASS (measured, not rewritten)** |
| 2026 AO correction | No historical rewrite without the official draw | **BLOCKED** |
| Ingestion → Agent 2 → Manager → publication → settlement | Isolated rehearsal, BO5 persisted through every boundary | **PASS (synthetic)** |
| Rescheduling | Append-only version rehearsal; original start and PIT retained; guard still excludes inside the original deadline | **PASS** |
| Realistic Agent 2 support | Real stored history snapshot (58,179 observations); 356/356 stored AO main-draw pairings above the support gates; synthetic 2027 pairing `PREDICTED` at 0.426741 vs cold-start `ABSTAINED` | **PASS (support condition only)** |
| Runner compatibility | PostgreSQL full-slate BO5 regression | **PASS locally; CI at head required** |
| Dashboard compatibility | Model Picks read model has no tournament/season filter and reads the same Manager/prediction/version tables | **PASS (structural)** |
| January operational rehearsal | Synthetic fixture only; no real January provider slate | **CONDITIONAL** |
| Independent official census/denominator | No authorised complete 2027 export | **BLOCKED** |
| Original AO forecast cohort | Zero original AO prediction targets in stored runs | **BLOCKED** |
| Owner approval and deployment | Not requested | **BLOCKED** |

## January pipeline evidence

| Stage | Result |
|---|---|
| Discovery/parser | AO `R128` becomes `GRAND_SLAM` / `HARD` / BO5 / `Australian Open 2027`; qualifying and United Cup remain separate |
| Ingestion | Isolated local database persists the BO5 version; no production write |
| Agent 2 | Resolves `BO5_DECIDER_TB10_AT_6_6`; real-history support rehearsal produces a prediction; cold-start control abstains |
| Manager | Real synthetic-slate result is `NO_BET` / `TWO_OR_MORE_ABSTENTIONS` because A2/A3 lack history; a clearly labelled forced consensus is used only for downstream rehearsal |
| Publication | BET decision published with publication control enabled |
| Rescheduling | Second append-only version moves the start; original start/PIT retained |
| Settlement | Synthetic completed result settles one `WON` decision |
| Runner | Full-slate AO BO5 regression completes and keeps the format |
| Dashboard | Read model is tournament/season agnostic and uses `match_versions` PIT lateral |

## What the evidence does not show

- It is **not** a 2027 forecast, an accuracy result, a Brier/log-loss result or
  an ROI result.
- It does **not** prove the real 2027 draw, entry list, seedings, byes,
  qualifying format or schedule.
- It does **not** show official fixture coverage; the stored history is internal
  evidence, not a licensed complete export.
- The forced-consensus publication step is a plumbing test, not a Manager
  decision on an AO fixture.

## Release checklist

- [ ] Official 2027 edition-specific Grand Slam/AO regulations located, frozen
      and cited.
- [ ] Owner confirms the 2026 format carries forward if no 2027 document exists.
- [ ] Exact pushed head has green PostgreSQL foundation CI.
- [ ] Reviewed diff of the ingestion/resolver path against the deployed runner.
- [ ] Real January discovery dry-run when the 2027 provider slate is available
      (no paid calls without approval).
- [ ] Read-only audit of the real AO 2027 provider tournament IDs, aliases,
      rounds, flags, surface and versions.
- [ ] Independent archive and settlement parity for the January window.
- [ ] Dashboard check with a real issued AO pick (not synthetic).
- [ ] Rollback plan and one-command redeploy rehearsed.
- [ ] Owner approval and a controlled-release window.
- [ ] Post-release attendance/watchdog review after the first two AO days.

## Decision

**Do not release as an approved AO 2027 production scope yet.** The code is
fail-closed and the rehearsals pass, but the edition-specific regulation and a
real January slate are missing. No production change, purchase or forecast claim
is made by this report.
