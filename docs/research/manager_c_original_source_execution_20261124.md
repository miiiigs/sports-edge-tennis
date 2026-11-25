# Manager C original-source execution handoff, 2026-11-24

Research-only follow-up on draft PR #386. No merge, deploy, database write,
new predictions or change to Manager decisions. Previous head
09fe1da2e8ce0fb7753a35bc336d857f567b33b4 passed PostgreSQL Foundation run
45346771122.

## Evidence recovered from actual Railway logs (not a new row-level run)

Three independently retrieved archived executions share original cohort
SHA256 e7d70fb821d7c69339adfce51bbd7fe35161adda49c595124d1791fef1b56863:

- PR357 deployment 356b35d5-0182-4c38-83db-b8e3fbd7bdf7,
  2026-11-22T07:30:57.883915394Z, code 38f716c4bb65cecbfb031720eb59e5bb593ed0c9.
  Exact 132 issued: B 88W35L, 9 ungraded; C 90W32L, 10 ungraded.
  Shared 95 settled: 71W24L, same probabilities (Brier .18545385863740527).
  Derived from overlap: B-only settled 17W11L; C-only settled 19W8L.
  C did not change any common winner; its selection mix changed. This is
  descriptive equal-K on the previously inspected cohort, not a future gate.
- PR375 deployment 15b862b1-12d3-409d-9e71-428eb30150ce,
  2026-11-23T11:04:23.932010663Z, code 5197cdddb7a40ca94c0ecb7283d81f1d6d852f11.
  Narrow 80W27L /117 issued, broad 176W80L /275, marginal 96W53L /158.
  Original A1/A2 probabilities available for all 275 broad picks including
  all 158 marginal (140 agree +18 disagree). Other numeric PIT features
  not proven. Ungraded is not a confirmed pending/void split.
- PR384 deployment 00ff36cc-d2c5-4de2-84a8-0a49c4062a7e,
  2026-11-23T12:09:14.454921685Z, code da954163b20f5d0fb986432da792c79c7e82210d.
  403 training labels; same 283 settled candidates raw Brier .212589895738,
  log loss .6138753483248836; learned Brier .21568814824109186, log loss
  .6223976666256784. Broad learned removes 14W12L, adds 6W5L, 15 fewer
  selections; reject promotion. No new tuning or evaluation.

Railway project c8eda2f4-0b73-43e3-a9e4-186e9e2b4190 visible; only production
environment, no existing fork, validation-runner TENNIS_DATABASE_URL
value withheld by connected OAuth. Execution container has neither that
DSN nor Railway CLI/token. Do NOT create a deployment or service to bypass
this. Archived aggregate evidence does not permit new learned ablations.

## Original source adapter

Uses unchanged historical first-run SQL and exact prediction run linkage;
fixture metadata is selected using all three availability clocks. Read
transaction is explicitly REPEATABLE READ READ ONLY, bounded statement and
lock timeouts. Source hash preserves prior format; full source hash covers
added metadata. Strict main-draw flag is reported separately from legacy
historical denominator; only verified main draws enter new manifest.
W/L only for time-safe COMPLETED, V for safely observed retired/walkover/
defaulted/cancelled (cancelled subcount), P otherwise. No wager settlement
mutation.

The rating_snapshots table has rating_value and sample_match_count, model
and feature version, as_of and created_at; no exact original prediction
snapshot foreign key was found in the persisted prediction contract.
The census checks whether both canonical players have any nonexperiment
same-model/feature sample count known and created by original agent as_of.
This is **unlinked visibility**, not proof of the model's actual inputs,
correct identity-bridge resolution, rating metric or surface. Do not feed
it to a trained selector. Numeric history/uncertainty remain unavailable
unless immutable original-run lineage is independently verified.

## Execute without deploying

From an authorized, isolated checkout of this PR, use an existing approved
read-only connection with TENNIS_DATABASE_URL provided securely through the
environment. Never print the DSN or commit private per-match records.
Verify exact checkout with git rev-parse HEAD. Commands below assume an
installed repository and POSIX shell; use the equivalent PowerShell env
handling on Windows.

```sh
SHA=$(git rev-parse HEAD)
python -m sports_edge_tennis.validation.manager_c_original_source historical --source-sha "$SHA" > manager_c_historical_private.json
```

Check historical_source_hash_matches=true and exact 117/275/158 selection
counts before interpreting a new census; mismatch is a data revision or
source issue to reconcile, not a reason to adjust filters until numbers fit.

The original Nov25 00:00Z freeze boundary is still future as of this
2026-11-24 handoff. **Protocol only; no trained manifest exists yet.**
If the time boundary cannot be independently attested before it passes,
register a new future boundary before looking at forward outcomes.
Training starts Oct15, stops at Nov4 with its own as-of Nov4; validation
Nov4–20 prediction scores only. No new fitting to November outcomes.

```sh
python -m sports_edge_tennis.validation.manager_c_original_source freeze --source-sha "$SHA" --boundary-utc 2026-11-25T00:00:00Z > manager_c_private_envelope.json
```

Protect the private envelope (original IDs), externally timestamp its
envelope_sha256 before the boundary, and archive independently verifiable
attestation. Do not edit the envelope after hashing. The CLI refuses a
past freeze boundary and a premature evaluation, but cannot authenticate
an external timestamp by itself. A new date requires the same precommitted
28-day evaluation and 7-day settlement grace; original predictions issued
before the boundary are excluded. Do not evaluate until the window matures.

```sh
python -m sports_edge_tennis.validation.manager_c_original_source evaluate --source-sha "$SHA" --manifest manager_c_private_envelope.json > manager_c_forward_private.json
```

Output contains same-candidate proper scores, W/L/P/V, frozen and
descriptive exact-K at registered targets, added/removed, tour breakdown
and unavailable feature status. No claim of independent evaluation until
external attestation, source audit, selection coverage and uncertainty
checks. No promotion from a seasonal low-volume window alone.

Run focused tests:
```sh
pytest -q tests/test_manager_c_original_source.py tests/test_manager_c_forward_feature_ablation.py
```
