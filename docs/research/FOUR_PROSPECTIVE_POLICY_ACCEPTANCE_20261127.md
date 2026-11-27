# Four prospective policies — integration acceptance addendum

Research-only, not approved for activation. The canonical registry is
`validation/prospective_policy_registry.py`. It is separate in namespace,
not an independent engine. Existing A-v1 rehearsals and historical lettered
experiments are not renamed or backfilled.

- A: production Manager as issued, no extra research threshold.
- B: corrected A1 primary, 0.668060.
- C: corrected Manager C, narrow frozen 0.7070145.
- D: corrected A1 secondary, 0.648708.

A1 provenance: `a1_selective_performance.py` confidence(), selection_side(),
rank_rows(), development_threshold(), apply_frozen_threshold(); exact
values in `evidence/a1_selective_performance_20261126.json`. Confidence is
Python round(max(p1,1-p1),6), side 1 on p1 >= .5, selection >= threshold-1e-12.
All equal threshold confidences pass, no weekly re-fit or quota. Historical
development ranking ties use ascending prediction ID then match ID; that
rule is not a prospective quota. Historical holdout 420 selections/321
wins (B) and 476/353 (D) are development-reused, not prospective.

C: the existing prospective frozen C cutoff is 0.7070145 in
`prospective_manager_shadow_capture.py`. The historical corrected C identity
comparison also uses a broad 0.6213299999999999 cutoff; do not activate it
under this ID. Choosing the existing narrow prospective definition is an
integration design choice requiring owner confirmation, not a new optimized
threshold. Reuse the existing uncalibrated mean of >=2 active agents with
0.60 floor and its tie semantics, then compare unrounded confidence +1e-12
against the narrow cutoff. No fallback to original A1 if corrected A1 is
missing; this stricter operational guard is explicit. Record missing agent
rows separately from real abstentions. Policy helpers are not genuine model
execution; the A-only transactional wrapper remains unchanged and rejects
corrected candidates.

Archive: new ArchiveHTTPTransport integrates via dispatch_archive submit
callable. Literal loopback HTTP for test; remote HTTPS with system certificate
verification, bounded request/response, no redirects, exact-byte retries,
explicit authentication. Receipt timing and duplicate flag are validated.
No independent service or trusted timestamp has been provisioned. PostgreSQL
outbox-to-HTTP tests simulate a lost acknowledgement *after* a real local
durable collector write; these are synthetic, not independent evidence.

Checkpoint counts 25/50/100/300/600/1000/1500/2000; preserve at least 30
calendar days and 200 first-issued settled before the pre-existing acceptance
gate. Counts alone do not qualify, and 300 is not proof of 75%. No repeated
fixed-sample significance testing. A separately reviewed statistical
analysis plan (dependency-aware intervals, paired comparisons, multiplicity
and monitoring rule) is still required before real activation.

Commercial review: #351 reports unresolved written permissions including
underlying historical sources, retention and derived commercial forecasts.
#393 uses separate competitor capture and identity/timing qualification.
Neither is a substitute for independent receipts or official fixture census.
Do not run corrected models until every active dependency is cleared.
No purchase, production migration, deployment, Telegram publication, model
training, or activation. can_promote=false.

## Archive operations and owner controls

Local backup/restore: use `forward_archive_backup.backup_archive` with a new
absolute operator-controlled path; SQLite online backup API, integrity check,
per-record exact digest and original receipt metadata manifest. Restore to a
fresh path with the separately retained expected manifest, then verify producer
inventory and rehearse retry. Do not overwrite a running archive. External
retention, encryption, off-host immutable copies, separate credentials and
independent clock validation are not implemented by this helper.

Proposed independent deployment: separate owner-controlled account/service,
TLS termination, private durable encrypted volume, backup in a separate
account, clock synchronization/monitoring, independent inventory access and
separate admin identities. Rotate high-entropy producer tokens with a staged
new credential, verify in isolated traffic, drain uncertain exact-byte retries,
revoke old credential, retain rotation evidence; collector restart is currently
required and dual-token overlap would need a reviewed implementation. Budget
must include hosting, durable storage, egress, backup retention and monitoring;
no pricing quote or spending authority obtained. Approve jurisdiction, operator,
retention period, access, clock trust, recovery objectives and cost ceiling
before provisioning. Local receipts remain ineligible for promotion.
