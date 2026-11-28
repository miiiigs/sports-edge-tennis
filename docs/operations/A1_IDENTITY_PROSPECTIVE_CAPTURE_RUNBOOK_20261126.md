# A1 identity-bridge prospective capture runbook — 2026-11-26

**Purpose:** operate the first-issued identity-bridge shadow capture and its migration
0025 persistence path without touching production decisions.
**Status:** research/evidence operation. No activation.

## 1. Preconditions

- Migration 0023/0024 (integration PR #392) are in the applied chain.
- Migration 0025 is applied through the reviewed process.
- The operator has a **read-only** connection to the tennis PostgreSQL database.
- An append-only archive path exists outside Railway logs (object storage, reviewed
  workstation directory, or a one-off read-only service volume).

## 2. Daily capture

Run after completed prediction runs have been finalized, ideally within 30 minutes of
issue so the record is proximate to the original prediction:

```powershell
$env:PYTHONPATH = "src"
python -m sports_edge_tennis.validation.a1_identity_bridge_forward_shadow `
  --discover-since 2026-11-25T00:00:00Z `
  --require-first-issue `
  --archive-jsonl D:\tennis-evidence\a1_identity_bridge_forward.jsonl
```

- `--discover-since` is a watermark, not a claim about first issue. A run already present
  in the archive summary is skipped.
- The observer sets `default_transaction_read_only=on` and records no outcome.
- Each capture record is hash-chained through `record_sha256`; the summary carries the
  record-set SHA-256 and archive size.
- Do not edit, rotate or truncate the archive. A new path is a new archive.

## 3. Integrate an archive into migration 0025

Dry run first:

```powershell
python -m sports_edge_tennis.validation.a1_identity_capture_integration `
  --archive-jsonl D:\tennis-evidence\a1_identity_bridge_forward.jsonl
```

Apply only for an archive that passed the dry run:

```powershell
python -m sports_edge_tennis.validation.a1_identity_capture_integration `
  --archive-jsonl D:\tennis-evidence\a1_identity_bridge_forward.jsonl --apply
```

The command writes only `identity_resolution_provenance`. Replaying the same archive is
idempotent; a different digest for the same run/match/player fails closed.

## 4. Daily monitoring

```sql
-- Capture and first-issue yield by run
SELECT prediction_run_id,
       count(*) FILTER (WHERE capture_source = 'FORWARD_SHADOW') AS provenance_rows,
       count(DISTINCT match_id) AS matches,
       min(predicted_at) AS first_observation,
       max(predicted_at) AS last_observation
FROM identity_resolution_provenance
GROUP BY prediction_run_id
ORDER BY prediction_run_id DESC;

-- Mapping method mix
SELECT resolution_method, count(*)
FROM identity_resolution_provenance
GROUP BY resolution_method
ORDER BY count(*) DESC;

-- Transport / scope split
SELECT m.match_id, mv.tour_scope, mv.qualifying_flag, count(*)
FROM identity_resolution_provenance p
JOIN match_versions mv USING (match_version_id)
JOIN matches m USING (match_id)
GROUP BY m.match_id, mv.tour_scope, mv.qualifying_flag;
```

Also watch:

- reference-drift detections from `reference_drift_for`;
- provenance digest mismatches (the reader raises);
- any non-read-only statement in the observer (the regression test rejects these, but
  production logs should confirm);
- capture rate relative to completed runs.

## 5. Stop rules

Stop the trial and keep the negative result if any of the following occurs:

- a capture is written after the scheduled start;
- a capture record contains an outcome;
- an archive record or database row fails digest verification;
- a conflicting provenance row appears for the same run/match/player;
- the observer attempts a write;
- the sample fails a paired proper-score gate at the frozen evaluation.

## 6. Evaluation

Do not evaluate before **200 settled first-issued bridged selections** and **30 calendar
days** of genuine future captures. At evaluation:

- grade only authoritative `COMPLETED` results visible after start;
- report ATP and Challenger separately;
- report paired Brier and log-loss intervals (candidate minus original), entirely below
  zero for activation;
- report Model Picks at equal selection count and at frozen eligible coverage;
- report selection overlap and whether shared picks keep the same side;
- keep RETIRED, WALKOVER, DEFAULTED and CANCELLED ungraded.

The 52-target run-2548 cohort and the 17 pre-outcome run-2535 records are **not** part of
the forward sample. Only first-issued, pre-outcome, independently receipted records are
admissible.
