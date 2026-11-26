# Manager C six-way evaluation and bookmaker acquisition resolution (2026-11-26)

Integration branch: `integration/offseason-evidence-foundation-20261125`
(PR #392). Research and engineering record only. No production Manager rule,
threshold, model, provider configuration, migration or credential was changed.
No provider request, purchase or quota was consumed. Production access was
read-only.

## Decision summary

**Direct answer to Brian:** corrected Agent 1 independently outperforms Manager
C on the identical 218-fixture common support (accuracy `0.7248` vs `0.7156`,
Brier `0.18839` vs `0.19239`). Manager C's higher headline accuracy is produced
by abstaining on `57.6%` of fixtures and by selecting the easier subset, not by
producing better probabilities. On a fixture the Manager covers, its
probability is exactly the A1+A2 mean.

1. **Corrected Agent 1 alone is the strongest simple forecasting arm.** It
   covers 100% of the 599 settled eligible fixtures, scores Brier `0.21615` and
   log loss `0.62130`, and its identity correction is a measured improvement
   over original Agent 1 (paired Brier interval `[-0.02236, -0.00179]`).
2. **Manager C's incremental value is abstention, not better winners.** It
   raises accuracy to `0.7047` by covering only `42.4%` of the population. On
   the 254 shared fixtures its paired Brier and accuracy intervals both span
   zero. On the 218-fixture available-arm common support, Agent 1 alone is
   actually better: accuracy `0.7248` vs `0.7156`, Brier `0.18839` vs `0.19239`,
   log loss `0.56104` vs `0.56787`. On a covered fixture Manager C's probability
   is exactly the A1+A2 mean, so arm E equals arm B on shared support. The
   Manager changes only 5 correct A1 winners into incorrect ones and corrects
   only 3 incorrect A1 winners. It abstains on 345 settled fixtures, including
   206 where A1 was correct.
3. **Agent 2 does not add value.** A simple A1+A2 average is worse on every
   metric. A weight fitted only on earlier fixtures chooses **100% Agent 1**
   and 0% Agent 2. Agent 2 is not code-dependent on Agent 1, but the two
   probabilities correlate `0.573` and their side agreement is `71.7%`.
4. **Agent 3 cannot be evaluated in this cohort.** It abstained on all 599
   settled fixtures. Its evidence gate was not relaxed. There is no measured
   incremental information, positive or negative.
5. **The OddsPapi `171` hypothesis is false for this account.** Every one of
   the 11,087 stored tennis payloads contains market `121` with outcomes
   `121/122`; **none** contains `171`. In the published catalogue `171` is
   Counter-Strike moneyline. The normalized snapshot count is **21,976**, not
   zero. The earlier zero was an attribution/definition error.
6. **The remaining acquisition blockers are timing and history shape.** The
   historical `bookmakers` shape (list-valued `players["0"]`) was never
   ingested. Median first-identity lag is `10.0 h`. 99 raw observations failed
   normalization, 74 because the provider start had already passed and 25
   because the canonical start had passed even though the provider start had
   not.
7. **Ten fixtures were genuinely verified and persisted** in an isolated
   PostgreSQL database, with raw lineage, both participants, orientation,
   prices, states and acquisition timestamps.
8. **API-Tennis direct bookmaker access is already proven.** 22,515 normalized
   offered observations across 13 bookmakers were persisted before the OddsPapi
   cutover. Current quota/entitlement is unproven without an approved call. The
   stored corpus has zero canonical matches priced by both providers, so no
   identical-fixture comparison is possible from existing data.
9. **Agent 1 selects market favourites and does not beat the market.** On the
   retrospective matched top-800 set (366 priced), market Brier is `0.14669`
   versus Agent 1 `0.17609`; market log loss `0.45906` versus `0.54383`;
   favourite agreement is `91.0%`. The in-sample market+model coefficient is
   only `+0.030` overall (`+0.121` ATP, `+0.018` Challenger). No original-time
   two-sided quote exists in any top-800 population.

## Phase 1 - six-way forecasting comparison

Harness: `python -m sports_edge_tennis.validation.six_way_manager_comparison`.
Evidence: `docs/research/evidence/six_way_manager_comparison_real_20261126.json`.

Identical fixtures, cutoffs, outcomes and frozen 20%/40% thresholds as the
registered identity comparison: 656 eligible fixtures, 599 settled. The weight
for arm C was fitted on the 1,027 earlier fixtures, using 696 earlier
dual-agent settled pairs; the evaluation window was not used.

### Complete eligible-population coverage

| Arm | Coverage | Accuracy | Brier | Log loss | ECE | Calibration slope |
|---|---:|---:|---:|---:|---:|---:|
| A corrected A1 alone | 599/599 (100%) | 0.6461 | **0.21615** | **0.62130** | 0.0372 | 0.922 |
| B corrected A1 + A2 mean | 466/599 (77.8%) | 0.6073 | 0.22147 | 0.63146 | 0.0470 | 1.087 |
| C corrected A1 + A2 weighted | 466/599 (77.8%) | 0.6266 | 0.22245 | 0.63437 | 0.0489 | 0.872 |
| D corrected A1 + A3 | **0/599** | - | - | - | - | - |
| E Manager C with corrected A1 | 254/599 (42.4%) | **0.7047** | **0.19893** | **0.58255** | 0.0373 | 1.115 |
| F all available agents | 466/599 (77.8%) | 0.6073 | 0.22147 | 0.63146 | 0.0470 | 1.087 |
| R1 original A1 alone | 599/599 (100%) | 0.6427 | 0.22827 | 0.65253 | 0.0658 | 0.664 |
| R2 original Manager C | 283/599 (47.2%) | 0.6784 | 0.21259 | 0.61388 | 0.0614 | 0.892 |

Arm F equals arm B because Agent 3 has zero coverage. Arm C requires both A1
and A2, so even a 100% A1 weight loses the A2-missing fixtures; it is not the
same as A alone.

### Common support

The all-arm intersection is zero because arm D has zero coverage. That is a
measured blocker, not a usable comparison. The meaningful common support is the
intersection over arms with non-zero coverage (A, B, C, E, F, R1, R2): **218
settled fixtures**.

| Arm | n | Accuracy | Brier | Log loss | ECE |
|---|---:|---:|---:|---:|---:|
| A corrected A1 | 218 | **0.7248** | **0.18839** | **0.56104** | 0.0986 |
| B A1+A2 mean | 218 | 0.7156 | 0.19239 | 0.56787 | 0.0515 |
| C weighted | 218 | 0.7248 | 0.18839 | 0.56104 | 0.0986 |
| E Manager C | 218 | 0.7156 | 0.19239 | 0.56787 | 0.0515 |
| F all available | 218 | 0.7156 | 0.19239 | 0.56787 | 0.0515 |
| R1 original A1 | 218 | 0.7064 | 0.21032 | 0.61772 | 0.0769 |
| R2 original Manager | 218 | 0.7110 | 0.20249 | 0.59239 | 0.0699 |
| D A1+A3 | 0 | - | - | - | - |

With Agent 3 absent, Manager C's rule is the A1+A2 mean restricted to
agreement and a 60% floor. On the fixtures it covers, its probability is
therefore the same as arm B; the Manager differs only by which fixtures it
covers. On identical fixtures Agent 1 alone has the better Brier, log loss and
accuracy. The Manager's higher complete-population accuracy is a selection
effect, not a forecasting improvement.

### Paired uncertainty (vs corrected A1, match-cluster bootstrap)

| Contrast | Brier improvement (A minus other), 95% | Accuracy improvement, 95% |
|---|---|---|
| A vs B | `[-0.00598, +0.00782]` | `[-0.0150, +0.0536]` |
| A vs E (Manager) | `[-0.01063, +0.00738]` | `[-0.0118, +0.0276]` |
| A vs R1 (original A1) | `[-0.02236, -0.00179]` | `[-0.0317, +0.0384]` |
| A vs R2 (original Manager) | `[-0.02000, +0.00704]` | `[-0.0565, +0.0283]` |

Positive Brier improvement means A scored worse. The identity correction is the
only contrast whose Brier interval excludes zero. Manager C's point advantage
is not statistically reliable on shared fixtures.

### ATP and Challenger

| Cohort | Arm | Covered | Accuracy | Brier |
|---|---|---:|---:|---:|
| ATP main (68 settled) | A corrected | 68 | 0.6471 | 0.22312 |
| | E Manager | 16 | 0.7500 | 0.19150 |
| | R1 original A1 | 68 | 0.6765 | 0.21805 |
| | R2 original Manager | 16 | 0.6875 | 0.20037 |
| Challenger (531 settled) | A corrected | 531 | 0.6460 | 0.21526 |
| | E Manager | 238 | 0.7017 | 0.19943 |
| | R1 original A1 | 531 | 0.6384 | 0.22958 |
| | R2 original Manager | 267 | 0.6779 | 0.21332 |

The identity correction helps overall and on Challenger. On the 68 ATP settled
fixtures the original A1 is marginally better; the sample is too small to
support a tour-specific policy.

### Reconciliation of A1 and the Manager

| Accounting (all settled with A1) | Count |
|---|---:|
| Manager abstained | 345 |
| - correct A1 lost to abstention | 206 |
| - incorrect A1 lost to abstention | 139 |
| Correct A1 rejected or overruled | 5 |
| - correct winner changed to incorrect | 5 |
| Incorrect winner corrected by additional agents | 3 |
| Both correct | 176 |
| Both incorrect | 70 |
| Coverage lost through abstention | 57.6% |

The Manager is not adding a second opinion on winners: on shared fixtures its
side disagrees with A1 only `3.1%` of the time, its p1 correlation is `0.937`,
and its Brier-contribution correlation is `0.929`. Its value is the decision to
stay out.

### Agent 2 dependency audit

- Agent 2 does not import Agent 1 and has no direct probability or rating
  dependency.
- Shared inputs are the canonical match/result history and PIT-visible stat
  observations; Agent 1 uses surface Elo, Agent 2 uses a serve/return point
  model.
- Corrected A1 vs A2 correlation: `0.573`; R² `0.329`; mean absolute p1
  difference `0.131`; side agreement `0.717` (509 fixtures).
- Earlier-data weight: **1.0 Agent 1**. The fitted Brier rises monotonically as
  A2 receives weight.
- Adding A2 lowers accuracy (`0.6073` vs `0.6461`), worsens Brier
  (`0.22147` vs `0.21615`) and log loss, and does not produce a paired
  improvement. Agent 2 is not independent enough, and not accurate enough on
  this cohort, to add value.

### Agent 3 incremental information

- Agent 3 active on settled fixtures: **0 of 599**.
- Agent 3 abstained on 599 of 599. Coverage `0.0%`.
- D and F therefore cannot test incremental information. No evidence gate was
  relaxed, no probability was imputed, and no "all agents" claim is made.

### Main-draw scope correction

`tour_scope` and `qualifying_flag` are different fields and were conflated in
the earlier "main draw" wording.

| Population | Settled | What it is |
|---|---:|---|
| ATP main | 68 | `tour_scope='ATP_MAIN'` |
| Challenger | 531 | `tour_scope='CHALLENGER'` |
| Main draw | 521 | `qualifying_flag=false` |
| Qualifying | 78 | `qualifying_flag=true` |

The correct main-draw population in the full settled cohort is 521, not 599.
The marginal Challenger population is Challenger-only and includes qualifying.

### Marginal Challenger arithmetic correction

The frozen marginal reconciliation is exact:

- 143 settled, 92 wins, expected `94.559351`, residual `-2.559351`,
  Brier `0.2275869325`, log loss `0.6473955793`.

Splitting by main draw versus qualifying:

| Subset | Settled | Wins | Expected | Residual | Brier | Log loss |
|---|---:|---:|---:|---:|---:|---:|
| Main draw | 130 | 86 | 85.9815375 | **+0.0184625** | 0.2213879781 | 0.6344641831 |
| Qualifying | 13 | 6 | 8.5778135 | **-2.5778135** | 0.2895764768 | 0.7767095417 |
| Total | 143 | 92 | 94.559351 | -2.559351 | 0.2275869325 | 0.6473955793 |

The entire pooled shortfall is the 13 qualifying matches. The main-draw
Challenger marginal is calibrated within `+0.018` wins. The earlier "143
main-draw" framing was wrong; the correct main-draw N is 130.

Uncertainty arithmetic: independent variance `31.952148` (SD `5.652623`);
player-clustered variance `71.036590` (SD `8.428321`, design effect `2.223218`);
pooled residual z `-0.416744`. The independent z (`-0.4527`) understates
uncertainty because selections share favourites.

## Phase 2 - OddsPapi normalization root cause

Harness: `python -m sports_edge_tennis.validation.oddspapi_normalization_root_cause`.
Evidence: `docs/research/evidence/oddspapi_normalization_root_cause_real_20261126.json`.

### What is actually in the raw payloads

| Check | Measured |
|---|---:|
| Raw OddsPapi observations | 11,087 |
| With `bookmakerOdds` (live shape) | 11,087 |
| With `bookmakers` (historical shape) | **0** |
| With market `121` | **11,087** |
| With market `171` | **0** |
| Bookmakers in payloads | `pinnacle+30` 3,649; `bet365` 3,143; `1xbet` 3,017; `unibet` 1,278 |

The published tennis tutorial's `171`/`172` assumption is not this account's
tennis market. The current catalogue page lists tennis `moneyline` at `121` and
Counter-Strike `moneyline` at `171`; the stored payloads confirm `121`/`122`.
The adapter was changed to resolve `121` first and to reject other market IDs
unless an explicit catalogue entry is supplied. It no longer guesses from any
two-outcome market.

### Normalized quotes do exist

| Check | Measured |
|---|---:|
| Snapshots joined through `raw_odds_observation_id` to OddsPapi raw | **21,976** |
| Distinct matches / fixtures | 513 / 513 |
| Snapshots with an `ODDSPAPI` source-id prefix | **0** |

`odds_snapshots` has no `provider_code` column and uses the generic
`SE_LIVE_ODDS_SNAPSHOT_V1:` prefix. A query that filters `source_snapshot_id`
for `ODDSPAPI` returns zero even though the raw lineage contains 21,976 quotes.
That attribution error is the origin of the "zero normalized snapshots" claim.
Provider attribution must join `raw_odds_observation_id`.

| Bookmaker | Source | Open | Suspended |
|---|---|---:|---:|
| PINNACLE | SHARP | 7,230 | 0 |
| BET365 | OFFERED | 5,820 | 396 |
| 1XBET | OFFERED | 5,630 | 370 |
| UNIBET | OFFERED | 2,528 | 2 |

### The 99 failed observations

| Reason | Count |
|---|---:|
| Raw observations with no snapshot | 99 |
| - provider start already passed | 74 |
| - pre-start by provider, canonical start had passed | 25 |
| - of the 99, unlicensed-book payloads (overlapping, not additive) | 13 |
| Ingest runs `COMPLETED` / `FAILED` | 10,988 / 99 |
| Normalization error reason | `match_not_prematch` 198 |

The normalizer rejects `match.match_start_at <= observation_time`. Provider
`startTime` and canonical start are not identical. The 25 pre-start failures are
canonical-start ambiguity, not a missing market and not a parser bug. The 74
post-start rejections are correct.

### Acquisition timing

In the 2026-10-15 to 2026-11-21 six-way window:

| Check | Measured |
|---|---:|
| First-issued A1 predictions | 1,706 |
| With an OddsPapi identity ever | 319 |
| Identity before issue | **4** |
| Median identity lag | **36,010 s (10.0 h)** |
| Strict original-time comparisons | **0** |

Four identities arrived before issue, but strict evidence also requires an
independent OddsPapi tournament mapping and a verified edition window. The
prior census measured zero independent mappings. "Normalized" and "strict
original-time" are different gates; the first is now 21,976, the second remains
zero.

### Historical shape

No stored payload uses `bookmakers`. The historical endpoint returns
`bookmakers[slug].markets[marketId].outcomes[outcomeId].players["0"]` as a
**list** of snapshots. The live parser correctly refuses a list. Historical
reconstruction now has a separate selector that:

- takes the latest entry at or before the cutoff, by `createdAt`;
- lets the latest inactive entry define a suspension (never resurrects an
  earlier active price);
- rejects entries without a timestamp or a valid decimal price;
- separates `createdAt` (provider-recorded), `changedAt` (provider last change),
  `bookmakerChangedAt` (bookmaker clock) and our acquisition time;
- never labels a late historical price as originally available.

## Phase 3 - ten verified normalized fixtures

Harness: `python -m sports_edge_tennis.validation.oddspapi_verified_fixtures`.
Evidence: `docs/research/evidence/oddspapi_verified_fixtures_isolated_20261126.json`.

An isolated PostgreSQL 16 database was created, migrations 0001-0024 were
applied, and ten existing production raw observations were replayed through the
real `ingest_provider_observation` path.

| Check | Result |
|---|---:|
| Fixtures verified | **10** |
| ATP main / Challenger | 5 / 5 |
| Snapshots persisted | 20 |
| Distinct matches | 10 |
| Bookmaker | `pinnacle+30` (all ten) |
| Two-sided prices | 10/10 |
| Orientation verified | 10/10 |
| Raw lineage verified | 10/10 |
| All snapshots OPEN | 10/10 |
| Ingest runs COMPLETED | 10/10 |

Each evidence row preserves the production raw id and SHA-256, canonical
production and isolated match ids, both provider and canonical participant
identities, market `121`, outcomes `121/122`, oriented `PLAYER_1`/`PLAYER_2`
prices, state, `startTime`, real `received_at`, `source_observed_at`, `as_of`,
`ingested_at`, and the inserted snapshot ids. The acquisition time is the real
poll time; no provider-observed timestamp is invented for the live shape.

Negative regression tests in `tests/test_oddspapi_historical_reconstruction.py`
cover reversed participant ordering, missing second price, suspended latest
state, duplicate observations, invalid decimal odds, post-start observations,
missing bookmaker timestamps, incorrect market type, incoherent two-sided
states, the historical list shape in the live parser, numeric keys, and the
four time bases.

## Phase 4 - API-Tennis direct bookmaker acquisition

Harness: `python -m sports_edge_tennis.validation.api_tennis_odds_entitlement`.
Evidence: `docs/research/evidence/api_tennis_odds_entitlement_real_20261126.json`.

No API-Tennis request was made. The existing subscription is already evidenced
by persisted rows:

| Check | Measured |
|---|---:|
| API-Tennis offered raw observations | 22,515 |
| Distinct events | 1,335 |
| First / last acquisition | 2026-09-09 / 2026-10-10 |
| Normalized bookmakers | 13 |
| `LIVE_ODDS` runs completed / failed | 20,483 / 2,032 |
| Adapter | `API_TENNIS_OFFERED_ODDS_V1` |

The documented `get_odds` method is implemented in
`live/transport.py::fetch_api_tennis_odds` with `method=get_odds`, `APIkey`,
`date_start`, `date_stop`. The adapter maps `Home/Away` to the first/second
player source ids; reconciliation then resolves canonical players. Historical
runs prove the account, key and endpoint worked.

Current plan, remaining quota and whether `get_odds` is still entitled cannot
be established from the database. That needs an owner-approved call or the
provider account page; none was made.

### Direct comparison with OddsPapi

| Priced matches | Count |
|---|---:|
| API-Tennis | 1,327 |
| OddsPapi | 513 |
| Both providers | **0** |

There is no canonical match priced by both providers in the stored corpus.
Same-book and provider-consensus joins both return zero. A direct comparison
requires a prospective overlap capture; it cannot be manufactured
retrospectively. Both providers should be retained.

## Phase 5 - bookmaker benchmarking of the Agent 1 top-800

Harness: `python -m sports_edge_tennis.validation.agent1_top800_bookmaker`.
Evidence: `docs/research/evidence/agent1_top800_bookmaker_real_20261126.json`.
As of `2026-11-21T00:00:00Z`; 3,011 settled first-issue A1 predictions available.

### Original-time coverage

| Population | Settled | Any quote | Original-time two-sided |
|---|---:|---:|---:|
| top800 by confidence, all | 800 | 382 | **0** |
| top800 by confidence, ATP | 571 | 261 | **0** |
| top800 by confidence, Challenger | 800 | 385 | **0** |
| latest800 settled, all | 800 | 98 | **0** |
| latest800 settled, ATP | 571 | 261 | **0** |
| latest800 settled, Challenger | 800 | 98 | **0** |

No original-time benchmark exists. Every stored quote for these populations is
late. The retrospective block is therefore labelled separately and is not used
as PIT evidence.

### Retrospective matched benchmark (explicitly late)

| Population | Priced | Coverage | States | Model Brier | Market Brier | Model LL | Market LL | Favourite agreement |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| top800 confidence, all | 366 | 45.75% | LATE 366 | 0.17609 | **0.14669** | 0.54383 | **0.45906** | 90.98% |
| top800 confidence, ATP | 254 | 44.48% | LATE 254 | 0.19129 | **0.17530** | 0.56268 | **0.52279** | 80.71% |
| top800 confidence, Challenger | 366 | 45.75% | LATE 366 | 0.19466 | **0.16309** | 0.58910 | **0.49924** | 87.70% |
| latest800, all | 88 | 11.00% | LATE 88 | 0.20286 | **0.18311** | 0.59262 | **0.54854** | 85.23% |

Proportional de-vig was used. Mean/median two-sided odds for top800 all:
`3.008` / `1.84` (n=732 price observations). Missing coverage is reported
explicitly (434 of 800 for top800 all); nothing is extrapolated.

### Does Agent 1 add information beyond the market?

| Population | Market-only coefficient | Market+model model coefficient | In-sample n |
|---|---:|---:|---:|
| top800 all | 0.590 | **+0.030** | 366 |
| top800 ATP | 0.578 | **+0.121** | 254 |
| top800 Challenger | 0.595 | **+0.018** | 366 |
| latest800 all | 0.635 | **+0.094** | 88 |

Agent 1 largely selects the market favourite (91% agreement overall) and the
market beats it on Brier and log loss. The incremental coefficient is small,
especially on the larger Challenger and overall samples. This is an in-sample
diagnostic, not a forward test, but it is consistent with the six-way result:
the corrected Agent 1 is a reasonable standalone model, not a demonstrated
source of edge over the closing market.

The top800 confidence population is overconfident on this window: 599/800 wins
versus 674.107 expected, residual `-75.107`, independent z `-7.37`, Brier
`0.19707`. The latest800 population is 510/800 versus 545.875 expected,
residual `-35.875`, z `-2.81`, Brier `0.22755`.

## Exact blockers

1. Production migrations 0023/0024 are still unapplied. No schema change was
   made or proposed here.
2. OddsPapi historical acquisition has never been ingested (`bookmakers` list
   shape absent). The selector is implemented and tested, not activated.
3. First-identity median lag is 10.0 h; original-time top-800 coverage is zero.
4. Canonical versus provider start semantics remain unresolved; 25 pre-start
   observations were rejected as `match_not_prematch`.
5. Independent OddsPapi tournament mappings remain zero, so strict
   original-time comparisons remain zero even when an identity arrives early.
6. API-Tennis current plan/quota is unverified without an approved billable
   call. No call was made.
7. Zero canonical matches are priced by both providers in stored data; a direct
   comparison needs prospective overlap capture.
8. Agent 3 has zero coverage on the evaluation cohort; no incremental claim is
   possible without relaxing its evidence gate, which was not done.

## Validation

- `tests/test_six_way_manager_comparison.py`: 11 passed.
- `tests/test_oddspapi_historical_reconstruction.py`: 14 passed.
- `tests/test_oddspapi_tennis_provider.py`: 6 passed.
- `tests/test_agent1_top800_bookmaker.py`: 6 passed.
- Full suite and PostgreSQL CI are reported on the pushed commit; see the PR
  checks for the authoritative result.

## Reproduction commands

```text
python -m sports_edge_tennis.validation.six_way_manager_comparison \
  --output docs/research/evidence/six_way_manager_comparison_real_20261126.json

python -m sports_edge_tennis.validation.oddspapi_normalization_root_cause \
  --output docs/research/evidence/oddspapi_normalization_root_cause_real_20261126.json

ISOLATED_DATABASE_URL=... python -m \
  sports_edge_tennis.validation.oddspapi_verified_fixtures \
  --output docs/research/evidence/oddspapi_verified_fixtures_isolated_20261126.json

python -m sports_edge_tennis.validation.api_tennis_odds_entitlement \
  --output docs/research/evidence/api_tennis_odds_entitlement_real_20261126.json

python -m sports_edge_tennis.validation.agent1_top800_bookmaker \
  --as-of-utc 2026-11-21T00:00:00Z \
  --output docs/research/evidence/agent1_top800_bookmaker_real_20261126.json
```

All production connections used `default_transaction_read_only=on`. The only
writes were to a disposable local PostgreSQL 16 database.
