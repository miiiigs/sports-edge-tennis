# Tennis approved-repair preparation — 2026-10-20 (Phase 2)

Status: read-only evidence, owner-review plans and dry-run implementation. **No production
write, migration application, result change, identity merge, settlement or deployment was
performed.**

Base: PR #243 (`fix/tennis-historical-identity-result-audit-20261020`,
`8bb43d8`). Production: `Postgres-Sdqz`, audited read-only on 2026-10-20.
Repository branch: `fix/tennis-approved-repair-preparation-20261020`.

Reproduce the plans:

```text
python -m sports_edge_tennis.live.approved_historical_repairs \
  --repair 132211-walkover-void --output walkover_plan.json

python -m sports_edge_tennis.live.approved_historical_repairs \
  --repair 131036-reconcile-131137 --output reconciliation_plan.json

python -m sports_edge_tennis.live.approved_historical_repairs \
  --repair 130717-unresolved --output blocked_130717.json
```

Both supported plans are append-only, deterministic (stable SHA-256) and idempotent.
`--apply` additionally requires the exact approval phrase and the reviewed plan SHA.

## 1. Match 132211 — J. Kym v D. Stricker

Canonical evidence:

| Field | Value |
| --- | --- |
| match_id | `132211` |
| Players | `4900` J. Kym (`API_TENNIS:1304`, `ODDSPAPI:539343`) v `5291` D. Stricker (`API_TENNIS:379`, `ODDSPAPI:496346`, `SPORTRADAR:sr:competitor:496346`) |
| Tournament | Roanne, tournament 858, edition 2531, season 2026, Challenger, hard |
| Start / round | `2026-10-16T12:10:00Z`, QF |
| Provider IDs | `API_TENNIS:12171502`, `ODDSPAPI:id1203543675371396` |
| Latest fixture status | `WALKOVER`, observed/as_of `2026-10-19T08:13:00.037065Z` |
| First explicit WALKOVER | observed/as_of `2026-10-17T08:12:18.698820Z` |
| Terminal result | none |
| BET decisions | 12, all side 1, decision ids `18324, 18375, 18400, 18427, 18451, 18475, 18498, 18519, 18540, 18561, 18581, 18596` |
| Settlements | 0 |

Bracket evidence: `132233` (J. Kym v M. Brunold, SF, `2026-10-17T12:40:00Z`)
was played and Kym lost `0 - 2` (result observed `2026-10-17T15:04:56.782781Z`).
Stricker has no later same-tournament match. This proves Kym advanced; it does not
provide a scoreline or a provider winner.

### Can a WALKOVER/VOID correction be represented without inventing a winner?

**Not under the current schema.** `match_result_observations` requires `winner_side`
for `WALKOVER`. Settlement policy does not need a winner: it maps `WALKOVER` to
`VOID` regardless of winner.

The prepared correction therefore includes migration
`0021_walkover_winner_optional`, which relaxes only `WALKOVER`; `COMPLETED` and
`RETIRED` still require a winner. The correction then appends:

- `outcome_status = WALKOVER`
- `winner_side = NULL`
- `scoreline_text = NULL`
- `source_observed_at = as_of = 2026-10-17T08:12:18.698820Z`
- a terminal `OPERATOR_CORRECTION` ingest run for provenance

Settlement maps all 12 existing side-1 BET decisions to `VOID`, never `WON` or `LOST`.

Dry-run manifest SHA-256: `7342692dcdf266768cf1f1f26b2c6ee6077386838ae74cbb8c28644bbffbf0b1`.
It is `BLOCKED` on production only because migration 0021 is not applied yet. Before:
no result, 12 BETs, 0 settlements. After: one WALKOVER result with no winner/score,
12 VOID settlements when settlement next runs, predictions and decisions unchanged.

## 2. Match 131036 vs 131137

| Field | 131036 (target) | 131137 (evidence) |
| --- | --- | --- |
| Players | `4445` M. Alcala Gurri v `4337` C. Taberner | same, DIRECT orientation |
| Tournament | tournament `822`, edition `2481` | tournament `822`, edition `2469` |
| Season / tier / scope | 2026 / CHALLENGER / CHALLENGER | same |
| Start | `2026-09-26T11:00:00Z` | `2026-09-26T11:40:00Z` |
| Round | `semifinal` | `SF` |
| Source | `SPORTRADAR:sr:sport_event:74753168`, `ODDSPAPI:id1205233274753168` | `API_TENNIS:12166029` |
| Result | none | `131592`: COMPLETED, winner side 1, `2 - 0`, observed/as_of `2026-09-27T00:14:38.198365Z` |
| BET decisions | 0 (all 24 decisions are NO_BET) | n/a |

Earlier versions of 131036 used the placeholder `WQF2` before resolving to
C. Taberner. The two records share the same canonical players, tournament, season,
scope, tier, round and local date; the start difference is 40 minutes.

### Does the evidence prove the same event?

It is **strong evidence, but not proof under the existing automatic policy**.
`cross_provider_exact_match_v1` rejects the pair with `TIME_MISMATCH` because its
maximum start delta is 15 minutes; this pair is 40 minutes apart. The audit's 6-hour
heuristic is not the automatic policy.

Therefore the prepared action is a **manual owner-approved reconciliation**, not an
automatic reconciliation:

- policy `owner_approved_historical_duplicate_v1`
- classification `EXACT_CROSS_PROVIDER_MATCH`
- player order `DIRECT`
- target `131036`, evidence `131137`, evidence result `131592`
- append only `cross_provider_match_reconciliations`
- **do not copy the result onto 131036**
- **do not merge matches or player identities**
- no target BET decisions exist, so no wager outcome changes

Dry-run manifest SHA-256: `0b208ada6023cf48510050f8739bb5dc00c66655fccb67517639a13d7a512d97`.
If the
owner does not accept the 40-minute evidence, the correct action is to leave 131036
unresolved.

## 3. Match 130717 — remains blocked

`130717` (K. Smith v E. Winter, 10 BET decisions on side 2) has no terminal result.
Bracket advancement proves K. Smith later played `130805`, but it does not prove
whether the target was COMPLETED, WALKOVER, RETIRED or CANCELLED. Settling side 2 as
LOST or as VOID would both be unsupported. The plan is intentionally `BLOCKED` with no
statements (SHA-256
`c964318a57a4866bc371764492234fd4db1f33803f65e917eb362741dc6aba39`). It remains
unresolved pending an authoritative provider or official result.

## 4. Implementation

Added:

- `db/migrations/0021_walkover_winner_optional.up.sql` and `.down.sql`
- `src/sports_edge_tennis/live/approved_historical_repairs.py`
- `tests/test_walkover_winner_optional_schema.py`
- `tests/test_approved_historical_repairs.py`
- `ops/historical_repair/grand_slam_format_stratification.sql`

The operator module:

- builds a deterministic plan with stable SHA-256, before/after state and owner questions
- is read-only unless `--apply` is passed
- requires the exact approval phrase and reviewed SHA
- re-builds the plan inside the transaction and rejects stale plans
- writes only `ingest_runs` + `match_result_observations` for 132211, or only
  `cross_provider_match_reconciliations` for 131036
- is idempotent: a second run returns `ALREADY_APPLIED`
- never touches `predictions`, `decisions`, `manager_consensus_results`,
  `player_source_ids`, `match_versions` or the dashboard

## 5. Tests

Focused:

- `tests/test_walkover_winner_optional_schema.py` — 3 passed
- `tests/test_approved_historical_repairs.py` — 5 passed
- Full suite with the disposable PostgreSQL database: **1932 passed, 39 skipped**

Coverage includes:

- WALKOVER without winner is allowed; COMPLETED without winner is still rejected
- WALKOVER proposals always settle VOID, never WON/LOST
- no ordinary win/loss is created
- no unrelated match result changes
- no predictions/decisions DML appears in either plan
- no identity merge
- reconciliation writes only the provenance row, not a target result
- idempotent apply and settlement (12 VOID rows, second settlement inserts 0)
- 130717 remains blocked

## 6. Grand Slam best-of-three review

Read-only stratification (`ops/historical_repair/grand_slam_format_stratification.sql`)
shows the production corpus is men's-only:

- `tour_scope` values: `ATP_MAIN` 52,905 rows; `CHALLENGER` 130,662 rows.
- No women's or doubles rows are present in this corpus.
- Wheelchair rows exist as separate tournament names (2 rows, best-of-3).

| Season | Event | Main/Qualifying | Best-of-3 | Best-of-5 |
| --- | --- | --- | ---: | ---: |
| 2026 | Australian Open | main | 128 | 0 |
| 2026 | Wimbledon | main | 129 | 0 |
| 2026 | US Open | main | 133 | 0 |
| 2026 | Wimbledon Men Singles | main | 0 | 3 |
| 2026 | Australian Open | qualifying | 115 | 0 |
| 2026 | Wimbledon | qualifying | 113 | 0 |
| 2026 | US Open | qualifying | 113 | 0 |
| 2026 | Wheelchairs Wimbledon Men Singles | main | 2 | 0 |
| 2025 | Australian Open | main | 0 | 123 |
| 2025 | Roland Garros | main | 6 | 117 |
| 2025 | US Open | main | 0 | 123 |
| 2025 | Wimbledon | main | 0 | 123 |

The audit's claim is directionally correct for **2026 ATP men's singles Slam main
draws** (390 API-Tennis rows at best-of-3). It should not be applied as a blanket
correction: qualifying is correctly best-of-3, wheelchair is separate, historical
Sackmann main draws are already best-of-5, and the corpus contains no women's or
doubles rows against which to validate a broader rule. Any format correction should
be scoped to 2026 ATP men's singles Slam main-draw rows and owner-approved separately.

## 7. Agent 1 historical identity bridge — shadow plan only

Do not activate production. Current read-only measurements:

- 132,193 Agent 1 history observations.
- 14,480 observations involve a live-only player.
- The existing bridge would change 14,252 observations.
- Distinct player IDs would fall from 6,102 to 4,889 (1,213 live-only IDs merge into
  historical identities).

Future shadow evaluation plan:

1. **Canonical identity evidence** — for every proposed bridge, record live player ID,
   historical player ID, source IDs, canonical names, alias evidence, tournament/date
   context and the exact PIT activation time. Reject any bridge with multiple
   candidates or a name-only match without historical evidence.
2. **Before/after historical inputs** — run `load_agent1_history` with
   `bridge_model_identities=False` and `True` at the same cutoff; diff observation
   count, player IDs, surfaces, best-of, outcomes and match coverage.
3. **PIT correctness** — prove that no bridge activated after the prediction cutoff
   changes that prediction's inputs; compare the PIT-visible bridge set at each cutoff.
4. **Coverage** — count targets with cold-start players before/after, surface-unknown
   inputs, and matches dropped or added.
5. **Prediction deltas** — run the production A1 code on identical targets; report
   probability deltas, side flips, 60% threshold crossings, Brier/log-loss and
   Model Picks W/L deltas with confidence intervals.
6. **Acceptance** — forward shadow over at least ~150 scored Model Picks; no win-rate
   decline, Brier improvement, coverage loss ≤5%, and no PIT leakage. Owner approval
   and a new A1 config version are required before activation.

## 8. Exact owner approval questions

1. **132211:** Do you approve applying migration `0021_walkover_winner_optional`
   (WALKOVER may have no winner; COMPLETED/RETIRED still require one) and then
   appending WALKOVER with `winner_side=NULL`, no scoreline, at
   `2026-10-17T08:12:18.698820Z`, causing the 12 existing side-1 BET decisions to
   settle VOID?
2. **131036:** Do you accept the manual duplicate evidence despite the automatic
   40-minute `TIME_MISMATCH`, and approve appending only the
   `owner_approved_historical_duplicate_v1` reconciliation row linking 131036 to
   evidence 131137/result 131592, with no target result copy and no identity merge?
3. **130717:** Do you have an authoritative result for K. Smith v E. Winter? If not,
   should it remain unresolved?
4. **Grand Slam format:** Do you approve a separate, scoped proposal for 2026 ATP men's
   singles Slam main-draw rows only, excluding qualifying, wheelchair, historical
   Sackmann rows and all women's/doubles categories?
5. **Agent 1 bridge:** Do you authorize a read-only forward shadow evaluation using the
   plan above, with no production activation?

## 9. Estimates after approval

- Apply migration 0021 + 132211 correction + verify settlement: **~1–2 hours**, plus
  owner review of the resulting 12 VOID rows.
- Apply 131036 reconciliation row + verify no result/identity change: **~1 hour**.
- 130717 authoritative result retrieval and correction: **~1–2 days** including
  provider/official verification.
- Grand Slam format correction (2026 ATP men's singles main draw only): **~2–3 days**
  including adapter tests, dry-run backfill and verification.
- Agent 1 bridge shadow evaluation: **~3–5 days** of compute/analysis plus forward
  shadow time; no production activation.
