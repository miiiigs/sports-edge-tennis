# Production Manager daily evidence

The hourly production runner logs seven completed Asia/Manila calendar days after its normal settlement cycle. The new `MANAGER_DAILY_EVIDENCE` rows report day, original first-production Manager pick count, wins/losses/void/pending, scored denominator, graded win rate, model-regime count and exact 73% target status. Days without picks are logged as NO_PICKS instead of omitted.

This is read-only operational telemetry, not a model or publication change. It deliberately does not include A1/A2 research-only tracking, post-result picks or Value Bet substitutions. Dates are assigned from the original point-in-time match version, not a later reschedule, so events can't migrate to more favorable days. The query filters to the last seven completed local days; the current incomplete day is never certified.

Any unverified pending means the daily target is UNSETTLED. A mix of incomplete model regimes is explicitly flagged rather than claimed to be a stable streak. A small daily win count can still mathematically exceed 73% but must be shown with its exact picks/scored count; no accuracy percentage is guaranteed.

Use the actual production logs to reconcile Brian's October 24–27 results, then investigate losses by match identity with authorized database access. No source keys, player identifiers or odds are logged. The runner must remain operational if telemetry fails. Verify exact CI and a successful Railway release before describing this as live.
