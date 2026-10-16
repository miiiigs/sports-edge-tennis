# Tennis Model Picks, model inputs and Value Bets — audit 2026-10-15

This audit is read-only, against production DB `Postgres-Sdqz` and `main` `a823832`.
No production data, configuration, thresholds or model behaviour were changed, and
no provider calls were made. Counts are one pick per sporting match, never hourly
re-issues: production has 3,812 CONSENSUS rows for 285 matches. Win-rate intervals
are 95% Wilson intervals.

Reproduce with `ops/validation/a1_identity_bridge_shadow_20261015.py` (Agent 1 and
Model Picks shadow comparison). The Model Picks breakdown reuses the dashboard's own
CTEs, `aligned_production_model_picks_cte`, `first_production_model_picks_cte` and
`first_production_bets_cte`.

## 1. The 71% Model Picks figure

The headline on `/model-pick-results` (UTC) is the aligned population: the earliest
production evaluation per match that clears 60%, either Manager CONSENSUS or A1+A2
Wide-Spread tracking, from 2026-09-10.

| category | picks | W | L | pending | void | win | 95% CI | period |
|---|---|---|---|---|---|---|---|---|
| **Combined aligned (dashboard headline)** | 610 | 385 | 153 | 26 | 46 | **71.6%** | 67.6–75.2 | 09-10 → 10-15 |
| Manager-qualified (CONSENSUS ≥ 60%) | 189 | 123 | 45 | 7 | 14 | 73.2% | 66.1–79.3 | 09-10 → 10-14 |
| A1+A2 Wide-Spread tracking (mean ≥ 60%) | 421 | 262 | 108 | 19 | 32 | 70.8% | 66.0–75.2 | 09-10 → 10-15 |
| aligned, picked before the 60% rule existed | 217 | 138 | 57 | 4 | 18 | 70.8% | 64.0–76.7 | 09-10 → 09-26 |
| **aligned, genuinely forward (after 2026-09-26 10:09Z)** | 393 | 247 | 96 | 22 | 28 | **72.0%** | 67.0–76.5 | 09-27 → 10-15 |
| forward Manager | 118 | 73 | 29 | 6 | 10 | 71.6% | 62.2–79.4 | |
| forward Wide-Spread | 275 | 174 | 67 | 16 | 18 | 72.2% | 66.2–77.5 | |
| last 7 days | 113 | 69 | 23 | 15 | 6 | 75.0% | 65.3–82.7 | |
| original first CONSENSUS ledger, any probability | 285 | 168 | 89 | 12 | 16 | 65.4% | 59.4–70.9 | 09-09 → 10-15 |
| Value Bets, first published BET per match | 57 | 26 | 28 | 1 | 2 | 48.1% | 35.4–61.1 | flat ROI −15.2% |
| Shadow `A1_A2_AGREE_A3_ABSTAIN_V1` | 101 | 45 | 47 | 4 | 5 | 48.9% | 38.9–59.0 | flat ROI −12.6% |
| Historical replay / experiment Manager runs | 0 | — | — | — | — | — | — | none stored in prod |

- **The 71% is real and holds forward.** No pick was issued after match start.
- **Counting rule.** Taking each match's first evaluation and then applying the 60%
  floor (instead of the earliest evaluation that clears 60%) changes 3 of ~595
  matches, and the win rate is unchanged (71.4% vs 71.3%).
- **What Brian sees.** The Today page and the Model Picks Results headline both use
  the same aligned population (`aligned_model_picks.py`). That is the combined
  Manager-qualified plus Wide-Spread series; Wide-Spread is about 69% of it.
- **Versus the market.** On the 131 forward picks with a pre-start Pinnacle price,
  picks won 71.8% against a Pinnacle no-vig expectation of 66.9% (+4.8 pp). That is
  not statistically significant (z ≈ 1.2), and 89% of picks are Pinnacle's
  favourite. When a pick was Pinnacle's underdog (n=15), it won 27%.
- **By surface.** Picks on matches with **no stored surface** won 55.2% (n=29)
  against 72–75% on clay and hard.

## 2. Agent 1 input defect: the identity bridge is not applied to history (CONFIRMED)

- **Where the IDs diverge.** A2 history (`agent2_history.py:716`), A3 history
  (`agent3_history.py:145`) and live targets (`runner/targets.py:410`) all map
  live-provider player IDs to model IDs. `load_agent1_history` does not.
- **Effect on history.** 14,026 of 131,965 A1 history rows (essentially all of
  2026's API-Tennis results, about 1,400 a month) name a live ID. A1 predicts with
  the model ID, so those results never reach the rating it uses.
- **Effect on targets.** 1,753 of 1,802 recent targets involve such a player.

The shadow replay runs the production A1 code locally. It reproduces stored
production A1 exactly for 83.1% of targets, and within 0.005 for 86.6%.

| A1 variant (1,436 settled targets) | accuracy | Brier | log loss |
|---|---|---|---|
| stored production | 64.6% | 0.2220 | 0.6381 |
| replay CURRENT | 64.5% | 0.2222 | 0.6389 |
| DEDUP (drop 104 cross-provider copies) | 64.6% | 0.2220 | 0.6386 |
| **BRIDGED** | **68.1%** | **0.2055** | **0.5976** |
| BRIDGED + DEDUP | 68.3% | 0.2052 | 0.5968 |
| Pinnacle no-vig (808 priced) | 71.2% | 0.1896 | 0.5576 |

BRIDGED − CURRENT Brier is −0.0167 (95% bootstrap CI −0.0222 to −0.0111).

Effect on Model Picks, same runs, stored A2/A3, only A1 replaced:

| | picks | W | L | win | 95% CI |
|---|---|---|---|---|---|
| since Sep 10, production | 594 | 372 | 148 | 71.5% | 67.5–75.2 |
| since Sep 10, bridged A1 | 574 | 372 | 130 | 74.1% | 70.1–77.7 |
| forward, production | 395 | 246 | 96 | 71.9% | 66.9–76.4 |
| forward, bridged A1 | 384 | 248 | 82 | 75.2% | 70.2–79.5 |

- Picks the bridged A1 drops won 53.8% (n=65 scored). Picks it adds won 74.5% (n=47).
- **Caveat 1.** Bridges are resolved as of today, so a few may post-date a target.
  Identity is not outcome information, but forward shadow confirmation is still
  required.
- **Caveat 2.** This is the same period the defect was found on. It is a defect fix,
  not a tuned parameter, but forward shadow confirmation is still required.

This branch adds `load_agent1_history(..., bridge_model_identities=False)`. The
default leaves production unchanged; activating it is an owner decision, together
with a new A1 config version.

## 3. Cross-provider duplicates (105 pairs)

- **Composition.** 104 are cross-provider with the same winner, 1 is same-system,
  and none have conflicting winners.
- **Effect.** Removing the later-visible copy changes 76 A1 targets (median |Δp|
  0.0007), flips 2 sides and moves 2 across 60%. Brier improves by 0.00013.
- **Verdict.** Worth doing for hygiene. It is not a material driver of results.
  A3 effective H2H counts are also inflated for those pairs (code review), but A3
  rarely predicts.

## 4. Other confirmed data gaps

- **Frozen results.**
  - 147 matches started 1–30 days ago are still `SCHEDULED` with no result:
    83 ATP main and 64 Challenger.
  - That includes **64 Shanghai Masters main-draw matches from Oct 7–10**.
  - Missing results by start time (UTC): 0–4h 12.5%, 4–8h 16.5%, 8–12h 11.7%,
    12–16h 3.8%, 16–20h 1.6%, 20–24h 22.9%.
  - Asian and late-UTC sessions are under-recovered.
  - Draft PR #223 targets this; a bounded API-Tennis `get_fixtures` date-range
    re-fetch (about 1 request per ≤31-day window) needs authorization.
- **Shanghai identity.**
  - Qualifying (Oct 5–6) sits on tournament 855 "Shanghai" (ATP_MAIN qualifying).
  - The main draw sits on 60 "Shanghai Masters".
  - The Sep 6–13 "Shanghai" tournament 621 is the Challenger.
  - Seven `tournament_identity_corrections` exist; Masters qualifying is not
    merged into the main-draw edition.
- **Surface missing.**
  - 12,218 matches (9%) have no surface on either the match version or the edition.
  - That includes about 25% of recent Challengers (Bari, Buenos Aires II, Curitiba,
    Mouilleron-le-Captif, Porto 2, Columbus) and Chengdu/Hangzhou.
  - Agent 1 then uses overall Elo only; A2 abstains `UNKNOWN_SURFACE`.
  - The only surface-recovery tool (`authoritative_surface_recovery.py`) depends on
    Sportradar, which is no longer a provider.
  - 165 matches have an edition surface that the A1 history query ignores.
- **Best-of-5 lost for Grand Slams.**
  - API-Tennis has no best-of field, so `_best_of_sets` defaults to 3
    (`api_tennis_live.py:604`).
  - Every 2026 API-Tennis Slam main-draw match is stored best-of-3 (about 520 rows).
  - A2's Slam main-draw rule needs 5 (`agent2_history.py:503`), so A2 cannot model
    Slam main draws.
  - No live impact until the Australian Open in January 2027.
- **Conflicting stats.** 253 (match, player) pairs carry more than one distinct
  serve-stat row.
- **NULL abstain reasons.** All NULL `abstain_reason_code` rows predate migration
  0017 (2026-10-02). The current insert path rejects a NULL reason.
- **Unsupported API-Tennis status.**
  - The raw value is still unknown; PR #230 captures it.
  - The documented in-play values `"Set 1"…"Set N"` are unmapped. That is a
    plausible cause for in-play rows, but not obviously for whole slates of future
    fixtures.
  - The mapping will be decided from the captured values.

## 5. Value Bets: where performance is lost

Same sporting matches, by stage:

- Aligned Model Picks that **were** Value Bets won 59.5% (n=42 scored).
- Aligned Model Picks that **were not** Value Bets won 72.6% (n=496).
- All 44 Value Bets on Model Pick matches took the same side.

So Value Bet selection takes the Model Picks where the model most disagrees with
the market, and that is where the model is weakest:

| first Type A bet by Pinnacle view | n | W–L | model p | Pinnacle no-vig p |
|---|---|---|---|---|
| Pinnacle favourite | 28 | 17–9 | 0.791 | 0.649 |
| Pinnacle underdog | 30 | 9–20 | 0.623 | 0.394 |

- **Wins track Pinnacle, not the model.** Favourites won 65%, exactly Pinnacle's
  number. Underdogs won 31%, close to Pinnacle's 39% and far below the model's 62%.
- **Model–Pinnacle gap.** At ≥20 pp, bets went 7–14.
- **Ruled out.**
  - Stale prices: the offered and Pinnacle snapshots were 0–1 minutes old at
    decision time.
  - Event mismatches: the offered/Pinnacle price ratio is 0.81–1.15, mean 0.99.
  - Wrong Pinnacle references: Pinnacle's favourite probability matched realised
    65%.
- **No single book explains it.** 1XBET +1% (n=17), BETANO −29%, SBO −57%,
  UNIBET 0–4. All samples are small.
- **CLV formula.** `clv/service.py:451` measures CLV against the margin-free closing
  price. An unmoved line scores about −margin, so the −6.5% mean is mostly
  bookmaker margin. Pinnacle drifted against the pick on 24 of 50 bets and towards
  it on 17.
- **Edge formula.** edge = p_model × best offered − 1, with Pinnacle used only as a
  direction gate (`market/evaluator.py:292-300`). There is no cap on model–market
  divergence, and quarter-Kelly uses the raw model probability.

**Root cause.** Value Bets are not losing because of bad prices or matching. They
lose because "edge" is largest exactly where Agent 1/2 probabilities are least
reliable: matches where the model contradicts Pinnacle, often because of the
stale-identity A1 inputs in section 2.

## 6. Agent calculations (code review; see the PR description for line references)

- **A1.** Elo formulas match the spec (K = 250/(n+5)^0.4, surface weight
  min(n,10)/10·0.5). There is no inactivity decay. Replay is ordered by result
  `as_of`. A1 emits 0.500 when both players are cold-start, and that counts as a
  vote.
- **A2.** The Markov recursion was verified against an independent DP to 9 d.p.;
  equal servers give 0.5 under all six rules. Deviations from the spec:
  - whole-career stats, with no 12-month window;
  - minimums of 5 matches and 250 points;
  - log5 instead of Barnett–Clarke.
- **A3.** About 99.5% abstention is by design. It needs ≥3 recent direct meetings,
  or ≥8 shared opponents each met twice. Output is clamped to [0.35, 0.65].
- **Manager v1.** Probability is the plain mean of active agents. The spread gate
  is 0.06 with A3 abstaining (0.08 otherwise). The spec's data-sufficiency gate and
  its +1 pp A3-abstention threshold are not implemented.

## 7. Controlled comparison plan (nothing activated)

| step | variant | status |
|---|---|---|
| A | production unchanged | baseline above |
| B | corrected inputs only: A1 identity bridge (+ dedupe) | shadow above; next step is a forward shadow |
| C | coverage: frozen-result recovery, surface recovery, Slam best-of | needs authorised provider calls / fixes |
| D | calculation fixes: Value Bet divergence guard, A1 cold-start vote | evaluate separately |
| E | combined | only after B–D are measured individually |

Activation criteria for B:

- Forward shadow over at least about 150 scored Model Picks.
- The win rate does not decline, and Brier against outcomes improves.
- Coverage loss is at most 5%.
- Then owner approval and a new A1 config version.
