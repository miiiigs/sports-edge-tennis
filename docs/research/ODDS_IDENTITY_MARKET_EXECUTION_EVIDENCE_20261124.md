# OddsPapi identity and market recovery — executed evidence (2026-11-24)

Research-only, read-only. No merge, deployment, provider call, database write,
migration or policy change. Every query ran inside an explicit
`REPEATABLE READ READ ONLY` transaction with `default_transaction_read_only=on`;
`SHOW transaction_read_only` returned `on`.

## Exact checkout

- Worktree: `C:\dev\sports-edge-tennis-pr387-20261124`
- Branch: `research/odds-identity-market-funnel-20261124`
- HEAD: `4f267cd3f68f7f437f08415d2784ed78fdcaf4a6` (matches last verified remote head)
- PR: #387; CI at this head: `postgres-foundation` pass, GitGuardian pass
- Focused tests: `pytest -q tests/test_odds_identity_market_export.py` → **3 passed**

## Reproduced future cohort (2026-11-23T12:06:32.228075Z)

```
python -m sports_edge_tennis.validation.odds_identity_market_export \
  --mode future --as-of-utc 2026-11-23T12:06:32.228075Z
```

| Metric | Value |
|---|---|
| observed candidates | 103 |
| eligible cohort | **71** |
| cohort_sha256 | `e2c3fffc21a656c4a395d03394c19fb08cfd5ada0f42c4636035404763bab79d` |
| sample_sha256 | `c17b57bd99883783e245bfead03739bf0a217528b429951e2855ca02d87fe9f2` |
| MISSING_IDENTITY | 63 |
| UNVERIFIED_TOURNAMENT | 8 |
| VERIFIED_IDENTITY | **0** |
| cache warm at cutoff | **0** |
| cache updated after cutoff (history unavailable) | 8 |
| fixtures with persistent mapping | 13 |
| fixtures with raw observation | 8 |
| independent tournament mapping | **0** |
| raw observations | 187 |
| raw book observations | 187 |
| qualified any book | 0 |
| provider calls | 0 |

The 103 → 71 eligible shape reproduces PR #383 exactly. The previously archived
"2 warm, 69 missing" is **not** reproducible from current state: warm cache is
mutable and the current rows show `last_verified_at >` the historical cutoff for
the 8 fixtures that have one. Current-state evidence is therefore 63 missing,
8 unverified-tournament, 0 verified. This is a retention limitation, not a new
finding about the original run.

## The two named candidates (original mode, 2026-11-24T08:25Z)

```
python -m ... --mode original --as-of-utc 2026-11-24T08:25:00Z --match-ids 134190 134191
```

Both return `MISSING_IDENTITY`; zero raw observations, zero quotes, zero warm
cache at their original first-issue cutoff.

Direct read-only validation (independent of the adapter):

| | 134190 | 134191 |
|---|---|---|
| tour / surface | CHALLENGER / HARD | CHALLENGER / HARD |
| match start (UTC) | 2026-11-24T16:30Z | 2026-11-23T16:40Z |
| tournament edition | 2577 (tournament 899) | 2577 (tournament 899) |
| edition end_date | **NULL** | **NULL** |
| independent OddsPapi tournament mapping | none (API_TENNIS only) | none (API_TENNIS only) |
| OddsPapi source id created | 2026-11-21T23:03:18Z | 2026-11-21T23:03:18Z |
| original first-issue cutoff | 2026-11-21T23:02:31Z | 2026-11-21T23:02:31Z |
| mapping lag vs issue | **+47 s (late)** | **+47 s (late)** |
| cache rows | 0 | 0 |
| raw observations | **0** | **34** |
| raw window | — | 2026-11-22T20:05Z → 2026-11-23T15:01Z |
| participant mapping | n/a | OddsPapi 217192/681005 → canonical 4709/6238, **DIRECT** |
| raw startTime values | n/a | 15:30, 15:35, 15:40, 16:20, 16:30 UTC |

Findings:

1. Both mappings were created ~47 seconds **after** the original first-issue
   cutoff, so neither was PIT-available at prediction time. Hindsight mapping
   recovery is possible now but not usable for the original cohort.
2. 134190 has no stored raw OddsPapi observation at all.
3. 134191 has 34 raw observations, all after the original cutoff; latest
   `startTime` 16:30Z is within the 30-minute start-drift rule of the 16:40Z
   canonical start, and players/orientation verify exactly.
4. Independent tournament verification is impossible with current data:
   `tournament_source_ids` has only API_TENNIS for tournament 899, and the
   edition `end_date` is NULL, so the adapter correctly refuses to assert
   tournament identity. This is the sole blocker for the 8 raw-visible fixtures.

## Recoverability measurement

Cohort classification using the adapter's own `extract_one` / `audit`:

- 63 fixtures: no raw observation and no persistent mapping → genuinely missing.
- 8 fixtures: persistent OddsPapi mapping + stored raw observations + normalized
  snapshots, but no independent tournament mapping → `UNVERIFIED_TOURNAMENT`.
- 5 further fixtures have a persistent mapping but no stored raw observation
  (cannot recover without a provider call).
- 0 identity conflicts, 0 stale/future identity, 0 start conflicts,
  0 invalid side mappings were reported at the identity stage.

### Raw market state for the 8 recoverable fixtures

| Metric | Value |
|---|---|
| raw book rows | 242 |
| at/before cutoff | 194 |
| after cutoff (late) | 48 |
| `suspended=true` | **0** |
| `marketActive=false` | **0** |
| both outcomes active | 242 / 242 |
| fixtures with any post-cutoff raw | 7 / 8 |
| by book | pinnacle+30 111, bet365 105, 1xbet 26 |

### Raw two-sided price upper bound (not verified identity)

Using the latest at/before-cutoff raw snapshot per book and requiring both
outcomes active with finite price > 1:

- 19 raw qualified two-sided pairs across **8 / 8** fixtures
- by book: pinnacle+30 8, bet365 8, 1xbet 3
- normalized `odds_snapshots`: 484 rows / 8 matches, 388 at/before cutoff

So the market data exists; the blocker is identity provenance, not market
availability.

## Separately reported categories (future cohort)

- missing identities: 63
- unverified tournament identity: 8
- conflicting identities: 0
- missing markets: not assessable at the verified layer (0 verified identities);
  raw layer shows 0 inactive markets among the 8 raw-visible fixtures
- suspensions: 0 raw `suspended=true`; 0 normalized suspensions attributed
- late prices: 48 raw book rows after cutoff on 7 of 8 raw-visible fixtures
- qualified two-sided prices: **0** verified; raw upper bound 19 pairs / 8 fixtures

## Broader historical evaluation (original first-issued ledger, 2026-11-23T12:06:32Z)

The prescribed single-process original-mode run was interrupted by the Railway
public proxy dropping the long-lived connection. The same PR helpers
(`source_sql` → `extract_one` → `audit`) were therefore driven in resumable
batches of 10 fixtures, each in its own explicit `REPEATABLE READ READ ONLY`
transaction with the identical per-fixture cutoff. All data is historical, so
batch boundaries do not change a fixture's result; 0 errors occurred.

| Metric | Value |
|---|---|
| historical first-issue cohort | 1384 |
| fixtures extracted (passed eligibility) | 1112 |
| MISSING_IDENTITY | 1111 |
| UNVERIFIED_TOURNAMENT | 1 |
| VERIFIED_IDENTITY | **0** |
| persistent mapping present | 1 |
| independent tournament mapping | **0** |
| warm cache at cutoff | 0 |
| cache updated after cutoff (history unavailable) | 68 |
| fixtures with raw observation | 1 (15 raw observations, 15 raw book rows) |
| qualified any book | 0 |
| cohort_sha256 | `3889c16bbcc7109dbdaef48490f3f76dd75c0019708095291f17f12bb8ece6b3` |

Exclusions counted by the adapter: 263 ineligible/late, 8 historical player-order
conflicts, 1 missing point-in-time fixture. Duplicate-owner collision resolution
was not applied because only one fixture carried any identity evidence; it does
not affect these counts.

Interpretation: across the entire historical first-issued ledger as of Nov 23,
stored OddsPapi evidence is essentially absent at issue time — 1,111 of 1,112
eligible fixtures have no usable identity and the single fixture with raw rows
still fails independent tournament verification. Historical market recovery is
therefore **0 verified fixtures**, not a coverage figure that a provider
entitlement change could fix.

## Database-wide corroboration (aggregate)

- OddsPapi raw observations: 10,153 rows, 472 distinct events
- every raw row has `payload_json->>'fixtureId' = provider_event_id`
  (10,153 / 10,153) — raw identity is internally consistent
- every raw row carries a `bookmakerOdds` object (10,153 / 10,153)
- normalized OddsPapi MATCH_WINNER LIVE_PROVIDER snapshots: 20,166 rows / 469 matches
- current `oddspapi_event_identity_cache`: 128 rows, all `EXACT`

## Justified engineering changes (zero provider cost)

1. Persist an independent tournament ↔ OddsPapi mapping with provenance, or
   record the verified tournament id inside the identity cache with its source.
   Without it, 8 otherwise-complete fixtures can never verify.
2. Populate `tournament_editions.end_date` (currently NULL for edition 2577);
   the adapter's edition-window gate depends on it.
3. Retain the PIT-valid warm-cache evidence (last_verified_at/resolved_at
   history) instead of mutating the current row, so a historical run can be
   reproduced. Current state cannot reconstruct the archived "2 warm" fixtures.
4. Reduce the mapping lag relative to first issue (observed +47 s): create or
   refresh the OddsPapi mapping before the first prediction is issued.
5. No discovery/provider experiment is justified yet; the recoverable yield is
   identity-provenance bound, not entitlement bound.

## Not claimed

No provider calls, no entitlement conclusions, no historical cache
reconstruction, no live prediction change, no promotion. Raw upper bounds are
labelled as such and are not verified identities.
