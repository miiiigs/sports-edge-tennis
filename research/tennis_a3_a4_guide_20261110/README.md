# Tennis A3/A4 + Guide Odds priority research, 10 November 2026

**Status: draft research only.** This branch is stacked on draft V2 PR #286.
None of this changes the production runner, A1/A2/A3 weights, Manager,
Today Picks, historical outcomes, canonical database, market decisions,
stakes, or bookmaker calls.

## Implemented in this iteration

1. **A3 as a risk signal:** take its first-issued PREDICTED side and status.
   Report CONFIRMS_PICK, OPPOSES_PICK, NO_EVIDENCE, UNKNOWN, and the genuine
   first-issued A3 `abstain_reason_code`. An abstention is NOT an upset
   prediction and is neutral in any A3 disagreement test. We do not pretend
   current A3 is a fully redesigned upset-detection engine. Existing raw>=2
   and effective>=2.5 support-gate inconsistency still requires its own
   prospective research, as documented in A3 issue #290 and PR #252.
2. **A4 reliability baseline:** interpretable ridge-logistic classifier trained
   on first-issued original Model Picks. Fixed input features: original
   probability, A1/A2 gap/agreement, original surface and tier, original
   Manager vs tracking status, first-issued A3 confirmation/opposition/missing
   and A3 recorded support/freshness reasons. There is NO player name, final
   result, closing odds, market return or future statistic in the features.
   The classifier fits ONLY graded results available before each Manila day:
   source-observed, internally observed AND ingested before the cutoff.
   Training is blocked without >=250 graded previous picks and >=40 of
   EACH win and loss. No hyperparameter search is performed on test dates.
3. **Guide Odds context:** optional sidecar from
   `ops/a4_guide_quotes.sql` selects the latest paired, OPEN, canonical
   pre-issuance OddsPapi match-winner snapshot for BOTH sides, matching
   exact source_snapshot_id, raw provider observation, bookmaker and timestamp.
   No quote later than first issuance, older than six hours or with
   implausible overround passes. Implied probabilities are normalized
   to remove the two-outcome bookmaker overround; this normalization is NOT
   independent evidence of actual win probability.
4. **Priority tags:** Guide-supported high priority, model-only priority,
   market disagreement review, A3 conflict review, reliability review, or
   unranked. These are RESEARCH DISPLAY PRIORITIES; they never change the
   original selected player or delete a Model Pick.
5. **Outcome accounting:** per Manila day, score each hypothetical priority
   group by W/L/VOID/PENDING, coverage and win rate, and compare original
   vs A4 Brier and log loss on the identical as-of-known graded cohort.
   Report missed winners and avoided losses for any hypothetical tier filter.
   No no-pick/low-sample day can be called a proven 73% day.
6. **Audit and CI:** one optional read-only odds SQL export, one offline
   `ops/a4_quality_shadow.py` CLI, synthetic anti-hindsight/priority tests,
   and separate no-Postgres GitHub Actions workflow.

## How to reproduce (on an authorized machine, with read-only exports)

First, export `ops/calibration_dataset.sql` as JSON
`{"data":[...]}`. Do not commit the original match rows.

Optionally export `ops/a4_guide_quotes.sql` as
`{"quotes":[...]}`. This includes `match_id, selected_side,
selected_decimal, opposite_decimal, source, bookmaker_code,
source_snapshot_id, source_observed_at, ingested_at, market_status`.

```bash
PYTHONPATH=src python ops/a4_quality_shadow.py \
  --ledger /secure/first_issued_original_picks.json \
  --guide-quotes /secure/odds_at_first_pick.json \
  --start 2026-10-27 --end 2026-11-06 \
  --evaluated-at 2026-11-07T09:04:42+08:00 \
  --expected-original research/tennis_v2_20261107/original_daily_scorecard_20261027_1106.json \
  --out /secure/a3_a4_guide_daily_replay.json
```

To test the no-provider case, omit `--guide-quotes`. This must NOT remove any
original picks or pretend that a bookmaker price exists.

```bash
PYTHONPATH=src python -m pytest -q tests/test_a4_quality_shadow.py
PYTHONPATH=src python -m pytest -q tests/test_v2_quality_research.py tests/test_v2_walkforward.py
PYTHONPATH=src python -m pytest -q
```

Use a newer frozen original daily scorecard if analyzing results settled after
the as-of timestamp above. The old snapshot should not be silently paired
with newer terminal results. For an exact frozen historical replay, both
the original result ledger and the market data must be synchronized as-of.

## November 10 production analysis checkpoint

This branch now also contains a separately frozen **original versus existing
>=70% confidence-filter** research snapshot:

- `production_snapshot_20261110_1805PHT.json`: frozen production
  `ALIGNED_DAILY_EVIDENCE`, `QUALIFIED_SHADOW_DAY` and independent
  `MANAGER_DAILY_EVIDENCE`, November 5–9, as of the November 10
  6:05 PM Manila run.
- `analyze_production_snapshot.py`: aggregate-only accounting validator,
  Wilson uncertainty, daily >=73% sample/coverage/settlement requirements,
  missed winners and avoided losses, plus bounds on unresolved matches.
- `FINDINGS_20261110.md`: the analyzed outcomes and production-independent
  A4 improvement targets. Tests are in
  `tests/test_tennis_nov10_production_snapshot.py`.

Run without private DB access:

```bash
python research/tennis_a3_a4_guide_20261110/analyze_production_snapshot.py \
  research/tennis_a3_a4_guide_20261110/production_snapshot_20261110_1805PHT.json
```

**Observed selection problem** (not a proven new model gain): original
63W–41L = 60.6% graded; existing >=70% research subset 33W–14L = 70.2%
on the same dates, but at 45.2% pick coverage. It excluded 27 known
losses **and 30 known winners**. Independent Manager-only scored
35W–25L = 58.3% on its own graded cohort. All intervals and incomplete-day
warnings remain explicit. V3/A4 has **not yet been scored on the private
per-match ledger**, let alone prospectively validated.

A4 also fixes an important hindsight issue: a match predicted *before* its
scheduled day must use training data frozen no later than the actual first
pick issuance. The previous per-match-day cutoff was potentially too late.

## Acceptance / reasons to hold

- CI may still fail before any job steps (tracked as GitHub issue #287).
  **Committed test code is not equivalent to a passing test.**
- OddsPapi remained HTTP 403 SUBSCRIPTION_RESTRICTED as of Nov 10 PHT.
  The model must remain unaffected by missing prices and no odds-based
  improvement can be claimed until the subscription is restored.
- A4 currently trains only on *original selected picks*, not all evaluated
  fixtures. It is a conditional reliability model with selection bias, not
  a calibrated probability across every possible tennis match.
- Historical data already used to propose these features can flag obvious
  failures, but cannot serve as a completely unseen holdout. Freeze code,
  use prior-only chronological replays and independent future live shadow.
- Release requires out-of-sample evidence that A4 improves Brier/log loss
  and candidate selection precision with acceptable volume and coverage,
  without missing a disproportionate number of winning picks. Report
  confidence intervals and daily results. A 73% daily streak is a target,
  not guaranteed by architecture.
- Guide Odds must **never** vote on the winner as a fourth agent. They can
  inform client-side prioritization/market sanity only after real data
  provenance and prospective evidence establish usefulness.
- Keep dashboard/mobile acceptance work (#289), CI incident (#287), Guide
  Odds subscription renewal, and pending-result reconciliation separate.

## Production boundary

**Do not merge/deploy this draft as a live prediction policy.** Promotion
requires review, passing tests, authorized historical export reconciliation,
and a prospective shadow comparison against the unchanged original.
