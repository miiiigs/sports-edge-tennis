# Australian Open historical match-format census — 25 November 2026

**Branch / PR:** `workstream/ao-2027-format-readiness-20261125` (PR #389)
**Verified starting head:** `09f574d1013344b5c70b21f0facc04e3b148137e`
**Execution mode:** authorised read-only PostgreSQL (`default_transaction_read_only=on`), no provider calls, no writes, no migrations, no deployment.
**Evidence JSON:** `docs/research/evidence/ao_grand_slam_format_census_20261125.json`
**Census script:** `ops/validation/ao_historical_format_census_20261125.py`

## Exact execution

The database DSN was resolved in memory from the linked Railway Postgres service and passed through `TENNIS_DATABASE_URL`; no credential is printed, logged or committed. The script sets a read-only transaction and a 300 s statement timeout. Command shape:

```powershell
$env:TENNIS_DATABASE_URL = <authorised read-only DSN resolved in memory>
python ops/validation/ao_historical_format_census_20261125.py \
  --out-json docs/research/evidence/ao_grand_slam_format_census_20261125.json
```

Validation executed:

```text
python -m pytest tests/test_ao_2027_format_readiness.py tests/test_sportradar_fixtures.py -q
45 passed in 4.24s
python -m py_compile ops/validation/ao_historical_format_census_20261125.py
COMPILE_OK
```

## What the stored historical census actually contains

Grand Slam men's singles main draw and qualifying rows are present from 2016 to 2026. The 2026 rows are a different ingestion regime from 2016–2025.

### Australian Open by edition

| Period | Tournament identity | Tier | Surface | Source | Main draw | Qualifying |
|---|---|---|---|---:|---:|---:|
| 2016–2025 | `Australian Open` (id 6) | `GRAND_SLAM` | `HARD` | `SACKMANN` | 127 per year | 112 per year (2016–2020, 2022–2025) |
| 2021 | `Doha Aus Open Qualies` (id 358) | `GRAND_SLAM` | `HARD` | `SACKMANN` | — | 112 |
| 2026 | `Australian Open` (id 640) | `ATP_TOUR` | `NULL` | `API_TENNIS` | 128 | 115 |

2021 is an identity split: the AO main draw is in edition 1010, while its qualifying draw is a separate tournament identity `Doha Aus Open Qualies` (edition 1068, Q1/Q2/Q3, best-of-3). It is not silently merged.

2026 is also split across two editions of tournament 640:

- edition `2248` — `Australian Open 2026`
- edition `2249` — `ATP Australian Open 2026`

Both are `ATP_TOUR`, both have `surface = NULL`, and both have `end_date = NULL`. This is a tournament-identity and surface-metadata defect relative to the 2016–2025 `GRAND_SLAM`/`HARD` rows.

### Draw separation

Qualifying and main draw were separated by round code plus the observed main-draw start, not by `qualifying_flag` alone. That is required because the stored `qualifying_flag` is wrong on some main-draw quarterfinals.

- **Main draw:** `R128, R64, R32, R16, QF, SF, F` on/after the edition's first `R128` start.
- **Qualifying:** `Q1, Q2, Q3`, or a main-round code scheduled before the first `R128` start (this captures the 2026 qualifying rows that were labelled `QF/SF/F`).

## Australian Open incorrect BO3 classifications

The actual measured defect is confined to 2026:

| Measure | Result |
|---|---:|
| AO main-draw matches stored `best_of_sets = 3` | **128** |
| AO main-draw matches stored `best_of_sets = 5` | 1,270 (2016–2025) |
| AO main-draw matches stored other than 5 | **128** (all 2026) |
| AO qualifying matches stored `best_of_sets = 3` | 1,123 |
| AO qualifying matches stored other than 3 | 0 |
| Main-draw QF rows wrongly flagged `qualifying = true` | **40** (4 per year, 2016–2025) |

2026 main-draw BO3 by round:

| Round | Stored BO3 matches | Expected main-draw matches | Delta |
|---|---:|---:|---:|
| R128 | 66 | 64 | **+2** |
| R64 | 32 | 32 | 0 |
| R32 | 16 | 16 | 0 |
| R16 | 7 | 8 | **−1** |
| QF | 4 | 4 | 0 |
| SF | 2 | 2 | 0 |
| F | 1 | 1 | 0 |
| **Total** | **128** | **127** | **+1** |

The 128 main-draw rows are not 128 correct best-of-5 matches. They are the stored 2026 AO main draw, all of which were ingested as best-of-3. Two extra `R128` rows and one missing `R16` row remain unresolved without the official draw. They are reported, not corrected.

### 2026 qualifying labels

The 2026 qualifying draw has no `Q1/Q2/Q3` round codes:

| Stored round code | Qualifying rows |
|---|---:|
| `QF` | 67 |
| `SF` | 32 |
| `F` | 16 |

These are qualifying rounds whose `Q` prefix was lost, not main-draw quarterfinals/semifinals/finals. They are separated by their pre-main-draw dates. The 2016–2025 qualifying rows correctly carry `Q1/Q2/Q3`.

### Quarterfinal records

- 2016–2025: 4 main-draw QF matches per year, all stored `best_of_sets = 5`, but all 40 rows carry `qualifying_flag = true`. The format is correct; the draw-stage flag is wrong.
- 2026: 4 main-draw QF matches, stored `best_of_sets = 3`, `qualifying_flag = false`. The format is wrong; the flag is right.
- 2026 qualifying `QF` rows (67) are qualifying matches with a stripped `Q` prefix.

## Affected original Agent 2 abstentions and Manager decisions

Measured directly:

| Original object | Australian Open rows |
|---|---:|
| `prediction_run_targets` | 0 |
| `predictions` (any agent) | 0 |
| `manager_consensus_results` | 0 |
| `decisions` | 0 |
| Grand Slam original predictions of any kind | 0 |

Therefore there are **zero affected original Agent 2 abstentions and zero affected original Manager decisions** for AO fixtures. The 2016–2026 AO rows are retrospective history, not original issued forecasts. They can affect later Agent 2 historical Markov state as training/history rows, but they were never AO prediction targets.

## BO3 versus correct BO5 comparison

**Blocked on lawful original inputs.** There are no original pre-match AO forecasts to pair. The same is true for every Grand Slam in the stored prediction runs. No matched original BO3/BO5 forecast pair exists, so no accuracy, Brier, log-loss or coverage comparison is reported. Constructing a retrospective replay and calling it an original forecast would violate the evidence standard.

## January tournament discovery using stored records

Stored January 2026 records demonstrate historical discovery capability for the January ATP/Challenger window:

- United Cup (25 matches), Brisbane (51), Hong Kong (39), Auckland (43), Adelaide (39), Australian Open (243), plus Challenger events.
- AO 2026 source identity: `API_TENNIS` tournament id `1236`; all 243 AO 2026 canonical matches have an API-Tennis source ID and a stored result row.
- 2016–2025 AO source identities: `SACKMANN` season IDs `2016-580` … `2025-580`; 2021 qualifying `2021-581`.

For January **2027**, the database currently contains **zero** tournament editions and zero fixtures. This is expected: 2027 is prospective and has not been discovered/ingested. Stored records prove the historical discovery path worked for January 2026; they do not prove 2027 coverage or the 2027 regulations.

## Identity, surface, version and scoring checks

- 2016–2025: edition surface `HARD`, version surface `HARD`, tier `GRAND_SLAM`. Consistent.
- 2026: edition surface `NULL`, version surface `NULL`, tier `ATP_TOUR`. Inconsistent with the Slam identity and with every prior AO edition.
- 2026 main draw has **zero** best-of-5 versions; all 128 are best-of-3. The only AO best-of-5 rows are 2016–2025.
- No historical row was rewritten. No 2027-specific rule was assumed or applied. The 2027 edition-specific rulebook still needs owner confirmation before release.
- No official draw was available, so the +2 `R128` / −1 `R16` anomaly is left unresolved.

## Remaining blockers

1. No lawful original AO pre-match forecast cohort exists in the database, so the BO3/BO5 predictive comparison cannot be executed.
2. No official AO 2026 draw export was available to resolve the extra `R128` and missing `R16` rows; they are not corrected.
3. The 2027 AO rules (main draw best-of-5, final-set TB10, qualifying best-of-3) still require the edition-specific 2027 rulebook and owner approval.
4. Commercial rights for the stored API-Tennis/Sackmann history remain unverified for commercial model validation; this census is internal read-only evidence, not a licensed dataset certification.

## Verdict

The historical census establishes a real, material AO 2026 data defect: **all 128 stored AO 2026 main-draw matches are best-of-3**, and **the 2026 tournament identity/surface metadata is wrong**. It also establishes a separate 40-row main-draw QF flag defect in 2016–2025 and a 2026 qualifying round-label defect. It does **not** establish any original Agent 2/Manager impact, because no AO original predictions exist, and it does **not** establish a measured BO3/BO5 accuracy difference. No historical correction or 2027 scope expansion is justified by this evidence alone.

## Evidence hashes

```text
docs/research/evidence/ao_grand_slam_format_census_20261125.json
SHA256 23BA84E2A033DEF54065D85556F9CF03F2139DA6D5B39108AEFAE5E3AA46ED82
bytes  184107

docs/research/AO_HISTORICAL_FORMAT_CENSUS_EXECUTION_20261125.md
SHA256 B1F93B33FB490BB6D906EA34C6DD510CE60716C7D272E3D3F2D14DA07F701775
```
