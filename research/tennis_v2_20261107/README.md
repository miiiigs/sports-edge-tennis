# Sports Edge Tennis V2: selection-quality research (November 7, 2026)

**Status: research-only, not a live policy.** This branch starts the next
development roadmap without changing the verified original results, A1/A2/A3
weights, Manager, pricing, settlement, or the deployed dashboard.

## What is already implemented

- One immutable point-in-time original-pick input population (Manager + A1/A2
  wide-spread research tracking), not rerun-generated duplicates.
- One read-only export (`ops/calibration_dataset.sql`) that includes original
  confidence, original surface/tier and original A1/A2 sides/probabilities.
  Critically, it also includes both result observation timestamps, so later
  knowledge cannot leak into pre-holdout calibration training.
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
  5. `v2_calibrated`: pre-holdout fitted Platt probability >=73%, together
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
   - terminal result was observed and source-observed *before* calibration
     cutoff, never on the holdout day or after;
   - missing A1/A2 sides and unknown surface/tier cause quality abstention.
4. Compare by individual day and coverage, **not** only pooled accuracy.
   One day with under five graded picks, any pending result, or no selections
   does not pass the proposed five-day research threshold.

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
