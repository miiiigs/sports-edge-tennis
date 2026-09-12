# Tennis Prospective Closing Price and CLV

This feature is deliberately prospective. It must not turn historical odds already sitting in the warehouse into claims of live closing-line evidence.

## Activation

The hourly runner does nothing related to CLV unless this variable is explicitly configured:

```text
TENNIS_CLV_ACTIVATION_AT
```

Use an ISO-8601 timestamp with timezone, for example:

```text
2026-09-12T12:00:00+00:00
```

Only Type A decisions whose database `created_at` is at or after that boundary can enter the close-capture lifecycle.

If the variable is absent, the runner logs:

```text
CLV_CAPTURE_DISABLED activation_env_unset=1
```

and does not create/finalize closing records.

Do not set the activation timestamp backwards to manufacture a prospective history.

## What is captured

Every eligible Type A decision, including `NO_BET`, receives one immutable `decision_close_evidence` record after its scheduled start has passed the finalization delay.

The close resolver uses only `LIVE_PROVIDER` odds snapshots that:

- belong to the decision's match
- are match-winner prices
- were observed at or before scheduled start
- were already ingested by the capture run
- remain pre-match/open market evidence

The resolver chooses the latest **complete two-way pair** at or before start. A quote observed after scheduled start is never substituted for a closing price.

Close state is explicit:

- `COMPLETE` - a complete Pinnacle/sharp two-way close exists
- `PARTIAL` - some sharp closing evidence exists but not a complete pair
- `MISSING` - no qualifying pre-start sharp close exists

A two-hour finalization delay is used so normal schedule/status corrections can arrive before the immutable close record is finalized. The close itself remains the latest qualifying snapshot at or before scheduled start, not a quote from two hours after start.

## NO_BET decisions

The original build specification requires closing-market evidence for every decision, including no-bets.

A `NO_BET` deliberately has no executable selected price. The system therefore records its closing sharp market evidence but does **not** invent a selected side or populate `decision_clv.price_taken`.

This distinction is important:

```text
closing-market evidence != CLV calculation
```

## Priced decisions

`decision_clv` is populated only when all required evidence exists:

- the decision is `BET` or `VETOED`
- a real `OFFERED_SELECTION` snapshot is linked to the decision
- that snapshot matches the persisted taken price/source/bookmaker/selection
- a complete same-book two-way close exists at or before match start
- that close is at or after the price-taken observation
- the closing two-way bookmaker margin is positive

If any requirement fails, close evidence is still recorded and the reason CLV is unavailable is persisted. No synthetic price is created.

## Calculation

For a valid priced decision:

```text
closing_margin = (1 / close_p1) + (1 / close_p2) - 1
clv = price_taken / closing_same_book_price / (1 + closing_margin) - 1
```

The sharp selected-side closing price is stored alongside the same-book close as diagnostic/reference evidence. The executable CLV calculation uses the same bookmaker where the original offered price was taken.

## Idempotency and append-only behavior

- one `decision_close_evidence` row per decision
- one `decision_clv` row per decision when calculation is valid
- repeated hourly finalization does not rewrite an existing close
- close evidence and CLV rows are update/delete protected when the database append-only guard is available

If later data arrives after a decision close was finalized, it does not silently rewrite the historical close.

## Runtime cost

CLV finalization uses already-persisted `odds_snapshots` only.

It does **not**:

- add provider requests
- increase Pinnacle/API-Tennis polling frequency
- retry missing closes
- replay models
- rerun Manager/Market decisions

## Interpretation

CLV is an evidence metric, not a guarantee of profitability.

Do not present a positive mean CLV from a small sample as proof that the live strategy has a durable edge. Track coverage and sample size alongside mean/median CLV, and preserve missing/partial closes in the denominator rather than silently dropping inconvenient evidence.
