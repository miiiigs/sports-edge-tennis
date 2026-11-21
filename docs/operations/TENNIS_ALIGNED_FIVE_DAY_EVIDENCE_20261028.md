# All-aligned five-day accuracy gate, Oct28–Nov01 2026

Owner: Brian acceptance target >=73% on **all original aligned Model Picks** from the Results page for each of five successive fully settled Asia/Manila calendar days. Manager-only and A1-only statistics are optional diagnostic slices, not a replacement denominator.

The hourly production runner now reads the same canonical current-Results aligned daily summaries with a read-only PostgreSQL transaction, even during upstream prediction batch failures. Log events:
- `ALIGNED_DAILY_EVIDENCE` for each date Oct28–Nov01 (wins/losses/void/pending, graded, win rate and true qualification state).
- `ALIGNED_FIVE_DAY_STATUS` only MET_FIVE_COMPLETE_DAYS when all five *completed* days have nonzero graded picks and are independently >=73%, with all originally issued matches terminal/VOID and no pending/inconsistent accounting.

No-pick days, incomplete today, unsettled results, totals that don't reconcile, and 72.7% rounded to 73% all explicitly fail qualification. Retrospective research subset figures and skipped historical losses cannot enter this scorecard. This change does **not** alter model filters, issue predictions, modify bets, execute new provider requests or write any table. No guarantee of >=73% future accuracy.

Run exact CI, verify source-only runner diff, release only after one successful natural replay audit from PR #271 and usual production safety checks.


## Continuous rolling five-day evaluation

The initial acceptance window remains Oct28-Nov01. On subsequent dates,
the same read-only monitor checks the latest five fully completed
consecutive Asia/Manila calendar dates without skipping losses, missing
dates or unsettled matches. On Nov03 this is Oct29-Nov02. No model,
settlement, wager or provider changes.
