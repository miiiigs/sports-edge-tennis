# Tennis Build Specification

**Version 1.0 — 15 August 2026**
**Audience: the developer. Assumes strong software skills, no sports knowledge.**

> This is a standalone build. It shares no code, no database and no configuration with the
> football build. Two repositories, two databases, two deployments.

---

## 0. Read this first: what you are actually building

You are **not** building something that predicts who wins tennis matches. Models that do that
already exist, they are good, and they are not good enough — the published academic work
consistently finds that a well-built tennis model reaches roughly 70% accuracy while the
bookmaker's price implies roughly 77%. If your success metric is "did we pick the winner", this
project fails on day one.

What you are building is a **price comparison engine with a sporting model inside it.**

The logic is:

1. Estimate the probability that a player wins.
2. Convert that probability into a "fair" price.
3. Take the bookmaker's price and strip out the bookmaker's built-in profit margin, giving the
   market's fair price.
4. Compare the two.
5. If our fair price is meaningfully longer than the market's — and only then — we have a bet.

A bet is good if and only if `p × o > 1`, where `p` is the true probability and `o` is the
decimal odds. The probability enters **only** through that inequality. It is entirely normal and
correct for this system to recommend a player we think will probably lose, because the price is
wrong by more than the probability is unfavourable.

Print that on the wall. Every design decision below follows from it.

### Glossary (skip if you know it)

| Term | Meaning |
|---|---|
| **Decimal odds (`o`)** | Total return per unit staked, including the stake. 2.50 means £1 returns £2.50. |
| **Implied probability** | `1/o`. Odds of 2.50 imply 40%. |
| **Fair odds** | `1/p`. A 40% chance has fair odds of 2.50. |
| **Overround / vig / margin** | Bookmakers price so implied probabilities sum to more than 100%. The excess is their margin. |
| **Booksum `S`** | `Σ(1/oᵢ)` across all outcomes. `S = 1.05` means a 5% overround. |
| **De-vig** | Removing the margin to recover the market's actual probability estimate. |
| **Sharp book** | A bookmaker with a very low margin that welcomes winning customers. Pinnacle is the reference. |
| **Soft book** | A high-margin bookmaker that restricts winners. Most UK high-street brands. |
| **Closing line** | The final price immediately before the match starts. Empirically the best available estimate of true probability. |
| **CLV** | Closing Line Value. Whether the price we took was better than the closing price. Our primary success metric. |
| **Edge** | `(our_p × market_odds) − 1`, expressed as a percentage. |
| **ATP** | The men's professional tour. |
| **Challenger** | The tier below the main tour. Same players earlier in their careers, thinner markets. |
| **Hold / break** | Winning / losing a service game. |
| **SPW / RPW** | Serve points won %, return points won %. |

---

## 1. Scope

**In scope:** Men's ATP Tour (Grand Slams, Masters 1000, ATP 500, ATP 250), and ATP Challenger
Tour. Singles only.

**Out of scope:** WTA and all women's tennis. Doubles. ITF Futures. Juniors. In-play betting.

**Rationale for Challenger inclusion:** the same players circulate repeatedly on the same
circuit, so the common-opponent structure our model needs is unusually dense. The trade-off is
smaller betting limits and faster-moving prices. Challengers therefore run with a **wider edge
threshold** (see §6.5).

**Markets:** match winner is the primary market. Total games and set betting are Phase 2 — the
Markov model prices them natively, and derivative markets receive less attention from
bookmakers than the match winner. Do not build them in the first 30 days.

---

## 2. Data sources

### 2.1 Jeff Sackmann — `tennis_atp` (free, no key)

`https://github.com/JeffSackmann/tennis_atp`

The backbone of the entire build. CSV files, one per year, going back decades. Includes ATP
tour-level and Challenger-level matches.

Columns you will care about:

| Column | Meaning |
|---|---|
| `tourney_date`, `tourney_name`, `tourney_level` | `G`=Grand Slam, `M`=Masters, `A`=ATP, `C`=Challenger |
| `surface` | Hard / Clay / Grass / Carpet |
| `winner_id`, `loser_id`, `winner_name`, `loser_name` | Player identity |
| `best_of` | 3 or 5 |
| `score` | Match score string |
| `minutes` | Match duration |
| `w_svpt`, `w_1stWon`, `w_2ndWon` | Winner's serve points, 1st-serve points won, 2nd-serve points won |
| `l_svpt`, `l_1stWon`, `l_2ndWon` | Same for loser |
| `w_ace`, `w_df`, `w_bpSaved`, `w_bpFaced` | Detail stats |

**Serve points won** = `(1stWon + 2ndWon) / svpt`. **Return points won** for a player is
`1 − (opponent's serve points won)` in that match. These two numbers are all the Markov model
needs, and critically **they are populated at Challenger level**, which point-by-point data is
not.

Clone the repo and pull updates nightly. Do not scrape the website.

**Known issue:** serve stats are missing for a minority of matches, especially older Challengers
and some qualifying rounds. Handle nulls explicitly — never impute silently. A player whose
recent matches all have null serve stats must fall back to their longer-run average, and the
veto agent (§7) should flag low-data players.

### 2.2 Tennis-Data.co.uk (free, no key)

`http://www.tennis-data.co.uk/alldata.php`

Historical odds by year, ATP level, multiple bookmakers plus max/average columns. This is your
backtest odds source. It does **not** cover Challengers reliably — accept that Challenger
backtesting will be thin and treat Challenger live results as the real test.

Join to Sackmann on `(date, player names, tournament)`. **Player-name matching is the single
biggest data-engineering headache in this build.** Sackmann uses "Novak Djokovic"; Tennis-Data
uses "Djokovic N.". Build a canonical player table with an alias list, resolve by fuzzy match
once, store the mapping, and **assert on unmatched names rather than dropping them silently.**
Silently dropped matches bias a backtest in ways you will not detect.

### 2.3 Live odds feed (paid, API key required)

Options, in preference order:

- **The Odds API** (`the-odds-api.com`) — cheapest entry point, simple REST, tennis coverage,
  free tier for development. Start here.
- **OddsJam** or equivalent — more books, faster refresh, includes sharp books. Upgrade to this
  once the pipeline works.
- **Betfair Exchange API** — free but requires a funded account and an application key. The
  exchange price is an excellent fair-value reference because the exchange has no traditional
  margin, only commission.

**Critical:** you need a sharp reference price. Pinnacle is the standard benchmark, but Pinnacle
does not accept UK customers, so you cannot simply read it off their website — you must obtain
their prices through a data provider that carries them, or use the Betfair Exchange as your
sharp reference instead. **Decide this before writing the pricing module**, because the entire
de-vig and CLV layer depends on it. If neither is available, the project's measurement layer
does not work and you should raise it immediately rather than substituting a soft book.

### 2.4 Live schedule, withdrawals and news

- Order of play and draws: ATP Tour site, plus your odds provider's fixture list.
- Withdrawals and retirements: these change hourly. The veto agent needs a live source; your
  odds feed disappearing a match is itself a signal.

---

## 3. Database

Postgres. Not spreadsheets, not CSV-on-disk. You will run thousands of backtests and you need
transactional integrity and indexed joins.

Minimum tables:

```
players            (player_id, canonical_name, aliases[], hand, height, dob, country)
matches            (match_id, date, tourney_id, level, surface, best_of,
                    p1_id, p2_id, winner_id, score, minutes,
                    p1_svpt, p1_sv_won, p2_svpt, p2_sv_won, retired_flag)
odds_snapshots     (snapshot_id, match_id, book, market, selection,
                    decimal_odds, captured_at, is_closing)
ratings            (player_id, as_of_date, surface, elo_overall, elo_surface,
                    spw_adj, rpw_adj, n_matches, model_version)
predictions        (prediction_id, match_id, model_version, agent_name,
                    p_win, fair_odds, generated_at)
decisions          (decision_id, match_id, model_version, decision,      -- BET | NO_BET | VETOED
                    consensus_p, fair_odds, market_odds, book,
                    devig_method, devig_fair_odds, edge_pct,
                    min_acceptable_odds, stake_pct, veto_reason,
                    generated_at, published_at)
results            (decision_id, outcome, closing_odds, closing_margin,
                    clv_pct, pnl_units, settled_at)
data_health        (source, last_successful_pull, rows_ingested, status, checked_at)
```

Three rules about this schema:

1. **`decisions` records NO_BET rows.** Most systems only log bets. If you only log bets you can
   never answer "is the model too tight or too loose", and you cannot reconstruct why a match
   was skipped.
2. **Every row carries `model_version`.** Model code, feature code and calibration are versioned
   together as one bundle. When results change in month three you must be able to say which
   version produced which pick.
3. **`odds_snapshots` is append-only with `captured_at`.** Never overwrite a price. The whole
   backtest depends on knowing what was available *at the time*, and overwriting introduces
   look-ahead bias that is invisible afterwards.

---

## 4. Agent 1 — Surface-Adjusted Elo

### What Elo is

Elo is a rating system where every player has a number. Before a match you compute an expected
score from the rating difference; after the match you move both ratings toward the actual
result. Winners take points from losers, and beating a strong player moves you more than beating
a weak one.

### Formulas

Expected probability that player A beats player B:

```
E_A = 1 / (1 + 10^((R_B - R_A) / 400))
```

Update after the match:

```
R_A_new = R_A + K × (S_A - E_A)      where S_A = 1 if A won, 0 if A lost
R_B_new = R_B + K × (S_B - E_B)
```

**K-factor.** Do not use a constant. Sackmann's approach scales K down as a player accumulates
matches, so new players move fast and established players move slowly:

```
K_i = 250 / (m_i + 5)^0.4          where m_i = number of matches player i has played
```

**Initialisation.** New players start at 1500. Around 2000 separates the elite.

### The surface blend

Maintain **two** rating series per player:

- `elo_overall` — updated on every match regardless of surface.
- `elo_surface` — separate ratings for Hard, Clay, Grass, each updated only on matches played on
  that surface.

The rating used for prediction is a **50/50 blend**:

```
R_blend = 0.5 × elo_overall + 0.5 × elo_surface(match_surface)
```

Sackmann tested alternative weightings and found 50/50 worked across every surface. **Do not
tune this weight on your backtest.** It is a published, externally validated choice, and tuning
it is exactly the kind of free parameter that manufactures a beautiful backtest and a losing
live model.

If a player has fewer than 10 matches on the surface, shrink toward overall:

```
w = min(n_surface, 10) / 20
R_blend = (0.5 + (0.5 - w)) × elo_overall + w × elo_surface
```

### Output

`p_elo` = probability player 1 wins, from the blended ratings.

### Honest limitation

Elo beats the ATP ranking comfortably, but it does not beat the market. Sackmann's own published
comparison puts his Tennis Abstract ratings at a Brier score of .182 against Pinnacle's .172 —
the market is better. Agent 1 is here to be a competent, independent baseline, not to find the
edge on its own.

---

## 5. Agent 2 — Hierarchical Markov (Barnett–Clarke)

### The idea in plain English

A tennis match is a tree. Points make games, games make sets, sets make matches. If you know the
probability each player wins a point on their own serve, you can compute — exactly, with no
simulation — the probability of winning the match, and simultaneously the probability of every
scoreline, total games line and set betting outcome.

The scoring structure is violently non-linear: a two-percentage-point serve advantage compounds
enormously by the time it reaches match level. This is why the model can be genuinely
informative about *derivative* markets even when it is only average at picking winners.

### Step 1 — raw serve and return numbers

For each player, over a rolling window (recommend 12 months, minimum 20 matches, surface-weighted
as below):

```
spw_i = Σ(1st_won + 2nd_won) / Σ(svpt)          -- serve points won
rpw_i = 1 - (Σ opponent 1st_won + 2nd_won) / Σ(opponent svpt)   -- return points won
```

Compute the same numbers **surface by surface**, and blend surface-specific with overall using
the same shrinkage logic as Elo (thin surface samples pull toward the overall figure).

Also compute tour-wide averages for the same window and surface:

```
spw_av , rpw_av        (note: spw_av + rpw_av = 1 by construction)
```

### Step 2 — the opponent adjustment

This is the load-bearing step, and it is one line of arithmetic. A player's raw serve number is
contaminated by who they happened to play. Barnett and Clarke's correction deducts from a
player's serve figure the amount by which their opponent's return ability exceeds the field
average:

```
f_ij = spw_i - (rpw_j - rpw_av)
```

Read: *"player i's expected serve-point win probability against player j"*. Symmetrically:

```
f_ji = spw_j - (rpw_i - rpw_av)
```

### Step 3 — the normalisation constraint

Left alone, the above can produce nonsense — both players winning 90% on serve, or a combined
figure far outside anything tennis produces. Impose:

```
f_ij + f_ji = 2 × spw_av(surface)
```

by scaling both values proportionally to hit that total while preserving their difference. This
is the constraint used in the published win-probability literature and it prevents the model
producing physically implausible inputs.

### Step 4 — point to game

**Implement by recursion, not by closed-form algebra.** Closed forms for tennis exist and they
are easy to get subtly wrong; recursion with memoisation is transparent, testable and fast
enough.

Let `p` = server's probability of winning a point.

```
P_game(a, b):                    # a, b = points won by server, receiver
    if a >= 4 and a - b >= 2: return 1.0
    if b >= 4 and b - a >= 2: return 0.0
    if a >= 3 and b >= 3:
        # deuce: collapse to a closed form
        return p*p / (p*p + (1-p)*(1-p))
    return p * P_game(a+1, b) + (1-p) * P_game(a, b+1)

P_hold = P_game(0, 0)
```

The deuce term is the standard geometric-series result: from deuce you must win two consecutive
points before your opponent does.

### Step 5 — tiebreak

Same recursion, but the server alternates on the 1-2-2 pattern, so state must include who is
serving. Points 1 serves; then two each thereafter. First to 7 with a margin of 2, then
continuing win-by-two.

```
P_tb(a, b, server):
    if a >= 7 and a - b >= 2: return 1.0
    if b >= 7 and b - a >= 2: return 0.0
    if a >= 6 and b >= 6:
        # win-by-two from 6-6 equivalent: use the two-point cycle closed form
        ...
    p = f_ij if server == 1 else (1 - f_ji)
    return p * P_tb(a+1, b, next_server(a+b)) + (1-p) * P_tb(a, b+1, next_server(a+b))
```

Write a unit test: with `f_ij = f_ji = 0.5`, `P_tb` must return exactly 0.5. With
`f_ij = f_ji = 0.65` (both hold easily), tiebreak probability must be 0.5 but hold probability
must be high — that pair of assertions catches most implementation errors.

### Step 6 — set and match

Set: recursion over games with alternating server, first to 6 with a margin of 2, 6-6 goes to
tiebreak. Handle the final-set format variation (most events now play a 10-point tiebreak at
6-6; Grand Slams vary — encode this per tournament, do not hardcode).

Match: recursion over sets, best of 3 or 5 per `best_of`.

### Output

`p_markov` = probability player 1 wins the match. The same object also gives you, free, the full
distribution over set scores and total games. Store it — Phase 2 depends on it.

### Known violations, stated honestly

Points are not truly independent and identically distributed. Klaassen and Magnus documented
this. The deviations are small, which is why the i.i.d. model survives as the working standard
despite being known to be false. Do not attempt to fix this in version one.

---

## 6. Agent 3 — Intransitivity Filter

### The idea

Tennis is not transitive. A beats B, B beats C, C beats A. This happens because of stylistic
matchups — a big server troubles a passive returner who troubles a grinder who troubles the big
server. Ratings systems like Elo assume transitivity by construction: they compress everything
onto a single number line, which cannot represent a cycle.

The 2025 paper *Intransitive Player Dominance and Market Inefficiency in Tennis Forecasting: A
Graph Neural Network Approach* (arXiv 2510.20454) found that:

- Applied to **all** matches indiscriminately, the approach returned **−5.49%**.
- Applied **only to matchups with high measured intransitivity**, it returned **+3.26% ROI with
  Kelly staking over 1,903 bets**, at 65.7% accuracy and a 0.215 Brier score.

The lesson is the filter, not the model. **This agent's job is to identify which matches the
other two agents are structurally likely to be wrong about.**

### Build it in two stages

**Stage 1 (build this first — days 1–30).** A classical cycle-density score, no neural network.

1. Build a directed graph. Nodes = players. For each match, add a weighted edge from loser to
   winner. Weight recent matches more heavily and same-surface matches more heavily.
2. For the two players in an upcoming match, take the subgraph induced by their combined
   neighbourhood at depth 2 (opponents, and opponents of opponents).
3. Count directed 3-cycles within that subgraph. Normalise by the number of possible triangles
   to get a **cycle density** in [0, 1].
4. Compute an **intransitivity score** for the specific matchup: the extent to which the graph's
   local structure contradicts the Elo ordering. A simple, defensible version: the proportion of
   depth-2 paths from player A to player B that point the opposite way to the Elo prediction.

**Stage 2 (Phase 2, not in the first 30 days).** Replace the hand-built score with the GNN from
the paper. Do not attempt this until Stage 1 is running, logged, and you know what the score
distribution looks like on your own data.

### How the score is used

**This agent does not output a winner.** It outputs a number in [0, 1] with two jobs:

1. **Gate.** Below a threshold (start at the median of the trailing 90-day distribution — i.e.
   the more intransitive half of matches), the manager requires a wider edge before firing.
2. **Weight.** Above the threshold, its own directional read gets a vote in the consensus.

Below the threshold it abstains rather than voting, because on transitive matchups it has no
information that Elo does not already have.

### Honest caveat

This paper is recent (October 2025) and **has not been independently replicated.** That matters,
because the previously best-evidenced tennis result — the Knottenbelt common-opponent model,
published at +3.8% ROI — collapsed under replication: the profit traced to a single outlier bet
on erroneously long odds, and the replicators concluded the residual returns came from
exploiting individual bookmakers' mispricing relative to the market, i.e. line shopping, not
from the tennis model. Treat +3.26% as an unconfirmed upper bound, not a forecast.

---

## 7. The Manager

The manager takes three probabilities and one market price and produces BET or NO_BET. It votes
on **price gaps, not on winners.** Three agents agreeing with the bookmaker is not a signal —
it is a fair-odds bet and a slow loss.

### 7.1 Consensus checks (all must pass)

| Check | Rule | Reason |
|---|---|---|
| **Direction** | All active agents must favour the same player | Disagreement on direction means we do not understand the match |
| **Spread** | `max(p) − min(p) ≤ 0.08` | Wide spread means the models disagree about magnitude; that uncertainty is bigger than our edge |
| **Floor** | No agent may have the selection below 0.50 | A single dissenting agent below evens kills the bet |
| **Data sufficiency** | Both players ≥ 20 matches in the rolling window with non-null serve stats | Thin data produces confident nonsense |

Note that Agent 3 abstains on low-intransitivity matches. When it abstains, the consensus is
Agents 1 and 2, and the edge threshold widens by one percentage point.

Consensus probability: **the mean of the active agents' probabilities.** Do not weight by
backtest performance in version one — that is fitting to noise.

### 7.2 De-vigging the market price

Take the two-way match-winner market from your sharp reference (Pinnacle via a provider, or
Betfair Exchange).

```
π_i = 1 / o_i                    for each of the two outcomes
S   = Σ π_i                      the booksum; S − 1 is the overround
```

**Use the power method as default.** Find the exponent `k` such that:

```
Σ (π_i)^k = 1
```

Solve numerically — Brent's method on `k ∈ [0.5, 1.5]` converges in milliseconds. Then
`p_market_i = (π_i)^k`.

**Do not use naive proportional de-vig** (`p_i = π_i / S`). On a balanced two-way market the
difference is trivial, but as prices lengthen the methods diverge enormously — on a
1.25 / 4.50 / 15.00 book the implied fair price of the longshot ranges from 16.33 to 27.00
depending on method, a 65% spread, against 5.5% for the favourite. Naive de-vig leaves longshot
probabilities too high and their fair prices too short, which **manufactures false-positive
value precisely where the bookmaker's real margin is hiding.** For anything priced above 4.00,
power or Shin only, and record which method was used in `decisions.devig_method`.

Shin's method is available as an alternative; it solves for an insider-trading parameter `z`:

```
p_i = [ sqrt(z² + 4(1−z) · π_i² / S) − z ] / (2(1−z))
```

with `z` found numerically such that `Σ p_i = 1`. Implement both, default to power, make the
choice a config flag.

### 7.3 Edge calculation

```
fair_odds_ours  = 1 / consensus_p
edge_pct        = (consensus_p × best_available_market_odds) − 1
```

Note that `best_available_market_odds` is the **best price we can actually get across all books
we monitor** — not the sharp price. The sharp price is used to establish what fair is; the soft
price is what we bet into. That gap is where the measurable money in this whole field lives.

### 7.4 Minimum acceptable price

For every BET, compute and store:

```
min_acceptable_odds = 1 / (consensus_p / (1 + threshold))
```

i.e. the price at which the edge falls to the threshold. If the price drifts below this between
generation and publication, the bet is dead and must not be published. Without this rule you
will publish picks at prices that no longer contain any edge.

### 7.5 Thresholds

| Segment | Minimum edge after de-vig |
|---|---|
| ATP Tour, all three agents active | **5%** |
| ATP Tour, Agent 3 abstaining | **6%** |
| ATP Challenger | **8%** |

**Why 5% and not 3%.** A well-regarded public tennis tipster was analysed as beating Pinnacle's
closing line by roughly 3% on average — which, once Pinnacle's own margin is removed, leaves
approximately break-even. A 3% threshold is therefore not a threshold at all. 5% is the floor at
which the gap survives the margin.

**Why Challenger is 8%.** Wider bookmaker margins, thinner data, faster line movement, lower
limits. The commonly repeated claim that "Challengers are under-modelled" has, on inspection, no
published win rate, ROI or CLV study behind it from any source — and it is internally
contradictory, since the same thinness that softens lines produces low limits and fast movement.
We include Challengers because the common-opponent structure is genuinely dense there, not
because we believe the folklore.

---

## 8. The Veto Agent

**The veto agent can only kill a bet. It can never create one.** This is a hard architectural
constraint. Do not let it become a fourth vote.

The reasoning: general tennis knowledge — height, nationality, ranking, serve reputation, recent
results — is already fully contained in the sharp price. Adding it as a vote adds noise. What is
*not* reliably in the price at the moment we generate a pick is **late-breaking, non-public or
fast-moving information.** That is the veto agent's entire remit.

### Veto conditions

| Condition | Action |
|---|---|
| Withdrawal, retirement or walkover announced | VETO |
| Injury reported in the last 72 hours for either player | VETO |
| Either player retired or received a medical timeout in their previous match | VETO |
| First match back after an absence of 6+ weeks | VETO |
| Previous match ended within 18 hours of scheduled start | VETO |
| Previous match exceeded 3 hours 15 minutes, or went five sets, with under 24 hours' turnaround | VETO |
| Player has fewer than 20 valid matches in the rolling window | VETO |
| Odds have moved more than 15% against our selection since generation | VETO — the market knows something |
| Any data source stale (see §10.1) | VETO all picks |

Every veto is logged with `veto_reason` in `decisions`. Over time this log tells you whether the
veto agent is earning its place or just costing you bets — and you cannot answer that question
unless you record the vetoed picks and settle them anyway on paper.

### On settlement rules

Bookmakers differ on how they settle when a player retires mid-match. Some void, some settle to
the leader, some settle as a loss. Handicaps and totals generally void if the match is
incomplete. **This is out of scope for the build** — we publish tips, we do not place the stakes
— but the developer should be aware that the same nominal price at two books is not the same
bet, and it is a real consideration if the operation ever stakes its own money.

---

## 9. Staking

```
f* = (p × o − 1) / (o − 1)          # full Kelly fraction
stake = min(0.5 × f*, 0.02)         # half Kelly, hard capped at 2% of bankroll
```

**Why half Kelly.** Full Kelly is optimal only if `p` is *known*. Ours is estimated, and
estimation error is punishing. At a true 2.5% edge, simulations show: correctly sized, a 34%
chance of ending in loss; believing the edge is 5% when it is 2.5%, ahead only half the time
with an 8% chance of a 90% drawdown; overestimating threefold, more likely to lose (66%) than
profit (34%) — **with a genuine, real edge.** Half Kelly raises profit probability from 66% to
73% and cuts 50%-drawdown risk from 10% to 1%, at the cost of about 30% of growth. That is an
excellent trade under parameter uncertainty.

**No accumulators.** Margin compounds multiplicatively: five legs at a 5% hold is a 22.6% hold;
ten legs is 40.1%. The system must not generate them.

---

## 10. Safeguards

These are not optional extras. Build them in the first two weeks, not the last.

### 10.1 Data freshness gate

Before any pick is generated, assert:

| Source | Maximum staleness |
|---|---|
| Odds feed | 15 minutes |
| Sackmann match data | 48 hours |
| Ratings table rebuild | 24 hours |
| Fixture/schedule feed | 6 hours |

If any check fails, the run **refuses to publish** and raises an alert. Fail loudly. The failure
mode this prevents — quietly pricing matches off yesterday's ratings and last night's odds —
produces confident, plausible, wrong picks that nobody notices for weeks.

### 10.2 Calibration tracking

This is more important than CLV in the first month, and it is the check most systems skip.

Bin every forecast into deciles (0–10%, 10–20%, … 90–100%). For each bin, plot predicted
probability against observed frequency. When the model says 60%, does it happen 60% of the time?

If calibration is off, **every downstream edge calculation is wrong.** You would be comparing a
bad probability to a good price and calling the difference value. It looks fine on paper and
bleeds money. Calibration is checkable on *all* matches, including no-bets, so it accumulates
sample far faster than P&L.

Report Brier score and a reliability diagram weekly. Recalibrate (Platt scaling or isotonic
regression on a held-out period) if systematic bias appears — and version the calibration
alongside the model.

### 10.3 CLV logging

For every decision, including no-bets, capture the closing price at match start.

```
clv = bet_odds / closing_odds / (1 + closing_margin) − 1
```

**Note the division by the closing margin.** Beating the close by less than the closing margin
is not an edge at all. This step is omitted from almost every popular explanation of CLV, and it
is why "I beat the close 60% of the time" is entirely consistent with losing money.

CLV is the primary success metric because outcome variance is roughly 75× CLV variance — what
takes thousands of bets to establish from profit and loss can take 50–100 bets from line
movement. It also persists: across 1,525 tracked bettors, first-half CLV explained about half
the variance in second-half CLV, where raw profit showed complete mean reversion.

Two honest limits, which must be stated in any reporting: an originator with a genuinely novel
model may move nothing and show no CLV (false negative), and latency arbitrage or
steam-following produces impeccable CLV with zero forecasting skill (false positive).

### 10.4 Monitoring and alerts

- **CLV drift alert:** if rolling 14-day mean CLV is negative, email immediately.
- **Volume alert:** if bets fired per week deviates more than 50% from the trailing 8-week mean,
  alert. A sudden spike usually means a data bug, not an opportunity.
- **Calibration alert:** if any decile's observed frequency sits more than 10 points from
  predicted over 100+ forecasts, alert.
- **Drawdown stop:** at a pre-agreed drawdown level (recommend 20% of notional bankroll),
  **everything pauses automatically** for review. Not because the model is necessarily broken —
  a working model with 6,562 picks and a genuine 3.79% yield still threw off 1,000-bet windows
  that looked like 1-in-40 failures — but because without a hard stop you will keep going on
  hope.

### 10.5 Validation discipline

- **Walk-forward only.** Train on years 1–N, test on year N+1, roll forward. Never fit and test
  on the same period.
- **Chronological ordering.** Simulate bets in date order. Never use information — including
  closing odds — that did not exist before the match started.
- **Minimum 300–500 bets** in any backtest before the result means anything. At a 100-bet
  sample, roughly 40% of genuinely losing bettors are showing a profit.
- **Count your configurations.** Every model variant tested must be recorded. Testing just 10
  strategy variants yields an expected in-sample Sharpe of 1.57 with **zero true skill**. A
  backtest result reported without the number of configurations tried is not evidence.
- **Paper-trading mode from day one.** Every pick logged and settled with no money staked. The
  paper log and the live log use the same code path, with a single flag.

---

## 11. Scheduling

All times UK. Tournaments in Asia and Australasia require working backwards from local first
serve — encode tournament timezone, do not assume.

| Time | Job |
|---|---|
| **22:00** | Ingest the day's completed results. Rebuild ratings. Recompute the graph. |
| **22:30** | Deliberate pause — the previous day's matches must be fully settled and their stats ingested before anything is priced. Do not price a match minutes after the previous one finished. |
| **23:00** | Generate probabilities for tomorrow's full slate. |
| **23:30** | Pull current prices across all monitored books. Run the manager. Run the veto agent. |
| **00:00** | Publish. Every pick carries its `min_acceptable_odds`. |
| **07:00** | Morning withdrawal and injury sweep. Publish any retractions. |
| **12:00** | Second sweep for matches starting after 16:00. |
| **Match start** | Capture closing price for every match on the slate, bet or no-bet. |

### The fixed slate rule

**Every scheduled singles match in scope goes onto the slate, every day, without exception.**

The model does not choose which matches to examine. If it did, the selection step would be doing
work you cannot audit, and you would never know whether the edge came from the model or from
which games it happened to look at. Feed it everything, let it decline most of them.

**Days with zero picks are a correct output, not a failure.** The system must be able to publish
"nothing today". If that is not designed in from the start, the pressure to post something will
quietly destroy the discipline that makes the whole thing work.

---

## 12. What gets recorded with every pick

Store all of it, whether or not it is published:

1. The sporting assessment.
2. Estimated probability, **with an interval** — not a point estimate.
3. The fair price implied by it.
4. The market price, the book, and the de-vig method named.
5. The edge as a number.
6. A **named mechanism** for why our number should beat the closing line. If none can be named,
   the answer is NO BET.
7. What would make us wrong, and what the market might know that we do not.
8. BET or NO BET, with the stake as a function of edge.

Point 6 is the one that does the work. "Our model says 57%" is not a mechanism — the literature
says our model is not better than the close. The mechanism has to be specific: a market segment,
a market type, or an information type the closing price genuinely does not contain.

**Banned language anywhere in the system's output:** banker, lock, guaranteed, certainty, free
money, can't lose, max play, best bet of the week.

**Banned as primary arguments:** head-to-head records (small-n, selection-biased, stale, and
already priced), ranking gaps, "unbeaten in six", streaks, "due a win", break-point conversion
rates, and any publicly available easily computed statistic. If you can find it in five seconds,
so did the market maker.

---

## 13. Honest statement of limitations

Give this section to anyone who asks what the system is worth.

1. **No tennis model in the published literature reliably beats the closing line.** Wilkens
   tested six model families across ~39,000 matches: all reached about 70% accuracy, matching
   bookmaker odds alone, with "hardly any room for consistent positive returns."

2. **The best-known profitable tennis result failed replication.** The Knottenbelt
   common-opponent model published +3.8% ROI over 2,173 ATP matches. On re-examination, the
   profit traced to a single outlier bet exploiting erroneously long odds in the test data. Once
   cleaned, the strategy was no longer profitable in practical scenarios, and the replicators
   concluded the residual returns came from exploiting individual bookmakers' mispricing
   relative to the market — line shopping — rather than from the tennis model.

3. **The intransitivity result (+3.26%) is unreplicated and recent.** Treat it as a hypothesis
   the system is testing, not a return it is expected to deliver.

4. **The most reliably measured edge in this entire field is structural, not sporting.** Taking
   the best available soft-book price where it exceeds a sharp book's de-vigged fair odds
   returned +3.40% over 22,281 bets, rising to +13.22% where the edge exceeded 5% (n=1,927),
   with a control betting deliberately *below* fair price returning −3.08% over 44,673 bets.
   That is the price-scanning layer in §7.3, and it is likely to contribute more than the three
   agents combined.

5. **Capacity, not edge, is the binding constraint.** UK Gambling Commission 2024 figures:
   643,779 accounts (4.31%) commercially restricted, and 46.78% of restricted customers were in
   profit against 25.42% of all customers. Roughly 60% of stake-factored accounts were capped at
   10% or less of their desired stake.

6. **The honest expected return is low single digits at best, and possibly zero.** Build it,
   measure it properly, and let the CLV log tell you the truth within a few months.

---

## 14. References

- Sackmann, J. — `tennis_atp` data repository and Tennis Abstract ratings methodology.
- Barnett, T. & Clarke, S. (2005) — *Combining player statistics to predict outcomes of tennis
  matches*. IMA Journal of Management Mathematics. Source of the opponent adjustment.
- Barnett, T., Brown, A. & Clarke, S. — *Developing a model that reflects outcomes of tennis
  matches*. Markov chain hierarchy. Free PDF: `strategicgames.tripod.com/8mcs.pdf`
- O'Malley, A.J. (2008) — *Probability formulas and statistical analysis in tennis*. Alternative
  hierarchy formulation.
- Klaassen, F. & Magnus, J. — on the failure of the i.i.d. assumption.
- Knottenbelt, W., Spanias, D. & Madurska, A. (2012) — *A common-opponent stochastic model for
  predicting the outcome of professional tennis matches*. Computers and Mathematics with
  Applications. **Read alongside the replication study that overturns its headline result.**
- Madurska, A. (2012) — *A set-by-set analysis method for predicting the outcome of professional
  tennis matches*. Imperial College MEng thesis. Free PDF, includes implementation detail:
  `doc.ic.ac.uk/teaching/distinguished-projects/2012/a.madurska.pdf`
- University of Glasgow MSci project — *Predicting the Outcome of Tennis Matches From
  Point-by-Point Data*. Free PDF with worked recursion:
  `dcs.gla.ac.uk/~srogers/files/projects/MSci_project_1006404b.pdf`
- *Intransitive Player Dominance and Market Inefficiency in Tennis Forecasting: A Graph Neural
  Network Approach* (2025) — arXiv:2510.20454. Free.
- Wilkens, S. (2021) — *Sports prediction and betting models in the machine learning age: The
  case of tennis*. Journal of Sports Analytics.
- Clarke, S. — comparative study of de-vig methods.
- Buchdahl, J. — closing line value methodology and the margin-arbitrage results.
