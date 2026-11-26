# Australian Open historical best-of-three defect — exact root cause and dependency report

**Date:** 26 November 2026
**Branch / PR:** `workstream/ao-2027-format-readiness-20261125` (PR #389)
**Trigger:** Brian's 25 November 2026 independent audit.
**Mode:** authorised read-only PostgreSQL. No provider call, no purchase, no
production write, no historical rewrite, no 2027 rule assumption.
**Evidence:**
[`ao_root_cause_and_dependency_20261126.json`](evidence/ao_root_cause_and_dependency_20261126.json)
and the fixture manifests in
[`ao_root_cause_fixtures_20261126/`](evidence/ao_root_cause_fixtures_20261126/).

## Verdict

The 128 incorrectly stored best-of-three records are **not** 128 provider-supplied
best-of-three matches. They are the complete stored 2026 Australian Open
main-draw population, ingested by the API-Tennis adapter at a time when a missing
format field defaulted to 3. The provider fixture schema has no best-of field.
The default is the cause, not the provider's format.

The defect does **not** invalidate the Agent 1 Elo results. Agent 1 validates but
never consumes `best_of_sets`; a controlled correction of the 229 completed 2026
AO history rows from best-of-three to best-of-five changes **zero** A1
probabilities on the 3,284-target settled population. A separate NULL-surface
defect does move A1, but that is a surface-metadata effect, not a best-of effect,
and its measured Brier effect is small and slightly negative.

No historical row was rewritten. The 2026 correction remains blocked on the
official draw and an owner decision.

## 1. Exact population

| Measure | Count | Exact manifest |
|---|---:|---|
| AO 2026 main-draw rows stored `best_of_sets=3` | **128** | `ao_2026_main_draw_bo3.csv` |
| AO 2026 qualifying rows stored `best_of_sets=3` | **115** | `ao_2026_qualifying.csv` |
| AO 2016–2025 main-draw QF rows flagged qualifying | **40** | `ao_2016_2025_main_draw_qf_misflag.csv` |
| AO 2026 edition-2249 phantom/cancelled rows | **5** | `ao_2026_edition_2249_phantom.csv` |

Every manifest row carries `match_id`, `match_version_id`,
`tournament_edition_id`, `season_year`, `round_code`, `best_of_sets`,
`qualifying_flag`, status, start, result status, players, the API-Tennis event
id and the retained source id/hash/locator/ingest run. The JSON also carries the
complete `fixture_manifests` arrays and set SHA-256 hashes.

### 2026 main draw by edition and round

| Edition | R128 | R64 | R32 | R16 | QF | SF | F | Total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2248 `Australian Open 2026` | 64 | 32 | 16 | **7** | 4 | 2 | 1 | **126** |
| 2249 `ATP Australian Open 2026` | **2** | 0 | 0 | 0 | 0 | 0 | 0 | **2** |
| **Stored total** | **66** | 32 | 16 | **7** | 4 | 2 | 1 | **128** |
| Official expectation | 64 | 32 | 16 | 8 | 4 | 2 | 1 | 127 |

All 128 rows are `best_of_sets=3`, including the final. The round distribution is
not a set of 128 correct best-of-five matches: two of the R128 rows are cancelled
phantom fixtures and one genuine R16 match is missing. Of the 128 stored rows,
**126 are genuine main-draw matches**; the official main-draw match count is 127,
with one genuine R16 match absent. The audit's "128 records" is the stored
misclassified row count, not 128 official matches.

### 2026 qualifying by edition and round

| Edition | QF | SF | F | Total |
|---|---:|---:|---:|---:|
| 2248 | 64 | 32 | 16 | **112** |
| 2249 | **3** | 0 | 0 | **3** |
| **Stored total** | **67** | 32 | 16 | **115** |
| Official expectation | 64 | 32 | 16 | 112 |

The 115 rows are qualifying matches. Their round labels lost the `Q` prefix; they
are not main-draw quarterfinals, semifinals or finals. Their starts are before the
first main-draw R128 start. Of the 115 stored rows, **112 are genuine qualifying
matches** (64 QF, 32 SF, 16 F) and 3 are cancelled phantom QF rows. The official
qualifying draw is 112 matches.

### 2016–2025 quarterfinal flag

Four main-draw QF matches per season, 2016–2025, are stored `best_of_sets=5`
and `round_code='QF'` but `qualifying_flag=true`. The format is correct; only the
draw-stage flag is wrong. The complete 40-row manifest is in the CSV.

## 2. Original provider payloads

**The full original payload is not retained for the 2026 AO backfill.** The
retained provenance is:

- `match_source_ids` / `match_version_sources`: source system, source match id,
  source record hash, row/file locator, source-file hash and ingest run;
- `provider_fixture_metadata` retention begins **2026-10-07**, while the AO 2026
  rows were ingested **2026-09-13**. The AO source rows have **zero**
  `provider_fixture_metadata` rows.

Therefore a byte-for-byte statement about the 2026 provider payload is not
possible from the database alone. What is established:

1. The public API-Tennis fixture schema contains `event_key`, `event_date`,
   `event_time`, both players, `event_status`, `event_type_type`,
   `tournament_name`, `tournament_key`, `tournament_round`,
   `tournament_season`, `event_live` and `event_qualification`. **It has no
   best-of field.**
2. At the ingestion base commit `ad65894`, `_best_of_sets` returned **3** when
   the optional `best_of_sets` / `event_best_of_sets` field was absent.
3. Every stored 2026 row is 3 across every round, including the final. That is
   the signature of a missing-field default, not a provider that supplied
   round-specific formats.

Conclusion: **the provider did not supply BO3; ingestion/normalization
introduced the default.** This is established by the provider schema and the
adapter code, with the original payload caveat stated explicitly.

For the Sackmann 2016–2025 rows, the original CSV files are also not retained in
this repository. The stored row locator (for example
`atp_matches_2016.csv#261`) and the source-file hash identify the original row,
but the files themselves are external. The 40 QF rows prove the parser behaviour,
not a provider format error.

## 3. Root cause by defect

### 3.1 Best-of-three default (128 rows)

- File: `src/sports_edge_tennis/data/sources/api_tennis_live.py`
- Base commit `ad65894`: `_best_of_sets(value)` returns `3` for `None`/empty.
- The API-Tennis scheduled-fixture payload has no best-of field.
- The 2026 AO main draw was ingested on 2026-09-13 through four
  `api_tennis_scheduled_fixtures` runs (15677, 15679, 15681, 15683).
- No historical version was corrected. PR #389 changes only future AO 2027
  ingestion: the recognised round supplies BO5, and missing/contradictory format
  is rejected.

### 3.2 Qualifying labels (115 rows)

- The stored rows have `qualifying_flag=true`, `round_code` in `QF/SF/F`, and
  starts before the first main-draw R128.
- The Q-prefix fallback in `_qualifying_flag` would return **false** for `QF`.
  The stored `true` therefore came from the provider's explicit
  `event_qualification` field, while the provider round label had no `Q` prefix.
- The original payload is not retained, so the exact `event_qualification` token
  cannot be re-read. The stored flag/round/date combination is decisive about the
  parser path.
- Future ingestion now rejects a qualifying draw whose round lost the Q prefix
  (`qualifying_round_unverified`).

### 3.3 Quarterfinal flag (40 rows)

- File: `src/sports_edge_tennis/data/sources/sackmann.py`
- Base commit `ad65894`: `qualifying_flag = round_code.startswith("Q")`.
- `QF` therefore matched. The branch now uses an explicit `{"Q1","Q2","Q3"}`
  set and a regression parses AO `QF` and `Q1` in the same file.

### 3.4 Two 2026 edition identities (2248 / 2249)

- `repository.get_or_create_tournament_edition` keys the edition on
  `(tournament_id, season_year, edition_name)`.
- API-Tennis display-name variants (`Australian Open 2026` and
  `ATP Australian Open 2026`) resolve to the same `tournament_id` (640, source
  tournament 1236) but create two editions.
- Edition 2248 contains the genuine draw (126 main + 112 qualifying). Edition
  2249 contains only five cancelled phantom rows (2 R128 + 3 QF).
- This is **not AO-specific**: 33 tournament/season pairs in 2026 have the same
  `X 2026` / `ATP X 2026` split, including Brisbane, Auckland, Doha, Dallas,
  Rotterdam and Montpellier. The AO case is one instance of a systemic edition
  identity key.
- The wrong `ATP_TOUR` tier has the same origin: API-Tennis supplies the generic
  `event_type_type` "Atp Singles", which the historical classifier maps to
  `ATP_TOUR`. The 2016–2025 Sackmann level `G` supplied `GRAND_SLAM`. The 2027
  AO contract now enforces `GRAND_SLAM` explicitly.

### 3.5 NULL surface and missing edition end date

- Every 2026 tournament edition has `end_date = NULL`; most have
  `surface = NULL`, including both AO editions.
- The API-Tennis scheduled-fixture payload has no edition surface or end date.
  At the base commit the adapter had no edition-end fallback; the 2027 branch
  adds an evidence-based end date only for a future AO final.
- This is a metadata gap, separate from the best-of default. It is the one
  defect with a measured A1 effect (section 5).

### 3.6 Bracket-count discrepancy

- Edition 2248 has 64 R128, 32 R64, 16 R32, **7 R16**, 4 QF, 2 SF, 1 F = 126.
- The two R32 winners absent from R16 are **Novak Djokovic** (R32 match 119014)
  and **Jakub Mensik** (R32 match 119013). Their R16 match is the missing genuine
  match. Djokovic appears in the QF, so he advanced; Mensik does not.
- Edition 2249 adds two cancelled R128 phantom rows:
  - 118731 `A. Cazaux` vs `J. Faria` (event 12096959)
  - 118788 `M. Berrettini` vs `A. De Minaur` (event 12096865)
- Those two rows produce the 66 R128 count and the 128 main-draw total. Excluding
  them leaves 126 genuine main-draw rows: one fewer than the official 127.
- The 115 qualifying count is 112 genuine + 3 cancelled QF phantom rows in 2249.

Exact edition-2249 phantom rows (all `CANCELLED`):

| Match id | Version id | Round | Players | API-Tennis event |
|---:|---:|---|---|---|
| 118473 | 122402 | QF (qualifying) | Y. Hanfmann vs B. Hassan | 12095814 |
| 118546 | 122475 | QF (qualifying) | K. Coppejans vs K. Nishikori | 12095799 |
| 118564 | 122493 | QF (qualifying) | V. Sachko vs M. Mmoh | 12095839 |
| 118731 | 122660 | R128 | A. Cazaux vs J. Faria | 12096959 |
| 118788 | 122717 | R128 | M. Berrettini vs A. De Minaur | 12096865 |

The +2 R128 / −1 R16 anomaly is fully reconciled as **2 phantom R128 rows plus 1
missing genuine R16 match**. It is not evidence of 128 correct Slam matches.

## 4. Original prediction, Manager and A2 dependency

| Object | AO rows |
|---|---:|
| `predictions` (any agent) | **0** |
| `prediction_run_targets` | **0** |
| `manager_consensus_results` | **0** |
| `decisions` | **0** |
| Published decisions | **0** |

There are **zero affected original Agent 2 abstentions and zero affected Manager
decisions** for AO fixtures. The 2016–2026 AO rows are retrospective history, not
original issued forecasts.

### Agent 2

- AO 2026 has **zero** `match_stat_observations`. API-Tennis supplied no serve
  statistics for the backfill, so no AO 2026 row can enter A2 serve/return
  fitting.
- The 40 main-draw QF rows have `best_of=5` and `round=QF`. A2 resolves the
  main-draw BO5 rule; the qualifying-flag disagreement is diagnostic and does not
  block the update. 37 of the 40 are `COMPLETED` with two coherent stat rows and
  are A2-eligible; the other 3 are non-completed results. The flag defect
  therefore removes no A2 history row.
- The 115 qualifying rows are BO3 and have no 2026 stats.

### Agent 1

- 229 completed AO 2026 matches enter the A1 history ledger.
- Agent 1 validates `best_of_sets > 0` but never uses the value in rating updates
  or prediction. The regression
  `test_best_of_sets_does_not_change_v1_probability` asserts equality.
- The controlled three-path counterfactual, including Agent 2 and Manager, is in
  [`AO_MATCH_FORMAT_DEPENDENCY_MATRIX_20261126.md`](AO_MATCH_FORMAT_DEPENDENCY_MATRIX_20261126.md).
- The surface is NULL on all 229 rows, so they update overall Elo only. This is a
  separate surface-metadata effect.
- The 5 cancelled phantom rows are skipped by the A1 cancelled policy.

## 5. Measured A1 dependency (do not assume invalidation)

The read-only counterfactual replays the unchanged A1 code over the same target
population used by the A1 identity-bridge workstream (A1 targets since
2026-09-10; 3,792 targets, 3,284 settled `COMPLETED`). It applies corrections
only inside history; no target identity or target surface is changed.

| Variant | Changed A1 probabilities | Mean abs delta | Max abs delta | Accuracy | Brier | Log loss |
|---|---:|---:|---:|---:|---:|---:|
| Stored | — | — | — | 62.089% | 0.231141 | 0.655031 |
| **BO3 → BO5 only** | **0 / 3,284** | **0.0** | **0.0** | **62.089%** | **0.231141** | **0.655031** |
| NULL surface → HARD only | 1,664 / 3,284 | 0.004861 | 0.100366 | 62.211% | 0.231404 | 0.655607 |
| Surface + BO5 | 1,664 / 3,284 | 0.004861 | 0.100366 | 62.211% | 0.231404 | 0.655607 |

**Conclusion:** the best-of-three defect has **exactly zero** effect on Agent 1
ratings and on the A1 evaluation results. The surface defect is real: it changes
half of the settled target probabilities by a small amount, and on this
population it slightly worsens Brier/log loss and slightly improves accuracy. It
is not a reason to discard the Agent 1 Elo results, but it is a reason to keep
the surface metadata defect separate and to prefer correcting it only with
source-backed data.

The dedicated handshake query in section 7, which additionally requires
`prediction_run_targets` and the `ingested_at` guard, returns 3,285 settled
targets at the same timestamp. The one-row difference is a target-visibility
filter, not a contradiction; the BO5-only zero result is independent of the
population because it follows from the A1 code path.

## 6. Evaluation target versus historical feature observation

This distinction is the core of the dependency answer:

- **Evaluation target:** the fixture whose forecast/outcome is scored. No AO
  fixture is an evaluation target in any stored prediction run. The AO 2026 rows
  cannot change an evaluation label or an issued forecast.
- **Historical feature observation:** a past match used to build model state. The
  229 completed AO 2026 rows are historical observations in the A1 ledger. Their
  best-of label has no effect because A1 ignores it; their NULL surface has a
  measured, separate effect.

The 3,152 / 3,201 / 3,223 populations are evaluation-target populations from the
A1 identity-bridge workstream, not AO evaluation sets. AO is January 2026; those
populations start 2026-09-10. Their AO intersection is structurally zero.

## 7. Coordination with DeepSeek 1

This branch emits the exact handshake needed for the fixture-set comparison:

- AO 2026 all-fixture set SHA-256 and 243 ids;
- AO 2026 main-draw BO3 set SHA-256 and 128 ids;
- AO 2026 qualifying set SHA-256 and 115 ids;
- reproduced A1 target and settled set SHA-256 values for 3,792 / 3,284 current
  rows.

Status: **PREPARED, NOT CONFIRMED.** Exact equality to the declared 3,152 /
3,201 / 3,223 populations requires DeepSeek 1's frozen fixture-id manifest or
set hash. The AO intersection with the reproduced A1 target and settled sets is
already **0**. Do not claim the three populations are identical until the hashes
match.

The dedicated handshake artifact is
[`ao_research_population_handshake_20261126.json`](evidence/ao_research_population_handshake_20261126.json)
(script `ops/validation/ao_research_population_handshake_20261126.py`). It
reproduces the A1 workstream's four-way target query exactly:

| Measure | Current reproduction |
|---|---:|
| A1 targets since 2026-09-10 | 3,793 |
| Settled `COMPLETED` targets | 3,285 |
| Earliest-issued targets | 134 |
| Target-set SHA-256 | `9b1c871f71a9b0645119130466ead4abd923403736cfa9474cb6c99559acca87` |
| Settled-set SHA-256 | `b680df7db63b1108ad1328a8f871ee4563e5dc56fa5dd6aa96b18ec5e003c30c` |
| AO 2026 fixture ids | 243 |
| AO 2026 fixture-set SHA-256 | `70f6a65ee14af417955a766dc96485acf4bc7db9f2ba24f3157b0778c20dce14` |
| AO ∩ targets / settled | **0 / 0** |

The counts 3,793 / 3,285 are the current database state, not a contradiction of
the declared 3,152 / 3,201 / 3,223. Those were earlier frozen snapshots with
different settled filters. The comparison is only exact when DeepSeek 1 supplies
the frozen manifest; the zero AO intersection is already established by the date
scope and by both reproductions.

## 8. Experiments that genuinely require reconsideration

| Experiment | BO3 reconsideration | Surface reconsideration | Action |
|---|---|---|---|
| A1 identity-bridge retrospective/forward shadow | **No** | **Yes, bounded** | Keep the result; apply the surface sensitivity bound. Do not re-score it as a BO3 defect. |
| A1 real-label-shuffle negative control | **No** | **Yes, bounded** | Re-run only if the surface bound is material; the BO3 label is irrelevant to A1. |
| Agent 2 Grand Slam support / BO5 rehearsal | **No** | No | The 2026 AO has zero A2 stats and was never an AO target; the rehearsal remains synthetic 2027 pairing. |
| Manager C / Challenger forward experiments | **No** | No | Challenger scope; no AO fixture in the population. |
| AO historical format census | **No** (it is the measurement) | No | Keep as descriptive evidence; correct only the bracket/edition interpretation in this report. |

No previous experiment is invalidated by the best-of-three label. The only
genuine follow-up is a bounded note on the surface defect for A1-history
experiments.

Reference populations in the A1 workstream:

- **3,152** — "same 3,152 settled fixtures" retrospective A1 identity-bridge
  comparison (`A1_IDENTITY_BRIDGE_FORWARD_SHADOW_20261124.md` §1).
- **3,201** — `settled_targets` in the equal-coverage comparison
  (`a1_identity_bridge_equal_coverage_20261124.json`).
- **3,223** — safely completed targets in the four-way comparison
  (`A1_IDENTITY_BRIDGE_FORWARD_SHADOW_20261124.md` §9.3).

All three are A1 **evaluation-target** populations from 2026-09-10 onward. AO
2026 is January 2026 and is not in any of them. AO rows can only be historical
A1 features; the measured BO5-only effect on those features is zero.

## 9. Mathematical acceptance

Brian's requested independent-set regression:

- set-win probability `p = 0.60`;
- BO3 match probability `p²(3 − 2p) = 0.648`;
- BO5 match probability `Σ_{k=3..5} C(5,k) p^k (1−p)^(5−k) = 0.68256`.

The regression is in
`tests/test_ao_2027_format_readiness.py::test_simple_independent_set_probability_reference_at_sixty_percent`.
The controlled Agent 1 / Agent 2 / Manager counterfactual is in
[`AO_MATCH_FORMAT_DEPENDENCY_MATRIX_20261126.md`](AO_MATCH_FORMAT_DEPENDENCY_MATRIX_20261126.md):
Agent 1 delta 0.000000, Agent 2 delta 0.0349056986…, Manager delta
0.0116352328… (exactly one third of the Agent 2 delta, with no second format
adjustment).
The serve-based Markov engine is verified separately by
`test_existing_markov_against_independent_point_to_match_reference`,
`test_markov_uses_different_match_length_and_symmetric_equal_players` and
`test_serve_based_markov_deciding_set_tiebreak_is_effective_and_symmetric`,
covering deciding-set TB10 vs advantage, first-server marginalisation and
player-swap symmetry. These are mathematical checks, not forecast results.

## 10. Runner, dashboard and future identity provenance

**Runner compatibility.** The runner reads the latest PIT `match_versions` row
and passes `surface`, `competition_tier`, `best_of_sets`, `qualifying_flag` and
round to the resolver. The new evidence work is read-only ops code and adds no
runner dependency. The AO 2027 candidate changes the future parsing path only;
it does not alter stored 2026 rows, the runner query or the Manager contract.

**Dashboard compatibility.** The aligned Model Picks read model has no
tournament, season or tier filter; it reads `manager_consensus_results`,
`predictions`, `prediction_runs` and the PIT `match_versions` lateral. A
correctly ingested AO 2027 pick would therefore be visible without a dashboard
change. The structural regression
`test_dashboard_aligned_read_model_is_not_tournament_or_season_scoped` asserts
this.

**Future identity provenance.** PR #388's `identity_resolution_provenance`
migration (`0025`) is additive. The live runner does not yet write runtime
resolution records, and the dashboard does not read them. Nothing in this PR
creates a schema dependency on that table. When the runner is wired to persist
provenance, the evidence scripts here remain read-only consumers and need no
change. Do not treat the absence of provenance rows as a dashboard error.

**January classification.** Stored 2026 Brisbane, Hong Kong, Adelaide, Auckland,
United Cup and Australian Open editions are ATP/Challenger main-draw and
qualifying fixtures with BO3 main draws. The AO identity/tier and all 2026
edition surfaces/end dates are wrong in storage (section 3.4–3.5). Those are
historical metadata defects, not a runner/dashboard incompatibility. The 2027
parser candidate enforces `GRAND_SLAM`/`HARD`/BO5 for the AO main draw and keeps
qualifying and United Cup outside the AO scope.

| Stored 2026 event | Stored tier | Main / qualifying | Format | Edition hygiene |
|---|---|---:|---|---|
| Brisbane | ATP_TOUR | 33 / 18 | BO3 | split 2234/2235; NULL surface/end |
| Hong Kong | ATP_TOUR | 27 / 12 | BO3 | single edition; NULL surface/end |
| Adelaide | ATP_TOUR | 27 / 12 | BO3 | single edition; NULL surface/end |
| Auckland | ATP_TOUR | 27 / 16 | BO3 | split 2242/2243; NULL surface/end |
| Australian Open | **ATP_TOUR (wrong)** | 126 genuine / 112 genuine (+5 phantom) | **BO3 (wrong)** | split 2248/2249; NULL surface/end |
| ATP United Cup | ATP_TOUR | 25 / 0 | BO3 | outside AO scope |

The 2027 parser candidate is not a correction of these stored rows. It changes
future ingestion only.

**Separate populations.** Official fixture completeness remains **UNKNOWN**.
Internal stored fixtures, original Manager consensus, tracking-only selections
and published Model Picks are distinct populations. January 2026 has zero
stored predictions, targets, Manager consensus, decisions and publications in
this database; internal coverage must not be described as official tour
coverage.

## 11. Acceptance status

| Requirement | Status | Evidence |
|---|---|---|
| Exact fixture/version/edition/year/round population | **PASS** | JSON + 4 CSV manifests |
| Original payload retained? | **NO** — bounded, stated | retention begins 2026-10-07; AO ingested 2026-09-13 |
| Provider supplied BO3? | **NO** — ingestion default | provider schema + base-commit code |
| Two 2026 edition identities reconciled | **PASS** | 2248 genuine 126+112; 2249 5 cancelled phantom |
| NULL surface / end date reconciled | **PASS (measured)** | systemic 2026 API-Tennis metadata gap |
| 115 qualifying labels | **PASS** | 112 genuine + 3 cancelled QF phantom |
| 40 QF flag rows | **PASS** | `startswith("Q")` at base commit |
| Agent 1 dependency quantified | **PASS** | BO5-only delta 0; surface-only delta measured |
| Agent 2 dependency | **NONE for 2026** | zero AO 2026 stat rows |
| Manager dependency | **ZERO** | zero targets/consensus/decisions |
| Evaluation target vs feature observation | **PASS** | AO is history only |
| DeepSeek 1 3,152/3,201/3,223 comparison | **PREPARED, NOT CONFIRMED** | set hashes emitted; intersection 0 |
| Historical rewrite | **NONE** | read-only evidence |
| 2027 rulebook | **STILL CONDITIONAL** | edition-specific 2027 rulebook not located |
| Official 2026 draw correction | **BLOCKED** | no official draw export |

## 12. Outstanding blockers

1. DeepSeek 1 must supply the frozen 3,152 / 3,201 / 3,223 fixture-id manifests
   or set hashes for exact equality confirmation.
2. The official AO 2026 draw export is still needed to decide whether the missing
   Djokovic–Mensik R16 row and the two cancelled R128 phantom rows should be
   corrected. No historical rewrite is authorised here. The provider documents a
   `get_draw` method (`tournament_key=1236`, `tournament_season=2026`,
   `include_qualification=1`); using it would require explicit owner approval and
   is not called by this branch.
3. The 2027 edition-specific rulebook remains unlocated; the conditional release
   status is retained.
4. Commercial rights for the stored API-Tennis/Sackmann history remain
   unverified; this is internal evidence, not a licensed dataset certification.
5. The Railway scheduler trigger failure remains unexplained; PR #391 makes a
   recurrence detectable but does not repair the platform fault.

## 13. Evidence execution

```powershell
$env:TENNIS_DATABASE_URL = <authorised read-only DSN>

python ops/validation/ao_a1_surface_sensitivity_20261126.py `
  --out-json docs/research/evidence/ao_a1_surface_sensitivity_20261126.json

python ops/validation/ao_root_cause_and_dependency_20261126.py `
  --out-json docs/research/evidence/ao_root_cause_and_dependency_20261126.json `
  --out-csv-dir docs/research/evidence/ao_root_cause_fixtures_20261126 `
  --a1-sensitivity-json docs/research/evidence/ao_a1_surface_sensitivity_20261126.json

python ops/validation/ao_research_population_handshake_20261126.py `
  --out-json docs/research/evidence/ao_research_population_handshake_20261126.json

python -m pytest tests/test_ao_2027_format_readiness.py `
  tests/test_historical_sources.py tests/test_sportradar_fixtures.py -q
```

Both evidence scripts set `default_transaction_read_only=on` and a statement
timeout. They do not write to the database, call a provider or create a
migration. The A1 sensitivity script imports the unchanged production Agent 1
engine and replays only the target population; the surface variant is a
counterfactual, not a historical correction.

## 14. Evidence hashes

These are SHA-256 values of the **committed Git blobs** (LF-normalised). Verify
with `git show HEAD:<path> | sha256sum`; a Windows checkout may materialise CRLF
and therefore hash differently on disk.

```text
docs/research/evidence/ao_root_cause_and_dependency_20261126.json
SHA256 58AA65B7321F672F344F9E2BA153587873167845AA9C3F57D3DB4EDE8238B448
blob bytes  387351

docs/research/evidence/ao_a1_surface_sensitivity_20261126.json
SHA256 ADE6120676FEB87EC9040AD216A199035A80E78D5E4BD660B01EA39816E015D4
blob bytes  2375

docs/research/evidence/ao_match_format_dependency_matrix_20261126.json
SHA256 C076D870B04C227781B120B6E978C1E5F034992ED3E1B7A9AA3DC00534B7C218
blob bytes  2194

docs/research/evidence/ao_research_population_handshake_20261126.json
SHA256 D392C1A40541CC7A54B804B66B9143720521CB73356F6173604928EB872603FB
blob bytes  4748

docs/research/evidence/ao_root_cause_fixtures_20261126/ao_2026_main_draw_bo3.csv
SHA256 517CF6A2AB8366D8011ED6105CC9C890D0F31D96D33C0C34BC5331314AB5AE63

docs/research/evidence/ao_root_cause_fixtures_20261126/ao_2026_qualifying.csv
SHA256 7F6F85C2A81E6235F76E2F6C73AC8423C092F1762773E1DFCE9D249A2C3AE6D4

docs/research/evidence/ao_root_cause_fixtures_20261126/ao_2016_2025_main_draw_qf_misflag.csv
SHA256 7C938A1124C108D561F1AA43DB1C10D2B5364D5531E15AC6CAE053F542682F4E

docs/research/evidence/ao_root_cause_fixtures_20261126/ao_2026_edition_2249_phantom.csv
SHA256 485BD2A415E5D7B394B3FC0F6E972FCC057C9B2E2F9DE94E0A4DAB699B361BDD
```

The JSON embeds the A1 sensitivity evidence; its hash changes if that embedded
file changes. Recompute the hashes after any evidence rerun.
