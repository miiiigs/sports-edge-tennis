# Manager C: forward feature ablation (pre-registration, 2026-11-24)

Status: research ONLY, not approved, not deployed. No new real-data result in
this PR. PRs #357/#375/#380/#384 are already-inspected historical evidence.
Do not use Nov 4–20 as an independent evaluation or retune on it.

## Locked design

- Original first completed nonexperimental run, original fixture version,
  30-minute safety cut-off, original agent probabilities; no inferred picks.
- Fit once using Oct 15–Nov 3 match starts and results known by Nov 4 00:00Z.
  Query the training result snapshot AT Nov 4, not at a later report time.
- Validation: Nov 4–20 match starts, original issues after Nov 4 00:00Z.
  Validation outcomes are not used in training or cut-offs.
- Proposed freeze boundary Nov 25 00:00Z, conditional on actually hashing
  and independently time-attesting the manifest before that instant.
  If missed, pre-register a new FUTURE boundary before examining outcomes;
  never claim a backdated manifest is prospective.
- Forward: original issues at/after the attested boundary, first-run targets;
  choose a fixed four-week end and a settlement grace period BEFORE opening
  forward results. Record exclusions, voids, pending; report coverage over
  known first-run targets and separately over an independent official census
  if available. No odds claims without original matched market data.
- Fixed ridge lambda 8, 350 iterations, step .12; minimum 30 labelled
  observations with both classes, no hyperparameter search. Evaluate four
  prespecified groups: confidence logistic calibration, A1/A2 oriented
  probability distance + side disagreement, log minimum player prior match
  count, log maximum player rating uncertainty. These are candidates, not
  proven features. Each compares with raw confidence and a confidence-only
  calibrator trained on identical available observations. All predict
  correctness of the original C winner; no side changes.
- For history/uncertainty, require an immutable original-run snapshot with
  exact match/run IDs, source reference, as_of/source_observed_at/ingested_at
  <= issue. Current rankings or recomputed histories are forbidden. The
  supplied source_ref and clocks require independent lineage verification;
  the harness cannot authenticate an arbitrary JSON assertion. Do not use
  those groups until definitions, units and data provenance are reviewed.
  Never impute absent data as zero. Complete-case comparisons are explicit;
  the denominator remains all original targets and missingness is reported.
- Compare frozen thresholds at 10/20/25/40/50/75% validation target coverage;
  report actual achieved coverage, unavailable capacity and separate
  retrospective exact-K rankings at each reachable evaluation count.
  The latter know the full evaluation score distribution and are NOT a
  live selection rule. Same issued count may have unequal settled counts.
- Report Brier/log loss on all identical settled candidates and selected
  overlap, accuracy, Wilson intervals, exact match-set hashes, added/removed
  wins/losses/ungraded, and ATP/Challenger common-candidate performance.
  Wilson is marginal binomial uncertainty, not proof of paired superiority;
  add tournament/day clustered bootstrap before acceptance.

## Reproduce with authorized read-only database access

Use an autocommit connection to the authorized tennis database; never print
its DSN. These steps are Python API examples, not deployed jobs. Use the
repository's existing dependencies and run
`pytest -q tests/test_manager_c_forward_feature_ablation.py`.

```python
from datetime import datetime
import json, os
import psycopg
from sports_edge_tennis.validation.manager_c_forward_feature_ablation import (
    read_original, freeze, evaluate,
)
def t(x):
    return datetime.fromisoformat(x + "T00:00:00+00:00")
with psycopg.connect(os.environ["TENNIS_DATABASE_URL"], autocommit=True) as c:
    train, ta, known, te, th = read_original(
        c, start=t("2026-10-15"), end=t("2026-11-04"), as_of=t("2026-11-04"))
    val, va, _, ve, vh = read_original(
        c, start=t("2026-11-04"), end=t("2026-11-21"), as_of=t("2026-11-21"))
    val = [r for r in val if r["issued_at"] >= t("2026-11-04")]
    manifest = freeze(train, val, train_cutoff=t("2026-11-04"),
        freeze_at=t("2026-11-25"), known=known, agents={**ta, **va},
        source_hashes={"train":th,"validation":vh})
# Save privately and externally attest SHA256 BEFORE the freeze boundary.
# No forward query is necessary to freeze. Keep IDs/private snapshots out of
# the public repository. Save exclusions te/ve alongside the manifest.
with open("manager_c_private_manifest.json", "w") as f:
    json.dump(manifest, f, indent=2)
```

After the preregistered window and grace period, separately:

```python
with open("manager_c_private_manifest.json") as f:
    manifest = json.load(f)
with psycopg.connect(os.environ["TENNIS_DATABASE_URL"], autocommit=True) as c:
    later, la, _, le, lh = read_original(
        c, start=t("2026-11-25"), end=t("2026-12-23"), as_of=t("2026-12-30"))
    later = [r for r in later if r["issued_at"] >= t("2026-11-25")]
    report = evaluate(manifest, later, agents=la)
    report.update(source_sha256=lh, original_exclusions=le)
# Verify external freeze attestation, preserve results including failures.
```

Pass audited original-run snapshots through snapshots= to both freeze and
evaluate if available. The illustrative future window is not evidence of
actual availability or an attested freeze. Seasonality and limited ATP
coverage require a later independent active-season replication.

## Existing evidence and acceptance

PR375 narrow CH 75/100 vs marginal CH 92/143 = 64.34%; marginal 152/158
issued Challenger. Marginal mean confidence .66146 vs narrow .77653; marginal
A1/A2 disagreement 13/17 vs agreement 83/132. Small n and confidence
confounding mean no disagreement gate is supported. PR384 broad selector
removed 14W/12L, admitted 6W/5L, net -8W/-7L and -15 settled; same 283
candidate Brier .215688 vs .212590, LL .622398 vs .613875. HOLD.
The original source lacks proven PIT player-history, uncertainty, surface,
serve and odds features. Need a read-only feature availability census
including narrow/marginal/rejected and source lineage before those ablations.

Acceptance: actual pre-outcome manifest attestation; reproducible source and
settlement hashes; same-match proper scoring not worse; meaningful comparable
coverage and more net wins; report uncertainty and prespecified failure
criteria; forward sample and active-season replication; explicit owner
approval. No changes to production Manager or retroactive picks.
