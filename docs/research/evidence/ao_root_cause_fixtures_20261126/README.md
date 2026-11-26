# AO root-cause fixture manifests — 26 November 2026

Read-only export from the authorised production PostgreSQL database. No row was
rewritten.

| File | Rows | Meaning |
|---|---:|---|
| `ao_2026_main_draw_bo3.csv` | 128 | Stored 2026 AO main-draw rows with `best_of_sets=3` (126 genuine + 2 cancelled phantom R128) |
| `ao_2026_qualifying.csv` | 115 | Stored 2026 AO qualifying rows with stripped `QF/SF/F` labels (112 genuine + 3 cancelled phantom QF) |
| `ao_2016_2025_main_draw_qf_misflag.csv` | 40 | Main-draw QF rows (4 per season) with `best_of_sets=5` but `qualifying_flag=true` |
| `ao_2026_edition_2249_phantom.csv` | 5 | The five `CANCELLED` rows that created edition 2249 |

Columns: `match_id`, `match_version_id`, `tournament_edition_id`, `season_year`,
`round_code`, `best_of_sets`, `qualifying_flag`, `match_status`,
`match_start_at`, `outcome_status`, player names and ids, `stat_rows`,
`source_system`, `source_match_id`, `source_record_hash`,
`source_record_locator`, `source_ingest_run_id`, `version_source_locator`,
`version_source_file`, `version_source_hash`, `api_tennis_event_ids`.

The original provider payload is not retained for the 2026 API-Tennis backfill.
The source hash and locator identify the original row but cannot reconstruct it.
See `../ao_root_cause_and_dependency_20261126.json` for the full provenance and
set hashes.
