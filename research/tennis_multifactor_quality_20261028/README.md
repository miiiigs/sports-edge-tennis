# Multi-factor aligned Model Pick quality research, 2026-10-28

Purpose: identify **forward-validatable** ways to raise ALL ORIGINAL ALIGNED Model Picks accuracy, including Manager consensus AND A1/A2 Wide-Spread research tracking, without retrospectively hiding losses or manipulating the 73%-for-five-consecutive-settled-days requirement.

## Why not immediately use 70% or 80%?

Real Oct24–27 results: original 53W–33L =61.6% graded; >=70 29W–14L=67.4% (51/98 retained); >=80 12W–5L=70.6% (20/98 retained). Neither passed the >=73% daily requirement; more selective rules missed many winners.

Prior Sept09–Oct04 cohort (382 graded): >=70% had 76.3%, >=80% 79.5%, and close A1/A2 spread <5% had 79.4%. Different period, not generalization. Challenger confidence >=80 was overconfident on separate Oct13 holdout; never assume raw 80% equals actual 80%.

## Candidate features and no-leakage protocol

Precommitted research-only candidates: original, raw >=70, known surface, quality_balanced, quality_strict and quality_tier_guard. Multi-factor gates require original pre-match confidence, same selected side, A1/A2 probability agreement, recognized original surface, and known original competition tier. A stricter Challenger threshold is a hypothesis for validation, **not** an approved policy. Missing/ambiguous inputs cause abstention for gated candidates, never inferred results.

`quality.py` fits *no* model or recalibrator. It permits **selecting one candidate** only using all outcomes whose original terminal observation AND source-observation timestamps were both available before the cut-off (Manila midnight on validation start). Train eligibility: >=40 graded in original and candidate; retained coverage>=45%, uplift>=3 percentage points; otherwise **no candidate**. This prevents any October 24–27 outcome from changing which candidate is proposed to evaluate there.

The untouched validation days show unchanged baseline plus each hypothetical candidate's W/L/VOID/PENDING, missed winners, avoided losses, coverage, and day status. A day with <5 graded selections, pending outcomes or no picks cannot be called a successful 73% replay. The **live acceptance** remains the separately deployed original aligned-picks tracker and is not modified by any of these comparisons.

## Run offline with authorized data

The existing `ops/calibration_dataset.sql` now additionally exports both terminal-observation timestamps for leakage controls; it is a read-only SELECT. Export its returned rows as private `{"data": [ ... ]}` JSON through approved read-only database access. No full player names or odds need be shared. Never commit private rows or credentials.

`python -m research.tennis_multifactor_quality_20261028.quality --ledger /secure/aligned.json --start 2026-10-24 --end 2026-11-01 --output /secure/quality_report.json`

This script requires actual authorized canonical records. The code alone does **not** produce verified improved Oct24–27 rates. **Do not activate any live prediction rule** unless subsequent holdout and forward shadow prove higher performance without unacceptable coverage loss. Confirm regime-key lineage before comparing versions. Old official ranking-band study had no point-in-time ranking; no ranking feature is used.

Production API fixture and settlement repair remain separate from this study. OddsPapi subscription 403 needs provider entitlement, never a fabricated bookmaker quote.
