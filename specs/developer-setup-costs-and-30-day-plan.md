# Developer Onboarding, Costs and 30-Day Delivery Plan

**Version 1.0 — 15 August 2026**

Read this alongside `football-build-spec.md` and `tennis-build-spec.md`. This document covers
what is shared between the two builds: environment, accounts, costs, working practices, and the
delivery schedule.

---

## 1. Before you write any code — read these

Both papers are free. Read them before day one, not during.

1. **Wheatcroft (2020)**, *A profitable model for predicting the over/under market in football*.
   Free accepted version at the LSE research repository. §2 contains the GAP ratings definition.
   This is the mathematical core of the football build.
2. **Wheatcroft (2021)**, *Forecasting football matches by predicting match statistics*. Free on
   arXiv as `2001.09097`.
3. **Barnett, Brown & Clarke**, *Developing a model that reflects outcomes of tennis matches*.
   Free PDF at `strategicgames.tripod.com/8mcs.pdf`. The Markov hierarchy.
4. **Madurska (2012)**, Imperial College MEng thesis on set-by-set tennis prediction. Free PDF at
   `doc.ic.ac.uk/teaching/distinguished-projects/2012/a.madurska.pdf`. Contains implementation
   detail the papers omit.

You do not need to understand tennis or football to build this. You do need to understand what
the models output and why the comparison against the bookmaker's price is the whole point.

---

## 2. The one-paragraph summary of what these systems do

They estimate the probability of a sporting outcome, convert it into a fair price, strip the
bookmaker's profit margin out of the market price to recover what the market really thinks, and
recommend a bet only where our fair price is meaningfully longer than the market's. **They are
not winner-prediction systems.** Recommending a competitor we expect to lose is a correct and
expected output when the price is wrong by more than the probability is unfavourable.

The success metric is **not** how many picks win. It is Closing Line Value — whether the price we
took beat the final price before the event started — and calibration, which asks whether a 60%
forecast happens 60% of the time. Both of those reach a meaningful sample in weeks. Profit takes
thousands of bets.

---

## 3. Environment

| Component | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | pandas, numpy, scipy, scikit-learn, statsmodels |
| Database | **PostgreSQL 15+** | Not SQLite, not spreadsheets. You will run thousands of backtests. |
| Migrations | Alembic | Schema is going to change. Version it. |
| Scheduling | APScheduler for v1; Airflow or Prefect if it grows | Cron is acceptable to start |
| Config | Environment variables, `.env` excluded from git | API keys never in the repository |
| Testing | pytest | See §5 for the tests that matter |
| Version control | Git, two separate repositories | `betting-football`, `betting-tennis` |
| Deployment | Single VPS is sufficient (Hetzner, DigitalOcean, ~£20–40/month) | No need for cloud scale |
| Alerting | Email via SES or SMTP; optionally a webhook | Alerts must reach a human |

**Two repositories, two databases, two deployments.** They share no code. If you find yourself
writing an abstract base class for "a sport", stop — the abstraction will cost more than it
saves and it will let a bug in one build silently affect the other.

---

## 4. Accounts and API keys to obtain

Get these on day one. Waiting on account approval is the most common avoidable delay in a build
like this.

| Service | Purpose | Key needed | Cost |
|---|---|---|---|
| **Football-Data.co.uk** | Historical football results, shots, corners, cards, referee, odds from ~20 books | No | Free |
| **Jeff Sackmann `tennis_atp`** (GitHub) | Historical ATP and Challenger match data with serve stats | No | Free |
| **Tennis-Data.co.uk** | Historical tennis odds | No | Free |
| **FBref / Understat** | xG for the top five leagues | No (scraping, respect rate limits) | Free |
| **The Odds API** | Live odds, both sports | Yes | Free dev tier, then paid |
| **API-Football** or **Sportmonks** | Fixtures, lineups, injuries, suspensions | Yes | Paid |
| **Betfair Exchange API** | Sharp reference price, free data | Yes (app key + funded account) | Free API, account must be funded |
| **OddsJam** or equivalent | Wide book coverage including sharp books | Yes | Paid, upgrade later |

### The one that must be resolved before anything else

**You need a sharp reference price.** Every de-vig calculation, every edge number and the entire
CLV measurement layer depends on having a credible low-margin benchmark to price against.

Pinnacle is the industry standard benchmark but **does not accept UK customers**, so you cannot
simply read prices off their site. You must either obtain Pinnacle prices through a data
provider that carries them, or use the **Betfair Exchange** as your sharp reference instead
(the exchange has no traditional bookmaker margin, only commission, which makes it a good fair
value proxy).

**Resolve this in the first two days.** If neither is available, the measurement layer does not
work and you must raise it immediately rather than substituting a high-margin bookmaker — doing
that silently invalidates every number the system produces.

---

## 5. Costs

Estimates as at August 2026. **Verify current pricing before committing** — these move, and
several providers have changed tiers in the past year.

| Service | Tier | Estimated monthly |
|---|---|---|
| The Odds API | Mid tier, both sports, frequent polling | £30–60 |
| API-Football or Sportmonks | Fixtures, lineups, injuries, both sports | £30–80 |
| Tennis live data (if via a separate provider) | Scores and schedule | £30–50 |
| VPS hosting | Single instance, Postgres included | £20–40 |
| **Optional upgrade:** OddsJam or equivalent | Wide book coverage, faster refresh | £100–150 |
| Football-Data, Sackmann, Tennis-Data, FBref, Understat | — | £0 |
| Betfair Exchange API | — | £0 (funded account required) |

**Realistic total: £200–300 per month for a serious setup.** Call it £250.

### Essential versus optional

**Non-negotiable:**
- A live odds feed with a sharp reference price. Without it there is no edge calculation and no
  CLV. This is the single most important line item.
- Football-Data.co.uk and Sackmann. Both free, and they carry the entire backtest.
- Hosting.

**Can wait:**
- Wide-coverage odds aggregation (OddsJam tier). Start with The Odds API, upgrade once the
  pipeline works. **But note:** the number of books you can read is measured as contributing
  roughly four times what the modelling contributes. This is the first thing to upgrade once
  the system is live, not the last.
- Lineup and injury feeds. The veto agent can start with manual checks and automate later.
- Tennis live data beyond the odds feed.

---

## 6. Working practices that are not optional

These exist because of specific, documented failure modes. Each one has cost somebody a working
model.

### 6.1 Version everything together

Model code, feature code and calibration are **one versioned bundle**, stamped on every
prediction and every decision row. When results shift in month three you must be able to say
which version produced which pick. Without this, you cannot diagnose anything.

### 6.2 Save every feed with an as-of timestamp

Never overwrite a price. `odds_snapshots` is append-only. This is the mechanical defence against
look-ahead bias, which is the single most common way a backtest lies — and it is invisible after
the fact if you have not prevented it structurally.

### 6.3 Walk-forward validation only

Train on periods 1–N, test on N+1, roll forward. Never fit and test on the same data. Simulate
bets in chronological order.

Models validated with a basic train/test split showed roughly 12% *better* backtest performance
and then dropped about 28% in forward testing compared with cross-validated equivalents. The
better the backtest looks, the more suspicious you should be.

### 6.4 Count and record every configuration you try

Every learning rate, every threshold, every feature set. Keep a running configuration log.

Testing just 10 strategy variants produces an expected in-sample Sharpe ratio of 1.57 with
**zero true skill.** A backtest result reported without stating how many configurations were
tried is not evidence of anything. This is the most important discipline in the entire build and
the easiest to skip.

### 6.5 Log NO_BET decisions

Most systems only log bets. If you only log bets you can never answer "is the threshold too
tight or too loose", you cannot reconstruct why a fixture was skipped, and you cannot measure
calibration on the full slate — which is what gets you a usable sample in weeks instead of
years.

### 6.6 Paper-trading mode from day one

Same code path as live, one configuration flag. Every pick logged, settled, and scored. Nothing
staked. This is not a phase to be completed and moved past — it runs permanently alongside live.

### 6.7 Fail loudly

Data freshness gates that refuse to publish. Assertions on unmatched team and player names
rather than silent drops. Alerts that reach a human. The failure mode to design against is not a
crash — it is the system quietly producing confident, plausible, wrong output off stale data for
three weeks before anyone notices.

### 6.8 Tests that actually matter

Beyond normal unit coverage:

- **Tennis:** with both players' serve probability set to 0.5, the Markov model must return
  exactly 0.5 for match, set, game and tiebreak. With both set to 0.65, hold probability must be
  high but tiebreak probability must still be exactly 0.5.
- **De-vig:** on a market with zero overround, the de-vigged probabilities must equal the raw
  implied probabilities to within floating-point tolerance.
- **De-vig:** de-vigged probabilities must always sum to exactly 1.0.
- **Kelly:** at zero edge, stake must be exactly zero. At negative edge, stake must be zero, not
  negative.
- **Backtest:** a run over a date range must produce identical results when executed twice.
  Non-determinism here means state is leaking between fixtures.
- **Look-ahead guard:** an assertion in the backtest harness that no feature's source timestamp
  is later than the fixture's kickoff time. Make this a hard failure.

---

## 7. The 30-day plan

**Sequencing decision: football first.** It has the stronger evidence base, higher fixture
volume, and a simpler core model. Tennis follows in week four.

Each day lists an objective and a concrete deliverable. The deliverable is the thing that gets
ticked off — "worked on the ingest pipeline" is not a deliverable, "all ten league-seasons
ingested with a null-rate report" is.

**Use the done-list format in §8.** Report daily. The blockers column is as important as the
completed column — a day lost to a broken API is information, a day with nothing in either
column is not.

---

### Week 1 — Foundations and data

| Day | Objective | Deliverable |
|---|---|---|
| **1** | Environment and accounts. Repositories created, Postgres running, all free data sources downloading, all paid accounts applied for. Resolve the sharp-reference-price question (§4). | Two repos initialised. Postgres up. A written answer on the sharp price source. Config log started. |
| **2** | Football schema. Implement the tables from `football-build-spec.md` §3 with Alembic migrations. | Migrations run clean from empty. Schema diagram committed. |
| **3** | Football-Data ingest. Parse every CSV for every in-scope league, all available seasons. Handle format changes across years — the column set has changed over time. | All league-seasons ingested. A null-rate report per league per season per column, committed to the repo. |
| **4** | Team-name canonicalisation. Build the canonical team table with aliases across Football-Data, the odds feed, and FBref. | Every team resolved. A hard assertion on unmatched names. Zero silent drops. |
| **5** | Odds ingest and the snapshot layer. Historical odds from Football-Data joined to fixtures; live odds pipeline pulling and appending with timestamps. | Historical odds joined with a match rate report. Live poller running and writing append-only snapshots. |
| **6** | De-vig module. Power method with numerical solve, Shin as an alternative, naive proportional implemented only as a comparison baseline. | Module with tests from §6.8. A worked comparison table across all three methods on a real three-way market, committed. |
| **7** | Backtest harness skeleton. Chronological iteration, as-of feature construction, the look-ahead assertion, deterministic replay. | Harness runs end to end on dummy predictions. Look-ahead guard demonstrably fails when deliberately violated. |

**End of week 1:** data in, prices in, margin stripped, a harness that cannot cheat. No model
yet. This is correct — everything downstream is worthless if this layer is wrong.

---

### Week 2 — The football model

| Day | Objective | Deliverable |
|---|---|---|
| **8** | GAP ratings core. Attack and defence ratings per team, per statistic, per venue. Prediction and update steps from `football-build-spec.md` §4.2–4.3. | Ratings engine implemented. Ratings rebuild from full history in reasonable time. Sanity check: strong teams rate high. |
| **9** | GAP ratings validation. Predicted shots and corners versus observed, by league. Mean absolute error and correlation. | Accuracy report per league per statistic. This tells you whether the ratings work before any betting logic exists. |
| **10** | Route A — direct classification. Logistic regression from GAP-predicted statistics to Over/Under 2.5 and BTTS probability. | Classifier trained walk-forward. Log-loss and Brier score reported against a naive baseline. |
| **11** | Calibration layer. Decile binning, reliability diagram, Platt scaling and isotonic regression on held-out data. | Reliability diagram for both markets. Calibrated probabilities versioned alongside the model. |
| **12** | First honest backtest. Walk-forward across all available seasons, using **opening** odds for the strategy. Full configuration log. | Backtest report: bets, ROI with confidence interval, drawdown profile, and **the number of configurations tried**. |
| **13** | Price scanner. Compare model probability against best available odds across all monitored books; sharp price establishes fair, soft price is what we bet into. | Scanner producing edge percentages. Backtest of the price-scanning layer *alone*, with no model, as a control. |
| **14** | Threshold and staking. Implement the threshold table (§7.4), minimum acceptable price, half-Kelly capped at 2%. | Thresholds configurable. Sensitivity analysis across threshold values, with every value tested recorded. |

**End of week 2:** a football model that produces calibrated probabilities and an audited
backtest. Expect the backtest to be underwhelming. A result around or below 1% per bet is
consistent with the published evidence; a result of 15% means there is a bug, most likely
look-ahead.

---

### Week 3 — Football safeguards and going live on paper

| Day | Objective | Deliverable |
|---|---|---|
| **15** | Route B — goal-expectation matrix. Bivariate Poisson with the Dixon–Coles low-score correction. Full scoreline matrix, prices for every market derived from it. | Matrix implemented. Internal consistency check: 1X2 derived from the matrix agrees with over/under derived from the same matrix. |
| **16** | Manager and consensus. Route A and Route B agreement, spread check, xG sanity check integration. | Manager producing BET / NO_BET with every field from §11 populated. |
| **17** | Veto agent. All conditions from `football-build-spec.md` §8, including the cup market restriction and the promoted-team rule enforced in code. | Veto agent running. Every veto logged with a reason. Vetoed picks settled on paper anyway. |
| **18** | Data freshness gates and monitoring. Staleness thresholds, refuse-to-publish behaviour, `data_health` table, alert delivery. | Deliberately stale a feed and demonstrate the run refuses to publish and alerts a human. |
| **19** | CLV and settlement. Closing price capture at kickoff for every fixture on the slate. CLV with the margin division. Automated settlement. | CLV computed for a full historical week. Settlement job running. |
| **20** | Reporting and alerts. Weekly CLV, calibration, volume and drawdown report. Drift alerts, volume alerts, drawdown stop. | Report generating. All four alert types tested by forcing the condition. |
| **21** | **Football goes live on paper.** Scheduler wired to the timetable in §10. Full slate generation, publication, retraction, closing capture. | Football system running unattended on the real schedule, publishing to paper. First real slate produced. |

**End of week 3:** the football system is operating end to end on live data, staking nothing. It
should now run without intervention while attention moves to tennis.

---

### Week 4 — Tennis

| Day | Objective | Deliverable |
|---|---|---|
| **22** | Tennis repository, schema, Sackmann ingest. All ATP and Challenger years. | Schema migrated. All match data ingested with a null-rate report on serve statistics by year and level. |
| **23** | Player canonicalisation and odds join. Sackmann to Tennis-Data name matching, alias table, hard assertions. | Every player resolved. Historical odds joined with a match-rate report. |
| **24** | Agent 1 — surface Elo. Overall and per-surface ratings, Sackmann K-factor, 50/50 blend, shrinkage for thin surface samples. | Elo engine running. Accuracy and Brier score reported against the market as a benchmark. |
| **25** | Agent 2, part one — serve and return numbers. Rolling windows, surface blending, tour averages, the Barnett–Clarke opponent adjustment, the normalisation constraint. | Adjusted serve probabilities produced for every fixture. Distribution sanity-checked against known benchmarks (top-100 ≈ 80% service games won). |
| **26** | Agent 2, part two — the Markov hierarchy. Point to game to tiebreak to set to match, by recursion with memoisation. Format variations encoded per tournament. | Markov model returning match probability. **All tests from §6.8 passing.** Set-score and total-games distributions stored. |
| **27** | Agent 3 — intransitivity, Stage 1. Directed graph construction, weighted edges, depth-2 neighbourhood, cycle density, matchup intransitivity score. | Score computed for every fixture. Distribution plotted. Threshold set at the trailing 90-day median. |
| **28** | Tennis manager and veto agent. Direction, spread, floor and data-sufficiency checks; de-vig; thresholds by segment; all veto conditions including fatigue and turnaround. | Manager producing BET / NO_BET. Veto agent running with logged reasons. |
| **29** | Tennis backtest and calibration. Walk-forward across available seasons, calibration by decile, full configuration log. | Backtest report with configuration count. Reliability diagram. Honest ROI with confidence interval. |
| **30** | **Tennis goes live on paper.** Scheduler wired to the timetable in §11 including the deliberate post-match pause. Handover documentation. | Both systems running unattended on paper. README covering operation, alerts, and how to interpret the weekly report. |

---

### What is deliberately NOT in the first 30 days

Do not start these. They are Phase 2, and starting them early is the most likely way this
schedule slips.

- Tennis derivative markets (total games, set betting) — the Markov model already stores the
  distributions, so the data accumulates while you wait.
- The graph neural network replacement for the intransitivity score.
- Asian handicap markets.
- The cards model. **Do** run passive referee data collection from day three — it costs nothing
  and the data accumulates.
- Any user-facing interface. Reports go to email and the database.
- Live/in-play anything.

---

## 8. Reporting format

Report daily using this structure. Copy it, fill it in, send it.

```
DAY [N] — [DATE]

OBJECTIVE (from plan)
[one line]

DONE
- [deliverable, with a link to the commit or a path to the artefact]
- [deliverable]

NOT DONE
- [anything from today's plan that did not complete, and why]

BLOCKERS
- [blocker] — [how long it cost] — [what would unblock it] — [status: open/resolved]

CONFIGURATIONS TESTED TODAY
- [running total, and what was tried]

NEXT
[tomorrow's objective]
```

**Notes on how this gets read:**

The blockers column is not a confession, it is the point. Estimates for unfamiliar work are
wrong more often than they are right, and this build has real unknowns in it — API behaviour,
data quality in the lower divisions, name-matching edge cases. A day lost to a genuinely nasty
bug is information. A developer who logs problems honestly is one worth trusting; one who is
always mysteriously exactly on schedule is the one to worry about.

The plan is a schedule, not a scoreboard. Where it slips, the useful question is *what
specifically blocked it*, not *why are you behind*.

The configuration count is not optional and it is not busywork. It is the number that determines
whether the eventual backtest means anything at all.

---

## 9. What "finished" looks like on day 30

Not a profitable system. A **measurable** one.

By day 30 you should be able to answer, with data:

1. How many fixtures were on the slate, how many produced bets, and why the rest did not.
2. Whether the models are calibrated — does a 60% forecast happen 60% of the time.
3. What the closing line value has been across all picks, bets and no-bets alike.
4. How many model configurations were tried before arriving at the current one.
5. What the backtest ROI was, **with a confidence interval**, and over how many bets.
6. What happens when a data feed goes stale (answer: nothing gets published and somebody is
   emailed).

If those six questions have clean answers, the build is a success regardless of whether the
first month's picks won.

### Expectations for the months after

- **Minimum 300–500 bets** before any backtest number means anything. At a 100-bet sample,
  roughly 40% of genuinely losing bettors are showing a profit.
- **A genuine 3% edge at even money needs roughly 3,000 bets** to establish at 95% confidence; a
  5% edge needs about 1,080. Longer prices need far more — a 5% edge at odds of 10.00 needs
  around 10,000 bets.
- Judge early picks on **closing line value and calibration**, not on whether they won. Outcome
  variance is roughly 75 times CLV variance, which is why CLV resolves in 50–100 bets what
  profit takes thousands to show.
- **Long losing runs are expected and are not evidence of a broken model.** A documented working
  model with 6,562 picks and a genuine 3.79% yield still produced 1,000-bet windows that looked
  like 1-in-40 failures. Build the reporting so that a bad month does not look like a
  catastrophe, because it will happen and it will not mean what it feels like it means.

---

## 10. The honest summary, for whoever is paying for this

Both systems are built on the best publicly available evidence, and that evidence is thin.

The football edge is documented at **0.8% per bet** over 68,672 bets and twelve years — real,
validated against a random-betting control, but small, and probably eroded since publication in
2020. The tennis side is weaker still: the best-known profitable tennis model failed replication
when its headline profit turned out to rest on a single outlier bet, and the strongest surviving
tennis result (+3.26% on intransitive matchups) is recent and unreplicated.

The most reliably measured edge in this entire field is **structural, not sporting**: taking the
best available price where it exceeds a sharp bookmaker's de-vigged fair odds returned +3.40%
over 22,281 bets, against a control that returned −3.08% over 44,673 bets when deliberately
betting below fair price. That is roughly four times the modelling edge, and it is the reason
the price-scanning layer matters more than any model refinement.

None of that makes the build a bad idea. It makes it a build that has to be **measured honestly
from day one**, which is what the calibration tracking, the CLV logging, the no-bet records and
the configuration counting are all for. The system is designed to tell you the truth about
itself within a few months rather than a few years.

That is the deliverable. Not certainty — measurability.
