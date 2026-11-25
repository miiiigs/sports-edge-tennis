# OddsPapi eight-fixture deep dive and identity-history corrective change (2026-11-25)

Research-only, read-only. No provider calls, no production writes, no applied
migration, no merge/deploy. All reads used `REPEATABLE READ READ ONLY`.

## 1. The eight raw-visible fixtures

They are not one tournament. They span three editions, all CHALLENGER main
draws with `qualifying_flag=false`:

| Canonical tournament | Edition | Edition name | Edition end_date | Fixtures | OddsPapi tournament id in payload |
|---|---|---|---|---|---|
| 896 | 2574 | Hersonissos 4 2026 | **NULL** | 1 | 47624 |
| 899 | 2577 | Monastir 2 (Tunisia) - Qualification 2026 | **NULL** | 5 | 52100 |
| 901 | 2579 | San Salvador (El Salvador) - Qualification 2026 | **NULL** | 2 | 53878 |

Tournament mapping provenance:

- `tournament_source_ids` contains **zero ODDSPAPI rows in the entire database**
  (SACKMANN 2289, API_TENNIS 310, SPORTRADAR 66). Each of the three canonical
  tournaments maps only to API_TENNIS (`12900`, `14882`, `14884`).
- `provider_fixture_metadata` has only API_TENNIS rows (9,446); their
  `raw_tournament_name` matches the canonical edition names for every fixture.
  That verifies the canonical edition, but it is not an OddsPapi mapping.
- `oddspapi_event_identity_cache` does store `oddspapi_tournament_id`, but the
  adapter deliberately refuses cache-derived tournament proof as independent.
- Every edition has `end_date = NULL`, so the adapter's edition-window gate
  can never be satisfied even if a mapping row were added.

Conclusion: the fixtures are correctly identified as canonical fixtures, but
independent OddsPapi tournament provenance does not exist and cannot be derived
from the current schema without fabricating it.

## 2. Start-time drift is the dominant blocker

For the same eight fixtures at the original cutoff
(`2026-11-23T12:06:32.228075Z`), the provider event start in the latest raw
observation is systematically **40–60 minutes earlier** than the canonical
point-in-time `match_start_at`:

| Match | Canonical start at cutoff | Provider event start | Drift |
|---|---|---|---|
| 134101 | 13:55 | 13:05 | −50 min |
| 134188 | 16:30 | 15:30 | −60 min |
| 134191 | 16:30 | 15:30 | −60 min |
| 134193 | 15:00 | 14:20 | −40 min |
| 134194 | 15:00 | 14:20 | −40 min |
| 134195 | 15:00 | 14:20 | −40 min |
| 134244 | 20:00 | 19:20 | −40 min |
| 134250 | 19:00 | 18:20 | −40 min |

0 of 8 are inside the 30-minute `MAX_DRIFT`. This is a data-convention
mismatch (provider schedule vs canonical match start), not a mapping issue.
`canonical_latest_start` also differs from the PIT start for several fixtures
(reschedules), so the drift is not a single fixed offset.

## 3. Do the 19 potential pairs genuinely qualify?

Strict original-cutoff audit using the PR's own `identity()` and `market()`:

| Layer | What is supplied | Result |
|---|---|---|
| Baseline | stored evidence only | 0 verified; 8 `UNVERIFIED_TOURNAMENT` |
| A | + independent OddsPapi tournament id | **0 / 8**; all `IDENTITY_START_CONFLICT` |
| B | + also align event start to canonical PIT start (counterfactual) | 8 / 8 verify; **18 of 24 book-fixtures QUALIFIED** |

Layer B book states: pinnacle+30 8/8 QUALIFIED; bet365 7/8 QUALIFIED +
1 `STALE_QUOTATION`; 1xbet 3/8 QUALIFIED + 5 `NO_STORED_MARKET`. Total
counterfactual qualified pairs = **18**, not 19 (the earlier 19-pair raw bound
included the stale bet365 pair).

**Actual answer to the question: 0 pairs genuinely qualify under original
cutoff requirements.** Recovered verified identities: **0**. The 18-pair figure
is a labelled counterfactual that removes two real defects (missing mapping and
start drift); it is not a recovered result and must not be reported as coverage.

## 4. Why the cache cannot reproduce the archived "2 warm / 69 missing"

`oddspapi_event_identity_cache` has no `created_at` and no history table.
`upsert_cached_identity()` is an `ON CONFLICT DO UPDATE` that overwrites
`last_verified_at` and `resolved_at`; `partition_cached_identities()` calls
`invalidate_cached_identity()`, which **DELETEs** any identity failing a rule.
Repository search shows no other writer of historical cache state.

Therefore the current row is not the historical state: 68 historical-cohort
fixtures carry cache rows updated after their cutoff, and 0 are warm at cutoff.
The two archived warm identities were overwritten or deleted. This is data
destruction, not a measurement discrepancy.

## 5. The 47-second late identity

In `live/batch.py` the cycle issues predictions and Manager consensus first;
only afterwards does it enter the TARGET odds branch, where
`partition_cached_identities()` + `_resolve_cold_oddspapi_identities()` perform
OddsPapi discovery and `upsert_cached_identity()`. Measured for the two named
candidates:

- first-issue prediction cutoff `2026-11-21T23:02:31Z`
- OddsPapi `match_source_ids` row created `2026-11-21T23:03:18Z` (**+47 s**)
- 134190 has no raw observation; 134191's first raw observation is
  `2026-11-22T20:05Z` (≈21 h later)

So the identity is structurally created after the first issue within the same
cycle. A fixture is only warm for a *later* cycle. The fix is ordering (resolve
identity before first-issue persistence) or an explicit `identity_available_at`
that research can use instead of pretending first issue had market identity.

## 6. Implemented corrective change (narrow, not applied)

Evidence supports exactly one safe change now: stop destroying identity history.

- `db/migrations/0023_oddspapi_identity_history.up.sql` (+ down): append-only
  `oddspapi_event_identity_history` with the full identity row, `event_type`
  (`UPSERTED`/`INVALIDATED`) and `recorded_at`; triggers block UPDATE/DELETE;
  index on `(canonical_match_id, recorded_at DESC)`.
- `src/sports_edge_tennis/live/oddspapi_identity_cache.py`:
  `_record_identity_history()` writes before upsert/invalidate, guarded by
  `to_regclass` so cache behaviour is unchanged until the migration is applied
  (fail-open). Invalidation now records the row it deletes.
- Tests: history on upsert, history before invalidation, fail-open when absent,
  migration append-only assertions. `tests/test_oddspapi_identity_cache.py`
  extended; all pass.

Not changed, because the evidence does not justify it:

- `MAX_DRIFT` was not loosened. A 60-minute allowance would manufacture
  identity matches from a real schedule mismatch; that requires a separate,
  approved scheduling-semantics study.
- No `tournament_source_ids` row was written, fabricated or backfilled.
- No provider discovery or paid request was made.
- No cache row was mutated.

## 7. Remaining blockers

1. Independent OddsPapi tournament mapping requires a reviewed producer, and
   `tournament_editions.end_date` must be populated for the edition gate.
2. Start-time semantics must be resolved (provider session time vs canonical
   match start) before any drift threshold change.
3. Identity must be resolved before first issue, or the system must record
   `identity_available_at` explicitly.
4. History becomes reproducible only for future cycles after migration 0023 is
   applied; past state cannot be reconstructed.
