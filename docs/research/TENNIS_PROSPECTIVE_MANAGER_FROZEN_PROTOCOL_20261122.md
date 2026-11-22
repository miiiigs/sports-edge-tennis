# Brazzy365 prospective frozen Manager shadow protocol — 22 November 2026

Research-only. No live Manager, odds, settlement, Model Picks publication or vendor data changes.

Four predeclared candidate policies:
- Original production Manager consensus with cutoff 0.612597 (October validation target 10%).
- Guarded two-agent B, >=2 same-side approvals, >=60% agreeing confidence, spread <=20 points, frozen selection score >=0.667679 (October validation target 20%).
- Raw-average C, >=2 valid agents and >=60% raw winner confidence, frozen selection score >=0.7070145 (October validation target 20%).
- Equal A1/A2 log-odds blend, first true co-issued Elo+Markov predictions, frozen cutoff 0.6711558673472298 (earlier Oct/November research validation target 40%).

These rules and thresholds were selected on a history that has been inspected many times; all previous research remains exploratory. Only NEW, timestamped, original upcoming matches captured after freezing these rules can contribute prospective evidence.

The capture CLI is read-only. It retrieves each canonical match's FIRST completed nonexperimental production run, genuine original agent predictions and their original as-of/finalization times; requires all original feature prediction times <= issuance, available point-in-time match version, and a match at least 30 minutes away from real collection time. It does not create unseen matches or overwrite prior choices. It emits independently timestamped one-line JSON original + B/C/A1A2 decisions, plus SHA256 digest. Its report denominator is first-run future-targeted matches, not all official eligible fixtures.

Run in a pinned isolated Railway job with read-only Postgres, using python -m sports_edge_tennis.validation.prospective_manager_shadow_capture --source-git-sha EXACT_COMMIT_SHA --horizon-days 7. Preserve the Railway logs and source SHA. Running the same script after match start constitutes retrospective reconstruction, NOT proof of a prior prediction.

Current limitation: one capture is not continuous prospective monitoring. The next work item is safe append-only storage / scheduled observer, not a new alternative live Manager. Without pre-match authentic capture data and >=200 future independently settled candidates, no claim of sustained 72–75% accuracy is authorized. Future scores must retain voids and pending separately, report ATP and Challenger independently and assess matched bookmaker favourite scores only for genuine paired odds present by original issuance.

See previous PRs #345-346 (threshold provenance), #352 (false-veto), #353 (A3 support), #357 (coverage research) and #358 (historical evidence contract).
