# Sports Edge Tennis V2: selection-quality research (November 7, 2026)

**Status: research-only, not a live policy.** This branch starts the next
development roadmap without changing the verified original results, A1/A2/A3
weights, Manager, pricing, settlement, or the deployed dashboard.

## What is already implemented

- One immutable point-in-time original-pick input population (Manager + A1/A2
  wide-spread research tracking), not rerun-generated duplicates.
- One read-only export (`ops/calibration_dataset.sql`) that includes original
  confidence, original surface/tier and original A1/A2 sides/probabilities.
  Critically, it includes result observation, source-observation and ingestion
  timestamps, so late-added outcomes cannot leak into earlier calibration.
- `ops/v2_quality_audit.py` computes W/L/VOID/PENDING, calibrated and raw Brier
  score/log loss, coverage and excluded winners, daily minimum-N outcomes,
  and diagnostics by confidence bucket, surface, ATP/Challenger tier,
  Manager/Wide-Spread type, and A1/A2 side/spread.
- Five *predefined* comparisons, all from the **same original picks**:
  1. `original`: all original aligned Model Picks;
  2. `confidence70`: fixed original confidence >=70%;
  3. `v2_agreement`: original >=70%, both A1/A2 select the same picked side,
     A1/A2 confidence gap below 10 points, known original surface/tier;
  4. `v2_tier_guard`: as (3), but >=76% confidence on Challenger matches;
  5. `v2_a3_confirmed`: as (3), with a currently active A3 agent selecting
     the same winner. It may produce few or zero picks.
  6. `v2_calibrated`: pre-holdout fitted Platt probability >=73%, together
     with agreement and known surface/tier. Calibration **fails closed** unless
     at least 200 prior-observed graded results, 20 wins AND 20 losses, and
     finite positive calibration slope exist.
- Exact Asia/Manila date boundaries and a fixed `--as-of` make report
  reproduction possible. No result backfills or prediction policy writes.

The Challenger 76% and confidence spread thresholds are **hypotheses**, not
proven improvements. DO NOT use the V2 report to retrodict new winning
probabilities on matches whose results are already known.

## Execution (authorized offline export, never commit private rows)

1. Run `ops/calibration_dataset.sql` with a **read-only** production database
   credential and export JSON `{"data":[...]}` to a private location. Use
   only previously persisted original predictions; never substitute later
   refreshed model probabilities for first-issued probabilities.
2. Run:

   ```bash
   python ops/v2_quality_audit.py \
      --ledger /secure/aligned_original_export.json \
      --start 2026-10-27 --end 2026-11-06 --as-of 2026-11-07 \
      --out /secure/v2_quality_report_20261107.json
   ```

3. Verify accounting:
   - `won + lost + void + pending = picks` for each method and calendar day;
   - each method's selected match IDs are a **subset** of the original IDs;
   - no duplicate match, invalid probability, unsupported result, or
     non-timezone-aware timestamp is accepted;
   - terminal result was observed, source-observed and ingested *before or
     at* the calibration cutoff, never learned later than the holdout began;
   - missing A1/A2 sides and unknown surface/tier cause quality abstention.
4. Compare by individual day and coverage, **not** only pooled accuracy.
   One day with under five graded picks, any pending result, or no selections
   does not pass the proposed five-day research threshold.

## Historical walk-forward, including previous days and weeks

**Yes, previous days and weeks are explicitly used.** Run the newly added
`--mode walk-forward` to evaluate each day independently with a *new* calibration
fit using only earlier results that were already source-observed, platform-observed
and ingested at that point. A result first ingested on October 31 cannot train
a model for an October 30 prediction, even if the match finished before then.

Use the larger September–October history in the authorized read-only export as
prior training, then walk sequentially through the October 27–November 6
reference window:

```bash
python ops/v2_quality_audit.py \
  --mode walk-forward \
  --ledger /secure/aligned_original_export.json \
  --start 2026-10-27 --end 2026-11-06 \
  --as-of 2026-11-07 \
  --out /secure/v2_walkforward_20261107.json
```

For the frozen November 7 historical checkpoint, also require exact parity
with the independently logged **original** live results:

```bash
python ops/v2_quality_audit.py \
  --mode walk-forward \
  --ledger /secure/aligned_original_export.json \
  --start 2026-10-27 --end 2026-11-06 \
  --evaluated-at 2026-11-07T09:04:42+08:00 \
  --expected-original research/tennis_v2_20261107/original_daily_scorecard_20261027_1106.json \
  --out /secure/v2_walkforward_reconciled_20261107.json
```

The frozen source is a dated aggregate of Railway `ALIGNED_DAILY_EVIDENCE`
records. It records **234 original picks across 11 Manila dates**, including
142 WON, 70 LOST, 21 VOID and 1 PENDING at the referenced observation instant.
It contains no match names or proprietary patient/user data. Because results
can legitimately settle later, use this exact `--evaluated-at` with this
dated snapshot; newer evaluations require a newly independently observed
baseline. If original counts differ, the audit aborts without claiming
a V2 win-rate improvement.

The rolling report includes each day's available **prior graded sample size**,
calibrator availability, original versus all fixed V2 candidates, W/L/V/P,
coverage, discarded winners, avoided losers, raw and calibrated Brier/log loss
and day-specific failure segments. It also enforces the evaluation as-of instant:
results first ingested after that instant are still PENDING in that report.

The old single-cutoff report remains available with `--mode fixed`.
For an exact audit-time instant rather than the end of a Manila date,
use `--evaluated-at 2026-11-07T09:00:00+08:00` with walk-forward.

**Interpretation:** the October 27–31 history and October 30–31 especially
help diagnose failures, but variants motivated by knowledge of those days
**cannot** call them pristine unseen holdouts. Earlier research PR #273
already explored some similar pre-match thresholds. Historical walk-forward
checks chronological training correctness and performance plausibility;
it does **not** replace a fixed, genuinely prospective V2 shadow.

### Approval gate for an actual prospective V2 release

1. Run all tests and reconcile exported match counts with original ledger.
2. Freeze the chosen candidate version and selection conditions in code **before**
   new scored fixtures. Preserve selection time, model version and selected side.
3. Issue each V2 pick once in parallel, with original plus >=70% comparison,
   and let outcomes settle without changing already-issued selections.
4. Report >=73% on five consecutive **fully settled** Manila days, with a
   meaningful graded sample and coverage; do not present zero-pick days as wins.
5. Promote only after independent review of calibration, coverage and
   rollback behavior; keep originals unchanged.

## Release gates

The first phase is **diagnosis**, not automatic V2 deployment. Next:

- Audit failed categories (especially October 30–31 Wide-Spread and
  November 5 Manager-only) on actual exported feature rows.
- Test fixed gates over a truly untouched forward period, preserving daily
  accepted original W/L and the shadow selected population.
- Consider calibration model promotion only if it improves proper scoring
  (Brier and log loss) on untouched data with adequate scored sample and no
  significant coverage collapse.
- Require five consecutive fully settled, >=73%-winning Manila days with
  at least five graded picks/day and a proposed >=45% coverage target
  (these are engineering proposals, **not** new Brian-approved contract terms).
- Only after review and approval consider a versioned manager policy and
  rollback-ready runner release. Keep original predictions forever.

## Known limitations

1. This first PR **does not** supply a current production export or claim a
   newly observed performance lift: production data needs read-only authorized
   access and may contain incomplete or stale match identity information.
2. Platt calibration trained on prior scored results is experimental and might
   degrade performance. The report exposes its out-of-sample Brier/log loss.
3. The raw `>=70%` shadow already improved pooled accuracy but failed
   several daily accuracy tests. Its display on production is **research**.
4. OddsPapi HTTP 403 limits Guide Odds but does not itself explain the
   manager-selection or historical W/L problem.
5. Historical result repairs should be append-only, independently verified,
   and preserved with first-source observation timestamps.
