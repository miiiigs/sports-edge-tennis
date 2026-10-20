# Tennis historical identity, result and input repair audit — 2026-10-20

Status: read-only production audit and dry-run repair manifest. No production writes,
identity merges, result fabrication, provider calls or live model changes were made.

Production database: `Postgres-Sdqz` (`railway`), audited 2026-10-20 03:56–04:20 UTC with
`default_transaction_read_only=on`. Repository base: `main` `b6b7e50`.

Reproduce with:

```text
python -m sports_edge_tennis.live.historical_repair_audit --output repair_report.json
```

The module opens a read-only transaction and emits the pending population, cross-source
result candidates, same-player/time conflicts, bracket evidence and a correction manifest.
The existing pending-result diagnostics remain the source of truth for the 63-item reason
breakdown.

## 1. Affected pending population (63)

| Reason | Count |
| --- | ---: |
| `NOT_STARTED` | 48 |
| `PROVIDER_FIXTURE_NOT_REOBSERVED` | 9 |
| `TERMINAL_FIXTURE_WITHOUT_RESULT` | 4 |
| `AWAITING_RESULT` | 1 |
| `NO_API_TENNIS_LINK` | 1 |

Age: 48 `NOT_STARTED`, 1 `LT_6H`, 14 `GT_72H`. Flags: 14
`OUTSIDE_RECOVERY_WINDOW`, 29 `RESCHEDULED`, 2 `TYPE_A_BET`, and (new) 1
`WALKOVER_TERMINAL`.

The 48 `NOT_STARTED` fixtures are current 2026-10-20/21 events, not historical defects:

```text
132248 132250 132251 132259 132263 132266 132299 132300 132301 132302 132356 132358
132359 132362 132367 132371 132372 132373 132375 132376 132378 132380 132382 132383
132411 132412 132422 132424 132452 132453 132455 132456 132461 132462 132470 132473
132474 132475 132478 132479 132482 132484 132485 132488 132489 132491 132494 132497
```

The 15 actionable records:

| match_id | Reason | Provider status | Match | Tournament | Latest start (UTC) | Last observed (UTC) | Provider IDs | BETs |
| ---: | --- | --- | --- | --- | --- | --- | --- | ---: |
| 117860 | `PROVIDER_FIXTURE_NOT_REOBSERVED` | SCHEDULED | J. Munar v G. Bueno | Seville | 2026-09-09T18:15Z | 2026-09-09T17:03:54Z | API_TENNIS:12161828, PINNAPI:1636119587 | 0 |
| 130363 | `PROVIDER_FIXTURE_NOT_REOBSERVED` | SCHEDULED | M. Zheng v E. Zhu | Tiburon | 2026-09-15T23:10Z | 2026-09-15T23:02:42Z | API_TENNIS:12163336, PINNAPI:1636618733 | 0 |
| 130686 | `PROVIDER_FIXTURE_NOT_REOBSERVED` | SCHEDULED | T. Legout v D. King | San Diego 2 Q | 2026-09-20T23:25Z | 2026-09-20T23:03:59Z | API_TENNIS:12164540, PINNAPI:1637131467, SPORTRADAR:sr:sport_event:74864806 | 0 |
| 130717 | `PROVIDER_FIXTURE_NOT_REOBSERVED` | SCHEDULED | K. Smith v E. Winter | San Diego 2 Q | 2026-09-21T22:40Z | 2026-09-21T22:05:04Z | API_TENNIS:12164591, PINNAPI:1637155667, SPORTRADAR:sr:sport_event:74867008 | 10 |
| 131036 | `TERMINAL_FIXTURE_WITHOUT_RESULT` | COMPLETED | M. Alcala Gurri v C. Taberner | Plovdiv 4 Q | 2026-09-26T11:00Z | 2026-09-26T23:02:58Z | ODDSPAPI:id1205233274753168, SPORTRADAR:sr:sport_event:74753168 | 0 |
| 130903 | `NO_API_TENNIS_LINK` | SCHEDULED | T. Legout v I. Marcondes | San Diego 2 Q | 2026-09-26T21:05Z | 2026-09-26T21:02:33Z | SPORTRADAR:sr:sport_event:74753234 | 0 |
| 131307 | `TERMINAL_FIXTURE_WITHOUT_RESULT` | COMPLETED | R. Te v J. Duckworth | Beijing | 2026-09-28T03:20Z | 2026-09-29T00:15:01Z | API_TENNIS:12166372, PINNAPI:1637438572 | 0 |
| 131446 | `TERMINAL_FIXTURE_WITHOUT_RESULT` | COMPLETED | A. Molcan v A. Rinderknech | Beijing | 2026-10-01T03:05Z | 2026-10-02T00:12:33Z | API_TENNIS:12166954, PINNAPI:1637478651 | 0 |
| 131527 | `PROVIDER_FIXTURE_NOT_REOBSERVED` | SCHEDULED | B. Tomic v M. Bellucci | Jingshan | 2026-10-02T08:25Z | 2026-10-02T06:02:41Z | API_TENNIS:12167805 | 0 |
| 131536 | `PROVIDER_FIXTURE_NOT_REOBSERVED` | SCHEDULED | H. Rocha v J. Kym | Porto | 2026-10-03T11:30Z | 2026-10-03T11:01:52Z | API_TENNIS:12167901 | 0 |
| 131637 | `PROVIDER_FIXTURE_NOT_REOBSERVED` | SCHEDULED | M. Purcell v B. Bayldon | Wuning 3 Q | 2026-10-05T03:30Z | 2026-10-05T03:04:11Z | API_TENNIS:12168174 | 0 |
| 131636 | `PROVIDER_FIXTURE_NOT_REOBSERVED` | SCHEDULED | G. Campana Lee v P. Vives Marcos | Palermo Q | 2026-10-05T09:00Z | 2026-10-05T08:04:02Z | API_TENNIS:12168173 | 0 |
| 131712 | `PROVIDER_FIXTURE_NOT_REOBSERVED` | SCHEDULED | N. Djokovic v D. Medvedev | Beijing | 2026-10-05T11:00Z | 2026-10-05T10:01:20Z | API_TENNIS:12168226 | 0 |
| 132211 | `TERMINAL_FIXTURE_WITHOUT_RESULT` | WALKOVER | J. Kym v D. Stricker | Roanne | 2026-10-16T12:10Z | 2026-10-19T08:13:00Z | API_TENNIS:12171502, ODDSPAPI:id1203543675371396 | 12 |
| 132459 | `AWAITING_RESULT` | SCHEDULED | K. Matsuda v Y. Zhou | Guangzhou | 2026-10-20T03:00Z | 2026-10-20T02:04:17Z | API_TENNIS:12172256, ODDSPAPI:id1200463175794080 | 0 |

Only two records carry financial exposure: 130717 (10 `BET` decisions on side 2,
E. Winter) and 132211 (12 `BET` decisions on side 1, J. Kym). All others are
`NO_BET` or tracking-only.

## 2. Root-cause classification

| Class | Records | Finding |
| --- | --- | --- |
| Cross-source duplicate canonical match | 131036 | Same players and same canonical tournament (822) were ingested twice under different provider events/editions. The API-Tennis copy 131137 has a result (COMPLETED, winner side 1, `2 - 0`, observed 2026-09-27T00:14:38Z). The Sportradar copy 131036 has no result. |
| Same-provider duplicate/misidentified fixture | 131307, 131446 | One player appears in two near-simultaneous same-tournament fixtures with different opponents; the non-target copy has a result. 131354 (R. Te v A. Gea, 03:25Z) beat 131307 (R. Te v J. Duckworth, 03:20Z). 131498 (A. Molcan v T. Machac, 03:20Z) beat 131446 (A. Molcan v A. Rinderknech, 03:05Z). Do not merge or copy by name. |
| Explicit walkover without a persisted result | 132211 | Provider reports `WALKOVER` from 2026-10-17T08:12:18Z through 2026-10-19T08:13:00Z. The next SF 132233 (J. Kym v M. Brunold, 2026-10-17T12:40Z) was played by Kym and lost; Stricker has no later match. Kym advanced. Settlement policy maps `WALKOVER` to `VOID`. |
| Provider fixture dropped before terminal observation | 117860, 130363, 130686, 130717, 131527, 131536, 131636, 131637, 131712 | Last observation is at or just before the scheduled start. Bracket progression proves one player advanced for most, but it does not prove COMPLETED vs WALKOVER/RETIRED/CANCELLED. No result may be inferred. |
| Provider-only identity with no API-Tennis link | 130903 | Sportradar-only event `sr:sport_event:74753234`; earlier versions used `WQF3`/`WQF4` placeholders, then actual players. No API-Tennis event key, so the bounded recovery lane cannot look it up. No later same-tournament match was found for either player. |
| Placeholder/pre-match version risk | 131036, 131038, 131039, 130900, 130901, 130903, 131041, 131042, 131043, 131095, etc. | 37 of 1,620 first-production-prediction matches in the last 30 days used placeholder participants (`WQF*`, `QF*`). 24 matches changed participants after issuance. No side swaps were detected. |

Key evidence:

- 131137: `match_result_observations` outcome `COMPLETED`, winner side 1, `2 - 0`,
  observed/as_of 2026-09-27T00:14:38.198365Z; source `API_TENNIS:12166029`.
- 131354: outcome `COMPLETED`, winner side 2, `0 - 2`, observed/as_of
  2026-09-29T00:15:01.804020Z; source `API_TENNIS:12166487`.
- 131498: outcome `COMPLETED`, winner side 1, `2 - 0`, observed/as_of
  2026-10-01T05:01:12.148139Z; source `API_TENNIS:12167607`.
- 132211: latest `WALKOVER` version observed/as_of 2026-10-19T08:13:00.037065Z;
  132233 result `COMPLETED`, winner side 2, `0 - 2`, observed/as_of
  2026-10-17T15:04:56.782781Z.

The existing refresh path requires a non-null provider winner even for an explicit
walkover (`parse_terminal_result` raises `missing_terminal_winner`). That is the most
likely reason 132211 stayed pending after the walkover was first observed. A new
regression test locks that behaviour: an explicit walkover without a provider winner
stays pending and is not inferred.

## 3. Identity and alias audit

- Source identity rows: `API_TENNIS` 1,812; `ODDSPAPI` 283; `SACKMANN` 4,277;
  `SPORTRADAR` 538.
- 55 exact duplicate canonical-name groups (110 player IDs). These are not safe merge
  candidates by name. Examples: `B. Nakashima` (Brandon vs Bryce), `Jakub Mensik`
  (171 vs 11 matches), `Bruno Kuzuhara` (88 vs 14), `Tristan McCormick` (26 vs 32).
  A pair of same-name rows can be different people; a split identity can be the same
  person. No merge is proposed without external identity evidence (date of birth,
  provider biography, or a reviewed exact match).
- 1,519 duplicate normalized `TENNIS_DATA` alias groups. Most are surname/initial
  collisions (e.g. `alcaraz c`) and are harmless as variants but unsafe as identity
  evidence. 204 groups contain a full-name-like alias; these require review, not
  automatic merging.
- Only 1 persisted `model_identity_bridge_events` row exists.
- 1,856 players active in the last 45 days have no non-live historical source identity.
- Agent 1 history reaches: 132,193 observations; 14,480 involve a live-only player.
  Turning on the existing identity bridge changes 14,252 observations and reduces
  distinct player IDs from 6,102 to 4,889 (1,213 live-only IDs merge into historical
  identities). Production Agent 1 still calls `load_agent1_history` without
  `bridge_model_identities=True`, so the 2026 live history is not reaching the model ID
  used for prediction.

## 4. Surface, format and pre-match input audit

- 3,884 matches started in the last 120 days have no surface on either the latest match
  version or the tournament edition. The all-time gap is 12,243.
- 398 Grand Slam main-draw matches are stored best-of-3. The 2026 API-Tennis Slams
  contribute 390 main-draw rows (Australian Open 128, Wimbledon 129, US Open 133) plus
  2 wheelchair rows; 6 historical Roland Garros rows also remain. Historical Sackmann
  Slam main draws are correctly best-of-5. The API-Tennis adapter has no best-of field
  and defaults to 3.
- 1,620 matches received a first production prediction in the last 30 days:
  - 286 had a null PIT surface (205 had null on both version and edition);
  - 234 had a null PIT round;
  - 37 used placeholder participants;
  - 24 changed participants between the first PIT version and the latest version;
  - 99 used a 00:00 or 02:00 UTC placeholder start;
  - 0 had no PIT version and 0 had an orientation swap;
  - 1 was issued 30 seconds after the scheduled start (130853, 2026-09-23).
- Round labels are inconsistent: `R32`, `SF`, `R16`, `F`, `QF`, `Q1` plus
  `round_of_16` (767), `quarterfinal` (612), `semifinal` (595), `round_of_32` (454),
  `qualification_round_1` (207), `final` (135), `qualification_final` (97), and
  6,593 null rows.

## 5. Dry-run correction manifest

The audit emits one proposal per actionable record:

| Action | Safety | Records |
| --- | --- | --- |
| `APPEND_EXPLICIT_WALKOVER_RESULT` | `REQUIRES_OWNER_APPROVAL` | 132211 |
| `RECONCILE_CROSS_PROVIDER_RESULT` | `REQUIRES_OWNER_APPROVAL` | 131036 |
| `HOLD_DUPLICATE_FIXTURE` | `BLOCKED` | 131307, 131446 |
| `PROVIDER_RESULT_REQUIRED` | `BLOCKED` | 117860, 130363, 130686, 130717, 130903, 131527, 131536, 131636, 131637, 131712 |
| `NO_ACTION_PENDING` | `EVIDENCE_ONLY` | 132459 |

Proposed before/after for the two owner-review records:

### 132211 — explicit walkover

- Before: no `match_result_observations`; 12 `BET` decisions on side 1; latest fixture
  status `WALKOVER`.
- After: append `WALKOVER`, `winner_side=1`, no scoreline. Settlement classifies it as
  `VOID`, never `WON` or `LOST`. Writes: `match_result_observations`.

### 131036 — cross-source duplicate result

- Before: no result on 131036; 0 `BET` decisions.
- After: append a `cross_provider_match_reconciliations` row linking target 131036 to
  evidence 131137 (`EXACT_CROSS_PROVIDER_MATCH`, DIRECT player order), using evidence
  result observation for the 2026-09-27T00:14:38Z result. Do not copy the result onto
  131036 and do not delete either match. Writes:
  `cross_provider_match_reconciliations`.

The manifest never proposes a `COMPLETED` result from bracket advancement alone. For
130717, bracket evidence proves K. Smith advanced (later match 130805), but it does
not prove the target match completed normally; settling the 10 side-2 bets as `LOST`
would be unsafe. It remains blocked pending a provider result or an owner-approved
rule.

## 6. Code changes in this branch

- Added `live/historical_repair_audit.py`: read-only reproduction of the pending
  population, cross-source result candidates, same-player/time conflicts, bracket
  evidence and dry-run manifest.
- Added `WALKOVER_TERMINAL` to pending-result diagnostics. The reason remains
  `TERMINAL_FIXTURE_WITHOUT_RESULT`; the flag makes an explicit provider walkover
  visible without changing the 63-item reason counts.
- Added focused tests:
  - `tests/test_historical_repair_audit.py` (6 tests) covering walkover->VOID,
    bracket-only inference refusal, cross-source reconciliation without copying,
    duplicate-fixture hold, and the two DB detection queries.
  - `tests/test_pending_result_diagnostics.py` walkover flag.
  - `tests/test_pending_result_refresh.py` walkover-without-winner stays pending.
- Updated `docs/PENDING_RESULT_DIAGNOSTICS.md` for the new flag.

Validation run with a disposable PostgreSQL 16 database:

```text
tests/test_historical_repair_audit.py                 6 passed
tests/test_pending_result_diagnostics.py             17 passed
tests/test_pending_result_refresh.py                 42 passed
tests/test_live_model_player_history_bridge.py       (included in 66 passed)
tests/test_settlement_postgres.py                    (included in 66 passed)
tests/test_api_tennis_round_compat.py                (included in 66 passed)
```

## 7. Status

| Area | Status |
| --- | --- |
| Reproducible population and manifest | Fixed in code (read-only). |
| Pending walkover visibility | Fixed in code (flag), reason counts unchanged. |
| Walkover result for 132211 | Not applied; owner approval required. |
| Cross-provider reconciliation for 131036 | Not applied; owner approval required. |
| Duplicate fixtures 131307, 131446 | Not applied; review required. |
| 130717 and the other dropped fixtures | Blocked: no stored terminal result; provider re-fetch or owner rule required. |
| Identity merges | Not attempted; names are not sufficient evidence. |
| Surface / best-of / pre-match placeholders | Audited, not repaired; ingestion/adapter changes need a separate scoped PR. |
| Agent 1 live-history bridge | Audited; production still disabled pending owner activation and forward shadow. |

## 8. Follow-up estimate

- Apply the approved walkover and cross-provider corrections, then re-run settlement:
  ~0.5 day after owner sign-off.
- Authorized bounded provider re-fetch for the 10 `PROVIDER_RESULT_REQUIRED` records
  and the remaining frozen-result population: ~1–2 days including verification.
- Reviewed identity collision cohort (55 exact-name groups + high-risk full-name alias
  groups): ~2–4 days; no merges by name, evidence per pair.
- Surface recovery, best-of enrichment and placeholder-version handling: ~3–5 days
  with adapter tests and a dry-run backfill.
- End-to-end regression and production verification: ~1 day.

Total realistic follow-up after this audit: 1–2 weeks, with the walkover/reconciliation
items deliverable first once the owner approves the dry-run manifest.

## 9. Appendix � exact pre-match version cohort


### 9.1 First production prediction used placeholder participants, then participants changed (24)

| match_id | First prediction as_of | First PIT participants | Latest participants | PIT start |
| ---: | --- | --- | --- | --- |
| 131039 | 2026-09-25 18:03:41.862422+00:00 | WQF1 v WQF2 | J. Aguilar Cardozo v A. Barrena | 2026-09-26 13:00:00+00:00 |
| 131038 | 2026-09-25 16:02:34.098155+00:00 | WQF3 v J. Forejtek | A. Andreev v J. Forejtek | 2026-09-26 12:10:00+00:00 |
| 131036 | 2026-09-25 12:03:37.317217+00:00 | M. Alcala Gurri v WQF2 | M. Alcala Gurri v C. Taberner | 2026-09-26 11:00:00+00:00 |
| 131028 | 2026-09-24 15:03:00.359815+00:00 | H. Mayot v Qf6 | H. Mayot v J. De Jong | 2026-09-25 10:10:00+00:00 |
| 131027 | 2026-09-24 15:03:00.359815+00:00 | Qf7 v Qf8 | J. Clarke v C. A. Caniato | 2026-09-25 08:00:00+00:00 |
| 131021 | 2026-09-24 14:03:25.921057+00:00 | M. Petkovic v Qf6 | M. Petkovic v F. Forti | 2026-09-25 08:00:00+00:00 |
| 130993 | 2026-09-24 11:02:23.065085+00:00 | M. Cassone v Qf8 | M. Cassone v D. Masur | 2026-09-25 08:30:00+00:00 |
| 130901 | 2026-09-24 00:04:50.019601+00:00 | WQF1 v WQF2 | T. Droguet v D. Prizmic | 2026-09-26 12:00:00+00:00 |
| 130900 | 2026-09-24 00:04:50.019601+00:00 | WQF3 v WQF4 | H. Mayot v M. Cassone | 2026-09-26 12:00:00+00:00 |
| 130903 | 2026-09-24 00:04:50.019601+00:00 | WQF3 v WQF4 | T. Legout v I. Marcondes | 2026-09-26 21:00:00+00:00 |
| 130898 | 2026-09-24 00:04:50.019601+00:00 | WQF3 v WQF4 | F. Forti v C. A. Caniato | 2026-09-26 08:00:00+00:00 |
| 130899 | 2026-09-24 00:04:50.019601+00:00 | WQF1 v WQF2 | M. Moeller v L. Mikrut | 2026-09-26 08:00:00+00:00 |
| 130902 | 2026-09-24 00:04:50.019601+00:00 | WQF1 v WQF2 | WQF1 v D. Dietrich | 2026-09-26 21:00:00+00:00 |
| 130897 | 2026-09-23 14:05:36.179499+00:00 | Qf7 v Qf8 | I. Marcondes v F. A. Gomez | 2026-09-25 20:00:00+00:00 |
| 130896 | 2026-09-23 14:05:36.179499+00:00 | Qf5 v Qf6 | T. Boyer v T. Legout | 2026-09-25 20:00:00+00:00 |
| 130891 | 2026-09-23 14:05:36.179499+00:00 | Qf1 v Qf2 | J. Aguilar Cardozo v J. P. Varillas | 2026-09-25 13:00:00+00:00 |
| 130889 | 2026-09-23 14:05:36.179499+00:00 | Qf1 v M. Lajal | T. Droguet v M. Lajal | 2026-09-25 08:30:00+00:00 |
| 130888 | 2026-09-23 14:05:36.179499+00:00 | Qf3 v L. Mikrut | L. Neumayer v L. Mikrut | 2026-09-25 08:00:00+00:00 |
| 130887 | 2026-09-23 14:05:36.179499+00:00 | M. Moeller v Qf2 | M. Moeller v D. Blanch | 2026-09-25 08:00:00+00:00 |
| 130886 | 2026-09-23 14:05:36.179499+00:00 | C. Hemery v Qf4 | C. Hemery v C. Taberner | 2026-09-25 07:00:00+00:00 |
| 130885 | 2026-09-23 14:05:36.179499+00:00 | Qf7 v Qf8 | F. Gill v J. Forejtek | 2026-09-25 07:00:00+00:00 |
| 130884 | 2026-09-23 14:05:36.179499+00:00 | Qf5 v Qf6 | A. Andreev v I. Almazan Valiente | 2026-09-25 07:00:00+00:00 |
| 130883 | 2026-09-23 14:05:36.179499+00:00 | V. Vacherot v R16P2 | V. Vacherot v L. Harris | 2026-09-25 05:00:00+00:00 |
| 130882 | 2026-09-23 14:05:36.179499+00:00 | R16P11 v B. Van De Zandschulp | N. Basilashvili v B. Van De Zandschulp | 2026-09-25 05:00:00+00:00 |

### 9.2 First production prediction used placeholder participants (37)

Sixteen matches still carry placeholder participants in the latest version:
`131029 131030 131031 131032 131037 131040 131041 131042 131043 130890 130892
130893 130894 130895 130902 131095`. These are the highest-risk pre-match inputs
because the issued prediction cannot be mapped to a real player without later
evidence.

| match_id | First prediction as_of | First PIT participants | Latest participants | PIT start |
| ---: | --- | --- | --- | --- |
| 131095 | 2026-09-26 07:05:18.651686+00:00 | Qf1 v Qf2 | Qf1 v Qf2 | 2026-09-27 08:30:00+00:00 |
| 131041 | 2026-09-26 00:05:05.148733+00:00 | WQF3 v WQF4 | WQF3 v WQF4 | 2026-09-28 02:00:00+00:00 |
| 131043 | 2026-09-26 00:05:05.148733+00:00 | WQF3 v WQF4 | WQF3 v WQF4 | 2026-09-28 09:00:00+00:00 |
| 131042 | 2026-09-26 00:05:05.148733+00:00 | WQF1 v WQF2 | WQF1 v WQF2 | 2026-09-28 09:00:00+00:00 |
| 131040 | 2026-09-26 00:05:05.148733+00:00 | WQF1 v WQF2 | WQF1 v WQF2 | 2026-09-28 02:00:00+00:00 |
| 131039 | 2026-09-25 18:03:41.862422+00:00 | WQF1 v WQF2 | J. Aguilar Cardozo v A. Barrena | 2026-09-26 13:00:00+00:00 |
| 131038 | 2026-09-25 16:02:34.098155+00:00 | WQF3 v J. Forejtek | A. Andreev v J. Forejtek | 2026-09-26 12:10:00+00:00 |
| 131036 | 2026-09-25 12:03:37.317217+00:00 | M. Alcala Gurri v WQF2 | M. Alcala Gurri v C. Taberner | 2026-09-26 11:00:00+00:00 |
| 131037 | 2026-09-25 12:03:37.317217+00:00 | WQF3 v WQF4 | WQF3 v WQF4 | 2026-09-26 13:00:00+00:00 |
| 131032 | 2026-09-25 00:03:24.743727+00:00 | Qf7 v Qf8 | Qf7 v Qf8 | 2026-09-27 02:00:00+00:00 |
| 131031 | 2026-09-25 00:03:24.743727+00:00 | Qf5 v Qf6 | Qf5 v Qf6 | 2026-09-27 02:00:00+00:00 |
| 131030 | 2026-09-25 00:03:24.743727+00:00 | Qf3 v Qf4 | Qf3 v Qf4 | 2026-09-27 02:00:00+00:00 |
| 131029 | 2026-09-25 00:03:24.743727+00:00 | Qf1 v Qf2 | Qf1 v Qf2 | 2026-09-27 02:00:00+00:00 |
| 131028 | 2026-09-24 15:03:00.359815+00:00 | H. Mayot v Qf6 | H. Mayot v J. De Jong | 2026-09-25 10:10:00+00:00 |
| 131027 | 2026-09-24 15:03:00.359815+00:00 | Qf7 v Qf8 | J. Clarke v C. A. Caniato | 2026-09-25 08:00:00+00:00 |
| 131021 | 2026-09-24 14:03:25.921057+00:00 | M. Petkovic v Qf6 | M. Petkovic v F. Forti | 2026-09-25 08:00:00+00:00 |
| 130993 | 2026-09-24 11:02:23.065085+00:00 | M. Cassone v Qf8 | M. Cassone v D. Masur | 2026-09-25 08:30:00+00:00 |
| 130901 | 2026-09-24 00:04:50.019601+00:00 | WQF1 v WQF2 | T. Droguet v D. Prizmic | 2026-09-26 12:00:00+00:00 |
| 130900 | 2026-09-24 00:04:50.019601+00:00 | WQF3 v WQF4 | H. Mayot v M. Cassone | 2026-09-26 12:00:00+00:00 |
| 130903 | 2026-09-24 00:04:50.019601+00:00 | WQF3 v WQF4 | T. Legout v I. Marcondes | 2026-09-26 21:00:00+00:00 |
| 130898 | 2026-09-24 00:04:50.019601+00:00 | WQF3 v WQF4 | F. Forti v C. A. Caniato | 2026-09-26 08:00:00+00:00 |
| 130899 | 2026-09-24 00:04:50.019601+00:00 | WQF1 v WQF2 | M. Moeller v L. Mikrut | 2026-09-26 08:00:00+00:00 |
| 130902 | 2026-09-24 00:04:50.019601+00:00 | WQF1 v WQF2 | WQF1 v D. Dietrich | 2026-09-26 21:00:00+00:00 |
| 130897 | 2026-09-23 14:05:36.179499+00:00 | Qf7 v Qf8 | I. Marcondes v F. A. Gomez | 2026-09-25 20:00:00+00:00 |
| 130896 | 2026-09-23 14:05:36.179499+00:00 | Qf5 v Qf6 | T. Boyer v T. Legout | 2026-09-25 20:00:00+00:00 |
| 130895 | 2026-09-23 14:05:36.179499+00:00 | Qf3 v Qf4 | Qf3 v Qf4 | 2026-09-25 20:00:00+00:00 |
| 130894 | 2026-09-23 14:05:36.179499+00:00 | Qf1 v Qf2 | Qf1 v Qf2 | 2026-09-25 20:00:00+00:00 |
| 130893 | 2026-09-23 14:05:36.179499+00:00 | Qf5 v Qf6 | Qf5 v Qf6 | 2026-09-25 13:00:00+00:00 |
| 130892 | 2026-09-23 14:05:36.179499+00:00 | Qf3 v Qf4 | Qf3 v Qf4 | 2026-09-25 13:00:00+00:00 |
| 130891 | 2026-09-23 14:05:36.179499+00:00 | Qf1 v Qf2 | J. Aguilar Cardozo v J. P. Varillas | 2026-09-25 13:00:00+00:00 |
| 130890 | 2026-09-23 14:05:36.179499+00:00 | Qf7 v Qf8 | Qf7 v Qf8 | 2026-09-25 13:00:00+00:00 |
| 130889 | 2026-09-23 14:05:36.179499+00:00 | Qf1 v M. Lajal | T. Droguet v M. Lajal | 2026-09-25 08:30:00+00:00 |
| 130888 | 2026-09-23 14:05:36.179499+00:00 | Qf3 v L. Mikrut | L. Neumayer v L. Mikrut | 2026-09-25 08:00:00+00:00 |
| 130887 | 2026-09-23 14:05:36.179499+00:00 | M. Moeller v Qf2 | M. Moeller v D. Blanch | 2026-09-25 08:00:00+00:00 |
| 130886 | 2026-09-23 14:05:36.179499+00:00 | C. Hemery v Qf4 | C. Hemery v C. Taberner | 2026-09-25 07:00:00+00:00 |
| 130885 | 2026-09-23 14:05:36.179499+00:00 | Qf7 v Qf8 | F. Gill v J. Forejtek | 2026-09-25 07:00:00+00:00 |
| 130884 | 2026-09-23 14:05:36.179499+00:00 | Qf5 v Qf6 | A. Andreev v I. Almazan Valiente | 2026-09-25 07:00:00+00:00 |
