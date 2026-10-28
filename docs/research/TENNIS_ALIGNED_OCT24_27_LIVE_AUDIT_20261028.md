# Oct 24–27 original-aligned picks: production read-only research evidence

This runner instrumentation does NOT re-issue or remove selections, re-grade matches, alter settlement, change the Manager, install filters, or fabricate bookmaker data. It is safe to run after normal DB settlement even when the fixture provider fails. A bounded SQL query reads four local calendar dates, using the exact original aligned Model Pick CTE used by the dashboard (Manager + A1/A2 Wide-Spread research).

Each scheduled runner logs per-day **original** vs counterfactual selections under fixed pre-match conditions:
- original baseline,
- original aligned confidence >=70%, >=80%,
- known original surface,
- >=70% and known original surface,
- same-side A1/A2 gap <5 percentage points,
- >=70% and same-side A1/A2 gap <10 percentage points.

Output always includes W/L/VOID/PENDING, graded win rate, original and retained selections, selection coverage, historical avoided losses AND missed winners. It is a what-if reclassification of ALREADY PUBLISHED PICKS, not a full alternate model replay, not actual October improvements, and not a claim that 73% will be achieved prospectively. Never publish simulated results as original historical results.

The original result observation is read from the latest authoritative terminal record, and no missing result is inferred. No player names, identifiers, API keys, odds, wagers or private credentials are logged. The audit is fail-isolated and has no provider API calls. An unresolved original pick remains pending in the baseline even if a hypothetical policy would have abstained. A day's retrospective >73% is not customer acceptance; it requires forward-issued picks.

Ensure the exact CI head passes before staging only the production runner branch SHA; verify runner exits correctly after natural scheduled execution. If the runner remains failed due API-Tennis outage, audit still runs, and the failure must NOT be masked. The definitive outcome categories in result diagnostics remain a separate issue (#259); shadow evidence is not authoritative settlement.
