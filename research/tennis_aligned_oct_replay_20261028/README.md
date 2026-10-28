# Oct 24–27 aligned Model Picks: point-in-time counterfactual replay

**Do not rewrite October 24–27 original predictions.** Brian's revised target is the entire original aligned Model Picks population (Manager plus A1/A2 Wide-Spread research), not Manager-only.

This script answers a precise question: *Would a few transparently predeclared, pre-match selection restrictions have produced higher accuracy among the original published picks?* These are hypothetical **abstentions**; it cannot claim new winners it did not originally predict. To genuinely rerun the model on the full match slate requires replayable point-in-time player history, rankings, surfaces and model artifacts that existed at the original prediction lock time.

## Data source

Canonical read-only export: `ops/calibration_dataset.sql`, which uses the original aligned picks, distinct first production per match, match_versions with as_of and source_observed_at <= picked_at, frozen A1/A2/A3 probabilities, tiers/surfaces and final result observations. Export its SELECT rows to JSON `{"data":[...rows...]}` using authorized read-only PostgreSQL access. No direct production database access or provider calls are performed by this script.

Run:

`python -m research.tennis_aligned_oct_replay_20261028.replay --ledger /secure/path/to/aligned_picks.json --output /secure/path/to/oct24_27_what_if.json`

Never commit private exported player or decision data.

## Predeclared strategies (no tuning on Oct24–27)

- Unchanged original published aligned Model Picks.
- Original aligned confidence >=70%, >=75%, >=80% (three separate what-if variants).
- Known original surface only (CLAY/HARD/GRASS).
- Aligned >=70% AND known surface.

All filters use information available at original prediction time. Never classify a match by its final result to decide whether it would have been picked.

Candidate eligibility is calculated using **only records before October 24**: >=40 graded original and candidate, candidate retains >=70% original selections, gain >=3 percentage points and no pending pre-period labels. If none passes, no candidate is selected. The October 24–27 results are a single untouched evaluation window, and the script never chooses/tunes a rule using those dates.

The output includes original W/L/VOID/PENDING, retained selection W/L/VOID/PENDING, skipped winners, skipped losers, skipped void/pending, daily and pooled win rates, and exact remaining coverage. A day with pending, no picks, or no graded selections cannot be accepted for the 73% objective.

## Critical limitations

- This is *retrospective*, not demonstrated future improvement, even if a 4-day result exceeds 73%.
- This is a what-if comparison of historical picks, **not replacement of the actual Results dashboard**. The original 46.2% / 55.6% / 40.0% / 77.3% must remain historical.
- The existing canonical calibration SQL currently has no explicit frozen `model_regime_key` in the export; do not mix incomparable model versions without adding verified original regime lineage before a live experiment.
- Before a release, require a longer chrono/forward shadow with confidence intervals, model-version stratification, original candidate coverage and reliability in all five consecutive completed calendar days.
- Pending October 25–27 results mean this four-day audit is provisional.
- For Type A wagering ROI claims, original-time bookmaker quotes are required. This data source cannot validate betting profitability if authentic market evidence is missing.
