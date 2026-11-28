# January 2027 scope, schedules and operational acceptance — 27 November 2026

**Branch / PR:** `workstream/ao-2027-format-readiness-20261125` (PR #389)
**Status:** scope verified where a source exists; publication, entry lists,
draws, 2027 edition rulebook and provider entitlement remain blocked.
**Mode:** public-source verification only. No provider call, no scraping, no
purchase, no database write, no deployment.
**Specification:** [`specs/january_2027_scope_20261127.json`](../../specs/january_2027_scope_20261127.json)
**Evidence:** [`january_2027_scope_20261127.json`](evidence/january_2027_scope_20261127.json)
**Validator:** `src/sports_edge_tennis/validation/january_scope.py`
**Tests:** `tests/test_january_scope.py` (10 tests)

## 1. Authoritative January 2027 schedule

| Competition | Scope | Dates (tournament local) | Surface | Format | Source | Verification |
|---|---|---|---|---|---|---|
| United Cup | Team | 1–10 Jan | Hard | Team | ATP 2027 calendar | **EXCLUDED** |
| Brisbane International | ATP 250 | week of 4 Jan | Hard | BO3, 32 draw | ATP 2027 calendar | date week verified |
| Bank of China Hong Kong Tennis Open | ATP 250 | week of 4 Jan | Hard | BO3, 28 draw | ATP 2027 calendar | date week verified |
| Adelaide International | ATP 250 | 11–16 Jan | Hard | BO3, 28 draw | ATP 2027 calendar | verified |
| ASB Classic | ATP 250 | 11–16 Jan | Hard | BO3, 28 draw | ATP 2027 calendar | verified |
| Australian Open 2027 | Grand Slam | 17–31 Jan | Hard | BO5, 128 draw | ausopen.com + ATP calendar | verified |
| AO 2027 Qualifying | Grand Slam qualifying | window 10–16 Jan; ATP lists week of 12 Jan | Hard | BO3, 128 draw | ausopen.com + ATP Challenger calendar | window verified; exact first match **unknown** |
| Canberra Challenger 125 | Challenger | week of 5 Jan | Hard | BO3 | ATP Challenger calendar | date week verified |

Source URLs:

- `https://ausopen.com/whats-on` — AO 2027 runs 10–31 January; Opening Week
  Sunday 10 – Saturday 16 January includes qualifying; Main Draw Week One
  17–24 January; Week Two 25–31 January.
- `https://www.atptour.com/-/media/files/calendar-pdfs/2026/2027-atp-tour-calendar-14-january-2026-updated.pdf`
  — January rows: United Cup 1 Jan, Brisbane and Hong Kong week of 4 Jan,
  Adelaide and Auckland week of 11 Jan, Australian Open 17 Jan.
- `https://www.atptour.com/-/media/files/calendar-pdfs/2026/2026-27-atp-challenger-calendar-as-of-15-november-2026.pdf`
  — Canberra 125 week of 5 Jan; Australian Open Qualifying 128 draw in the week
  commencing 12 Jan.

**Access caveat.** The ATP PDF URL returned HTTP 403 to the fetch tool. The
January rows above were read from the indexed PDF excerpt and cross-checked
against two independent ATP calendar transcriptions that cite the same official
PDF. The repository does **not** claim a byte-level archive of the PDF. Before
the readiness gate is signed, the owner should archive the official PDF and
confirm the January rows. This is a documentation task, not a reason to invent a
date.

## 2. Format and rule verification

| Rule | Status | Evidence |
|---|---|---|
| AO 2027 main draw is best-of-five | **PASS** | general AO/Grand Slam scoring guidance; ATP Grand Slam 128 draw |
| AO 2027 qualifying is best-of-three | **PASS** | general Grand Slam qualifying format |
| AO deciding set is a 10-point tiebreak at 6–6 | **PASS** | AO scoring guidance; `BO5_DECIDER_TB10_AT_6_6` |
| ATP 250 and Challenger singles are best-of-three | **PASS** | ATP format |
| Edition-specific 2027 AO rulebook | **BLOCKED** | no 2027 rulebook located; 2026 is the latest found |
| Carry-forward of 2026 format | **OWNER DECISION REQUIRED** | flagged in the specification, not asserted |

The specification records the carry-forward as an explicit assumption:
`edition_rulebook_2027_status = NOT_LOCATED` and
`carry_forward_assumption = "General AO/Grand Slam format; owner confirmation
required if no 2027 edition rulebook is published."`

## 3. Explicitly unsupported competitions

The following are outside men's ATP/Challenger singles scope and must never
resolve the AO BO5 rule:

- **United Cup** — mixed national team event, 1–10 January.
- **Davis Cup** — national team event (first-round qualifiers in February).
- **Laver Cup** — team exhibition.
- **Next Gen ATP Finals** — special-format invitation event (December).
- **Kooyong Classic** — exhibition; not in the ATP calendar.
- **ITF, junior, wheelchair, doubles and mixed events** — outside scope.

`january_scope.validate_scope` fails if an unsupported competition is marked
singles-eligible, or if an eligible competition lacks a cited source.

## 4. Operational event matrix

| January item | Implementation / contract | Acceptance |
|---|---|---|
| Tournament classification | AO main draw `GRAND_SLAM`/`HARD`/BO5; qualifying separate BO3 | **PASS (spec)** |
| Men's singles eligibility | only `ATP_MAIN` and `CHALLENGER`; team/exhibition excluded | **PASS (spec)** |
| Main draw vs qualifying | separate competition keys and draw stages; qualifying not forecast-enabled | **PASS (spec)** |
| BO3 / BO5 | explicit per competition; no missing-format default for AO | **PASS (spec + code)** |
| Deciding-set tiebreak | `TB10_AT_6_6` for AO main draw | **PASS (spec)** |
| UTC timestamps | tournament-local dates flagged; UTC conversion required | **PASS (contract)** |
| Original and revised scheduled start | append-only `match_versions`; original retained | **PASS (existing lifecycle)** |
| Actual competitive start | observed start retained separately | **PASS (existing lifecycle)** |
| Participant replacements | represented only with a two-snapshot draw diff | **PARTIAL / NOT IMPLEMENTED** |
| Lucky losers | `seed="LL"` may exist; entitlement unverified; no draw diff | **BLOCKED** |
| Withdrawals | provider status `WITHDRAWN`; unknown status fails closed | **PASS (metadata)** |
| Cancellations | explicit cancellation; absence never inferred as cancellation | **PASS (lifecycle)** |
| Official settlement | official result observation; internal until an official feed exists | **PARTIAL** |
| Bookmaker settlement | separate ledger contract; no grading table implemented | **BLOCKED** |
| 2027 entry lists and draws | not published; specification records `NOT_PUBLISHED` | **UNKNOWN** |
| AO qualifying exact first match | no official order of play; only the 10–16 Jan window | **UNKNOWN** |
| API-Tennis `get_draw` + `include_qualification` | no live call; entitlement unverified | **BLOCKED** |
| Owner approval / release | not requested | **BLOCKED** |

## 5. Readiness gate

The first independently eligible January fixture is **4 January 2027** (Brisbane
and Hong Kong; Canberra Challenger in the week of 5 January). Readiness must be
complete **before** that date. The AO qualifying classification must be ready
before the 10 January qualifying window opens.

```text
assert_readiness_precedes(spec, 2027-01-03T23:59Z)  -> passes
assert_readiness_precedes(spec, 2027-01-04T00:00Z)  -> raises
```

## 6. API-Tennis draw probe and PR #351 coordination

The public API-Tennis documentation (version 2.9.5) documents `get_draw` with
`include_qualification=1`. Documentation is not proof of entitlement on this
account. The request path and offline summariser already exist, but no live call
was made.

A future probe must be:

1. owner-authorised and budgeted;
2. a **single** request, not a new scheduled caller;
3. issued by the commercial data worker or under its ledger, coordinated with
   PR #351;
4. recorded with request time, tournament key, season, `include_qualification`,
   response source and response hash **before** parsing or storage;
5. not retried automatically; a failed probe is evidence.

Until then the entitlement remains `UNVERIFIED_NO_PROVIDER_CALL`, and the
specification's readiness matrix keeps it `BLOCKED_UNVERIFIED`.

## 7. Readiness matrix (machine-readable)

`readiness_matrix(spec)` emits the following statuses; the evidence JSON carries
the full rows.

| Item | Status |
|---|---|
| Authoritative 2027 schedule | PASS |
| Men's ATP/Challenger singles scope | PASS |
| Main-draw / qualifying separation | PASS |
| BO5 main draw / BO3 qualifying | PASS |
| Deciding-set tiebreak | PASS |
| UTC and original/revised/actual starts | PASS (contract) |
| Replacements, lucky losers, withdrawals, cancellations | PARTIAL |
| Official vs bookmaker settlement | PASS (contract only) |
| 2027 edition-specific rulebook | BLOCKED |
| Published entry lists and draws | UNKNOWN_NOT_PUBLISHED |
| AO qualifying exact first match | UNKNOWN |
| `get_draw` entitlement | BLOCKED_UNVERIFIED |
| Owner approval and release | BLOCKED_NOT_REQUESTED |
| Readiness before first eligible fixture | SCHEDULED_GATE |

## 8. Non-claims

- No 2027 entry list, seeding, draw, qualifying field, lucky loser or schedule
  is published here.
- No fixture is created by the specification (`fixtures: []`).
- No edition-specific 2027 rulebook was located.
- No provider call was made and no entitlement is asserted.
- No forecast, accuracy, Brier, log-loss or ROI result is claimed for January.

## 9. Decision

**Scope accepted, release not approved.** The January 2027 scope is now explicit
and fail-closed: supported tournaments, formats, dates with cited sources,
unsupported competitions, and every unknown are recorded. The remaining blockers
are external (published draws, 2027 rulebook, provider entitlement, owner
approval), not silent assumptions.
