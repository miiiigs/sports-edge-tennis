# Model Evidence Diagnostics

This lane exists to explain the gap between historical model validation and prospective live
betting without weakening production selection rules.

## Safety boundary

The diagnostics are read-only. They do not change:

- Agent 1, Agent 2, or Agent 3 production configuration
- Manager C1-C4 consensus rules
- Type A or Type B edge thresholds
- Veto logic
- quarter-Kelly sizing
- provider polling
- settlement or append-only evidence

Agent 2 recency work is shadow-only until a leakage-safe walk-forward comparison demonstrates
an improvement.

## Live evidence endpoint

The production dashboard composition exposes:

```text
GET /api/dashboard/model-evidence
```

It reports:

1. Latest completed prediction-run funnel: targets, Manager consensus, Manager NO_BET, Type A BET.
2. Exact Manager rejection reasons for that run.
3. Exact Type A market reasons for that run.
4. Unique first-BET performance, where each sporting match can enter the sample once.
5. Unique-match live prediction coverage and accuracy for each predictive agent.
6. Agent 3 shadow cohorts for matches where Agent 1 and Agent 2 agree:
   - `A3_ABSTAINS`
   - `A3_ACTIVE_AGREES`
   - `A3_ACTIVE_DISAGREES`

The Agent 3 cohorts are descriptive. They do not authorize replacing the canonical Tennis
Manager with 2-of-3 voting.

## Why unique first-BET evidence matters

The live runner evaluates the same scheduled match repeatedly. Raw decision-row counts are
therefore not sporting-event sample sizes. Client performance uses the first persisted Type A
BET per `match_id`, consistent with Results and the paper-bankroll publication boundary.

## Agent 2 recency experiment

Production Agent 2 currently accumulates the canonical historical serve/return state. The new
shadow runner compares that baseline with rolling evidence windows while preserving the same
Agent 2 model, priors, scoring rules, point-in-time ordering, and minimum-history gates.

Run manually against a database connection:

```bash
python -m sports_edge_tennis.validation.agent2_recency_cli \
  --windows 365,730,1460 \
  --evaluation-year-start 2019
```

The output is JSON containing, for each variant:

- sample
- predicted
- abstained
- correct
- accuracy
- Brier score
- log loss

`window_days = null` is the current cumulative-history baseline.

A recency variant is not eligible for production merely because accuracy is higher. It should
also improve or preserve probability quality, avoid pathological coverage loss, and remain
stable across time/tournament cohorts.

## Historical full-strategy backtest readiness

Historical individual-agent accuracy is not equivalent to historical betting profitability.
An apples-to-apples Type A replay needs genuine point-in-time market evidence in addition to
model predictions.

`load_historical_strategy_readiness(...)` checks each evaluation year for:

- completed matches
- any genuine pre-start match-winner market observation
- a complete pre-start Pinnacle PLAYER_1 + PLAYER_2 market

The default readiness gate requires at least 80% complete pre-start Pinnacle coverage in every
evaluation year. If that condition is not met, historical Type A ROI remains explicitly
blocked rather than being reconstructed from future/closing prices or fabricated market data.

## Decision rule for follow-up model changes

Do not loosen production rules just to increase output. Use the evidence in this order:

1. Fix data/identity/surface/market coverage defects that create avoidable abstentions.
2. Compare unique-match live agent accuracy and calibration against historical baselines.
3. Compare Agent 2 cumulative history against recency variants leakage-safely.
4. Measure Agent 3's incremental value and coverage cost in shadow cohorts.
5. Only propose a production model/policy change when prospective and walk-forward evidence
   both support it.
