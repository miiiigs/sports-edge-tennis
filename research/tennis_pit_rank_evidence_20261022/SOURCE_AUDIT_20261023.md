# Historical ATP/WTA ranking source and availability audit — 2026-10-23

**Research only / no customer claim / no model-policy change.** Frozen-study baseline provided by the independent Oct 22 research handoff: 866 eligible prediction records, **0 verified official pre-match rank values**. That 866-row underlying export was **not available in this GitHub branch**, so no newly computed empirical denominator, rank coverage, band accuracy, or exclusion estimate is claimed. The path `research/tennis_ranking_band_study_20261022/` reported in the handoff was not present in the inspected main/research tree or in this PR branch; it may be held as a local artifact outside source control. Do not replace that freeze with a later production extract without documenting the change.

## Source decision ledger

| Candidate | Authority and available history | Effective / release evidence | IDs and coverage | Usage constraints | Decision |
|---|---|---|---|---|---|
| ATP Tour rankings https://www.atptour.com/rankings/singles | Official ATP, rank date-week selector | Published week is displayed; **exact original release timestamp must be evidenced**, not inferred from Monday 00:00 | ATP player profile IDs require an audited mapping to canonical IDs; inspect singles beyond #600 | ATP website terms explicitly bar systematic ranking retrieval and any commercial/wagering uses absent written permission: https://www.atptour.com/en/terms-and-conditions | **Source authentic; bulk enrichment NOT AUTHORIZED by public terms** |
| WTA official player stats and rankings https://www.wtatennis.com/rankings/singles/ , https://www.wtatennis.com/players/333071/ekaterina-perelygina/stats | Official WTA weekly singles rank histories visible per profile (including 401–600 examples) | Listed ranking week, but historical exact publication instant not independently established | WTA profile numeric IDs and name require audited canonical crosswalk; record gaps/unranked | No explicit commercial database reuse permission established | **Authentic snapshots, acquisition and publication-time gates open** |
| Sportradar Tennis v3 rankings https://developer.sportradar.com/tennis/reference/rankings | Licensed provider publishes current ATP/WTA lists, updated generally Mondays | `generated_at` on captured API response can establish observed time, but does **not** retrospectively prove an uncaptured historical snapshot | Provider `sr:competitor:...` IDs; integration example shows **top 500 per tour**, insufficient for complete 401–600 and 601+ | Entitlements and permission to persist historical snapshots must be confirmed in active account | **Potential prospective shadow input only**, no verified historical archive |
| Matchstat / RapidAPI dated ranking https://tennisapidoc.matchstat.com/rankings | Third-party **archive of rankings** (not direct ATP/WTA publication) for ATP and WTA, dated weekly singles lists | Snapshot date available; precise original official release timestamp absent from documented fields | Third-party player IDs, pagination; coverage to be benchmarked | Current access key, retention, research, and commercial permissions unverified | **Proxy/candidate pending licensing and independent official verification** |
| Cito API historical rankings https://docs.citoapi.com/docs/api/tennis/ | Third-party claims multi-decade ATP/WTA weekly ranking history | Weekly historical date; original publication-at-time proof not established | Stable provider IDs but mapping and per-week missingness untested; key required | License, cost, retention and data provenance require authorization | **Candidate only; no automatic official status** |
| Sackmann ATP/WTA archive mirror https://github.com/Aneeshers/tennis-sackmann-archive | Reconstructed/repackaged historical weekly ranking files (not original official archive) | Ranking date only, not verified original availability at prediction timestamp | Sackmann IDs require crosswalk, historic weekly coverage has gaps | CC BY-NC-SA 4.0 **non-commercial**, cannot use for this commercial/wagering platform without an appropriate license | **BLOCKED for production research** |
| Repo `rating_snapshots` / other Elo | Derived Elo rating, **not ATP/WTA tour ranking** | Model snapshot timestamps are not official release timestamps | Model-specific identities | Internal data, different metric | **Never substitute as official ranking** |

Sources consulted October 23, 2026. Documented API availability is **not evidence of contracted entitlements, response correctness, historical source release time, or permission to redistribute**. No ranking provider API or paid endpoint was invoked for this investigation. Site ToS should be checked by counsel or account owner before any bulk retrieval.

## Required signed source evidence for every enriched rank

- `authority` = tour `ATP` or `WTA`, `source_kind` = `OFFICIAL`, `ranking_type` = `SINGLES`.
- `effective_date` exact original ranking week date (YYYY-MM-DD), **not** tournament week or a retroactively calculated effective date.
- `source_published_at` original verified release timestamp with timezone, `published_at_verified: true` and a nonempty immutable `release_evidence_id`. **A page date alone is insufficient.** Archives retrieved later are acceptable only if original publication at or before the pick is independently evidenced.
- `player_id` is the **exact canonical repo player ID**, with authoritative provider-ID crosswalk documentation keyed by `identity_evidence_id` and `identity_verified: true`. Reject name-only matching, aliases, homonyms, new/old team IDs, uncertain WTA/ATP/ITF identity.
- `rank` is an actual positive integer, never career-high rank, later rank, interpolated rank, race ranking, Elo, protected entry ranking or draw seed. Unranked must not be inferred solely from absence in a truncated top-500 provider list.
- `source_url`, `source_record_id`, `snapshot_sha256` full SHA-256 and `license_authorized: true` backed by documented rights to fetch/store/analyze commercially. `source_published_at` must not exceed original `picked_at`.
- Official weekly snapshot must be effective on or before actual match event day and be no more than 35 days old (conservative explicit research freshness bound). ATP and WTA are never interchangeable. No forward-filling across unspecified ranking suspensions.
- Record coverage denominators separately for tours, populations, selected/opponent ranks, both ranks, missingness and source week; count conflicting same-week evidence as missing, not as a winner.

## Frozen prediction file interface

`enrichment.py` is fully offline and takes two JSON Lines input files, independently hashed. Each original prediction row must contain:
`pick_id, canonical_match_id, prediction_run_id, model_version, population, selection_definition, first_production, prediction_finalized, is_experiment, tour, picked_at, scheduled_start_at, event_date, side1_player_id, side2_player_id, selected_player_id, opponent_player_id, manager_side, manager_p, a1_side, a1_p, a2_side, a2_p, outcome_status, winner_side, surface, tournament_tier`. The Manager selected/opponent canonical IDs MUST agree with the original Manager side and side1/side2 identity; agent A1/A2 selections are oriented independently using their own original side.

The frozen cohort operator must prove all rows are genuinely original first-production selections; `selection_definition` is `MANAGER_PRIMARY` for `MANAGER` and `A1_A2_WIDE_SPREAD` for `A1_A2_TRACKING`. The adapter from actual database enums and status fields must be independently reviewed before use. The script **does not query the database, infer a selection population, or reconstruct later predictions**. `event_date` is the *match date*, not tournament start date. A change to historical result status must preserve an immutable outcome/as-of lineage. Deduplication key: `(population, canonical_match_id)`, ordering `(picked_at, pick_id)`.

Example after receiving *approved* exports:

```bash
python -m research.tennis_pit_rank_evidence_20261022.enrichment \
  --frozen-picks authorized_frozen_picks.jsonl \
  --rank-evidence licensed_verified_official_rankings.jsonl \
  --expected-picks-sha256 <independently_frozen_sha256> \
  --expected-ranks-sha256 <independently_frozen_sha256> \
  --output enriched.jsonl --report ranking_coverage_and_cohorts.json
```

No download, network request, provider spend, database write, migration, production rule change or stake publication is performed.

## Oct 23 coverage and decision

| Metric | Audited value | Comment |
|---|---|---|
| Prior study analysis eligible | 866, **handoff-reported** | Frozen data unavailable to independently reproduce |
| Prior official PIT ranking matches | 0, **handoff-reported** | No official rank evidence supplied |
| Newly validated licensed ATP rows | 0 | No source package with release/ID/license evidence supplied |
| Newly validated licensed WTA rows | 0 | No source package with release/ID/license evidence supplied |
| New actual band win/loss results | **Not estimable** | Never manufacture a 401–600 result |
| Baseline-v-filter holdout results | **Not estimable** | Ranking exposure and frozen prediction rows unavailable |
| Original decision-odds ROI | **Not estimable** | No verified original issued quote cohort |

**Exact blockers:** obtain written rights/appropriate provider entitlement; prove original source release times and complete weekly coverage through 601+; produce an audited canonical player crosswalk; hand off the original frozen prediction export, independent SHA-256 and schema mapping; preserve settlement correction lineage; run offline evidence validation and only then train/holdout. Do not silently substitute contemporary or third-party estimated rankings. 
