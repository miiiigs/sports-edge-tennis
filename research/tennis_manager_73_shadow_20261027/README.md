# Tennis Manager 73% — Research-only confidence screening

This independent lane does **not** claim the target can be guaranteed and does not change production predictions or betting decisions.

## Input

Existing calibration-ledger export JSON with a `data` array. Each Manager row must contain `aligned_pick_type='MANAGER_CONSENSUS'`, unique sporting `match_id`, original `aligned_probability`, `manila_date`, `result_status` (WON/LOST/VOID/PENDING) and **point-in-time** `model_regime_key`. A missing/mixed model regime is a hard block, not a justification to blend historic model policies.

Research tracking rows are ignored; Value Bets are NOT substituted for the Manager cohort. Rows must be first-qualified productions, not generated retrospectively from finished results.

Run:

`python -m research.tennis_manager_73_shadow_20261027.evaluator --ledger /path/to/verified_ledger.json --output /tmp/tennis_73_shadow.json`

This module is intentionally independent of the live runner, dashboard, Market, Manager, pricing and settlement code. It reads a local file and emits a local JSON report.

## Predeclared evaluation

Fixed thresholds: 0.65, 0.70, 0.75, 0.80, applied only to original Manager probability observed at prediction time. Chronological whole-day split: 60% earlier days for population audit, 20% validation for threshold selection, final 20% untouched holdout. Validation and holdout each require >=50 graded Manager matches. Validation candidate requires >=70% original pick retention, >=50 scored, and >=3 percentage-point uplift against exact baseline. The holdout is reported but never used to retune.

The report always shows baseline, selected candidate, W/L, selection retention, dates, and calendar-day streaks. Any missing day, all-void/no-graded day or pending pick means that day does not count toward a five-day claim. No one is authorized to activate the threshold based on this retrospective study alone. Higher confidence does not guarantee higher actual accuracy, and this study doesn't yet control for bookmaker valuation.

## Required production prerequisites

1. Obtain an authenticated, point-in-time, canonical first-production Manager ledger, with no duplicates and no results leakage. Current `ops/calibration_evaluation.py` export readers previously did not require model-regime lineage; source data may therefore need enrichment.
2. Validate calibration, uncertainty, daily sample sizes, odds ROI (only originally observed prices), model-lineage stability, and broad selection retention.
3. Forward prospective shadow selections through five or more independent completed days and longer windows as necessary. Preserve full daily denominator, pending/void and no-pick gaps. Compare to unchanged Manager baseline on the same events.
4. Only then propose an approval-gated live experiment if evidence is strong. Never change real bets, thresholds, or persisted original predictions based on in-sample improvements.


## Read-only original Manager data export

The existing calibration_dataset.sql contains Manager plus tracking and does not prove point-in-time regime lineage. For PR #265, run the new exporter against an authorized read-only PostgreSQL connection in an environment with TENNIS_DATABASE_URL:

python -m ops.export_tennis_manager_73_ledger --output local_manager_ledger.json
python -m research.tennis_manager_73_shadow_20261027.evaluator --ledger local_manager_ledger.json

Source is exactly the first-production model picks CTE used by the customer Manager scorecard. The exporter chooses the latest fully proven Manager/A1/A2/A3 regime by default, excludes the current incomplete Asia/Manila day, preserves pending/void, uses PIT match start and original probability, and writes only a local JSON file with a provenance SHA256. Mixed, unknown or incomplete lineage blocks export or evaluation. Unsettled validation or holdout dates block threshold recommendations. No API keys, market prices, stakes, or provider calls.

Even a successful retrospective holdout is NOT a guarantee of 73% for 5 future days; forward shadow and customer acceptance remain required.
