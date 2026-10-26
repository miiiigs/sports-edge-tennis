# Manager 73% target — five consecutive days (read-only)

Brian's objective is >=73% settled Manager accuracy on five **consecutive completed calendar days**, with the same published day timezone on every view.

- The daily population is **first-production Manager picks**, NOT combined A1/A2 tracking and NOT solely Value Bets. A match is counted once by the existing first-production historical ledger.
- Each day must have at least one graded pick, **no pending**, and wins/(wins+losses) >= 0.73. WALKOVER, RETIRED and CANCELLED are excluded from graded W/L denominator. Missing days break the sequence; do not skip or relabel them.
- Daily actual W/L and sample sizes must always be displayed; fewer than five graded picks prompts a small-sample warning (not an invented minimum number of selections).
- The streak is evaluated on the five *immediately preceding local calendar days*, never including today's incomplete date. Yesterday may later change as a late authoritative result arrives; re-evaluate daily. This is a descriptive operational target **not proof the model can guarantee 73% or evidence of improved expected value**.
- Backend: `manager_five_day_target()` is a pure read-only function. It is exposed under `manager_accuracy_target` in the existing authenticated Results API and appears separately above the aligned research population on the Results page.
- A single day with pending results blocks the streak regardless of current provisional percentage; incomplete, duplicate or inconsistent rows fail closed.
- Analysis/experimental exclusions must run on independently defined held-out windows and prospective shadow picks. No retroactive filtering of issued predictions or wagering decisions is allowed.
- Deployment: dashboard-only; live runner, historical ledgers, betting/odds policies and model probabilities remain unchanged.

## Acceptance

CI passing + production Railway deployed SHA and /healthz + authenticated customer Results/API verification for UTC and Manila timezone are separate gates. Not reporting a five-day success from local tests alone.
