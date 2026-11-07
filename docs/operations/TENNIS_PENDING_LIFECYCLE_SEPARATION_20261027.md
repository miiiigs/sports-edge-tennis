# Tennis pending lifecycle and daily accuracy — 27 Oct 2026

### Why
Brian observed many "PENDING" results for matches that had not begun, were postponed or rescheduled. The Results page had one giant count combining unresolved graded-match outcomes with fixtures that were not yet eligible for a final result. That is misleading.

### Classification (read-only)
For each original aligned production pick:
- WON / LOST: verified completed result only.
- VOID: verified RETIRED / WALKOVER / CANCELLED according to existing settlement policy.
- AWAITING_RESULT: provider indicates match played/playing and result has not been independently verified.
- NOT_STARTED: latest fixture remains in scheduled/not-started state, not a result wait.
- RESCHEDULED: provider updated the start by >=6 hours or >=1 hour across the local calendar day, using the existing identity-confirmed schedule evidence. This is deferred, not a result wait.
- POSTPONED: explicit provider postponement.
- NEEDS_RESULT_REVIEW: terminal status missing a persisted terminal outcome, absent provider state, or inconsistent completed winner. Flag for investigation; never guess a winner.

The dashboard's daily and period presentation shows separate columns. The API exposes the extra category counts and per-row `lifecycle_status`. The original `result=PENDING` and `pending` total remain for immutable historical audit, and are labeled **Unsettled total**, not "active pending result".

### Accuracy integrity
The five-consecutive-day Manager target uses the original first-production picks, exact W/L, and the full unsettled population to decide if a day is complete. Deferred fixtures may not count as losses, but neither are their picks silently removed or backfilled as wins. A day cannot be certified at 73% while outcomes remain unresolved. There is no database migration, model, provider, staking or betting change.

### Important limitations
`PENDING_RESULT_DIAGNOSTICS` represents a different production pick-monitoring scope from aligned dashboard history; never equate its 52 monitored records to the aligned daily table by subtraction. An unstarted fixture that is overdue is still a reconciliation priority, not a reason to VOID without an authoritative cancellation.

The correct long-term backend work remains provider-result recovery issue #259 / walkover PR #260. No manual results deletion. This read-only UI change does not turn historical losses into wins or guarantee >=73% over any five-day period.
