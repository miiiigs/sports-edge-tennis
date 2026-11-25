# Australian Open 2026 defect resolution and 2027 pipeline rehearsal — 25 November 2026

**Branch / PR:** `workstream/ao-2027-format-readiness-20261125` (PR #389)
**Verified starting head:** `2de4233cabf454f35e1257892670d285b402d7fc`
**Execution mode:** read-only production census; isolated local PostgreSQL for the rehearsal. No provider calls, production writes, migrations against production, or deployment.
**Historical census evidence:** `docs/research/evidence/ao_grand_slam_format_census_20261125.json`
**Rehearsal evidence:** `docs/research/evidence/ao_january_pipeline_rehearsal_20261125.json`
**Rehearsal script:** `ops/validation/ao_january_pipeline_rehearsal_20261125.py`

## 1. The measured 2026 defects

The historical census is unchanged and is not rewritten here.

| Defect | Measured | Root cause |
|---|---:|---|
| AO 2026 main-draw matches stored best-of-3 | **128** | API-Tennis supplied no `best_of` and the adapter defaulted every missing format to 3 |
| AO 2026 editions | **2** (`2248`, `2249`) | Provider name variant `ATP Australian Open` created a second edition keyed on `edition_name` |
| AO 2026 edition surface | **NULL** on both | Provider surface absent; adapter persisted `NULL` |
| AO 2026 edition end date | **NULL** on both | Adapter always passed `end_date=None` |
| AO 2026 qualifying round labels | **115** rows labelled `QF` / `SF` / `F` (64 + 32 + 16 + 3) | Provider qualifying draw lost the `Q` prefix |
| AO 2016–2025 main-draw QF rows flagged qualifying | **40** (4 per year) | Sackmann parser used `round_code.startswith("Q")`, so `QF` matched |
| AO 2021 identity split | main draw edition `1010`; qualifying identity `Doha Aus Open Qualies` (`358` / edition `1068`) | Separate provider identity; documented, not merged |

`edition 2249` is not a second tournament. It is the same canonical tournament `640` and the same provider tournament `1236`; five rows (2 × `R128`, 3 × qualifying `QF`) were written under the alias-derived edition name. The 2026 main-draw total is 126 rows in edition 2248 plus 2 in edition 2249. The extra `R128` / missing `R16` anomaly remains unresolved without an official draw and is not corrected.

The 115 qualifying rows are not main-draw quarterfinals. They are qualifying matches whose provider round codes lost the `Q` prefix; their stored `qualifying_flag` is true and their dates precede the first `R128`. They are classified as qualifying in the census by round code plus the observed main-draw start, not by the flag alone.

## 2. Future-ingestion fixes (no historical rewrite)

### Sackmann quarterfinal flag

`src/sports_edge_tennis/data/sources/sackmann.py` now derives qualifying only from the explicit qualifying rounds:

```python
qualifying_flag = round_code.strip().upper() in {"Q1", "Q2", "Q3"}
```

`QF` is a main-draw quarterfinal. This is the direct fix for the 40-row historical defect and prevents repetition in any future Sackmann import. A regression test parses an AO `QF` and `Q1` in the same file and asserts the flags and formats.

### API-Tennis AO 2027 contract

The existing 2027 rule is retained and tightened, not replaced with a generic guess:

- recognised reviewed aliases canonicalise to `Australian Open` (`ATP Australian Open` included), so the 2026-style edition split is not recreated;
- `GRAND_SLAM` tier, `HARD` surface and canonical `Australian Open <year>` edition name are enforced;
- a missing surface is filled with the reviewed AO surface `HARD`; a non-hard surface is rejected;
- main-draw rounds `R128`–`F` are best-of-5; qualifying `Q1`–`Q3` are best-of-3;
- a qualifying fixture carrying a main-draw round label is rejected generically (`qualifying_round_unverified`, and `ao_2027_unverified_round` in the AO path) rather than repeating the 115-row mislabelling;
- explicit contradictory tier, surface, round, format or qualifying metadata is rejected;
- a final supplies an evidence-based edition end date (the final's scheduled date); earlier rounds do not invent one;
- AO 2028+ is rejected as `ao_future_edition_unverified` rather than silently falling back to best-of-3.

The 2026 rows are deliberately not re-ingested or corrected by this change. Correcting them requires an independently authorised historical remediation with an official draw; adding a new "corrected" version now would silently move the historical truth.

### Qualifying and United Cup remain excluded

The API-Tennis qualifying path still resolves no Agent 2 scoring rule. United Cup and future-edition scope remain fail-closed. No production eligibility is expanded by this PR.

## 3. Isolated January operational rehearsal

The rehearsal ran against a disposable local PostgreSQL database (`postgres:16`, local TCP, no Railway host). The script refuses any DSN containing `railway` or a non-local host, drops only the local `public` schema, applies the migration manifest, and runs synthetic data.

Executed path:

| Stage | Result |
|---|---|
| Ingestion | 1 synthetic AO 2027 `R128` fixture persisted: `best_of_sets=5`, `GRAND_SLAM`, `HARD`, `qualifying=false`, edition `Australian Open 2027`, edition surface `HARD` |
| Agent 2 | `BO5_DECIDER_TB10_AT_6_6`; player-1 probability `0.861890…` under synthetic serve probabilities |
| Manager | Full slate completed; with no synthetic player history A2/A3 abstained, so the real Manager result was `NO_BET` / `TWO_OR_MORE_ABSTENTIONS` |
| Publication | A clearly labelled synthetic `CONSENSUS` parent was created only to exercise the downstream boundary; the BET decision published at `2026-11-25T03:00:00Z` with publication control enabled |
| Rescheduling | A second append-only `match_versions` row moved the fixture from 18 Jan to 19 Jan; the original start, first observation and PIT visibility were retained; the target guard still excludes inside the original 30-minute deadline (`ao_2027_reschedule_rehearsal_20261125.json`) |
| Settlement | A synthetic completed AO result was inserted; settlement created one `WON` decision settlement at `2027-01-18T16:00:00Z` |

The forced-consensus step is not a Manager result and is labelled as such in the evidence (`forced_consensus_for_downstream_rehearsal: true`, `actual_status: NO_BET`). It exists because the database trigger correctly refuses a BET without a CONSENSUS parent, and no synthetic history was loaded to make A2/A3 predict. The rehearsal proves the BO5 format crosses the ingestion, Agent 2, publication and settlement boundaries unchanged; it does not claim a real Manager consensus on an AO fixture.

### Verification of the BO5 path

- Parser and scoring-rule regressions cover every main-draw round and reject BO3 or contradictory metadata.
- The existing Markov implementation is exercised against an independent point-to-match reference for both best-of-3 and best-of-5.
- The isolated rehearsal persists a real BO5 version and reads it back unchanged before Agent 2 scoring.
- A PostgreSQL full-slate regression (`test_bo5_australian_open_target_runs_full_slate_and_keeps_format`) is added for CI; it asserts a `GRAND_SLAM` / `HARD` / `R128` / best-of-5 target completes the full slate and still resolves the BO5 rule.

### Rescheduling rehearsal

A separate isolated rehearsal exercises the append-only reschedule path:

- ingest an AO 2027 `R128` fixture for 18 January 2027;
- append a second `match_versions` row moving it to 19 January, observed after
  the original;
- rebuild the timeline directly from the stored rows: original start retained,
  latest start moved, classification `RESCHEDULED`, one start change, original
  PIT visibility retained, no retrospective flag;
- confirm the pre-match target guard admits the fixture with a two-hour lead and
  excludes it inside the original 30-minute deadline.

Evidence: `docs/research/evidence/ao_2027_reschedule_rehearsal_20261125.json`.
No historical row is rewritten; the second version exists only in the
disposable local schema.

### Regulation verification

The general AO/Slam format is verified from the official AO scoring guide and
the 2027 AO site header (10–31 January 2027). The **2027 edition-specific
Grand Slam rulebook was not located**; the latest published edition found was
2026. See
[`AO_2027_REGULATION_VERIFICATION_20261125.md`](AO_2027_REGULATION_VERIFICATION_20261125.md).
Release remains owner-gated.

### Realistic Agent 2 support rehearsal

The cold-start synthetic fixture is not the only support condition. A separate
read-only rehearsal loaded the internal stored history to a fixed cutoff
(2026-11-25T17:00Z, lookback from 2023) and built the same Agent 2 player and
population snapshot the live loader uses. It then selected a real AO main-draw
pairing from the stored hard-court editions and asked Agent 2 to price a
**synthetic** AO 2027 `R128` BO5 fixture between those two real players.

| Measure | Result |
|---|---|
| Loaded historical observations | 58,179 |
| Stored AO main-draw observations in lookback | 356 |
| Pairings above the Agent 2 support gates | 356 / 356 |
| Selected player support | 246 and 219 prior usable matches; 11,648/12,026 hard serve points; 12,313/12,700 hard return points |
| Agent 2 realistic support | `PREDICTED`, side 2, player-1 probability `0.426741`, win probability `0.573259` |
| Agent 2 cold-start control | `ABSTAINED`, `INSUFFICIENT_HISTORY` |

This demonstrates the support gate, not a forecast for the real 2027 draw. The
pairing is synthetic, the 2027 fixture does not exist, and no accuracy claim is
made. Evidence:
`docs/research/evidence/ao_2027_a2_support_rehearsal_20261125.json`.

## 4. What is still blocked

1. **Official AO 2026 draw** — needed to resolve the extra `R128` / missing `R16` rows and to authorise any historical correction. Not available.
2. **2027 edition-specific rulebook and owner approval** — the code now fails closed, but release still requires the official 2027 regulations.
3. **Real AO historical forecast cohort** — there are still zero original AO prediction targets, so no BO3-vs-BO5 accuracy comparison is possible.
4. **Edition dates** — a final now supplies an end date; the start date remains the earliest observed scheduled fixture and is not claimed as the official tournament start.
5. **Commercial rights** — the stored API-Tennis/Sackmann history is internal evidence, not a licensed dataset certification.

## 5. Validation executed

```text
python -m pytest tests/test_ao_2027_format_readiness.py tests/test_historical_sources.py tests/test_sportradar_fixtures.py -q
66 passed

python ops/validation/ao_january_pipeline_rehearsal_20261125.py \
  --out-json docs/research/evidence/ao_january_pipeline_rehearsal_20261125.json
REHEARSAL_OK

python ops/validation/ao_2027_reschedule_rehearsal_20261125.py \
  --out-json docs/research/evidence/ao_2027_reschedule_rehearsal_20261125.json
RESCHEDULE_REHEARSAL_OK

python ops/validation/ao_2027_a2_support_rehearsal_20261125.py \
  --out-json docs/research/evidence/ao_2027_a2_support_rehearsal_20261125.json
A2_SUPPORT_REHEARSAL_OK
```

## Verdict

The 2026 AO defect is a real ingestion-default and identity-metadata defect, not 128 correct best-of-5 matches. The 40-row QF flag defect has a concrete code cause (`startswith("Q")`) and is fixed for future Sackmann ingestion. The 115 qualifying rows are stripped-label qualifying records and are now rejected fail-closed when a future provider repeats that ambiguity. The 2026 split editions, NULL surface and NULL end date are documented; only future ingestion is corrected, and no historical row is silently rewritten. An isolated January rehearsal confirms best-of-5 survives ingestion, Agent 2, Manager handoff, publication and settlement. Qualifying and United Cup remain excluded.

## Evidence hashes

```text
docs/research/evidence/ao_january_pipeline_rehearsal_20261125.json
SHA256 E5B8C55CC4567C336A22FD80775E7DBAF3EB3A6D630F4EF020E86F83FEA27ADD

docs/research/evidence/ao_2027_reschedule_rehearsal_20261125.json
SHA256 52315BC26DA36F4E7E8D116FA4FE507E568D18B86B3B1547E1F5CA48FB857526

docs/research/evidence/ao_2027_a2_support_rehearsal_20261125.json
SHA256 AB8BCA878CE76C497BB5069FB8F106ADBAA4EBE3AA9538DAE5D8F29894374411
```
