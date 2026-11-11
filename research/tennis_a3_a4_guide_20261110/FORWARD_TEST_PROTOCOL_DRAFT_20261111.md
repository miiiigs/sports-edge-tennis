# Sports Edge Tennis A3/A4 preregistered forward evaluation, DRAFT 2026-11-11

**Status: PRE-REGISTRATION DRAFT, NOT FROZEN, NOT STARTED.**
The official live prediction runner remains untouched. Do not call any
November 5-9 cohort untouched, and do not pool different candidate versions.

## Test question

Does a fixed A3 matchup-risk gate and/or interpretable A4 reliability
policy improve future WIN/(WIN+LOSS) of the first-issued, pre-match
Model Picks relative to the existing original and >=70% shadow without
collapsing selection volume?

## Arms (identical eligible fixtures and recorded kickoff timestamps)

- **ORIGINAL_V1**: first-issued real production original aligned 60% rule,
  exactly as live, including its A1/A2 spread-tracking component. No revision
  or retrospective replacement.
- **QUALIFIED70_V1**: existing >=70% confidence filter, same original
  selection side, frozen before the test starts, no independent re-training.
- **A3_RISK_V2**: A1+A2/Manager proposed side unchanged. A3 evidence from
  original issue time: CONFIRMS / OPPOSES / ABSTAINED / UNKNOWN.
  Abstained is NEUTRAL, never a forced veto. Any gate must be fixed and
  explicitly specified in a frozen policy before first forward match.
- **A4_RELIABILITY_V1**: first-issued original outcome reliability model
  trained ONLY on earlier-known WON/LOST, with first-issue timestamp cutoff
  and fixed features, coefficients, preprocessing and regularization.
  Proposes selections or abstention but cannot change the winner.
- **A3_PLUS_A4_V1**: frozen combined gating, tracked independently.
  Do not blend this arm with individual A3/A4 or with future revisions.
- **GUIDE_PRIORITY_V1**: secondary context only, no winner vote. Missing,
  subscription-restricted, stale or post-issued odds are visibly missing.
  Bookmaker no-vig market probability is observational, and never modifies
  official picks or settlement.

**Critical freeze requirements** before any prospective test begins:
- Commit SHA of model code, model coefficients/fitted cutoff and training
  dataset as-of, exact features, thresholds, handling of UNKNOWN, fixture
  quality screen, winner-selection rule, odds priority algorithm, and
  emitted output schema. Full dated, signed manifest with SHA256 of the
  rules and train artifact.
- Document which official market/surface/tour levels are eligible and
  every original match that was excluded from the candidate arm.
- Nonempty candidate arms cannot be activated until code/test/DB
  acceptance is actually passed. No pilot output may be renamed a
  forward result after outcomes are known.

## Evidence contract

At each first-issued prediction, BEFORE kickoff, write append-only
`candidate_issuance` evidence with:
- canonical match ID, point-in-time player identity, event/tournament,
  Manila match date, originally scheduled start, source-observed kickoff,
  original first-issued timestamp, candidate emitted timestamp and run ID;
- ORIGINAL side/probability, existing qualified70 flag, A1/A2/A3 states,
  A3 risk, A4 calibrated reliability, selected side unchanged,
  candidate pick or abstain reason, Guide Odds provenance if available;
- exact frozen policy version+commit hash and training cutoff;
- unique match ID across the test, preserving earliest valid issued event,
  never re-select later if an early pick lost.

Every actual result also carries provider source ID, terminal type,
winner/void resolution, source observed and ingested timestamps, plus
the last scoring-as-of instant. No "pending=lost" conversion.

## Fixed duration, volume and coverage rules

- **Period:** at least **30 consecutive Manila calendar days** from the
  first actually registered future fixture. No cherry-picking days; report
  all calendar days, including days with zero picks and unfinished fixtures.
- **Sample:** at least **200 fully graded WON/LOST candidate selections**
  per arm to make the first main assessment. Voids/pending count toward
  issued and coverage but NOT graded accuracy. Extend test as necessary
  if 30 days is too short or official result ingestion lags.
- **Daily volume:** at least **15 distinct evaluated match days with 5+
  graded candidate selections**. If not met, continue instead of
  advertising a thin 75% figure.
- **Coverage:** at least **40% of eligible original first-issued picks**
  within the identical period, with no post-hoc fixture exclusion.
- **Interim:** review operational data after the first **50 fully graded**
  candidate selections, but DO NOT change the rules or call it proof.
- **Primary endpoint:** total WON / (WON+LOST), with a 95% confidence
  interval and original/qualified70 side-by-side pooled accuracy and
  selection coverage. The 73-75% target is a goal, not an acceptance
  guarantee. 200 graded observations are still statistically imprecise,
  and must not be used to claim permanent 75% performance.
- **Paired comparisons:** per-match set difference vs original, avoided
  losses, missed winners, discarded cohort accuracy, and Manager matches
  separately identified. Compare outcome on identical match IDs and
  first-issued times; report selection-volume tradeoff, not just percent.
- **Diagnostics:** daily W/L/V/P, result age, odds availability, A3 abstention
  reasons, Brier/log loss for calibrated probabilities, and tournament/surface
  stratification only as exploratory secondary analyses.

## No hindsight / no silent changes

- Historical Nov5-9 already influenced model development and are NOT
  an independent holdout. Nov13 report is explicitly DEVELOPMENT
  DIAGNOSTICS unless a separately identified untouched, timestamped
  historical cohort is independently certified.
- Any change to model inputs, features, weights, thresholds, target
  population or odds priority after freeze creates a NEW version and
  NEW forward cohort. Old version's ledger remains complete, immutable,
  and graded; never pool for the claimed independent result.
- Candidates must be appended before kickoff. Backfilled or
  post-match issuance is excluded and transparently counted.
- Pending and source-dependent outcomes remain provisional until
  official provider reconciliation. Dashboard and exported report must
  reconcile by matching first-issued canonical match ID.

## Go/no-go for Nov15

Nov15 is only an **earliest possible first issuance**, not the date of
validated improvement. It requires by Nov14:
1. all actionable pending identities and source/result ingestion issues
   triaged; independent original/qualified dashboard results reconcile;
2. exact code + model manifest frozen BEFORE outcomes;
3. actual automated or approved independent CI tests PASS with run/SHA,
   not just scripts committed; authenticated production shadow endpoint or
   append-only writer accepted;
4. Brian receives baseline, exclusions, coverage and pending behavior
   documentation; no live winner-selection replacement.

If a gate fails, publish BLOCKED status and new decision date; never
attribute a nonexistent 73-75% result to unstarted model training.
